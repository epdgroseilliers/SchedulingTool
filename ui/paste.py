"""The broker-string paste box: parse on Enter, fill the form, show what was
understood. See data/trade_string.py for the parser itself.
"""

from datetime import date

import streamlit as st

from data.bilateral import load_market_full_names
from data.trade_string import parse_trade_string
from domain.grid import dates_in_range
from domain.options import (
    COUNTERPARTIES,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    INDEXES,
    LOCATIONS,
    PARSED_COMMUNICATION,
    SPECIFIED_SOURCES,
)
from domain.shapes import combined_block_grid, segments_share_one_block
from domain.trade import format_price
from ui.session import block_date_defaults, block_grid_key, dates_last_default_keys


def apply_parsed_string(text):
    """Parse a broker string and fill the form from it.

    Runs before any of the widgets it fills are created, so the values land
    in the same script run — no rerun needed. On any parse error nothing is
    filled at all: a half-populated form is worse than an obvious refusal.
    """
    # Anchor relative dates (a bare weekday, or the year for an MM/DD flow
    # date) on the Trade Date the trader set — not the real wall-clock date.
    # Trade Date renders before the paste box for exactly this reason, so
    # its widget already holds a value by the time this runs.
    trade_date = st.session_state.get("trade_date") or date.today()
    parsed = parse_trade_string(
        text,
        counterparties=COUNTERPARTIES,
        locations=LOCATIONS,
        indexes=INDEXES,
        specified_sources=[s for s in SPECIFIED_SOURCES if s],
        full_names=load_market_full_names(),
        today=trade_date,
    )
    summary = {"errors": list(parsed.errors), "warnings": list(parsed.warnings), "lines": []}
    if not parsed.ok:
        st.session_state.parse_summary = summary
        return

    st.session_state.is_sell = parsed.get("direction") == "Sell"
    st.session_state.counterparty = parsed.get("counterparty")
    st.session_state.location = parsed.get("location")
    st.session_state.index_name = parsed.get("index")
    st.session_state.price = float(parsed.get("price"))
    # Trade Date is left alone: it's set as context above, before pasting,
    # not something the string should override.

    # Every broker string is pasted from ICE Chat by default; the string can
    # name a different method (a broker, or a specific phone contact).
    st.session_state.communication = parsed.get("communication", PARSED_COMMUNICATION)
    st.session_state.wspp_contract = parsed.get("wspp_contract", DEFAULT_WSPP_CONTRACT)
    st.session_state.specified_source = parsed.get(
        "specified_source", DEFAULT_SPECIFIED_SOURCE
    )
    st.session_state.is_nws = bool(parsed.get("is_nws", False))
    st.session_state.is_source_non_caiso = bool(parsed.get("is_source_non_caiso", False))
    # A flow range spanning a month or more (e.g. a quarter) implies IsMonthly.
    st.session_state.rare_fields["is_monthly"] = bool(parsed.get("is_monthly", False))

    # A pasted string describes one trade, but a trade can run at one MW
    # over some hours and another MW over others ("150mw he9-12 and 100mw
    # he13-14"). That is **one schedule block with a profile that steps**:
    # a block's Shape and MW only seed its grid, and the grid is what gets
    # written. The database still gets its row per shape, because
    # compress_schedule splits a block by MW level on the way in.
    #
    # Two quantities only need blocks of their own when one of them names
    # HL, LL or ATC, or when their hours overlap — see
    # domain.shapes.segments_share_one_block, which is where both reasons
    # are written down.
    segments = parsed.get("segments") or [
        {"mw": parsed.get("mw"), "shape": parsed.get("shape")}
    ]
    one_block = segments_share_one_block(segments)
    # What the Shape and MW boxes of each block show. On a stepped
    # profile that is the first quantity, which is what `mw` and `shape`
    # mean everywhere else too: the boxes are the grid's generator, not
    # its record, and pressing Generate flattens the profile to them,
    # which is what Generate has always meant.
    blocks = segments[:1] if one_block else segments
    for key in [
        k for k in st.session_state
        if k.startswith(("block_grid_", "block_edits_", "grid_ver_", "block_editor_"))
    ]:
        del st.session_state[key]
    # Every block the string names, and no others: a leftover block from a
    # previous paste would silently ride along on this one.
    #
    # Dropping the keys of the blocks that go is only half Streamlit's job.
    # It discards a *widget's* state on a run that doesn't render it, so
    # Shape and MW clear themselves — but the default each block's dates
    # were last given is plain state (ui.session.dates_last_default_keys),
    # which is never collected and would be sitting there waiting for the
    # next block to be given that number.
    for bid in st.session_state.block_ids:
        if bid < len(blocks):
            continue
        for name in (f"shape_{bid}", f"mw_{bid}", f"start_{bid}", f"end_{bid}",
                     *dates_last_default_keys(bid)):
            st.session_state.pop(name, None)
    st.session_state.block_ids = list(range(len(blocks)))
    st.session_state.next_block_id = len(blocks)

    start = parsed.get("start_date")
    if not start:
        # No flow date in the string: recompute the same IsDAM/calendar-
        # driven default a fresh block would get, and set it directly.
        # Popping the keys and relying on render_schedule_section's own
        # value= fallback doesn't work here — date_input's value= kwarg is
        # only ever honored the very first time a widget with a given key
        # is created, and Start/End Date have already rendered many times
        # over by this point, so that fallback silently leaves the
        # displayed dates stuck at whatever they last were.
        is_dam = st.session_state.get("is_dam", True)
        start, end = block_date_defaults(trade_date, is_dam)
        defaulted = True
    else:
        end = parsed.get("end_date", start)
        defaulted = False

    for bid, segment in enumerate(blocks):
        st.session_state[f"shape_{bid}"] = segment["shape"]
        mw = segment["mw"]
        if float(mw) != int(mw):
            summary["warnings"].append(
                f"{mw:g} MW rounded to {round(mw)} — whole MW only."
            )
        st.session_state[f"mw_{bid}"] = int(round(mw))
        st.session_state[f"start_{bid}"] = start
        st.session_state[f"end_{bid}"] = end
        if defaulted:
            last_start_key, last_end_key = dates_last_default_keys(bid)
            st.session_state[last_start_key] = start
            st.session_state[last_end_key] = end

    if one_block and len(segments) > 1:
        # The one block's grid, built here rather than left to Shape/MW,
        # which between them can only describe the first quantity.
        # ui.schedule.get_block_grid hands a stored grid straight back
        # when its dates are the block's, so this is the seed the editor
        # opens on.
        block_dates = dates_in_range(start, end)
        grid, error = combined_block_grid(start, end, block_dates, segments)
        if error:
            summary["warnings"].append(error)
        else:
            st.session_state[block_grid_key(0)] = grid

    price_text = format_price(parsed.get("index"), float(parsed.get("price")))
    summary["lines"] = [
        f"{parsed.get('direction')} {parsed.get('counterparty')}",
        # One entry per block, so a split schedule is visibly a split one
        # before the trader scrolls down to the blocks themselves.
        " + ".join(f"{seg['mw']:g} MW {seg['shape']}" for seg in segments),
        f"@ {parsed.get('location')}",
        price_text,
        f"flow {start}" if not defaulted else "flow: default",
        f"WSPP {st.session_state.wspp_contract}",
    ]
    if st.session_state.specified_source:
        summary["lines"].append(f"source {st.session_state.specified_source}")
    if st.session_state.is_nws:
        summary["lines"].append("IsNWS")
    if st.session_state.is_source_non_caiso:
        summary["lines"].append("IsSourceNonCaiso")
    if st.session_state.rare_fields["is_monthly"]:
        summary["lines"].append("IsMonthly")
    if st.session_state.communication != PARSED_COMMUNICATION:
        summary["lines"].append(f"via {st.session_state.communication}")
    st.session_state.parse_summary = summary


