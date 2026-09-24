"""The Scheduling View page, through AppTest.

The board is a custom component, so AppTest never renders its iframe — but a
component's value lands in `session_state` under its key like any widget's,
so setting `session_state["mv_board"]` to an event is exactly what the real
frontend does when a trader drags something. That makes the whole loop
testable: event in, state change, popup, link out. What can't be tested here
is the component's own interior — the dragging, the hit-testing, which
square the pointer was over — which is why every decision that *can* live on
the Python side does (see tests/ui/test_board_payload.py).

The bilateral database is stubbed to an empty book throughout, so the board
is driven entirely by trades seeded into `st.session_state.trades` and each
test's book is explicit. Rendering still reaches the app's own WECC calendar
for the flow date's peak flag, like the rest of the ui/ tier.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.scheduling.state import BIDFILE_DIALOG, LINK_DIALOG

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")

FLOW = date.today() + timedelta(days=1)


@pytest.fixture
def no_bilateral_db(monkeypatch):
    """The Scheduling View's DB read, stubbed to an empty book."""
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


def hourly_trade(direction, counterparty, location, hours, mw, flow_date=FLOW):
    """A trade in the shape ui.actions puts into st.session_state.trades."""
    return {
        "direction": direction,
        "counterparty": counterparty,
        "location": location,
        "index": None,
        "price": 73.0,
        "is_monthly": False,
        "schedule": [(flow_date, h, float(mw)) for h in hours],
    }


BUY_LEG = hourly_trade("Buy", "AZPS", "PALOVERDE500", range(7, 23), 100)
SELL_LEG = hourly_trade("Sell", "BPAT", "MIDC", range(7, 23), 60)


def _run(trades=(), **state):
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.session_state["trades"] = list(trades)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def _emit(at, event):
    """Do to the page what the component does when the trader acts on it."""
    event = dict(event)
    event.setdefault("seq", at.session_state["mv_last_seq"] + 1)
    at.session_state["mv_board"] = event
    return at.run()


def _click(at, label):
    [b for b in _in_dialog(at).button if b.label == label][0].click().run()
    return at

def _in_dialog(at):
    """Tell the page its modal is still open.

    A real click inside a dialog reruns the *fragment*; AppTest only does
    full script runs, and a full run with a dialog still flagged open is
    exactly what a dismissal looks like to
    ui.scheduling.state.dialog_was_dismissed. Without this, every step taken
    inside a modal here would read as "the trader pressed Esc".
    """
    at.session_state[BIDFILE_DIALOG] = False
    at.session_state[LINK_DIALOG] = False
    return at



class TestPageRenders:
    def test_an_empty_book_still_renders(self, no_bilateral_db):
        at = _run()
        assert not at.exception, [e.value for e in at.exception]

    def test_the_open_position_line_is_on_the_page(self, no_bilateral_db):
        at = _run([BUY_LEG, SELL_LEG])
        assert not at.exception, [e.value for e in at.exception]
        assert any("Open buys" in m.value for m in at.markdown)

    def test_a_trade_on_another_flow_date_is_absent(self, no_bilateral_db):
        other = hourly_trade(
            "Buy", "AZPS", "PALOVERDE500", range(7, 23), 100, FLOW + timedelta(days=7)
        )
        at = _run([other])
        # Nothing to match means nothing open.
        assert any("Open buys **0**" in m.value for m in at.markdown)

    def test_a_trade_already_written_to_the_db_is_not_counted_twice(self, no_bilateral_db):
        at = _run([dict(BUY_LEG, db_trade_ids=[90001])])
        assert any("Open buys **0**" in m.value for m in at.markdown)


