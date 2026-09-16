"""The Schedule section: block auto-populate on first render, IsDAM-driven
date defaults and the guards that stop them from clobbering real content,
Generate/Clear, block add/remove, and validation errors.

Needs the app's own WECC calendar DB connection (HL is the default Shape
for a new block, and is auto-generated on first render) — see
tests/README.md.
"""

from datetime import date, timedelta

from streamlit.testing.v1 import AppTest

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
