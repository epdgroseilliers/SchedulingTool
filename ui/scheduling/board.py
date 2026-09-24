"""The board — the adapter between the day's legs and the `trade_board`
custom component.

Nothing is rendered with Streamlit widgets here: the component owns the
canvas, the dragging and the link lines, and this module's whole job is to
(a) turn legs and links into its JSON payload and (b) turn the events it
sends back into state changes. Keeping that translation here rather than in
`components/trade_board/` is what lets the component stay ignorant of what a
leg or a WECC calendar is.
"""

import streamlit as st

from components.trade_board import trade_board
from domain.matching import (
    BUY,
    MARKETS,
    SELL,
    matched_mwh,
    open_mwh,
    over_allocated_hours,
)
from ui.scheduling.state import (
    focused_key,
    hide_leg,
    highlighted_keys,
    market_leg,
    open_bidfile,
    set_focus,
    start_link,
    start_link_edit,
    store_positions,
)

#: The canvas's *minimum* height, in px. It sizes itself to the day — the
#: busier side's squares plus two squares' clear space above the market
#: rail — so this only matters on a near-empty board, where there still has
#: to be somewhere to drag something to.
MIN_BOARD_HEIGHT = 240


def _square(leg, links, related):
    total = leg.mwh
    matched = matched_mwh(leg, links)
    over = over_allocated_hours(leg, links)
    title = (
        f"{leg.direction} {leg.pse} @ {leg.por_pod}\n"
        f"HE {leg.he_label} · {leg.mw_label} · {total:,.0f} MWh\n"
        f"{matched:,.0f} matched · {open_mwh(leg, links):,.0f} open"
    )
    if leg.price:
        title += f"\n{leg.index} {leg.price}".rstrip()
    if leg.trade_id is not None:
        title += f"\nId {leg.trade_id}"
    if over:
        title += "\n⚠ over-allocated on HE " + ", ".join(str(h) for h in sorted(over))
    return {
        "key": leg.key,
        "side": "buy" if leg.direction == BUY else "sell",
        "name": leg.pse or "—",
        "detail": f"{leg.por_pod or '—'} · {leg.he_label} · {total:,.0f}",
        "matched_frac": (matched / total) if total else 0.0,
        "is_market": leg.is_market,
        "related": related,
        "title": title,
    }


def build_payload(legs, links, selected, highlighted):
    """(squares, markets, wires) for the component.

    Markets are *not* squares: they live as chips on the rail, always
    available as a drop target and showing their MWh once something lands on
    them. That keeps the middle of the board clear for the links themselves,
    which is the whole reason the squares are small.
    """
    by_key = {lg.key: lg for lg in legs}
    real = [lg for lg in legs if not lg.is_market]
    squares = [
        _square(lg, links, not highlighted or lg.key in highlighted) for lg in real
    ]

    market_mwh = {name: 0.0 for name in MARKETS}
    for lg in legs:
        if lg.is_market and lg.pse in market_mwh:
            market_mwh[lg.pse] += lg.mwh
    markets = [
        {"name": name, "mwh": f"{mwh:,.0f}" if mwh else ""}
        for name, mwh in market_mwh.items()
    ]

    wires = []
    for ln in links:
        buy, sell = by_key.get(ln.buy_key), by_key.get(ln.sell_key)
        # A link is only drawable when both of its ends are on the board.
        # Filtering or clearing one end has to take its lines with it —
        # otherwise the line hangs off the last place that square happened
        # to be sitting, pointing at nothing. The link itself is untouched:
        # drop the filter and it comes straight back.
        if buy is None or sell is None:
            continue
        wires.append(
            {
                "link_id": ln.link_id,
                "buy_key": ln.buy_key,
                "sell_key": ln.sell_key,
                # Set when that end is a market, so the component can anchor
                # the line on the rail chip instead of a square.
                "buy_market": buy.pse if buy.is_market else None,
                "sell_market": sell.pse if sell.is_market else None,
                "mwh": round(ln.mwh, 1),
                "label": (
                    f"{ln.link_id}: {buy.pse} → {sell.pse} · "
                    f"HE {ln.he_label} · {ln.mwh:,.0f} MWh"
                ),
                "related": (
                    not highlighted
                    or (ln.buy_key in highlighted and ln.sell_key in highlighted)
                ),
            }
        )
    return squares, markets, wires