def render_paste_input(target=st):
    """The broker-string text input only: paste, press Enter, the form
    fills in. `target` is where the widget is placed — pass a column to
    put it beside other controls; defaults to the full page width.

    Nothing is written to the database here — the filled form is still
    reviewed and submitted with Add Trade as usual. Call
    render_paste_summary() afterward to show what was understood.
    """
    text = target.text_input(
        "Paste broker string",
        key="paste_box",
        placeholder="APS SELLS/MAG BUYS 100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c",
        help="Fills the fields below. Nothing is saved until you press Add Trade.",
    )
    # Guarded so a paste is parsed once, not again on every later rerun —
    # which would otherwise undo any hand-edit made after the paste.
    if text and text != st.session_state.get("last_parsed_string"):
        st.session_state.last_parsed_string = text
        apply_parsed_string(text)


def render_paste_summary():
    """Errors/warnings/what-was-understood from the last paste, if any.
    Rendered separately from the input so the input can sit in a narrow
    column while this still spans the full page width."""
    summary = st.session_state.get("parse_summary")
    if not summary:
        return
    for message in summary.get("errors", []):
        st.error(message)
    for message in summary.get("warnings", []):
        st.warning(message)
    if summary.get("lines"):
        st.caption("Parsed → " + "  ·  ".join(summary["lines"]))
