"""domain/matching.py — the Phase 2 matching rules.

Pure: no Streamlit, no DB, no calendar connection. The flow date's peak flag
is passed in as a plain bool everywhere precisely so these stay that way.
"""

from datetime import date

import pytest

from domain.matching import (
    BUY,
    SELL,
    Link,
    TradeLeg,
    allocated_by_hour,
    board_totals,
    expand_he,
    filter_legs,
    is_open,
    leg_from_row,
    legs_from_db_rows,
    legs_from_session_trades,
    links_on,
    make_link,
    market_leg_key,
    market_legs,
    matched_mwh,
    open_by_hour,
    open_mwh,
    over_allocated_hours,
    parse_market_key,
    sort_legs,
    suggest_allocation,
)

FLOW = date(2026, 9, 18)


def leg(key, direction=BUY, mw_by_hour=None, pse="AZPS", por_pod="PALOVERDE500", source="db"):
    return TradeLeg(
        key=key,
        source=source,
        direction=direction,
        pse=pse,
        por_pod=por_pod,
        flow_date=FLOW,
        mw_by_hour=dict(mw_by_hour or {h: 100.0 for h in range(7, 23)}),
    )


class TestExpandHe:
    def test_hl_on_a_peak_day_is_he7_22(self):
        assert expand_he("HL", True) == set(range(7, 23))

    def test_hl_on_an_off_peak_day_flows_nothing(self):
        # Not an error — an HL leg spanning a weekend simply has no hours on
        # the off-peak days, which is why the leg is dropped entirely.
        assert expand_he("HL", False) == set()

    def test_ll_on_a_peak_day_is_the_shoulders(self):
        assert expand_he("LL", True) == {1, 2, 3, 4, 5, 6, 23, 24}

    def test_ll_on_an_off_peak_day_is_the_whole_day(self):
        assert expand_he("LL", False) == set(range(1, 25))

    def test_atc_is_the_whole_day_either_way(self):
        assert expand_he("ATC", True) == set(range(1, 25))
        assert expand_he("ATC", False) == set(range(1, 25))

    def test_explicit_range(self):
        assert expand_he("17-22", True) == {17, 18, 19, 20, 21, 22}

    def test_explicit_comma_joined_ranges_round_trip_hours_to_he_string(self):
        assert expand_he("1-6,23-24", True) == {1, 2, 3, 4, 5, 6, 23, 24}

    def test_single_bare_hour(self):
        assert expand_he("14", True) == {14}

    def test_lowercase_and_padding_are_tolerated(self):
        assert expand_he("  hl ", True) == set(range(7, 23))

    def test_peak_dependent_label_without_a_calendar_row_is_an_error(self):
        with pytest.raises(ValueError, match="calendar"):
            expand_he("HL", None)

    def test_atc_still_works_without_a_calendar_row(self):
        assert expand_he("ATC", None) == set(range(1, 25))

    def test_unreadable_he_is_an_error(self):
        with pytest.raises(ValueError):
            expand_he("banana", True)


class TestLegFromRow:
    def row(self, **over):
        base = {
            "start_date": date(2026, 9, 17),
            "stop_date": date(2026, 9, 19),
            "he": "HL",
            "mw": 100,
            "direction": BUY,
            "pse": "AZPS",
            "por_pod": "PALOVERDE500",
            "price": "73",
            "index": "",
            "trade_id": 42,
        }
        base.update(over)
        return base

    def test_a_multi_day_row_yields_this_days_leg(self):
        lg = leg_from_row(self.row(), FLOW, True, "db:42", "db")
        assert lg.hours == list(range(7, 23))
        assert lg.mwh == 1600
        assert lg.flow_date == FLOW

    def test_a_row_outside_the_flow_date_yields_nothing(self):
        lg = leg_from_row(self.row(), date(2026, 9, 25), True, "db:42", "db")
        assert lg is None

    def test_an_hl_row_on_an_off_peak_day_yields_nothing(self):
        assert leg_from_row(self.row(), FLOW, False, "db:42", "db") is None

    def test_it_carries_the_whole_trades_range_not_just_this_day(self):
        # Linking on one day has to know which other days the same trade
        # flows — see ui.scheduling.state.propagate_link.
        lg = leg_from_row(self.row(), FLOW, True, "db:42", "db")
        assert (lg.start_date, lg.stop_date) == (date(2026, 9, 17), date(2026, 9, 19))

    def test_he_label_and_mw_label_describe_the_day(self):
        lg = leg_from_row(self.row(he="17-22", mw=50), FLOW, True, "db:1", "db")
        assert lg.he_label == "17-22"
        assert lg.mw_label == "50 MW"
        assert lg.mwh == 300


