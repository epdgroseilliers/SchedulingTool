"""The Trades history list at the bottom of the page — everything added
this session, with a Delete button for each."""

import streamlit as st

from domain.grid import HOURS, schedule_to_wide
from domain.trade import backoffice_summary, format_price


def render_trades_list():
    st.header("Trades")

    if not st.session_state.trades:
        st.info("No trades yet.")
        return

    for i, t in enumerate(st.session_state.trades):
        total_mwh = sum(mw for _, _, mw in t["schedule"])
        dates = sorted({d for d, _, _ in t["schedule"]})
        with st.expander(
            f"{t['direction']} — {t['counterparty']} @ {t['location']} — "
            f"{total_mwh:,.0f} MWh across {len(dates)} date(s)"
        ):
            c1, c2 = st.columns([4, 1])
            with c1:
                wide_df = schedule_to_wide(t["schedule"])
                st.dataframe(
                    wide_df,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Date": st.column_config.DateColumn("Date", width=100),
                        **{
                            str(h): st.column_config.NumberColumn(str(h), width=45)
                            for h in HOURS
                        },
                    },
                )
                st.caption(
                    f"Trade date: {t.get('trade_date', '—')} | "
                    f"Price: {format_price(t.get('index'), t['price'])}"
                )
                st.caption(backoffice_summary(t))
                db_ids = t.get("db_trade_ids")
                st.caption(
                    f"DB: {', '.join(str(i) for i in db_ids)}"
                    if db_ids
                    else "DB: not written"
                )
            with c2:
                if st.button("Delete", key=f"del_trade_{i}"):
                    st.session_state.trades.pop(i)
                    st.rerun()
