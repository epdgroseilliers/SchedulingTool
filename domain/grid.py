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


def parse_pasted_schedule(text, dates):
    """(mw_by_date, error) for a column of MW copied out of Excel.

    `mw_by_date` is {date: {he: mw}}, ready for make_block_grid; on any
    problem it is None and `error` is a trader-facing message instead. An
    all-or-nothing parse on purpose, like the broker string's: half a
    schedule silently applied is worse than a refusal.

    Split on newlines and tabs — the two things Excel actually puts on the
    clipboard — and never on commas, which appear *inside* numbers as
    thousands separators ("1,200" is one value, not two).

    Two shapes are accepted, and nothing else:

    - **24 values** — one day's hours, applied to every date in the block,
      which is the common case: the same variable shape each day.
    - **24 x however many dates** — consecutive days in date order.

    Any other count is ambiguous about which hours were meant (a 16-value
    paste could be HE7-22 or HE1-16, and guessing wrong moves a trade's
    energy to the wrong hours), so it is refused with the counts named.
    """
    tokens = [
        tok.strip().replace(",", "")
        for tok in (text or "").replace("\t", "\n").split("\n")
    ]
    tokens = [tok for tok in tokens if tok]
    if not tokens:
        return None, "Nothing pasted — copy the MW column out of Excel first."

    values = []
    for tok in tokens:
        try:
            values.append(float(tok))
        except ValueError:
            return None, f"Could not read '{tok}' as a number."
    negative = next((v for v in values if v < 0), None)
    if negative is not None:
        return None, f"{negative:g} is negative — MW must be 0 or more."

    per_day = len(HOURS)
    if len(values) == per_day:
        day_values = [values] * len(dates)
    elif len(values) == per_day * len(dates):
        day_values = [
            values[i * per_day:(i + 1) * per_day] for i in range(len(dates))
        ]
    else:
        expected = f"{per_day}"
        if len(dates) > 1:
            expected += f" (one per hour, repeated on each date) or {per_day * len(dates)}"
        return None, (
            f"Pasted {len(values)} values; expected {expected} for "
            f"{len(dates)} date(s)."
        )

    return {
        d: dict(zip(HOURS, day)) for d, day in zip(dates, day_values)
    }, None


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