class TestFilters:
    def test_pse_filter_narrows_the_board(self, no_bilateral_db):
        at = _run([BUY_LEG, SELL_LEG])
        at.multiselect(key="mv_pse").set_value(["BPAT"]).run()
        assert not at.exception, [e.value for e in at.exception]
        assert any("Open buys **0**" in m.value for m in at.markdown)

    def test_por_pod_filter_narrows_the_board(self, no_bilateral_db):
        at = _run([BUY_LEG, SELL_LEG])
        at.multiselect(key="mv_porpod").set_value(["PALOVERDE500"]).run()
        assert any("Open sells **0**" in m.value for m in at.markdown)

    def test_a_stale_pse_filter_does_not_break_the_next_flow_date(self, no_bilateral_db):
        # PSE options come from the legs the flow date holds, so a filter
        # picked on one day names something the next day has no square for.
        at = _run([BUY_LEG])
        at.multiselect(key="mv_pse").set_value(["AZPS"]).run()
        at.date_input(key="mv_flow_date").set_value(FLOW + timedelta(days=30)).run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["mv_pse"] == []

    def test_a_linked_counterparty_survives_a_pse_filter(self, no_bilateral_db):
        # The exact case reported: filtering down to the *sell* side (CONC)
        # must not drop AZPS, the buy it's linked to — even though AZPS
        # itself fails the filter. If AZPS were dropped, it would take its
        # still-open 640 MWh off the open-position line with it; rescue
        # keeps that number showing, which is the only way to tell from the
        # page whether the square survived (the board itself is a custom
        # component AppTest can't see inside).
        conc_sale = hourly_trade("Sell", "CONC", "MIDC", range(7, 23), 60)
        at = _emit(
            _run([BUY_LEG, conc_sale]),
            {"type": "link_request", "from": "session:0", "to": "session:1"},
        )
        _click(at, "Create link")
        at.multiselect(key="mv_pse").set_value(["CONC"]).run()

        assert not at.exception, [e.value for e in at.exception]
        assert any("Open buys **640**" in m.value for m in at.markdown)

    def test_an_unlinked_counterparty_is_still_dropped_by_the_filter(
        self, no_bilateral_db
    ):
        # PACE has no link to AZPS, so rescue does not apply — filtering to
        # AZPS must still drop PACE's whole 640 MWh off the open-sells total.
        unrelated_sale = hourly_trade("Sell", "PACE", "MEAD230", range(7, 23), 40)
        at = _run([BUY_LEG, unrelated_sale])
        at.multiselect(key="mv_pse").set_value(["AZPS"]).run()
        assert any("Open sells **0**" in m.value for m in at.markdown)


class TestBoardEvents:
    def test_clicking_a_square_focuses_it(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "select", "key": "session:0"})
        assert at.session_state["mv_focus"] == "session:0"

    def test_clicking_empty_space_clears_the_focus(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "select", "key": "session:0"})
        at = _emit(at, {"type": "select", "key": None})
        assert at.session_state["mv_focus"] is None

    def test_dragging_a_square_stores_where_it_was_put(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "move", "positions": {"session:0": [310, 88]}})
        assert at.session_state["mv_positions"] == {"session:0": [310.0, 88.0]}

    def test_positions_survive_a_later_move_of_another_square(self, no_bilateral_db):
        # Merged, not replaced: a square filtered off the board keeps its
        # place for when the filter comes back off.
        at = _run([BUY_LEG, SELL_LEG])
        at = _emit(at, {"type": "move", "positions": {"session:0": [10, 20]}})
        at = _emit(at, {"type": "move", "positions": {"session:1": [30, 40]}})
        assert at.session_state["mv_positions"] == {
            "session:0": [10.0, 20.0],
            "session:1": [30.0, 40.0],
        }

    def test_an_event_is_applied_once_however_often_it_is_replayed(self, no_bilateral_db):
        # Streamlit hands a component's last value back on every rerun, so
        # without the seq guard a drag would re-fire on every later rerun.
        at = _run([BUY_LEG])
        at = _emit(at, {"seq": 5, "type": "select", "key": "session:0"})
        at = _emit(at, {"seq": 5, "type": "select", "key": None})
        assert at.session_state["mv_focus"] == "session:0"

    def test_a_rebuilt_iframe_is_not_mistaken_for_a_replay(self, no_bilateral_db):
        # The reported "linking often doesn't work, no pattern to it".
        # Navigating to Add Trade and back rebuilds the component, so its
        # seq counter restarts at 0 while this watermark — in session state
        # — survives. Every event from the fresh frame then looked stale
        # and was dropped until the counter climbed past the old mark. The
        # per-frame instance id is what tells the two apart.
        at = _run([BUY_LEG, SELL_LEG])
        for seq in range(1, 8):
            at = _emit(at, {"seq": seq, "instance": "frame-A",
                            "type": "select", "key": "session:0"})
        assert at.session_state["mv_last_seq"] == 7

        at = _emit(at, {"seq": 1, "instance": "frame-B", "type": "link_request",
                        "from": "session:0", "to": "session:1"})
        assert at.session_state["mv_pending"] == {
            "buy_key": "session:0",
            "sell_key": "session:1",
        }

    def test_replay_protection_still_holds_within_one_frame(self, no_bilateral_db):
        at = _run([BUY_LEG, SELL_LEG])
        at = _emit(at, {"seq": 3, "instance": "frame-A", "type": "link_request",
                        "from": "session:0", "to": "session:1"})
        at.session_state["mv_pending"] = None
        at = _emit(at, {"seq": 3, "instance": "frame-A", "type": "link_request",
                        "from": "session:0", "to": "session:1"})
        assert at.session_state["mv_pending"] is None

    def test_a_link_request_between_two_sides_opens_the_popup(self, no_bilateral_db):
        at = _emit(
            _run([BUY_LEG, SELL_LEG]),
            {"type": "link_request", "from": "session:0", "to": "session:1"},
        )
        assert at.session_state["mv_pending"] == {
            "buy_key": "session:0",
            "sell_key": "session:1",
        }
        assert at.session_state["mv_links"] == []  # nothing until it's confirmed

    def test_the_drag_direction_does_not_matter(self, no_bilateral_db):
        # Dragged from the sell onto the buy — the same link either way.
        at = _emit(
            _run([BUY_LEG, SELL_LEG]),
            {"type": "link_request", "from": "session:1", "to": "session:0"},
        )
        assert at.session_state["mv_pending"] == {
            "buy_key": "session:0",
            "sell_key": "session:1",
        }

    def test_two_squares_on_the_same_side_cannot_be_linked(self, no_bilateral_db):
        second_buy = hourly_trade("Buy", "PNM", "PALOVERDE500", range(7, 23), 20)
        at = _emit(
            _run([BUY_LEG, second_buy]),
            {"type": "link_request", "from": "session:0", "to": "session:1"},
        )
        assert at.session_state["mv_pending"] is None


