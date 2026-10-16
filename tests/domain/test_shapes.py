"""domain.shapes — shape parsing is pure; build_schedule's on_peak/off_peak
branches need the live WECC calendar (marked db), flat/custom don't."""

from datetime import date

import pytest

from domain.shapes import (
    build_schedule,
    combined_block_grid,
    generate_block_grid,
    monthly_block_rows,
    parse_shape,
    segments_share_one_block,
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


class TestTheTwoSingleDayProducts:
    """1x16 and 1x8 look like HL and LL and deliberately aren't them: HL
    and LL ask the WECC calendar what kind of day it is, these name their
    hours outright. That is the whole reason the desk writes them — they
    are used on one named day, weekend days included."""

    SATURDAY = date(2026, 10, 17)

    @pytest.mark.parametrize("written,kind", [
        ("1x16", "1x16"), ("1X16", "1x16"), ("1 x 16", "1x16"),
        ("1x8", "1x8"), ("1X8", "1x8"), ("1 X 8", "1x8"),
    ])
    def test_how_they_are_written(self, written, kind):
        assert parse_shape(written)[0] == kind

    def test_1x16_is_the_sixteen_on_peak_hours(self):
        hours, _, _ = build_schedule(
            self.SATURDAY, self.SATURDAY, "1x16", 25.0
        )
        assert hours[self.SATURDAY] == set(range(7, 23))

    def test_1x8_is_the_eight_off_peak_ones(self):
        hours, _, _ = build_schedule(self.SATURDAY, self.SATURDAY, "1x8", 25.0)
        assert hours[self.SATURDAY] == set(range(1, 7)) | {23, 24}

    def test_neither_asks_the_calendar_anything(self):
        # Which is the point. An HL leg flows *no* hours on a Saturday and
        # an LL leg flows all twenty-four; a 1x16 on a Saturday is sixteen
        # hours on Saturday. This test runs with no database at all.
        hours, excluded, missing = build_schedule(
            self.SATURDAY, self.SATURDAY, "1x16", 25.0
        )
        assert not excluded and not missing

    def test_they_are_stored_as_the_hours_they_cover(self):
        # Never as "1x16": the He column's HL and LL mean "whatever the
        # calendar says", and these mean exactly these hours.
        assert shape_to_he("1x16") == "7-22"
        assert shape_to_he("1x8") == "1-6,23-24"
        assert shape_label("1x16") is None
        assert shape_label("1x8") is None


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


def seg(mw, shape):
    return {"mw": mw, "shape": shape}


class TestWhenQuantitiesShareOneBlock:
    """A broker string naming a quantity more than once is usually one
    block whose profile steps — the Shape and MW boxes only seed the
    grid, and the grid is what gets written."""

    def test_hour_ranges_share_one(self):
        assert segments_share_one_block([seg(150, "9-12"), seg(100, "13-14")])

    def test_and_so_do_the_single_day_products(self):
        # 1x16 and 1x8 store their hours either way, so a block of their
        # own buys nothing.
        assert segments_share_one_block([seg(50, "1x16"), seg(25, "1x8")])

    def test_a_lone_quantity_trivially_does(self):
        assert segments_share_one_block([seg(50, "HL")])
        assert segments_share_one_block([])

    def test_but_hl_and_ll_each_need_their_own(self):
        # The block's Shape is what stores the row as the word rather
        # than the hours, and "HL" is not "7-22" on an off-peak day.
        assert not segments_share_one_block([seg(50, "HL"), seg(25, "LL")])
        assert not segments_share_one_block([seg(50, "ATC"), seg(25, "9-12")])

    def test_and_so_do_quantities_whose_hours_overlap(self):
        # No one profile to build: one would overwrite the other's MW
        # without saying so.
        assert not segments_share_one_block([seg(50, "1-6"), seg(25, "5-8")])
        assert not segments_share_one_block([seg(50, "1x16"), seg(25, "9-12")])

    def test_an_unreadable_shape_is_not_merged_over(self):
        assert not segments_share_one_block([seg(50, "9-12"), seg(25, "banana")])


class TestTheGridOfABlockThatSteps:
    def test_each_quantity_lands_on_its_own_hours(self):
        days = [date(2026, 9, 15), date(2026, 9, 16)]
        grid, error = combined_block_grid(
            days[0], days[-1], days, [seg(150, "9-12"), seg(100, "13-14")]
        )
        assert error is None
        for _, row in grid.iterrows():
            assert [h for h in range(1, 25) if row[str(h)] > 0] == [9, 10, 11, 12, 13, 14]
            assert (row["9"], row["13"]) == (150.0, 100.0)

    def test_it_needs_no_calendar_for_the_shapes_that_name_their_hours(self):
        # 1x16 on a Saturday is sixteen hours on Saturday. Nothing here
        # asks the WECC calendar anything, which is why this test is not
        # marked db.
        sat = date(2026, 9, 19)
        grid, error = combined_block_grid(
            sat, sat, [sat], [seg(50, "1x16"), seg(25, "1x8")]
        )
        assert error is None
        row = grid.iloc[0]
        assert row["12"] == 50.0 and row["3"] == 25.0

    def test_a_bad_shape_is_reported_rather_than_half_built(self):
        d = date(2026, 9, 15)
        grid, error = combined_block_grid(d, d, [d], [seg(50, "9-12"), seg(25, "nope")])
        assert grid is None and "nope" in error
