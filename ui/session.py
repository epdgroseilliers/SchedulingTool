"""Session-state initialization and the small helpers other ui modules use
to read/write it consistently.
"""

import streamlit as st

from domain.options import (
    DEFAULT_COMMUNICATION,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    RARE_FIELD_DEFAULTS,
)


def init_session_state():
    """Seed every session_state key the app reads before it's ever written.
    Safe to call on every rerun — each guard only fires once per session.
    """
    if "trades" not in st.session_state:
        st.session_state.trades = []
    if "block_ids" not in st.session_state:
        st.session_state.block_ids = [0]
    if "next_block_id" not in st.session_state:
        st.session_state.next_block_id = 1
    if "rare_fields" not in st.session_state:
        st.session_state.rare_fields = dict(RARE_FIELD_DEFAULTS)
    # (kind, text) pairs to show once after the rerun that follows a submit —
    # st.success/st.warning written just before st.rerun() never reach the screen.
    if "flash" not in st.session_state:
        st.session_state.flash = []
    # Last "Preview DB Insert" result (resolved rows, no write). Persisted in
    # session_state — not just rendered inline on the click — so it survives
    # the reruns caused by tweaking other fields, instead of vanishing
    # immediately.
    if "db_preview" not in st.session_state:
        st.session_state.db_preview = None
    # Last broker string parsed, so a paste fires once rather than on every rerun.
    if "last_parsed_string" not in st.session_state:
        st.session_state.last_parsed_string = None
    if "parse_summary" not in st.session_state:
        st.session_state.parse_summary = None


def widget_defaults(key, **defaults):
    """Default kwargs for a widget, dropped once its key holds a value.

    Passing a default *and* having a session_state value for the same key
    logs a Streamlit policy warning, and the broker-string parser fills
    these keys directly.
    """
    return {} if key in st.session_state else defaults


def push_flash(kind, text):
    """Queue a message (kind is an st.* method name: 'success'/'warning'/…)
    to show on the *next* render — for use right before st.rerun(), since a
    message written just before it never reaches the screen."""
    st.session_state.flash.append((kind, text))


def flush_flash():
    """Show and clear any messages queued by push_flash(). Call once, near
    the top of the page, before anything else renders."""
    for kind, text in st.session_state.flash:
        getattr(st, kind)(text)
    st.session_state.flash = []


def block_grid_key(bid):
    return f"block_grid_{bid}"


def version_key(bid):
    return f"grid_ver_{bid}"


def get_version(bid):
    return st.session_state.get(version_key(bid), 0)


def dates_last_default_keys(bid):
    return f"dates_last_default_start_{bid}", f"dates_last_default_end_{bid}"


def queue_form_reset():
    """Request a full form reset on the very next script run — see
    apply_pending_form_reset(). A widget's session_state key can't be
    reassigned once that widget has rendered this same run (Streamlit
    raises), and by the time a button handler or a submit path knows it
    needs to reset the form, most of the page's widgets already have —
    so the actual reset always happens at the very top of the *next* run,
    before any widget gets the chance to claim its key. Callers should
    st.rerun() right after calling this.
    """
    st.session_state["_pending_form_reset"] = True


def apply_pending_form_reset():
    """Call once, at the very top of the script, before any widget is
    created — applies a reset queued by queue_form_reset() on the
    previous run."""
    if st.session_state.pop("_pending_form_reset", False):
        reset_trade_fields()


def reset_trade_fields():
    """Reset every entry field to its default — Trade Date and IsDAM are
    the only exceptions, since those are context the trader sets once and
    expects to persist across trades.

    Only safe to call before any widget has rendered this script run — use
    queue_form_reset() from a button handler or a submit path instead of
    calling this directly.
    """
    st.session_state.is_sell = False
    st.session_state.counterparty = None
    st.session_state.location = None
    st.session_state.index_name = None
    st.session_state.price = 0.0
    st.session_state.communication = DEFAULT_COMMUNICATION
    st.session_state.specified_source = DEFAULT_SPECIFIED_SOURCE
    st.session_state.wspp_contract = DEFAULT_WSPP_CONTRACT
    st.session_state.is_nws = False
    st.session_state.is_source_non_caiso = False
    st.session_state.rare_fields = dict(RARE_FIELD_DEFAULTS)

    st.session_state.paste_box = ""
    st.session_state.last_parsed_string = None
    st.session_state.parse_summary = None

    # Drop every block back to a single fresh one; it auto-populates from
    # Trade Date/IsDAM on the next render exactly like a brand-new page load.
    for bid in st.session_state.block_ids:
        st.session_state.pop(block_grid_key(bid), None)
        st.session_state.pop(version_key(bid), None)
        last_start_key, last_end_key = dates_last_default_keys(bid)
        for key in (
            f"start_{bid}", f"end_{bid}", f"shape_{bid}", f"mw_{bid}",
            last_start_key, last_end_key,
        ):
            st.session_state.pop(key, None)
    st.session_state.block_ids = [0]
    st.session_state.next_block_id = 1

    st.session_state.db_preview = None
