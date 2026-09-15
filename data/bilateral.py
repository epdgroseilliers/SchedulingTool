"""Writing trades into [PhysiqueBilateral].[west].[BilateralTrades].

This replaces the `InputTrades` / `importTradeSQL` Excel macro. Table and
column names here are transcribed verbatim from that macro.

Two differences from the macro are deliberate:

- Lookups (MarketId, SpecifiedSourceId) run as their own SELECTs before the
  INSERT. The macro folded them into an `INSERT ... SELECT ... WHERE
  x.MarketName = '...'`, so an unknown counterparty matched zero rows and
  inserted nothing, silently. Here a failed lookup is a reported error.
- The new row's Id comes from an OUTPUT clause rather than a follow-up
  `SELECT MAX(Id)`, which returns another session's row under concurrent
  inserts. Id itself is left out of the INSERT: it carries a default backed
  by dbo.TradesIdSequence, so the server assigns it.

The OUTPUT goes `INTO` a table variable because BilateralTrades has enabled
AFTER triggers (TR_BilateralTrades_UpdateDatePhoto,
TR_BilateralTrades_InsertHistoric), and SQL Server rejects a bare OUTPUT
clause on a table with triggers (error 334).

Column types were read from the live schema, and shape a few conversions:
Mw, ResupplyId and ExchangeId are int; He is nvarchar(20); Price is
nvarchar and holds the desk's premium notation ('x+0.25', or a bare number
for a fixed price) rather than the index name.
"""

from datetime import timedelta
from pathlib import Path

import streamlit as st
from sqlalchemy import text

from data.db import get_bilateral_engine

TRADES_TABLE = "[PhysiqueBilateral].[west].[BilateralTrades]"
MARKET_TABLE = "[PhysiqueBilateral].[dbo].[BilateralMarket]"
SPECIFIED_SOURCES_TABLE = "[PhysiqueBilateral].[west].[REF_SpecifiedSources]"
MARKET_CONTRACT_TABLE = "[PhysiqueBilateral].[dbo].[REF_MarketContract]"

# Closed set the macro validated against — not a "fill these in" option list.
TIME_ZONES = [
    "EPT", "EDT", "EST",
    "CPT", "CDT", "CST",
    "MPT", "MDT", "MST",
    "PPT", "PDT", "PST",
]

# ContractType fallback when a counterparty has no REF_MarketContract row,
# matching the macro's `ELSE 'WSPP'`.
DEFAULT_CONTRACT_TYPE = "WSPP"

SPECIFIED_SOURCE_FOLDER = Path(
    r"\\FILECENTRAL\Physique\West\Compliance\Specified Source Contract"
)
NON_WASHINGTON_SINK_FOLDER = Path(
    r"\\FILECENTRAL\Physique\West\Compliance\Non Washington Sink"
)

# Columns only written when they hold a value, as in the macro's conditional
# header building. Maps the trade dict's key to its DB column.
OPTIONAL_TEXT_COLUMNS = {
    "resupply_id": "ResupplyId",
    "secondary_por_pod": "SecondaryPorPod",
    "resource_adequacy_id": "ResourceAdequacyId",
    "exchange_id": "ExchangeId",
}

# Of those, the two the live schema types as int rather than nvarchar.
INT_OPTIONAL_FIELDS = ("resupply_id", "exchange_id")

# He is nvarchar(20).
HE_MAX_LENGTH = 20

# 4,500-odd existing rows write standard shapes as a label ('HL', 'LL',
# 'ATC') and only fall back to explicit hour ranges for custom shapes, so
# match that. Hours alone can't tell LL on an off-peak day (a full 1-24)
# from ATC, so the label the trader picked in the block's Shape box breaks
# the tie — hence the per-shape list of hour sets each label may cover.
SHAPE_LABEL_HOURS = {
    "HL": [frozenset(range(7, 23))],
    "LL": [frozenset(list(range(1, 7)) + [23, 24]), frozenset(range(1, 25))],
    "ATC": [frozenset(range(1, 25))],
}


def hours_to_he_string(hours):
    """Collapse hour-endings into the macro's He format: contiguous runs
    become "start-end", isolated hours stay bare, and runs are comma
    joined — e.g. [1,2,3,4,5,6,23,24] -> "1-6,23-24"."""
    ordered = sorted(set(int(h) for h in hours))
    if not ordered:
        return ""
    parts = []
    run_start = run_end = ordered[0]
    for h in ordered[1:]:
        if h == run_end + 1:
            run_end = h
            continue
        parts.append(f"{run_start}-{run_end}" if run_start != run_end else f"{run_start}")
        run_start = run_end = h
    parts.append(f"{run_start}-{run_end}" if run_start != run_end else f"{run_start}")
    return ",".join(parts)


