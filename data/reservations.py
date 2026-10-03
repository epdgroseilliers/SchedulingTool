"""Looking one OASIS transmission reservation up by its assignment
reference.

The tag sheet's transmission block wants two things per row: a path string
like `WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/` and the reservation number
behind it. The desk's own OASIS summary already holds both —
`OATI_TransmissionSummary_Hourly.PathName` is character-for-character the
string the spreadsheets carry — so only the number needs typing and the path
follows from it.

**Read-only**, like everything touching `MAG.dbo.OATI_*`. The one statement
this module sends comes from `data/sql/oasis_reservation.sql`, and that file
is a SELECT.

A reference that isn't found is not an error. This table holds the
reservations **MAG itself** holds; a segment wheeled on a counterparty's
reservation — the ABEX leg of an ABEX-SWPW tag, say — is genuinely not in
it, and roughly three in ten of the references on the desk's own tags are of
that kind. The path is then left for the scheduler to fill, exactly as it is
today.
"""

from pathlib import Path

import streamlit as st
from sqlalchemy import text

from data.db import get_bilateral_engine

SQL_PATH = Path(__file__).parent / "sql" / "oasis_reservation.sql"

#: A day. A reservation's path never changes once granted, and the rest of
#: what's read here moves no faster.
CACHE_TTL_SECONDS = 60 * 60 * 24


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def reservation(aref):
    """What OASIS holds for this assignment reference, or None.

    None covers both "no such reservation of ours" and "the database
    couldn't be reached" on purpose: neither is something to stop a
    scheduler over, and in both cases the answer is the same — leave the
    path alone and let them type it.

    The caller decides what is worth looking up at all — see
    domain.tags.lookupable_aref.
    """
    aref = (aref or "").strip()
    if not aref:
        return None
    try:
        with get_bilateral_engine().connect() as conn:
            row = conn.execute(
                text(SQL_PATH.read_text()), {"aref": aref}
            ).mappings().first()
    except Exception:
        return None
    if row is None:
        return None
    return {
        "aref": row["AssignmentRef"],
        "path": row["PathName"],
        "provider": row["Provider"],
        "por": row["POR"],
        "pod": row["POD"],
        "status": row["Status"],
        "ts_class": row["TsClass"],
        "granted_mw": float(row["MaxGranted"]) if row["MaxGranted"] is not None else None,
        "first_date": row["FirstDate"],
        "last_date": row["LastDate"],
    }


def clear_cache():
    reservation.clear()
