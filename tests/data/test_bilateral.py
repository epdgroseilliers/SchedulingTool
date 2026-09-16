"""data.bilateral — the pure pieces: He-string collapsing, schedule
compression, price/int formatting, and the shape of the generated SQL
(checked against a fake connection, no network involved).

DB-touching functions (lookups, resolve_and_validate, the real insert) are
in test_bilateral_db.py, marked `db`.
"""

import re
from datetime import date

import pytest

from data.bilateral import (
    OPTIONAL_TEXT_COLUMNS,
    compress_schedule,
    find_duplicate_id,
    format_db_price,
    he_for_hours,
    hours_to_he_string,
    insert_trade_row,
    parse_optional_int,
    whole_mw,
)


class TestHoursToHeString:
    def test_on_peak_range(self):
        assert hours_to_he_string(range(7, 23)) == "7-22"

    def test_off_peak_split(self):
        assert hours_to_he_string([1, 2, 3, 4, 5, 6, 23, 24]) == "1-6,23-24"

    def test_single_hour(self):
        assert hours_to_he_string([14]) == "14"

    def test_flat_day(self):
        assert hours_to_he_string(range(1, 25)) == "1-24"

    def test_scattered_hours(self):
        assert hours_to_he_string([1, 3, 4, 5, 9]) == "1,3-5,9"

    def test_empty(self):
        assert hours_to_he_string([]) == ""

    def test_unsorted_with_duplicates(self):
        assert hours_to_he_string([5, 3, 4, 3]) == "3-5"


class TestHeForHours:
    def test_hl_label_from_matching_hours(self):
        assert he_for_hours(range(7, 23), "HL") == "HL"

    def test_ll_label_from_off_peak_split(self):
        assert he_for_hours([1, 2, 3, 4, 5, 6, 23, 24], "LL") == "LL"

    def test_ll_label_from_full_day(self):
        assert he_for_hours(range(1, 25), "LL") == "LL"

    def test_atc_label_from_full_day(self):
        assert he_for_hours(range(1, 25), "ATC") == "ATC"

    def test_hand_edited_hours_fall_back_to_explicit_range(self):
        assert he_for_hours(range(7, 22), "HL") == "7-21"

    def test_no_label_gives_explicit_range(self):
        assert he_for_hours(range(7, 23), None) == "7-22"


class TestCompressSchedule:
    def test_uniform_block_collapses_to_one_row(self):
        d1, d2, d3 = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
        schedule = [(d, he, 25.0) for d in (d1, d2, d3) for he in range(7, 23)]
        assert compress_schedule(schedule, "HL") == [
            {"start_date": d1, "stop_date": d3, "he": "HL", "mw": 25.0}
        ]

    def test_hand_edit_splits_and_delabels_only_the_edited_day(self):
        d1, d2, d3 = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
        schedule = [(d, he, 25.0) for d in (d1, d2, d3) for he in range(7, 23)]
        schedule = [
            (d, he, 50.0 if (d == d2 and he == 10) else mw) for d, he, mw in schedule
        ]
        assert compress_schedule(schedule, "HL") == [
            {"start_date": d1, "stop_date": d1, "he": "HL", "mw": 25.0},
            {"start_date": d2, "stop_date": d2, "he": "7-9,11-22", "mw": 25.0},
            {"start_date": d2, "stop_date": d2, "he": "10", "mw": 50.0},
            {"start_date": d3, "stop_date": d3, "he": "HL", "mw": 25.0},
        ]

    def test_gap_in_dates_does_not_merge(self):
        d1, d3 = date(2026, 9, 7), date(2026, 9, 9)
        schedule = [(d, he, 25.0) for d in (d1, d3) for he in range(7, 23)]
        rows = compress_schedule(schedule, "HL")
        assert [r["start_date"] for r in rows] == [d1, d3]
        assert [r["stop_date"] for r in rows] == [d1, d3]

    def test_a_b_a_pattern_produces_three_rows(self):
        d1, d2, d3 = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
        schedule = (
            [(d1, he, 25.0) for he in range(7, 23)]
            + [(d2, he, 40.0) for he in range(7, 23)]
            + [(d3, he, 25.0) for he in range(7, 23)]
        )
        rows = compress_schedule(schedule, "HL")
        assert [(r["start_date"], r["mw"]) for r in rows] == [
            (d1, 25.0), (d2, 40.0), (d3, 25.0),
        ]

    def test_zero_mw_hours_are_dropped(self):
        d = date(2026, 9, 7)
        schedule = [(d, 1, 0.0), (d, 2, 10.0), (d, 3, 0.0)]
        assert compress_schedule(schedule) == [
            {"start_date": d, "stop_date": d, "he": "2", "mw": 10.0}
        ]

    def test_off_peak_overnight_spans_midnight_label(self):
        d1, d2 = date(2026, 9, 7), date(2026, 9, 8)
        schedule = [(d, he, 25.0) for d in (d1, d2) for he in [1, 2, 3, 4, 5, 6, 23, 24]]
        assert compress_schedule(schedule, "LL") == [
            {"start_date": d1, "stop_date": d2, "he": "LL", "mw": 25.0}
        ]

    def test_empty_schedule(self):
        assert compress_schedule([]) == []

    def test_no_shape_label_uses_explicit_ranges(self):
        d = date(2026, 9, 7)
        schedule = [(d, he, 25.0) for he in range(7, 23)]
        rows = compress_schedule(schedule)
        assert rows == [{"start_date": d, "stop_date": d, "he": "7-22", "mw": 25.0}]


