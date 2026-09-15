import re
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from data.calendar import is_peak_map
from data.bilateral import (
    TIME_ZONES,
    compress_schedule,
    create_compliance_folders,
    format_db_price,
    insert_trade,
    load_market_full_names,
    resolve_and_validate,
)
from data.trade_string import parse_trade_string

st.set_page_config(page_title="Trade Scheduler", page_icon="⚡", layout="wide")

# --- Option lists --------------------------------------------------------
# Fill these in with the values the desk and back office use. The order here
# is the order shown in the dropdown. Every entry is a plain string.
#
# The dropdowns also accept a typed-in value, so a name missing from a list
# never blocks a trade — it just isn't offered as a choice.
COUNTERPARTIES = ["AZPS", "ABEX", "BPAT", "SCLM",  "EPE", "NEVP", "PACE", "PNM", "WALC", "PSCO", "SRP",
    "TNSK", "CONC", "EEMU", "AVAT", "ATOP", "GPM", "P66T", "SCET", "DYNP",
    "ENKP", "HRTL", "TEA", "MCPI", "BHP", "IID", "CSUM", "MEAI", "BEPC", "BPEC",
    "UMPA", "IPCM", "MSCG", "DECM", "CORP", "NWDS", "DGTM", "TEPM", "REMC", "SCP",
    "CCG", "CHPM",  "UNSE", "MID", "PRPA", "SMUD", "VTOL", "CEM", "BURB",
    "EPCR", "TEMU", "TPWP", "AEPC", "PGEM", "TIDS", "DRWT", "BRTM", "NRG", "EAGL",
    "LAWM", "LAC1A", "CAISO"]
LOCATIONS = ["PALOVERDE500", "MIDCRemote", "MEAD230", "M345", "LAM345", "MATL.NWMT",
    "SPRINGER345", "AU", "MDWP", "MIDW", "DJ", "NAVAJO500", "MALIN500", "Boundary",
    "JEFF", "JOHNDAY", "LAGRANDE", "FOURCORNE345", "LOLO", "MIDC", "BPAT.NWMT",
    "MCWEST.NWMT", "AMRAD345", "PNPKWALC230", "BPAPUNSCHD", "EDDY230", "BRDY",
    "BC.US.Border", "WESTWING500", "Sylmar", "BigEddy", "SPRINGER345", "GLENCANYON2",
    "WWA", "AB.MT.MATL", "BPAPOWER", "SLATT230", "CROSSOVER", "JBSN", "YTP",
    "ARLINGTONWIND", "AVAT.NWMT", "BPAT.GCPD", "COLSTRIP", "KERR", "REDB", "PACE",
    "AVA.BPAT", "BPAT.PGE", "GLWND1"]
INDEXES = ["PALOVERDE", "MONA", "MEAD230", "AESO", "MIDC", "CAISO MALIN DA"]
COMMUNICATION_METHODS = [None, "ICE", "ICE Chat", "Broker - BGC", "ITAP", "Broker - Equus", 
    "Phone - Thomas", "Phone - Charles", "Phone - Emilio", "Phone - Byron", "Broker - Tullett", "Broker - ChoicePower", "EnelX",
    "Replacement Tag","Email"]
WSPP_CONTRACT_TYPES = ["C", "B"]
SPECIFIED_SOURCES = [None,
    "Bonneville Power Administration",
    "Palo Verde Nuclear",
    "Boundary Dam Hydro",
    "Lucky Peak Power Plant",
    "Kerr Hydro",
    "Headgate Rock Hydro",
    "Tacoma Power - ACS",
    "Seattle City Light - ACS",
    "Lake Chelan Hydro",
    "Mid-C Hydro - Rock Island (Chelan County PUD)",
    "Mid-C Hydro - Rocky Reach (Chelan County PUD)",
    "Oxbox-Brownlee - Idaho Power"]

DEFAULT_COMMUNICATION = None  # matches the placeholder entry in COMMUNICATION_METHODS
DEFAULT_WSPP_CONTRACT = "C"
DEFAULT_SPECIFIED_SOURCE = None

