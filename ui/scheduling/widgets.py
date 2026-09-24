"""Rendering helpers shared across the Scheduling View's popups.

Just the one-row, HE1..HE24 hour editor so far — the link schedule popup's
(`links.py`), where one allocation reads best laid out the same wide way
the Add Trade grid lays a schedule out.

The bid-file builder deliberately doesn't use it: that one shows several
bid lines at once and mirrors the workbook it writes, hours down the side —
see `bidfile.py`.
"""

import streamlit as st

from domain.grid import HOURS, block_grid_to_date_frames, make_block_grid


def hours_editor(mw_by_hour, flow_date, key, disabled=False):
    """One row, one column per HE — the same wide shape the Add Trade grid
    uses, so an allocation reads like the schedule it came from. Returns the
    edited {hour: mw}."""
    grid = make_block_grid([flow_date], {flow_date: mw_by_hour})
    edited = st.data_editor(
        grid,
        key=key,
        num_rows="fixed",
        disabled=disabled,
        column_config={
            "Date": st.column_config.DateColumn("Date", disabled=True, width=95),
            **{
                str(h): st.column_config.NumberColumn(
                    str(h), min_value=0.0, step=1.0, required=True, width=40
                )
                for h in HOURS
            },
        },
        hide_index=True,
        width="stretch",
        height="content",
        row_height=20,
    )
    frame = block_grid_to_date_frames(edited).get(flow_date)
    if frame is None:
        return {}
    return {
        int(row["HE"]): float(row["MW"])
        for _, row in frame.iterrows()
        if row["MW"] and row["MW"] > 0
    }
