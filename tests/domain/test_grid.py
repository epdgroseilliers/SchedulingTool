"""domain.grid — the HE x Date grid and its wide/long conversions. Pure,
no Streamlit or DB."""

from datetime import date

import pandas as pd

from domain.grid import (
    HOURS,
    as_date,
    block_grid_to_date_frames,
    dates_in_range,
    make_block_grid,
    schedule_to_wide,
)


def test_hours_is_1_through_24():
    assert HOURS == list(range(1, 25))


class TestDatesInRange:
    def test_single_day(self):
        d = date(2026, 9, 15)
        assert dates_in_range(d, d) == [d]

    def test_multi_day_inclusive(self):
        start, end = date(2026, 9, 15), date(2026, 9, 17)
        assert dates_in_range(start, end) == [
            date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17),
        ]

    def test_end_before_start_is_empty(self):
        assert dates_in_range(date(2026, 9, 17), date(2026, 9, 15)) == []


class TestAsDate:
    def test_plain_date_passthrough(self):
        d = date(2026, 9, 15)
        assert as_date(d) is d

    def test_timestamp_converts(self):
        ts = pd.Timestamp("2026-09-15")
        assert as_date(ts) == date(2026, 9, 15)


class TestMakeBlockGrid:
    def test_all_zero_by_default(self):
        dates = [date(2026, 9, 15), date(2026, 9, 16)]
        grid = make_block_grid(dates)
        assert list(grid["Date"]) == dates
        assert (grid.drop(columns=["Date"]) == 0.0).all().all()

    def test_populates_given_hours(self):
        d = date(2026, 9, 15)
        grid = make_block_grid([d], {d: {7: 25.0, 8: 25.0}})
        row = grid.iloc[0]
        assert row["7"] == 25.0
        assert row["8"] == 25.0
        assert row["9"] == 0.0

    def test_missing_date_defaults_to_zero(self):
        d1, d2 = date(2026, 9, 15), date(2026, 9, 16)
        grid = make_block_grid([d1, d2], {d1: {1: 10.0}})
        row2 = grid[grid["Date"] == d2].iloc[0]
        assert (row2.drop("Date") == 0.0).all()


class TestBlockGridToDateFrames:
    def test_round_trips_through_make_block_grid(self):
        d = date(2026, 9, 15)
        wide = make_block_grid([d], {d: {1: 5.0, 2: 10.0}})
        result = block_grid_to_date_frames(wide)
        assert list(result.keys()) == [d]
        long_df = result[d]
        assert list(long_df["HE"]) == HOURS
        assert long_df.loc[long_df["HE"] == 1, "MW"].iloc[0] == 5.0
        assert long_df.loc[long_df["HE"] == 2, "MW"].iloc[0] == 10.0
        assert long_df.loc[long_df["HE"] == 3, "MW"].iloc[0] == 0.0


class TestScheduleToWide:
    def test_pivots_flat_schedule(self):
        d1, d2 = date(2026, 9, 15), date(2026, 9, 16)
        schedule = [(d1, 7, 25.0), (d1, 8, 25.0), (d2, 1, 10.0)]
        wide = schedule_to_wide(schedule)
        assert list(wide["Date"]) == [d1, d2]
        row1 = wide[wide["Date"] == d1].iloc[0]
        assert row1["7"] == 25.0 and row1["8"] == 25.0 and row1["1"] == 0.0
        row2 = wide[wide["Date"] == d2].iloc[0]
        assert row2["1"] == 10.0

    def test_empty_schedule_gives_empty_frame(self):
        wide = schedule_to_wide([])
        assert len(wide) == 0
