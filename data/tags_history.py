"""Reading the desk's own e-Tag history out of OATI.

**Read-only, always.** `MAG.dbo.OATI_Tag` and its three child tables are the
live tagging record — the thing regulators and counterparties see. Every
statement this module sends comes from one of the four `data/sql/oati_*.sql`
files, there is no SQL built in Python here, and none of those files contains
anything but a SELECT. That is what makes the guarantee checkable by reading
four short files rather than by auditing a call graph.

The four tables sit at four grains — one row per tag, per market segment, per
physical segment, per transmission allocation — and assemble into one nested
record per tag:

    OATI_Tag     the header: GCA, LCA, the times
    OATI_TagMS   the market path: PSE chain with its energy products
    OATI_TagPS   the physical path: one G row, one L row, a T row per wire
    OATI_TagTA   the reservation behind each T row ("# trans" on the sheet)

Four queries rather than one join, deliberately. A `Tag -> MS -> PS -> TA`
join repeats the header on every (segment x allocation) row — for 11,000 tags
that is 66,000 copies of it on the wire instead of 11,000, so the "one query"
moves more bytes, not fewer. It would also need a LEFT JOIN at every hop or a
segment with no allocation vanishes silently, and it still has to be
un-flattened in Python afterwards. Each child query is restricted by the same
tag filter, so none of them scans its millions of rows.

Plain dicts rather than DataFrames: the result is nested per tag, so a frame
would be a waypoint costing a conversion each way, and it is the shape the
fake-row tests can write by hand.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import text

from data.db import get_bilateral_engine

SQL_DIR = Path(__file__).parent / "sql"
TAG_SQL = SQL_DIR / "oati_tag.sql"
MS_SQL = SQL_DIR / "oati_tagMS.sql"
PS_SQL = SQL_DIR / "oati_tagPS.sql"
TA_SQL = SQL_DIR / "oati_tagTA.sql"

#: The desk's clock. OATI stores tag times in UTC and the app works in PPT,
#: so a 24-hour PPT day reads as 07:00 to 07:00 — converting at this
#: boundary is what keeps domain/ free of time zones.
DESK_TZ = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

#: Six hours. History only grows at the tail, but today's tags are still
#: being amended, so a day-long cache would hide this morning's work.
CACHE_TTL_SECONDS = 60 * 60 * 6

#: Segment types in OATI_TagPS.
GENERATION, TRANSMISSION, LOAD = "G", "T", "L"


def _read(path):
    return path.read_text()


def _fetch(conn, path, params):
    """One SQL file's rows as plain dicts.

    The single place a statement is executed, which is what the read-only
    tests assert against — and the statement always comes from a file.
    """
    return [dict(row) for row in conn.execute(text(_read(path)), params).mappings()]


def _window(start_date, stop_date, all_actions):
    """The binds every one of the four files takes. `stop_date` is
    inclusive of the day, so the window runs to the start of the next one."""
    return {
        "start_time": datetime.combine(start_date, time.min),
        "stop_time": datetime.combine(stop_date + timedelta(days=1), time.min),
        "all_actions": 1 if all_actions else 0,
    }


def fetch_frames(start_date, stop_date, all_actions=False):
    """The four result sets, unassembled and uncached, keyed
    'tag'/'ms'/'ps'/'ta'.

    Separate from load_west_tags so the export script can bypass Streamlit's
    cache entirely rather than pickling a whole corpus nothing will read
    again.
    """
    params = _window(start_date, stop_date, all_actions)
    with get_bilateral_engine().connect() as conn:
        return {
            "tag": _fetch(conn, TAG_SQL, params),
            "ms": _fetch(conn, MS_SQL, params),
            "ps": _fetch(conn, PS_SQL, params),
            "ta": _fetch(conn, TA_SQL, params),
        }


def flow_date_of(start_time):
    """The PPT day a tag covers, from its UTC start.

    Not a subtraction: the offset is seven hours for half the year and eight
    for the other half, and a tag starting at 07:00 UTC is the first hour of
    a PPT day in one and the last hour of the previous one in the other.
    """
    if start_time is None:
        return None
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    return start_time.astimezone(DESK_TZ).date()


def _clean(value):
    """A value the way the rest of this module wants it: a stripped string
    for text, untouched for everything else. The SQL trims already; this
    catches a row typed by hand in a test."""
    return value.strip() if isinstance(value, str) else value


def assemble(tag_rows, ms_rows, ps_rows, ta_rows):
    """Nested records, one per tag, in the order the header query returned.

    Pure: no database, no Streamlit, no clock. This is the function the
    fake-row tests drive.

    Both child joins carry the tag index as well as their own key.
    `TagPSIndex` and `MSIndex` are unique only *within* one tag, so joining
    on either alone quietly attaches one tag's reservation to another's
    route — a bug that produces plausible output rather than an error.
    """
    reservations = {}
    for row in ta_rows:
        key = (row["TagIndex"], row["ParentSegmentRef"])
        reservations.setdefault(key, []).append(
            {
                "index": row.get("TagTAIndex"),
                "tp": _clean(row.get("TP", "")),
                "product": _clean(row.get("TransProduct", "")),
                "contract": _clean(row.get("ContractNumber", "")),
                "customer": _clean(row.get("TransCustCode", "")),
                "nits_resource": _clean(row.get("NITSResourceName", "")),
            }
        )

    market_path = {}
    for row in sorted(ms_rows, key=lambda r: (r["TagIndex"], r["TagMSIndex"])):
        market_path.setdefault(row["TagIndex"], []).append(
            {
                "index": row["TagMSIndex"],
                "pse": _clean(row.get("PSEcode", "")),
                "product": _clean(row.get("EnergyProduct", "")),
                "contracts": _clean(row.get("ContractNumberList", "")),
            }
        )

    physical_path = {}
    for row in sorted(
        ps_rows, key=lambda r: (r["TagIndex"], r["MSIndex"], r["TagPSIndex"])
    ):
        physical_path.setdefault(row["TagIndex"], []).append(
            {
                "index": row["TagPSIndex"],
                "ms_index": row["MSIndex"],
                "type": _clean(row.get("Type", "")),
                "pse": _clean(row.get("MSPSEcode", "")),
                "tp": _clean(row.get("TPcode", "")),
                "ca": _clean(row.get("CAcode", "")),
                "por_name": _clean(row.get("POR_Name", "")),
                "por_ca": _clean(row.get("POR_CAcode", "")),
                "pod_name": _clean(row.get("POD_Name", "")),
                "pod_ca": _clean(row.get("POD_CAcode", "")),
                "mo": _clean(row.get("MOcode", "")),
                "reservations": reservations.get(
                    (row["TagIndex"], row["TagPSIndex"]), []
                ),
            }
        )

    records = []
    for row in tag_rows:
        index = row["TagIndex"]
        records.append(
            {
                "tag_index": index,
                "tag_id": _clean(row.get("TagID", "")),
                "tag_code": _clean(row.get("TagCode", "")),
                "gca": _clean(row.get("GCA", "")),
                "lca": _clean(row.get("LCA", "")),
                "cpse": _clean(row.get("CPSE", "")),
                "start_time": row.get("StartTime"),
                "stop_time": row.get("StopTime"),
                "creation_time": row.get("CreationTime"),
                "flow_date": flow_date_of(row.get("StartTime")),
                "pse_comment": _clean(row.get("PseComment", "")),
                "last_action": _clean(row.get("LastAction", "")),
                "composite_state": row.get("TagCompositeState"),
                "market_path": market_path.get(index, []),
                "physical_path": physical_path.get(index, []),
            }
        )
    return records


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading tag history...")
def load_west_tags(start_date, stop_date, all_actions=False):
    """(records, error) for every West tag starting in [start_date, stop_date].

    Degrades the way data.matching.load_trades_for_flow_date does: an
    unreachable or slow OATI server gives back an empty history and a
    message, never an exception. Nothing here is on the critical path of
    entering or scheduling a trade, so failing quietly is right.
    """
    try:
        frames = fetch_frames(start_date, stop_date, all_actions)
    except Exception as e:
        return [], f"Could not read the tag history: {e}"
    return assemble(frames["tag"], frames["ms"], frames["ps"], frames["ta"]), None


def load_one(tag_index, around=None):
    """(record, error) for a single tag, looked up by TagIndex.

    Needs a date to search near, since every query is windowed — defaults to
    a fortnight either side of today, which covers anything still being
    worked on.
    """
    around = around or date.today()
    records, error = load_west_tags(
        around - timedelta(days=14), around + timedelta(days=14), all_actions=True
    )
    if error:
        return None, error
    for record in records:
        if record["tag_index"] == tag_index:
            return record, None
    return None, f"No West tag with TagIndex {tag_index} in that window."


def clear_cache():
    load_west_tags.clear()
