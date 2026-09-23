"""Reading [PhysiqueBilateral].[west].[BilateralTrades] back out, for the
Phase 2 Scheduling View.

`data/bilateral.py` is the write side of the same table; this is the read
side, kept separate because it answers a different question ("what is on the
book for this flow date?") and has no business importing the insert path.

Everything here is read-only, and a failed connection degrades to an empty
book with a message rather than an exception — the view still has the
session's own trades to show, and a trader losing the DB shouldn't lose the
page.
"""

import streamlit as st
from sqlalchemy import text

from data.bilateral import MARKET_TABLE, TRADES_TABLE
from data.db import get_bilateral_engine

# Short TTL rather than the day-long one the calendar uses: the book changes
# every time anyone on the desk enters a trade, and a stale board is worse
# than a slow one. The view's Refresh button clears this outright.
CACHE_TTL_SECONDS = 60

FLOW_DATE_SQL = text(
    "SELECT t.Id, t.TradeDate, t.StartDate, t.StopDate, t.He, t.MW, t.IsBuy, "
    "t.price, t.PricingNode, t.PorPod, t.IsMonthly, t.DAM_RT, m.MarketName "
    f"FROM {TRADES_TABLE} t "
    f"LEFT JOIN {MARKET_TABLE} m ON m.Id = t.MarketId "
    "WHERE t.StartDate <= :flow_date AND t.StopDate >= :flow_date "
    "ORDER BY m.MarketName, t.PorPod, t.Id"
)


def _as_date(value):
    """Plain `date` for a DB date column.

    The driver hands these back as `datetime`, and everything downstream
    keys on and compares plain dates — a datetime/date comparison raises
    TypeError rather than quietly doing the wrong thing, so this has to
    happen at the boundary.
    """
    return value.date() if hasattr(value, "date") else value


def _as_row(record):
    """One DB record in the shape domain.matching.leg_from_row() expects."""
    return {
        "trade_id": record.Id,
        "trade_date": _as_date(record.TradeDate),
        "start_date": _as_date(record.StartDate),
        "stop_date": _as_date(record.StopDate),
        "he": record.He,
        "mw": float(record.MW or 0),
        "direction": "Buy" if record.IsBuy else "Sell",
        "pse": record.MarketName,
        "por_pod": record.PorPod,
        "price": record.price,
        "index": record.PricingNode,
        "is_monthly": bool(record.IsMonthly),
        "dam_rt": record.DAM_RT,
    }


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading the book...")
def load_trades_for_flow_date(flow_date):
    """(rows, error) for every trade whose date range covers `flow_date`.

    Date ranges are filtered in SQL; which *hours* each row flows on that
    date is domain.matching's job, since it depends on the WECC calendar
    rather than on anything the query can see.
    """
    try:
        with get_bilateral_engine().connect() as conn:
            records = conn.execute(FLOW_DATE_SQL, {"flow_date": flow_date}).fetchall()
    except Exception as e:
        return [], f"Could not read the trades database: {e}"
    return [_as_row(r) for r in records], None


def clear_cache():
    """Drop the cached book so the next render re-queries — behind the
    view's Refresh button, for when a trade was just entered elsewhere."""
    load_trades_for_flow_date.clear()
