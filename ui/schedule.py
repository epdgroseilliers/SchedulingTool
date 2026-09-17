"""The Schedule section: one or more date-range blocks, each with a Shape/MW
that Generate expands into a full HE grid, editable by hand afterward.
"""

import streamlit as st

from domain.grid import (
    HOURS,
    MAX_BLOCK_DATES,
    as_date,
    block_grid_to_date_frames,
    dates_in_range,
    make_block_grid,
)
from domain.shapes import generate_block_grid, shape_to_he
from domain.trade import default_block_start
from ui.session import block_grid_key, get_version, version_key, widget_defaults

DATE_EDITOR_ROW_HEIGHT = 20


def get_block_grid(bid, dates):
    """Session-state grid for a block, reconciled to `dates`: rows for dates
    still in range keep their values, new dates start all-zero, and rows for
    dates no longer in range are dropped."""
    existing = st.session_state.get(block_grid_key(bid))
    mw_by_date = {}
    if existing is not None:
        for _, row in existing.iterrows():
            mw_by_date[as_date(row["Date"])] = {h: row[str(h)] for h in HOURS}
    grid = make_block_grid(dates, mw_by_date)
    st.session_state[block_grid_key(bid)] = grid
    return grid


def sync_block_dates(bid, default_date):
    """Keep a pristine block's Start/End Date following `default_date`
    (which tracks IsDAM) until the trader diverges from it — by editing
    either date directly, or by using Generate/Clear, at which point the
    block has real content and silently moving its date range could drop
    entered MW values (get_block_grid reconciles to whatever dates it's
    given, dropping ones no longer in range).

    Needed because date_input's `value=` argument is only honored the
    first time a widget with a given key is created — flipping IsDAM on
    the very first block (id 0, which exists from the first script run)
    would otherwise never visibly change anything.
    """
    start_key, end_key = f"start_{bid}", f"end_{bid}"
    last_key = f"dates_last_default_{bid}"
    last_default = st.session_state.get(last_key)

    if start_key in st.session_state:
        pristine = (
            get_version(bid) == 0
            and last_default is not None
            and st.session_state[start_key] == last_default
            and st.session_state[end_key] == last_default
        )
        if pristine:
            st.session_state[start_key] = default_date
            st.session_state[end_key] = default_date

    st.session_state[last_key] = default_date


