"""domain/bidfiles.py — grouping SWPW links by counterparty, and validating
a trader's split of that grouping into GCA/LCA bid lines.

Pure: builds its own legs/links rather than going through the board.
"""

from datetime import date

from domain.bidfiles import (
    DEFAULT_PRICE,
    LONG,
    SHORT,
    blank_line,
    build_bid_lines,
    is_active,
    market_groups,
    ppt_to_ept,
    rebalance_hour,
    validate_split,
)
from domain.matching import BUY, SELL, Link, TradeLeg, market_leg_key

FLOW = date(2026, 9, 23)
MARKET = "SWPW"


def real_leg(key, direction, pse, hours=range(1, 25), mw=100.0):
    return TradeLeg(
        key=key, source="db", direction=direction, pse=pse, por_pod="X",
        flow_date=FLOW, mw_by_hour={h: mw for h in hours},
    )


def market_leg(direction):
    return TradeLeg(
        key=market_leg_key(MARKET, direction, FLOW), source="market",
        direction=direction, pse=MARKET, por_pod="Market", flow_date=FLOW,
    )


class TestMarketGroups:
    def test_a_link_to_a_mag_buy_is_a_short_position(self):
        # MAG buys -> the seller's generation needs wheeling in -> SHORT.
        azps = real_leg("db:1", BUY, "AZPS")
        mkt = market_leg(SELL)  # buy=azps, sell=market, per ui.scheduling.board
        links = [Link("L1", "db:1", mkt.key, {7: 50.0}, FLOW)]
        groups = market_groups(MARKET, [azps, mkt], links, FLOW)
        assert groups == {(SHORT, "AZPS"): {7: 50.0}}

    def test_a_link_to_a_mag_sale_is_a_long_position(self):
        azps = real_leg("db:1", SELL, "AZPS")
        mkt = market_leg(BUY)  # buy=market, sell=azps
        links = [Link("L1", mkt.key, "db:1", {7: 50.0}, FLOW)]
        groups = market_groups(MARKET, [azps, mkt], links, FLOW)
        assert groups == {(LONG, "AZPS"): {7: 50.0}}

    def test_two_links_to_the_same_counterparty_are_summed(self):
        azps = real_leg("db:1", BUY, "AZPS")
        mkt = market_leg(SELL)
        links = [
            Link("L1", "db:1", mkt.key, {7: 50.0}, FLOW),
            Link("L2", "db:1", mkt.key, {7: 20.0, 8: 30.0}, FLOW),
        ]
        groups = market_groups(MARKET, [azps, mkt], links, FLOW)
        assert groups == {(SHORT, "AZPS"): {7: 70.0, 8: 30.0}}

    def test_a_link_to_a_different_market_is_ignored(self):
        azps = real_leg("db:1", BUY, "AZPS")
        other_mkt = TradeLeg(
            key=market_leg_key("CAISO", SELL, FLOW), source="market",
            direction=SELL, pse="CAISO", por_pod="Market", flow_date=FLOW,
        )
        links = [Link("L1", "db:1", other_mkt.key, {7: 50.0}, FLOW)]
        assert market_groups(MARKET, [azps, other_mkt], links, FLOW) == {}

    def test_a_link_between_two_real_legs_is_ignored(self):
        a = real_leg("db:1", BUY, "AZPS")
        b = real_leg("db:2", SELL, "BPAT")
        links = [Link("L1", "db:1", "db:2", {7: 50.0}, FLOW)]
        assert market_groups(MARKET, [a, b], links, FLOW) == {}

    def test_both_sides_can_appear_for_the_same_counterparty(self):
        buy_leg = real_leg("db:1", BUY, "AZPS")
        sell_leg = real_leg("db:2", SELL, "AZPS")
        mkt_sell = market_leg(SELL)
        mkt_buy = market_leg(BUY)
        links = [
            Link("L1", "db:1", mkt_sell.key, {7: 50.0}, FLOW),
            Link("L2", mkt_buy.key, "db:2", {7: 30.0}, FLOW),
        ]
        groups = market_groups(MARKET, [buy_leg, sell_leg, mkt_sell, mkt_buy], links, FLOW)
        assert groups == {(SHORT, "AZPS"): {7: 50.0}, (LONG, "AZPS"): {7: 30.0}}


