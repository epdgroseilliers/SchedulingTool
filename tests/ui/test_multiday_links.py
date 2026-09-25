"""A trade spanning several flow dates gets one link per day, and the days
are independent of each other.

The bug this covers: a leg's key carries no flow date (`session:0` is the
same key on every day that trade flows), and the link book was handed to the
page unfiltered — so a two-day trade's two squares resolved to the *same*
`Link`. One `mw_by_hour` shared between them, editing either day rewriting
the other, and the bid-file builder on the second day coming up prefilled
with the first day's lines.

Driven through the real page like the rest of `tests/ui/`: a board event is
just `session_state["mv_board"] = {...}` — see test_scheduling_view.py.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from domain.bidfiles import SHORT
from domain.matching import TradeLeg
from ui.scheduling.state import (
    BIDFILE_DIALOG,
    LINK_DIALOG,
    PROPAGATE_MAX_DAYS,
    _shared_flow_dates,
)

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")

#: Day one is what the flow-date filter defaults to, so the page opens on it.
DAY1 = date.today() + timedelta(days=1)
DAY2 = DAY1 + timedelta(days=1)


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


@pytest.fixture
def open_horizon(monkeypatch):
    """Put both test days inside the session being scheduled.

    A link only propagates as far as today's trading session reaches, so
    how many days that covers depends on which weekday this suite runs: a
    Thursday or Friday session covers two flow dates, a Monday's covers
    one. These tests are about the days being *independent* of each other,
    so the horizon is pinned rather than left to the calendar — where it
    is the subject, it's set explicitly (see TestTheSessionCapsIt).
    """
    monkeypatch.setattr(
        "ui.scheduling.state.calendar_horizon", lambda trade_date=None: DAY2
    )


def two_day_trade(direction, counterparty, location, mw):
    """One trade flowing the same hours on both days — the shape that made
    the bug visible, since the two days are otherwise indistinguishable."""
    return {
        "direction": direction, "counterparty": counterparty, "location": location,
        "index": None, "price": 73.0, "is_monthly": False,
        "schedule": [
            (day, h, float(mw)) for day in (DAY1, DAY2) for h in range(7, 23)
        ],
    }


BUY = two_day_trade("Buy", "AZPS", "PALOVERDE500", 100)
SELL = two_day_trade("Sell", "BPAT", "MIDC", 100)


def _run(trades=()):
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.session_state["trades"] = list(trades)
    return at.run()


def _in_dialog(at):
    """See the same helper in test_bidfile.py: AppTest only does full script
    runs, which is what a dismissal looks like to the page."""
    at.session_state[BIDFILE_DIALOG] = False
    at.session_state[LINK_DIALOG] = False
    return at


def _emit(at, event):
    event = dict(event)
    event["seq"] = at.session_state["mv_last_seq"] + 1
    at.session_state["mv_board"] = event
    return at.run()


def _click(at, label):
    [b for b in _in_dialog(at).button if b.label == label][0].click().run()
    return at


def _on(at, day):
    """Move the flow-date filter to `day`."""
    at.session_state["mv_flow_date"] = day
    return at.run()


def _link_the_two(at):
    at = _emit(at, {"type": "link_request", "from": "session:0", "to": "session:1"})
    return _click(at, "Create link")


def _codes(at, day, side=SHORT, pse="AZPS"):
    """The GCA/LCA codes the builder holds for one flow date.

    Found by which keys mention the day rather than by the key's exact
    shape, so a store that doesn't separate the days at all reads as "this
    day has no lines of its own" — which is the bug — instead of a KeyError
    about tuple arity.
    """
    store = at.session_state["mv_bidfile_splits"]
    return [
        line["code"]
        for key, lines in store.items()
        if day in key and side in key and pse in key
        for line in lines
    ]


def _links(at, day=None):
    links = at.session_state["mv_links"]
    if day is None:
        return links
    return [ln for ln in links if ln.flow_date == day]


class TestOneLinkPerDay:
    def test_linking_on_one_day_links_every_day_both_trades_flow(
        self, no_bilateral_db, open_horizon
    ):
        at = _link_the_two(_run([BUY, SELL]))
        assert {ln.flow_date for ln in _links(at)} == {DAY1, DAY2}

    def test_each_day_gets_its_own_link_not_a_shared_one(self, no_bilateral_db, open_horizon):
        at = _link_the_two(_run([BUY, SELL]))
        day1, day2 = _links(at, DAY1), _links(at, DAY2)
        assert len(day1) == len(day2) == 1
        assert day1[0].link_id != day2[0].link_id
        assert day1[0] is not day2[0]

    def test_the_page_only_ever_sees_one_days_links(self, no_bilateral_db, open_horizon):
        # Proof that the *page* filtered, not just that state holds two:
        # the detail strip counts the links touching the focused square.
        # Unfiltered, every day would report both days' links on it.
        at = _link_the_two(_run([BUY, SELL]))
        # Focused once: clicking the focused square again clears it, and the
        # focus survives the date change because the key does.
        at = _emit(at, {"type": "select", "key": "session:0"})
        for day in (DAY1, DAY2):
            at = _on(at, day)
            mine = _links(at, day)[0].link_id
            theirs = _links(at, DAY2 if day == DAY1 else DAY1)[0].link_id

            detail = [m.value for m in at.markdown if "link(s)" in m.value]
            assert detail and "1 link(s)" in detail[0], (day, detail)
            # And it's this day's link, not merely one of them: the detail
            # strip captions each link touching the square by id.
            captions = [c.value for c in at.caption]
            assert any(c.startswith(f"{mine} →") for c in captions), (day, captions)
            assert not any(c.startswith(f"{theirs} →") for c in captions)

    def test_the_trader_is_told_which_other_days_were_linked(self, no_bilateral_db, open_horizon):
        at = _link_the_two(_run([BUY, SELL]))
        assert any(DAY2.isoformat() in t.value for t in at.toast)


class TestItOnlyEverGoesForward:
    """Through the page: linking on a day must never create links on days
    before it. Those are days already scheduled, and reaching back into
    them rewrites finished work on a day that isn't even on screen."""

    def test_linking_on_the_later_day_leaves_the_earlier_one_alone(
        self, no_bilateral_db, open_horizon
    ):
        at = _on(_run([BUY, SELL]), DAY2)
        at = _emit(at, {"type": "link_request", "from": "session:0", "to": "session:1"})
        _click(at, "Create link")

        assert {ln.flow_date for ln in _links(at)} == {DAY2}
        assert _links(at, DAY1) == []

    def test_and_says_nothing_about_days_it_did_not_link(
        self, no_bilateral_db, open_horizon
    ):
        at = _on(_run([BUY, SELL]), DAY2)
        at = _emit(at, {"type": "link_request", "from": "session:0", "to": "session:1"})
        _click(at, "Create link")
        assert not [t for t in at.toast if "Also linked" in t.value]


