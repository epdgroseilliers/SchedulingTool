"""The non-schedule trade fields: the entry row (Trade Date/IsDAM plus the
broker-string paste box), the economics row (counterparty/location/index/
price plus the back-office fields), and the rarely-used "Other attributes".

Each render_* function returns the values it collected; app.py assembles
them into the trade dict passed to ui.actions.
"""

from datetime import date

import streamlit as st

from domain.options import (
    COMMUNICATION_METHODS,
    COUNTERPARTIES,
    DEFAULT_COMMUNICATION,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    INDEXES,
    LOCATIONS,
    RARE_FIELD_LABELS,
    SPECIFIED_SOURCES,
    WSPP_CONTRACT_TYPES,
    default_index,
)
from domain.trade import rare_fields_set
from ui.paste import render_paste_input
from ui.session import widget_defaults


def render_entry_row():
    """Trade Date, IsDAM, and the broker-string paste box, side by side.

    Trade Date almost never differs from today, and IsDAM drives the
    Schedule section's default flow date — both are context the paste
    shouldn't need to touch, which is why they sit beside it rather than
    below it. Call ui.paste.render_paste_summary() after this to show
    what the paste was understood as (full width, not squeezed into a
    column).

    Returns (trade_date, is_dam).
    """
    top1, top2, top3 = st.columns([1.5, .5, 7], vertical_alignment="bottom")
    trade_date = top1.date_input(
        "Trade Date", key="trade_date", help="Date the deal was struck.",
        **widget_defaults("trade_date", value=date.today()),
    )
    is_dam = top2.checkbox(
        "IsDAM",
        key="is_dam",
        help="Writes DAM_RT as DAM when ticked, RT when not. Left NULL for a monthly trade. "
        "Also sets a new block's default Start Date: the next day (day-ahead) when ticked, "
        "the trade date (real-time) when not.",
        **widget_defaults("is_dam", value=True),
    )
    render_paste_input(top3)
    return trade_date, is_dam


def render_economics_row():
    """Sell/Counterparty/Location/Index/Price, plus Communication/Specified
    Source/WSPP/IsNWS/IsSourceNonCaiso in the same row — what the parser
    fills on a clean parse, and the fallback/review surface either way, all
    at a glance in one line. The two checkbox-like fields (Sell, IsNWS,
    IsSourceNonCaiso) get narrower columns than the selects/number input.

    Returns a dict: direction, counterparty, location, index_name, price,
    communication, specified_source, wspp_contract, is_nws,
    is_source_non_caiso.
    """
    c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns(
        [0.6, 1.1, 1.1, 1.0, 0.9, 1.1, 1.4, 0.9, 0.6, 0.8],
        vertical_alignment="bottom",
    )
    is_sell = c1.toggle(
        "Sell", key="is_sell", help="Off = Buy, On = Sell",
        **widget_defaults("is_sell", value=False),
    )
    counterparty = c2.selectbox(
        "Counterparty",
        COUNTERPARTIES,
        placeholder="Select or type…",
        key="counterparty",
        accept_new_options=True,
        **widget_defaults("counterparty", index=None),
    )
    location = c3.selectbox(
        "Location (POR/POD)",
        LOCATIONS,
        placeholder="Select or type…",
        key="location",
        accept_new_options=True,
        **widget_defaults("location", index=None),
    )
    index_name = c4.selectbox(
        "Index",
        INDEXES,
        # index=None (not widget_defaults' usual omit-once-set) every run,
        # not just the first: that's what keeps this box clearable back to
        # blank — Streamlit only shows the clear affordance while a
        # selectbox is created with index=None, and the parser writing
        # index_name via the Session State API means this can log that
        # policy warning right after a paste. Worth it: without it, once
        # any value is set the field can never be cleared again.
        index=None,
        placeholder="None (fixed price)",
        key="index_name",
        accept_new_options=True,
        help="Leave blank for a flat fixed price.",
    )
    price = c5.number_input(
        "Price / Premium",
        step=0.01,
        key="price",
        help="Flat price if Index is blank. Premium to the index (e.g. +2 or -1) if Index is set.",
    )
    communication = c6.selectbox(
        "Communication",
        COMMUNICATION_METHODS,
        key="communication",
        accept_new_options=True,
        **widget_defaults(
            "communication",
            index=default_index(COMMUNICATION_METHODS, DEFAULT_COMMUNICATION),
        ),
    )
    specified_source = c7.selectbox(
        "Specified Source",
        SPECIFIED_SOURCES,
        key="specified_source",
        accept_new_options=True,
        **widget_defaults(
            "specified_source",
            index=default_index(SPECIFIED_SOURCES, DEFAULT_SPECIFIED_SOURCE),
        ),
    )
    wspp_contract = c8.selectbox(
        "WSPP Contract Type",
        WSPP_CONTRACT_TYPES,
        key="wspp_contract",
        accept_new_options=True,
        **widget_defaults(
            "wspp_contract",
            index=default_index(WSPP_CONTRACT_TYPES, DEFAULT_WSPP_CONTRACT),
        ),
    )
    is_nws = c9.checkbox("IsNWS", key="is_nws", **widget_defaults("is_nws", value=False))
    is_source_non_caiso = c10.checkbox(
        "IsSourceNonCaiso",
        key="is_source_non_caiso",
        **widget_defaults("is_source_non_caiso", value=False),
    )
    return {
        "direction": "Sell" if is_sell else "Buy",
        "counterparty": counterparty,
        "location": location,
        "index_name": index_name,
        "price": price,
        "communication": communication,
        "specified_source": specified_source,
        "wspp_contract": wspp_contract,
        "is_nws": is_nws,
        "is_source_non_caiso": is_source_non_caiso,
    }


def render_other_attributes():
    """The rarely-used fields (ResupplyId, IsOption, ...), collapsed behind
    an expander whose label counts how many are off their default.

    Returns the rare-fields dict (also stored in session_state, so edits
    survive collapsing the expander).
    """
    rare = st.session_state.rare_fields
    off_default = rare_fields_set(rare)

    label = "Other attributes"
    if off_default:
        label = f"Other attributes — {len(off_default)} field(s) set"
    with st.expander(label, expanded=False):
        r0, r1, r2, r3 = st.columns(4)
        rare["resupply_id"] = r0.text_input(
            RARE_FIELD_LABELS["resupply_id"], value=rare["resupply_id"]
        )
        rare["secondary_por_pod"] = r1.text_input(
            RARE_FIELD_LABELS["secondary_por_pod"], value=rare["secondary_por_pod"]
        )
        rare["resource_adequacy_id"] = r2.text_input(
            RARE_FIELD_LABELS["resource_adequacy_id"], value=rare["resource_adequacy_id"]
        )
        rare["exchange_id"] = r3.text_input(
            RARE_FIELD_LABELS["exchange_id"], value=rare["exchange_id"]
        )
        r4, r5, _ = st.columns([1, 1, 3])
        rare["is_option"] = r4.checkbox(
            RARE_FIELD_LABELS["is_option"], value=rare["is_option"]
        )
        rare["is_monthly"] = r5.checkbox(
            RARE_FIELD_LABELS["is_monthly"], value=rare["is_monthly"]
        )
    return rare