class TestFormatDbPrice:
    def test_index_positive_premium(self):
        assert format_db_price("MIDC", 0.25) == "x+0.25"

    def test_index_whole_premium(self):
        assert format_db_price("MIDC", 1.0) == "x+1"

    def test_index_negative_premium(self):
        assert format_db_price("MIDC", -1.5) == "x-1.5"

    def test_index_zero_premium(self):
        assert format_db_price("MIDC", 0.0) == "x"

    def test_fixed_price(self):
        assert format_db_price(None, 40.0) == "40"

    def test_fixed_price_with_decimal(self):
        assert format_db_price(None, 40.25) == "40.25"


class TestParseOptionalInt:
    def test_blank_string_is_none(self):
        assert parse_optional_int("", "X") == (None, None)

    def test_none_value_is_none(self):
        assert parse_optional_int(None, "X") == (None, None)

    def test_valid_int_string(self):
        assert parse_optional_int("123", "X") == (123, None)

    def test_non_numeric_is_an_error(self):
        value, error = parse_optional_int("RS1", "ResupplyId")
        assert value is None
        assert error is not None and "ResupplyId" in error


class TestWholeMw:
    def test_whole_number(self):
        assert whole_mw(25.0, "x") == (25, None)

    def test_fractional_is_an_error(self):
        value, error = whole_mw(25.5, "x")
        assert value is None
        assert error is not None and "25.5" in error


class FakeConn:
    """Captures the statement and params instead of executing them."""

    def __init__(self, scalar_result=4242):
        self.sql = None
        self.params = None
        self._scalar_result = scalar_result

    def execute(self, sql, params=None):
        self.sql = str(sql)
        self.params = params
        return self

    def scalar(self):
        return self._scalar_result


BASE_ROW = {
    "trade_date": date(2026, 9, 7),
    "start_date": date(2026, 9, 8),
    "stop_date": date(2026, 9, 10),
    "he": "7-22",
    "time_zone": "PPT",
    "is_monthly": 0,
    "is_buy": 1,
    "market_id": 12,
    "mw": 25.0,
    "price": "MIDC+2.50",
    "pricing_node": "MIDC",
    "por_pod": "MIDC",
    "communication": "ICE",
    "wspp": "C",
    "is_source_non_caiso": 0,
    "specified_source_id": None,
    "contract_type": "WSPP",
    "is_non_washington_sink": 0,
    "resupply_id": None,
    "secondary_por_pod": "",
    "resource_adequacy_id": "",
    "exchange_id": None,
    "is_option": 0,
    "dam_rt": "DAM",
}


def _columns_and_values(sql):
    m = re.search(r"\((.*?)\)\s*OUTPUT INSERTED\.Id INTO @new\s*VALUES\s*\((.*)\);", sql, re.S)
    assert m, f"could not parse SQL:\n{sql}"
    cols = [c.strip() for c in m.group(1).split(",")]
    vals = [v.strip() for v in m.group(2).split(",")]
    return cols, vals


class TestInsertTradeRowSql:
    def test_returns_the_output_id(self):
        conn = FakeConn(scalar_result=4242)
        assert insert_trade_row(conn, dict(BASE_ROW)) == 4242

    def test_output_goes_into_a_table_variable_not_bare(self):
        # BilateralTrades has AFTER triggers; a bare OUTPUT clause is
        # rejected by SQL Server on such a table (error 334).
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        assert "OUTPUT INSERTED.Id INTO @new" in conn.sql
        assert "OUTPUT INSERTED.Id VALUES" not in conn.sql

    def test_set_nocount_on_present(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        assert conn.sql.startswith("SET NOCOUNT ON;")

    def test_id_is_not_in_the_column_list(self):
        # Id has a default backed by a sequence — the server assigns it.
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        cols, _ = _columns_and_values(conn.sql)
        assert "Id" not in cols

    def test_column_and_value_counts_match(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        cols, vals = _columns_and_values(conn.sql)
        assert len(cols) == len(vals)

    def test_optional_columns_omitted_when_blank(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        cols, _ = _columns_and_values(conn.sql)
        for column in OPTIONAL_TEXT_COLUMNS.values():
            assert column not in cols

    def test_optional_columns_included_when_set(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(
            BASE_ROW, resupply_id=7, exchange_id=9,
            secondary_por_pod="MEAD230", resource_adequacy_id="RA1",
        ))
        cols, vals = _columns_and_values(conn.sql)
        for column in OPTIONAL_TEXT_COLUMNS.values():
            assert column in cols
        assert len(cols) == len(vals)
        assert conn.params["resupply_id"] == 7
        assert conn.params["exchange_id"] == 9

    def test_is_option_and_dam_rt_always_present(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        cols, _ = _columns_and_values(conn.sql)
        assert cols[-2:] == ["isOption", "DAM_RT"]

    def test_monthly_trade_binds_dam_rt_as_none(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW, is_monthly=1, dam_rt=None))
        assert conn.params["dam_rt"] is None

    def test_every_placeholder_is_bound_and_no_stray_params(self):
        conn = FakeConn()
        insert_trade_row(conn, dict(BASE_ROW))
        cols, vals = _columns_and_values(conn.sql)
        placeholders = {v[1:] for v in vals if v.startswith(":")}
        assert placeholders == set(conn.params)


class TestFindDuplicateIdSql:
    def test_binds_exactly_the_core_terms(self):
        conn = FakeConn()
        find_duplicate_id(conn, dict(BASE_ROW))
        core = {
            "trade_date", "start_date", "stop_date", "he", "time_zone",
            "is_buy", "market_id", "por_pod", "mw", "price",
        }
        assert set(conn.params) == core

    def test_is_a_top_1_select(self):
        conn = FakeConn()
        find_duplicate_id(conn, dict(BASE_ROW))
        assert "TOP 1 Id" in conn.sql
