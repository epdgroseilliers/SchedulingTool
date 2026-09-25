"""Session state for the Scheduling View, and assembling the day's legs.

Links are **session-only** for now, by decision: nothing here is written
back to any database. That mirrors how Phase 1 itself started — trades lived
in `st.session_state.trades` long before `BilateralTrades` writes existed —
and keeps the interaction model free to change before a schema is committed
to it.

Every key this module owns is prefixed `mv_` (matching view) so it can't
collide with the Add Trade page's state in the same session.
"""

from datetime import date, timedelta

import streamlit as st

from data.calendar import is_peak_map, sessions_near
from data.matching import load_trades_for_flow_date
from domain.matching import (
    Link,
    TradeLeg,
    legs_from_db_rows,
    legs_from_session_trades,
    links_on,
    make_link,
    market_leg_key,
    market_legs,
    parse_market_key,
    suggest_allocation,
)
from domain.trade import session_horizon

SOURCE_DB = "Database"
SOURCE_SESSION = "This session"
ALL_SOURCES = [SOURCE_DB, SOURCE_SESSION]

#: Backstop on how far a link may propagate (see propagate_link). The real
#: bound is the trading session's own horizon, which is a day or two out —
#: this only bites when the calendar can't be read at all and there is no
#: horizon to use. Every day inside it costs one (cached) query, so an
#: unreadable calendar shouldn't turn one click into hundreds of them.
PROPAGATE_MAX_DAYS = 31

#: Where the flow date the trader last looked at is kept.
#:
#: Deliberately *not* the date widget's own key. Streamlit discards a
#: widget's state on any script run that doesn't render it, and stepping
#: over to Add Trade is exactly such a run — so `mv_flow_date` alone comes
#: back empty and the page snaps to tomorrow every time the trader returns,
#: however many days forward they were working. Plain state is never
#: garbage-collected that way. (Same reason the Add Trade page holds its
#: rarely-used fields outside widget state — see domain.options.)
FLOW_DATE_MEMORY = "mv_flow_date_last"

#: "This dialog has already been drawn on a page run" — one key per modal.
BIDFILE_DIALOG = "mv_bidfile_shown"
LINK_DIALOG = "mv_popup_shown"


def arm_dialog(key):
    """Ask for the dialog behind `key` to be drawn on the next page run:
    on opening it, and before any rerun deliberately made from inside it."""
    st.session_state[key] = False


def dialog_was_dismissed(key):
    """Whether an open modal was closed with ✕, Esc or a click outside —
    the one thing `st.dialog` gives no callback for.

    A modal covers the page while it's open, so a *full script run* can only
    mean one of two things: the trader closed it, or the dialog itself asked
    for that rerun — and those re-arm first (`arm_dialog`). So a second page
    run with the dialog still flagged open is a dismissal.

    Without this the flag simply survived being dismissed, and the next page
    run silently re-opened a modal nobody asked for. Reproduced exactly that
    way: open the SWPW builder, press Esc, click Refresh, and it's back.
    """
    if st.session_state.get(key):
        return True
    st.session_state[key] = True
    return False


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
    # everything else here: {(market, flow_date, side, pse): [{"code",
    # "price", "mw_by_hour"}, ...]}. Keyed by market so SWPW's splits don't
    # leak into some other market's builder, and by flow date because a bid
    # file is written per day — see bidfile_splits_for.
    if "mv_bidfile_splits" not in st.session_state:
        st.session_state.mv_bidfile_splits = {}
    # The other flow dates the last confirmed link also linked, so the page
    # can say so once — see propagate_link.
    if "mv_propagated" not in st.session_state:
        st.session_state.mv_propagated = []
    # The flow date to come back to — see FLOW_DATE_MEMORY. None means
    # "never set one", which default_flow_date reads as tomorrow.
    if FLOW_DATE_MEMORY not in st.session_state:
        st.session_state[FLOW_DATE_MEMORY] = None
    # The bid grid's own event watermark. Its own keys rather than the
    # board's, because the board is still on the page behind the builder's
    # dialog, counting separately — see ui.scheduling.bidgrid.handle_event.
    if "mv_bidgrid_last_seq" not in st.session_state:
        st.session_state.mv_bidgrid_last_seq = 0
    if "mv_bidgrid_instance" not in st.session_state:
        st.session_state.mv_bidgrid_instance = None
    # Whether each dialog has already been drawn on a page run — see
    # dialog_was_dismissed, which is the only way to notice a modal closed
    # with ✕, Esc or a click outside.
    if BIDFILE_DIALOG not in st.session_state:
        st.session_state[BIDFILE_DIALOG] = False
    if LINK_DIALOG not in st.session_state:
        st.session_state[LINK_DIALOG] = False


