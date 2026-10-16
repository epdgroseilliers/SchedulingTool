"""Writing a bilateral e-Tag workbook.

Unlike the SWPW bid file — whose column layout the desk has evolved by hand,
and which is therefore built from scratch — every one of the 2,638 real tag
files under `TAG_FOLDER` puts the same label in the same cell. The row
numbers below were read off that whole set, not off one example: the
Market/GCA/Source block, the MW columns, the Path block and the Market Path
Product block are identical in all of them, and the Carbon Copy block is
identical wherever it appears at all (it is absent from the older ones).

So this fills a **blank copy of the desk's own template** rather than
recreating it. `bilateral_tag_template.xlsx` beside this module is
`Templates/Tag_dynamique_bilateral - Template - SWPW-SWPP.xlsx` with every
input cell emptied and nothing else touched — its labels, fills, borders,
merges, column widths, zoom and the two cell comments on the `1*`/`2*` rows
all come along. Regenerate it the same way if the desk's template changes.

**The sheet's hour column is EPT; the app is PPT**, exactly as in the SWPW
bid file, and for the same reason the same three `1*`/`2*`/`3*` rows sit
under HE24 in both — see `domain.bidfiles.ppt_to_ept`. Confirmed against the
real files twice over: "… TEPC HE7-14" fills rows 23-30, which is EPT
HE10-17, and a full-day position fills EPT HE4-24 plus the three stars.
"""

from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from data.calendar import sessions_near
from domain.bidfiles import ppt_to_ept
from domain.tags import (
    carbon_copy_rows,
    day_folder_name,
    folder_covers,
    market_path_rows,
    sink_pse,
    source_pse,
    tag_filename,
    transmission_rows,
)
from domain.trade import session_days

TAG_FOLDER = Path(r"Y:\West\TAG Bilateral WEST")

#: Send every generated tag to the desk's own "Test" folder instead of the
#: session's real one, so nothing lands where a scheduler would pick it up.
#:
#: **Off: tags go to the real folder.** It was on while the feature was
#: under review and is off now that the desk wants the files where it
#: files them. Turning it back on is a one-line change and nothing else
#: about the file differs — but note that it flattens every tag into one
#: folder, so it is the one setting under which the session-folder rule
#: below can't be seen working.
TEST_MODE = False
TEST_SUBFOLDER = "Test"

TEMPLATE = Path(__file__).with_name("bilateral_tag_template.xlsx")
SHEET = "BIDS"

#: The flow date, which every tag in the file shares — the one cell that
#: isn't per-tag.
DATE_CELL = "C5"

#: Every other cell a tag fills is in **its own column group**, so these
#: are rows: the column comes from which tag it is (see GROUP_WIDTH).
LABEL_ROW = 5

#: The rows of each half the trader fills, in the order the sheet lists
#: them. The source half's second row is the GCA and the sink half's is the
#: LCA — the only difference between the two blocks.
SOURCE_ROWS = {
    "market": 7, "gca": 8, "point": 9, "comment": 11, "contract": 12,
}
SINK_ROWS = {
    "market": 44, "lca": 45, "point": 46, "comment": 48, "contract": 49,
}

#: The two PSE rows, which nobody types: they are the ends of the market
#: path — see domain.tags.source_pse.
SOURCE_PSE_ROW, SINK_PSE_ROW = 10, 47

#: Each MW block: the row EPT HE1 sits on, then the three rows holding HE1-3
#: of the *next* EPT day (the last three hours of the PPT day).
SOURCE_HOURS_ROW, SOURCE_STAR_ROWS = 14, (38, 39, 40)
SINK_HOURS_ROW, SINK_STAR_ROWS = 51, (75, 76, 77)

MW_COL, PRICE_COL = 4, 5  # D, E — the first tag's group

#: One tag is three columns wide, and a file holding several lays them out
#: side by side: D/E/F, G/H/I, J/K/L, and so on. Read off the desk's own
#: split file, Archives/May 2026/23-24 May 2026/"... EPE - SWPW(CRSP)
#: SPLIT.xlsx", which carries five tags that way — same rows, same labels
#: down column C, one group of three per tag.
GROUP_WIDTH = 3