def board_revision(flow_date, legs, links, selected):
    """What the component watches to decide whether to rebuild.

    Positions are deliberately absent: a trader dragging a square causes a
    rerun, and rebuilding on that would snap everything back to the
    auto-layout mid-drag. Everything that genuinely changes the board *is*
    here.
    """
    return hash(
        (
            str(flow_date),
            tuple(sorted((lg.key, round(lg.mwh, 3)) for lg in legs)),
            tuple(
                sorted(
                    (ln.link_id, ln.buy_key, ln.sell_key, round(ln.mwh, 3))
                    for ln in links
                )
            ),
            selected,
        )
    )


def render_board(legs, links, flow_date):
    """Draw the board and apply whatever the trader did on it. Returns True
    when something changed and the caller should rerun."""
    highlighted = highlighted_keys(legs)
    selected = focused_key()
    squares, markets, wires = build_payload(legs, links, selected, highlighted)

    event = trade_board(
        squares=squares,
        links=wires,
        markets=markets,
        positions=st.session_state.get("mv_positions", {}),
        selected=selected,
        revision=board_revision(flow_date, legs, links, selected),
        height=MIN_BOARD_HEIGHT,
    )
    return handle_event(event, legs, flow_date)


def handle_event(event, legs, flow_date):
    """Apply one board event, ignoring ones already applied.

    Streamlit hands back a component's last value on every rerun, so the
    `seq` the component stamps on each event is the only way to tell a new
    one from the same one being replayed — without it, dragging a square
    would re-fire the link that preceded it on every later rerun.

    But `seq` counts only within one loaded iframe, and Streamlit rebuilds
    that iframe whenever the page is navigated away from and back — the
    counter restarts at 0 while this watermark, living in session state,
    survives. So every event from the fresh frame looked stale and was
    dropped until the counter climbed past the old mark: links that simply
    didn't happen, as often as not, with no pattern to it. The component's
    per-frame `instance` id is what distinguishes "counter restarted" from
    "event replayed"; a new instance resets the watermark.
    """
    if not isinstance(event, dict):
        return False
    seq = event.get("seq")
    if seq is None:
        return False
    instance = event.get("instance")
    if instance != st.session_state.get("mv_board_instance"):
        st.session_state.mv_board_instance = instance
        st.session_state.mv_last_seq = 0
    if seq <= st.session_state.get("mv_last_seq", 0):
        return False
    st.session_state.mv_last_seq = seq

    kind = event.get("type")
    if kind == "move":
        # Positions only — no rerun needed, and forcing one mid-drag is
        # exactly what would make the board feel laggy.
        store_positions(event.get("positions") or {})
        return False
    if kind == "select":
        # Clicking the focused square again clears it, so there's a way back
        # to the undimmed board without hunting for empty canvas.
        key = event.get("key")
        set_focus(None if key and key == focused_key() else key)
        return True
    if kind == "link_click":
        start_link_edit(event.get("link_id"))
        return True
    if kind == "dismiss":
        hide_leg(event.get("key"))
        return True
    if kind == "chip_click":
        open_bidfile(event.get("market"))
        return True
    if kind == "link_request":
        return _request_link(event, legs, flow_date)
    return False


def _request_link(event, legs, flow_date):
    """Turn a dragged link into a pending buy/sell pair.

    Three shapes arrive here: square → square, square → market chip, and
    market chip → square (a link can start from either end, since a market
    is as good a place to start from as a trade).
    """
    by_key = {lg.key: lg for lg in legs}

    from_market = event.get("from_market")
    if from_market:
        target = by_key.get(event.get("to"))
        if target is None or target.is_market:
            return False
        source = market_leg(
            from_market, SELL if target.direction == BUY else BUY, flow_date, legs
        )
        other = target
    else:
        source = by_key.get(event.get("from"))
        if source is None:
            return False
        market = event.get("market")
        if market:
            other = market_leg(
                market, SELL if source.direction == BUY else BUY, flow_date, legs
            )
        else:
            other = by_key.get(event.get("to"))
            if other is None or other.direction == source.direction:
                return False

    buy, sell = (source, other) if source.direction == BUY else (other, source)
    start_link(buy.key, sell.key)
    return True


def open_position_line(totals):
    """The one line of numbers above the board. Only the three the desk
    actually reads — bought/sold totals were noise next to them."""
    net = totals["net_mwh"]
    tone = "Long" if net > 0 else ("Short" if net < 0 else "Flat")
    return (
        f"Open buys **{totals['buy_open_mwh']:,.0f}** MWh &nbsp;·&nbsp; "
        f"Open sells **{totals['sell_open_mwh']:,.0f}** MWh &nbsp;·&nbsp; "
        f"Net **{abs(net):,.0f}** MWh {tone}"
    )
