"""Writing the SWPW bilateral bid file.

Reverse-engineered from the desk's own real files under `BID_FOLDER`
(27 days' worth, plus the blank "TEST" template) rather than from any
written spec — see PROJECT.md for exactly what was confirmed from that
history.

**The sheet's hour column is EPT; the app is PPT**, so every hour is
shifted +3 before it's written, and the last three hours of a PPT day land
on the `1*`/`2*`/`3*` rows — HE1-3 of the next EPT day. See
`domain.bidfiles.ppt_to_ept`, which is also what finally explained those
starred rows.

Built from scratch every time (`openpyxl.Workbook()`, no template *content*
copied at runtime) rather than by copying and editing a prior day's file —
the fixed 14-column layout the desk has evolved by hand reuses the same
lettered column for different GCA/LCA codes from one day to the next, which
isn't something to reproduce programmatically without guessing. This writes
exactly the columns today's positions need, no more.

**The one thing that *is* borrowed from an existing file is its VBA shell.**
A first version of this wrote a plain workbook straight to a `.xlsm` name —
Excel refused to open it outright ("the file format or file extension is
not valid"), not merely a warning. A `.xlsm`'s macro-enabled-ness is part of
its actual OOXML content type, not just its extension, and a workbook with
no VBA project at all doesn't have that content type. The fix is the same
mechanism openpyxl's own `load_workbook(path, keep_vba=True)` round-trip
uses: attach a real macro-enabled file's zip as `Workbook.vba_archive`
before saving, and the writer pulls in `xl/vbaProject.bin` and declares the
right content types on its own. See `_attach_vba_shell()` below.
"""

import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from domain.bidfiles import LONG, SHORT, ppt_to_ept

BID_FOLDER = Path(r"Z:\Bids\BID Positionnement SPP\BID Bilateral SPP")

# TEMPORARY, while this feature is under review — every generated file is
# written under this fixed name instead of the real dated one below, so
# nothing touches the desk's actual submissions yet. Flip to False (or just
# call official_filename() directly from write_bid_file) once ready to go
# live; nothing else about the file changes.
TEST_MODE = True
TEST_FILENAME = "BID officiel Bilateral SPP - test emilio.xlsm"

#: The blank template — always present, and unlike a dated file, never
#: going away — used purely as a source of a genuine VBA project so the
#: output is a real macro-enabled workbook. Its own (unrelated) macro
#: content doesn't matter; only its presence does. See _attach_vba_shell().
VBA_SHELL_NAME = "BID officiel Bilateral SPP - TEST.xlsm"

SHEET_TITLE = "Bid Officiel SPP Bilateral"
DISCLAIMER = (
    "Ce fichier contient les bids officiels du passif. Aucune modification "
    "ne peut être effectué aux formules de ce fichier sans l'autorisation "
    "nécessaire."
)
SHORT_LABEL = "SHORT (Source>SWPW)"
LONG_LABEL = "LONG (SWPW>Sink)"

FONT_NAME = "Calibri"
YELLOW = PatternFill("solid", fgColor="FFFFFF99")
ORANGE = PatternFill("solid", fgColor="FFFF9933")
# Approximations of the real file's theme-based banner colors (a light blue
# for SHORT, a light tan for LONG) — not a pixel-exact match, since that
# would mean reproducing this workbook's specific theme.xml tint math for
# no real benefit; only the yellow input-cell fill is load-bearing (it
# marks "this is a data cell," reproduced exactly).
SHORT_BANNER = PatternFill("solid", fgColor="FFBDD7EE")
LONG_BANNER = PatternFill("solid", fgColor="FFFCE4D6")

THIN = Side(style="thin", color="FF000000")
MEDIUM = Side(style="medium", color="FF000000")
CENTER = Alignment(horizontal="center", vertical="center")
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center")

FIRST_DATA_COL = 4  # column D
BANNER_ROW, HEADER_ROW, DAMRT_ROW, MARKET_ROW, MWPRICE_ROW = 4, 5, 6, 7, 8
HOUR_FIRST_ROW, HOUR_LAST_ROW = 10, 33  # EPT HE1..HE24, one row each
#: The three rows after HE24, labeled '1*'/'2*'/'3*': HE1-3 of the *next*
#: EPT day, which is where the last three hours of a PPT day land. See
#: domain.bidfiles.ppt_to_ept.
STAR_ROWS = (34, 35, 36)
HOURTX_ROW, STRATEGY_ROW, VERIFCODE_ROW, ISNEDAM_ROW = 37, 38, 39, 40
LEGEND_FIRST_ROW = 42

#: PPT hour-endings — what the app works in, and what a bid line's
#: mw_by_hour is keyed by. The sheet's own hour column is EPT.
HOURS = list(range(1, 25))


def ppt_hour_row(he_ppt):
    """The sheet row a PPT hour-ending belongs on.

    The sheet's hour column is EPT ("HE EPT"), the app is PPT, and the
    three hours that fall past midnight Eastern go on the starred rows —
    see domain.bidfiles.ppt_to_ept for why.
    """
    he_ept, next_day = ppt_to_ept(he_ppt)
    if next_day:
        return STAR_ROWS[he_ept - 1]
    return HOUR_FIRST_ROW + he_ept - 1


