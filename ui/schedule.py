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
    parse_pasted_schedule,
    make_block_grid,
)
from domain.shapes import generate_block_grid, shape_to_he
from ui.session import (
    block_date_defaults,
    block_edits_key,
    block_grid_key,
    dates_last_default_keys,
    get_version,
    version_key,
    widget_defaults,
)

DATE_EDITOR_ROW_HEIGHT = 20


def get_block_grid(bid, dates, shape, mw):
    """The frame to seed a block's editor with, reconciled to `dates`.

    **The same frame is handed back unchanged while the trader is typing,
    and that is load-bearing.** `st.data_editor` builds its element id from
    a hash of the data it is given, not from `key` alone
    (`streamlit/elements/widgets/data_editor.py`: `compute_and_register_
    element_id(..., key_as_main_identity=False, data=arrow_bytes, ...)`).
    So re-seeding it with the previous run's *edited* frame renamed the
    widget after every accepted edit, and the next edit — sent by a browser
    that still knew the old name — was dropped on arrival. The symptom was
    every other keystroke going missing: type, nothing; type again, it
    sticks. The editor's own accumulated diff is what carries edits between
    runs; this only has to stop moving underneath it.

    A genuine change of dates does rebuild it: rows for dates this block has
    already held keep their values (hand edits included, which is what
    block_edits_key is for); rows for dates no longer in range are dropped;
    genuinely new dates are seeded via Shape/MW against the WECC calendar,
    the same as clicking Generate would give for just that date, rather than
    left at zero MW. Rebuilding then is safe — the editor's key carries the
    date range, so it is a new widget with no edits to lose anyway.

    Silent on a calendar failure for those new dates (falls back to
    all-zero): this runs on every render, and a WECC calendar hiccup here
    shouldn't block the page — clicking Generate explicitly still surfaces
    the error.
    """
    seed = st.session_state.get(block_grid_key(bid))
    if seed is not None and [as_date(d) for d in seed["Date"]] == list(dates):
        return seed

    # The dates moved (or this is the first render): rebuild, preferring
    # what the trader has actually typed over the untouched seed.
    existing = st.session_state.get(block_edits_key(bid), seed)
    mw_by_date = {}
    if existing is not None:
        for _, row in existing.iterrows():
            mw_by_date[as_date(row["Date"])] = {h: row[str(h)] for h in HOURS}

    new_dates = [d for d in dates if d not in mw_by_date]
    if new_dates:
        gen_grid, _, _, error = generate_block_grid(
            min(new_dates), max(new_dates), new_dates, shape, mw
        )
        if not error:
            for _, row in gen_grid.iterrows():
                mw_by_date[as_date(row["Date"])] = {h: row[str(h)] for h in HOURS}

    grid = make_block_grid(dates, mw_by_date)
    st.session_state[block_grid_key(bid)] = grid
    return grid


def sync_block_dates(bid, default_start, default_end):
    """Keep a pristine block's Start/End Date following `default_start`/
    `default_end` (which track IsDAM and the WECC calendar's own trading
    session — see ui.session.block_date_defaults) until the trader diverges
    from it — by
    editing either date directly, or by using Generate/Clear, at which
    point the block has real content and silently moving its date range
    could drop entered MW values (get_block_grid reconciles to whatever
    dates it's given, dropping ones no longer in range).

    Needed because date_input's `value=` argument is only honored the
    first time a widget with a given key is created — flipping IsDAM on
    the very first block (id 0, which exists from the first script run)
    would otherwise never visibly change anything.
    """
    start_key, end_key = f"start_{bid}", f"end_{bid}"
    last_start_key, last_end_key = dates_last_default_keys(bid)
    last_default_start = st.session_state.get(last_start_key)
    last_default_end = st.session_state.get(last_end_key)

    if start_key in st.session_state:
        pristine = (
            get_version(bid) == 0
            and last_default_start is not None
            and st.session_state[start_key] == last_default_start
            and st.session_state[end_key] == last_default_end
        )
        if pristine:
            st.session_state[start_key] = default_start
            st.session_state[end_key] = default_end

    st.session_state[last_start_key] = default_start
    st.session_state[last_end_key] = default_end


