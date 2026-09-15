"""The "DB Insert Preview" panel — shows what a real Add Trade would write,
without writing it. Populated by ui.actions.handle_submit on a Preview
click; this module only renders whatever's currently in st.session_state.
"""

import pandas as pd
import streamlit as st

from domain.trade import db_input_warnings

PREVIEW_COLUMNS = [
    "trade_date", "start_date", "stop_date", "he", "time_zone",
    "is_monthly", "is_buy", "market_id", "mw", "price", "pricing_node",
    "por_pod", "communication", "wspp", "is_source_non_caiso",
    "specified_source_id", "contract_type", "is_non_washington_sink",
    "resupply_id", "secondary_por_pod", "resource_adequacy_id",
    "exchange_id", "is_option", "dam_rt",
]


def render_preview_panel():
    preview = st.session_state.db_preview
    if not preview:
        return

    with st.container(border=True):
        hcol, xcol = st.columns([9, 1])
        hcol.subheader("DB Insert Preview — nothing written yet")
        if xcol.button("✕", key="clear_preview", help="Dismiss this preview"):
            st.session_state.db_preview = None
            st.rerun()

        t = preview["trade"]
        st.caption(
            f"{t['direction']} — {t['counterparty']} @ {t['location']} — "
            f"would write {len(preview['rows'])} row(s) to "
            f"PhysiqueBilateral.west.BilateralTrades"
        )
        if preview["past_dated"]:
            st.warning(
                "This trade is past-dated. A real Add Trade would refuse it "
                "until 'Confirm past-dated trade' is ticked."
            )

        preview_df = pd.DataFrame(preview["rows"])[PREVIEW_COLUMNS]
        st.dataframe(preview_df, hide_index=True, width="stretch")
        for note in db_input_warnings(t):
            st.caption(f"⚠️ {note}")
        st.caption(
            "Also printed to the terminal running `streamlit run` for copy/paste."
        )