def official_filename(flow_date):
    """'BID officiel Bilateral SPP - 23 September 2026.xlsm' — day-of-month
    with no leading zero, full month name, matching every real filename in
    BID_FOLDER exactly."""
    return f"BID officiel Bilateral SPP - {flow_date.day} {flow_date:%B} {flow_date.year}.xlsm"


def target_path(flow_date):
    name = TEST_FILENAME if TEST_MODE else official_filename(flow_date)
    return BID_FOLDER / name


def _style(cell, *, font=None, fill=None, align=None, border=None, numfmt=None):
    cell.font = font or Font(name=FONT_NAME, size=11)
    if fill is not None:
        cell.fill = fill
    if align is not None:
        cell.alignment = align
    if border is not None:
        cell.border = border
    if numfmt is not None:
        cell.number_format = numfmt
    return cell


def _pair_border(is_mw_col):
    left = MEDIUM if is_mw_col else THIN
    return Border(left=left, right=THIN, top=THIN, bottom=THIN)


def _write_static(ws):
    """Everything that never depends on how many bid lines exist today:
    title, disclaimer, the hour-index column, the four flag-row labels, and
    the legend — all in column C, all copied verbatim from the real files.
    """
    _style(ws["C1"], font=Font(name=FONT_NAME, size=16, bold=True), align=LEFT)
    ws["C1"] = SHEET_TITLE
    _style(ws["C2"], font=Font(name=FONT_NAME, size=11, italic=True), align=LEFT)
    ws["C2"] = DISCLAIMER

    for row, label in (
        (DAMRT_ROW, "DAM/RT"), (MARKET_ROW, "Market"), (MWPRICE_ROW, "HE EPT"),
        (HOURTX_ROW, "Hour Transaction"), (STRATEGY_ROW, "Code de stratégie"),
        (VERIFCODE_ROW, "Code vérification"), (ISNEDAM_ROW, "Is NE DAM"),
        (LEGEND_FIRST_ROW, "IsPassif"),
    ):
        _style(ws.cell(row=row, column=3), font=Font(name=FONT_NAME, size=11, bold=(row == MWPRICE_ROW)), align=CENTER)
        ws.cell(row=row, column=3).value = label

    for i, hour in enumerate(HOURS):
        r = HOUR_FIRST_ROW + i
        _style(ws.cell(row=r, column=3), align=CENTER)
        ws.cell(row=r, column=3).value = hour
    for r, label in zip(STAR_ROWS, ("1*", "2*", "3*")):
        _style(ws.cell(row=r, column=3), align=CENTER)
        ws.cell(row=r, column=3).value = label

    for i, text in enumerate(("0 : Strandard", "1 : Passif ", "2 : Passif virtuel")):
        r = LEGEND_FIRST_ROW + 1 + i
        _style(ws.cell(row=r, column=3), align=LEFT)
        ws.cell(row=r, column=3).value = text

    ws.column_dimensions["A"].width = 1.71
    ws.column_dimensions["C"].width = 26.57
    ws.row_dimensions[1].height = 21
    ws.row_dimensions[BANNER_ROW].height = 47.25
    ws.row_dimensions[HEADER_ROW].height = 51
    ws.sheet_view.zoomScale = 70


def _write_date(ws, flow_date):
    cell = ws["C5"]
    cell.value = flow_date
    _style(cell, fill=ORANGE, align=CENTER, numfmt="mm-dd-yy",
           border=Border(left=THIN, right=THIN, top=THIN, bottom=THIN))


def _write_pair(ws, col, side, line):
    """One bid line: a MW column and a Price column. Returns the next free
    column (col + 2)."""
    mw_col, price_col = col, col + 1
    header = f"SPP-{side}({line['code']})"

    for row, mw_value, price_value, wrap in (
        (HEADER_ROW, header, header, True),
        (DAMRT_ROW, "DAM", "DAM", False),
        (MARKET_ROW, "SPP", "SPP", False),
    ):
        ws.merge_cells(start_row=row, start_column=mw_col, end_row=row, end_column=price_col)
        cell = ws.cell(row=row, column=mw_col)
        cell.value = mw_value
        _style(cell, fill=YELLOW, align=(CENTER_WRAP if wrap else CENTER),
               border=_pair_border(True))
        _style(ws.cell(row=row, column=price_col), fill=YELLOW, align=CENTER,
               border=_pair_border(False))

    mw_head = ws.cell(row=MWPRICE_ROW, column=mw_col)
    mw_head.value = "MW"
    _style(mw_head, fill=YELLOW, align=CENTER, border=_pair_border(True))
    price_head = ws.cell(row=MWPRICE_ROW, column=price_col)
    price_head.value = "Price"
    _style(price_head, fill=YELLOW, align=CENTER, border=_pair_border(False))

    # Style every hour cell first — EPT HE1-24 plus the three star rows —
    # then fill in only the hours this position actually flows.
    for r in list(range(HOUR_FIRST_ROW, HOUR_LAST_ROW + 1)) + list(STAR_ROWS):
        _style(ws.cell(row=r, column=mw_col), fill=YELLOW, align=CENTER,
               border=_pair_border(True), numfmt="0.00")
        _style(ws.cell(row=r, column=price_col), fill=YELLOW, align=CENTER,
               border=_pair_border(False), numfmt="0.00")

    for he_ppt in HOURS:
        mw = line["mw_by_hour"].get(he_ppt)
        # Only an hour the position actually flows gets a value — blank
        # otherwise, matching every real file (an unused hour is left
        # empty, never written as an explicit 0).
        if not mw:
            continue
        r = ppt_hour_row(he_ppt)
        ws.cell(row=r, column=mw_col).value = float(mw)
        ws.cell(row=r, column=price_col).value = float(line["price"])

    # The four rows that are always the same default in every real file —
    # see domain/bidfiles.py and PROJECT.md.
    for col_idx, is_mw in ((mw_col, True), (price_col, False)):
        for row, value in ((HOURTX_ROW, False), (VERIFCODE_ROW, 2), (ISNEDAM_ROW, False)):
            cell = ws.cell(row=row, column=col_idx)
            cell.value = value
            _style(cell, fill=YELLOW, align=CENTER, border=_pair_border(is_mw))
        strat_cell = ws.cell(row=STRATEGY_ROW, column=col_idx)
        _style(strat_cell, fill=YELLOW, align=CENTER, border=_pair_border(is_mw))

    return col + 2


