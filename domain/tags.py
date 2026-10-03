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
        # No PSE on either half: the sheet has a cell for each, but both are
        # the ends of the market path — see source_pse/sink_pse — and asking
        # for the same code twice is how the two come to disagree.
        "source": {
            "market": "", "gca": "", "point": "", "comment": "", "contract": "",
        },
        "sink": {
            "market": "", "lca": "", "point": "", "comment": "", "contract": "",
        },
        "market_path": [],
        "transmissions": [],
        "carbon_copy": [],
        # The path as it stands in the scheduler chat — see
        # default_path_string. Free text on purpose: it is whatever the last
        # round of the back-and-forth produced, and nothing here reads it
        # back into the fields above yet.
        "path_string": "",
        # PPT hour-ending -> MW, straight off the link.
        "mw_by_hour": {},
    }


def default_market_path(buy_leg, sell_leg, pse_for=None):
    """The PSE chain a link implies: generator, MAG, load.

    A market end has no PSE of its own — MAG is the one standing at it — so
    linking a counterparty to a market gives a two-row path, and parking
    both ends in markets gives MAG at both ends, which is exactly what the
    desk's own SWPW-SWPP template holds.

    `pse_for` maps a market name to its tagging PSE code
    (`data.markets.pse_for_market`). A leg's `pse` field holds a
    `BilateralMarket.MarketName` — the desk's internal name, ABEX or EPE —
    and a tag's market path is written in PSE codes, EPEC01 rather than EPE.
    Passed the lookup, this writes the codes; without it, or for a market the
    lookup doesn't know, it falls back to the internal name so the row is
    still a legible starting point rather than an empty cell.
    """
    pse_for = pse_for or (lambda market: None)

    def code_for(leg):
        if leg.is_market:
            return MAG_PSE
        return pse_for(leg.pse) or leg.pse

    generator, load = code_for(buy_leg), code_for(sell_leg)
    middle = [] if MAG_PSE in (generator, load) else [MAG_PSE]
    chain = [generator] + middle + [load]

    rows = [{"pse": pse, "product": "", "contract": ""} for pse in chain]
    rows[0]["product"] = GENERATOR_PRODUCT
    rows[-1]["product"] = LOAD_PRODUCT
    return rows


#: What stands in for an end of the path nobody has named yet.
#:
#: The desk's chat strings are built outward from the middle. The two PSEs
#: either side of MAG are settled the moment the trade is done; everything
#: past them — who generates, who sinks, which wires carry it — is filled in
#: over the back-and-forth with the other schedulers. So the opening offer
#: is the part that is already certain, with a placeholder at each end.
PATH_UNKNOWN = "??"

#: How the chain marks the end MAG itself stands at — generator or sink.
#: Only reached when that end is a market, because that is the only way MAG
#: is the last party rather than one in the middle.
GENERATOR_MARK, SINK_MARK = "(g)", "(s)"


def default_path_string(buy_leg, sell_leg, pse_for=None):
    """The opening path string for this link: `??-ABEX-MAG001-BPAT-??`.

    The same chain as `default_market_path`, in the same PSE codes, written
    the way schedulers exchange it. A starting point rather than an answer:
    on a link MAG doesn't have to tag, this is what gets pasted into the
    chat and comes back a little longer each round.

    `??` stands for a party nobody has named yet — so an end MAG itself
    stands at doesn't get one. Selling into a market *is* MAG sinking the
    power, and the path stops there: `??-EPEC01-MAG001(s)`, not
    `??-EPEC01-MAG001-??`, which would claim somebody downstream is still
    to be found. Buying from one is the same fact the other way up, and is
    marked `(g)` by symmetry — the desk named only the sink case, so that
    one follows its wording and this one follows its logic.
    """
    chain = [row["pse"] for row in default_market_path(buy_leg, sell_leg, pse_for)]
    head = PATH_UNKNOWN if not getattr(buy_leg, "is_market", False) else None
    tail = PATH_UNKNOWN if not getattr(sell_leg, "is_market", False) else None
    if head is None and chain:
        chain[0] += GENERATOR_MARK
    if tail is None and chain:
        chain[-1] += SINK_MARK
    return "-".join([p for p in (head, *chain, tail) if p])