def default_flow_date():
    """The flow date the page should open on: the one the trader was last
    looking at this session, or tomorrow on the first visit.

    Whatever they set is what comes back, including a date now in the past
    — a trader who went to look at Monday and stepped away shouldn't have
    to find Monday again.
    """
    return st.session_state.get(FLOW_DATE_MEMORY) or date.today() + timedelta(days=1)


def remember_flow_date(flow_date):
    st.session_state[FLOW_DATE_MEMORY] = flow_date


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
    # up by key can name it. Only this day's links imply them.
    legs += market_legs(links_on(st.session_state.mv_links, flow_date), flow_date)
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
    arm_dialog(LINK_DIALOG)


def start_link_edit(link_id):
    """Open the schedule popup for an existing link — the same popup, with
    Delete alongside Save."""
    st.session_state.mv_editing = link_id
    st.session_state.mv_pending = None
    st.session_state.mv_bidfile_market = None  # only one dialog at a time
    arm_dialog(LINK_DIALOG)


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


def _new_link(buy_leg, sell_leg, mw_by_hour):
    """Append a link with the next id, or return None for an empty one."""
    link = make_link(
        f"L{st.session_state.mv_next_link_id}", buy_leg, sell_leg, mw_by_hour
    )
    if not link.mw_by_hour:
        return None
    st.session_state.mv_next_link_id += 1
    st.session_state.mv_links.append(link)
    return link


def commit_link(buy_leg, sell_leg, mw_by_hour):
    """Create the link and close the popup. Returns the new Link, or None
    when the allocation is empty (every hour zeroed out), which is a cancel
    in all but name."""
    close_popup()
    link = _new_link(buy_leg, sell_leg, mw_by_hour)
    if link is None:
        return None
    st.session_state.mv_propagated = propagate_link(buy_leg, sell_leg)
    return link


def take_propagated():
    """The dates the last confirmed link also linked, consumed once.

    Links appearing on days that aren't on screen — and that feed those
    days' bid files — shouldn't be invisible, and the rerun that follows a
    confirmation is what carries the news to the next page run.
    """
    dates = st.session_state.get("mv_propagated") or []
    st.session_state.mv_propagated = []
    return dates


def propagate_link(buy_leg, sell_leg, sources=None):
    """Link the same two trades on every *other* day they both flow, and
    return those dates.

    A trade spanning several days is a separate square, and so a separate
    link, on each of them — but a trader who has decided these two go
    together has decided it for the whole overlap, and re-drawing the same
    link once per day is exactly the busywork this view exists to remove.

    Each day gets its **own** suggestion rather than a copy of the one just
    confirmed: the days can differ (an HL trade doesn't flow at all on an
    off-peak day, and a day may already be partly linked elsewhere), and a
    copied allocation would over-commit the ones that differ. They are
    ordinary independent links afterwards — editing or deleting one leaves
    the rest alone.
    """
    dates = _shared_flow_dates(buy_leg, sell_leg, calendar_horizon())
    if not dates:
        return []
    if sources is None:
        sources = st.session_state.get("mv_sources", ALL_SOURCES)

    linked = []
    for day in dates:
        legs, _ = load_legs(day, sources)
        by_key = legs_by_key(legs)
        ends = []
        for leg in (buy_leg, sell_leg):
            parsed = parse_market_key(leg.key)
            if parsed is None:
                ends.append(by_key.get(leg.key))
            else:
                market, direction = parsed
                ends.append(market_leg(market, direction, day, legs))
        buy_day, sell_day = ends
        # A trade legitimately doesn't reach every day of its own range —
        # an HL row flows no hours on an off-peak day — so a missing end is
        # a skip, not a problem.
        if buy_day is None or sell_day is None:
            continue
        allocation = suggest_allocation(
            buy_day, sell_day, links_on(st.session_state.mv_links, day)
        )
        if _new_link(buy_day, sell_day, allocation) is not None:
            linked.append(day)
    return linked


