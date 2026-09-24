"""Scheduling View — page entry point (Phase 2).

Wires the view's sections together in order, the same way app.py does for
Add Trade; the logic lives in `domain/matching.py` (pure), `data/matching.py`
(the book), `ui/scheduling/` (the adapters) and `components/trade_board/`
(the canvas itself). See PROJECT.md for what this view is for.

Everything above the board is one row, and the board takes the full width
and sizes its own height to the day's busier side — neither is a constant
here.
"""

import streamlit as st

from domain.matching import board_totals, filter_legs
from ui.nav import render_nav
from ui.scheduling.bidfile import render_bidfile_popup
from ui.scheduling.board import open_position_line, render_board
from ui.scheduling.filters import (
    render_date_row,
    render_hidden_notice,
    render_leg_filters,
)
from ui.scheduling.links import render_detail, render_popup
from ui.scheduling.state import (
    init_matching_state,
    load_legs,
    prune_selection,
    visible_legs,
)

st.set_page_config(
    page_title="Scheduling View",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="collapsed",
)
init_matching_state()
render_nav("scheduling")

links = st.session_state.mv_links

flow_date, sources, filter_slots, status_slot = render_date_row()
all_legs, notes = load_legs(flow_date, sources)
sources, pses, por_pods = render_leg_filters(filter_slots, all_legs)
# Rescue is applied here: a leg that fails the PSE/POR-POD filter is kept
# anyway when it's directly linked to one that passes, so a link is never
# left with only one end drawn — see domain.matching.filter_legs.
legs = filter_legs(visible_legs(all_legs), links=links, pses=pses, por_pods=por_pods)
prune_selection(legs)

status_slot.markdown(open_position_line(board_totals(legs, links)))

if notes:
    with st.expander(f"⚠ {len(notes)} note(s)"):
        for note in notes:
            st.warning(note)

render_popup(legs, links, flow_date)
# The bid-file builder reads from every loaded leg, not the filtered/hidden
# subset the board is showing — a trader filtering a counterparty out of
# view, or clearing its square, must not make its SWPW position disappear
# from what gets bid.
render_bidfile_popup(all_legs, links, flow_date)
if render_board(legs, links, flow_date):
    st.rerun()

detail, hidden = st.columns([5, 1], vertical_alignment="center")
with detail:
    render_detail(legs, links, flow_date)
render_hidden_notice(hidden)