class TestLegsFromDbRows:
    def test_one_square_per_row(self):
        rows = [
            {
                "start_date": FLOW, "stop_date": FLOW, "he": "ATC", "mw": 25,
                "direction": BUY, "pse": "AZPS", "por_pod": "PV", "trade_id": 1,
            },
            {
                "start_date": FLOW, "stop_date": FLOW, "he": "HL", "mw": 50,
                "direction": SELL, "pse": "BPAT", "por_pod": "MIDC", "trade_id": 2,
            },
        ]
        legs, warnings = legs_from_db_rows(rows, FLOW, True)
        assert [lg.key for lg in legs] == ["db:1", "db:2"]
        assert warnings == []

    def test_an_unreadable_he_is_reported_not_raised(self):
        rows = [{
            "start_date": FLOW, "stop_date": FLOW, "he": "???", "mw": 25,
            "direction": BUY, "pse": "AZPS", "por_pod": "PV", "trade_id": 7,
        }]
        legs, warnings = legs_from_db_rows(rows, FLOW, True)
        assert legs == []
        assert len(warnings) == 1 and "7" in warnings[0]


class TestLegsFromSessionTrades:
    def test_an_hourly_trade_becomes_this_dates_leg(self):
        trades = [{
            "direction": BUY, "counterparty": "AZPS", "location": "PALOVERDE500",
            "price": 73.0, "index": None,
            "schedule": [(FLOW, 7, 100.0), (FLOW, 8, 100.0), (date(2026, 9, 19), 7, 100.0)],
        }]
        legs, warnings = legs_from_session_trades(trades, FLOW, True)
        assert len(legs) == 1
        assert legs[0].hours == [7, 8]
        assert warnings == []
        # This day's hours, but the whole grid's range — the 19th is in it.
        assert (legs[0].start_date, legs[0].stop_date) == (FLOW, date(2026, 9, 19))

    def test_a_trade_already_in_the_db_is_skipped(self):
        # It comes back through legs_from_db_rows instead; counting it here
        # as well would double the book.
        trades = [{
            "direction": BUY, "counterparty": "AZPS", "location": "PV",
            "db_trade_ids": [90001],
            "schedule": [(FLOW, 7, 100.0)],
        }]
        assert legs_from_session_trades(trades, FLOW, True)[0] == []

    def test_a_monthly_trade_is_read_from_its_blocks(self):
        trades = [{
            "direction": SELL, "counterparty": "BPAT", "location": "MIDC",
            "is_monthly": True,
            "monthly_blocks": [
                {"start_date": date(2026, 9, 1), "stop_date": date(2026, 9, 30),
                 "he": "ATC", "mw": 25.0}
            ],
        }]
        legs, _ = legs_from_session_trades(trades, FLOW, True)
        assert len(legs) == 1 and legs[0].mwh == 600

    def test_a_trade_that_does_not_reach_this_date_is_absent(self):
        trades = [{
            "direction": BUY, "counterparty": "AZPS", "location": "PV",
            "schedule": [(date(2026, 9, 25), 7, 100.0)],
        }]
        assert legs_from_session_trades(trades, FLOW, True)[0] == []


class TestOpenAndMatched:
    def test_an_unlinked_leg_is_entirely_open(self):
        lg = leg("db:1")
        assert open_mwh(lg, []) == lg.mwh
        assert matched_mwh(lg, []) == 0
        assert is_open(lg, [])

    def test_a_link_reduces_the_open_position_hour_by_hour(self):
        lg = leg("db:1", mw_by_hour={7: 100.0, 8: 100.0})
        links = [Link("L1", "db:1", "db:2", {7: 40.0}, FLOW)]
        assert open_by_hour(lg, links) == {7: 60.0, 8: 100.0}
        assert matched_mwh(lg, links) == 40
        assert open_mwh(lg, links) == 160

    def test_several_links_on_one_leg_accumulate(self):
        # Many-to-many is the point: one buy covered by two sells.
        lg = leg("db:1", mw_by_hour={7: 100.0})
        links = [
            Link("L1", "db:1", "db:2", {7: 60.0}, FLOW),
            Link("L2", "db:1", "db:3", {7: 40.0}, FLOW),
        ]
        assert allocated_by_hour("db:1", links) == {7: 100.0}
        assert open_by_hour(lg, links) == {}
        assert not is_open(lg, links)

    def test_over_allocation_is_reported_rather_than_clamped(self):
        # The trader can overwrite any suggested allocation, so committing
        # more than the trade carries has to be possible — and visible.
        lg = leg("db:1", mw_by_hour={7: 100.0})
        links = [Link("L1", "db:1", "db:2", {7: 130.0}, FLOW)]
        assert over_allocated_hours(lg, links) == {7: 30.0}
        assert open_by_hour(lg, links) == {}  # never negative
        assert open_mwh(lg, links) == 0


