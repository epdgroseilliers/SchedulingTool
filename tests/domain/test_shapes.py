"""domain.shapes — shape parsing is pure; build_schedule's on_peak/off_peak
branches need the live WECC calendar (marked db), flat/custom don't."""

from datetime import date, timedelta

import pytest

from domain.shapes import (
    DAM_DEFAULT_LOOKAHEAD_DAYS,
    build_schedule,
    dam_default_end_date,
    generate_block_grid,
    monthly_block_rows,
    parse_shape,
    shape_label,
    shape_to_he,
)


class TestParseShape:
    @pytest.mark.parametrize("alias,kind", [
        ("HL", "on_peak"), ("hl", "on_peak"), ("on-peak", "on_peak"), ("ONPEAK", "on_peak"),
        ("LL", "off_peak"), ("off-peak", "off_peak"), ("OFFPEAK", "off_peak"),
        ("ATC", "flat"), ("flat", "flat"), ("24h", "flat"),
    ])
    def test_known_aliases(self, alias, kind):
        assert parse_shape(alias) == (kind, None, None)

    def test_hour_range(self):
        assert parse_shape("7-22") == ("custom", 7, 22)

    def test_hour_range_with_spaces(self):
        assert parse_shape(" 7 - 22 ") == ("custom", 7, 22)

    def test_single_hour(self):
        assert parse_shape("14") == ("custom", 14, 14)

    def test_blank_raises(self):
        with pytest.raises(ValueError):
            parse_shape("")
        with pytest.raises(ValueError):
            parse_shape(None)

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_shape("banana")

    def test_out_of_range_hour_raises(self):
        with pytest.raises(ValueError):
            parse_shape("0-25")

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError):
            parse_shape("22-7")


class TestShapeLabel:
    def test_hl_ll_atc_map_to_themselves(self):
        assert shape_label("HL") == "HL"
        assert shape_label("LL") == "LL"
        assert shape_label("ATC") == "ATC"

    def test_custom_range_has_no_label(self):
        assert shape_label("7-22") is None

    def test_invalid_shape_has_no_label(self):
        assert shape_label("garbage") is None


class TestBuildScheduleFlatAndCustom:
    def test_flat_covers_every_hour_every_day(self):
        start, end = date(2026, 9, 15), date(2026, 9, 16)
        hours_by_date, excluded, missing = build_schedule(start, end, "flat", 25)
        assert excluded == [] and missing == []
        assert hours_by_date[start] == set(range(1, 25))
        assert hours_by_date[end] == set(range(1, 25))

    def test_custom_covers_the_given_range_every_day(self):
        start, end = date(2026, 9, 15), date(2026, 9, 16)
        hours_by_date, excluded, missing = build_schedule(start, end, "custom", 25, 7, 10)
        assert hours_by_date[start] == {7, 8, 9, 10}
        assert hours_by_date[end] == {7, 8, 9, 10}
        assert excluded == [] and missing == []

    def test_single_day(self):
        d = date(2026, 9, 15)
        hours_by_date, _, _ = build_schedule(d, d, "flat", 25)
        assert set(hours_by_date) == {d}


class TestGenerateBlockGridBadShape:
    def test_bad_shape_reports_error_not_exception(self):
        start = end = date(2026, 9, 15)
        grid, excluded, missing, error = generate_block_grid(
            start, end, [start], "garbage", 25
        )
        assert grid is None
        assert error is not None
        assert "garbage" in error


class TestGenerateBlockGridCustom:
    def test_custom_shape_builds_grid_without_db(self):
        start, end = date(2026, 9, 15), date(2026, 9, 16)
        dates = [start, end]
        grid, excluded, missing, error = generate_block_grid(
            start, end, dates, "7-9", 25
        )
        assert error is None
        assert excluded == [] and missing == []
        row = grid[grid["Date"] == start].iloc[0]
        assert row["7"] == 25.0 and row["8"] == 25.0 and row["9"] == 25.0
        assert row["10"] == 0.0


class TestShapeToHe:
    """The He text for a monthly-style row that skips the hourly grid —
    stores the shape as written, no WECC calendar lookup involved."""

    def test_hl_ll_atc_map_to_their_label(self):
        assert shape_to_he("HL") == "HL"
        assert shape_to_he("LL") == "LL"
        assert shape_to_he("atc") == "ATC"

    def test_custom_range_is_written_plainly(self):
        assert shape_to_he("7-22") == "7-22"

    def test_single_hour_has_no_dash(self):
        assert shape_to_he("14") == "14"

    def test_invalid_shape_raises(self):
        with pytest.raises(ValueError):
            shape_to_he("garbage")


