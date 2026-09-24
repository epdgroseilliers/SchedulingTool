"""Session-state initialization and the small helpers other ui modules use
to read/write it consistently.
"""

from datetime import date

import streamlit as st

from domain.options import (
    DEFAULT_COMMUNICATION,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    RARE_FIELD_DEFAULTS,
)
from data.calendar import sessions_near
from domain.trade import default_flow_window, has_trading_session


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


def block_date_defaults(trade_date, is_dam):
    """(start, end) a fresh block's dates default to.

    The one place the WECC calendar's session-to-flow-date pairing is read
    for this — schedule.py, paste.py and the form reset all come through
    here so a trade date means the same range wherever it's set. An
    unreachable calendar falls back to the plain next-day default rather
    than blocking the page; clicking Generate still surfaces the error.
    """
    try:
        sessions = sessions_near(trade_date)
    except Exception:
        sessions = {}
    return default_flow_window(trade_date, is_dam, sessions)


def trade_date_has_session(trade_date):
    """Whether a day-ahead trade can exist for this trade date — ie. whether
    the WECC calendar has a trading session on it. An unreachable calendar
    answers True and leaves the choice alone."""
    try:
        sessions = sessions_near(trade_date)
    except Exception:
        sessions = {}
    return has_trading_session(trade_date, sessions)


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

    # Drop every block back to a single fresh one. Start/End/Shape/MW are
    # set directly (not popped) for every bid that existed — date_input's
    # value= kwarg is only honored the very first time a widget with a
    # given key is ever created, and these have already rendered many
    # times over by now, so popping them and relying on
    # render_schedule_section's own value= fallback would silently leave
    # them stuck at their old values (this also protects a block id that
    # gets reused later via "+ Add another block").
    trade_date = st.session_state.get("trade_date", date.today())
    is_dam = st.session_state.get("is_dam", True)
    default_start, default_end = block_date_defaults(trade_date, is_dam)
    for bid in st.session_state.block_ids:
        st.session_state.pop(block_grid_key(bid), None)
        st.session_state.pop(version_key(bid), None)
        st.session_state[f"shape_{bid}"] = "HL"
        st.session_state[f"mw_{bid}"] = 25
        st.session_state[f"start_{bid}"] = default_start
        st.session_state[f"end_{bid}"] = default_end
        last_start_key, last_end_key = dates_last_default_keys(bid)
        st.session_state[last_start_key] = default_start
        st.session_state[last_end_key] = default_end
    st.session_state.block_ids = [0]
    st.session_state.next_block_id = 1

    st.session_state.db_preview = None
