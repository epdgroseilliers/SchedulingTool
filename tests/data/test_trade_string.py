"""data.trade_string — the deterministic broker-string parser. Pure, no
Streamlit or DB: the option vocabulary is passed in directly.

Every real broker string this parser has been written against lives here,
verbatim, as its own test — see the module docstring in trade_string.py for
the running list.
"""

from datetime import date

import pytest

from data.trade_string import parse_trade_string

COUNTERPARTIES = ["AZPS", "BPAT", "EPE", "ABEX", "SRP"]
LOCATIONS = ["PALOVERDE500", "MEAD230", "MIDC", "JohnDay", "SPRINGER345", "GLWND1", "CROSSOVER"]
INDEXES = ["PALOVERDE", "MONA", "MEAD230", "AESO", "MIDC"]
SPECIFIED_SOURCES = [
    "Bonneville Power Administration", "Palo Verde Nuclear",
    "Tacoma Power - ACS", "Seattle City Light - ACS",
]
FULL_NAMES = {
    "EPE": "El Paso Electric Company",
    "AZPS": "Arizona Public Service Company",
    "BPAT": "Bonneville Power Administration",
}
TODAY = date(2026, 9, 15)  # a Tuesday


def parse(text, today=TODAY):
    return parse_trade_string(
        text, COUNTERPARTIES, LOCATIONS, INDEXES, SPECIFIED_SOURCES,
        full_names=FULL_NAMES, today=today,
    )


# --- The real strings this parser was written against ---------------------

class TestRealExamples:
    def test_example_1_fixed_price_explicit_hours(self):
        r = parse(
            "APS SELLS/MAG BUYS 100 MWS HE18-HE21  PV FIXED $73 flow 9/15  wspp sched c"
        )
        assert r.ok, r.errors
        assert r.get("counterparty") == "AZPS"
        assert r.get("direction") == "Buy"
        assert r.get("mw") == 100.0
        assert r.get("shape") == "18-21"
        assert r.get("location") == "PALOVERDE500"
        assert r.get("index") is None
        assert r.get("price") == 73.0
        assert r.get("start_date") == date(2026, 9, 15)
        assert r.get("end_date") == date(2026, 9, 15)
        assert r.get("wspp_contract") == "C"

    def test_example_2_at_index_derives_node_from_location(self):
        r = parse("APS SELLS/MAG BUYS 12 MWS PV HL @ index +.50  flow 9/15 wspp sched c")
        assert r.ok, r.errors
        assert r.get("mw") == 12.0
        assert r.get("shape") == "HL"
        assert r.get("location") == "PALOVERDE500"
        assert r.get("index") == "PALOVERDE"
        assert r.get("price") == 0.5

    def test_example_3_lone_verb_inverts_and_acs_resolves_source(self):
        r = parse("BPA sells 100MW LL ACS at JD for midc+3")
        assert r.ok, r.errors
        assert r.get("counterparty") == "BPAT"
        assert r.get("direction") == "Buy"  # BPA sells -> we buy
        assert r.get("location") == "JohnDay"
        assert r.get("index") == "MIDC"
        assert r.get("price") == 3.0
        assert r.get("specified_source") == "Bonneville Power Administration"

    def test_example_4_full_name_counterparty_and_bare_dollar(self):
        r = parse("el paso buys 100mw H18-21 springer  $83")
        assert r.ok, r.errors
        assert r.get("counterparty") == "EPE"
        assert r.get("direction") == "Sell"  # el paso buys -> we sell
        assert r.get("shape") == "18-21"
        assert r.get("location") == "SPRINGER345"
        assert r.get("index") is None
        assert r.get("price") == 83.0

    def test_example_5_two_midcs_split_by_preposition_and_nws_flag(self):
        r = parse("BPA buys 50mw nws LL at midc for midc-3")
        assert r.ok, r.errors
        assert r.get("direction") == "Sell"  # BPA buys -> we sell
        assert r.get("location") == "MIDC"
        assert r.get("index") == "MIDC"
        assert r.get("price") == -3.0
        assert r.get("is_nws") is True

    def test_example_6_flow_date_with_the_word_date(self):
        r = parse("APS sells 50MW ncs he17-22 at PV for $65 flow date 09/17")
        assert r.ok, r.errors
        assert r.get("is_source_non_caiso") is True
        assert r.get("start_date") == date(2026, 9, 17)
        assert r.get("end_date") == date(2026, 9, 17)

    def test_example_7_weekday_only_flow_date(self):
        # today is a Tuesday; "Mon only" resolves to the following Monday.
        r = parse("APS sells 50MW ncs he17-22 Mon only at PV for $65")
        assert r.ok, r.errors
        assert r.get("start_date") == date(2026, 9, 21)
        assert r.get("end_date") == date(2026, 9, 21)

    def test_example_8_bare_sched_form(self):
        r = parse("ABEX sells 2mw atc at Glacier for $28 sched B", today=date(2026, 9, 1))
        # GLWND1 isn't aliased under "Glacier" in this test's own LOCATIONS
        # setup (that alias lives in trade_string's LOCATION_ALIASES, which
        # this test exercises directly), so assert only what this file owns.
        assert r.ok, r.errors
        assert r.get("counterparty") == "ABEX"
        assert r.get("direction") == "Buy"
        assert r.get("mw") == 2.0
        assert r.get("shape") == "ATC"
        assert r.get("price") == 28.0
        assert r.get("wspp_contract") == "B"

    def test_example_9_single_hour_shape(self):
        r = parse("NWMT sells 50mw HE18 at crossover for $45")
        assert r.ok, r.errors
        assert r.get("counterparty") == "NWDS"  # NWMT is a desk alias for NWDS
        assert r.get("direction") == "Buy"  # NWMT sells -> we buy
        assert r.get("mw") == 50.0
        assert r.get("shape") == "18"
        assert r.get("location") == "CROSSOVER"
        assert r.get("index") is None
        assert r.get("price") == 45.0


