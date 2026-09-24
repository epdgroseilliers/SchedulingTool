"""The adapter between a market's open position and the `bid_grid`
component — what it draws, and what its events mean.

The same split of labour as `ui.scheduling.board`: the component owns the
table, the caret and the `+` buttons; this module turns the day's grouped
position into its payload and turns the trader's edits back into state. The
rules themselves stay in `domain.bidfiles` — one authority for what a typed
MW does to the rest of a split, and it's the tested one.

Nothing here writes a file. `ui.scheduling.bidfile` still owns the dialog
around this, the validation messages and Generate.
"""

import streamlit as st

from components.bid_grid import bid_grid
from domain.bidfiles import (
    LONG,
    SHORT,
    blank_line,
    ppt_to_ept,
    rebalance_hour,
    split_mismatches,
)
from domain.grid import HOURS
from ui.scheduling.state import bidfile_split, set_bidfile_split

SIDE_SUB = {SHORT: "Source > market", LONG: "market > Sink"}
CODE_LABEL = {SHORT: "GCA", LONG: "LCA"}

#: The frontend's <colgroup> widths, plus the panel's own border — kept here
#: because the modal has to be sized to the grid, and Python is the only
#: side that knows how many columns there will be before it's drawn. Change
#: one of these and the matching number in the component's CSS.
INDEX_COL_PX, DATA_COL_PX = 42, 90
PANEL_BORDER_PX, PANEL_GAP_PX = 2, 8
#: The modal's own padding, and a floor so a single narrow panel doesn't
#: produce a modal too cramped for its caption and buttons.
DIALOG_PADDING_PX, MIN_DIALOG_PX = 64, 840


def hour_rows():
    """The index column: the PPT hour the desk works in, and beside it the
    EPT row it lands on in the file — including the `1*`/`2*`/`3*` rows the
    last three hours of a Pacific day fall on (domain.bidfiles.ppt_to_ept).

    It's there because everything else in this app is Pacific and the sheet
    is Eastern; a trader reconciling the two shouldn't have to do that
    arithmetic in their head.
    """
    rows = []
    for he in HOURS:
        he_ept, next_day = ppt_to_ept(he)
        rows.append({"he": he, "ept": f"{he_ept}*" if next_day else str(he_ept)})
    return rows


def copy_lines(lines):
    """A working copy — the grid's events edit these before they're stored
    back, and mutating what's already in session state in place would make
    a half-applied event indistinguishable from a finished one."""
    return [
        {
            "code": line.get("code") or "",
            "price": line.get("price"),
            "mw_by_hour": dict(line.get("mw_by_hour") or {}),
        }
        for line in lines
    ]


def _cells(line):
    """One bid line's two columns, as 24 values each.

    `None` means an empty cell, and that's the whole convention: an hour a
    position doesn't flow is blank in the file rather than an explicit 0, so
    it's blank here — and **its price is blank with it**, because a price on
    an hour carrying no MW is a number the file would never contain. A price
    of 0 is a real price and shows as one; only `None` is blank.
    """
    mw, price = [], []
    for hour in HOURS:
        value = line["mw_by_hour"].get(hour) or 0.0
        mw.append(float(value) if value else None)
        price.append(line.get("price") if value else None)
    return mw, price


def build_payload(groups, splits):
    """(hours, sides) for the component.

    `groups` is domain.bidfiles.market_groups' output, {(side, pse): {hour:
    mw}}; `splits` is {(side, pse): lines} already narrowed to this market.
    A counterparty absent from `splits` shows as one line carrying its whole
    aggregate — the same fallback build_bid_lines makes.
    """
    hours = hour_rows()
    sides = []
    for side in (SHORT, LONG):
        side_groups = sorted(
            (pse, agg) for (s, pse), agg in groups.items() if s == side
        )
        if not side_groups:
            continue

        payload_groups = []
        for pse, agg in side_groups:
            lines = splits.get((side, pse)) or [blank_line(side, agg)]
            bad = {
                str(hour): (
                    f"Splits total {allocated:g} MW here; the schedule calls "
                    f"for {expected:g} MW."
                )
                for hour, (allocated, expected) in split_mismatches(agg, lines).items()
            }
            payload_lines = []
            for line in lines:
                mw, price = _cells(line)
                payload_lines.append(
                    {
                        "code": line.get("code") or "",
                        "mw": mw,
                        "price": price,
                        "total": round(sum(line["mw_by_hour"].values()), 2),
                    }
                )
            payload_groups.append(
                {
                    "pse": pse,
                    "total": round(sum(agg.values()), 2),
                    "bad_hours": bad,
                    "lines": payload_lines,
                }
            )

        sides.append(
            {
                "side": side,
                "sub": SIDE_SUB[side],
                "code_label": CODE_LABEL[side],
                "groups": payload_groups,
            }
        )
    return hours, sides