class TestSchedulePopup:
    def _open(self, at, frm="session:0", to="session:1"):
        return _emit(at, {"type": "link_request", "from": frm, "to": to})

    def test_creating_a_link_at_the_suggested_schedule(self, no_bilateral_db):
        at = self._open(_run([BUY_LEG, SELL_LEG]))
        _click(at, "Create link")
        assert not at.exception, [e.value for e in at.exception]
        links = at.session_state["mv_links"]
        assert len(links) == 1
        # The overlap at the smaller side: the sell's 60 MW.
        assert links[0].mw_by_hour == {h: 60.0 for h in range(7, 23)}
        assert at.session_state["mv_pending"] is None

    def test_cancelling_creates_nothing(self, no_bilateral_db):
        at = self._open(_run([BUY_LEG, SELL_LEG]))
        _click(at, "Cancel")
        assert at.session_state["mv_links"] == []
        assert at.session_state["mv_pending"] is None

    def test_clicking_a_link_reopens_the_same_popup_to_edit_it(self, no_bilateral_db):
        at = self._open(_run([BUY_LEG, SELL_LEG]))
        _click(at, "Create link")
        at = _emit(at, {"type": "link_click", "link_id": "L1"})
        assert at.session_state["mv_editing"] == "L1"
        assert {"Save", "Delete", "Cancel"} <= {b.label for b in at.button}

    def test_deleting_from_the_popup_removes_the_link(self, no_bilateral_db):
        at = self._open(_run([BUY_LEG, SELL_LEG]))
        _click(at, "Create link")
        at = _emit(at, {"type": "link_click", "link_id": "L1"})
        _click(at, "Delete")
        assert at.session_state["mv_links"] == []
        assert at.session_state["mv_editing"] is None

    def test_editing_an_existing_link_offers_its_own_schedule_back(self, no_bilateral_db):
        at = self._open(_run([BUY_LEG, SELL_LEG]))
        _click(at, "Create link")
        at = _emit(at, {"type": "link_click", "link_id": "L1"})
        # Saving untouched must not shrink it — the link's own MW has to be
        # added back before asking what's open, or every hour it already
        # holds would read as fully committed.
        _click(at, "Save")
        assert at.session_state["mv_links"][0].mw_by_hour == {
            h: 60.0 for h in range(7, 23)
        }

    def test_one_buy_can_be_covered_by_two_sells(self, no_bilateral_db):
        second_sell = hourly_trade("Sell", "PACE", "PALOVERDE500", range(7, 23), 40)
        at = _run([BUY_LEG, SELL_LEG, second_sell])
        at = self._open(at, "session:0", "session:1")
        _click(at, "Create link")
        at = self._open(at, "session:0", "session:2")
        _click(at, "Create link")

        links = at.session_state["mv_links"]
        assert len(links) == 2
        assert {ln.sell_key for ln in links} == {"session:1", "session:2"}
        # The second suggestion only offered what the first left open.
        assert links[1].mw_by_hour == {h: 40.0 for h in range(7, 23)}


