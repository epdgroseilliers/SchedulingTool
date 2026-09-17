"""The action row (Add another block / Add Trade / Preview DB Insert / Input
in DB), the past-date confirmation gate, and what happens when each button
fires.
"""

from datetime import date

import pandas as pd
import streamlit as st

from data.bilateral import (
    compress_schedule,
    create_compliance_folders,
    format_db_price,
    insert_trade,
    resolve_and_validate,
)
from domain.options import TIME_ZONE
from domain.shapes import monthly_block_rows, shape_label
from domain.trade import db_input_errors, db_input_warnings
from ui.session import push_flash


def render_action_row():
    """Returns (add_block_clicked, add_trade_clicked, preview_clicked, input_in_db)."""
    bcol1, bcol2, bcol3, bcol4, _ = st.columns(
        [1.5, 1.5, 1.8, 1.5, 3.7], vertical_alignment="bottom"
    )
    add_block_clicked = bcol1.button("+ Add another block", width="stretch")
    add_trade_clicked = bcol2.button(
        "Add Trade", type="primary", key="add_trade_btn", width="stretch"
    )
    preview_clicked = bcol3.button(
        "Preview DB Insert",
        key="preview_db_btn",
        width="stretch",
        help="Run the DB lookups and duplicate check, and show the rows that "
        "would be written — without writing anything.",
    )
    input_in_db = bcol4.checkbox(
        "Input in DB",
        value=False,
        help="Also write this trade to PhysiqueBilateral.west.BilateralTrades.",
    )
    return add_block_clicked, add_trade_clicked, preview_clicked, input_in_db


def render_past_date_gate(trade_date, block_ranges, input_in_db):
    """The macro asked for confirmation in a dialog box; Streamlit has no
    blocking prompt inside a script run, so the confirmation is a checkbox
    that only appears when a date is actually in the past.

    Returns (past_dated, past_confirmed).
    """
    today = date.today()
    past_dated = trade_date < today or any(
        s < today or e < today for s, e in block_ranges.values()
    )
    past_confirmed = False
    if input_in_db and past_dated:
        st.warning(
            "This trade has a trade date, start date, or stop date in the past."
        )
        past_confirmed = st.checkbox("Confirm past-dated trade", value=False)
    return past_dated, past_confirmed


def handle_add_block(add_block_clicked):
    if not add_block_clicked:
        return
    st.session_state.block_ids.append(st.session_state.next_block_id)
    st.session_state.next_block_id += 1
    st.rerun()


def _flatten_schedule(block_grids):
    """Flatten {bid: {date: long HE/MW DataFrame}} into a single (date, he,
    mw) list plus the same thing split back out per block (for compression,
    which needs each block's own Shape). Returns (schedule, block_schedules,
    errors) — errors covers duplicate (date, HE) entries across blocks.
    """
    schedule = []
    block_schedules = {}
    errors = []
    seen = set()
    for bid, date_grids in block_grids.items():
        block_schedules[bid] = []
        for d, grid in date_grids.items():
            for _, row in grid.iterrows():
                he = row.get("HE")
                mw_val = row.get("MW")
                if pd.isna(he) or pd.isna(mw_val) or mw_val <= 0:
                    continue
                key = (d, int(he))
                if key in seen:
                    errors.append(
                        f"Duplicate schedule entry for {d} HE {he} — merge into one block."
                    )
                    continue
                seen.add(key)
                schedule.append((d, int(he), float(mw_val)))
                block_schedules[bid].append((d, int(he), float(mw_val)))
    return schedule, block_schedules, errors


