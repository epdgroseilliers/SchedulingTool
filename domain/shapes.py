"""Shape parsing (HL/LL/ATC/1x16/1x8/custom hour range) and turning a shape
into an actual per-date set of hours, using the WECC on-/off-peak calendar
— except for the two shapes that deliberately don't ask it anything, which
is the whole reason the desk writes them (FIXED_SHAPE_HOURS).
"""

import re
from datetime import timedelta

from data.calendar import is_peak_map
from domain.grid import HOURS, as_date, make_block_grid

SHAPE_ALIASES = {
    "HL": "on_peak",
    "ON-PEAK": "on_peak",
    "ONPEAK": "on_peak",
    "LL": "off_peak",
    "OFF-PEAK": "off_peak",
    "OFFPEAK": "off_peak",
    "ATC": "flat",
    "FLAT": "flat",
    "24H": "flat",
    "1X16": "1x16",
    "1X8": "1x8",
}
SHAPE_KIND_LABELS = {"on_peak": "HL", "off_peak": "LL", "flat": "ATC"}

#: The two single-day products, as the hours they actually cover.
#:
#: They look like HL and LL and are deliberately not them. **HL and LL ask
#: the WECC calendar what kind of day it is; these don't.** An HL leg flows
#: no hours at all on an off-peak day, and an LL leg flows all twenty-four
#: — which is exactly wrong for a 1x16 or a 1x8, whose whole purpose is
#: naming a block of hours on one named day, weekend days included. "1x16
#: on Saturday" is sixteen hours on Saturday; "HL on Saturday" is nothing.
FIXED_SHAPE_HOURS = {
    "1x16": tuple(range(7, 23)),
    "1x8": tuple(range(1, 7)) + (23, 24),
}

#: What each one stores in the sheet's He column: the hours written out,
#: never "1x16". The desk's own He format (data.bilateral.hours_to_he_string)
#: and the only honest answer — "HL" would claim the WECC calendar had a
#: say in it, and these are the shapes used precisely when it doesn't.
FIXED_SHAPE_HE = {"1x16": "7-22", "1x8": "1-6,23-24"}

HOUR_RANGE_RE = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})$")
SINGLE_HOUR_RE = re.compile(r"^(\d{1,2})$")


def parse_shape(shape_str):
    """Parse a shape entry into (kind, start_he, end_he).

    kind is one of on_peak/off_peak/flat/custom. start_he/end_he are only
    set (and only meaningful) for custom. Raises ValueError with a
    trader-facing message on anything unrecognized.
    """
    s = (shape_str or "").strip().upper()
    if not s:
        raise ValueError(
            "Enter a shape: HL, LL, ATC, 1x16, 1x8, or an hour range like 7-22."
        )
    # Spaces squeezed out first, so "1 x 16" and "ON PEAK" are the shapes
    # they obviously are. A trader typing into the Shape box is not
    # writing the same way the parser normalizes a broker string.
    squashed = s.replace(" ", "")
    if squashed in SHAPE_ALIASES:
        return SHAPE_ALIASES[squashed], None, None

    m = HOUR_RANGE_RE.match(s)
    if m:
        start_he, end_he = int(m.group(1)), int(m.group(2))
    else:
        m = SINGLE_HOUR_RE.match(s)
        if not m:
            raise ValueError(
                f"Could not parse shape '{shape_str}'. Use HL, LL, ATC, "
                "1x16, 1x8, or an hour range like 7-22."
            )
        start_he = end_he = int(m.group(1))

    if not (1 <= start_he <= 24 and 1 <= end_he <= 24):
        raise ValueError(f"Hours in '{shape_str}' must be between 1 and 24.")
    if start_he > end_he:
        raise ValueError(f"In '{shape_str}', the start hour must be ≤ the end hour.")
    return "custom", start_he, end_he


