"""Session state for the Scheduling View, and assembling the day's legs.

Links are **session-only** for now, by decision: nothing here is written
back to any database. That mirrors how Phase 1 itself started — trades lived
in `st.session_state.trades` long before `BilateralTrades` writes existed —
and keeps the interaction model free to change before a schema is committed
to it.

Every key this module owns is prefixed `mv_` (matching view) so it can't
collide with the Add Trade page's state in the same session.
"""

import streamlit as st

from data.calendar import is_peak_map
from data.matching import load_trades_for_flow_date
from domain.matching import (
    Link,
    TradeLeg,
    legs_from_db_rows,
    legs_from_session_trades,
    make_link,
    market_leg_key,
    market_legs,
    parse_market_key,
)

SOURCE_DB = "Database"
SOURCE_SESSION = "This session"
ALL_SOURCES = [SOURCE_DB, SOURCE_SESSION]


def init_matching_state():
    """Seed every key the view reads before it's written. Safe on every
    rerun — each guard fires once per session."""
    if "mv_links" not in st.session_state:
        st.session_state.mv_links = []
    if "mv_next_link_id" not in st.session_state:
        st.session_state.mv_next_link_id = 1
    # The square the trader last clicked: what the board highlights around
    # and what the detail panel describes.
    if "mv_focus" not in st.session_state:
        st.session_state.mv_focus = None
    # Where the trader has dragged each square, {key: [x, y]} in board
    # pixels. Held here so a square stays where it was put across the reruns
    # that linking and filtering cause.
    if "mv_positions" not in st.session_state:
        st.session_state.mv_positions = {}
    # The last board event applied, and which loaded iframe it came from —
    # see ui.scheduling.board.handle_event. The instance is what makes the
    # watermark safe across a page navigation, which rebuilds the component
    # and restarts its counter.
    if "mv_last_seq" not in st.session_state:
        st.session_state.mv_last_seq = 0
    if "mv_board_instance" not in st.session_state:
        st.session_state.mv_board_instance = None
    # Squares the trader has cleared off the board. Hiding, not deleting:
    # nothing here writes to BilateralTrades, and a compliance row is not
    # something a scheduling view should be able to remove. Reversible from
    # the filter row.
    if "mv_hidden" not in st.session_state:
        st.session_state.mv_hidden = set()
    # {"buy_key", "sell_key"} while a new link's schedule is in the popup.
    if "mv_pending" not in st.session_state:
        st.session_state.mv_pending = None
    # A link_id while an existing link's schedule is in the popup.
    if "mv_editing" not in st.session_state:
        st.session_state.mv_editing = None
    # A market name while its bid-file builder is open (see
    # ui.scheduling.bidfile) — opened by a plain click on that market's
    # chip, as distinct from a click-and-drag to start a link.
    if "mv_bidfile_market" not in st.session_state:
        st.session_state.mv_bidfile_market = None
    # Per-counterparty splits made in that builder, session-only like
    # everything else here: {(market, side, pse): [{"code","price",
    # "mw_by_hour"}, ...]}. Keyed by market too so SWPW's splits don't leak
    # into some other market's builder later.
    if "mv_bidfile_splits" not in st.session_state:
        st.session_state.mv_bidfile_splits = {}


def flow_date_is_peak(flow_date):
    """(is_peak, error) for one flow date. None with no error means the
    calendar simply has no row for that date."""
    try:
        return is_peak_map(flow_date, flow_date).get(flow_date), None
    except Exception as e:
        return None, f"Could not load the WECC calendar: {e}"


