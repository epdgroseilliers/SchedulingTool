"""ui/scheduling/bidgrid.py — what the bid grid is handed, and what it makes
of the trader's edits.

`build_payload`, `hour_rows` and `dialog_width_px` are pure and are called
directly. `handle_event` needs session state, so it runs inside a real app
script — the same arrangement test_scheduling_view.py uses for the board,
and for the same reason: a component's value lands in `session_state` under
its key exactly as a widget's does, so setting it *is* what the frontend
does when a trader types into a cell.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from domain.bidfiles import LONG, SHORT
from ui.scheduling.state import BIDFILE_DIALOG, LINK_DIALOG
from ui.scheduling.bidgrid import (
    DATA_COL_PX,
    MIN_DIALOG_PX,
    build_payload,
    dialog_width_px,
    hour_rows,
)

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")
FLOW = date.today() + timedelta(days=1)

AZPS = {h: 100.0 for h in range(7, 23)}
BPAT = {h: 60.0 for h in range(7, 23)}
GROUPS = {(SHORT, "AZPS"): AZPS, (LONG, "BPAT"): BPAT}


def line(code="", price=0.0, mw=None):
    return {"code": code, "price": price, "mw_by_hour": dict(mw or {})}


class TestTheIndexColumn:
    def test_it_runs_the_pacific_day(self):
        assert [row["he"] for row in hour_rows()] == list(range(1, 25))

    def test_beside_the_eastern_row_each_hour_lands_on(self):
        # PPT HE1 is EPT HE4; the last three PPT hours fall after midnight
        # Eastern, which is what the file's starred rows are.
        assert [row["ept"] for row in hour_rows()] == (
            [str(h) for h in range(4, 25)] + ["1*", "2*", "3*"]
        )


class TestThePayload:
    def test_a_side_with_no_position_is_not_drawn(self):
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, {})
        assert [s["side"] for s in sides] == [SHORT]

    def test_both_sides_when_both_have_one(self):
        _, sides = build_payload(GROUPS, {})
        assert [s["side"] for s in sides] == [SHORT, LONG]
        assert [s["code_label"] for s in sides] == ["GCA", "LCA"]

    def test_an_untouched_counterparty_is_one_line_carrying_the_whole_day(self):
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, {})
        group = sides[0]["groups"][0]
        assert group["pse"] == "AZPS"
        assert group["total"] == 1600
        assert len(group["lines"]) == 1
        assert group["lines"][0]["total"] == 1600

    def test_an_hour_that_doesnt_flow_is_blank_rather_than_zero(self):
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, {})
        mw = sides[0]["groups"][0]["lines"][0]["mw"]
        assert mw[6] == 100.0          # HE7
        assert mw[0] is None           # HE1, which it doesn't flow

    def test_a_price_shows_only_where_there_is_mw(self):
        # A price on an hour carrying no MW is a number the file would never
        # contain — the writer only fills a price where it fills a MW.
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, {})
        price = sides[0]["groups"][0]["lines"][0]["price"]
        assert price[6] == 0.0         # a real price of zero, not a blank
        assert price[0] is None

    def test_the_sides_own_default_price(self):
        _, sides = build_payload(GROUPS, {})
        short, long_ = sides
        assert short["groups"][0]["lines"][0]["price"][6] == 0.0
        assert long_["groups"][0]["lines"][0]["price"][6] == 50.0

    def test_a_split_becomes_two_lines(self):
        splits = {(SHORT, "AZPS"): [
            line("PALOVERDE", 0.0, {h: 60.0 for h in range(7, 23)}),
            line("MEAD230", 0.0, {h: 40.0 for h in range(7, 23)}),
        ]}
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, splits)
        lines = sides[0]["groups"][0]["lines"]
        assert [ln["code"] for ln in lines] == ["PALOVERDE", "MEAD230"]
        assert [ln["total"] for ln in lines] == [960, 640]

    def test_an_hour_that_doesnt_reconcile_is_flagged_where_it_happened(self):
        splits = {(SHORT, "AZPS"): [
            line("PALOVERDE", 0.0, {7: 60.0}),
            line("MEAD230", 0.0, {7: 10.0}),   # 100 expected, only 70 allocated
        ]}
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, splits)
        bad = sides[0]["groups"][0]["bad_hours"]
        assert "7" in bad and "70 MW" in bad["7"] and "100 MW" in bad["7"]

    def test_a_reconciled_split_flags_nothing(self):
        splits = {(SHORT, "AZPS"): [
            line("PALOVERDE", 0.0, {h: 60.0 for h in range(7, 23)}),
            line("MEAD230", 0.0, {h: 40.0 for h in range(7, 23)}),
        ]}
        _, sides = build_payload({(SHORT, "AZPS"): AZPS}, splits)
        assert sides[0]["groups"][0]["bad_hours"] == {}


class TestSizingTheModal:
    """st.dialog offers only small/medium/large; the useful width is
    whatever today's counterparties add up to."""

    BUSY = {
        (SHORT, "AZPS"): AZPS, (SHORT, "TEPC"): AZPS, (SHORT, "WALC"): AZPS,
        (LONG, "BPAT"): BPAT, (LONG, "PACW"): BPAT,
    }

    def test_it_grows_with_the_columns(self):
        _, few = build_payload({(SHORT, "AZPS"): AZPS, (LONG, "BPAT"): BPAT}, {})
        _, many = build_payload(self.BUSY, {})
        assert dialog_width_px(many) > dialog_width_px(few)

    def test_a_split_widens_it_by_exactly_one_pair(self):
        _, before = build_payload(self.BUSY, {})
        _, after = build_payload(
            self.BUSY,
            {(SHORT, "AZPS"): [line("A", 0.0, {7: 60.0}), line("B", 0.0, {7: 40.0})]},
        )
        assert dialog_width_px(after) - dialog_width_px(before) == 2 * DATA_COL_PX

    def test_a_narrow_grid_still_gets_a_modal_wide_enough_to_use(self):
        # One counterparty's grid is only a few hundred px; a modal that
        # narrow can't hold its own caption and buttons, so there's a floor.
        _, one = build_payload({(SHORT, "AZPS"): AZPS}, {})
        assert dialog_width_px(one) == MIN_DIALOG_PX


