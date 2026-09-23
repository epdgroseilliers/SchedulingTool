"""Trade Scheduler — page entry point.

This file only wires page sections together in order; the logic behind each
section lives in `domain/` (pure business rules) and `ui/` (Streamlit
rendering, one module per section). See ARCHITECTURE.md for the map.
"""

import streamlit as st

from ui.actions import (
    handle_add_block,
    handle_submit,
    render_action_row,
    render_past_date_gate,
)
from ui.nav import render_nav
from ui.paste import render_paste_summary
from ui.preview import render_preview_panel
from ui.schedule import render_schedule_section
from ui.session import apply_pending_form_reset, flush_flash, init_session_state
from ui.trade_fields import (
    render_economics_row,
    render_entry_row,
    render_other_attributes,
)
from ui.trades_list import render_trades_list

st.set_page_config(
    page_title="Trade Scheduler",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)
init_session_state()
apply_pending_form_reset()

render_nav("add_trade")
flush_flash()

trade_date, is_dam = render_entry_row()
render_paste_summary()
st.divider()

economics = render_economics_row()
rare = render_other_attributes()

block_grids, block_ranges, block_shapes, block_mws = render_schedule_section(
    trade_date, is_dam, is_monthly=rare["is_monthly"]
)

add_block_clicked, add_trade_clicked, preview_clicked, input_in_db = render_action_row()
past_dated, past_confirmed = render_past_date_gate(trade_date, block_ranges, input_in_db)
handle_add_block(add_block_clicked)

trade_fields = {
    "trade_date": trade_date,
    "is_dam": is_dam,
    "direction": economics["direction"],
    "counterparty": economics["counterparty"],
    "location": economics["location"],
    "index": economics["index_name"],
    "price": economics["price"],
    "communication": economics["communication"],
    "wspp_contract": economics["wspp_contract"],
    "specified_source": economics["specified_source"],
    "is_nws": economics["is_nws"],
    "is_source_non_caiso": economics["is_source_non_caiso"],
    **rare,
}
handle_submit(
    trade_fields,
    block_grids,
    block_shapes,
    block_ranges,
    block_mws,
    add_trade_clicked,
    preview_clicked,
    input_in_db,
    past_dated,
    past_confirmed,
)

render_preview_panel()

st.divider()
render_trades_list()
