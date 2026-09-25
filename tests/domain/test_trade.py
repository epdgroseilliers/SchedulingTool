"""domain.trade — pure trade-level rules: formatting, the DAM/RT date
default, and the DB-input validation ported from the legacy macro."""

from datetime import date, timedelta

from domain.trade import (
    backoffice_summary,
    db_input_errors,
    db_input_warnings,
    default_block_start,
    default_flow_window,
    has_trading_session,
    format_price,
    rare_fields_set,
    session_horizon,
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


class TestDefaultFlowWindow:
    """The flow dates a fresh block defaults to, read from the WECC
    calendar's own pairing of trading sessions to flow dates rather than
    inferred. Every case below is a real September 2026 session — the
    desk reported each one.
    """

    #: What PhysiqueWest.dbo.WECC_PowerCalendar_Detailed actually holds for
    #: that week: weekdays only, a Thursday covering two days.
    SESSIONS = {
        date(2026, 9, 21): [date(2026, 9, 22)],
        date(2026, 9, 22): [date(2026, 9, 23)],
        date(2026, 9, 23): [date(2026, 9, 24)],
        date(2026, 9, 24): [date(2026, 9, 25), date(2026, 9, 26)],
        date(2026, 9, 25): [date(2026, 9, 27), date(2026, 9, 28)],
        date(2026, 9, 28): [date(2026, 9, 29)],
    }

    def test_a_one_day_session_starts_and_ends_next_day(self):
        # The bug this replaced extended these to the following Saturday,
        # by following a run of matching peak status instead of asking.
        for trade_date in (date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)):
            assert default_flow_window(trade_date, True, self.SESSIONS) == (
                trade_date + timedelta(days=1),
                trade_date + timedelta(days=1),
            )

    def test_a_session_covering_two_days_gives_both(self):
        assert default_flow_window(date(2026, 9, 24), True, self.SESSIONS) == (
            date(2026, 9, 25),
            date(2026, 9, 26),
        )

    def test_a_session_can_skip_a_day_the_previous_one_already_covered(self):
        # Friday 9/25 covers 9/27-9/28; 9/26 was already sold on Thursday.
        assert default_flow_window(date(2026, 9, 25), True, self.SESSIONS) == (
            date(2026, 9, 27),
            date(2026, 9, 28),
        )

    def test_a_day_with_no_session_is_a_real_time_trade(self):
        # Saturday: there is no day-ahead market to trade it in, so it
        # flows the day it's traded whatever the IsDAM box says — and
        # ui.trade_fields unticks the box to match.
        assert default_flow_window(date(2026, 9, 26), True, self.SESSIONS) == (
            date(2026, 9, 26),
            date(2026, 9, 26),
        )

    def test_an_empty_calendar_falls_back_to_next_day(self):
        # What an unreachable calendar leaves: the old plain default,
        # rather than a blocked page.
        assert default_flow_window(date(2026, 9, 21), True, {}) == (
            date(2026, 9, 22),
            date(2026, 9, 22),
        )

    def test_a_session_less_day_offers_no_day_ahead_at_all(self):
        assert has_trading_session(date(2026, 9, 24), self.SESSIONS)
        assert not has_trading_session(date(2026, 9, 26), self.SESSIONS)

    def test_an_unreadable_calendar_leaves_the_choice_alone(self):
        # Empty means "couldn't be read", not "nothing trades" — turning a
        # day-ahead trade real-time on a DB hiccup would be worse than
        # leaving the trader to decide.
        assert has_trading_session(date(2026, 9, 26), {})

    def test_a_real_time_trade_flows_the_day_it_is_traded(self):
        # And never consults the calendar at all.
        for trade_date in (date(2026, 9, 24), date(2026, 9, 26)):
            assert default_flow_window(trade_date, False, self.SESSIONS) == (
                trade_date,
                trade_date,
            )


class TestSessionHorizon:
    """How far forward the app may act on its own — the last flow date of
    the session being traded. Beyond it nothing has been traded yet, so a
    link auto-propagated out there has no position behind it."""

    SESSIONS = TestDefaultFlowWindow.SESSIONS

    def test_it_is_the_last_flow_date_of_that_days_session(self):
        # Friday 9/25 sells 9/27 and 9/28, so 9/28 is as far as anything
        # may reach on its own.
        assert session_horizon(date(2026, 9, 25), self.SESSIONS) == date(2026, 9, 28)

    def test_a_one_day_session_reaches_only_the_next_day(self):
        assert session_horizon(date(2026, 9, 21), self.SESSIONS) == date(2026, 9, 22)

    def test_a_day_with_no_session_falls_back_to_the_live_one(self):
        # Saturday: the market didn't trade, but Friday's session is still
        # the one being scheduled, so its horizon still stands.
        assert session_horizon(date(2026, 9, 26), self.SESSIONS) == date(2026, 9, 28)
        assert session_horizon(date(2026, 9, 27), self.SESSIONS) == date(2026, 9, 28)

    def test_a_date_before_any_session_has_no_horizon(self):
        assert session_horizon(date(2026, 9, 1), self.SESSIONS) is None

    def test_an_unreadable_calendar_has_no_horizon(self):
        # None means "don't cap" — a DB hiccup shouldn't silently stop
        # links propagating, the same stance has_trading_session takes.
        assert session_horizon(date(2026, 9, 25), {}) is None


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
