"""Market name -> tagging PSE code.

`BilateralMarket.MarketName` is the desk's internal name for a counterparty
(AZPS, EPE, ABEX) — the one the back office keys on, and the one a trade row
already carries, so a leg's `pse` field *is* a MarketName. A tag's market
path, though, is written in PSE codes (APS01, EPEC01, ABEX), and
`BilateralMarketPseMapping` is the desk's own map between the two. Nothing
should ever ask a trader to type that mapping out.

Sixteen of the eighty markets map to more than one code — MSCG tags as both
RRWE01 and MSCG01, MAG as MAG001 and MMA — so "the" code for a market is a
choice, and the one this desk actually uses is the one its own tag history
uses most. That ranking is what `pse_usage()` is for; without it the mapping
table's own order stands, which for MAG would give MMA rather than MAG001.

Every query here is read-only, and the two that touch `MAG.dbo.OATI_*` are
doubly so — those tables are the live tagging record.
"""

from datetime import date, timedelta
from pathlib import Path

import streamlit as st
from sqlalchemy import text

from data.db import get_bilateral_engine

MAP_SQL_PATH = Path(__file__).parent / "sql" / "market_pse_map.sql"
USAGE_SQL_PATH = Path(__file__).parent / "sql" / "pse_usage.sql"

#: A day. Both of these change on the timescale of the desk signing up a new
#: counterparty, so a stale read costs nothing and a per-render query would
#: cost a round trip on every keystroke in the tag form.
CACHE_TTL_SECONDS = 60 * 60 * 24

#: How far back to count PSE usage. A year covers the seasonal counterparties
#: without letting a code the desk stopped using three years ago outrank one
#: it uses now.
USAGE_WINDOW_DAYS = 365


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def pse_usage():
    """{PSECode: times used} across our own West tags' market paths.

    Returns {} if the tagging database can't be read. That only costs the
    ranking below its best evidence — the mapping table still answers which
    codes exist — so it degrades quietly rather than reporting an error.
    """
    since = date.today() - timedelta(days=USAGE_WINDOW_DAYS)
    try:
        with get_bilateral_engine().connect() as conn:
            rows = conn.execute(
                text(USAGE_SQL_PATH.read_text()), {"start_time": since}
            ).fetchall()
    except Exception:
        return {}
    return {str(code).strip(): int(uses) for code, uses in rows if code}


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def market_pse_map():
    """{MarketName: [PSECode, ...]}, each market's codes best first.

    "Best" is most-used on our own tags, falling back to the mapping table's
    own order for codes the history has never seen. Returns {} if the
    mapping can't be read, which leaves the tag builder defaulting to
    counterparty names as it did before this lookup existed — worse, but
    still usable.
    """
    try:
        with get_bilateral_engine().connect() as conn:
            rows = conn.execute(text(MAP_SQL_PATH.read_text())).fetchall()
    except Exception:
        return {}

    usage = pse_usage()
    by_market = {}
    for market, _full_name, code, mapping_id in rows:
        market, code = str(market).strip(), str(code).strip()
        if market and code:
            by_market.setdefault(market, []).append((code, int(mapping_id)))

    return {
        market: [
            code
            for code, _ in sorted(
                codes, key=lambda pair: (-usage.get(pair[0], 0), pair[1])
            )
        ]
        for market, codes in by_market.items()
    }


def pse_for_market(market):
    """The PSE code this market tags under, or None if it has none.

    None is not a failure: a market square (SWPW, SWPP) is a place, not a
    counterparty, and has no PSE of its own — MAG is the one standing there.
    """
    if not market:
        return None
    codes = market_pse_map().get(str(market).strip())
    return codes[0] if codes else None


def known_pse_codes():
    """Every PSE code the tag builder should offer, sorted.

    The union of two sources on purpose. The mapping table alone covers only
    87% of the codes our own tags actually use — PPLMS1, EPLUW and CITI01 are
    all real and all absent from it — so offering the mapping alone would
    refuse codes the desk writes every week. The history alone would miss a
    counterparty newly signed up and not yet tagged.
    """
    codes = {code for codes in market_pse_map().values() for code in codes}
    codes |= set(pse_usage())
    return sorted(codes)


def clear_cache():
    """Drop both cached lookups — for a Refresh button, or after the desk
    adds a counterparty mid-session."""
    market_pse_map.clear()
    pse_usage.clear()