def grid_revision(sides):
    """What the component watches to decide whether to redraw. Every value
    it shows is in here — the payload *is* what's on screen — so a rebalance
    two columns away still reaches the cell it changed."""
    return hash(repr(sides))


def dialog_width_px(sides):
    """How wide the modal has to be for this grid — no wider.

    `st.dialog` offers only small/medium/large, and the useful width here
    isn't any of the three: it's whatever today's counterparties add up to.
    Two column pairs want a narrow modal, eight want a wide one, and a fixed
    choice is either cramped or mostly empty.
    """
    width = DIALOG_PADDING_PX
    for i, side in enumerate(sides):
        lines = sum(len(group["lines"]) for group in side["groups"])
        width += 2 * INDEX_COL_PX + 2 * DATA_COL_PX * lines + PANEL_BORDER_PX
        if i:
            width += PANEL_GAP_PX
    return max(MIN_DIALOG_PX, width)


def dialog_css(sides):
    """Widen the modal to fit the grid. The same targeted-CSS approach
    ui/nav.py already takes to the page header — capped at the viewport so
    a very busy day scrolls inside the panels instead of off-screen."""
    width = dialog_width_px(sides)
    return (
        "<style>div[data-testid='stDialog'] div[role='dialog']{"
        f"width:min({width}px,97vw);max-width:min({width}px,97vw);"
        "}</style>"
    )


def seed_state(market, groups):
    """Give every counterparty on the board a stored line before the grid
    draws it, so what's shown and what Generate reads are the same thing —
    including the side's default price, which is otherwise only a default in
    the drawing."""
    for (side, pse), agg in groups.items():
        if not bidfile_split(market, side, pse):
            set_bidfile_split(market, side, pse, [blank_line(side, agg)])


def splits_for_groups(market, groups):
    return {
        (side, pse): bidfile_split(market, side, pse)
        for (side, pse) in groups
        if bidfile_split(market, side, pse)
    }


def render_bid_grid(market, groups):
    """Draw the grid and apply whatever the trader did to it. Returns True
    when something changed and the caller should rerun."""
    seed_state(market, groups)
    hours, sides = build_payload(groups, splits_for_groups(market, groups))
    st.markdown(dialog_css(sides), unsafe_allow_html=True)
    event = bid_grid(hours=hours, sides=sides, revision=grid_revision(sides))
    return handle_event(event, market, groups)


def handle_event(event, market, groups):
    """Apply one grid event, ignoring ones already applied.

    The `seq`/`instance` pair is the board's, for the board's reasons:
    Streamlit hands a component's last value back on every rerun, so `seq`
    tells a new event from a replayed one — and since that counter restarts
    whenever the iframe is rebuilt, the per-frame `instance` is what stops a
    fresh frame's events from looking stale. Its own keys, because the board
    is still on the page behind this dialog with a counter of its own.
    """
    if not isinstance(event, dict):
        return False
    seq = event.get("seq")
    if seq is None:
        return False
    instance = event.get("instance")
    if instance != st.session_state.get("mv_bidgrid_instance"):
        st.session_state.mv_bidgrid_instance = instance
        st.session_state.mv_bidgrid_last_seq = 0
    if seq <= st.session_state.get("mv_bidgrid_last_seq", 0):
        return False
    st.session_state.mv_bidgrid_last_seq = seq

    side, pse, index = event.get("side"), event.get("pse"), event.get("line")
    agg = groups.get((side, pse))
    if agg is None or not isinstance(index, int):
        return False
    lines = copy_lines(bidfile_split(market, side, pse) or [blank_line(side, agg)])

    kind = event.get("type")
    if kind == "split":
        if not 0 <= index < len(lines):
            return False
        lines.insert(index + 1, blank_line(side))
    elif kind == "unsplit":
        # Never the first line: it's the one the position starts on, and the
        # MW of a removed split has to land somewhere — it goes back there,
        # so dropping a split can't quietly lose part of the day.
        if not 1 <= index < len(lines):
            return False
        dropped = lines.pop(index)
        for hour, mw in dropped["mw_by_hour"].items():
            lines[0]["mw_by_hour"][hour] = lines[0]["mw_by_hour"].get(hour, 0.0) + mw
    elif not 0 <= index < len(lines):
        return False
    elif kind == "code":
        lines[index]["code"] = (event.get("value") or "").strip()
    elif kind == "price":
        value = event.get("value")
        lines[index]["price"] = None if value is None else float(value)
    elif kind == "mw":
        hour = event.get("hour")
        if not isinstance(hour, int):
            return False
        rebalanced = rebalance_hour(
            [float(line["mw_by_hour"].get(hour) or 0.0) for line in lines],
            index,
            float(event.get("value") or 0.0),
            float(agg.get(hour, 0.0)),
        )
        for line, mw in zip(lines, rebalanced):
            if mw:
                line["mw_by_hour"][hour] = mw
            else:
                line["mw_by_hour"].pop(hour, None)
    else:
        return False

    set_bidfile_split(market, side, pse, lines)
    return True