# --- Single-hour shapes (no dash) ------------------------------------------

class TestSingleHourShape:
    """A bare hour-ending like 'HE18' (no '-end') names a one-hour shape,
    same as typing '18' alone into the Shape box does — previously only a
    range like 'HE18-HE21' was recognized and a lone hour fell through to
    'No shape found', silently dropping the whole trade."""

    @pytest.mark.parametrize("text,expected_shape,expected_source", [
        ("AZPS sells 10MW HE18 PALOVERDE500 FIXED $5", "18", "HE18"),
        ("AZPS sells 10MW H18 PALOVERDE500 FIXED $5", "18", "H18"),
        ("AZPS sells 10MW he9 PALOVERDE500 FIXED $5", "9", "he9"),
    ])
    def test_bare_hour_forms(self, text, expected_shape, expected_source):
        r = parse(text)
        assert r.ok, r.errors
        assert r.get("shape") == expected_shape
        assert r.fields["shape"].source_text == expected_source

    def test_shape_value_has_no_dash(self):
        r = parse("AZPS sells 10MW HE18 PALOVERDE500 FIXED $5")
        assert r.get("shape") == "18"

    def test_single_hour_does_not_swallow_a_following_range(self):
        # A range must still win over the single-hour form when both could
        # in principle start matching at the same "h" — greedy-optional
        # matching should always prefer the full range.
        r = parse("AZPS sells 10MW HE18-HE21 PALOVERDE500 FIXED $5")
        assert r.get("shape") == "18-21"

    def test_range_forms_still_work_alongside_the_fix(self):
        assert parse("AZPS sells 10MW HE7-HE22 PALOVERDE500 FIXED $5").get("shape") == "7-22"
        assert parse("AZPS sells 10MW H7-22 PALOVERDE500 FIXED $5").get("shape") == "7-22"


# --- Direction resolution --------------------------------------------------

class TestDirection:
    def test_mag_buys_explicit(self):
        r = parse("AZPS SELLS/MAG BUYS 10MW HL PALOVERDE500 FIXED $5")
        assert r.get("direction") == "Buy"

    def test_mag_sells_explicit(self):
        r = parse("AZPS BUYS/MAG SELLS 10MW HL PALOVERDE500 FIXED $5")
        assert r.get("direction") == "Sell"

    def test_no_buy_sell_wording_errors(self):
        r = parse("100MW HL at midc for midc-3")
        assert not r.ok
        assert any("buy/sell" in e.lower() for e in r.errors)

    def test_only_mag_named_with_no_counterparty_errors(self):
        r = parse("MAG buys 10MW HL PALOVERDE500 FIXED $5")
        assert not r.ok
        assert any("counterparty" in e.lower() for e in r.errors)


# --- WSPP contract type forms ----------------------------------------------