def handle_submit(
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
):
    """Validate, and either preview or write, a trade. `trade_fields` holds
    everything from ui.trade_fields plus trade_date/is_dam — see app.py for
    exactly what's assembled.

    Runs the full pipeline (local validation -> DB lookups/duplicate check
    -> insert) up to whichever step the button asked for. Preview stops
    just short of insert_trade(), so what's previewed is exactly what a
    real submit would attempt.
    """
    if not (add_trade_clicked or preview_clicked):
        return

    errors = []
    if not trade_fields["counterparty"]:
        errors.append("Counterparty is required.")
    if not trade_fields["location"]:
        errors.append("Location is required.")

    is_monthly = bool(trade_fields.get("is_monthly"))
    block_schedules = {}
    if is_monthly:
        # A fixed MW for the whole period needs no hourly grid — each
        # block is already exactly the row that gets written, so there's
        # no per-hour schedule to flatten or fold back down.
        blocks = [
            (*block_ranges[bid], block_shapes.get(bid, ""), block_mws.get(bid))
            for bid in block_shapes
        ]
        schedule = []
        db_rows, shape_errors = monthly_block_rows(blocks)
        errors += shape_errors
        if not db_rows:
            errors.append("At least one schedule block is required.")
    else:
        schedule, block_schedules, dup_errors = _flatten_schedule(block_grids)
        errors += dup_errors
        if not schedule:
            errors.append("Schedule needs at least one hour with MW > 0.")
        db_rows = []

    trade = {
        **trade_fields,
        "schedule": schedule,
        "time_zone": TIME_ZONE,
    }
    if is_monthly:
        trade["monthly_blocks"] = db_rows if not errors else []

    # Everything the DB needs is checked before a single row is written, the
    # way the macro validated every sheet row before importing any of them.
    # Preview runs this same path — it just stops short of insert_trade() —
    # so what you see previewed is exactly what a real submit would attempt.
    run_db_path = input_in_db or preview_clicked
    if run_db_path and not errors:
        if not is_monthly:
            # Compressed per block, so each block's Shape can name its He
            # the way the existing rows do ('HL' rather than '7-22').
            for bid, block_schedule in block_schedules.items():
                db_rows += compress_schedule(
                    block_schedule, shape_label(block_shapes.get(bid))
                )
            db_rows.sort(key=lambda r: (r["start_date"], r["he"]))
        # Preview bypasses the past-date confirmation gate: it's a "would
        # this block a real insert" note, not something previewing itself
        # should be blocked on — the checkbox that clears it only renders
        # when Input in DB is ticked.
        errors += db_input_errors(
            trade, db_rows, past_dated, True if preview_clicked else past_confirmed
        )

    resolved_rows = []
    if run_db_path and not errors:
        try:
            resolved_rows, db_errors = resolve_and_validate(
                db_rows,
                dict(
                    trade,
                    price_text=format_db_price(trade["index"], trade["price"]),
                ),
            )
        except Exception as e:
            errors.append(f"Could not reach the trades database: {e}")
        else:
            errors += db_errors

    if errors:
        for e in errors:
            st.error(e)
        if preview_clicked:
            st.session_state.db_preview = None
        return

    if preview_clicked:
        st.session_state.db_preview = {
            "trade": trade,
            "rows": resolved_rows,
            "past_dated": past_dated and not past_confirmed,
        }
        print(f"\n=== DB insert preview: {len(resolved_rows)} row(s) ===")
        for r in resolved_rows:
            print(r)
        return

    _insert_and_save(trade, resolved_rows, input_in_db)


def _insert_and_save(trade, resolved_rows, input_in_db):
    db_trade_ids = []
    if input_in_db:
        try:
            db_trade_ids = insert_trade(resolved_rows)
        except Exception as e:
            st.error(f"Nothing was written — the insert failed: {e}")
            return
        created, existing, failed = create_compliance_folders(trade, db_trade_ids)
        for path in created:
            push_flash("info", f"Folder created: {path}")
        for path in existing:
            push_flash("info", f"Folder already exists: {path}")
        for problem in failed:
            push_flash("warning", f"Trade saved, but a folder could not be created: {problem}")

    st.session_state.db_preview = None
    trade["db_trade_ids"] = db_trade_ids
    st.session_state.trades.append(trade)
    # Schedule blocks (dates, shape, MW, grid) are deliberately left as-is —
    # booking several trades against the same schedule is a common desk
    # workflow, and re-entering it every time isn't. Trader-facing fields
    # above the Schedule section persist too, simply because nothing here
    # clears their widget state either.
    if input_in_db:
        for note in db_input_warnings(trade):
            push_flash("warning", note)
    if db_trade_ids:
        push_flash(
            "success",
            f"Trade added and written to the DB as {len(db_trade_ids)} row(s): "
            f"{', '.join(str(i) for i in db_trade_ids)}.",
        )
    else:
        push_flash("success", "Trade added.")
    st.rerun()