class TestMonthlyBlockRows:
    def test_one_row_per_block_no_hourly_expansion(self):
        blocks = [
            (date(2027, 7, 1), date(2027, 9, 30), "HL", 75),
            (date(2027, 1, 1), date(2027, 3, 31), "7-22", 10),
        ]
        rows, errors = monthly_block_rows(blocks)
        assert errors == []
        assert rows == [
            {"start_date": date(2027, 7, 1), "stop_date": date(2027, 9, 30), "he": "HL", "mw": 75.0},
            {"start_date": date(2027, 1, 1), "stop_date": date(2027, 3, 31), "he": "7-22", "mw": 10.0},
        ]

    def test_start_after_end_is_an_error_and_skips_the_row(self):
        blocks = [(date(2027, 9, 30), date(2027, 7, 1), "HL", 75)]
        rows, errors = monthly_block_rows(blocks)
        assert rows == []
        assert len(errors) == 1
        assert "on or before" in errors[0]

    def test_bad_shape_is_an_error_and_skips_the_row(self):
        blocks = [(date(2027, 7, 1), date(2027, 9, 30), "garbage", 75)]
        rows, errors = monthly_block_rows(blocks)
        assert rows == []
        assert "garbage" in errors[0]

    def test_one_bad_block_does_not_drop_the_others(self):
        blocks = [
            (date(2027, 7, 1), date(2027, 9, 30), "HL", 75),
            (date(2027, 1, 1), date(2027, 3, 31), "garbage", 10),
        ]
        rows, errors = monthly_block_rows(blocks)
        assert len(rows) == 1
        assert rows[0]["he"] == "HL"
        assert len(errors) == 1


class TestDamDefaultEndDateInvalidShape:
    """An unparseable shape can't be reasoned about at all, so the default
    End Date is always just the Start Date — and, since that's decided
    before ever consulting the calendar, this doesn't need the DB."""

    def test_invalid_shape_stays_a_single_day(self):
        d = date(2026, 9, 18)
        assert dam_default_end_date(d, "garbage") == d


@pytest.mark.db
class TestDamDefaultEndDateCalendarDriven:
    """Every shape (HL, LL, ATC, or a custom hour range) extends the
    default End Date through the WECC calendar's run of consecutive days
    sharing the Start Date's own peak/off-peak status — e.g. a peak Friday
    immediately followed by a peak Saturday belongs to the same DAM leg.
    This is a *default* only: an explicit date the parser read from the
    string (a weekday, or a literal MM/DD) always overrides it — see
    ui.paste.apply_parsed_string. Checked against the live calendar itself
    rather than hardcoded exact dates, so this doesn't rot as the calendar
    (which is real, mutable data) changes over time.
    """

    def test_extends_through_the_run_of_matching_peak_status(self):
        from data.calendar import is_peak_map

        start = date(2026, 9, 15)
        end = dam_default_end_date(start, "HL")
        assert end >= start

        peak_map = is_peak_map(start, end + timedelta(days=1))
        start_is_peak = peak_map.get(start)
        d = start
        while d <= end:
            assert peak_map.get(d) == start_is_peak
            d += timedelta(days=1)

        # The day after `end` must break the run — differ from the start
        # day's status, or be missing from the calendar — unless the
        # lookahead window itself was exhausted first.
        after_status = peak_map.get(end + timedelta(days=1))
        assert after_status != start_is_peak or (
            (end - start).days >= DAM_DEFAULT_LOOKAHEAD_DAYS
        )

    def test_hl_ll_atc_and_custom_extend_through_the_same_run(self):
        # Which hours get used differs (on-peak, off-peak, every hour, or
        # an explicit hour range), but all four follow the same day-by-day
        # peak/off-peak continuity for the *date range* itself.
        start = date(2026, 9, 18)
        hl_end = dam_default_end_date(start, "HL")
        assert dam_default_end_date(start, "LL") == hl_end
        assert dam_default_end_date(start, "ATC") == hl_end
        assert dam_default_end_date(start, "7-22") == hl_end

    def test_missing_calendar_data_falls_back_to_a_single_day(self):
        far_future = date(2099, 1, 1)
        assert dam_default_end_date(far_future, "HL") == far_future


@pytest.mark.db
class TestBuildScheduleOnOffPeak:
    """HL/LL consult the live WECC calendar to split peak/off-peak hours."""

    def test_on_peak_uses_7_to_22_on_a_peak_day(self):
        # A midweek day is virtually always a WECC peak day.
        d = date(2026, 9, 15)  # Tuesday
        hours_by_date, excluded, missing = build_schedule(d, d, "on_peak", 25)
        assert not missing
        if d not in excluded:
            assert hours_by_date[d] == set(range(7, 23))

    def test_off_peak_is_the_complement_of_on_peak(self):
        d = date(2026, 9, 15)
        on_hours, on_excluded, on_missing = build_schedule(d, d, "on_peak", 25)
        off_hours, _, off_missing = build_schedule(d, d, "off_peak", 25)
        assert not on_missing and not off_missing
        if d not in on_excluded:
            assert on_hours[d] | off_hours[d] == set(range(1, 25))
            assert on_hours[d] & off_hours[d] == set()

    def test_generate_block_grid_hl_shape_reaches_the_calendar(self):
        d = date(2026, 9, 15)
        grid, excluded, missing, error = generate_block_grid(d, d, [d], "HL", 25)
        assert error is None
        assert not missing