class TestPptToEpt:
    """The app is PPT throughout; the bid file's hour column is EPT."""

    def test_the_offset_is_three_hours(self):
        assert ppt_to_ept(1) == (4, False)
        assert ppt_to_ept(12) == (15, False)

    def test_the_last_hour_before_midnight_eastern_still_fits(self):
        assert ppt_to_ept(21) == (24, False)

    def test_the_final_three_hours_spill_into_the_next_eastern_day(self):
        # This is what the file's `1*`/`2*`/`3*` rows are.
        assert ppt_to_ept(22) == (1, True)
        assert ppt_to_ept(23) == (2, True)
        assert ppt_to_ept(24) == (3, True)

    def test_a_full_ppt_day_covers_24_distinct_slots(self):
        slots = [ppt_to_ept(h) for h in range(1, 25)]
        assert len(set(slots)) == 24

    def test_an_ll_shape_lands_where_the_real_files_put_it(self):
        # PPT off-peak (domain.shapes: HE1-6 + HE23-24) is EPT HE4-9 plus
        # two starred rows — exactly the real SPP-LONG(CISO) column.
        off_peak = list(range(1, 7)) + [23, 24]
        assert [ppt_to_ept(h) for h in off_peak] == [
            (4, False), (5, False), (6, False), (7, False), (8, False), (9, False),
            (2, True), (3, True),
        ]


class TestBlankLine:
    def test_a_short_opens_at_zero_and_a_long_at_fifty(self):
        assert blank_line(SHORT)["price"] == 0.0
        assert blank_line(LONG)["price"] == 50.0
        assert DEFAULT_PRICE == {SHORT: 0.0, LONG: 50.0}

    def test_it_carries_no_code_and_copies_any_schedule_given(self):
        schedule = {7: 100.0}
        line = blank_line(SHORT, schedule)
        assert line["code"] == ""
        assert line["mw_by_hour"] == schedule
        line["mw_by_hour"][8] = 50.0
        assert schedule == {7: 100.0}  # a copy, not the caller's dict

    def test_a_fresh_split_starts_empty(self):
        # What "+ Split" adds: no MW at all, so the first line still holds
        # the whole position until the trader moves some of it across.
        assert blank_line(SHORT)["mw_by_hour"] == {}


class TestRebalanceHour:
    def test_the_other_line_takes_the_rest(self):
        # The everyday case: 100 MW split two ways, the trader types 40
        # into the new column and the original drops to 60 on its own.
        assert rebalance_hour([100.0, 0.0], 1, 40.0, 100.0) == [60.0, 40.0]

    def test_it_works_the_same_from_the_first_column(self):
        assert rebalance_hour([100.0, 0.0], 0, 70.0, 100.0) == [70.0, 30.0]

    def test_a_single_line_is_left_alone(self):
        # Nothing to rebalance against; validate_split reports the gap.
        assert rebalance_hour([100.0], 0, 60.0, 100.0) == [60.0]

    def test_three_lines_share_the_residual_in_proportion(self):
        assert rebalance_hour([60.0, 30.0, 10.0], 0, 20.0, 100.0) == [20.0, 60.0, 20.0]

    def test_an_all_empty_remainder_lands_on_the_first_of_them(self):
        assert rebalance_hour([0.0, 0.0, 0.0], 2, 30.0, 100.0) == [70.0, 0.0, 30.0]

    def test_an_entry_over_the_day_is_kept_and_the_others_go_to_zero(self):
        # Not trimmed back: validate_split reports the overrun rather than
        # this quietly rewriting what was just typed.
        assert rebalance_hour([100.0, 0.0], 1, 150.0, 100.0) == [0.0, 150.0]

    def test_taking_the_whole_hour_empties_the_others(self):
        assert rebalance_hour([60.0, 40.0], 0, 100.0, 100.0) == [100.0, 0.0]

    def test_a_negative_entry_is_floored_at_zero(self):
        assert rebalance_hour([100.0, 0.0], 1, -5.0, 100.0) == [100.0, 0.0]

    def test_the_hour_still_adds_up_exactly_despite_rounding(self):
        out = rebalance_hour([50.0, 50.0], 0, 10.0, 100.0)
        assert sum(out) == 100.0

    def test_a_thirds_split_still_adds_up_exactly(self):
        out = rebalance_hour([10.0, 10.0, 10.0], 0, 1.0, 100.0)
        assert out[0] == 1.0
        assert sum(out) == 100.0


class TestIsActive:
    def test_any_nonzero_hour_is_active(self):
        assert is_active({7: 50.0, 8: 0.0})

    def test_all_zero_or_empty_is_not_active(self):
        assert not is_active({7: 0.0})
        assert not is_active({})
        assert not is_active(None)