def load_legs(flow_date, sources):
    """(legs, notes) — every square for this flow date, from the chosen
    sources, plus the market legs the link book implies.

    `notes` collects everything worth telling the trader but not worth
    stopping for: an unreachable database, a row whose He couldn't be read,
    a missing calendar day.
    """
    notes = []
    is_peak, calendar_error = flow_date_is_peak(flow_date)
    if calendar_error:
        notes.append(calendar_error)
    elif is_peak is None:
        notes.append(
            f"The WECC calendar has no row for {flow_date}, so HL and LL "
            "trades can't be expanded into hours and are not shown."
        )

    legs = []
    if SOURCE_DB in sources:
        rows, error = load_trades_for_flow_date(flow_date)
        if error:
            notes.append(error)
        db_legs, warnings = legs_from_db_rows(rows, flow_date, is_peak)
        legs += db_legs
        notes += warnings
    if SOURCE_SESSION in sources:
        session_legs, warnings = legs_from_session_trades(
            st.session_state.get("trades", []), flow_date, is_peak
        )
        legs += session_legs
        notes += warnings

    # Market legs aren't drawn as squares (they're chips on the board's
    # rail), but they're carried here so anything looking a link's other end
    # up by key can name it.
    legs += market_legs(st.session_state.mv_links, flow_date)
    return legs, notes


def legs_by_key(legs):
    return {lg.key: lg for lg in legs}


def prune_selection(legs):
    """Forget a focus or an open popup whose square is no longer on the
    board — the flow date moved, a filter hid it, or the trade was deleted."""
    keys = {lg.key for lg in legs}
    if st.session_state.mv_focus not in keys:
        st.session_state.mv_focus = None

    pending = st.session_state.mv_pending
    if pending:
        # A market key is allowed to be absent: a market leg is derived from
        # the links reaching it, so the one a pending link is about to
        # create doesn't exist until that link is confirmed.
        missing = [
            key
            for key in (pending["buy_key"], pending["sell_key"])
            if key not in keys and parse_market_key(key) is None
        ]
        if missing:
            st.session_state.mv_pending = None

    if st.session_state.mv_editing and not any(
        ln.link_id == st.session_state.mv_editing for ln in st.session_state.mv_links
    ):
        st.session_state.mv_editing = None


# ------------------------------------------------------------ board events


def set_focus(key):
    st.session_state.mv_focus = key or None


def focused_key():
    return st.session_state.get("mv_focus")


def store_positions(positions):
    """Remember where the trader dragged each square. Merged rather than
    replaced so a square filtered off the board keeps its place for when the
    filter comes back off."""
    clean = {
        str(key): [float(xy[0]), float(xy[1])]
        for key, xy in (positions or {}).items()
        if isinstance(xy, (list, tuple)) and len(xy) == 2
    }
    st.session_state.mv_positions = {**st.session_state.mv_positions, **clean}


def hide_leg(key):
    """Clear a square off the board for this session.

    Deliberately not a delete: these rows live in a compliance database that
    this view only ever reads, and a session trade belongs to the Add Trade
    page's list. Hiding is reversible and can't lose anything.
    """
    st.session_state.mv_hidden = st.session_state.mv_hidden | {key}


def restore_hidden():
    st.session_state.mv_hidden = set()


def visible_legs(legs):
    return [lg for lg in legs if lg.key not in st.session_state.mv_hidden]


def highlighted_keys(legs):
    """The focused square plus everything linked to it — what the board dims
    around."""
    focus = focused_key()
    if not focus:
        return set()
    keys = {focus}
    for ln in st.session_state.mv_links:
        if ln.touches(focus):
            keys.add(ln.other_key(focus))
    return keys


# ------------------------------------------------------------------- links


def start_link(buy_key, sell_key):
    """Open the schedule popup for a pairing the trader just dragged. The
    link isn't created until they confirm it."""
    st.session_state.mv_pending = {"buy_key": buy_key, "sell_key": sell_key}
    st.session_state.mv_editing = None
    st.session_state.mv_bidfile_market = None  # only one dialog at a time


def start_link_edit(link_id):
    """Open the schedule popup for an existing link — the same popup, with
    Delete alongside Save."""
    st.session_state.mv_editing = link_id
    st.session_state.mv_pending = None
    st.session_state.mv_bidfile_market = None  # only one dialog at a time


def close_popup():
    st.session_state.mv_pending = None
    st.session_state.mv_editing = None