class TestWsppForms:
    @pytest.mark.parametrize("text,expected", [
        ("AZPS sells 10MW HL PALOVERDE500 FIXED $5 wspp sched c", "C"),
        ("AZPS sells 10MW HL PALOVERDE500 FIXED $5 wspp schedule c", "C"),
        ("AZPS sells 10MW HL PALOVERDE500 FIXED $5 sched B", "B"),
        ("AZPS sells 10MW HL PALOVERDE500 FIXED $5 schedule B", "B"),
        ("AZPS sells 10MW HL PALOVERDE500 FIXED $5 wspp B", "B"),
    ])
    def test_accepted_forms(self, text, expected):
        r = parse(text)
        assert r.get("wspp_contract") == expected

    def test_absent_is_none_not_an_error(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5")
        assert r.ok
        assert r.get("wspp_contract") is None


# --- Price / index forms ----------------------------------------------------

class TestPriceForms:
    def test_at_index_on_location_with_no_node_errors(self):
        r = parse("AZPS sells 10MW HL springer @ index +.50 flow 9/15")
        assert not r.ok
        assert any("pricing node" in e for e in r.errors)

    def test_unrecognized_index_errors(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 for banana+2")
        assert not r.ok
        assert any("not recognized" in e for e in r.errors)


# --- Flow date / weekday resolution, anchored on the given `today` --------

class TestFlowDateAnchoring:
    def test_plain_flow_date_still_works(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5 flow 9/15")
        assert r.get("start_date") == date(2026, 9, 15)

    def test_flow_date_range(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5 flow 9/15-9/17")
        assert r.get("start_date") == date(2026, 9, 15)
        assert r.get("end_date") == date(2026, 9, 17)

    def test_year_inferred_nearest_when_pasted_near_year_boundary(self):
        r = parse(
            "AZPS sells 10MW HL PALOVERDE500 FIXED $5 flow 1/5",
            today=date(2026, 12, 28),
        )
        assert r.get("start_date") == date(2027, 1, 5)

    def test_weekday_anchors_on_the_given_today_not_real_today(self):
        # The exact reported bug: a Friday trade date, "Mon only" must
        # resolve to the very next Monday (3 days later), not a week out.
        friday = date(2026, 9, 11)
        r = parse("EPE sells 100mw he7-10 Mon only at springer for $10", today=friday)
        assert r.ok, r.errors
        assert r.get("start_date") == date(2026, 9, 14)
        assert r.get("end_date") == date(2026, 9, 14)

    def test_struck_on_the_weekday_itself_resolves_to_that_day(self):
        monday = date(2026, 9, 14)
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5 Mon only", today=monday)
        assert r.get("start_date") == monday

    def test_bare_weekday_without_only_also_works(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5 Mon", today=date(2026, 9, 15))
        assert r.get("start_date") == date(2026, 9, 21)

    def test_full_weekday_name(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5 Monday", today=date(2026, 9, 15))
        assert r.get("start_date") == date(2026, 9, 21)

    def test_no_date_info_leaves_dates_unset(self):
        r = parse("AZPS sells 10MW HL PALOVERDE500 FIXED $5")
        assert r.get("start_date") is None
        assert r.get("end_date") is None


# --- Order independence -----------------------------------------------------

def test_reordered_string_parses_the_same():
    r = parse(
        "flow 9/15 wspp sched c 100 MWS APS SELLS/MAG BUYS PV HE18-HE21 FIXED $73"
    )
    assert r.ok, r.errors
    assert r.get("counterparty") == "AZPS"
    assert r.get("direction") == "Buy"
    assert r.get("mw") == 100.0
    assert r.get("shape") == "18-21"
    assert r.get("location") == "PALOVERDE500"
    assert r.get("price") == 73.0


# --- Required-field enforcement: fail closed, never partial ----------------

class TestRequiredFields:
    @pytest.mark.parametrize("text,missing_keyword", [
        ("APS SELLS/MAG BUYS 100 MWS HE18-HE21 FIXED $73", "location"),
        ("APS SELLS/MAG BUYS HE18-HE21 PV FIXED $73", "mw"),
        ("APS SELLS/MAG BUYS 100MW PV FIXED $73", "shape"),
    ])
    def test_missing_field_is_an_error(self, text, missing_keyword):
        r = parse(text)
        assert not r.ok
        assert any(missing_keyword in e.lower() for e in r.errors)

    def test_unknown_counterparty_errors(self):
        r = parse("ZZZQQQ sells 100MW LL at midc for midc-3")
        assert not r.ok
        assert any("not recognized" in e for e in r.errors)

    def test_acs_with_unmapped_counterparty_errors(self):
        r = parse("SRP sells 100MW LL ACS at PALOVERDE500 for PALOVERDE+1")
        assert not r.ok
        assert any("Asset Controlling Supplier" in e for e in r.errors)

    def test_nothing_to_parse(self):
        r = parse("")
        assert not r.ok


# --- Fuzzy matching: resolved, but flagged, never silent -------------------

def test_fuzzy_match_resolves_but_is_flagged():
    r = parse("APS SELLS/MAG BUYS 100 MWS HL PALOVERDE50 FIXED $73")
    location = r.fields.get("location")
    assert location is not None
    assert location.value == "PALOVERDE500"
    assert location.confidence == "fuzzy"
    assert any("worth checking" in w for w in r.warnings)
