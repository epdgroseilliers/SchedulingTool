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
    parse_pasted_schedule,
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


class TestParsePastedSchedule:
    """A column of MW copied out of Excel. All-or-nothing, like the broker
    string: half a schedule silently applied is worse than a refusal."""

    ONE_DAY = [date(2026, 9, 27)]
    TWO_DAYS = [date(2026, 9, 27), date(2026, 9, 28)]
    # The shape from the desk's own screenshot.
    SHAPE = [16, 19, 21, 21, 22, 21, 19, 15, 12, 9, 9, 10,
             13, 18, 24, 32, 40, 40, 40, 40, 40, 40, 40, 40]

    def _text(self, values):
        return "\n".join(str(v) for v in values)

    def test_twenty_four_values_fill_one_day(self):
        mw, error = parse_pasted_schedule(self._text(self.SHAPE), self.ONE_DAY)
        assert error is None
        assert mw[self.ONE_DAY[0]] == dict(zip(HOURS, [float(v) for v in self.SHAPE]))

    def test_one_day_of_values_repeats_on_every_date(self):
        # The common case for a multi-day block: the same variable shape
        # each day, so it's pasted once.
        mw, error = parse_pasted_schedule(self._text(self.SHAPE), self.TWO_DAYS)
        assert error is None
        assert mw[self.TWO_DAYS[0]] == mw[self.TWO_DAYS[1]]

    def test_a_full_grid_of_values_goes_day_by_day_in_order(self):
        second = [v + 1 for v in self.SHAPE]
        mw, error = parse_pasted_schedule(
            self._text(self.SHAPE + second), self.TWO_DAYS
        )
        assert error is None
        assert mw[self.TWO_DAYS[0]][1] == 16
        assert mw[self.TWO_DAYS[1]][1] == 17

    def test_tabs_count_as_separators_too(self):
        # Excel puts tabs on the clipboard for a row selection.
        mw, error = parse_pasted_schedule("\t".join(str(v) for v in self.SHAPE), self.ONE_DAY)
        assert error is None and mw[self.ONE_DAY[0]][24] == 40

    def test_blank_lines_and_padding_are_ignored(self):
        text = "\n  ".join([""] + [str(v) for v in self.SHAPE] + ["", "  "])
        mw, error = parse_pasted_schedule(text, self.ONE_DAY)
        assert error is None and len(mw[self.ONE_DAY[0]]) == 24

    def test_a_thousands_separator_is_part_of_the_number(self):
        # "1,200" is one value, not two — which is why commas never split.
        values = ["1,200"] + [str(v) for v in self.SHAPE[1:]]
        mw, error = parse_pasted_schedule("\n".join(values), self.ONE_DAY)
        assert error is None and mw[self.ONE_DAY[0]][1] == 1200.0

    def test_decimals_survive(self):
        values = [12.5] + self.SHAPE[1:]
        mw, error = parse_pasted_schedule(self._text(values), self.ONE_DAY)
        assert error is None and mw[self.ONE_DAY[0]][1] == 12.5

    def test_nothing_pasted_is_an_error(self):
        mw, error = parse_pasted_schedule("   \n  ", self.ONE_DAY)
        assert mw is None and "Nothing pasted" in error

    def test_a_non_number_is_named_in_the_error(self):
        values = [str(v) for v in self.SHAPE[:-1]] + ["forty"]
        mw, error = parse_pasted_schedule("\n".join(values), self.ONE_DAY)
        assert mw is None and "forty" in error

    def test_a_negative_is_refused(self):
        values = [-5] + self.SHAPE[1:]
        mw, error = parse_pasted_schedule(self._text(values), self.ONE_DAY)
        assert mw is None and "negative" in error

    def test_the_wrong_count_is_refused_rather_than_guessed(self):
        # 16 values could be HE7-22 or HE1-16; guessing wrong moves a
        # trade's energy to the wrong hours.
        mw, error = parse_pasted_schedule(self._text(self.SHAPE[:16]), self.ONE_DAY)
        assert mw is None
        assert "16" in error and "24" in error

    def test_the_error_names_both_counts_a_multi_day_block_accepts(self):
        mw, error = parse_pasted_schedule(self._text(self.SHAPE[:10]), self.TWO_DAYS)
        assert mw is None and "24" in error and "48" in error