def default_tag(buy_leg, sell_leg, mw_by_hour, flow_date, pse_for=None):
    """The tag a link starts as: its schedule, its two ends' markets, and
    the market path those two ends imply. Everything else is blank, because
    nothing on the board knows it."""
    tag = blank_tag(flow_date)
    tag["name"] = f"{buy_leg.pse}-{sell_leg.pse}"
    tag["source"]["market"] = buy_leg.pse
    tag["sink"]["market"] = sell_leg.pse
    tag["market_path"] = default_market_path(buy_leg, sell_leg, pse_for)
    tag["path_string"] = default_path_string(buy_leg, sell_leg, pse_for)
    tag["mw_by_hour"] = {int(h): float(mw) for h, mw in mw_by_hour.items()}
    return tag


def source_pse(tag):
    """The sheet's "Source PSE" cell: the first PSE of the market path.

    Not typed anywhere — it *is* the head of the chain, and the desk's own
    files agree every time: the ABEX-SWPW tag's D10 is RRWE01, which is its
    market path's first row, not the ABEX in the Market cell above it.

    Blank when MAG stands at that end, matching the desk's own files again
    (the SWPW-SWPP template leaves both PSE cells empty and puts MAG at both
    ends of the path).
    """
    rows = market_path_rows(tag)
    return _pse_unless_mag(rows[0]["pse"]) if rows else ""


def sink_pse(tag):
    """The sheet's "Source PSE" cell on the sink half — the last PSE of the
    market path. See source_pse; the sheet reuses the label."""
    rows = market_path_rows(tag)
    return _pse_unless_mag(rows[-1]["pse"]) if rows else ""


def _pse_unless_mag(pse):
    return "" if pse == MAG_PSE else pse


#: The lengths an OASIS assignment reference actually has. Every one of the
#: 39,699 references in the desk's own OASIS summary is either seven or nine
#: digits — nine for the overwhelming majority, seven for the older ones.
#:
#: Widened from "nine digits" deliberately: seven-digit references are real,
#: they resolve, and ten of them appear on the desk's September tags. Change
#: this set to {9} for the strict reading.
AREF_LENGTHS = frozenset({7, 9})


#: The control area a market end tags under.
#:
#: Four of the five markets tag under their own trading name; CAISO is the
#: exception, and tags as CISO. Counted over a year of the desk's own West
#: tags: CISO appears as a control area 2,670 times, SWPW 2,492, SWPP 1,561,
#: AESO 724 and CEN 218 — "CAISO" never once.
#:
#: This matters because a market end is not a guess: if a link sells into
#: SWPW then MAG sinks the power there, and the tag's LCA *is* SWPW. A
#: market a future desk adds falls back to its own name, which is right four
#: times out of five.
MARKET_CONTROL_AREAS = {
    "CAISO": "CISO",
    "SWPW": "SWPW",
    "SWPP": "SWPP",
    "AESO": "AESO",
    "CEN": "CEN",
}


def control_area_for_market(market):
    """The GCA or LCA a link into this market implies. See
    MARKET_CONTROL_AREAS; None for anything that isn't a market."""
    name = (market or "").strip().upper()
    return MARKET_CONTROL_AREAS.get(name, name) or None


def reservation_covers(found, flow_date):
    """Whether an OASIS reservation is still good on this flow date.

    A reservation number is not a property of a route, it's a property of a
    *day*: 92% of the desk's own references appear on exactly one. So a
    number copied off a tag run last month is almost always dead, and
    copying it forward would be worse than leaving the cell empty — the
    scheduler would have to notice it was wrong rather than notice it was
    missing.

    Unknown dates answer False. Not knowing whether a reservation still
    covers the day is not the same as knowing that it does.
    """
    first, last = (found or {}).get("first_date"), (found or {}).get("last_date")
    if not first or not last or not flow_date:
        return False
    return _as_date(first) <= flow_date <= _as_date(last)


def _as_date(value):
    """A date, whether the driver handed back a date or a datetime."""
    return value.date() if hasattr(value, "date") else value


def lookupable_aref(text):
    """Whether this "# trans" value is worth asking OASIS about.

    The column takes anything a scheduler needs to write there, and plenty
    of what lands in it is not an assignment reference at all — `FCATBTEP`,
    `EPEPVEX`, `GF`, a bare contract number. Those are left entirely alone:
    no lookup, no path filled, no complaint. Only a plain run of digits of
    the right length is asked about.
    """
    value = (text or "").strip()
    return value.isdigit() and len(value) in AREF_LENGTHS


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