def calendar_horizon(trade_date=None):
    """How far forward a link may be auto-propagated: the last flow date of
    the trading session for `trade_date`, defaulting to today's.

    Today's, because the Scheduling View has no trade date of its own — it
    shows a flow date, and the session being scheduled is the one traded
    today. None if the calendar can't be read, which propagation treats as
    "don't cap" rather than refusing to act.
    """
    day = trade_date or date.today()
    try:
        return session_horizon(day, sessions_near(day))
    except Exception:
        return None


def _shared_flow_dates(buy_leg, sell_leg, horizon=None):
    """The days *after* the one being linked that both these trades flow.

    **Forward only, never backward.** A day earlier than the one in front of
    the trader is a day they have already scheduled, and reaching back into
    it would rewrite finished work — quietly, on a day not even on screen.
    Linking on the last day a trade flows therefore propagates nothing,
    which is correct: there is no later day left to cover.

    A market end constrains nothing — it exists wherever a link puts it —
    so the window is the other end's own range.

    `horizon` is the last flow date of the session being traded (see
    domain.trade.session_horizon). A deal running past it is not
    auto-linked past it: beyond that day nothing has been traded yet, so a
    link the app invented out there is one with no position behind it. The
    days inside it still get theirs.

    Capped as well — the backstop for a calendar that gave no horizon at
    all, which would otherwise let one click fan out across a whole deal.
    """
    spans = [
        (lg.start_date, lg.stop_date)
        for lg in (buy_leg, sell_leg)
        if lg.start_date and lg.stop_date
    ]
    if not spans:
        return []
    stop = min(e for _, e in spans)
    if horizon is not None:
        stop = min(stop, horizon)
    on = buy_leg.flow_date or sell_leg.flow_date

    # Start the day after the one being linked, never at the trade's own
    # start — that is what keeps this forward-only.
    day = max(max(s for s, _ in spans), on + timedelta(days=1))
    days = []
    while day <= stop and len(days) < PROPAGATE_MAX_DAYS:
        days.append(day)
        day += timedelta(days=1)
    return days


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
            flow_date=ln.flow_date,
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
    arm_dialog(BIDFILE_DIALOG)


def close_bidfile():
    st.session_state.mv_bidfile_market = None


def bidfile_market():
    return st.session_state.get("mv_bidfile_market")


def bidfile_split(market, flow_date, side, pse):
    """That counterparty's current split, or None if it's never been
    touched (still one implicit line covering the whole aggregate)."""
    return st.session_state.mv_bidfile_splits.get((market, flow_date, side, pse))


def set_bidfile_split(market, flow_date, side, pse, lines):
    st.session_state.mv_bidfile_splits[(market, flow_date, side, pse)] = lines


def bidfile_splits_for(market, flow_date):
    """{(side, pse): [...]} restricted to one market on one flow date, in
    the shape domain.bidfiles.build_bid_lines expects.

    Keyed by the date as well as the market because a bid file is written
    per flow date: without it, the codes, prices *and* MW typed for one day
    came back as the next day's defaults — the same position bid twice.
    """
    return {
        (side, pse): lines
        for (mkt, day, side, pse), lines in st.session_state.mv_bidfile_splits.items()
        if mkt == market and day == flow_date
    }


__all__ = [
    "ALL_SOURCES",
    "BIDFILE_DIALOG",
    "LINK_DIALOG",
    "PROPAGATE_MAX_DAYS",
    "arm_dialog",
    "SOURCE_DB",
    "SOURCE_SESSION",
    "bidfile_market",
    "bidfile_split",
    "bidfile_splits_for",
    "calendar_horizon",
    "close_bidfile",
    "close_popup",
    "FLOW_DATE_MEMORY",
    "default_flow_date",
    "dialog_was_dismissed",
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
    "propagate_link",
    "prune_selection",
    "remember_flow_date",
    "remove_link",
    "restore_hidden",
    "set_bidfile_split",
    "set_focus",
    "start_link",
    "start_link_edit",
    "store_positions",
    "take_propagated",
    "update_link",
    "visible_legs",
]
