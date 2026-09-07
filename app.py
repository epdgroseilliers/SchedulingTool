import re
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from data.calendar import is_peak_map

st.set_page_config(page_title="Trade Scheduler", page_icon="⚡", layout="wide")

if "trades" not in st.session_state:
    st.session_state.trades = []
if "block_ids" not in st.session_state:
    st.session_state.block_ids = [0]
if "next_block_id" not in st.session_state:
    st.session_state.next_block_id = 1

HOURS = list(range(1, 25))
MAX_BLOCK_DATES = 31
DATE_EDITOR_ROW_HEIGHT = 20

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


def dates_in_range(start_date, end_date):
    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def day_grid_key(bid, d):
    return f"day_grid_{bid}_{d.isoformat()}"


def version_key(bid):
    return f"grid_ver_{bid}"


def get_version(bid):
    return st.session_state.get(version_key(bid), 0)


def make_empty_day_grid():
    return pd.DataFrame({"HE": HOURS, "MW": [0.0] * 24})


def get_day_grid(bid, d):
    key = day_grid_key(bid, d)
    if key not in st.session_state:
        st.session_state[key] = make_empty_day_grid()
    return st.session_state[key]


def to_wide_row(long_df):
    """Collapse a long (24-row HE/MW) grid into a single-row grid: a leading
    "HE" column labeling the row as "MW", then one column per hour holding
    the MW value."""
    ordered = long_df.sort_values("HE")
    row = {"HE": "MW"}
    row.update({str(h): mw for h, mw in zip(ordered["HE"], ordered["MW"])})
    return pd.DataFrame([row])


def from_wide_row(wide_df):
    """Expand a wide single-row grid (with its leading HE label column)
    back into the long 24-row HE/MW grid."""
    row = wide_df.iloc[0]
    return pd.DataFrame({"HE": HOURS, "MW": [float(row[str(h)]) for h in HOURS]})


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


def format_price(index_name, price):
    if index_name:
        sign = "+" if price >= 0 else ""
        return f"{index_name}{sign}{price:.2f}"
    return f"{price:.2f}"


st.title("⚡ Add Trade")

is_sell = st.toggle("Sell", value=False, help="Off = Buy, On = Sell")
direction = "Sell" if is_sell else "Buy"

c0, c1, c2, c3, c4 = st.columns(5)
trade_date = c0.date_input("Trade Date", value=date.today(), help="Date the deal was struck.")
counterparty = c1.text_input("Counterparty")
location = c2.text_input("Location (POR/POD)")
index_name = c3.text_input("Index", help="Leave blank for a flat fixed price.")
price = c4.number_input(
    "Price / Premium",
    step=0.01,
    help="Flat price if Index is blank. Premium to the index (e.g. +2 or -1) if Index is set.",
)

st.subheader("Schedule")

block_grids = {}
for bid in st.session_state.block_ids:
    with st.container(border=True):
        specs = [1.1, 1.1, 1.5, 0.7, 0.9, 1.1]
        show_remove = len(st.session_state.block_ids) > 1
        if show_remove:
            specs.append(1.0)
        row = st.columns(specs, vertical_alignment="bottom")
        dcol1, dcol2, gcol1, gcol2, gcol3, gcol4 = row[:6]

        start_date = dcol1.date_input("Start Date", value=date.today(), key=f"start_{bid}")
        end_date = dcol2.date_input("End Date", value=start_date, key=f"end_{bid}")
        shape = gcol1.text_input(
            "Shape",
            value="HL",
            key=f"shape_{bid}",
            help="HL and LL follow the WECC on-/off-peak calendar. ATC is flat 24H. "
            "Anything else is read as an hour range, e.g. 7-22, 10-11, or a single hour like 14.",
        )
        mw = gcol2.number_input("MW", min_value=0.01, step=1.0, value=100.0, key=f"mw_{bid}")
        generate_clicked = gcol3.button("Generate", key=f"generate_{bid}", width="stretch")
        clear_clicked = gcol4.button(
            "Clear schedule", key=f"clear_{bid}", type="primary", width="stretch"
        )
        remove_clicked = row[6].button("Remove block", key=f"remove_{bid}", width="stretch") if show_remove else False

        if remove_clicked:
            st.session_state.block_ids.remove(bid)
            for d in dates_in_range(start_date, end_date):
                st.session_state.pop(day_grid_key(bid, d), None)
            st.session_state.pop(version_key(bid), None)
            st.rerun()

        ver = get_version(bid)
        block_grids[bid] = {}

        if start_date > end_date:
            st.error("Start Date must be on or before End Date.")
        else:
            block_dates = dates_in_range(start_date, end_date)
            if len(block_dates) > MAX_BLOCK_DATES:
                st.error(
                    f"This block spans {len(block_dates)} dates. Split it into smaller "
                    f"blocks of {MAX_BLOCK_DATES} dates or fewer."
                )
            else:
                if generate_clicked:
                    try:
                        kind, custom_start_he, custom_end_he = parse_shape(shape)
                    except ValueError as e:
                        st.error(str(e))
                    else:
                        try:
                            hours_by_date, excluded, missing = build_schedule(
                                start_date, end_date, kind, mw, custom_start_he, custom_end_he
                            )
                        except Exception as e:
                            st.error(f"Could not load the WECC calendar: {e}")
                        else:
                            for d in block_dates:
                                scheduled = hours_by_date.get(d, set())
                                st.session_state[day_grid_key(bid, d)] = pd.DataFrame(
                                    {"HE": HOURS, "MW": [float(mw) if he in scheduled else 0.0 for he in HOURS]}
                                )
                            st.session_state[version_key(bid)] = ver + 1
                            if excluded:
                                st.toast(
                                    f"HL leg skipped {len(excluded)} off-peak calendar day(s): "
                                    f"{', '.join(str(d) for d in excluded)}",
                                    icon="ℹ️",
                                )
                            if missing:
                                st.toast(
                                    f"No WECC calendar data for {len(missing)} date(s), skipped: "
                                    f"{', '.join(str(d) for d in missing)}",
                                    icon="⚠️",
                                )
                            st.rerun()
                if clear_clicked:
                    for d in block_dates:
                        st.session_state[day_grid_key(bid, d)] = make_empty_day_grid()
                    st.session_state[version_key(bid)] = ver + 1
                    st.rerun()

                for d in block_dates:
                    st.caption(f"**{d.strftime('%a %Y-%m-%d')}**")
                    seed = to_wide_row(get_day_grid(bid, d))
                    wide_edited = st.data_editor(
                        seed,
                        key=f"editor_{bid}_{d.isoformat()}_{ver}",
                        num_rows="fixed",
                        column_config={
                            "HE": st.column_config.TextColumn("HE", disabled=True, width=45),
                            **{
                                str(h): st.column_config.NumberColumn(
                                    str(h), min_value=0.0, step=1.0, required=True, width=45
                                )
                                for h in HOURS
                            },
                        },
                        hide_index=True,
                        width="stretch",
                        height="content",
                        row_height=DATE_EDITOR_ROW_HEIGHT,
                    )
                    day_df = from_wide_row(wide_edited)
                    st.session_state[day_grid_key(bid, d)] = day_df
                    block_grids[bid][d] = day_df