class TestTheDaysAreIndependent:
    def test_editing_one_day_leaves_the_other_alone(self, no_bilateral_db, open_horizon):
        at = _link_the_two(_run([BUY, SELL]))
        before = dict(_links(at, DAY2)[0].mw_by_hour)

        edit = _links(at, DAY1)[0]
        at = _emit(at, {"type": "link_click", "link_id": edit.link_id})
        # Halve day one's allocation through the popup's own hour editor.
        # AppTest has no data_editor accessor, so the edit goes in the way
        # Streamlit itself stores one: a diff against the frame the widget
        # was handed, under the widget's key (see ui.scheduling.widgets).
        at.session_state[f"mv_alloc_{edit.link_id}"] = {
            "edited_rows": {0: {str(h): 50.0 for h in range(7, 23)}},
            "added_rows": [],
            "deleted_rows": [],
        }
        _click(at, "Save")

        assert _links(at, DAY2)[0].mw_by_hour == before
        assert set(_links(at, DAY1)[0].mw_by_hour.values()) == {50.0}

    def test_deleting_one_day_leaves_the_other_alone(self, no_bilateral_db, open_horizon):
        at = _link_the_two(_run([BUY, SELL]))
        gone = _links(at, DAY1)[0].link_id
        at = _emit(at, {"type": "link_click", "link_id": gone})
        _click(at, "Delete")

        assert _links(at, DAY1) == []
        assert len(_links(at, DAY2)) == 1


