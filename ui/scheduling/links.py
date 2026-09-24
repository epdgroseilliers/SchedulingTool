"""The schedule popup, and the detail strip under the board.

One modal serves both jobs the spec asks of it: it opens with a suggested
schedule when a link is dragged into existence, and it opens with the
existing schedule — plus Delete — when a link is clicked. That's deliberate;
creating and editing a link are the same question ("which hours, at what
MW?"), and two different surfaces for it would be two things to learn.
"""

import streamlit as st

from domain.matching import links_for, open_by_hour, suggest_allocation
from ui.scheduling.state import (
    LINK_DIALOG,
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
from ui.scheduling.widgets import hours_editor as _hours_editor


def _side_line(leg, links, label):
    if leg is None:
        return f"**{label}** — (not on the board)"
    return (
        f"**{label}** {leg.pse} · {leg.por_pod} · HE {leg.he_label} · "
        f"{leg.mw_label} · {sum(open_by_hour(leg, links).values()):,.0f} MWh open"
    )


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

        st.markdown(_side_line(buy, without_this, "Buy"))
        st.markdown(_side_line(sell, without_this, "Sell"))

        if start_from is None:
            start_from = suggest_allocation(buy, sell, without_this)
            if start_from:
                st.caption(
                    "Suggested: every hour both sides still have open, at the "
                    "smaller of the two remaining MW. Overwrite any hour."
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
        st.caption(f"{sum(allocation.values()):,.0f} MWh on this link")

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