# --------------------------------------------------- events, through the page


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


def hourly_trade(direction, counterparty, location, hours, mw, flow_date=FLOW):
    return {
        "direction": direction, "counterparty": counterparty, "location": location,
        "index": None, "price": 73.0, "is_monthly": False,
        "schedule": [(flow_date, h, float(mw)) for h in hours],
    }


BUY_LEG = hourly_trade("Buy", "AZPS", "PALOVERDE500", range(7, 23), 100)


def _open_grid(trades=(BUY_LEG,)):
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.session_state["trades"] = list(trades)
    at.run()
    at = _board(at, {"type": "link_request", "from": "session:0", "market": "SWPW"})
    [b for b in _in_dialog(at).button if b.label == "Create link"][0].click().run()
    return _board(at, {"type": "chip_click", "market": "SWPW"})


def _in_dialog(at):
    """See the same helper in test_bidfile.py: AppTest only does full script
    runs, which is what a dismissal looks like to the page."""
    at.session_state[BIDFILE_DIALOG] = False
    at.session_state[LINK_DIALOG] = False
    return at


def _board(at, event):
    event = dict(event)
    event["seq"] = at.session_state["mv_last_seq"] + 1
    at.session_state["mv_board"] = event
    return at.run()


def _grid(at, event, seq=None):
    """One grid event, the way the component sends it."""
    event = dict(event)
    event.setdefault("instance", "test-frame")
    event["seq"] = seq if seq is not None else at.session_state["mv_bidgrid_last_seq"] + 1
    at.session_state["mv_bidgrid"] = event
    return _in_dialog(at).run()


def _lines(at, side="SHORT", pse="AZPS"):
    return at.session_state["mv_bidfile_splits"][("SWPW", FLOW, side, pse)]