# Every broker string is pasted from ICE Chat, so this is constant rather
# than a per-trade choice.
PARSED_COMMUNICATION = "ICE Chat"

# The desk only trades Pacific Prevailing Time, so this isn't a per-trade
# choice — asserted against the DB's enum so a change there can't silently
# write an invalid value.
TIME_ZONE = "PPT"
assert TIME_ZONE in TIME_ZONES, f"{TIME_ZONE!r} is not one of {TIME_ZONES}"

# POR/PODs that legitimately settle against the MIDC index. The macro read
# these from the sheet's AN5:AN27 range; fill them in here to turn the MIDC
# coherence warning on (an empty list just skips that one check).
MIDC_POR_PODS = []

# Pricing node -> POR/PODs that normally go with it. A mismatch is only a
# warning: the macro asked "continue?" rather than refusing.
PRICING_NODE_POR_PODS = {
    "PALOVERDE": ["PALOVERDE500"],
    "MEAD230": ["MEAD230"],
    "MONA": ["MDWP"],
    "MIDC": MIDC_POR_PODS,
}

# Back office fields that are almost never moved off their defaults. They sit
# behind a guard in the Back Office section, and their values are held in
# session_state (not widget state) so collapsing the guard can't discard an
# edit.
RARE_FIELD_DEFAULTS = {
    "resupply_id": "",
    "secondary_por_pod": "",
    "resource_adequacy_id": "",
    "exchange_id": "",
    "is_option": False,
    "is_monthly": False,
}
RARE_FIELD_LABELS = {
    "resupply_id": "ResupplyID",
    "secondary_por_pod": "Secondary POR/POD",
    "resource_adequacy_id": "ResourceAdequacyID",
    "exchange_id": "ExchangeID",
    "is_option": "IsOption",
    "is_monthly": "IsMonthly",
}

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
# session_state — not just rendered inline on the click — so it survives the
# reruns caused by tweaking other fields, instead of vanishing immediately.
if "db_preview" not in st.session_state:
    st.session_state.db_preview = None
# Last broker string parsed, so a paste fires once rather than on every rerun.
if "last_parsed_string" not in st.session_state:
    st.session_state.last_parsed_string = None
if "parse_summary" not in st.session_state:
    st.session_state.parse_summary = None

HOURS = list(range(1, 25))
MAX_BLOCK_DATES = 31
DATE_EDITOR_ROW_HEIGHT = 20

SHAPE_ALIASES = {
    "HL": "on_peak",
    "ON-PEAK": "on_peak",
    "ONPEAK": "on_peak",
    "LL": "off_peak",
    "OFF-PEAK": "off_peak",
    "OFFPEAK": "off_peak",
    "ATC": "flat",
    "FLAT": "flat",
    "24H": "flat",
}
HOUR_RANGE_RE = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})$")
SINGLE_HOUR_RE = re.compile(r"^(\d{1,2})$")


def parse_shape(shape_str):
    """Parse a shape entry into (kind, start_he, end_he).

    kind is one of on_peak/off_peak/flat/custom. start_he/end_he are only
    set (and only meaningful) for custom. Raises ValueError with a
    trader-facing message on anything unrecognized.
    """
    s = (shape_str or "").strip().upper()
    if not s:
        raise ValueError("Enter a shape: HL, LL, ATC, or an hour range like 7-22.")
    if s in SHAPE_ALIASES:
        return SHAPE_ALIASES[s], None, None

    m = HOUR_RANGE_RE.match(s)
    if m:
        start_he, end_he = int(m.group(1)), int(m.group(2))
    else:
        m = SINGLE_HOUR_RE.match(s)
        if not m:
            raise ValueError(
                f"Could not parse shape '{shape_str}'. Use HL, LL, ATC, or an hour range like 7-22."
            )
        start_he = end_he = int(m.group(1))

    if not (1 <= start_he <= 24 and 1 <= end_he <= 24):
        raise ValueError(f"Hours in '{shape_str}' must be between 1 and 24.")
    if start_he > end_he:
        raise ValueError(f"In '{shape_str}', the start hour must be ≤ the end hour.")
    return "custom", start_he, end_he