def he_for_hours(hours, shape_label=None):
    """The He value to store for a set of hours.

    Uses the block's shape label when the hours are exactly what that shape
    covers, so a plain HL leg is stored as 'HL' like the existing rows;
    anything hand-edited falls back to explicit ranges.
    """
    if shape_label:
        hour_set = frozenset(int(h) for h in hours)
        if hour_set in SHAPE_LABEL_HOURS.get(shape_label, []):
            return shape_label
    return hours_to_he_string(hours)


def compress_schedule(schedule, shape_label=None):
    """Fold a flat (date, he, mw) schedule into BilateralTrades rows.

    Each row is one He shape at one MW level over a contiguous date range,
    which is the granularity the legacy sheet used (one spreadsheet row per
    leg). Consecutive dates only merge when their whole shape matches, so
    hand-edits to a single day split the range instead of being flattened.

    `shape_label` is the block's shape ('HL'/'LL'/'ATC') when it maps to one,
    and only decides how He is spelled — never which hours are included.

    Returns a list of {"start_date", "stop_date", "he", "mw"} sorted by
    start date then first hour.
    """
    by_date = {}
    for d, he, mw in schedule:
        if mw and mw > 0:
            by_date.setdefault(d, {})[int(he)] = float(mw)

    # Per date, one line item per distinct MW level.
    items_by_date = {}
    for d, hours in by_date.items():
        by_mw = {}
        for he, mw in hours.items():
            by_mw.setdefault(mw, []).append(he)
        items_by_date[d] = sorted(
            (
                (he_for_hours(hes, shape_label), mw, min(hes))
                for mw, hes in by_mw.items()
            ),
            key=lambda item: (item[2], item[1]),
        )

    rows = []
    open_runs = {}  # signature -> {"start", "end", "items"}
    for d in sorted(items_by_date):
        items = items_by_date[d]
        signature = tuple((he, mw) for he, mw, _ in items)
        run = open_runs.get(signature)
        if run is not None and run["end"] + timedelta(days=1) == d:
            run["end"] = d
            continue
        if run is not None:
            rows.extend(_run_to_rows(run))
        open_runs[signature] = {"start": d, "end": d, "items": items}

    for run in open_runs.values():
        rows.extend(_run_to_rows(run))

    rows.sort(key=lambda r: (r["start_date"], r["first_he"]))
    for r in rows:
        del r["first_he"]
    return rows


def _run_to_rows(run):
    return [
        {
            "start_date": run["start"],
            "stop_date": run["end"],
            "he": he,
            "mw": mw,
            "first_he": first_he,
        }
        for he, mw, first_he in run["items"]
    ]


def format_db_price(index_name, price):
    """The Price column's notation, taken from the existing rows: 'x' plus a
    signed premium for an index trade ('x+0.25', 'x-1', bare 'x' at zero),
    and just the number for a fixed price. The index itself lives in
    PricingNode, not here.
    """
    if index_name:
        if not price:
            return "x"
        return f"x{price:+g}"
    return f"{price:g}"


def parse_optional_int(value, label):
    """(int_or_None, error_or_None) for an optional int-typed column."""
    text_value = str(value).strip() if value is not None else ""
    if not text_value:
        return None, None
    try:
        return int(text_value), None
    except ValueError:
        return None, f"{label} must be a whole number (the DB column is an int)."


def whole_mw(mw, label):
    """(int_or_None, error_or_None) for the int-typed Mw column."""
    if float(mw) != int(float(mw)):
        return None, f"{label} is {mw:g} MW; the DB stores whole MW only."
    return int(float(mw)), None


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def load_market_full_names():
    """{MarketName: MarketFullName} for every counterparty.

    Lets the broker-string parser resolve names that only appear in full,
    like "el paso" -> "El Paso Electric Company" -> EPE. Returns {} if the
    DB is unreachable: it only ever widens what the parser can match, so
    losing it degrades matching rather than breaking entry.
    """
    try:
        with get_bilateral_engine().connect() as conn:
            rows = conn.execute(
                text(f"SELECT MarketName, MarketFullName FROM {MARKET_TABLE}")
            ).fetchall()
        return {name: full for name, full in rows if name}
    except Exception:
        return {}


def lookup_market_id(conn, counterparty):
    """BilateralMarket.Id for a counterparty name, or None if absent."""
    sql = text(f"SELECT Id FROM {MARKET_TABLE} WHERE MarketName = :name")
    return conn.execute(sql, {"name": counterparty}).scalar()