#: The last row any group reaches (the carbon copy block's end), for
#: copying a group's shell across.
LAST_ROW = 112

#: The three list blocks, each running from its first row up to the header
#: of the next block. Their lengths are what domain.tags checks against.
TRANSMISSION_ROW, TRANSMISSION_FIELDS = 80, ("path", "reservation", "mw")
MARKET_PATH_ROW, MARKET_PATH_FIELDS = 94, ("pse", "product", "contract")
CARBON_COPY_ROW, CARBON_COPY_FIELDS = 105, ("type", "market")


def hour_row(base_row, star_rows, he_ppt):
    """The row a PPT hour-ending lands on in a block starting at
    `base_row` — see the module docstring on EPT."""
    he_ept, next_day = ppt_to_ept(he_ppt)
    if next_day:
        return star_rows[he_ept - 1]
    return base_row + he_ept - 1


def _as_number(text):
    """An OASIS reservation number written back as the integer it is in
    every real file, or left as typed when it isn't one (a contract
    reference like 'FCATBTEP' goes in the same column)."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return text


def group_col(index):
    """The first column of the index-th tag's group: D, G, J, ..."""
    return MW_COL + GROUP_WIDTH * index


def _add_group(ws, index):
    """Give the index-th tag its own three columns, copied off the first
    tag's.

    The bundled template is the desk's single-tag one, so a second tag has
    no headers, borders or widths of its own until they are put there. They
    are copied rather than written out: every label and every bit of
    formatting then comes from the same place it always did, and a change
    to the template reaches the extra groups for free.

    Called on the pristine template, before any tag is written, so what
    gets copied is the empty shell and never another tag's values.
    """
    offset = GROUP_WIDTH * index
    for row in range(1, LAST_ROW + 1):
        for column in range(MW_COL, MW_COL + GROUP_WIDTH):
            source, target = ws.cell(row, column), ws.cell(row, column + offset)
            target.value = source.value
            target._style = copy(source._style)
    # After the values, never before: merging turns the other two cells of
    # a row into MergedCells, which raise on assignment.
    for merged in list(ws.merged_cells.ranges):
        if merged.min_col == MW_COL and merged.max_col == MW_COL + GROUP_WIDTH - 1:
            ws.merge_cells(
                start_row=merged.min_row, end_row=merged.max_row,
                start_column=MW_COL + offset,
                end_column=MW_COL + GROUP_WIDTH - 1 + offset,
            )
    for column in range(MW_COL, MW_COL + GROUP_WIDTH):
        width = ws.column_dimensions[get_column_letter(column)].width
        if width:
            ws.column_dimensions[get_column_letter(column + offset)].width = width


def _write_block(ws, first_row, rows, fields, first_col, converters=None):
    converters = converters or {}
    for offset, row in enumerate(rows):
        for column, field in enumerate(fields, start=first_col):
            value = row.get(field, "")
            if not value:
                continue
            convert = converters.get(field)
            ws.cell(first_row + offset, column).value = (
                convert(value) if convert else value
            )


def _write_hours(ws, base_row, star_rows, mw_by_hour, first_col):
    for he_ppt, mw in sorted(mw_by_hour.items()):
        if not mw:
            continue
        ws.cell(
            hour_row(base_row, star_rows, int(he_ppt)), first_col
        ).value = float(mw)