class TestValidateSplit:
    def test_a_single_unsplit_line_needs_a_code_and_price(self):
        agg = {7: 100.0}
        errors = validate_split(SHORT, "AZPS", agg, [{"code": "", "price": None, "mw_by_hour": agg}])
        assert any("code" in e for e in errors)
        assert any("price" in e for e in errors)

    def test_a_complete_single_line_has_no_errors(self):
        agg = {7: 100.0}
        line = {"code": "AZPS", "price": -1.0, "mw_by_hour": dict(agg)}
        assert validate_split(SHORT, "AZPS", agg, [line]) == []

    def test_a_reconciled_split_has_no_errors(self):
        agg = {7: 100.0, 8: 100.0}
        lines = [
            {"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 60.0, 8: 60.0}},
            {"code": "TEPC", "price": 0.0, "mw_by_hour": {7: 40.0, 8: 40.0}},
        ]
        assert validate_split(SHORT, "AZPS", agg, lines) == []

    def test_a_split_that_does_not_add_up_is_an_error(self):
        agg = {7: 100.0}
        lines = [
            {"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 60.0}},
            {"code": "TEPC", "price": 0.0, "mw_by_hour": {7: 30.0}},
        ]
        errors = validate_split(SHORT, "AZPS", agg, lines)
        assert any("HE7" in e and "90" in e and "100" in e for e in errors)

    def test_an_untouched_extra_split_slot_is_silently_ignored(self):
        # "+ Split" adds an empty slot; leaving it empty must not force the
        # trader to fill in a code/price for a line that bids nothing.
        agg = {7: 100.0}
        lines = [
            {"code": "AZPS", "price": -1.0, "mw_by_hour": dict(agg)},
            {"code": "", "price": None, "mw_by_hour": {7: 0.0}},
        ]
        assert validate_split(SHORT, "AZPS", agg, lines) == []

    def test_an_hour_the_schedule_does_not_have_is_still_checked(self):
        # A split line can't invent MW on an hour the aggregate never had.
        agg = {7: 100.0}
        lines = [{"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 100.0, 8: 20.0}}]
        errors = validate_split(SHORT, "AZPS", agg, lines)
        assert any("HE8" in e for e in errors)


class TestBuildBidLines:
    def test_an_unsplit_group_becomes_one_line_needing_input(self):
        groups = {(SHORT, "AZPS"): {7: 100.0}}
        short, long_, errors = build_bid_lines(groups, {})
        assert short == [] and long_ == []
        assert any("code" in e for e in errors)

    def test_a_filled_in_unsplit_group_becomes_one_line(self):
        groups = {(SHORT, "AZPS"): {7: 100.0}}
        split_state = {(SHORT, "AZPS"): [{"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 100.0}}]}
        short, long_, errors = build_bid_lines(groups, split_state)
        assert errors == []
        assert short == [{"pse": "AZPS", "code": "AZPS", "price": -1.0, "mw_by_hour": {7: 100.0}}]
        assert long_ == []

    def test_short_and_long_are_kept_separate(self):
        groups = {(SHORT, "AZPS"): {7: 100.0}, (LONG, "BPAT"): {7: 50.0}}
        split_state = {
            (SHORT, "AZPS"): [{"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 100.0}}],
            (LONG, "BPAT"): [{"code": "BPAT", "price": 50.0, "mw_by_hour": {7: 50.0}}],
        }
        short, long_, errors = build_bid_lines(groups, split_state)
        assert errors == []
        assert [l["pse"] for l in short] == ["AZPS"]
        assert [l["pse"] for l in long_] == ["BPAT"]

    def test_a_split_group_becomes_two_lines(self):
        groups = {(SHORT, "AZPS"): {7: 100.0}}
        split_state = {
            (SHORT, "AZPS"): [
                {"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 60.0}},
                {"code": "TEPC", "price": 0.0, "mw_by_hour": {7: 40.0}},
            ]
        }
        short, long_, errors = build_bid_lines(groups, split_state)
        assert errors == []
        assert [l["code"] for l in short] == ["AZPS", "TEPC"]

    def test_no_groups_at_all_is_an_error_not_an_empty_file(self):
        short, long_, errors = build_bid_lines({}, {})
        assert short == [] and long_ == []
        assert errors == ["No open position through this market — nothing to bid."]

    def test_a_broken_line_is_never_silently_included(self):
        groups = {(SHORT, "AZPS"): {7: 100.0}}
        split_state = {(SHORT, "AZPS"): [{"code": "", "price": None, "mw_by_hour": {7: 100.0}}]}
        short, long_, errors = build_bid_lines(groups, split_state)
        assert short == [] and long_ == []
        assert errors  # reported, not silently dropped into a half-built file