class TestClearingSquaresOffTheBoard:
    def test_the_x_hides_a_square(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG, SELL_LEG]), {"type": "dismiss", "key": "session:0"})
        assert at.session_state["mv_hidden"] == {"session:0"}
        assert any("Open buys **0**" in m.value for m in at.markdown)
        assert any("Open sells **960**" in m.value for m in at.markdown)

    def test_hiding_is_not_deleting(self, no_bilateral_db):
        # This view only ever reads BilateralTrades, and a session trade
        # belongs to the Add Trade page's list — neither is this page's to
        # destroy, so × is reversible by construction.
        at = _emit(_run([BUY_LEG]), {"type": "dismiss", "key": "session:0"})
        assert len(at.session_state["trades"]) == 1

    def test_a_restore_button_appears_and_brings_it_back(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "dismiss", "key": "session:0"})
        restore = [b for b in at.button if b.label.startswith("Restore")]
        assert restore and "1 hidden" in restore[0].label
        restore[0].click().run()
        assert at.session_state["mv_hidden"] == set()
        assert any("Open buys **1,600**" in m.value for m in at.markdown)

    def test_no_restore_button_when_nothing_is_hidden(self, no_bilateral_db):
        at = _run([BUY_LEG])
        assert not [b for b in at.button if b.label.startswith("Restore")]

    def test_the_focused_square_can_also_be_cleared_from_the_detail_strip(
        self, no_bilateral_db
    ):
        # Two routes to the same thing: the × on the square is quick once
        # you know it's there, this one is findable.
        at = _emit(_run([BUY_LEG]), {"type": "select", "key": "session:0"})
        _click(at, "✕ Clear")
        assert at.session_state["mv_hidden"] == {"session:0"}

    def test_a_hidden_square_takes_its_links_off_the_board_with_it(
        self, no_bilateral_db
    ):
        at = _emit(
            _run([BUY_LEG, SELL_LEG]),
            {"type": "link_request", "from": "session:0", "to": "session:1"},
        )
        _click(at, "Create link")
        at = _emit(at, {"type": "dismiss", "key": "session:1"})
        assert at.session_state["mv_hidden"] == {"session:1"}
        # The link itself survives — it comes back with the square. Only the
        # drawing goes (test_board_payload covers that end).
        assert len(at.session_state["mv_links"]) == 1
        # The buy stays 960 of its 1,600 covered: clearing a square narrows
        # what's *shown*, and a position that is genuinely matched must not
        # start reading as open just because its counterpart is off-screen.
        assert any("Open buys **640**" in m.value for m in at.markdown)


class TestMarketChips:
    def test_a_link_can_start_at_a_market_chip(self, no_bilateral_db):
        # Dragged from the chip onto a buy: the market becomes the sell side.
        at = _emit(
            _run([BUY_LEG]),
            {"type": "link_request", "from_market": "SWPP", "to": "session:0"},
        )
        pending = at.session_state["mv_pending"]
        assert pending["buy_key"] == "session:0"
        assert pending["sell_key"].startswith("market:SWPP:Sell:")
        _click(at, "Create link")
        assert at.session_state["mv_links"][0].mwh == 1600

    def test_a_link_from_a_chip_onto_a_sell_puts_the_market_on_the_buy_side(
        self, no_bilateral_db
    ):
        at = _emit(
            _run([SELL_LEG]),
            {"type": "link_request", "from_market": "CEN", "to": "session:0"},
        )
        assert at.session_state["mv_pending"]["buy_key"].startswith("market:CEN:Buy:")

    def test_dropping_a_link_on_a_market_chip_parks_the_open_position(self, no_bilateral_db):
        at = _emit(
            _run([BUY_LEG]),
            {"type": "link_request", "from": "session:0", "market": "CAISO"},
        )
        assert at.session_state["mv_pending"]["sell_key"].startswith("market:CAISO:Sell:")
        _click(at, "Create link")
        assert not at.exception, [e.value for e in at.exception]
        links = at.session_state["mv_links"]
        assert len(links) == 1
        # A market is a sink with no schedule of its own, so it takes the
        # whole open position rather than an overlap.
        assert links[0].mw_by_hour == {h: 100.0 for h in range(7, 23)}
        assert any("Open buys **0**" in m.value for m in at.markdown)

    def test_a_sell_parks_on_the_buy_side_of_a_market(self, no_bilateral_db):
        at = _emit(
            _run([SELL_LEG]),
            {"type": "link_request", "from": "session:0", "market": "AESO"},
        )
        assert at.session_state["mv_pending"]["buy_key"].startswith("market:AESO:Buy:")


class TestDetailStrip:
    def test_it_prompts_when_nothing_is_focused(self, no_bilateral_db):
        at = _run([BUY_LEG])
        assert any("drag a square to move it" in c.value for c in at.caption)

    def test_it_describes_the_focused_square(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "select", "key": "session:0"})
        assert any(
            "AZPS" in m.value and "1,600 MWh open" in m.value for m in at.markdown
        )
