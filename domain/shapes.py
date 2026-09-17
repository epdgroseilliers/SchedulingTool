"""Shape parsing (HL/LL/ATC/custom hour range) and turning a shape into an
actual per-date set of hours, using the WECC on-/off-peak calendar.
"""

import re
from datetime import timedelta

from data.calendar import is_peak_map
from domain.grid import make_block_grid

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
}
SHAPE_KIND_LABELS = {"on_peak": "HL", "off_peak": "LL", "flat": "ATC"}

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
        raise ValueError("Enter a shape: HL, LL, ATC, or an hour range like 7-22.")
    if s in SHAPE_ALIASES:
        return SHAPE_ALIASES[s], None, None

    m = HOUR_RANGE_RE.match(s)
    if m:
        start_he, end_he = int(m.group(1)), int(m.group(2))
    else:
        m = SINGLE_HOUR_RE.match(s)
        if not m:
            raise ValueError(
                f"Could not parse shape '{shape_str}'. Use HL, LL, ATC, or an hour range like 7-22."
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


#: How far ahead to look when extending a DAM block's default End Date —
#: comfortably past any realistic weekend/holiday run of same-peak-status
#: days.
DAM_DEFAULT_LOOKAHEAD_DAYS = 14


def dam_default_end_date(start_date, shape):
    """The default End Date to pair with a DAM block's default Start Date,
    for any shape (HL, LL, ATC, or a custom hour range).

    A day-ahead trade's flow naturally continues through every following
    day that shares the start date's own on-/off-peak status — e.g. a
    Friday (peak) immediately followed by a peak Saturday belongs to the
    same DAM leg — so this extends day by day until the run breaks (or the
    calendar has no data for the next day). This is only ever the
    *default*: an explicit date the trader typed, or that the broker-string
    parser read from the string itself (a weekday like "Sun only", or a
    literal "9/18"), always wins — see ui.paste.apply_parsed_string, which
    only falls back to this default when the string carries no date of its
    own.
    """
    try:
        parse_shape(shape)
    except ValueError:
        return start_date

    try:
        peak_map = is_peak_map(
            start_date, start_date + timedelta(days=DAM_DEFAULT_LOOKAHEAD_DAYS)
        )
    except Exception:
        # A calendar hiccup here shouldn't block the page from rendering —
        # falls back to a single day, same as a shape with no peak/off-peak
        # notion; clicking Generate explicitly still surfaces the error.
        return start_date
    start_is_peak = peak_map.get(start_date)
    if start_is_peak is None:
        return start_date

    end_date = start_date
    d = start_date + timedelta(days=1)
    while peak_map.get(d) == start_is_peak:
        end_date = d
        d += timedelta(days=1)
    return end_date


def build_schedule(start_date, end_date, kind, mw, custom_start_he=None, custom_end_he=None):
    """Determine which hours get `mw` for each date in [start_date, end_date],
    using the WECC calendar to decide which hours are on-/off-peak per day.

    kind is one of on_peak/off_peak/flat/custom, as returned by parse_shape.

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
