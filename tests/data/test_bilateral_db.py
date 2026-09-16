"""data.bilateral — functions that need a live, read-only connection to
MAGAPPSERVER (PhysiqueBilateral). Never calls insert_trade: these tests
must not write anything to the compliance database. Run with --run-db.
"""

from datetime import date

import pytest

from data.bilateral import (
    compress_schedule,
    format_db_price,
    load_market_full_names,
    resolve_and_validate,
)

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def known_trade():
    """A trade against a counterparty/location known to exist in the real
    BilateralMarket table — resolve_and_validate needs live lookups to
    succeed for the happy-path test."""
    d1, d2 = date(2026, 9, 8), date(2026, 9, 9)
    schedule = [(d, he, 25.0) for d in (d1, d2) for he in range(7, 23)]
    rows = compress_schedule(schedule, "HL")
    trade = {
        "trade_date": date(2026, 9, 7),
        "direction": "Buy",
        "counterparty": "AZPS",
        "location": "PALOVERDE500",
        "index": "PALOVERDE",
        "time_zone": "PPT",
        "communication": "ICE",
        "wspp_contract": "C",
        "specified_source": None,
        "is_nws": False,
        "is_source_non_caiso": False,
        "is_monthly": False,
        "is_option": False,
        "resupply_id": "",
        "secondary_por_pod": "",
        "resource_adequacy_id": "",
        "exchange_id": "",
        "is_dam": True,
        "price_text": format_db_price("PALOVERDE", 0.25),
    }
    return rows, trade


class TestLoadMarketFullNames:
    def test_returns_a_populated_mapping(self):
        names = load_market_full_names()
        assert isinstance(names, dict)
        assert names  # the real table has ~90 rows
        assert names.get("AZPS") == "Arizona Public Service Company"

    def test_caiso_is_present(self):
        # Confirms CAISO is a real BilateralMarket entry, not just a
        # PROJECT.md assumption for the Phase 2 "market" concept.
        names = load_market_full_names()
        assert names.get("CAISO") is not None


class TestResolveAndValidate:
    def test_known_counterparty_resolves_and_returns_no_errors(self, known_trade):
        rows, trade = known_trade
        resolved, errors = resolve_and_validate(rows, trade)
        assert errors == []
        assert len(resolved) == len(rows)
        for row in resolved:
            assert row["market_id"] is not None
            assert row["contract_type"]

    def test_unknown_counterparty_errors_cleanly(self, known_trade):
        rows, trade = known_trade
        bad_trade = dict(trade, counterparty="ZZZ_NOT_A_REAL_COUNTERPARTY")
        resolved, errors = resolve_and_validate(rows, bad_trade)
        assert resolved == []
        assert any("not recognized" not in e and "MarketName" in e for e in errors)

    def test_unknown_specified_source_errors_cleanly(self, known_trade):
        rows, trade = known_trade
        bad_trade = dict(trade, specified_source="Not A Real Source At All")
        resolved, errors = resolve_and_validate(rows, bad_trade)
        assert resolved == []
        assert any("SourceName" in e for e in errors)

    def test_fractional_mw_is_caught_before_any_db_call(self, known_trade):
        rows, trade = known_trade
        bad_rows = [dict(rows[0], mw=25.5)]
        resolved, errors = resolve_and_validate(bad_rows, trade)
        assert resolved == []
        assert any("whole MW" in e for e in errors)

    def test_non_numeric_optional_int_is_caught(self, known_trade):
        rows, trade = known_trade
        bad_trade = dict(trade, resupply_id="RS1")
        resolved, errors = resolve_and_validate(rows, bad_trade)
        assert any("whole number" in e for e in errors)