def market_leg(market, direction, flow_date, legs):
    """The market leg for this side, standing one up if the link book
    doesn't imply it yet — a market is derived from the links reaching it,
    so before the first link there's nothing to derive."""
    key = market_leg_key(market, direction, flow_date)
    existing = legs_by_key(legs).get(key)
    if existing is not None:
        return existing
    return TradeLeg(
        key=key,
        source="market",
        direction=direction,
        pse=market,
        por_pod="Market",
        flow_date=flow_date,
    )


def leg_or_market(key, legs, flow_date):
    """Look a link end up, standing up a market leg if that's what it is."""
    found = legs_by_key(legs).get(key)
    if found is not None:
        return found
    parsed = parse_market_key(key)
    if parsed is None:
        return None
    market, direction = parsed
    return market_leg(market, direction, flow_date, legs)


def commit_link(buy_leg, sell_leg, mw_by_hour):
    """Create the link and close the popup. Returns the new Link, or None
    when the allocation is empty (every hour zeroed out), which is a cancel
    in all but name."""
    link = make_link(
        f"L{st.session_state.mv_next_link_id}", buy_leg, sell_leg, mw_by_hour
    )
    close_popup()
    if not link.mw_by_hour:
        return None
    st.session_state.mv_next_link_id += 1
    st.session_state.mv_links.append(link)
    return link


def update_link(link_id, mw_by_hour):
    """Overwrite an existing link's schedule; an emptied one is removed."""
    close_popup()
    for i, ln in enumerate(st.session_state.mv_links):
        if ln.link_id != link_id:
            continue
        cleaned = {int(h): float(mw) for h, mw in mw_by_hour.items() if mw and mw > 0}
        if not cleaned:
            st.session_state.mv_links.pop(i)
            return None
        st.session_state.mv_links[i] = Link(
            link_id=ln.link_id,
            buy_key=ln.buy_key,
            sell_key=ln.sell_key,
            mw_by_hour=cleaned,
        )
        return st.session_state.mv_links[i]
    return None


def remove_link(link_id):
    close_popup()
    st.session_state.mv_links = [
        ln for ln in st.session_state.mv_links if ln.link_id != link_id
    ]


def find_link(link_id):
    for ln in st.session_state.mv_links:
        if ln.link_id == link_id:
            return ln
    return None


# ---------------------------------------------------------- bid file popup


def open_bidfile(market):
    st.session_state.mv_bidfile_market = market
    close_popup()  # only one dialog at a time


def close_bidfile():
    st.session_state.mv_bidfile_market = None


def bidfile_market():
    return st.session_state.get("mv_bidfile_market")


def bidfile_split(market, side, pse):
    """That counterparty's current split, or None if it's never been
    touched (still one implicit line covering the whole aggregate)."""
    return st.session_state.mv_bidfile_splits.get((market, side, pse))


def set_bidfile_split(market, side, pse, lines):
    st.session_state.mv_bidfile_splits[(market, side, pse)] = lines


def bidfile_splits_for(market):
    """{(side, pse): [...]} restricted to one market, in the shape
    domain.bidfiles.build_bid_lines expects."""
    return {
        (side, pse): lines
        for (mkt, side, pse), lines in st.session_state.mv_bidfile_splits.items()
        if mkt == market
    }


__all__ = [
    "ALL_SOURCES",
    "SOURCE_DB",
    "SOURCE_SESSION",
    "bidfile_market",
    "bidfile_split",
    "bidfile_splits_for",
    "close_bidfile",
    "close_popup",
    "commit_link",
    "find_link",
    "flow_date_is_peak",
    "focused_key",
    "hide_leg",
    "highlighted_keys",
    "init_matching_state",
    "leg_or_market",
    "legs_by_key",
    "load_legs",
    "market_leg",
    "open_bidfile",
    "prune_selection",
    "remove_link",
    "restore_hidden",
    "set_bidfile_split",
    "set_focus",
    "start_link",
    "start_link_edit",
    "store_positions",
    "update_link",
    "visible_legs",
]