if st.button("+ Add another block"):
    st.session_state.block_ids.append(st.session_state.next_block_id)
    st.session_state.next_block_id += 1
    st.rerun()

st.divider()

st.markdown(
    """
    <style>
    div.st-key-add_trade_btn {
        position: fixed;
        bottom: 1.5rem;
        right: 1.5rem;
        z-index: 9999;
    }
    div.st-key-add_trade_btn button {
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if st.button("Add Trade", type="primary", key="add_trade_btn"):
    errors = []
    if not counterparty:
        errors.append("Counterparty is required.")
    if not location:
        errors.append("Location is required.")

    schedule = []
    seen = set()
    for date_grids in block_grids.values():
        for d, grid in date_grids.items():
            for _, row in grid.iterrows():
                he = row.get("HE")
                mw_val = row.get("MW")
                if pd.isna(he) or pd.isna(mw_val) or mw_val <= 0:
                    continue
                key = (d, int(he))
                if key in seen:
                    errors.append(f"Duplicate schedule entry for {d} HE {he} — merge into one block.")
                    continue
                seen.add(key)
                schedule.append((d, int(he), float(mw_val)))

    if not schedule:
        errors.append("Schedule needs at least one hour with MW > 0.")

    if errors:
        for e in errors:
            st.error(e)
    else:
        st.session_state.trades.append({
            "trade_date": trade_date,
            "direction": direction,
            "counterparty": counterparty,
            "location": location,
            "index": index_name,
            "price": price,
            "schedule": schedule,
        })
        for bid, date_grids in block_grids.items():
            for d in date_grids:
                st.session_state.pop(day_grid_key(bid, d), None)
            st.session_state.pop(version_key(bid), None)
        st.session_state.block_ids = [st.session_state.next_block_id]
        st.session_state.next_block_id += 1
        st.success("Trade added.")
        st.rerun()

st.header("Trades")

if not st.session_state.trades:
    st.info("No trades yet.")
else:
    for i, t in enumerate(st.session_state.trades):
        total_mwh = sum(mw for _, _, mw in t["schedule"])
        dates = sorted({d for d, _, _ in t["schedule"]})
        with st.expander(
            f"{t['direction']} — {t['counterparty']} @ {t['location']} — "
            f"{total_mwh:,.0f} MWh across {len(dates)} date(s)"
        ):
            c1, c2 = st.columns([4, 1])
            with c1:
                sched_df = pd.DataFrame(t["schedule"], columns=["Date", "HE", "MW"])
                st.dataframe(sched_df, hide_index=True, width="stretch")
                st.caption(
                    f"Trade date: {t.get('trade_date', '—')} | "
                    f"Price: {format_price(t.get('index'), t['price'])}"
                )
            with c2:
                if st.button("Delete", key=f"del_trade_{i}"):
                    st.session_state.trades.pop(i)
                    st.rerun()