class TestApplyingAnEdit:
    def test_opening_the_grid_stores_a_line_per_counterparty(self, no_bilateral_db):
        # What's shown and what Generate reads have to be the same thing —
        # including the side's default price.
        at = _open_grid()
        assert not at.exception, [e.value for e in at.exception]
        lines = _lines(at)
        assert len(lines) == 1
        assert lines[0]["price"] == 0.0
        assert lines[0]["mw_by_hour"][7] == 100.0

    def test_a_typed_code_is_stored(self, no_bilateral_db):
        at = _grid(_open_grid(), {
            "type": "code", "side": SHORT, "pse": "AZPS", "line": 0, "value": "PALOVERDE",
        })
        assert _lines(at)[0]["code"] == "PALOVERDE"

    def test_a_typed_price_covers_the_whole_line(self, no_bilateral_db):
        at = _grid(_open_grid(), {
            "type": "price", "side": SHORT, "pse": "AZPS", "line": 0, "value": -1.0,
        })
        assert _lines(at)[0]["price"] == -1.0

    def test_a_cleared_price_is_none_and_is_reported(self, no_bilateral_db):
        at = _grid(_open_grid(), {
            "type": "price", "side": SHORT, "pse": "AZPS", "line": 0, "value": None,
        })
        assert _lines(at)[0]["price"] is None
        assert any("price" in e.value for e in at.error)

    def test_splitting_adds_an_empty_second_line(self, no_bilateral_db):
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0})
        lines = _lines(at)
        assert len(lines) == 2
        assert lines[1]["mw_by_hour"] == {}
        assert lines[1]["price"] == 0.0        # the short side's default
        assert lines[0]["mw_by_hour"][7] == 100.0   # nothing moved yet

    def test_typing_mw_into_the_split_moves_it_off_the_first(self, no_bilateral_db):
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0})
        at = _grid(at, {
            "type": "mw", "side": SHORT, "pse": "AZPS", "line": 1, "hour": 7, "value": 40.0,
        })
        lines = _lines(at)
        assert lines[0]["mw_by_hour"][7] == 60.0
        assert lines[1]["mw_by_hour"][7] == 40.0
        assert lines[0]["mw_by_hour"][8] == 100.0   # an hour they didn't touch

    def test_an_over_allocation_is_kept_and_reported(self, no_bilateral_db):
        # Not trimmed back: the trader is told, rather than having what they
        # just typed quietly rewritten.
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0})
        at = _grid(at, {
            "type": "mw", "side": SHORT, "pse": "AZPS", "line": 1, "hour": 7, "value": 150.0,
        })
        assert _lines(at)[1]["mw_by_hour"][7] == 150.0
        assert 7 not in _lines(at)[0]["mw_by_hour"]
        assert any("HE7" in e.value for e in at.error)

    def test_dropping_a_split_gives_its_mw_back(self, no_bilateral_db):
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0})
        at = _grid(at, {
            "type": "mw", "side": SHORT, "pse": "AZPS", "line": 1, "hour": 7, "value": 40.0,
        })
        at = _grid(at, {"type": "unsplit", "side": SHORT, "pse": "AZPS", "line": 1})
        lines = _lines(at)
        assert len(lines) == 1
        assert lines[0]["mw_by_hour"][7] == 100.0   # whole again, nothing lost

    def test_the_first_line_can_never_be_dropped(self, no_bilateral_db):
        at = _grid(_open_grid(), {"type": "unsplit", "side": SHORT, "pse": "AZPS", "line": 0})
        assert len(_lines(at)) == 1
        assert _lines(at)[0]["mw_by_hour"][7] == 100.0


class TestIgnoringWhatItShould:
    def test_a_replayed_event_is_applied_once(self, no_bilateral_db):
        # Streamlit hands a component's last value back on every rerun, so
        # without the seq watermark one split would keep splitting.
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0})
        at.run()
        at.run()
        assert len(_lines(at)) == 2

    def test_a_fresh_frame_restarts_the_watermark(self, no_bilateral_db):
        # A reloaded iframe counts from 0 again. Without the instance id its
        # events would look stale and be dropped — the bug that made the
        # board's links intermittently do nothing.
        at = _grid(_open_grid(), {"type": "split", "side": SHORT, "pse": "AZPS", "line": 0}, seq=9)
        at = _grid(at, {
            "type": "code", "side": SHORT, "pse": "AZPS", "line": 0,
            "value": "PALOVERDE", "instance": "a-new-frame",
        }, seq=1)
        assert _lines(at)[0]["code"] == "PALOVERDE"

    def test_an_event_for_a_counterparty_that_isnt_there_is_ignored(self, no_bilateral_db):
        at = _grid(_open_grid(), {
            "type": "code", "side": SHORT, "pse": "NOBODY", "line": 0, "value": "X",
        })
        assert not at.exception, [e.value for e in at.exception]
        assert ("SWPW", FLOW, SHORT, "NOBODY") not in at.session_state["mv_bidfile_splits"]

    def test_an_event_for_a_line_that_isnt_there_is_ignored(self, no_bilateral_db):
        at = _grid(_open_grid(), {
            "type": "code", "side": SHORT, "pse": "AZPS", "line": 7, "value": "X",
        })
        assert not at.exception, [e.value for e in at.exception]
        assert _lines(at)[0]["code"] == ""

    def test_an_unknown_event_type_is_ignored(self, no_bilateral_db):
        at = _grid(_open_grid(), {"type": "wat", "side": SHORT, "pse": "AZPS", "line": 0})
        assert not at.exception, [e.value for e in at.exception]
        assert len(_lines(at)) == 1
