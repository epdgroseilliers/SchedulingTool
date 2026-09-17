"""The broker-string paste box: parse on Enter, fill the form, show what was
understood. See data/trade_string.py for the parser itself.
"""

from datetime import date

import streamlit as st

from data.bilateral import load_market_full_names
from data.trade_string import parse_trade_string
from domain.options import (
    COUNTERPARTIES,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    INDEXES,
    LOCATIONS,
    PARSED_COMMUNICATION,
    SPECIFIED_SOURCES,
)
from domain.trade import format_price


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

    # A pasted string describes exactly one leg, so collapse back to one
    # block and drop every existing grid — otherwise leftover blocks from a
    # previous trade would silently ride along on this one.
    st.session_state.block_ids = [0]
    for key in [
        k for k in st.session_state
        if k.startswith(("block_grid_", "grid_ver_", "block_editor_"))
    ]:
        del st.session_state[key]

    st.session_state.shape_0 = parsed.get("shape")
    mw = parsed.get("mw")
    if float(mw) != int(mw):
        summary["warnings"].append(
            f"{mw:g} MW rounded to {round(mw)} — whole MW only."
        )
    st.session_state.mw_0 = int(round(mw))

    start = parsed.get("start_date")
    if start:
        st.session_state.start_0 = start
        st.session_state.end_0 = parsed.get("end_date", start)
    else:
        # No flow date in the string: clear the dates so they fall back to
        # the IsDAM-driven WECC default for a fresh block.
        for key in ("start_0", "end_0", "dates_last_default_0"):
            st.session_state.pop(key, None)

    price_text = format_price(parsed.get("index"), float(parsed.get("price")))
    summary["lines"] = [
        f"{parsed.get('direction')} {parsed.get('counterparty')}",
        f"{parsed.get('mw'):g} MW",
        str(parsed.get("shape")),
        f"@ {parsed.get('location')}",
        price_text,
        f"flow {start}" if start else "flow: default",
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