def _render_paste_schedule(bid, block_dates, ver):
    """The "Paste MW" popover: a column of numbers straight out of Excel.

    Kept behind a popover because it's the exception, not the rule — most
    trades are a shape and an MW, and this is for the ones that aren't.
    Applying it replaces the whole block, exactly as Generate does (same
    seed/edits/version bookkeeping), so the editor is rebuilt around the
    pasted numbers rather than trying to merge them into what's there.
    """
    expected = len(HOURS) * len(block_dates)
    with st.popover("Paste MW", width="stretch"):
        st.caption(
            f"A column of MW copied from Excel — {len(HOURS)} values, one per "
            "hour ending"
            + (
                f", used on each of the {len(block_dates)} dates; or "
                f"{expected} for the dates in order."
                if len(block_dates) > 1
                else "."
            )
        )
        text = st.text_area(
            "MW values",
            key=f"paste_sched_{bid}",
            height=160,
            label_visibility="collapsed",
            placeholder="16\n19\n21\n21\n…",
        )
        if st.button("Apply", key=f"apply_paste_{bid}", type="primary", width="stretch"):
            mw_by_date, error = parse_pasted_schedule(text, block_dates)
            if error:
                st.error(error)
            else:
                st.session_state[block_grid_key(bid)] = make_block_grid(
                    block_dates, mw_by_date
                )
                st.session_state.pop(block_edits_key(bid), None)
                st.session_state[version_key(bid)] = ver + 1
                st.rerun()


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

    new_block_default_start, new_block_default_end = block_date_defaults(
        trade_date, is_dam
    )

    block_grids = {}
    block_ranges = {}
    block_shapes = {}
    block_mws = {}
    for bid in st.session_state.block_ids:
        with st.container(border=True):
            specs = [1.1, 1.1, 1.5, 0.7, 0.9, 1.1]
            if not is_monthly:
                specs.append(0.8)  # the paste-a-schedule popover
            show_remove = len(st.session_state.block_ids) > 1
            if show_remove:
                specs.append(1.0)
            row = st.columns(specs, vertical_alignment="bottom")
            dcol1, dcol2, gcol1, gcol2, gcol3, gcol4 = row[:6]
            # Filled in further down, once the block's dates are known.
            pcol = None if is_monthly else row[6]
            remove_col = row[-1] if show_remove else None

            # sync_block_dates may just have written today's default straight
            # into session_state; passing `value=` as well on that same call
            # logs a Streamlit policy warning, so it's only passed for a
            # widget key that doesn't exist yet (a genuinely new block).
            sync_block_dates(bid, new_block_default_start, new_block_default_end)
            start_key, end_key = f"start_{bid}", f"end_{bid}"
            start_kwargs = (
                {} if start_key in st.session_state
                else {"value": new_block_default_start}
            )
            start_date = dcol1.date_input("Start Date", key=start_key, **start_kwargs)
            end_kwargs = (
                {} if end_key in st.session_state
                else {"value": new_block_default_end}
            )
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
                    remove_col.button("Remove block", key=f"remove_{bid}", width="stretch")
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
                remove_col.button("Remove block", key=f"remove_{bid}", width="stretch")
                if show_remove else False
            )

            if remove_clicked:
                st.session_state.block_ids.remove(bid)
                st.session_state.pop(block_grid_key(bid), None)
                st.session_state.pop(block_edits_key(bid), None)
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

            # Filled now rather than with the rest of the row, because it
            # needs the block's dates to know how many values to expect.
            with pcol:
                _render_paste_schedule(bid, block_dates, ver)

            if generate_clicked:
                grid, excluded, missing, error = generate_block_grid(
                    start_date, end_date, block_dates, shape, mw
                )
                if error:
                    st.error(error)
                else:
                    st.session_state[block_grid_key(bid)] = grid
                    st.session_state.pop(block_edits_key(bid), None)
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
                st.session_state.pop(block_edits_key(bid), None)
                st.session_state[version_key(bid)] = ver + 1
                st.rerun()

            grid_seed = get_block_grid(bid, block_dates, shape, mw)
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
            # Deliberately *not* block_grid_key: feeding this back as the
            # editor's data renames the widget and loses the next edit.
            st.session_state[block_edits_key(bid)] = edited
            block_grids[bid] = block_grid_to_date_frames(edited)

    return block_grids, block_ranges, block_shapes, block_mws
