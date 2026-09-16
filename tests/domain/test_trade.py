"""domain.trade — pure trade-level rules: formatting, the DAM/RT date
default, and the DB-input validation ported from the legacy macro."""

from datetime import date

from domain.trade import (
    backoffice_summary,
    db_input_errors,
    db_input_warnings,
    default_block_start,
    format_price,
    rare_fields_set,
)


class TestFormatPrice:
    def test_fixed_price(self):
        assert format_price(None, 40.0) == "40.00"

    def test_fixed_negative(self):
        assert format_price(None, -1.5) == "-1.50"

    def test_index_priced_positive_premium(self):
        assert format_price("MIDC", 2.5) == "MIDC+2.50"

    def test_index_priced_negative_premium(self):
        assert format_price("MIDC", -1.0) == "MIDC-1.00"

    def test_index_priced_zero_premium(self):
        assert format_price("MIDC", 0.0) == "MIDC+0.00"


class TestRareFieldsSet:
    def test_all_default_is_empty(self):
        from domain.options import RARE_FIELD_DEFAULTS
        assert rare_fields_set(dict(RARE_FIELD_DEFAULTS)) == []

    def test_off_default_fields_are_reported(self):
        from domain.options import RARE_FIELD_DEFAULTS
        values = dict(RARE_FIELD_DEFAULTS)
        values["is_option"] = True
        values["exchange_id"] = "EX1"
        assert set(rare_fields_set(values)) == {"is_option", "exchange_id"}


class TestDefaultBlockStart:
    def test_dam_is_next_day(self):
        d = date(2026, 9, 15)
        assert default_block_start(d, True) == date(2026, 9, 16)

    def test_real_time_is_trade_date(self):
        d = date(2026, 9, 15)
        assert default_block_start(d, False) == d


class TestDbInputErrors:
    def _trade(self, **overrides):
        base = {
            "wspp_contract": "C",
            "location": "PALOVERDE500",
            "trade_date": date(2026, 9, 15),
        }
        base.update(overrides)
        return base

    def test_no_errors_for_a_clean_trade(self):
        rows = [{"start_date": date(2026, 9, 15), "stop_date": date(2026, 9, 15)}]
        assert db_input_errors(self._trade(), rows, past_dated=False, past_confirmed=False) == []

    def test_missing_wspp_contract(self):
        errors = db_input_errors(
            self._trade(wspp_contract=""), [], past_dated=False, past_confirmed=False
        )
        assert any("WSPP" in e for e in errors)

    def test_paloverde_typo(self):
        errors = db_input_errors(
            self._trade(location="PALOVERDE"), [], past_dated=False, past_confirmed=False
        )
        assert any("PALOVERDE500" in e for e in errors)

    def test_past_dated_without_confirmation_blocks(self):
        errors = db_input_errors(self._trade(), [], past_dated=True, past_confirmed=False)
        assert any("past" in e.lower() for e in errors)

    def test_past_dated_with_confirmation_does_not_block(self):
        errors = db_input_errors(self._trade(), [], past_dated=True, past_confirmed=True)
        assert not any("past" in e.lower() for e in errors)

    def test_row_start_before_trade_date(self):
        rows = [{"start_date": date(2026, 9, 10), "stop_date": date(2026, 9, 10)}]
        errors = db_input_errors(self._trade(), rows, past_dated=False, past_confirmed=False)
        assert any("before the trade date" in e for e in errors)

    def test_row_stop_before_start(self):
        rows = [{"start_date": date(2026, 9, 16), "stop_date": date(2026, 9, 15)}]
        errors = db_input_errors(self._trade(), rows, past_dated=False, past_confirmed=False)
        assert any("before start date" in e for e in errors)


class TestDbInputWarnings:
    def _trade(self, **overrides):
        base = {"wspp_contract": "C", "location": "PALOVERDE500", "index": None}
        base.update(overrides)
        return base

    def test_no_warnings_for_a_clean_trade(self):
        assert db_input_warnings(self._trade()) == []

    def test_schedule_b_warns(self):
        warnings = db_input_warnings(self._trade(wspp_contract="B"))
        assert any("Schedule B" in w for w in warnings)

    def test_incoherent_pricing_node_warns(self):
        warnings = db_input_warnings(self._trade(index="PALOVERDE", location="MIDC"))
        assert any("may not be coherent" in w for w in warnings)

    def test_coherent_pricing_node_does_not_warn(self):
        warnings = db_input_warnings(self._trade(index="PALOVERDE", location="PALOVERDE500"))
        assert warnings == []


class TestBackofficeSummary:
    def test_includes_always_shown_fields(self):
        t = {
            "time_zone": "PPT", "communication": "ICE Chat", "wspp_contract": "C",
            "specified_source": None, "is_nws": False, "is_dam": True,
            "is_source_non_caiso": False,
        }
        summary = backoffice_summary(t)
        assert "TZ: PPT" in summary
        assert "Comm: ICE Chat" in summary
        assert "WSPP: C" in summary
        assert "Source: —" in summary
        assert "IsNWS: N" in summary
        assert "IsDAM: Y" in summary

    def test_rare_field_off_default_is_appended(self):
        t = {
            "time_zone": "PPT", "communication": None, "wspp_contract": "C",
            "specified_source": None, "is_nws": False, "is_dam": True,
            "is_source_non_caiso": False, "is_option": True,
        }
        summary = backoffice_summary(t)
        assert "IsOption: Y" in summary

    def test_rare_field_at_default_is_not_appended(self):
        t = {
            "time_zone": "PPT", "communication": None, "wspp_contract": "C",
            "specified_source": None, "is_nws": False, "is_dam": True,
            "is_source_non_caiso": False,
        }
        summary = backoffice_summary(t)
        assert "IsOption" not in summary