def render_schedule_section(trade_date, is_dam, is_monthly=False):
    """Render every block in st.session_state.block_ids.

    Returns (block_grids, block_ranges, block_shapes, block_mws):
    - block_grids: {bid: {date: long HE/MW DataFrame}} — empty for a
      monthly block, which has no hourly grid.
    - block_ranges: {bid: (start_date, end_date)}
    - block_shapes: {bid: shape string}
    - block_mws: {bid: mw}

    `is_monthly` skips generating, editing, and rendering the per-hour
    grid entirely: a monthly-or-longer trade is a fixed MW for the whole
    date range (and routinely spans more dates than MAX_BLOCK_DATES), so
    the block's own Start/End/Shape/MW *is* the row that gets written —
    see domain.shapes.monthly_block_rows.
    """
    st.subheader("Schedule")

    new_block_default_start = default_block_start(trade_date, is_dam)

    block_grids = {}
    block_ranges = {}
    block_shapes = {}
    block_mws = {}
    for bid in st.session_state.block_ids:
        with st.container(border=True):
            specs = [1.1, 1.1, 1.5, 0.7, 0.9, 1.1]
            show_remove = len(st.session_state.block_ids) > 1
            if show_remove:
                specs.append(1.0)
            row = st.columns(specs, vertical_alignment="bottom")
            dcol1, dcol2, gcol1, gcol2, gcol3, gcol4 = row[:6]

            # sync_block_dates may just have written today's default straight
            # into session_state; passing `value=` as well on that same call
            # logs a Streamlit policy warning, so it's only passed for a
            # widget key that doesn't exist yet (a genuinely new block).
            sync_block_dates(bid, new_block_default_start)
            start_key, end_key = f"start_{bid}", f"end_{bid}"
            start_kwargs = (
                {} if start_key in st.session_state
                else {"value": new_block_default_start}
            )
            start_date = dcol1.date_input("Start Date", key=start_key, **start_kwargs)
            end_kwargs = {} if end_key in st.session_state else {"value": start_date}
            end_date = dcol2.date_input("End Date", key=end_key, **end_kwargs)
            block_ranges[bid] = (start_date, end_date)
            shape = gcol1.text_input(
                "Shape",
                key=f"shape_{bid}",
                help="HL and LL follow the WECC on-/off-peak calendar. ATC is flat 24H. "
                "Anything else is read as an hour range, e.g. 7-22, 10-11, or a single hour like 14.",
                **widget_defaults(f"shape_{bid}", value="HL"),
            )
            block_shapes[bid] = shape
            mw = gcol2.number_input(
                "MW", min_value=1, step=1, key=f"mw_{bid}",
                **widget_defaults(f"mw_{bid}", value=25),
            )
            block_mws[bid] = mw

            if is_monthly:
                remove_clicked = (
                    row[6].button("Remove block", key=f"remove_{bid}", width="stretch")
                    if show_remove else False
                )
                if remove_clicked:
                    st.session_state.block_ids.remove(bid)
                    st.session_state.pop(block_grid_key(bid), None)
                    st.session_state.pop(version_key(bid), None)
                    st.rerun()
                block_grids[bid] = {}
                if start_date > end_date:
                    st.error("Start Date must be on or before End Date.")
                    continue
                try:
                    he_preview = shape_to_he(shape)
                except ValueError as e:
                    st.error(str(e))
                else:
                    st.caption(
                        f"Fixed {mw:g} MW, {he_preview}, for the full period — "
                        f"no hourly schedule needed."
                    )
                continue

            generate_clicked = gcol3.button("Generate", key=f"generate_{bid}", width="stretch")
            clear_clicked = gcol4.button(
                "Clear schedule", key=f"clear_{bid}", type="primary", width="stretch"
            )
            remove_clicked = (
                row[6].button("Remove block", key=f"remove_{bid}", width="stretch")
                if show_remove else False
            )

            if remove_clicked:
                st.session_state.block_ids.remove(bid)
                st.session_state.pop(block_grid_key(bid), None)
                st.session_state.pop(version_key(bid), None)
                st.rerun()

            ver = get_version(bid)
            block_grids[bid] = {}

            if start_date > end_date:
                st.error("Start Date must be on or before End Date.")
                continue

            block_dates = dates_in_range(start_date, end_date)
            if len(block_dates) > MAX_BLOCK_DATES:
                st.error(
                    f"This block spans {len(block_dates)} dates. Split it into smaller "
                    f"blocks of {MAX_BLOCK_DATES} dates or fewer."
                )
                continue

            if generate_clicked:
                grid, excluded, missing, error = generate_block_grid(
                    start_date, end_date, block_dates, shape, mw
                )
                if error:
                    st.error(error)
                else:
                    st.session_state[block_grid_key(bid)] = grid
                    st.session_state[version_key(bid)] = ver + 1
                    if excluded:
                        st.toast(
                            f"HL leg skipped {len(excluded)} off-peak calendar day(s): "
                            f"{', '.join(str(d) for d in excluded)}",
                            icon="ℹ️",
                        )
                    if missing:
                        st.toast(
                            f"No WECC calendar data for {len(missing)} date(s), skipped: "
                            f"{', '.join(str(d) for d in missing)}",
                            icon="⚠️",
                        )
                    st.rerun()
            if clear_clicked:
                st.session_state[block_grid_key(bid)] = make_block_grid(block_dates)
                st.session_state[version_key(bid)] = ver + 1
                st.rerun()

            if block_grid_key(bid) not in st.session_state:
                # A block this app has never shown before: populate it
                # immediately with the same result Generate would give, so
                # the common single-block default-shape trade never needs
                # that click. Runs exactly once per block id — Generate/
                # Clear/a direct edit all leave the grid present in
                # session_state, so this never re-fires and so never
                # overwrites anything the trader has touched. Silent on
                # failure (falls back to an all-zero grid): this fires on
                # every page load, and a WECC calendar hiccup here
                # shouldn't block the page from rendering — clicking
                # Generate explicitly still surfaces the error.
                grid, _, _, error = generate_block_grid(
                    start_date, end_date, block_dates, shape, mw
                )
                st.session_state[block_grid_key(bid)] = (
                    grid if not error else make_block_grid(block_dates)
                )

            grid_seed = get_block_grid(bid, block_dates)
            edited = st.data_editor(
                grid_seed,
                # The date range is part of the key: when it changes, this
                # becomes a new widget for Streamlit, so it re-seeds from
                # grid_seed (which reconciles kept dates' values) instead
                # of returning its old cached grid at the old row count.
                key=f"block_editor_{bid}_{start_date.isoformat()}_{end_date.isoformat()}_{ver}",
                num_rows="fixed",
                column_config={
                    "Date": st.column_config.DateColumn("Date", disabled=True, width=100),
                    **{
                        str(h): st.column_config.NumberColumn(
                            str(h), min_value=0.0, step=1.0, required=True, width=45
                        )
                        for h in HOURS
                    },
                },
                hide_index=True,
                width="stretch",
                height="content",
                row_height=DATE_EDITOR_ROW_HEIGHT,
            )
            st.session_state[block_grid_key(bid)] = edited
            block_grids[bid] = block_grid_to_date_frames(edited)

    return block_grids, block_ranges, block_shapes, block_mws