def lookup_specified_source_id(conn, source_name):
    """REF_SpecifiedSources.Id for a source name, or None if absent."""
    sql = text(f"SELECT Id FROM {SPECIFIED_SOURCES_TABLE} WHERE SourceName = :name")
    return conn.execute(sql, {"name": source_name}).scalar()


def lookup_contract_type(conn, market_id):
    """Most recent ContractType on file for a counterparty, else 'WSPP' —
    the Python form of the macro's REF_MarketContract join and CASE."""
    sql = text(
        f"SELECT TOP 1 ContractType FROM {MARKET_CONTRACT_TABLE} "
        "WHERE MarketId = :market_id ORDER BY ContractDate DESC"
    )
    return conn.execute(sql, {"market_id": market_id}).scalar() or DEFAULT_CONTRACT_TYPE


def find_duplicate_id(conn, row):
    """Id of an existing row matching this one on its core terms, else None.

    Core terms are TradeDate, StartDate, StopDate, He, TimeZone, IsBuy,
    MarketId, PorPod, MW and price.
    """
    sql = text(
        f"SELECT TOP 1 Id FROM {TRADES_TABLE} WHERE "
        "TradeDate = :trade_date AND StartDate = :start_date "
        "AND StopDate = :stop_date AND He = :he AND TimeZone = :time_zone "
        "AND IsBuy = :is_buy AND MarketId = :market_id AND PorPod = :por_pod "
        "AND MW = :mw AND price = :price"
    )
    return conn.execute(
        sql,
        {
            "trade_date": row["trade_date"],
            "start_date": row["start_date"],
            "stop_date": row["stop_date"],
            "he": row["he"],
            "time_zone": row["time_zone"],
            "is_buy": row["is_buy"],
            "market_id": row["market_id"],
            "por_pod": row["por_pod"],
            "mw": row["mw"],
            "price": row["price"],
        },
    ).scalar()


def insert_trade_row(conn, row):
    """Insert one BilateralTrades row, returning its new Id."""
    columns = [
        "TradeDate", "StartDate", "StopDate", "He", "TimeZone", "IsMonthly",
        "IsBuy", "MarketId", "MW", "price", "PricingNode", "PorPod",
        "Communication", "WSPP", "datephoto", "IsSourceNonCaiso",
        "SpecifiedSourceId", "ContractType", "IsNonWashingtonSink",
    ]
    values = [
        ":trade_date", ":start_date", ":stop_date", ":he", ":time_zone",
        ":is_monthly", ":is_buy", ":market_id", ":mw", ":price",
        ":pricing_node", ":por_pod", ":communication", ":wspp", "GETDATE()",
        ":is_source_non_caiso", ":specified_source_id", ":contract_type",
        ":is_non_washington_sink",
    ]
    params = {
        "trade_date": row["trade_date"],
        "start_date": row["start_date"],
        "stop_date": row["stop_date"],
        "he": row["he"],
        "time_zone": row["time_zone"],
        "is_monthly": row["is_monthly"],
        "is_buy": row["is_buy"],
        "market_id": row["market_id"],
        "mw": row["mw"],
        "price": row["price"],
        "pricing_node": row["pricing_node"],
        "por_pod": row["por_pod"],
        "communication": row["communication"],
        "wspp": row["wspp"],
        "is_source_non_caiso": row["is_source_non_caiso"],
        "specified_source_id": row["specified_source_id"],
        "contract_type": row["contract_type"],
        "is_non_washington_sink": row["is_non_washington_sink"],
    }

    for key, column in OPTIONAL_TEXT_COLUMNS.items():
        if row.get(key):
            columns.append(column)
            values.append(f":{key}")
            params[key] = row[key]

    columns += ["isOption", "DAM_RT"]
    values += [":is_option", ":dam_rt"]
    params["is_option"] = row["is_option"]
    params["dam_rt"] = row["dam_rt"]

    # OUTPUT ... INTO (not a bare OUTPUT) because the table has enabled
    # triggers; SET NOCOUNT ON so the INSERT's row count doesn't hide the
    # SELECT's result from the driver.
    sql = text(
        "SET NOCOUNT ON; "
        "DECLARE @new TABLE (Id int); "
        f"INSERT INTO {TRADES_TABLE} ({', '.join(columns)}) "
        f"OUTPUT INSERTED.Id INTO @new VALUES ({', '.join(values)}); "
        "SELECT Id FROM @new;"
    )
    return conn.execute(sql, params).scalar()


