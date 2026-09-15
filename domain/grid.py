"""The HE x Date grid: building it, and converting between its wide (one row
per date, one column per hour-ending) and long (date, he, mw) shapes.
"""

from datetime import timedelta

import pandas as pd

HOURS = list(range(1, 25))

# A block spanning more than this many dates is rejected — past this, a
# trader almost certainly meant several separate legs, and the on-screen
# grid stops being something you can sanity-check at a glance.
MAX_BLOCK_DATES = 31


def dates_in_range(start_date, end_date):
    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def as_date(x):
    """Normalize a Date-column cell back to a plain `date`. The data_editor
    round trip can hand back a pandas Timestamp for a DateColumn; downstream
    code keys everything on plain `date` objects."""
    return x.date() if hasattr(x, "date") else x


def make_block_grid(dates, mw_by_date=None):
    """One row per date, a leading Date column, then one column per HE
    holding the MW value. `mw_by_date`, if given, is {date: {he: mw}};
    missing dates or hours default to 0.0."""
    mw_by_date = mw_by_date or {}
    rows = []
    for d in dates:
        hours = mw_by_date.get(d, {})
        row = {"Date": d}
        row.update({str(h): float(hours.get(h, 0.0)) for h in HOURS})
        rows.append(row)
    return pd.DataFrame(rows)


def block_grid_to_date_frames(wide_df):
    """Expand a block's wide grid (Date + HE columns) into {date: long
    HE/MW DataFrame}, the shape the rest of the app works with."""
    result = {}
    for _, row in wide_df.iterrows():
        d = as_date(row["Date"])
        result[d] = pd.DataFrame({"HE": HOURS, "MW": [float(row[str(h)]) for h in HOURS]})
    return result


def schedule_to_wide(schedule):
    """Pivot a flat (date, he, mw) schedule into the same wide shape used
    while building the trade: one row per date, one column per HE."""
    by_date = {}
    for d, he, mw in schedule:
        by_date.setdefault(d, {})[he] = mw
    rows = []
    for d in sorted(by_date):
        row = {"Date": d}
        row.update({str(h): by_date[d].get(h, 0.0) for h in HOURS})
        rows.append(row)
    return pd.DataFrame(rows)
