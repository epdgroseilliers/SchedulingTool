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
    """A block's default Start Date: the next day for a DAM trade
    (day-ahead delivery always targets the next WECC calendar day), or the
    trade date itself for a real-time trade.
    """
    return trade_date + timedelta(days=1) if is_dam else trade_date


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