class TestSuggestAllocation:
    def test_overlapping_hours_at_the_smaller_side(self):
        buy = leg("db:1", BUY, {7: 100.0, 8: 100.0, 9: 100.0})
        sell = leg("db:2", SELL, {8: 60.0, 9: 150.0, 10: 50.0})
        assert suggest_allocation(buy, sell, []) == {8: 60.0, 9: 100.0}

    def test_no_shared_hours_suggests_nothing(self):
        buy = leg("db:1", BUY, {7: 100.0})
        sell = leg("db:2", SELL, {20: 100.0})
        assert suggest_allocation(buy, sell, []) == {}

    def test_it_never_re_commits_what_another_link_already_took(self):
        buy = leg("db:1", BUY, {7: 100.0})
        sell = leg("db:2", SELL, {7: 100.0})
        existing = [Link("L1", "db:1", "db:3", {7: 70.0}, FLOW)]
        assert suggest_allocation(buy, sell, existing) == {7: 30.0}

    def test_a_market_takes_the_other_sides_whole_open_schedule(self):
        # A market is a sink with no schedule of its own, so there's nothing
        # to overlap with — linking to one parks whatever is still open.
        buy = leg("db:1", BUY, {7: 100.0, 8: 40.0})
        market = TradeLeg(
            key=market_leg_key("CAISO", SELL, FLOW),
            source="market",
            direction=SELL,
            pse="CAISO",
            por_pod="Market",
            flow_date=FLOW,
        )
        assert suggest_allocation(buy, market, []) == {7: 100.0, 8: 40.0}

    def test_a_market_only_takes_what_other_links_left_open(self):
        buy = leg("db:1", BUY, {7: 100.0})
        market = TradeLeg(
            key=market_leg_key("CAISO", SELL, FLOW),
            source="market",
            direction=SELL,
            pse="CAISO",
            por_pod="Market",
            flow_date=FLOW,
        )
        existing = [Link("L1", "db:1", "db:2", {7: 75.0}, FLOW)]
        assert suggest_allocation(buy, market, existing) == {7: 25.0}

    def test_a_fully_allocated_pair_suggests_nothing(self):
        buy = leg("db:1", BUY, {7: 100.0})
        sell = leg("db:2", SELL, {7: 100.0})
        existing = [Link("L1", "db:1", "db:2", {7: 100.0}, FLOW)]
        assert suggest_allocation(buy, sell, existing) == {}


class TestMakeLink:
    def test_zero_and_negative_hours_are_dropped(self):
        link = make_link("L1", leg("db:1", BUY), leg("db:2", SELL), {7: 50.0, 8: 0.0, 9: -5})
        assert link.mw_by_hour == {7: 50.0}
        assert link.mwh == 50
        assert link.he_label == "7"

    def test_it_records_which_key_is_which_side(self):
        link = make_link("L1", leg("db:1", BUY), leg("db:2", SELL), {7: 50.0})
        assert (link.buy_key, link.sell_key) == ("db:1", "db:2")
        assert link.other_key("db:1") == "db:2"
        assert link.touches("db:2") and not link.touches("db:9")

    def test_it_takes_the_flow_date_from_the_legs(self):
        # A leg's key doesn't carry the date, so without this a two-day
        # trade's two squares share one link and one allocation.
        link = make_link("L1", leg("db:1", BUY), leg("db:2", SELL), {7: 50.0})
        assert link.flow_date == FLOW


class TestLinksOn:
    def test_it_keeps_only_that_days_links(self):
        other = date(2026, 9, 19)
        today = Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)
        tomorrow = Link("L2", "db:1", "db:2", {7: 50.0}, other)
        assert links_on([today, tomorrow], FLOW) == [today]
        assert links_on([today, tomorrow], other) == [tomorrow]

    def test_an_undated_link_belongs_to_no_day(self):
        # Rather than to every day, which is exactly the bug: one link
        # rendered on each day of a trade, sharing one schedule.
        undated = Link("L1", "db:1", "db:2", {7: 50.0})
        assert links_on([undated], FLOW) == []
        assert links_on([undated], None) == []


