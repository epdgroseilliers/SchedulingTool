"""The Schedule section: block auto-populate on first render, IsDAM-driven
date defaults and the guards that stop them from clobbering real content,
Generate/Clear, block add/remove, and validation errors.

Needs the app's own WECC calendar DB connection (HL is the default Shape
for a new block, and is auto-generated on first render) — see
tests/README.md.
"""

from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from ui.session import block_date_defaults
from tests.conftest import APP_PATH


def _run():
    return AppTest.from_file(APP_PATH, default_timeout=90).run()


class TestAutoPopulate:
    def test_block_is_populated_on_first_render_without_clicking_generate(self):
        at = _run()
        assert not at.exception, [e.value for e in at.exception]
        grid = at.dataframe[0].value
        nonzero = (grid.drop(columns=["Date"]) > 0).any().any()
        assert nonzero, "block was not auto-populated"

    def test_auto_populated_grid_matches_default_hl_shape(self):
        at = _run()
        grid = at.dataframe[0].value
        row = grid.iloc[0]
        scheduled = [h for h in range(1, 25) if row[str(h)] > 0]
        # HL is the default shape: on-peak hours are HE7-22 on a peak day.
        assert scheduled == list(range(7, 23)) or scheduled == []


class TestGenerateAndClear:
    def test_generate_button_populates_the_grid(self):
        at = _run()
        gen = [b for b in at.button if b.label == "Generate"][0]
        gen.click().run()
        assert not at.exception, [e.value for e in at.exception]
        grid = at.dataframe[0].value
        assert (grid.drop(columns=["Date"]) >= 0).all().all()

    def test_clear_zeroes_the_grid(self):
        at = _run()
        [b for b in at.button if b.label == "Generate"][0].click().run()
        [b for b in at.button if b.label == "Clear schedule"][0].click().run()
        grid = at.dataframe[0].value
        assert (grid.drop(columns=["Date"]) == 0).all().all()


