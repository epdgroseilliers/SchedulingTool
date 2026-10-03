"""The link popup, and the detail strip under the board.

One modal serves every job the spec asks of it: it opens with a suggested
schedule when a link is dragged into existence, it opens with the existing
schedule — plus Delete — when a link is clicked, and either way the path
string and the tag it becomes are on the same surface underneath
(ui.scheduling.tag). That's deliberate; creating a link, editing it, telling
the other scheduler about it and tagging it are one train of thought, and a
separate surface for each would be four things to learn.
"""

import streamlit as st

from data.bilateral import hours_to_he_string
from domain.matching import links_for, open_by_hour, suggest_allocation
from ui.scheduling.state import (
    LINK_DIALOG,
    PENDING_TAG,
    close_popup,
    dialog_was_dismissed,
    commit_link,
    find_link,
    focused_key,
    hide_leg,
    leg_or_market,
    legs_by_key,
    remove_link,
    update_link,
)
from ui.scheduling.tag import recipe_was_applied, render_path_string, render_tag, render_tag_lookup
from ui.scheduling.widgets import hours_editor as _hours_editor


#: How wide the link popup is drawn. `st.dialog` offers only small and
#: large, and large is still too narrow to put the route lookup beside the
#: tag fields — which is the whole reason for splitting them. Same targeted
#: CSS as ui/scheduling/bidgrid.py uses on the bid-file modal, capped at the
#: viewport so a small screen scrolls inside the columns rather than off the
#: side. Only one modal is ever open at a time, so the two can't collide.
DIALOG_WIDTH_PX = 1600

#: The lookup against the tag fields. The lookup is text that wraps; the tag
#: fields are two halves of a form plus three editors, and want the room.
POPUP_SPLIT = [2, 3]


def _dialog_css():
    return (
        "<style>div[data-testid='stDialog'] div[role='dialog']{"
        f"width:min({DIALOG_WIDTH_PX}px,97vw);"
        f"max-width:min({DIALOG_WIDTH_PX}px,97vw);"
        "}</style>"
    )


def _side_text(leg, verb):
    """"Buy from AZPS at PALOVERDE500" — who and where, and nothing else.

    No MW and no hours: both are in the editor immediately below, and a
    header that repeats the grid underneath it is a header nobody reads.
    """
    if leg is None:
        return f"{verb} — (not on the board)"
    if leg.is_market:
        return f"{verb} the **{leg.pse}** market"
    return f"{verb} **{leg.pse}** at {leg.por_pod}"


def _open_note(leg, links):
    """How much of that side is still unlinked. Kept — unlike the MW above —
    because it is the one number the editor can't show: it comes from the
    *other* links on the leg, not from this one. A market has no schedule of
    its own, so it has nothing open."""
    if leg is None or leg.is_market:
        return None
    return f"{leg.pse} {sum(open_by_hour(leg, links).values()):,.0f} MWh open"