def dates_in_range(start_date, end_date):
    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def block_grid_key(bid):
    return f"block_grid_{bid}"


def version_key(bid):
    return f"grid_ver_{bid}"


def get_version(bid):
    return st.session_state.get(version_key(bid), 0)


def as_date(x):
    """Normalize a Date-column cell back to a plain `date`. The data_editor
    round trip can hand back a pandas Timestamp for a DateColumn; downstream
    code keys everything on plain `date` objects."""
    return x.date() if hasattr(x, "date") else x


def make_block_grid(dates, mw_by_date=None):
    """One row per date, a leading Date column, then one column per HE
    holding the MW value. `mw_by_date`, if given, is {date: {he: mw}};
    missing dates or hours default to 0.0."""
    mw_by_date = mw_by_date or {}
    rows = []
    for d in dates:
        hours = mw_by_date.get(d, {})
        row = {"Date": d}
        row.update({str(h): float(hours.get(h, 0.0)) for h in HOURS})
        rows.append(row)
    return pd.DataFrame(rows)


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


def block_grid_to_date_frames(wide_df):
    """Expand a block's wide grid (Date + HE columns) into {date: long
    HE/MW DataFrame}, the shape the rest of the app works with."""
    result = {}
    for _, row in wide_df.iterrows():
        d = as_date(row["Date"])
        result[d] = pd.DataFrame({"HE": HOURS, "MW": [float(row[str(h)]) for h in HOURS]})
    return result


def schedule_to_wide(schedule):
    """Pivot a flat (date, he, mw) schedule into the same wide shape used
    while building the trade: one row per date, one column per HE."""
    by_date = {}
    for d, he, mw in schedule:
        by_date.setdefault(d, {})[he] = mw
    rows = []
    for d in sorted(by_date):
        row = {"Date": d}
        row.update({str(h): by_date[d].get(h, 0.0) for h in HOURS})
        rows.append(row)
    return pd.DataFrame(rows)


def build_schedule(start_date, end_date, kind, mw, custom_start_he=None, custom_end_he=None):
    """Determine which hours get `mw` for each date in [start_date, end_date],
    using the WECC calendar to decide which hours are on-/off-peak per day.

    kind is one of on_peak/off_peak/flat/custom, as returned by parse_shape.

    Returns (hours_by_date, excluded_dates, missing_calendar_dates).
    hours_by_date: {date: set of HE to set to mw}. Dates with no entry (or
    an empty set) get an all-zero MW day.
    excluded_dates: off-peak calendar days skipped by an on_peak leg.
    missing_calendar_dates: dates the WECC calendar had no row for at all.
    """
    peak_map = {}
    if kind in ("on_peak", "off_peak"):
        peak_map = is_peak_map(start_date, end_date)

    hours_by_date = {}
    excluded_dates = []
    missing_dates = []
    d = start_date
    while d <= end_date:
        if kind == "flat":
            hours = range(1, 25)
        elif kind == "custom":
            hours = range(int(custom_start_he), int(custom_end_he) + 1)
        else:
            is_peak = peak_map.get(d)
            if is_peak is None:
                missing_dates.append(d)
                d += timedelta(days=1)
                continue
            if kind == "on_peak":
                if is_peak:
                    hours = range(7, 23)
                else:
                    excluded_dates.append(d)
                    hours = []
            else:  # off_peak
                hours = range(1, 25) if not is_peak else list(range(1, 7)) + list(range(23, 25))
        hours_by_date[d] = set(hours)
        d += timedelta(days=1)

    return hours_by_date, excluded_dates, missing_dates


def format_price(index_name, price):
    if index_name:
        sign = "+" if price >= 0 else ""
        return f"{index_name}{sign}{price:.2f}"
    return f"{price:.2f}"


def default_index(options, default):
    """Position of `default` in `options`, or None if it isn't there.

    Returning None leaves the dropdown empty rather than silently selecting
    whatever happens to sit at index 0 — so dropping a default out of an
    option list shows up as a blank field instead of a wrong value.
    """
    try:
        return options.index(default)
    except ValueError:
        return None