def shape_label(shape_str):
    """The He label a shape stores as ('HL'/'LL'/'ATC'), or None for a
    custom hour range, which is written out as explicit hours instead."""
    try:
        kind, _, _ = parse_shape(shape_str)
    except ValueError:
        return None
    return SHAPE_KIND_LABELS.get(kind)


def shape_to_he(shape_str):
    """The He text for a block that skips the hourly grid entirely — a
    monthly-or-longer trade's MW is fixed for the whole period, so the
    shape is stored as written rather than expanded against the WECC
    peak calendar. Raises ValueError on an unparseable shape, same as
    parse_shape.
    """
    kind, start_he, end_he = parse_shape(shape_str)
    if kind in FIXED_SHAPE_HE:
        return FIXED_SHAPE_HE[kind]
    if kind in SHAPE_KIND_LABELS:
        return SHAPE_KIND_LABELS[kind]
    return str(start_he) if start_he == end_he else f"{start_he}-{end_he}"


def monthly_block_rows(blocks):
    """One BilateralTrades-shaped row per block for a monthly-or-longer
    trade: MW is fixed for the whole date range, so there's no hourly
    grid to generate against the WECC calendar or fold back down — each
    block *is* the row.

    `blocks` is an iterable of (start_date, end_date, shape, mw). Returns
    (rows, errors) in the same {"start_date", "stop_date", "he", "mw"}
    shape data.bilateral.compress_schedule produces; rows is only
    complete when errors is empty.
    """
    rows, errors = [], []
    for start_date, end_date, shape, mw in blocks:
        if start_date > end_date:
            errors.append(
                f"Start Date must be on or before End Date ({start_date} - {end_date})."
            )
            continue
        try:
            he = shape_to_he(shape)
        except ValueError as e:
            errors.append(str(e))
            continue
        rows.append(
            {"start_date": start_date, "stop_date": end_date, "he": he, "mw": float(mw)}
        )
    return rows, errors


def build_schedule(start_date, end_date, kind, mw, custom_start_he=None, custom_end_he=None):
    """Determine which hours get `mw` for each date in [start_date, end_date],
    using the WECC calendar to decide which hours are on-/off-peak per day.

    kind is one of on_peak/off_peak/flat/custom/1x16/1x8, as returned by
    parse_shape. The last two are fixed hour sets and never ask the
    calendar anything — see FIXED_SHAPE_HOURS.

    Returns (hours_by_date, excluded_dates, missing_calendar_dates).
    hours_by_date: {date: set of HE to set to mw}. Dates with no entry (or
    an empty set) get an all-zero MW day.
    excluded_dates: off-peak calendar days skipped by an on_peak leg.
    missing_calendar_dates: dates the WECC calendar had no row for at all.
    """
    peak_map = {}
    if kind in ("on_peak", "off_peak"):
        peak_map = is_peak_map(start_date, end_date)

    hours_by_date = {}
    excluded_dates = []
    missing_dates = []
    d = start_date
    while d <= end_date:
        if kind == "flat":
            hours = range(1, 25)
        elif kind in FIXED_SHAPE_HOURS:
            hours = FIXED_SHAPE_HOURS[kind]
        elif kind == "custom":
            hours = range(int(custom_start_he), int(custom_end_he) + 1)
        else:
            is_peak = peak_map.get(d)
            if is_peak is None:
                missing_dates.append(d)
                d += timedelta(days=1)
                continue
            if kind == "on_peak":
                if is_peak:
                    hours = range(7, 23)
                else:
                    excluded_dates.append(d)
                    hours = []
            else:  # off_peak
                hours = range(1, 25) if not is_peak else list(range(1, 7)) + list(range(23, 25))
        hours_by_date[d] = set(hours)
        d += timedelta(days=1)

    return hours_by_date, excluded_dates, missing_dates


