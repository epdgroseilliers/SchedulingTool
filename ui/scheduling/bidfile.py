"""The market bid-file builder — opened by a plain click on a market chip
(dragging it starts a link instead; see components/trade_board's
click/drag threshold).

Wired for SWPW only so far, per PROJECT.md — clicking any other market chip
opens the same dialog with a short "not built yet" notice, so the click
isn't a silent no-op while the rest of the desk's bid files get built out.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import data.bidfiles.swpw as swpw
from domain.bidfiles import LONG, SHORT, build_bid_lines, market_groups, validate_split
from ui.scheduling.bidgrid import render_bid_grid
from ui.scheduling.state import (
    BIDFILE_DIALOG,
    arm_dialog,
    bidfile_market,
    bidfile_splits_for,
    close_bidfile,
    dialog_was_dismissed,
)

#: market name -> its writer module. Each one is expected to expose
#: write_bid_file(flow_date, short_lines, long_lines, overwrite=...) and
#: target_path(flow_date) — see data/bidfiles/swpw.py.
BUILDERS = {"SWPW": swpw}


def _render_generate(market, groups, flow_date, writer):
    st.divider()
    c1, c2 = st.columns([1, 1])

    if c1.button("Preview lines", width="stretch"):
        short_lines, long_lines, errors = build_bid_lines(
            groups, bidfile_splits_for(market, flow_date)
        )
        for e in errors:
            st.error(e)
        if short_lines or long_lines:
            rows = [
                {"Side": SHORT, "Code": ln["code"], "PSE": ln["pse"],
                 "Price": ln["price"], "MWh": sum(ln["mw_by_hour"].values())}
                for ln in short_lines
            ] + [
                {"Side": LONG, "Code": ln["code"], "PSE": ln["pse"],
                 "Price": ln["price"], "MWh": sum(ln["mw_by_hour"].values())}
                for ln in long_lines
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    conflict = st.session_state.get("mv_bidfile_conflict")
    if c2.button("Generate Bid File", type="primary", width="stretch"):
        short_lines, long_lines, errors = build_bid_lines(
            groups, bidfile_splits_for(market, flow_date)
        )
        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                path = writer.write_bid_file(flow_date, short_lines, long_lines, overwrite=False)
            except FileExistsError:
                st.session_state.mv_bidfile_conflict = str(writer.target_path(flow_date))
                arm_dialog(BIDFILE_DIALOG)
                st.rerun()
            except Exception as e:
                st.error(f"Could not write the bid file: {e}")
            else:
                st.session_state.mv_bidfile_conflict = None
                st.success(f"Bid file written to {path}")

    if conflict:
        st.warning(f"{Path(conflict).name} already exists.")
        confirmed = st.checkbox("Overwrite it", key="mv_bidfile_overwrite_confirm")
        if confirmed and st.button("Overwrite and Generate", type="primary"):
            short_lines, long_lines, errors = build_bid_lines(
                groups, bidfile_splits_for(market, flow_date)
            )
            if errors:
                for e in errors:
                    st.error(e)
            else:
                try:
                    path = writer.write_bid_file(flow_date, short_lines, long_lines, overwrite=True)
                except Exception as e:
                    st.error(f"Could not write the bid file: {e}")
                else:
                    st.session_state.mv_bidfile_conflict = None
                    st.success(f"Bid file written to {path}")


def render_bidfile_popup(legs, links, flow_date):
    """Show the bid-file builder if one is open. Returns True when
    something changed and the caller should rerun."""
    market = bidfile_market()
    if not market:
        return False
    if dialog_was_dismissed(BIDFILE_DIALOG):
        close_bidfile()
        return False

    @st.dialog(f"{market} bid file", width="large")
    def _dialog():
        writer = BUILDERS.get(market)
        if writer is None:
            st.info(f"No bid file is built for {market} yet.")
            if st.button("Close"):
                close_bidfile()
                st.rerun()
            return

        groups = market_groups(market, legs, links, flow_date)
        if not groups:
            st.info(f"No open position through {market} for this flow date.")
            if st.button("Close"):
                close_bidfile()
                st.rerun()
            return

        st.caption(
            "Hours down the index, a MW and a price column under every "
            "GCA/LCA. **+** splits a counterparty across another code: type "
            "MW into the new pair and the first gives way. A price covers "
            "its whole line."
        )
        if render_bid_grid(market, flow_date, groups):
            # st.rerun() runs the whole script, which is also what a
            # dismissal looks like — say this one isn't.
            arm_dialog(BIDFILE_DIALOG)
            st.rerun()

        # One box, not one per message: on a busy day most of these are just
        # "not filled in yet", and a stack of full-width alerts under a grid
        # this compact buries the grid itself.
        splits = bidfile_splits_for(market, flow_date)
        errors = [
            error
            for (side, pse), agg in sorted(groups.items())
            for error in validate_split(side, pse, agg, splits.get((side, pse), []))
        ]
        if errors:
            st.error("\n".join(f"- {error}" for error in errors))

        _render_generate(market, groups, flow_date, writer)

        if st.button("Close", key="mv_bidfile_close"):
            close_bidfile()
            st.rerun()

    _dialog()
    return False
