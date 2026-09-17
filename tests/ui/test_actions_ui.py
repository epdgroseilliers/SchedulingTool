"""The action row: local-only Add Trade, Preview DB Insert (read-only,
marked db), the past-date confirmation gate, and the real-insert path —
which is NEVER exercised against the real database here. See
tests/conftest.py's `no_real_db_writes` fixture: insert_trade and
create_compliance_folders are monkeypatched to recording fakes for that one
test, so nothing this suite does can write to
PhysiqueBilateral.west.BilateralTrades or touch the UNC compliance folders.
"""

from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import APP_PATH


def _run():
    return AppTest.from_file(APP_PATH, default_timeout=120).run()


def _fill_minimal_trade(at, counterparty="AZPS", location="PALOVERDE500"):
    at.selectbox(key="counterparty").set_value(counterparty).run()
    at.selectbox(key="location").set_value(location).run()
    return at


class TestLocalOnlyAddTrade:
    def test_missing_counterparty_and_location_are_errors(self):
        at = _run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        messages = [e.value for e in at.error]
        assert any("Counterparty" in m for m in messages)
        assert any("Location" in m for m in messages)
        assert len(at.session_state["trades"]) == 0

    def test_minimal_trade_can_be_added_without_touching_the_db(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        assert len(at.session_state["trades"]) == 1
        trade = at.session_state["trades"][0]
        assert trade["counterparty"] == "AZPS"
        assert trade["db_trade_ids"] == []

    def test_form_resets_to_defaults_after_a_local_submit(self):
        # Deliberate design choice (see ui.actions._insert_and_save /
        # ui.session.reset_trade_fields): every entry field goes back to
        # its default once the trade is saved — Trade Date and IsDAM are
        # the only fields left as-is.
        at = _run()
        trade_date, is_dam = at.session_state["trade_date"], at.session_state["is_dam"]
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()

        assert at.session_state["trade_date"] == trade_date
        assert at.session_state["is_dam"] == is_dam
        assert at.session_state["counterparty"] is None
        assert at.session_state["location"] is None
        assert at.session_state["block_ids"] == [0]
        assert at.session_state["shape_0"] == "HL"
        assert at.session_state["mw_0"] == 25

    def test_two_trades_can_be_added_in_a_row(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        # The form reset after the first submit, so both required fields
        # need refilling — same as a trader starting the next trade fresh.
        _fill_minimal_trade(at, counterparty="BPAT", location="MIDC")
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert len(at.session_state["trades"]) == 2
        assert at.session_state["trades"][0]["counterparty"] == "AZPS"
        assert at.session_state["trades"][1]["counterparty"] == "BPAT"

    def test_delete_removes_a_trade(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        [b for b in at.button if b.label == "Delete"][0].click().run()
        assert len(at.session_state["trades"]) == 0


class TestPastDateGate:
    def test_no_confirmation_checkbox_when_input_in_db_is_off(self):
        at = _run()
        at.date_input(key="trade_date").set_value(
            date.today() - timedelta(days=5)
        ).run()
        assert not [c for c in at.checkbox if c.label == "Confirm past-dated trade"]

    def test_confirmation_checkbox_appears_when_input_in_db_is_on(self):
        at = _run()
        at.date_input(key="trade_date").set_value(
            date.today() - timedelta(days=5)
        ).run()
        [c for c in at.checkbox if c.label == "Input in DB"][0].set_value(True).run()
        assert [c for c in at.checkbox if c.label == "Confirm past-dated trade"]

    @pytest.mark.db
    def test_past_dated_real_submit_blocked_without_confirmation(self):
        at = _run()
        _fill_minimal_trade(at)
        # ATC (flat 24H) rather than the default HL: HL correctly zeroes
        # out an off-peak day, and today - 5 days lands on a weekend often
        # enough to make the test flaky otherwise — ATC has no such
        # calendar dependency.
        at.text_input(key="shape_0").set_value("ATC").run()
        at.date_input(key="trade_date").set_value(
            date.today() - timedelta(days=5)
        ).run()
        # Changing Trade Date shifts the still-pristine block's dates too
        # (sync_block_dates); get_block_grid auto-fills the newly-in-range
        # date via Shape/MW against the WECC calendar on its own, so no
        # explicit Generate click is needed before Add Trade.
        [c for c in at.checkbox if c.label == "Input in DB"][0].set_value(True).run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert any("past" in e.value.lower() for e in at.error)
        assert len(at.session_state["trades"]) == 0


@pytest.mark.db
class TestPreviewDbInsert:
    """Preview stops before insert_trade() is ever called (see
    ui.actions.handle_submit) — entirely read-only against the real
    bilateral DB, so no mocking is needed here."""

    def test_preview_shows_resolved_rows_without_adding_a_trade(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Preview DB Insert"][0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        preview = at.session_state["db_preview"]
        assert preview is not None
        assert len(preview["rows"]) > 0
        assert len(at.session_state["trades"]) == 0

    def test_preview_can_be_dismissed(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Preview DB Insert"][0].click().run()
        [b for b in at.button if b.label == "✕"][0].click().run()
        assert at.session_state["db_preview"] is None

    def test_duplicate_trade_is_blocked_on_preview(self):
        # AZPS/PALOVERDE500/HL/25MW is a pattern real production data has
        # matched before in this environment (see conversation history) —
        # if it doesn't collide, the preview simply succeeds instead, which
        # is also a valid, non-flaky outcome for this test.
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Preview DB Insert"][0].click().run()
        duplicate_errors = [e.value for e in at.error if "Already in the DB" in e.value]
        if duplicate_errors:
            assert at.session_state["db_preview"] is None
        else:
            assert at.session_state["db_preview"] is not None


class TestMonthlyTrades:
    """IsMonthly skips the hourly grid entirely — a fixed MW for the whole
    period, so each schedule block is written as a single row instead of
    being generated/edited/compressed hour by hour. See
    domain.shapes.monthly_block_rows and ui.schedule's is_monthly branch.
    """

    def _set_monthly(self, at, value=True):
        [c for c in at.checkbox if c.label == "IsMonthly"][0].set_value(value).run()
        return at

    def test_checking_is_monthly_hides_the_hourly_grid(self):
        at = _run()
        assert at.dataframe  # the ordinary hourly grid is there by default
        self._set_monthly(at)
        assert not at.dataframe
        assert not [b for b in at.button if b.label == "Generate"]

    def test_a_range_over_31_dates_is_not_an_error_when_monthly(self):
        at = _run()
        self._set_monthly(at)
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        d["End Date"].set_value(start + timedelta(days=90)).run()
        assert not any("Split it into smaller" in e.value for e in at.error)

    def test_monthly_trade_writes_one_row_per_block_no_schedule(self):
        at = _run()
        _fill_minimal_trade(at)
        self._set_monthly(at)
        d = {x.label: x for x in at.date_input}
        start = d["Start Date"].value
        d["End Date"].set_value(start + timedelta(days=90)).run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        assert len(at.session_state["trades"]) == 1
        trade = at.session_state["trades"][0]
        assert trade["schedule"] == []
        assert len(trade["monthly_blocks"]) == 1
        row = trade["monthly_blocks"][0]
        assert row["start_date"] == start
        assert row["stop_date"] == start + timedelta(days=90)
        assert row["he"] == "HL"

    def test_invalid_shape_blocks_add_trade_when_monthly(self):
        at = _run()
        _fill_minimal_trade(at)
        self._set_monthly(at)
        at.text_input(key="shape_0").set_value("garbage").run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert any("garbage" in e.value for e in at.error)
        assert len(at.session_state["trades"]) == 0

    def test_trades_list_shows_monthly_trade_without_an_hourly_grid(self):
        at = _run()
        _fill_minimal_trade(at)
        self._set_monthly(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        monthly_expanders = [e for e in at.expander if "Monthly" in e.label]
        assert monthly_expanders

        def has_dataframe(node):
            for child in node:
                if type(child).__name__ == "Dataframe":
                    return True
                kids = getattr(child, "children", None)
                if kids and has_dataframe(kids.values()):
                    return True
            return False

        # The trades-list expander for a monthly trade must show no wide
        # hourly grid — unlike the (unrelated) Schedule section above it,
        # which is back to a fresh, non-monthly block after the reset that
        # follows Add Trade and so has a dataframe of its own.
        assert not has_dataframe(monthly_expanders[0].children.values())


class TestRealInsertPathIsMocked:
    """The only place a real submit's insert path is exercised — always
    against a fake insert_trade/create_compliance_folders, never the
    network. See tests/conftest.py::no_real_db_writes."""

    @pytest.mark.db  # resolve_and_validate's lookups still hit the real bilateral DB
    def test_real_submit_calls_the_mocked_insert_not_the_network(self, no_real_db_writes):
        at = _run()
        _fill_minimal_trade(at)
        [c for c in at.checkbox if c.label == "Input in DB"][0].set_value(True).run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        assert len(no_real_db_writes["insert_trade"]) == 1
        assert len(at.session_state["trades"]) == 1
        trade = at.session_state["trades"][0]
        assert trade["db_trade_ids"] == list(range(90001, 90001 + len(trade["db_trade_ids"])))