def _hours_without_the_calendar(shape):
    """The hours a shape covers when it covers the same ones every day,
    or None when only the WECC calendar can say (HL, LL, ATC).

    1x16 and 1x8 are in the first group: naming their hours outright,
    weekend days included, is the whole reason the desk writes them.
    """
    kind, start_he, end_he = parse_shape(shape)
    if kind in FIXED_SHAPE_HOURS:
        return set(FIXED_SHAPE_HOURS[kind])
    if kind == "custom":
        return set(range(start_he, end_he + 1))
    return None


def segments_share_one_block(segments):
    """Whether a string's quantities belong in one schedule block.

    Normally yes, and that is the point: a block's Shape and MW only ever
    *seed* its grid, and the grid is what gets written, so "150mw he9-12
    and 100mw he13-14" is one block whose profile steps rather than two
    blocks. The database still gets the row per shape it has always got
    — data.bilateral.compress_schedule splits a block by MW level
    before writing it, so the rows are the same either way.

    Two quantities need blocks of their own:

    **One of them names HL, LL or ATC.** A block's Shape is what lets its
    row be stored as that word rather than as the hours
    (data.bilateral.he_for_hours), and "HL" is not "7-22": one asks the
    calendar what kind of day it is and the other doesn't, so they are
    different trades on an off-peak day. Merging would quietly rewrite
    the word as hours.

    Those are exactly the shapes whose hours this can't name without the
    calendar, so one test catches both facts — which is not a
    coincidence: a shape is written as a word in the He column because
    the word means something the hours don't. (1x16 and 1x8 name their
    hours outright and are stored as hours whatever block they are in,
    so they lose nothing by sharing one.)

    **Their hours overlap.** Then there is no single profile to build:
    one would overwrite the other's MW without saying so. Kept apart,
    the duplicate check at submit names the hour
    (ui.actions._flatten_schedule).
    """
    if len(segments) < 2:
        return True          # nothing to share it with
    seen = set()
    for segment in segments:
        try:
            hours = _hours_without_the_calendar(segment["shape"])
        except ValueError:
            return False
        if hours is None or hours & seen:
            return False
        seen |= hours
    return True


def combined_block_grid(start_date, end_date, block_dates, segments):
    """One grid holding every segment's own hours at its own MW.

    For a block that came off a broker string naming a quantity more than
    once. Only ever called on segments segments_share_one_block has
    passed, so the hours don't overlap and nothing here needs the
    calendar — but it goes through generate_block_grid all the same, so
    a block built this way and a block built by Generate are built by the
    same code.

    Returns (grid, error), the same way generate_block_grid does.
    """
    mw_by_date = {}
    for segment in segments:
        grid, _, _, error = generate_block_grid(
            start_date, end_date, block_dates, segment["shape"], segment["mw"]
        )
        if error:
            return None, error
        for _, row in grid.iterrows():
            day = mw_by_date.setdefault(as_date(row["Date"]), {})
            for he in HOURS:
                if row[str(he)]:
                    day[he] = float(row[str(he)])
    return make_block_grid(block_dates, mw_by_date), None


def generate_block_grid(start_date, end_date, block_dates, shape, mw):
    """Build a block's grid from its Shape/MW — the logic behind the
    Generate button, factored out so a brand-new block can be
    auto-populated with the exact same result on its first render.

    Returns (grid_df, excluded_dates, missing_dates, error). On a bad Shape
    or a WECC calendar failure, `error` is a trader-facing message and the
    other values are unusable.
    """
    try:
        kind, custom_start_he, custom_end_he = parse_shape(shape)
    except ValueError as e:
        return None, [], [], str(e)
    try:
        hours_by_date, excluded, missing = build_schedule(
            start_date, end_date, kind, mw, custom_start_he, custom_end_he
        )
    except Exception as e:
        return None, [], [], f"Could not load the WECC calendar: {e}"
    mw_by_date = {
        d: {he: mw for he in hours_by_date.get(d, set())} for d in block_dates
    }
    return make_block_grid(block_dates, mw_by_date), excluded, missing, None