def render_popup(legs, links, flow_date):
    """Show the schedule modal if one is open. Everything it does ends in a
    rerun, which is also what dismisses it."""
    pending = st.session_state.mv_pending
    editing = st.session_state.mv_editing
    if not pending and not editing:
        return
    # Every button in here closes the popup before rerunning, so a page run
    # that still finds it open means it was dismissed instead.
    if dialog_was_dismissed(LINK_DIALOG):
        close_popup()
        return

    @st.dialog("Link schedule", width="large")
    def _dialog():
        st.markdown(_dialog_css(), unsafe_allow_html=True)
        if editing:
            link = find_link(editing)
            if link is None:
                close_popup()
                st.rerun()
            buy = leg_or_market(link.buy_key, legs, flow_date)
            sell = leg_or_market(link.sell_key, legs, flow_date)
            # The link's own MW is added back before asking what's open, or
            # every hour it already holds would read as fully committed.
            without_this = [ln for ln in links if ln.link_id != link.link_id]
            start_from = dict(link.mw_by_hour)
        else:
            link = None
            buy = leg_or_market(pending["buy_key"], legs, flow_date)
            sell = leg_or_market(pending["sell_key"], legs, flow_date)
            without_this = links
            start_from = None

        if buy is None or sell is None:
            st.error("One side of this link is no longer on the board.")
            if st.button("Close"):
                close_popup()
                st.rerun()
            return

        st.markdown(
            f"{_side_text(buy, 'Buy from')} · {_side_text(sell, 'sell to')}"
        )

        # One surface, top to bottom, in the order the work happens: the
        # hours, then the path string that goes out on chat, then — behind an
        # expander — the tag fields, which only the links MAG has to tag ever
        # need. (They were two tabs briefly; a tab hid half of what a trader
        # was in the middle of deciding.)
        if start_from is None:
            start_from = suggest_allocation(buy, sell, without_this)
            if start_from:
                st.caption(
                    "Suggested: every hour both sides still have open, at "
                    "the smaller of the two remaining MW. Overwrite any hour."
                )
            else:
                st.warning(
                    "No open hour in common — every hour these two share is "
                    "already allocated elsewhere. Type the hours you want, or "
                    "cancel."
                )

        allocation = _hours_editor(
            start_from, flow_date, key=f"mv_alloc_{editing or 'new'}"
        )
        open_notes = [
            note
            for note in (_open_note(buy, without_this), _open_note(sell, without_this))
            if note
        ]
        st.caption(
            " · ".join(
                [f"{sum(allocation.values()):,.0f} MWh on this link"] + open_notes
            )
        )

        st.divider()
        # A link still being drawn gets its tag — and its path string — too,
        # filed under PENDING_TAG until Create link gives it an id.
        tag_key = link.link_id if link else PENDING_TAG
        render_path_string(st, tag_key, buy, sell, allocation, flow_date)

        # Side by side below the full-width half, because both are tall and
        # stacking them is what made the popup scroll: together they cost
        # the height of the taller one rather than the sum of the two.
        lookup_col, tag_col = st.columns(POPUP_SPLIT)
        render_tag_lookup(lookup_col, tag_key, buy, sell, allocation, flow_date)

        # Open on a link being revisited, shut on one being drawn — unless
        # "Use this route" just filled it, in which case it's worth seeing
        # right away. Drawing a link is about the hours, the path string and
        # perhaps a lookup; the tag fields are the next sitting.
        with tag_col.expander(
            "Tag details", expanded=bool(editing) or recipe_was_applied(tag_key)
        ):
            render_tag(
                st.container(),
                tag_key,
                buy,
                sell,
                allocation,
                flow_date,
                hours_to_he_string(sorted(allocation)),
            )
        st.divider()

        cols = st.columns([1, 1, 1, 3])
        if cols[0].button(
            "Save" if editing else "Create link", type="primary", width="stretch"
        ):
            if editing:
                update_link(editing, allocation)
            else:
                commit_link(buy, sell, allocation)
            st.rerun()
        if editing and cols[1].button("Delete", width="stretch"):
            remove_link(editing)
            st.rerun()
        if cols[2 if editing else 1].button("Cancel", width="stretch"):
            close_popup()
            st.rerun()

    _dialog()


def render_detail(legs, links, flow_date):
    """The focused square, under the board: one line always, the hour grids
    behind an expander so the board still owns the screen."""
    key = focused_key()
    if not key:
        st.caption(
            "Click a square for its detail · drag a square to move it · drag "
            "its **+** onto the other side (or a market chip) to link."
        )
        return

    leg = legs_by_key(legs).get(key)
    if leg is None:
        return
    own = links_for(leg.key, links)
    line, clear = st.columns([5, 1], vertical_alignment="center")
    line.markdown(
        f"**{leg.direction} {leg.pse}** · {leg.por_pod} · HE {leg.he_label} · "
        f"{leg.mw_label} · {leg.mwh:,.0f} MWh · "
        f"**{sum(open_by_hour(leg, links).values()):,.0f} MWh open** · "
        f"{len(own)} link(s)"
    )
    # The same thing the × on the square does. Two routes to it on purpose:
    # the × is quick once you know it's there, this one is findable.
    if clear.button(
        "✕ Clear",
        width="stretch",
        help="Take this square off the board. Nothing is deleted — Restore "
        "brings it back.",
    ):
        hide_leg(leg.key)
        st.rerun()

    with st.expander("Hour by hour"):
        st.caption("This trade's schedule")
        _hours_editor(leg.mw_by_hour, flow_date, key=f"mv_d_{leg.key}", disabled=True)
        for ln in own:
            other = legs_by_key(legs).get(ln.other_key(leg.key))
            st.caption(
                f"{ln.link_id} → {other.pse if other else '(not shown)'} · "
                f"{ln.mwh:,.0f} MWh"
            )
            _hours_editor(
                ln.mw_by_hour, flow_date, key=f"mv_dl_{leg.key}_{ln.link_id}", disabled=True
            )
        st.caption("Still open")
        _hours_editor(
            open_by_hour(leg, links), flow_date, key=f"mv_do_{leg.key}", disabled=True
        )