def _write_tag(ws, tag, index):
    """One tag into its own column group."""
    first_col = group_col(index)
    ws.cell(LABEL_ROW, first_col).value = tag.get("label", "")

    for half, rows in ((tag["source"], SOURCE_ROWS), (tag["sink"], SINK_ROWS)):
        for field, row in rows.items():
            if half.get(field):
                ws.cell(row, first_col).value = half[field]

    # The two PSE cells come from the market path's own ends, not from a
    # field of their own — the one place either code is written down.
    for row, pse in ((SOURCE_PSE_ROW, source_pse(tag)), (SINK_PSE_ROW, sink_pse(tag))):
        if pse:
            ws.cell(row, first_col).value = pse

    # Both halves of the sheet carry the same schedule: it is one flow of
    # energy described from each end.
    _write_hours(ws, SOURCE_HOURS_ROW, SOURCE_STAR_ROWS, tag["mw_by_hour"], first_col)
    _write_hours(ws, SINK_HOURS_ROW, SINK_STAR_ROWS, tag["mw_by_hour"], first_col)

    _write_block(
        ws, TRANSMISSION_ROW, transmission_rows(tag), TRANSMISSION_FIELDS,
        first_col, {"reservation": _as_number, "mw": _as_number},
    )
    _write_block(
        ws, MARKET_PATH_ROW, market_path_rows(tag), MARKET_PATH_FIELDS, first_col
    )
    _write_block(
        ws, CARBON_COPY_ROW, carbon_copy_rows(tag), CARBON_COPY_FIELDS, first_col
    )


def build_workbook(tags):
    """The template filled in for these tags, one column group each.

    The caller validates first — `domain.tags.tag_errors` — this only
    writes what it's given. One tag produces exactly the file it always
    did: the extra groups only exist when there is a second tag to put in
    one, which is also how the desk's own split files read.

    The flow date is written once: every tag in a file is one day's.
    """
    tags = list(tags)
    wb = load_workbook(TEMPLATE)
    ws = wb[SHEET]
    for index in range(1, len(tags)):
        _add_group(ws, index)

    ws[DATE_CELL] = tags[0]["flow_date"]
    for index, tag in enumerate(tags):
        _write_tag(ws, tag, index)
    return wb


def day_folder(flow_date):
    """The folder this flow date's tags belong in, created if it isn't
    there.

    One folder per **trading session**, not per day: Friday and Saturday
    traded together on Thursday both go in "16-17 Oct 2026". The session's
    days come from the WECC calendar (domain.trade.session_days), which is
    the only thing that knows which days were traded together.

    An existing folder still wins over a new one. The desk names these by
    hand too, and two folders for one session is the one outcome nobody
    wants — so anything already there that covers the day is used as it
    stands, whichever way its month is spelled.

    An unreadable root or calendar falls through to the day's own name and
    lets the write itself report the problem, which is where the useful
    message is.
    """
    if TEST_MODE:
        return TAG_FOLDER / TEST_SUBFOLDER
    try:
        for child in sorted(TAG_FOLDER.iterdir()):
            if child.is_dir() and folder_covers(child.name, flow_date):
                return child
    except OSError:
        pass
    try:
        days = session_days(flow_date, sessions_near(flow_date))
    except Exception:
        days = [flow_date]
    return TAG_FOLDER / day_folder_name(days)


#: What the desk appends when one file holds more than one tag, read off
#: its own "... - EPE - SWPW(CRSP) SPLIT.xlsx".
SPLIT_SUFFIX = " SPLIT"


def target_path(tags):
    """Where these tags are written. One file, named after the first of
    them DASH they share nothing in the workbook but its name."""
    tags = list(tags)
    name = tags[0].get("name", "")
    if len(tags) > 1:
        name += SPLIT_SUFFIX
    return day_folder(tags[0]["flow_date"]) / tag_filename(
        tags[0]["flow_date"], name
    )


def write_tag_file(tags, overwrite=False):
    """Build and save these tags as one workbook. Returns the path written.

    Raises FileExistsError if the target is already there and `overwrite`
    isn't set: two tags on one day that happen to share a name are far more
    likely to be a second leg needing its own name than a file anyone
    wanted replaced.
    """
    tags = list(tags)
    path = target_path(tags)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists.")
    wb = build_workbook(tags)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except OSError as e:
        raise RuntimeError(
            f"Could not write {path.name}: {e}. If that file is open in "
            "Excel, close it and generate again."
        ) from e
    return path
