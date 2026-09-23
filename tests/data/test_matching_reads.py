"""data/matching.py — reading the book back out for one flow date.

Named `_reads` rather than matching the module: pytest imports test files as
top-level modules here (there are no package __init__ files under tests/),
so a second `test_matching.py` would collide with the domain one.

The pure parts (the SQL's shape, the row conversion, the degrade-to-empty
behaviour) are checked here with a fake engine. The one test that opens a
real connection carries the `db` marker — see tests/README.md.
"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

import data.matching as matching
from data.matching import FLOW_DATE_SQL, _as_row, load_trades_for_flow_date

FLOW = date(2026, 9, 18)


def record(**over):
    base = {
        "Id": 42,
        "TradeDate": date(2026, 9, 17),
        "StartDate": date(2026, 9, 18),
        "StopDate": date(2026, 9, 19),
        "He": "HL",
        "MW": 100,
        "IsBuy": True,
        "price": "73",
        "PricingNode": "",
        "PorPod": "PALOVERDE500",
        "IsMonthly": False,
        "DAM_RT": "DAM",
        "MarketName": "AZPS",
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestRowConversion:
    def test_it_produces_what_domain_matching_expects(self):
        row = _as_row(record())
        assert row["trade_id"] == 42
        assert row["direction"] == "Buy"
        assert row["pse"] == "AZPS"
        assert row["por_pod"] == "PALOVERDE500"
        assert row["he"] == "HL"
        assert row["mw"] == 100.0
        assert (row["start_date"], row["stop_date"]) == (date(2026, 9, 18), date(2026, 9, 19))

    def test_is_buy_false_is_a_sell(self):
        assert _as_row(record(IsBuy=False))["direction"] == "Sell"

    def test_a_null_mw_reads_as_zero_rather_than_blowing_up(self):
        assert _as_row(record(MW=None))["mw"] == 0.0

    def test_datetime_columns_are_narrowed_to_plain_dates(self):
        # The driver returns datetime for these; comparing one against the
        # flow date downstream raises TypeError, so it has to be fixed here.
        row = _as_row(
            record(
                TradeDate=datetime(2026, 9, 17, 0, 0),
                StartDate=datetime(2026, 9, 18, 0, 0),
                StopDate=datetime(2026, 9, 19, 0, 0),
            )
        )
        assert row["trade_date"] == date(2026, 9, 17)
        assert row["start_date"] == date(2026, 9, 18)
        assert row["stop_date"] == date(2026, 9, 19)
        assert all(
            type(row[k]) is date for k in ("trade_date", "start_date", "stop_date")
        )


class TestQuery:
    def test_the_date_range_is_filtered_in_sql(self):
        # Which *hours* a row flows on the date is domain.matching's job —
        # it depends on the WECC calendar, which this query can't see.
        sql = str(FLOW_DATE_SQL)
        assert "t.StartDate <= :flow_date" in sql
        assert "t.StopDate >= :flow_date" in sql

    def test_it_joins_the_market_name_rather_than_returning_a_bare_id(self):
        sql = str(FLOW_DATE_SQL)
        assert "BilateralMarket" in sql
        assert "m.MarketName" in sql


class FakeEngine:
    def __init__(self, records=None, error=None):
        self._records = records or []
        self._error = error
        self.params = None

    def connect(self):
        if self._error:
            raise self._error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.params = params
        return self

    def fetchall(self):
        return self._records


class TestLoadTradesForFlowDate:
    def setup_method(self):
        load_trades_for_flow_date.clear()

    def teardown_method(self):
        load_trades_for_flow_date.clear()

    def test_it_returns_converted_rows(self, monkeypatch):
        engine = FakeEngine([record(), record(Id=43, IsBuy=False, MarketName="BPAT")])
        monkeypatch.setattr(matching, "get_bilateral_engine", lambda: engine)
        rows, error = load_trades_for_flow_date(FLOW)
        assert error is None
        assert [r["trade_id"] for r in rows] == [42, 43]
        assert engine.params == {"flow_date": FLOW}

    def test_an_unreachable_database_degrades_to_an_empty_book(self, monkeypatch):
        # The view still has the session's own trades to show, and losing
        # the DB shouldn't lose the page.
        monkeypatch.setattr(
            matching, "get_bilateral_engine", lambda: FakeEngine(error=OSError("no route"))
        )
        rows, error = load_trades_for_flow_date(FLOW)
        assert rows == []
        assert error is not None and "no route" in error


@pytest.mark.db
class TestAgainstTheLiveBook:
    def test_it_reads_without_error(self):
        load_trades_for_flow_date.clear()
        rows, error = load_trades_for_flow_date(date(2026, 9, 18))
        assert error is None, error
        for row in rows:
            assert row["direction"] in ("Buy", "Sell")
            assert row["start_date"] <= date(2026, 9, 18) <= row["stop_date"]
        load_trades_for_flow_date.clear()
