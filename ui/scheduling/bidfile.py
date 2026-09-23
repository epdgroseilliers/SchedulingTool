"""The market bid-file builder — opened by a plain click on a market chip
(dragging it starts a link instead; see components/trade_board's
click/drag threshold).

Wired for SWPW only so far, per PROJECT.md — clicking any other market chip
opens the same dialog with a short "not built yet" notice, so the click
isn't a silent no-op while the rest of the desk's bid files get built out.
"""

from pathlib import Path

import streamlit as st

import data.bidfiles.swpw as swpw
from domain.bidfiles import LONG, SHORT, build_bid_lines, market_groups, validate_split
from ui.scheduling.state import (
    bidfile_market,
    bidfile_split,
    bidfile_splits_for,
    close_bidfile,
    set_bidfile_split,
)
from ui.scheduling.widgets import hours_editor

#: market name -> its writer module. Each one is expected to expose
#: write_bid_file(flow_date, short_lines, long_lines, overwrite=...) and
#: target_path(flow_date) — see data/bidfiles/swpw.py.
BUILDERS = {"SWPW": swpw}

SIDE_LABEL = {SHORT: "SHORT — Source > market", LONG: "LONG — market > Sink"}


def _empty_line(mw_by_hour=None):
    return {"code": "", "price": None, "mw_by_hour": dict(mw_by_hour or {})}


def _render_group(market, side, pse, agg_mw_by_hour, flow_date):
    """One counterparty's group: its line(s), each with a code, a price,
    and its own editable 24h schedule. Returns the edited lines (the
    caller persists them) — always via set_bidfile_split, right after,
    since every widget change here already triggers a rerun.
    """
    lines = bidfile_split(market, side, pse)
    if not lines:
        lines = [_empty_line(agg_mw_by_hour)]

    total = sum(agg_mw_by_hour.values())
    st.markdown(f"**{pse}** · {side} · {total:,.0f} MWh")

    edited_lines = []
    for i, line in enumerate(lines):
        cols = st.columns([2, 1.2, 6], vertical_alignment="top")
        code_label = "GCA" if side == SHORT else "LCA"
        code = cols[0].text_input(
            code_label, value=line.get("code") or "",
            key=f"mv_bf_code_{market}_{side}_{pse}_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        price = cols[1].number_input(
            "Price", value=line.get("price"), step=1.0,
            key=f"mv_bf_price_{market}_{side}_{pse}_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        with cols[2]:
            mw_by_hour = hours_editor(
                line.get("mw_by_hour") or {}, flow_date,
                key=f"mv_bf_hours_{market}_{side}_{pse}_{i}",
            )
        edited_lines.append({"code": code, "price": price, "mw_by_hour": mw_by_hour})

    set_bidfile_split(market, side, pse, edited_lines)

    for error in validate_split(side, pse, agg_mw_by_hour, edited_lines):
        st.error(error)

    if st.button("+ Split", key=f"mv_bf_split_{market}_{side}_{pse}"):
        edited_lines.append(_empty_line())
        set_bidfile_split(market, side, pse, edited_lines)
        st.rerun()


def _render_generate(market, groups, flow_date, writer):
    st.divider()
    c1, c2 = st.columns([1, 1])

    if c1.button("Preview lines", width="stretch"):
        short_lines, long_lines, errors = build_bid_lines(groups, bidfile_splits_for(market))
        for e in errors:
            st.error(e)
        if short_lines or long_lines:
            import pandas as pd

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
        short_lines, long_lines, errors = build_bid_lines(groups, bidfile_splits_for(market))
        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                path = writer.write_bid_file(flow_date, short_lines, long_lines, overwrite=False)
            except FileExistsError:
                st.session_state.mv_bidfile_conflict = str(writer.target_path(flow_date))
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
            short_lines, long_lines, errors = build_bid_lines(groups, bidfile_splits_for(market))
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
            "One line per counterparty, aggregated for the day. Enter the "
            "GCA (SHORT) or LCA (LONG) to bid at, and split a counterparty "
            "into more than one with **+ Split** if it needs more than one "
            "code."
        )
        for side in (SHORT, LONG):
            side_groups = {pse: agg for (s, pse), agg in groups.items() if s == side}
            if not side_groups:
                continue
            st.subheader(SIDE_LABEL[side])
            for pse, agg in sorted(side_groups.items()):
                _render_group(market, side, pse, agg, flow_date)

        _render_generate(market, groups, flow_date, writer)

        if st.button("Close", key="mv_bidfile_close"):
            close_bidfile()
            st.rerun()

    _dialog()
    return False