class TestTheSessionCapsIt:
    """Through the page: a deal running past the session being traded is
    not auto-linked past it. Beyond the current session's last flow date
    nothing has been traded, so a link invented out there has no position
    behind it."""

    @staticmethod
    def _horizon(monkeypatch, day):
        monkeypatch.setattr(
            "ui.scheduling.state.calendar_horizon", lambda trade_date=None: day
        )

    def test_a_day_past_the_session_gets_no_link(self, no_bilateral_db, monkeypatch):
        self._horizon(monkeypatch, DAY1)
        at = _link_the_two(_run([BUY, SELL]))
        assert {ln.flow_date for ln in _links(at)} == {DAY1}
        assert _links(at, DAY2) == []

    def test_nothing_is_announced_when_nothing_propagated(
        self, no_bilateral_db, monkeypatch
    ):
        self._horizon(monkeypatch, DAY1)
        at = _link_the_two(_run([BUY, SELL]))
        assert not [t for t in at.toast if "Also linked" in t.value]

    def test_a_session_reaching_the_day_still_links_it(
        self, no_bilateral_db, monkeypatch
    ):
        self._horizon(monkeypatch, DAY2)
        at = _link_the_two(_run([BUY, SELL]))
        assert {ln.flow_date for ln in _links(at)} == {DAY1, DAY2}

    def test_an_unreadable_calendar_does_not_stop_it(self, no_bilateral_db, monkeypatch):
        self._horizon(monkeypatch, None)
        at = _link_the_two(_run([BUY, SELL]))
        assert {ln.flow_date for ln in _links(at)} == {DAY1, DAY2}