class TestWideningDateRangeFollowsCalendar:
    """Regression: widening a block's End Date used to leave the newly
    in-range date at all-zero MW until Generate was clicked again —
    get_block_grid now auto-fills a genuinely new date via Shape/MW
    against the WECC calendar itself, the same as Generate would, so the
    schedule keeps following the calendar without that extra click.
    """

    def test_new_date_from_widening_end_date_is_not_left_at_zero(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        widened_end = start + timedelta(days=1)
        d["End Date"].set_value(widened_end).run()
        grid = at.dataframe[0].value
        new_row = grid[grid["Date"] == widened_end].iloc[0]
        scheduled = [h for h in range(1, 25) if new_row[str(h)] > 0]
        # HL is the default shape: on-peak hours are HE7-22 on a peak day,
        # all-zero on an off-peak one — either is correct, but it must
        # actually reflect the calendar rather than always being zero.
        assert scheduled == list(range(7, 23)) or scheduled == []

    def test_existing_dates_are_untouched_by_widening(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        grid_before = at.dataframe[0].value.copy()
        d["End Date"].set_value(start + timedelta(days=1)).run()
        grid_after = at.dataframe[0].value
        row_before = grid_before.iloc[0]
        row_after = grid_after[grid_after["Date"] == start].iloc[0]
        for h in range(1, 25):
            assert row_after[str(h)] == row_before[str(h)]

    def test_clear_schedule_stays_zero_on_a_later_rerun(self):
        # Clear zeroes whichever dates the block holds *at that moment*, so
        # widen the range first — both dates must still be zero afterward,
        # not silently re-filled from the calendar on the next rerun.
        at = _run()
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        d["End Date"].set_value(start + timedelta(days=1)).run()
        [b for b in at.button if b.label == "Clear schedule"][0].click().run()
        at.run()
        grid = at.dataframe[0].value
        assert (grid.drop(columns=["Date"]) == 0).all().all()


class TestIsDamDrivesDefaultDates:
    def test_isdam_true_defaults_start_to_tomorrow(self):
        at = _run()
        today = date.today()
        d = {x.label: x for x in at.date_input}
        assert d["Start Date"].value == today + timedelta(days=1)

    def test_isdam_false_defaults_start_to_trade_date(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        today = date.today()
        d = {x.label: x for x in at.date_input}
        assert d["Start Date"].value == today

    def test_generate_locks_dates_against_further_isdam_changes(self):
        at = _run()
        [b for b in at.button if b.label == "Generate"][0].click().run()
        d = {x.label: x for x in at.date_input}
        generated_start = d["Start Date"].value
        grid_before = at.dataframe[0].value.copy()

        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        d2 = {x.label: x for x in at.date_input}
        assert d2["Start Date"].value == generated_start, "Generate must lock the date"
        grid_after = at.dataframe[0].value
        import pandas as pd
        pd.testing.assert_frame_equal(
            grid_before.reset_index(drop=True), grid_after.reset_index(drop=True)
        )

    def test_manual_date_edit_locks_against_further_isdam_changes(self):
        at = _run()
        today = date.today()
        picked = today + timedelta(days=10)
        d = {x.label: x for x in at.date_input}
        d["Start Date"].set_value(picked).run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        d2 = {x.label: x for x in at.date_input}
        assert d2["Start Date"].value == picked

    def test_new_block_picks_up_current_isdam_default(self):
        at = _run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        [b for b in at.button if b.label == "+ Add another block"][0].click().run()
        today = date.today()
        starts = [x.value for x in at.date_input if x.label == "Start Date"]
        assert len(starts) == 2
        assert all(s == today for s in starts)


class TestDamDefaultDatesThroughTheApp:
    """A fresh IsDAM block's Start/End dates are the flow dates the WECC
    calendar pairs with the trade date — one trading session, which is
    usually one day but covers the weekend from a Thursday or Friday. It's
    read from the calendar's own TradeDate column rather than inferred:
    guessing it from runs of matching peak status put every Monday-to-
    Thursday trade's end date on the following Saturday.
    """

    def test_the_dates_are_the_calendars_own_session(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        trade_date = d["Trade Date"].value
        assert (d["Start Date"].value, d["End Date"].value) == block_date_defaults(
            trade_date, True
        )

    @staticmethod
    def _a_day_with_no_session():
        """A real date the calendar has no trading session on, found in the
        calendar rather than hardcoded — it's live, mutable data."""
        from data.calendar import sessions_near

        today = date.today()
        sessions = sessions_near(today)
        if not sessions:
            pytest.skip("the WECC calendar could not be read")
        for offset in range(0, 8):
            day = today + timedelta(days=offset)
            if day not in sessions:
                return day
        pytest.skip("the calendar has a session every day this week")

    @staticmethod
    def _a_trading_day():
        from data.calendar import sessions_near

        today = date.today()
        sessions = sessions_near(today)
        upcoming = [d for d in sessions if d >= today]
        if not upcoming:
            pytest.skip("no upcoming trading session in the calendar")
        return min(upcoming)

    def test_a_date_with_no_session_turns_the_trade_real_time(self):
        # There is no day-ahead market on a Saturday, so IsDAM goes off and
        # can't be put back on, and the flow date is the trade date.
        closed = self._a_day_with_no_session()
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        at.date_input(key="trade_date").set_value(closed).run()
        assert at.session_state["is_dam"] is False
        assert at.checkbox(key="is_dam").disabled
        ss = at.session_state
        assert ss["start_0"] == ss["end_0"] == closed

    def test_returning_to_a_trading_day_gives_the_box_back(self):
        # A mistyped weekend date must not silently leave the next trade
        # real-time.
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        at.date_input(key="trade_date").set_value(self._a_day_with_no_session()).run()
        at.date_input(key="trade_date").set_value(self._a_trading_day()).run()
        assert at.session_state["is_dam"] is True
        assert not at.checkbox(key="is_dam").disabled

    def test_but_a_deliberate_real_time_choice_survives_the_detour(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        at.checkbox(key="is_dam").set_value(False).run()
        at.date_input(key="trade_date").set_value(self._a_day_with_no_session()).run()
        at.date_input(key="trade_date").set_value(self._a_trading_day()).run()
        assert at.session_state["is_dam"] is False

    def test_the_shape_does_not_change_them(self):
        # The session is a property of the trade date, not of which hours
        # are being bought within it.
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        before = {x.label: x.value for x in at.date_input}
        at.text_input(key="shape_0").set_value("7-22").run()
        d = {x.label: x for x in at.date_input}
        assert d["Start Date"].value == before["Start Date"]
        assert d["End Date"].value == before["End Date"]

    def test_a_multi_day_default_is_populated_from_the_calendar_not_zero(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        start, end = d["Start Date"].value, d["End Date"].value
        if end == start:
            pytest.skip("today's WECC calendar run happens to be a single day")
        grid = at.dataframe[0].value
        last_row = grid[grid["Date"] == end].iloc[0]
        scheduled = [h for h in range(1, 25) if last_row[str(h)] > 0]
        assert scheduled == list(range(7, 23))  # HL: on-peak hours


class TestBlockValidation:
    def test_start_after_end_is_an_error(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        d["End Date"].set_value(start - timedelta(days=1)).run()
        assert any("on or before" in e.value for e in at.error)

    def test_block_spanning_too_many_dates_is_an_error(self):
        at = _run()
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        d["End Date"].set_value(start + timedelta(days=40)).run()
        assert any("Split it into smaller" in e.value for e in at.error)


class TestBlockAddRemove:
    def test_add_and_remove_block(self):
        at = _run()
        [b for b in at.button if b.label == "+ Add another block"][0].click().run()
        assert len(at.session_state["block_ids"]) == 2
        remove_buttons = [b for b in at.button if b.label == "Remove block"]
        assert len(remove_buttons) == 2
        remove_buttons[0].click().run()
        assert len(at.session_state["block_ids"]) == 1

    def test_single_block_has_no_remove_button(self):
        at = _run()
        assert not [b for b in at.button if b.label == "Remove block"]
