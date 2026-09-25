"""ui/scheduling/board.py — what gets handed to the custom component.

build_payload() and board_revision() take legs and links as arguments and
touch no session state, so they're checked directly. This is the half of the
board that *can* be tested without a browser: the component's own dragging
and hit-testing can't be, which is exactly why the translation into its
payload is kept out of the component and pinned down here.
"""

from datetime import date

from domain.matching import BUY, SELL, Link, TradeLeg, market_leg_key, market_legs
from ui.scheduling.board import board_revision, build_payload, open_position_line

FLOW = date(2026, 9, 18)


def leg(key, direction, pse="AZPS", por_pod="PALOVERDE500", hours=range(7, 23), mw=100.0):
    return TradeLeg(
        key=key,
        source="db",
        direction=direction,
        pse=pse,
        por_pod=por_pod,
        flow_date=FLOW,
        mw_by_hour={h: mw for h in hours},
        trade_id=int(key.split(":")[1]) if key.startswith("db:") else None,
    )


BUY_A = leg("db:1", BUY)
SELL_B = leg("db:2", SELL, pse="BPAT", por_pod="MIDC", mw=60.0)


class TestSquares:
    def test_one_square_per_real_leg(self):
        squares, _, _ = build_payload([BUY_A, SELL_B], [], None, set())
        assert [s["key"] for s in squares] == ["db:1", "db:2"]
        assert [s["side"] for s in squares] == ["buy", "sell"]

    def test_the_detail_line_is_short_enough_for_a_small_square(self):
        squares, _, _ = build_payload([BUY_A], [], None, set())
        assert squares[0]["name"] == "AZPS"
        assert squares[0]["detail"] == "PALOVERDE500 · 7-22 · 1,600"

    def test_the_tooltip_carries_what_the_square_has_no_room_for(self):
        squares, _, _ = build_payload([BUY_A], [], None, set())
        title = squares[0]["title"]
        assert "100 MW" in title and "1,600 MWh" in title and "Id 1" in title

    def test_the_bar_tracks_how_much_is_matched(self):
        links = [Link("L1", "db:1", "db:2", {h: 50.0 for h in range(7, 23)}, FLOW)]
        squares, _, _ = build_payload([BUY_A, SELL_B], links, None, set())
        by_key = {s["key"]: s for s in squares}
        assert by_key["db:1"]["matched_frac"] == 0.5
        assert by_key["db:2"]["matched_frac"] == 50 / 60

    def test_over_allocation_is_called_out_in_the_tooltip(self):
        links = [Link("L1", "db:1", "db:2", {7: 130.0}, FLOW)]
        squares, _, _ = build_payload([BUY_A, SELL_B], links, None, set())
        assert "over-allocated on HE 7" in squares[0]["title"]

    def test_highlighting_marks_what_is_related_rather_than_dropping_it(self):
        # Dimming happens in the component; the payload only says which
        # squares are in the focused square's neighbourhood.
        squares, _, _ = build_payload([BUY_A, SELL_B], [], "db:1", {"db:1"})
        by_key = {s["key"]: s for s in squares}
        assert by_key["db:1"]["related"] is True
        assert by_key["db:2"]["related"] is False

    def test_with_nothing_focused_everything_is_related(self):
        squares, _, _ = build_payload([BUY_A, SELL_B], [], None, set())
        assert all(s["related"] for s in squares)


class TestMarkets:
    def test_markets_are_rail_chips_not_squares(self):
        # They stay off the canvas so the middle is free for the links —
        # which is the point of making the squares small.
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [Link("L1", "db:1", key, {h: 100.0 for h in range(7, 23)}, FLOW)]
        legs = [BUY_A] + market_legs(links, FLOW)
        squares, markets, _ = build_payload(legs, links, None, set())
        assert [s["key"] for s in squares] == ["db:1"]
        assert {m["name"] for m in markets} == {"CAISO", "SWPW", "SWPP", "AESO", "CEN"}

    def test_an_unused_market_chip_shows_no_number(self):
        _, markets, _ = build_payload([BUY_A], [], None, set())
        assert all(m["mwh"] == "" for m in markets)

    def test_a_used_market_chip_shows_its_mwh(self):
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [Link("L1", "db:1", key, {h: 100.0 for h in range(7, 23)}, FLOW)]
        legs = [BUY_A] + market_legs(links, FLOW)
        _, markets, _ = build_payload(legs, links, None, set())
        assert next(m for m in markets if m["name"] == "CAISO")["mwh"] == "1,600"