class TestMarketLegs:
    def test_a_market_square_appears_only_because_a_link_reaches_it(self):
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [Link("L1", "db:1", key, {7: 50.0, 8: 50.0}, FLOW)]
        markets = market_legs(links, FLOW)
        assert len(markets) == 1
        m = markets[0]
        assert (m.pse, m.direction, m.is_market) == ("CAISO", SELL, True)
        assert m.mwh == 100

    def test_no_links_means_no_market_squares(self):
        assert market_legs([], FLOW) == []

    def test_another_days_links_do_not_put_a_chip_on_this_day(self):
        # A market key carries its own date, so a chip derived from another
        # day's link is one this day's bid-file builder can't then see —
        # the chip and the builder disagreeing about the same market.
        other = date(2026, 9, 19)
        key = market_leg_key("CAISO", SELL, other)
        assert market_legs([Link("L1", "db:1", key, {7: 50.0}, other)], FLOW) == []

    def test_a_market_leg_has_no_trade_range_behind_it(self):
        key = market_leg_key("CAISO", SELL, FLOW)
        m = market_legs([Link("L1", "db:1", key, {7: 50.0}, FLOW)], FLOW)[0]
        assert (m.start_date, m.stop_date) == (None, None)

    def test_several_links_to_one_market_accumulate_into_one_square(self):
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [
            Link("L1", "db:1", key, {7: 50.0}, FLOW),
            Link("L2", "db:2", key, {7: 25.0, 8: 10.0}, FLOW),
        ]
        markets = market_legs(links, FLOW)
        assert len(markets) == 1
        assert markets[0].mw_by_hour == {7: 75.0, 8: 10.0}

    def test_key_round_trip(self):
        key = market_leg_key("AESO", BUY, FLOW)
        assert parse_market_key(key) == ("AESO", BUY)
        assert parse_market_key("db:42") is None


class TestBoardTotals:
    def test_counts_both_sides_and_what_is_still_open(self):
        buy = leg("db:1", BUY, {7: 100.0, 8: 100.0})
        sell = leg("db:2", SELL, {7: 60.0})
        links = [Link("L1", "db:1", "db:2", {7: 60.0}, FLOW)]
        totals = board_totals([buy, sell], links)
        assert totals["buy_mwh"] == 200
        assert totals["sell_mwh"] == 60
        assert totals["net_mwh"] == 140
        assert totals["buy_open_mwh"] == 140
        assert totals["sell_open_mwh"] == 0
        assert totals["linked_mwh"] == 60
        assert totals["link_count"] == 1

    def test_market_squares_are_excluded_from_the_book(self):
        # A market is a placeholder for spot exposure, not a trade — adding
        # it to "bought"/"sold" would double-count the position it absorbs.
        key = market_leg_key("CAISO", SELL, FLOW)
        buy = leg("db:1", BUY, {7: 100.0})
        links = [Link("L1", "db:1", key, {7: 100.0}, FLOW)]
        totals = board_totals([buy] + market_legs(links, FLOW), links)
        assert totals["sell_mwh"] == 0
        assert totals["buy_open_mwh"] == 0


class TestSortAndFilter:
    def test_open_positions_come_first(self):
        open_leg = leg("db:open", BUY, {7: 100.0}, pse="ZZZZ")
        done_leg = leg("db:done", BUY, {7: 100.0}, pse="AAAA")
        links = [Link("L1", "db:done", "db:2", {7: 100.0}, FLOW)]
        assert [lg.key for lg in sort_legs([done_leg, open_leg], links)] == [
            "db:open",
            "db:done",
        ]

    def test_filters_narrow_what_is_shown(self):
        a = leg("db:1", BUY, pse="AZPS", por_pod="PALOVERDE500")
        b = leg("db:2", BUY, pse="BPAT", por_pod="MIDC")
        assert [lg.key for lg in filter_legs([a, b], pses=["BPAT"])] == ["db:2"]
        assert [lg.key for lg in filter_legs([a, b], por_pods=["PALOVERDE500"])] == ["db:1"]
        assert len(filter_legs([a, b])) == 2

    def test_markets_survive_every_filter(self):
        # A market is a chip on the board's rail, not a square, and is
        # always somewhere to park a position — a PSE filter naming a
        # counterparty would otherwise take the markets away with it.
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [Link("L1", "db:1", key, {7: 50.0}, FLOW)]
        legs = [leg("db:1", BUY, pse="AZPS")] + market_legs(links, FLOW)
        kept = filter_legs(legs, pses=["BPAT"], por_pods=["MIDC"])
        assert [lg.pse for lg in kept] == ["CAISO"]

    def test_filtering_does_not_touch_the_links(self):
        # Filters narrow the view, not the data — PROJECT.md is explicit.
        a = leg("db:1", BUY)
        links = [Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)]
        filter_legs([a], pses=["NOBODY"])
        assert links[0].mw_by_hour == {7: 50.0}


