"""The Clear button next to the paste box, and the same reset applied
automatically after a successful Add Trade — see ui.session.reset_trade_fields
/ queue_form_reset. Both reset every entry field to its default except
Trade Date and IsDAM.

Needs the app's own WECC calendar DB connection (HL is the default Shape,
auto-generated on render) — see tests/README.md.
"""

from datetime import date

from streamlit.testing.v1 import AppTest

from tests.conftest import APP_PATH


def _run():
    return AppTest.from_file(APP_PATH, default_timeout=120).run()


def _click_clear(at):
    return [b for b in at.button if b.label == "Clear"][0].click().run()


class TestClearButton:
    def test_resets_economics_and_backoffice_fields(self):
        at = _run()
        at.text_input(key="paste_box").set_value(
            "APS sells 25mw hl at pv for pv+0.5"
        ).run()
        assert at.session_state["counterparty"] == "AZPS"

        _click_clear(at)
        assert not at.exception, [e.value for e in at.exception]
        ss = at.session_state
        assert ss["counterparty"] is None
        assert ss["location"] is None
        assert ss["index_name"] is None
        assert ss["price"] == 0.0
        assert ss["is_sell"] is False
        assert ss["communication"] is None
        assert ss["specified_source"] is None
        assert ss["wspp_contract"] == "C"
        assert ss["is_nws"] is False
        assert ss["is_source_non_caiso"] is False

    def test_resets_rare_fields(self):
        at = _run()
        [c for c in at.checkbox if c.label == "IsOption"][0].set_value(True).run()
        _click_clear(at)
        assert at.session_state["rare_fields"]["is_option"] is False

    def test_resets_the_paste_box_and_summary(self):
        # Regression: the paste_box widget renders *before* the Clear
        # button in the same row, so resetting it immediately (in the same
        # run the button was clicked) used to raise
        # StreamlitAPIException — the reset must be deferred to the next
        # run's very start, before any widget claims its key.
        at = _run()
        at.text_input(key="paste_box").set_value(
            "APS sells 25mw hl at pv for pv+0.5"
        ).run()
        _click_clear(at)
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["paste_box"] == ""
        assert at.session_state["last_parsed_string"] is None
        assert at.session_state["parse_summary"] is None

    def test_resets_the_schedule_to_a_single_fresh_block(self):
        at = _run()
        [b for b in at.button if b.label == "+ Add another block"][0].click().run()
        at.text_input(key="shape_1").set_value("ATC").run()
        assert len(at.session_state["block_ids"]) == 2

        _click_clear(at)
        assert not at.exception, [e.value for e in at.exception]
        ss = at.session_state
        assert ss["block_ids"] == [0]
        assert ss["shape_0"] == "HL"
        assert ss["mw_0"] == 25
        grid = at.dataframe[0].value
        assert len(grid) >= 1
        assert (grid.drop(columns=["Date"]) >= 0).all().all()

    def test_resets_is_monthly(self):
        at = _run()
        [c for c in at.checkbox if c.label == "IsMonthly"][0].set_value(True).run()
        _click_clear(at)
        assert at.session_state["rare_fields"]["is_monthly"] is False

    def test_leaves_trade_date_and_isdam_untouched(self):
        picked = date(2026, 9, 11)
        at = _run()
        at.date_input(key="trade_date").set_value(picked).run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        _click_clear(at)
        assert at.session_state["trade_date"] == picked
        assert at.session_state["is_dam"] is False

    def test_leaves_existing_trades_list_untouched(self):
        at = _run()
        at.selectbox(key="counterparty").set_value("AZPS").run()
        at.selectbox(key="location").set_value("PALOVERDE500").run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert len(at.session_state["trades"]) == 1

        _click_clear(at)
        assert len(at.session_state["trades"]) == 1


class TestFormResetAfterAddTrade:
    def test_add_trade_resets_the_form_but_not_trade_date_or_isdam(self):
        at = _run()
        picked = date(2026, 9, 11)
        at.date_input(key="trade_date").set_value(picked).run()
        at.text_input(key="paste_box").set_value(
            "APS sells 25mw hl at pv for pv+0.5"
        ).run()
        [b for b in at.button if b.label == "Add Trade"][0].click().run()

        assert not at.exception, [e.value for e in at.exception]
        ss = at.session_state
        assert len(ss["trades"]) == 1
        assert ss["trades"][0]["counterparty"] == "AZPS"  # the saved trade is intact
        assert ss["trade_date"] == picked
        assert ss["is_dam"] is True
        assert ss["counterparty"] is None
        assert ss["location"] is None
        assert ss["paste_box"] == ""
        assert ss["block_ids"] == [0]
