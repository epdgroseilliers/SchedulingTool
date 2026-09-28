"""Phase 3 — the bilateral e-Tag a confirmed link turns into.

Pure: no Streamlit, no openpyxl, no filesystem. `data/tagfiles/bilateral.py`
writes the spreadsheet, `ui/scheduling/tag.py` collects the fields; this
module owns what a tag *is*, what a link implies about it, and what has to
be filled in before one can be written.

A tag is a plain nested dict rather than a dataclass, because it lives in
`st.session_state` and is edited field by field by widgets that address it
by name. `blank_tag()` is the only definition of its shape — read it first.

The schedule is not part of what the trader types: it comes from the link,
which is the whole reason the tag is raised from the Scheduling View rather
than from a blank workbook.
"""

import re

#: MAG's own PSE code, which appears in every market path in the desk's own
#: files — as the generator when MAG sources from a market, as the load when
#: it sinks into one, and in the middle when both ends are counterparties.
MAG_PSE = "MAG001"

#: Products seen in the desk's 2026 tag files, most common first. The
#: generator end of a market path carries a G product and the load end
#: carries L; a wheel-through in the middle carries none.
PRODUCTS = ["", "G-F", "G-EX", "G-NF", "G-FP", "L"]
GENERATOR_PRODUCT = "G-F"
LOAD_PRODUCT = "L"

#: Carbon-copy row types. Only "BA" appears in the recent files, but the
#: column is free text in the sheet and this list is only what's offered.
CC_TYPES = ["", "BA", "PSE", "TP"]

#: How many rows each block of the sheet has before it runs into the next
#: one — see data/tagfiles/bilateral.py, which owns the row numbers.
MAX_TRANSMISSIONS = 13
MAX_MARKET_PATH = 10
MAX_CARBON_COPY = 8

#: Characters Windows won't take in a file name.
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

#: "25-26 September 2026" and "1 September 2026" — how the desk names the
#: day folders under the tag root. A trailing " - Copy" is ignored.
_FOLDER = re.compile(
    r"^\s*(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s+([A-Za-z]+)\s+(\d{4})\s*(?:-\s*Copy)?\s*$"
)


def blank_tag(flow_date=None):
    """Every field a tag carries, empty. The shape everything else assumes.

    `source`/`sink` mirror the two halves of the sheet: the generation side
    on top with its GCA, the load side below with its LCA. `point` is the
    sheet's "Source"/"Sink" cell — a named plant or an interface, not a
    market.
    """
    return {
        "flow_date": flow_date,
        # The sheet's own header line, e.g. "SOURCE-SWPW(WAUW)". Free text
        # in every real file, so free text here.
        "label": "SOURCE-SINK",
        # What the file is named after, following the counterparties by
        # default: "ABEX-SWPW" in "… - September 25 2026 - ABEX-SWPW.xlsx".
        "name": "",
        "source": {
            "market": "", "gca": "", "point": "", "pse": "",
            "comment": "", "contract": "",
        },
        "sink": {
            "market": "", "lca": "", "point": "", "pse": "",
            "comment": "", "contract": "",
        },
        "market_path": [],
        "transmissions": [],
        "carbon_copy": [],
        # PPT hour-ending -> MW, straight off the link.
        "mw_by_hour": {},
    }


def default_market_path(buy_leg, sell_leg):
    """The PSE chain a link implies: generator, MAG, load.

    A market end has no PSE of its own — MAG is the one standing at it — so
    linking a counterparty to a market gives a two-row path, and parking
    both ends in markets gives MAG at both ends, which is exactly what the
    desk's own SWPW-SWPP template holds.

    The codes a counterparty end starts with are its *trading* name (ABEX,
    EPE), not always its PSE code (RRWE01, EPEC01) — the app has no lookup
    from one to the other, so this is a starting point the trader corrects,
    not an answer.
    """
    generator = MAG_PSE if buy_leg.is_market else buy_leg.pse
    load = MAG_PSE if sell_leg.is_market else sell_leg.pse
    middle = [] if MAG_PSE in (generator, load) else [MAG_PSE]
    chain = [generator] + middle + [load]

    rows = [{"pse": pse, "product": "", "contract": ""} for pse in chain]
    rows[0]["product"] = GENERATOR_PRODUCT
    rows[-1]["product"] = LOAD_PRODUCT
    return rows


