"""The filter strip: one row, on the surface.

An earlier version folded PSE/POR-POD/source into a popover to save height
and they were simply never found — a filter you have to go looking for isn't
a filter. So they sit in the row itself with collapsed labels and
self-describing placeholders ("All PSEs"), which costs one row and no
discoverability.

Split in two calls on purpose. Flow date and source decide *which trades get
loaded*; PSE and POR/POD can only offer the values that day actually
contains, so they render after the load, from the legs themselves.
"""

from datetime import date, timedelta

import streamlit as st

from data.matching import clear_cache
from ui.scheduling.state import ALL_SOURCES, restore_hidden
from ui.session import widget_defaults


def _drop_stale_choices(key, options):
    """Forget selections that this flow date has no square for.

    Streamlit rejects a multiselect whose session_state value holds
    something outside `options`, and PSE/POR-POD options change with the
    flow date — so a filter picked on one day would otherwise break the page
    on the next. Safe here because the widget hasn't been created yet this
    run.
    """
    current = st.session_state.get(key)
    if current:
        kept = [v for v in current if v in options]
        if kept != current:
            st.session_state[key] = kept


def render_date_row():
    """Returns (flow_date, sources, filter_slots, status_slot).

    `filter_slots` are empty containers on this same row, filled by
    render_leg_filters() once the legs are known; `status_slot` takes the
    open-position line.
    """
    cols = st.columns(
        [1.1, 1.25, 1.5, 1.5, 0.45, 3.0], vertical_alignment="bottom"
    )
    flow_date = cols[0].date_input(
        "Flow date",
        key="mv_flow_date",
        label_visibility="collapsed",
        help="One flow date at a time. A trade spanning several days appears "
        "on each of them, with that day's own hours.",
        **widget_defaults("mv_flow_date", value=date.today() + timedelta(days=1)),
    )
    if cols[4].button("↻", width="stretch", help="Re-read the book from the database."):
        clear_cache()
        st.rerun()
    return (
        flow_date,
        st.session_state.get("mv_sources", ALL_SOURCES),
        (cols[1], cols[2], cols[3]),
        cols[5],
    )


def render_leg_filters(slots, legs):
    """Fill the three filter slots. Returns (sources, pses, por_pods); empty
    lists mean no filter."""
    source_slot, pse_slot, por_pod_slot = slots
    real = [lg for lg in legs if not lg.is_market]
    pse_options = sorted({lg.pse for lg in real if lg.pse})
    por_pod_options = sorted({lg.por_pod for lg in real if lg.por_pod})
    _drop_stale_choices("mv_pse", pse_options)
    _drop_stale_choices("mv_porpod", por_pod_options)

    sources = source_slot.multiselect(
        "Trades from",
        ALL_SOURCES,
        key="mv_sources",
        label_visibility="collapsed",
        placeholder="Source",
        help="The database is the desk's book. 'This session' adds trades "
        "entered on the Add Trade page but not written to the DB — one "
        "written to the DB is only counted once, from the DB.",
        **widget_defaults("mv_sources", default=ALL_SOURCES),
    )
    pses = pse_slot.multiselect(
        "PSE",
        pse_options,
        key="mv_pse",
        label_visibility="collapsed",
        placeholder="All PSEs",
        help="Counterparty or market — both live in BilateralMarket, so PSE "
        "covers the whole column.",
    )
    por_pods = por_pod_slot.multiselect(
        "POR/POD",
        por_pod_options,
        key="mv_porpod",
        label_visibility="collapsed",
        placeholder="All POR/PODs",
    )
    return sources, pses, por_pods


def render_hidden_notice(slot):
    """Offer the way back for squares cleared off the board — otherwise
    hiding one would be indistinguishable from losing it."""
    hidden = st.session_state.mv_hidden
    if not hidden:
        return
    if slot.button(
        f"Restore {len(hidden)} hidden",
        width="stretch",
        help="Squares cleared off the board with × come back. Nothing was "
        "ever deleted — this view only reads the trades database.",
    ):
        restore_hidden()
        st.rerun()