class TestWires:
    def test_a_wire_carries_both_ends_and_its_weight(self):
        links = [Link("L1", "db:1", "db:2", {h: 60.0 for h in range(7, 23)}, FLOW)]
        _, _, wires = build_payload([BUY_A, SELL_B], links, None, set())
        assert len(wires) == 1
        assert (wires[0]["buy_key"], wires[0]["sell_key"]) == ("db:1", "db:2")
        assert wires[0]["mwh"] == 960.0
        assert "AZPS" in wires[0]["label"] and "BPAT" in wires[0]["label"]

    def test_a_market_end_names_the_market_so_it_can_anchor_on_the_rail(self):
        key = market_leg_key("CAISO", SELL, FLOW)
        links = [Link("L1", "db:1", key, {7: 100.0}, FLOW)]
        legs = [BUY_A] + market_legs(links, FLOW)
        _, _, wires = build_payload(legs, links, None, set())
        assert wires[0]["sell_market"] == "CAISO"
        assert wires[0]["buy_market"] is None

    def test_a_link_to_a_square_that_is_not_shown_is_not_drawn(self):
        # A filtered-out or cleared square takes its lines with it. Left in,
        # the line hangs off wherever that square last sat, pointing at
        # nothing — the link itself is untouched and comes back with it.
        links = [Link("L1", "db:1", "db:2", {7: 60.0}, FLOW)]
        _, _, wires = build_payload([BUY_A], links, None, set())
        assert wires == []

    def test_the_other_links_still_draw(self):
        links = [
            Link("L1", "db:1", "db:2", {7: 60.0}, FLOW),
            Link("L2", "db:1", "db:9", {7: 10.0}, FLOW),
        ]
        _, _, wires = build_payload([BUY_A, SELL_B], links, None, set())
        assert [w["link_id"] for w in wires] == ["L1"]


class TestRevision:
    def test_it_changes_when_the_links_change(self):
        before = board_revision(FLOW, [BUY_A, SELL_B], [], None)
        after = board_revision(
            FLOW, [BUY_A, SELL_B], [Link("L1", "db:1", "db:2", {7: 10.0}, FLOW)], None
        )
        assert before != after

    def test_it_changes_when_the_selection_changes(self):
        assert board_revision(FLOW, [BUY_A], [], None) != board_revision(
            FLOW, [BUY_A], [], "db:1"
        )

    def test_it_changes_with_the_flow_date(self):
        assert board_revision(FLOW, [BUY_A], [], None) != board_revision(
            date(2026, 9, 19), [BUY_A], [], None
        )

    def test_it_is_stable_across_identical_boards(self):
        # This is what stops a square snapping back to its auto-layout on
        # every rerun — the component only rebuilds when this changes.
        assert board_revision(FLOW, [BUY_A, SELL_B], [], None) == board_revision(
            FLOW, [SELL_B, BUY_A], [], None
        )


class TestOpenPositionLine:
    def test_it_reports_only_open_and_net(self):
        line = open_position_line(
            {
                "buy_mwh": 200,
                "sell_mwh": 60,
                "net_mwh": 140,
                "buy_open_mwh": 140,
                "sell_open_mwh": 0,
                "linked_mwh": 60,
                "link_count": 1,
            }
        )
        assert "Open buys **140**" in line
        assert "Open sells **0**" in line
        assert "Net **140** MWh Long" in line
        assert "200" not in line  # bought/sold totals are deliberately gone

    def test_a_short_book_says_short(self):
        line = open_position_line(
            {"net_mwh": -50, "buy_open_mwh": 0, "sell_open_mwh": 50}
        )
        assert "Net **50** MWh Short" in line