def default_tag(buy_leg, sell_leg, mw_by_hour, flow_date):
    """The tag a link starts as: its schedule, its two ends' markets, and
    the market path those two ends imply. Everything else is blank, because
    nothing on the board knows it."""
    tag = blank_tag(flow_date)
    tag["name"] = f"{buy_leg.pse}-{sell_leg.pse}"
    tag["source"]["market"] = buy_leg.pse
    tag["sink"]["market"] = sell_leg.pse
    tag["market_path"] = default_market_path(buy_leg, sell_leg)
    tag["mw_by_hour"] = {int(h): float(mw) for h, mw in mw_by_hour.items()}
    return tag


def clean_rows(rows, fields):
    """Drop rows where every field is blank, and normalize the rest to
    strings — what a dynamic data editor hands back is full of None and
    NaN, and a row the trader added and then emptied is not a row."""
    clean = []
    for row in rows or []:
        values = {}
        for field in fields:
            value = row.get(field)
            # NaN is the one value that isn't equal to itself — pandas puts
            # it in every cell of a row added and left alone.
            if value is None or value != value:
                value = ""
            values[field] = str(value).strip()
        if any(values.values()):
            clean.append(values)
    return clean


def market_path_rows(tag):
    return clean_rows(tag.get("market_path"), ("pse", "product", "contract"))


def transmission_rows(tag):
    return clean_rows(tag.get("transmissions"), ("path", "reservation", "mw"))


def carbon_copy_rows(tag):
    return clean_rows(tag.get("carbon_copy"), ("type", "market"))


def tag_errors(tag):
    """Everything that stops this tag being written, in the order a trader
    would fix it. Empty means it can be generated.

    Deliberately short: the GCA and LCA, because a tag without them names no
    control areas at all, and a market path with both ends on it, because a
    one-sided path isn't a path. Every other cell in the sheet is legitimately
    blank in the desk's own files, so nothing else is required here — the
    check that the numbers are right is the scheduler's, not this app's.
    """
    errors = []
    if not tag.get("mw_by_hour"):
        errors.append("This link has no hours — there is nothing to tag.")
    if not tag["source"]["gca"].strip():
        errors.append("GCA is required.")
    if not tag["sink"]["lca"].strip():
        errors.append("LCA is required.")

    path = market_path_rows(tag)
    if len([row for row in path if row["pse"]]) < 2:
        errors.append(
            "The market path needs at least two PSEs — who generates and "
            "who takes it."
        )
    elif any(not row["pse"] for row in path):
        errors.append("Every market path row needs a PSE.")

    for rows, limit, what in (
        (transmission_rows(tag), MAX_TRANSMISSIONS, "transmission"),
        (path, MAX_MARKET_PATH, "market path"),
        (carbon_copy_rows(tag), MAX_CARBON_COPY, "carbon copy"),
    ):
        if len(rows) > limit:
            errors.append(
                f"The sheet has room for {limit} {what} rows; this tag has "
                f"{len(rows)}."
            )
    return errors


def safe_name(text):
    """A file-name fragment Windows will take, with runs of spaces
    collapsed. Never empty — an unnamed tag is still a file."""
    cleaned = _ILLEGAL.sub("-", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Tag"


def tag_filename(flow_date, name):
    """'Tag_dynamique_bilateral - September 25 2026 - ABEX-SWPW.xlsx' —
    full month name, day with no leading zero, matching every real file
    under the desk's tag folder."""
    return (
        f"Tag_dynamique_bilateral - {_MONTHS[flow_date.month - 1]} "
        f"{flow_date.day} {flow_date.year} - {safe_name(name)}.xlsx"
    )


def day_folder_name(flow_date):
    """'25 September 2026' — what to call a day folder that doesn't exist
    yet. The desk also uses two-day names ("25-26 September 2026") for a
    weekend or a holiday, but those are a judgement about which days get
    traded together, so one is never *created* here — only matched."""
    return f"{flow_date.day} {_MONTHS[flow_date.month - 1]} {flow_date.year}"


def folder_covers(name, flow_date):
    """Whether a day folder's name covers this flow date.

    Handles "1 September 2026", "25-26 September 2026" and the " - Copy"
    Windows leaves behind. Anything else ("Archives", "Templates") answers
    False, which is what keeps a tag out of them.
    """
    match = _FOLDER.match(name or "")
    if not match:
        return False
    first, last, month, year = match.groups()
    if month.lower() != _MONTHS[flow_date.month - 1].lower():
        return False
    if int(year) != flow_date.year:
        return False
    return int(first) <= flow_date.day <= int(last or first)
