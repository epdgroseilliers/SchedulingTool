"""Trade-level business rules: formatting, defaults, and the validation the
legacy `InputTrades` macro ran before writing a row to the DB.
"""

from datetime import timedelta

from domain.options import PRICING_NODE_POR_PODS, RARE_FIELD_DEFAULTS, RARE_FIELD_LABELS


def format_price(index_name, price):
    if index_name:
        sign = "+" if price >= 0 else ""
        return f"{index_name}{sign}{price:.2f}"
    return f"{price:.2f}"


def rare_fields_set(values):
    """Keys in `values` that sit off their default."""
    return [k for k, v in values.items() if v != RARE_FIELD_DEFAULTS[k]]


def default_block_start(trade_date, is_dam):
    """A block's default Start Date when the WECC calendar has nothing to
    say: the next day for a DAM trade, or the trade date itself for a
    real-time one.

    The calendar normally decides — see default_flow_window, which only
    falls back to this when it can't be reached or has no session covering
    the trade date at all.
    """
    return trade_date + timedelta(days=1) if is_dam else trade_date


def default_flow_window(trade_date, is_dam, sessions):
    """(start, end) a fresh block defaults to, from the WECC calendar's own
    pairing of trading sessions to flow dates.

    `sessions` is {trade date: [flow date, ...]} — see
    data.calendar.sessions_near. One session covers one or more flow dates
    and the calendar says which, so the range is read from it rather than
    inferred: guessing it from runs of matching peak/off-peak days put a
    Monday trade's end date on the following Saturday.

    Three cases:

    - **A real-time trade** flows the day it's traded, and doesn't consult
      the calendar at all.
    - **A trading day** takes its session's flow dates, first to last.
    - **A day with no session** (a weekend, a holiday) has no day-ahead
      market to trade in at all, so whatever the IsDAM box says, the trade
      is real-time and flows that same day. `ui.trade_fields` unticks the
      box to match — see has_trading_session.

    An empty `sessions` is the one ambiguous case: the calendar couldn't be
    read, which says nothing about whether the market trades that day. That
    falls back to the plain next-day default rather than quietly turning a
    day-ahead trade into a real-time one.
    """
    if not is_dam:
        return trade_date, trade_date

    flows = sessions.get(trade_date)
    if flows:
        return min(flows), max(flows)

    if sessions:
        return trade_date, trade_date

    start = default_block_start(trade_date, is_dam)
    return start, start


def session_horizon(trade_date, sessions):
    """The furthest flow date the trading session for `trade_date` reaches.

    How far forward the app may act on its own — specifically, how far a
    link may be auto-propagated across a trade's range (see
    ui.scheduling.state.propagate_link). Beyond the current session's last
    flow date nothing has been traded yet, so a link invented out there is
    one the desk has no position behind and never asked for.

    A day the market doesn't trade has no session of its own, so the most
    recent session on or before it answers instead — that's the one whose
    flow dates are still being scheduled. On a Saturday, Friday's session
    is still the live one.

    Returns None when the calendar says nothing at all, which callers read
    as "don't cap" — a DB hiccup shouldn't quietly change what the app
    does, the same stance the rest of this module takes.
    """
    if not sessions:
        return None
    flows = sessions.get(trade_date)
    if flows:
        return max(flows)
    earlier = [day for day in sessions if day <= trade_date]
    if not earlier:
        return None
    return max(sessions[max(earlier)])


def has_trading_session(trade_date, sessions):
    """Whether the WECC calendar has a day-ahead session on this date — ie.
    whether a DAM trade can exist for it at all.

    An empty `sessions` means the calendar couldn't be read, not that
    nothing trades, so that answers True: the trader keeps the choice.
    """
    if not sessions:
        return True
    return bool(sessions.get(trade_date))


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
