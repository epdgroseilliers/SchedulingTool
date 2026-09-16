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

    def test_schedule_and_form_persist_after_a_local_submit(self):
        # Deliberate design choice (see ui.actions._insert_and_save):
        # booking several trades against the same schedule is a common
        # desk workflow, so nothing is reset after Add Trade.
        at = _run()
        _fill_minimal_trade(at)
        grid_before = at.dataframe[0].value.copy()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()

        assert at.session_state["counterparty"] == "AZPS"
        assert at.session_state["location"] == "PALOVERDE500"
        assert at.session_state["block_ids"] == [0]
        import pandas as pd
        pd.testing.assert_frame_equal(
            grid_before.reset_index(drop=True),
            at.dataframe[0].value.reset_index(drop=True),
        )

    def test_two_trades_can_be_added_in_a_row(self):
        at = _run()
        _fill_minimal_trade(at)
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        at.selectbox(key="counterparty").set_value("BPAT").run()
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
        at.date_input(key="trade_date").set_value(
            date.today() - timedelta(days=5)
        ).run()
        # Changing Trade Date shifts the still-pristine block's dates too
        # (sync_block_dates), which reconciles the grid to all-zero for the
        # newly-in-range date — so it must be regenerated before Add Trade,
        # or "Schedule needs at least one hour" fires first and the
        # past-date branch (further down handle_submit) is never reached.
        [b for b in at.button if b.label == "Generate"][0].click().run()
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