class TestWhichDaysGetOne:
    """_shared_flow_dates touches no session state, so it's called directly."""

    @staticmethod
    def _leg(start, stop, on, direction="Buy"):
        return TradeLeg(
            key="k", source="db", direction=direction, pse="AZPS",
            por_pod="PALOVERDE500", flow_date=on, start_date=start, stop_date=stop,
        )

    def test_the_other_days_of_the_overlap_and_not_the_one_just_linked(self):
        both = [self._leg(DAY1, DAY2, DAY1), self._leg(DAY1, DAY2, DAY1, "Sell")]
        assert _shared_flow_dates(*both) == [DAY2]

    def test_a_single_day_trade_costs_nothing(self):
        # No candidates means propagate_link returns before loading any day.
        both = [self._leg(DAY1, DAY1, DAY1), self._leg(DAY1, DAY1, DAY1, "Sell")]
        assert _shared_flow_dates(*both) == []

    def test_a_market_end_constrains_nothing(self):
        market = TradeLeg(
            key="market:SWPW:Sell:x", source="market", direction="Sell",
            pse="SWPW", por_pod="Market", flow_date=DAY1,
        )
        assert _shared_flow_dates(self._leg(DAY1, DAY2, DAY1), market) == [DAY2]

    def test_only_the_overlap_of_the_two_trades_counts(self):
        # One flows three days, the other two: the third is nobody's to link.
        day3 = DAY2 + timedelta(days=1)
        long_leg = self._leg(DAY1, day3, DAY1)
        short_leg = self._leg(DAY1, DAY2, DAY1, "Sell")
        assert _shared_flow_dates(long_leg, short_leg) == [DAY2]

    def test_a_deal_running_past_the_calendar_is_not_linked_past_it(self):
        # Beyond the calendar's last flow date there is no peak flag and no
        # session, so nothing here can say what flows — the days inside it
        # still get their link.
        day3 = DAY2 + timedelta(days=1)
        both = [self._leg(DAY1, day3, DAY1), self._leg(DAY1, day3, DAY1, "Sell")]
        assert _shared_flow_dates(*both, horizon=DAY2) == [DAY2]
        assert _shared_flow_dates(*both, horizon=DAY1) == []

    def test_a_horizon_beyond_the_trade_changes_nothing(self):
        both = [self._leg(DAY1, DAY2, DAY1), self._leg(DAY1, DAY2, DAY1, "Sell")]
        assert _shared_flow_dates(*both, horizon=DAY2 + timedelta(days=90)) == [DAY2]

    def test_an_unreadable_calendar_does_not_block_propagation(self):
        # Same stance as a session-less trade date in domain.trade: a DB
        # hiccup shouldn't quietly change what the app does.
        both = [self._leg(DAY1, DAY2, DAY1), self._leg(DAY1, DAY2, DAY1, "Sell")]
        assert _shared_flow_dates(*both, horizon=None) == [DAY2]

    def test_it_never_reaches_backward(self):
        # A day earlier than the one in front of the trader is a day they
        # have already scheduled; reaching back into it would rewrite
        # finished work on a day not even on screen.
        start, stop = date(2026, 1, 1), date(2026, 3, 1)
        on = date(2026, 2, 19)
        days = _shared_flow_dates(
            self._leg(start, stop, on), self._leg(start, stop, on, "Sell")
        )
        assert days and min(days) > on
        assert days == sorted(days)

    def test_linking_on_the_last_day_propagates_nothing(self):
        # Nothing later left to cover — and it must not fall back to the
        # days before it.
        on = DAY2
        both = [self._leg(DAY1, DAY2, on), self._leg(DAY1, DAY2, on, "Sell")]
        assert _shared_flow_dates(*both) == []

    def test_the_cap_limits_how_far_forward_it_runs(self):
        # The backstop for a calendar that gave no horizon at all.
        start = date(2026, 1, 1)
        days = _shared_flow_dates(
            self._leg(start, date(2026, 3, 1), start),
            self._leg(start, date(2026, 3, 1), start, "Sell"),
        )
        assert len(days) == PROPAGATE_MAX_DAYS
        assert min(days) == start + timedelta(days=1)


class TestTheBidFileIsPerDay:
    def _open_swpw(self, at):
        return _emit(at, {"type": "chip_click", "market": "SWPW"})

    def _park_in_swpw(self, at):
        at = _emit(at, {"type": "link_request", "from": "session:0", "market": "SWPW"})
        return _click(at, "Create link")

    def test_a_second_days_builder_does_not_inherit_the_first_days_lines(
        self, no_bilateral_db, open_horizon
    ):
        at = self._open_swpw(self._park_in_swpw(_run([BUY])))
        # Fill day one in: a GCA code the trader typed into the grid.
        event = {"type": "code", "side": SHORT, "pse": "AZPS", "line": 0, "value": "AZPS"}
        event["seq"] = at.session_state["mv_bidgrid_last_seq"] + 1
        event["instance"] = "test-frame"
        at.session_state["mv_bidgrid"] = event
        at = _in_dialog(at).run()
        assert _codes(at, DAY1) == ["AZPS"], "day one's lines aren't held per day"

        at = self._open_swpw(_on(at, DAY2))
        at = _in_dialog(at).run()
        # Day two has lines of its own — seeded from its own position, with
        # nothing typed. Sharing day one's is the bug: the same position bid
        # twice, under a code that was only ever agreed for the other day.
        assert _codes(at, DAY2) == [""], "day two has no lines of its own"
        # ...and day one's are untouched, not overwritten by day two.
        assert _codes(at, DAY1) == ["AZPS"]