def _write_banner(ws, start_col, end_col, label, fill):
    ws.merge_cells(start_row=BANNER_ROW, start_column=start_col, end_row=BANNER_ROW, end_column=end_col)
    for col in range(start_col, end_col + 1):
        cell = ws.cell(row=BANNER_ROW, column=col)
        _style(cell, font=Font(name=FONT_NAME, size=11, bold=True), fill=fill, align=CENTER,
               border=Border(left=MEDIUM, right=MEDIUM, top=MEDIUM, bottom=MEDIUM))
    ws.cell(row=BANNER_ROW, column=start_col).value = label


def build_workbook(flow_date, short_lines, long_lines):
    """A fresh BIDS workbook for exactly the given lines — no leftover
    columns from any prior day. `short_lines`/`long_lines` are dicts with
    "code", "price", "mw_by_hour" (see domain.bidfiles.build_bid_lines)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "BIDS"

    _write_static(ws)
    _write_date(ws, flow_date)

    col = FIRST_DATA_COL
    if short_lines:
        start = col
        for line in short_lines:
            col = _write_pair(ws, col, SHORT, line)
        _write_banner(ws, start, col - 1, SHORT_LABEL, SHORT_BANNER)
    if long_lines:
        start = col
        for line in long_lines:
            col = _write_pair(ws, col, LONG, line)
        _write_banner(ws, start, col - 1, LONG_LABEL, LONG_BANNER)

    return wb


def vba_shell_path():
    """Where to find the VBA-shell donor file (see _save_as_macro_enabled).
    A function, not a frozen constant, so it re-derives from the current
    BID_FOLDER each call — tests that redirect BID_FOLDER redirect this
    with it."""
    return BID_FOLDER / VBA_SHELL_NAME


def _save_as_macro_enabled(wb, path, shell_path=None):
    """Save `wb` to `path` as a genuine macro-enabled workbook.

    Attaches a real macro-enabled file's zip as `wb.vba_archive` before
    saving — openpyxl's writer then pulls in `xl/vbaProject.bin` and
    declares the right content types on its own; this is the same
    mechanism `load_workbook(path, keep_vba=True)` uses internally to
    round-trip a workbook's macros.

    Raises if the shell can't be read, rather than falling back to a plain
    save: a `.xlsm` without this isn't missing a nicety, it's a file Excel
    refuses to open at all ("the file format or file extension is not
    valid") — confirmed the hard way — so failing loudly here beats
    silently writing another broken one.
    """
    shell_path = shell_path or vba_shell_path()
    # Opening the shell and writing the output are caught separately: they
    # fail for completely different reasons, and folding them together
    # reported a locked output file as an unreadable VBA shell, which sends
    # you looking in the wrong place entirely.
    try:
        shell = zipfile.ZipFile(shell_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise RuntimeError(
            f"Could not build a valid .xlsm — the VBA shell file "
            f"({shell_path}) could not be read: {e}"
        ) from e

    try:
        wb.vba_archive = shell
        wb.save(path)
    except OSError as e:
        raise RuntimeError(
            f"Could not write {Path(path).name}: {e}. If that file is open "
            "in Excel, close it and generate again."
        ) from e
    finally:
        shell.close()


def write_bid_file(flow_date, short_lines, long_lines, overwrite=False, shell_path=None):
    """Build and save the file. Returns the path written to.

    Raises FileExistsError if the target already exists and `overwrite`
    isn't set — generating this twice for the same date must not silently
    clobber a file without the caller having asked for that.
    """
    path = target_path(flow_date)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(flow_date, short_lines, long_lines)
    _save_as_macro_enabled(wb, path, shell_path)
    return path