def rare_fields_set(values):
    """Keys in `values` that sit off their default."""
    return [k for k, v in values.items() if v != RARE_FIELD_DEFAULTS[k]]


SHAPE_KIND_LABELS = {"on_peak": "HL", "off_peak": "LL", "flat": "ATC"}


def shape_label(shape_str):
    """The He label a shape stores as ('HL'/'LL'/'ATC'), or None for a
    custom hour range, which is written out as explicit hours instead."""
    try:
        kind, _, _ = parse_shape(shape_str)
    except ValueError:
        return None
    return SHAPE_KIND_LABELS.get(kind)


def generate_block_grid(start_date, end_date, block_dates, shape, mw):
    """Build a block's grid from its Shape/MW — the logic behind the
    Generate button, factored out so a brand-new block can be
    auto-populated with the exact same result on its first render.

    Returns (grid_df, excluded_dates, missing_dates, error). On a bad Shape
    or a WECC calendar failure, `error` is a trader-facing message and the
    other values are unusable.
    """
    try:
        kind, custom_start_he, custom_end_he = parse_shape(shape)
    except ValueError as e:
        return None, [], [], str(e)
    try:
        hours_by_date, excluded, missing = build_schedule(
            start_date, end_date, kind, mw, custom_start_he, custom_end_he
        )
    except Exception as e:
        return None, [], [], f"Could not load the WECC calendar: {e}"
    mw_by_date = {
        d: {he: mw for he in hours_by_date.get(d, set())} for d in block_dates
    }
    return make_block_grid(block_dates, mw_by_date), excluded, missing, None


def db_input_errors(trade, rows, past_dated, past_confirmed):
    """Checks that must pass before anything is written to the DB.

    These are the macro's validation pass, minus the rules that only
    existed to police hand-typed spreadsheet cells: the He format check
    (we build He ourselves), the 2x8/3x8 date-span rules (product
    shorthands this app never produces), and the PricingNode-vs-fixed-price
    pairing (implied here by whether Index is set).
    """
    errors = []

    if not trade["wspp_contract"]:
        errors.append("WSPP Contract Type is required. C is the default value.")

    if trade["location"] == "PALOVERDE":
        errors.append("PALOVERDE500 is the right POR/POD value, not PALOVERDE.")

    if past_dated and not past_confirmed:
        errors.append(
            "This trade has a trade date, start date, or stop date in the past. "
            "Tick 'Confirm past-dated trade' to continue."
        )

    for row in rows:
        if row["start_date"] < trade["trade_date"]:
            errors.append(
                f"Start date {row['start_date']} is before the trade date "
                f"{trade['trade_date']}."
            )
        if row["stop_date"] < row["start_date"]:
            errors.append(
                f"Stop date {row['stop_date']} is before start date {row['start_date']}."
            )

    return errors


def db_input_warnings(trade):
    """Non-blocking notes the macro raised as "continue?" prompts."""
    notes = []

    if trade["wspp_contract"] == "B":
        notes.append("WSPP Schedule B was used — C is the usual value.")

    expected = PRICING_NODE_POR_PODS.get(trade.get("index") or "")
    if expected and trade["location"] not in expected:
        notes.append(
            f"Pricing node {trade['index']} with POR/POD {trade['location']} "
            f"may not be coherent (expected {', '.join(expected)})."
        )

    return notes


def backoffice_summary(t):
    """One-line back office view: the always-shown fields, plus any
    rarely-used field that was moved off its default."""
    parts = [
        f"TZ: {t.get('time_zone') or '—'}",
        f"Comm: {t.get('communication') or '—'}",
        f"WSPP: {t.get('wspp_contract') or '—'}",
        f"Source: {t.get('specified_source') or '—'}",
        f"IsNWS: {'Y' if t.get('is_nws') else 'N'}",
        f"IsDAM: {'Y' if t.get('is_dam') else 'N'}",
        f"IsSourceNonCaiso: {'Y' if t.get('is_source_non_caiso') else 'N'}",
    ]
    for key, label in RARE_FIELD_LABELS.items():
        val = t.get(key, RARE_FIELD_DEFAULTS[key])
        if val != RARE_FIELD_DEFAULTS[key]:
            parts.append(f"{label}: {'Y' if val is True else val}")
    return " | ".join(parts)


