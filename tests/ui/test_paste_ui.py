"""Paste a broker string into the real running app: it should fill the
form, build the schedule, and never touch the database or add a trade by
itself. See tests/README.md for the WECC calendar DB dependency.
"""

from datetime import date, timedelta

from streamlit.testing.v1 import AppTest

from tests.conftest import APP_PATH


def _paste(at, text):
    at.text_input(key="paste_box").set_value(text).run()
    return at


def _ss_get(at, key, default=None):
    """AppTest's session_state has no .get()."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


class TestFillsFormOnCleanParse:
    def test_example_1_fills_every_field_and_builds_the_schedule(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c")
        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]

        ss = at.session_state
        assert ss["counterparty"] == "AZPS"
        assert ss["location"] == "PALOVERDE500"
        assert ss["index_name"] is None
        assert ss["price"] == 73.0
        assert ss["is_sell"] is False
        assert ss["wspp_contract"] == "C"
        assert ss["communication"] == "ICE Chat"
        assert ss["shape_0"] == "18-21"
        assert ss["mw_0"] == 100
        # Year is inferred relative to the real "today" this test runs on,
        # so only month/day are deterministic here (see TestTradeDateAnchoring
        # for year-inference coverage against a fixed anchor date).
        assert (ss["start_0"].month, ss["start_0"].day) == (9, 15)
        assert len(ss["trades"]) == 0, "pasting must not add a trade by itself"

        grid = at.dataframe[0].value
        row = grid.iloc[0]
        scheduled = [h for h in range(1, 25) if row[str(h)] > 0]
        assert scheduled == [18, 19, 20, 21]
        assert row["18"] == 100.0

    def test_example_3_acs_source_and_defaults(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "BPA sells 100MW LL ACS at JD for midc+3")
        assert not at.error, [e.value for e in at.error]
        ss = at.session_state
        assert ss["counterparty"] == "BPAT"
        assert ss["is_sell"] is False  # BPA sells -> we buy
        assert ss["location"] == "JOHNDAY"
        assert ss["index_name"] == "MIDC"
        assert ss["price"] == 3.0
        assert ss["specified_source"] == "Bonneville Power Administration"
        assert ss["wspp_contract"] == "C"  # absent in string -> default

    def test_example_5_nws_flag_and_sell_direction(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "BPA buys 50mw nws LL at midc for midc-3")
        assert not at.error, [e.value for e in at.error]
        ss = at.session_state
        assert ss["is_sell"] is True  # BPA buys -> we sell
        assert ss["location"] == "MIDC"
        assert ss["price"] == -3.0
        assert ss["is_nws"] is True
        assert ss["mw_0"] == 50


class TestRejectsIncompleteString:
    def test_bad_string_shows_errors_and_fills_nothing(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        before = _ss_get(at, "counterparty")
        _paste(at, "some nonsense with no trade in it")
        assert not at.exception, [e.value for e in at.exception]
        assert len(at.error) > 0
        assert _ss_get(at, "counterparty") == before
        assert len(at.session_state["trades"]) == 0


class TestHandEditsSurviveReruns:
    def test_editing_a_field_after_a_paste_is_not_clobbered(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c")
        at.selectbox(key="location").set_value("MEAD230").run()
        at.number_input(key="price").set_value(99.0).run()
        assert at.session_state["location"] == "MEAD230"
        assert at.session_state["price"] == 99.0

    def test_a_new_paste_refills_the_form(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c")
        _paste(at, "BPA buys 50mw nws LL at midc for midc-3")
        assert at.session_state["counterparty"] == "BPAT"
        assert at.session_state["location"] == "MIDC"
        assert at.session_state["is_sell"] is True


class TestTradeDateAnchoring:
    """The reported bug: weekday/flow-date resolution must anchor on the
    Trade Date set on the page, not the real wall-clock date."""

    def test_mon_only_anchors_on_trade_date_not_today(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        friday = date(2026, 9, 11)
        at.date_input(key="trade_date").set_value(friday).run()
        _paste(at, "EPE sells 100mw he7-10 Mon only at springer for $10")
        assert not at.error, [e.value for e in at.error]
        assert at.session_state["start_0"] == date(2026, 9, 14)
        assert at.session_state["end_0"] == date(2026, 9, 14)
        grid = at.dataframe[0].value
        assert len(grid) == 1
        row = grid.iloc[0]
        scheduled = [h for h in range(1, 25) if row[str(h)] > 0]
        assert scheduled == [7, 8, 9, 10]


class TestNoDateFallsBackToTradeDate:
    """No flow date in the string clears start_0/end_0, which falls back to
    domain.trade.default_block_start — the trade date itself when IsDAM is
    off (vs. the next day when it's on). See ui.paste.apply_parsed_string.
    """

    def test_isdam_off_before_paste(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        _paste(at, "BPA sells 100MW LL ACS at JD for midc+3")
        assert not at.error, [e.value for e in at.error]
        ss = at.session_state
        assert ss["start_0"] == ss["trade_date"]
        assert ss["end_0"] == ss["trade_date"]

    def test_isdam_off_after_paste(self):
        # Toggling IsDAM after a dateless paste must still resync to the
        # trade date — the block is still pristine (untouched since the
        # paste populated it from the same IsDAM-driven default).
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "BPA sells 100MW LL ACS at JD for midc+3")
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        ss = at.session_state
        assert ss["start_0"] == ss["trade_date"]
        assert ss["end_0"] == ss["trade_date"]

    def test_dateless_paste_over_a_previously_dated_one(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        [c for c in at.checkbox if c.label == "IsDAM"][0].set_value(False).run()
        _paste(at, "APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c")
        _paste(at, "BPA sells 100MW LL ACS at JD for midc+3")
        ss = at.session_state
        assert ss["start_0"] == ss["trade_date"]
        assert ss["end_0"] == ss["trade_date"]


class TestWsppFormsThroughTheApp:
    def test_bare_sched_form_sets_wspp_contract_type(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "ABEX sells 2mw atc at Glacier for $28 sched B")
        assert not at.error, [e.value for e in at.error]
        assert at.session_state["counterparty"] == "ABEX"
        assert at.session_state["location"] == "GLWND1"
        assert at.session_state["wspp_contract"] == "B"


class TestFromCounterpartyAndMonthlyFlagThroughTheApp:
    def test_from_counterparty_bare_index_and_monthly_flag(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(
            at,
            "MAG buys from Conoco 75mw HL of Non-caiso power at PV index + 9.5 "
            "flow 9/1-9/28",
        )
        assert not at.error, [e.value for e in at.error]
        ss = at.session_state
        assert ss["counterparty"] == "CONC"
        assert ss["is_sell"] is False
        assert ss["location"] == "PALOVERDE500"
        assert ss["index_name"] == "PALOVERDE"
        assert ss["price"] == 9.5
        assert ss["is_source_non_caiso"] is True
        assert ss["rare_fields"]["is_monthly"] is True

    def test_full_quarter_parses_and_skips_the_block_size_cap(self):
        # Q3 spans 92 days, well past the ordinary 31-date block cap (see
        # domain/grid.py MAX_BLOCK_DATES) — but IsMonthly skips the hourly
        # grid entirely, so that cap never applies here.
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(
            at,
            "MAG buys from Conoco 75mw of Non-caiso power for Q3 HL 2027 "
            "at PV index + 9.5",
        )
        ss = at.session_state
        assert not at.error, [e.value for e in at.error]
        assert ss["counterparty"] == "CONC"
        assert ss["start_0"] == date(2027, 7, 1)
        assert ss["end_0"] == date(2027, 9, 30)
        assert ss["rare_fields"]["is_monthly"] is True


class TestPasteThenAddTrade:
    def test_full_flow_local_only(self):
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        _paste(at, "APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c")
        [b for b in at.button if b.label == "Add Trade"][0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        assert len(at.session_state["trades"]) == 1
        t = at.session_state["trades"][0]
        assert t["counterparty"] == "AZPS"
        assert t["direction"] == "Buy"
        assert t["communication"] == "ICE Chat"
        assert sorted({he for _, he, _ in t["schedule"]}) == [18, 19, 20, 21]
        assert {mw for _, _, mw in t["schedule"]} == {100.0}
        assert t["db_trade_ids"] == []  # Input in DB was never ticked
