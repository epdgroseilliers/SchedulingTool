from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

from data.db import get_engine

SQL_PATH = Path(__file__).parent / "sql" / "wecc_calendar.sql"

# Cached window is wider than any single trade's date range so almost every
# lookup is served from cache; the 30-day TTL matches how often the WECC
# calendar itself actually changes.
WINDOW_BEFORE_DAYS = 60
WINDOW_AFTER_DAYS = 450
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading WECC calendar...")
def _load_calendar_window(window_start: date, window_end: date) -> pd.DataFrame:
    sql = SQL_PATH.read_text()
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(sql), conn, params={"start_date": window_start, "stop_date": window_end}
        )
    df["FlowDate"] = pd.to_datetime(df["FlowDate"]).dt.date
    df["IsPeak"] = df["IsPeak"].astype(bool)
    return df


def get_calendar(start_date: date, end_date: date) -> pd.DataFrame:
    """Return FlowDate/IsPeak rows covering [start_date, end_date]."""
    today = date.today()
    window_start = today - timedelta(days=WINDOW_BEFORE_DAYS)
    window_end = today + timedelta(days=WINDOW_AFTER_DAYS)
    df = _load_calendar_window(window_start, window_end)
    if start_date < window_start or end_date > window_end:
        extra = _load_calendar_window(start_date, end_date)
        df = pd.concat([df, extra]).drop_duplicates("FlowDate")
    return df[(df["FlowDate"] >= start_date) & (df["FlowDate"] <= end_date)]


def is_peak_map(start_date: date, end_date: date) -> dict:
    """Map each FlowDate in range to its IsPeak flag from the WECC calendar."""
    df = get_calendar(start_date, end_date)
    return dict(zip(df["FlowDate"], df["IsPeak"]))