class TestFilterRescue:
    """Filtering down to one PSE must not hide the counterparty on a link
    that PSE is actively part of — see filter_legs' own docstring for the
    reasoning; these pin the exact rule down."""

    def test_a_linked_leg_that_fails_the_filter_is_rescued(self):
        azps = leg("db:1", BUY, pse="AZPS")
        conc = leg("db:2", SELL, pse="CONC")
        links = [Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)]
        kept = filter_legs([azps, conc], links=links, pses=["AZPS"])
        assert {lg.key for lg in kept} == {"db:1", "db:2"}

    def test_without_links_nothing_is_rescued(self):
        # Backward compatible: omitting links reproduces the old behavior.
        azps = leg("db:1", BUY, pse="AZPS")
        conc = leg("db:2", SELL, pse="CONC")
        kept = filter_legs([azps, conc], pses=["AZPS"])
        assert {lg.key for lg in kept} == {"db:1"}

    def test_an_unlinked_leg_that_fails_the_filter_stays_out(self):
        azps = leg("db:1", BUY, pse="AZPS")
        unrelated = leg("db:2", SELL, pse="CONC")
        kept = filter_legs([azps, unrelated], links=[], pses=["AZPS"])
        assert {lg.key for lg in kept} == {"db:1"}

    def test_rescue_works_from_either_side_of_the_link(self):
        azps = leg("db:1", BUY, pse="AZPS")
        conc = leg("db:2", SELL, pse="CONC")
        links = [Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)]
        # Filtering to the *sell* side rescues the buy side just the same.
        kept = filter_legs([azps, conc], links=links, pses=["CONC"])
        assert {lg.key for lg in kept} == {"db:1", "db:2"}

    def test_rescue_applies_to_the_por_pod_filter_too(self):
        azps = leg("db:1", BUY, pse="AZPS", por_pod="PALOVERDE500")
        conc = leg("db:2", SELL, pse="CONC", por_pod="MIDC")
        links = [Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)]
        kept = filter_legs([azps, conc], links=links, por_pods=["PALOVERDE500"])
        assert {lg.key for lg in kept} == {"db:1", "db:2"}

    def test_rescue_is_one_hop_only(self):
        # A -> B -> C: filtering to A's PSE rescues B (linked to A) but not
        # C (linked only to B, not to A) — otherwise a well-connected book
        # could reconstitute itself through a filter meant to narrow it.
        a = leg("db:a", BUY, pse="AZPS")
        b = leg("db:b", SELL, pse="BPAT")
        c = leg("db:c", BUY, pse="PACE")
        links = [
            Link("L1", "db:a", "db:b", {7: 50.0}, FLOW),
            Link("L2", "db:c", "db:b", {7: 50.0}, FLOW),
        ]
        kept = filter_legs([a, b, c], links=links, pses=["AZPS"])
        assert {lg.key for lg in kept} == {"db:a", "db:b"}

    def test_a_leg_kept_only_via_the_market_bypass_does_not_rescue_through(self):
        # A real leg linked only to an always-visible market chip is not,
        # on that account, a rescue hub for the market's other links.
        key = market_leg_key("CAISO", SELL, FLOW)
        a = leg("db:a", BUY, pse="AZPS")
        b = leg("db:b", BUY, pse="BPAT")
        links = [
            Link("L1", "db:a", key, {7: 50.0}, FLOW),
            Link("L2", "db:b", key, {7: 50.0}, FLOW),
        ]
        legs = [a, b] + market_legs(links, FLOW)
        # Filtering to a PSE neither A nor B belongs to: the market chip
        # still shows (it always does), but B must not be rescued just
        # because it shares that always-visible market with A.
        kept = filter_legs(legs, links=links, pses=["SOMEONE ELSE"])
        assert {lg.key for lg in kept} == {key}

    def test_no_filter_at_all_returns_everything_unchanged(self):
        a = leg("db:1", BUY)
        b = leg("db:2", SELL)
        assert filter_legs([a, b], links=[Link("L1", "db:1", "db:2", {7: 1.0}, FLOW)]) == [a, b]
