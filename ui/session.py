"""Session-state initialization and the small helpers other ui modules use
to read/write it consistently.
"""

import streamlit as st

from domain.options import RARE_FIELD_DEFAULTS


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