def resolve_and_validate(rows, trade):
    """Resolve lookups and check for duplicates for every row of a trade.

    Runs read-only, before anything is written. Returns
    (resolved_rows, errors); `resolved_rows` is only complete when `errors`
    is empty.
    """
    errors = []
    resolved = []

    # Int-typed optional columns, converted once for the whole trade.
    optional_ints = {}
    for field in INT_OPTIONAL_FIELDS:
        parsed, error = parse_optional_int(
            trade.get(field), OPTIONAL_TEXT_COLUMNS[field]
        )
        if error:
            errors.append(error)
        optional_ints[field] = parsed

    for row in rows:
        if len(row["he"]) > HE_MAX_LENGTH:
            errors.append(
                f"The hour list for {row['start_date']} is '{row['he']}' "
                f"({len(row['he'])} chars); the He column holds "
                f"{HE_MAX_LENGTH}. Split that day into separate blocks."
            )
        _, mw_error = whole_mw(row["mw"], f"{row['start_date']} HE {row['he']}")
        if mw_error:
            errors.append(mw_error)

    if errors:
        return [], errors

    engine = get_bilateral_engine()
    with engine.connect() as conn:
        market_id = lookup_market_id(conn, trade["counterparty"])
        if market_id is None:
            errors.append(
                f"Counterparty '{trade['counterparty']}' has no row in "
                f"{MARKET_TABLE} (MarketName), so MarketId can't be resolved."
            )
            return [], errors

        contract_type = lookup_contract_type(conn, market_id)

        specified_source_id = None
        if trade.get("specified_source"):
            specified_source_id = lookup_specified_source_id(
                conn, trade["specified_source"]
            )
            if specified_source_id is None:
                errors.append(
                    f"Specified Source '{trade['specified_source']}' has no row in "
                    f"{SPECIFIED_SOURCES_TABLE} (SourceName)."
                )
                return [], errors

        for row in rows:
            full = dict(
                row,
                mw=int(float(row["mw"])),
                trade_date=trade["trade_date"],
                time_zone=trade["time_zone"],
                is_monthly=int(bool(trade["is_monthly"])),
                is_buy=int(trade["direction"] == "Buy"),
                market_id=market_id,
                price=trade["price_text"],
                pricing_node=trade.get("index") or "",
                por_pod=trade["location"],
                communication=trade.get("communication") or "",
                wspp=trade["wspp_contract"],
                is_source_non_caiso=int(bool(trade["is_source_non_caiso"])),
                specified_source_id=specified_source_id,
                contract_type=contract_type,
                is_non_washington_sink=int(bool(trade["is_nws"])),
                resupply_id=optional_ints["resupply_id"],
                secondary_por_pod=trade.get("secondary_por_pod") or "",
                resource_adequacy_id=trade.get("resource_adequacy_id") or "",
                exchange_id=optional_ints["exchange_id"],
                is_option=int(bool(trade["is_option"])),
                # The macro required DAM_RT to be empty for a monthly trade
                # and DAM or RT otherwise; derived here instead of validated.
                dam_rt=None if trade["is_monthly"] else ("DAM" if trade["is_dam"] else "RT"),
            )
            duplicate_id = find_duplicate_id(conn, full)
            if duplicate_id is not None:
                errors.append(
                    f"Already in the DB as Id {duplicate_id}: "
                    f"{full['start_date']}–{full['stop_date']} HE {full['he']} "
                    f"@ {full['mw']:g} MW."
                )
            resolved.append(full)

    return resolved, errors


def insert_trade(rows):
    """Insert all rows of one trade in a single transaction.

    Returns the new Ids. Either every row lands or none do — the macro
    executed each row on its own, so a mid-way failure left a partial trade.
    """
    engine = get_bilateral_engine()
    with engine.begin() as conn:
        return [insert_trade_row(conn, row) for row in rows]


def create_compliance_folders(trade, trade_ids):
    """Create the per-trade compliance folders the macro made: one under
    Specified Source Contract for a purchased specified source, and one
    under Non Washington Sink when IsNWS is set.

    Returns (created_paths, existing_paths, failed) — failures are reported
    rather than raised, since the row is already committed by this point.
    """
    targets = []
    if trade.get("specified_source") and trade["direction"] == "Buy":
        targets.append(SPECIFIED_SOURCE_FOLDER)
    if trade.get("is_nws"):
        targets.append(NON_WASHINGTON_SINK_FOLDER)

    created, existing, failed = [], [], []
    for base in targets:
        for trade_id in trade_ids:
            path = base / f"TRADE_ID {trade_id}"
            try:
                if path.is_dir():
                    existing.append(path)
                else:
                    path.mkdir(parents=True)
                    created.append(path)
            except OSError as e:
                failed.append(f"{path}: {e}")
    return created, existing, failed