def widget_defaults(key, **defaults):
    """Default kwargs for a widget, dropped once its key holds a value.

    Passing a default *and* having a session_state value for the same key
    logs a Streamlit policy warning, and the parser fills these keys
    directly.
    """
    return {} if key in st.session_state else defaults


def apply_parsed_string(text):
    """Parse a broker string and fill the form from it.

    Runs before any of the widgets it fills are created, so the values land
    in the same script run — no rerun needed. On any parse error nothing is
    filled at all: a half-populated form is worse than an obvious refusal.
    """
    parsed = parse_trade_string(
        text,
        counterparties=COUNTERPARTIES,
        locations=LOCATIONS,
        indexes=INDEXES,
        specified_sources=[s for s in SPECIFIED_SOURCES if s],
        full_names=load_market_full_names(),
        today=date.today(),
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
    st.session_state.trade_date = date.today()

    # Constant for pasted strings; the rest fall back to their defaults when
    # the string doesn't mention them.
    st.session_state.communication = PARSED_COMMUNICATION
    st.session_state.wspp_contract = parsed.get("wspp_contract", DEFAULT_WSPP_CONTRACT)
    st.session_state.specified_source = parsed.get(
        "specified_source", DEFAULT_SPECIFIED_SOURCE
    )
    st.session_state.is_nws = bool(parsed.get("is_nws", False))
    st.session_state.is_source_non_caiso = bool(parsed.get("is_source_non_caiso", False))

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
    st.session_state.parse_summary = summary


def render_paste_box():
    """Broker-string entry: paste, press Enter, the form fills in.

    Nothing is written to the database here — the filled form is still
    reviewed and submitted with Add Trade as usual.
    """
    text = st.text_input(
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

    summary = st.session_state.get("parse_summary")
    if not summary:
        return
    for message in summary.get("errors", []):
        st.error(message)
    for message in summary.get("warnings", []):
        st.warning(message)
    if summary.get("lines"):
        st.caption("Parsed → " + "  ·  ".join(summary["lines"]))


st.title("⚡ Add Trade")

for kind, text_ in st.session_state.flash:
    getattr(st, kind)(text_)
st.session_state.flash = []

render_paste_box()

is_sell = st.toggle(
    "Sell", key="is_sell", help="Off = Buy, On = Sell",
    **widget_defaults("is_sell", value=False),
)
direction = "Sell" if is_sell else "Buy"

c0, c1, c2, c3, c4 = st.columns(5)
trade_date = c0.date_input(
    "Trade Date", key="trade_date", help="Date the deal was struck.",
    **widget_defaults("trade_date", value=date.today()),
)
counterparty = c1.selectbox(
    "Counterparty",
    COUNTERPARTIES,
    placeholder="Select or type…",
    key="counterparty",
    accept_new_options=True,
    **widget_defaults("counterparty", index=None),
)
location = c2.selectbox(
    "Location (POR/POD)",
    LOCATIONS,
    placeholder="Select or type…",
    key="location",
    accept_new_options=True,
    **widget_defaults("location", index=None),
)
index_name = c3.selectbox(
    "Index",
    INDEXES,
    placeholder="None (fixed price)",
    key="index_name",
    accept_new_options=True,
    help="Leave blank for a flat fixed price.",
    **widget_defaults("index_name", index=None),
)
price = c4.number_input(
    "Price / Premium",
    step=0.01,
    key="price",
    help="Flat price if Index is blank. Premium to the index (e.g. +2 or -1) if Index is set.",
)

# Back office attributes. Always visible — these are set on every trade.
# Each carries an explicit key so the broker-string parser can fill it.
rare = st.session_state.rare_fields
off_default = rare_fields_set(rare)

b0, b1, b2, b3, b4, b5 = st.columns(6, vertical_alignment="bottom")
communication = b0.selectbox(
    "Communication",
    COMMUNICATION_METHODS,
    key="communication",
    accept_new_options=True,
    **widget_defaults(
        "communication",
        index=default_index(COMMUNICATION_METHODS, DEFAULT_COMMUNICATION),
    ),
)
wspp_contract = b1.selectbox(
    "WSPP Contract Type",
    WSPP_CONTRACT_TYPES,
    key="wspp_contract",
    accept_new_options=True,
    **widget_defaults(
        "wspp_contract",
        index=default_index(WSPP_CONTRACT_TYPES, DEFAULT_WSPP_CONTRACT),
    ),
)
specified_source = b2.selectbox(
    "Specified Source",
    SPECIFIED_SOURCES,
    key="specified_source",
    accept_new_options=True,
    **widget_defaults(
        "specified_source",
        index=default_index(SPECIFIED_SOURCES, DEFAULT_SPECIFIED_SOURCE),
    ),
)
is_dam = b3.checkbox(
    "IsDAM",
    key="is_dam",
    help="Writes DAM_RT as DAM when ticked, RT when not. Left NULL for a monthly trade. "
    "Also sets a new block's default Start Date: the next day (day-ahead) when ticked, "
    "the trade date (real-time) when not.",
    **widget_defaults("is_dam", value=True),
)
is_nws = b4.checkbox("IsNWS", key="is_nws", **widget_defaults("is_nws", value=False))
is_source_non_caiso = b5.checkbox(
    "IsSourceNonCaiso",
    key="is_source_non_caiso",
    **widget_defaults("is_source_non_caiso", value=False),
)

other_label = "Other attributes"
if off_default:
    other_label = f"Other attributes — {len(off_default)} field(s) set"
with st.expander(other_label, expanded=False):
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

def default_block_start(trade_date_val, is_dam_val):
    """A block's default Start Date: the next day for a DAM trade
    (day-ahead delivery always targets the next WECC calendar day), or the
    trade date itself for a real-time trade.
    """
    return trade_date_val + timedelta(days=1) if is_dam_val else trade_date_val


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


st.subheader("Schedule")

new_block_default_start = default_block_start(trade_date, is_dam)

block_grids = {}
block_ranges = []
block_shapes = {}
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
        # logs a Streamlit policy warning, so it's only passed for a widget
        # key that doesn't exist yet (a genuinely new block).
        sync_block_dates(bid, new_block_default_start)
        start_key, end_key = f"start_{bid}", f"end_{bid}"
        start_kwargs = (
            {} if start_key in st.session_state else {"value": new_block_default_start}
        )
        start_date = dcol1.date_input("Start Date", key=start_key, **start_kwargs)
        end_kwargs = {} if end_key in st.session_state else {"value": start_date}
        end_date = dcol2.date_input("End Date", key=end_key, **end_kwargs)
        block_ranges.append((start_date, end_date))
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
        generate_clicked = gcol3.button("Generate", key=f"generate_{bid}", width="stretch")
        clear_clicked = gcol4.button(
            "Clear schedule", key=f"clear_{bid}", type="primary", width="stretch"
        )
        remove_clicked = row[6].button("Remove block", key=f"remove_{bid}", width="stretch") if show_remove else False

        if remove_clicked:
            st.session_state.block_ids.remove(bid)
            st.session_state.pop(block_grid_key(bid), None)
            st.session_state.pop(version_key(bid), None)
            st.rerun()

        ver = get_version(bid)
        block_grids[bid] = {}

        if start_date > end_date:
            st.error("Start Date must be on or before End Date.")
        else:
            block_dates = dates_in_range(start_date, end_date)
            if len(block_dates) > MAX_BLOCK_DATES:
                st.error(
                    f"This block spans {len(block_dates)} dates. Split it into smaller "
                    f"blocks of {MAX_BLOCK_DATES} dates or fewer."
                )
            else:
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
                    # immediately with the same result Generate would give,
                    # so the common single-block default-shape trade never
                    # needs that click. Runs exactly once per block id —
                    # Generate/Clear/a direct edit all leave the grid
                    # present in session_state, so this never re-fires and
                    # so never overwrites anything the trader has touched.
                    # Silent on failure (falls back to an all-zero grid):
                    # this fires on every page load, and a WECC calendar
                    # hiccup here shouldn't block the page from rendering —
                    # clicking Generate explicitly still surfaces the error.
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

# The macro asked for confirmation in a dialog box; Streamlit has no blocking
# prompt inside a script run, so the confirmation is a checkbox that only
# appears when a date is actually in the past.
today = date.today()
past_dated = trade_date < today or any(
    s < today or e < today for s, e in block_ranges
)
past_confirmed = False
if input_in_db and past_dated:
    st.warning(
        "This trade has a trade date, start date, or stop date in the past."
    )
    past_confirmed = st.checkbox("Confirm past-dated trade", value=False)

if add_block_clicked:
    st.session_state.block_ids.append(st.session_state.next_block_id)
    st.session_state.next_block_id += 1
    st.rerun()

if add_trade_clicked or preview_clicked:
    errors = []
    if not counterparty:
        errors.append("Counterparty is required.")
    if not location:
        errors.append("Location is required.")

    schedule = []
    block_schedules = {}
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
                    errors.append(f"Duplicate schedule entry for {d} HE {he} — merge into one block.")
                    continue
                seen.add(key)
                schedule.append((d, int(he), float(mw_val)))
                block_schedules[bid].append((d, int(he), float(mw_val)))

    if not schedule:
        errors.append("Schedule needs at least one hour with MW > 0.")

    trade = {
        "trade_date": trade_date,
        "direction": direction,
        "counterparty": counterparty,
        "location": location,
        "index": index_name,
        "price": price,
        "schedule": schedule,
        "time_zone": TIME_ZONE,
        "communication": communication,
        "wspp_contract": wspp_contract,
        "specified_source": specified_source,
        "is_nws": is_nws,
        "is_dam": is_dam,
        "is_source_non_caiso": is_source_non_caiso,
        **rare,
    }

    # Everything the DB needs is checked before a single row is written, the
    # way the macro validated every sheet row before importing any of them.
    # Preview runs this same path — it just stops short of insert_trade() —
    # so what you see previewed is exactly what a real submit would attempt.
    run_db_path = input_in_db or preview_clicked
    db_rows = []
    if run_db_path and not errors:
        # Compressed per block, so each block's Shape can name its He the way
        # the existing rows do ('HL' rather than '7-22').
        for bid, block_schedule in block_schedules.items():
            db_rows += compress_schedule(
                block_schedule, shape_label(block_shapes.get(bid))
            )
        db_rows.sort(key=lambda r: (r["start_date"], r["he"]))
        # Preview bypasses the past-date confirmation gate: it's a "would
        # this block a real insert" note, not something previewing itself
        # should be blocked on — the checkbox that clears it only renders
        # when Input in DB is ticked (see above).
        errors += db_input_errors(
            trade, db_rows, past_dated, True if preview_clicked else past_confirmed
        )

    resolved_rows = []
    if run_db_path and not errors:
        try:
            resolved_rows, db_errors = resolve_and_validate(
                db_rows, dict(trade, price_text=format_db_price(index_name, price))
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
    elif preview_clicked:
        st.session_state.db_preview = {
            "trade": trade,
            "rows": resolved_rows,
            "past_dated": past_dated and not past_confirmed,
        }
        print(f"\n=== DB insert preview: {len(resolved_rows)} row(s) ===")
        for r in resolved_rows:
            print(r)
    else:
        db_trade_ids = []
        insert_failed = False
        if input_in_db:
            try:
                db_trade_ids = insert_trade(resolved_rows)
            except Exception as e:
                insert_failed = True
                st.error(f"Nothing was written — the insert failed: {e}")
            else:
                created, existing, failed = create_compliance_folders(
                    trade, db_trade_ids
                )
                for path in created:
                    st.session_state.flash.append(("info", f"Folder created: {path}"))
                for path in existing:
                    st.session_state.flash.append(
                        ("info", f"Folder already exists: {path}")
                    )
                for problem in failed:
                    st.session_state.flash.append(
                        ("warning", f"Trade saved, but a folder could not be created: {problem}")
                    )

        if not insert_failed:
            st.session_state.db_preview = None
            trade["db_trade_ids"] = db_trade_ids
            st.session_state.trades.append(trade)
            # Schedule blocks (dates, shape, MW, grid) are deliberately left
            # as-is — booking several trades against the same schedule is a
            # common desk workflow, and re-entering it every time isn't.
            # Trader-facing fields above the Schedule section persist too,
            # simply because nothing here clears their widget state either.
            if input_in_db:
                for note in db_input_warnings(trade):
                    st.session_state.flash.append(("warning", note))
            if db_trade_ids:
                st.session_state.flash.append((
                    "success",
                    f"Trade added and written to the DB as {len(db_trade_ids)} row(s): "
                    f"{', '.join(str(i) for i in db_trade_ids)}.",
                ))
            else:
                st.session_state.flash.append(("success", "Trade added."))
            st.rerun()

if st.session_state.db_preview:
    preview = st.session_state.db_preview
    with st.container(border=True):
        hcol, xcol = st.columns([9, 1])
        hcol.subheader("DB Insert Preview — nothing written yet")
        if xcol.button("✕", key="clear_preview", help="Dismiss this preview"):
            st.session_state.db_preview = None
            st.rerun()

        t = preview["trade"]
        st.caption(
            f"{t['direction']} — {t['counterparty']} @ {t['location']} — "
            f"would write {len(preview['rows'])} row(s) to "
            f"PhysiqueBilateral.west.BilateralTrades"
        )
        if preview["past_dated"]:
            st.warning(
                "This trade is past-dated. A real Add Trade would refuse it "
                "until 'Confirm past-dated trade' is ticked."
            )

        preview_df = pd.DataFrame(preview["rows"])[[
            "trade_date", "start_date", "stop_date", "he", "time_zone",
            "is_monthly", "is_buy", "market_id", "mw", "price", "pricing_node",
            "por_pod", "communication", "wspp", "is_source_non_caiso",
            "specified_source_id", "contract_type", "is_non_washington_sink",
            "resupply_id", "secondary_por_pod", "resource_adequacy_id",
            "exchange_id", "is_option", "dam_rt",
        ]]
        st.dataframe(preview_df, hide_index=True, width="stretch")
        for note in db_input_warnings(t):
            st.caption(f"⚠️ {note}")
        st.caption(
            "Also printed to the terminal running `streamlit run` for copy/paste."
        )

st.divider()

st.header("Trades")

if not st.session_state.trades:
    st.info("No trades yet.")
else:
    for i, t in enumerate(st.session_state.trades):
        total_mwh = sum(mw for _, _, mw in t["schedule"])
        dates = sorted({d for d, _, _ in t["schedule"]})
        with st.expander(
            f"{t['direction']} — {t['counterparty']} @ {t['location']} — "
            f"{total_mwh:,.0f} MWh across {len(dates)} date(s)"
        ):
            c1, c2 = st.columns([4, 1])
            with c1:
                wide_df = schedule_to_wide(t["schedule"])
                st.dataframe(
                    wide_df,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Date": st.column_config.DateColumn("Date", width=100),
                        **{
                            str(h): st.column_config.NumberColumn(str(h), width=45)
                            for h in HOURS
                        },
                    },
                )
                st.caption(
                    f"Trade date: {t.get('trade_date', '—')} | "
                    f"Price: {format_price(t.get('index'), t['price'])}"
                )
                st.caption(backoffice_summary(t))
                db_ids = t.get("db_trade_ids")
                st.caption(
                    f"DB: {', '.join(str(i) for i in db_ids)}"
                    if db_ids
                    else "DB: not written"
                )
            with c2:
                if st.button("Delete", key=f"del_trade_{i}"):
                    st.session_state.trades.pop(i)
                    st.rerun()
