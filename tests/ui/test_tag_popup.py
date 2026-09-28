"""ui/scheduling/tag.py — the Tag tab of the link popup, through the
Scheduling View page.

The board is a custom component, so clicking a link reaches Python the way
any board event does: `session_state["mv_board"] = {...}` — see
tests/ui/test_scheduling_view.py's own docstring. Every generated file goes
to tmp_path via `redirect_tags`; nothing here touches Y:\\West.
"""

from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

import data.tagfiles.bilateral as tagfile
from domain.tags import MAG_PSE
from ui.scheduling.state import BIDFILE_DIALOG, LINK_DIALOG

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")
FLOW = date.today() + timedelta(days=1)


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


@pytest.fixture
def redirect_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(tagfile, "TAG_FOLDER", tmp_path)
    monkeypatch.setattr(tagfile, "TEST_MODE", True)
    return tmp_path


def hourly_trade(direction, counterparty, location, hours, mw, flow_date=FLOW):
    return {
        "direction": direction, "counterparty": counterparty, "location": location,
        "index": None, "price": 73.0, "is_monthly": False,
        "schedule": [(flow_date, h, float(mw)) for h in hours],
    }


BUY_LEG = hourly_trade("Buy", "AZPS", "PALOVERDE500", range(7, 23), 100)
SELL_LEG = hourly_trade("Sell", "BPAT", "MIDC", range(7, 23), 60)


def _in_dialog(at):
    """Tell the page its modal is still open — see test_bidfile.py."""
    at.session_state[BIDFILE_DIALOG] = False
    at.session_state[LINK_DIALOG] = False
    return at


def _run(trades=()):
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.session_state["trades"] = list(trades)
    return at.run()


def _emit(at, event):
    event = dict(event)
    event["seq"] = at.session_state["mv_last_seq"] + 1
    at.session_state["mv_board"] = event
    return at.run()


def _click(at, label):
    [b for b in _in_dialog(at).button if b.label == label][0].click().run()
    return at


def _tagging(trades=(BUY_LEG, SELL_LEG), frm="session:0", to="session:1", market=None):
    """A link, opened again by clicking it — which is where tagging starts."""
    at = _run(trades)
    request = {"type": "link_request", "from": frm}
    request["market" if market else "to"] = market or to
    at = _click(_emit(at, request), "Create link")
    at = _emit(_in_dialog(at), {"type": "link_click", "link_id": "L1"})
    return _in_dialog(at).run()


def _tag(at, link_id="L1"):
    return at.session_state["mv_tags"][link_id]


def _fill(at, link_id="L1", gca="GWA", lca="BPAT"):
    at.text_input(f"mv_tag_{link_id}_Source_gca").set_value(gca)
    at.text_input(f"mv_tag_{link_id}_Sink_lca").set_value(lca)
    return _in_dialog(at).run()


pytestmark = pytest.mark.usefixtures("no_bilateral_db")


class TestWhereTaggingStarts:
    def test_clicking_a_link_opens_its_tag_alongside_its_schedule(self):
        at = _tagging()
        assert not at.exception, [e.value for e in at.exception]
        assert "L1" in at.session_state["mv_tags"]

    def test_a_link_being_drawn_has_nothing_to_tag_yet(self):
        # The tag belongs to a link, and until Create link is pressed there
        # isn't one.
        at = _emit(_run([BUY_LEG, SELL_LEG]), {
            "type": "link_request", "from": "session:0", "to": "session:1", "seq": 1
        })
        assert at.session_state["mv_tags"] == {}
        assert any("Create the link first" in i.value for i in at.info)

    def test_the_tag_goes_when_the_link_does(self):
        at = _click(_tagging(), "Delete")
        assert at.session_state["mv_tags"] == {}


class TestWhatTheLinkFillsIn:
    def test_the_schedule_comes_from_the_link_not_the_trader(self):
        tag = _tag(_tagging())
        assert sorted(tag["mw_by_hour"]) == list(range(7, 23))
        assert set(tag["mw_by_hour"].values()) == {60.0}  # the smaller side

    def test_it_follows_the_link_when_the_hours_are_edited(self):
        at = _tagging()
        # Re-opening a tag whose link has since been re-allocated must not
        # show the old schedule — the same popup holds both.
        at.session_state["mv_links"][0].mw_by_hour = {7: 5.0}
        at = _emit(_in_dialog(at), {"type": "link_click", "link_id": "L1"})
        assert _tag(_in_dialog(at).run())["mw_by_hour"] == {7: 5.0}

    def test_the_two_counterparties_name_the_tag_and_its_markets(self):
        tag = _tag(_tagging())
        assert tag["name"] == "AZPS-BPAT"
        assert (tag["source"]["market"], tag["sink"]["market"]) == ("AZPS", "BPAT")

    def test_the_market_path_starts_with_mag_between_them(self):
        assert [row["pse"] for row in _tag(_tagging())["market_path"]] == [
            "AZPS", MAG_PSE, "BPAT"
        ]

    def test_a_link_into_a_market_puts_mag_at_that_end(self):
        at = _tagging(trades=[BUY_LEG], market="SWPW")
        assert [row["pse"] for row in _tag(at)["market_path"]] == ["AZPS", MAG_PSE]


class TestWhatTheTraderTypes:
    def test_a_typed_field_reaches_the_tag(self):
        # Into `mv_tags`, which is plain session state — not left in the
        # widget, whose state Streamlit discards on any run that doesn't
        # render it (a closed dialog renders none of these). What comes
        # *back* out of it on reopening can't be checked here: AppTest
        # reads the previous run's widget tree, so a widget that legitimately
        # vanished makes the next run raise. Verified in the browser
        # instead — see PROJECT.md.
        at = _fill(_tagging())
        assert _tag(at)["source"]["gca"] == "GWA"
        assert _tag(at)["sink"]["lca"] == "BPAT"

    def test_a_row_added_to_the_market_path_reaches_the_tag(self):
        at = _fill(_tagging())
        at.session_state["mv_tag_L1_market_path"] = {
            "edited_rows": {}, "deleted_rows": [],
            "added_rows": [{"pse": "EEMU", "product": ""}],
        }
        at = _in_dialog(at).run()
        assert [row["pse"] for row in _tag(at)["market_path"]][-1] == "EEMU"

    def test_the_seed_of_a_table_never_moves_while_it_is_edited(self):
        # The same trap as the schedule grid's double-entry bug: a
        # data_editor's element id is a hash of the data it is handed, so a
        # seed rebuilt from its own output drops the next edit.
        at = _fill(_tagging())
        before = at.session_state["mv_tag_L1_market_path_seed"].copy()
        at.session_state["mv_tag_L1_market_path"] = {
            "edited_rows": {0: {"pse": "TYPED"}}, "added_rows": [], "deleted_rows": [],
        }
        at = _in_dialog(at).run()
        after = at.session_state["mv_tag_L1_market_path_seed"]
        assert after["pse"].tolist() == before["pse"].tolist()
        assert _tag(at)["market_path"][0]["pse"] == "TYPED"


class TestGenerating:
    def test_an_unfinished_tag_cannot_be_generated(self):
        at = _tagging()
        assert any("GCA is required." in e.value for e in at.error)
        assert at.button("mv_tag_L1_generate").disabled

    def test_a_finished_one_can(self, redirect_tags):
        at = _fill(_tagging())
        assert not at.button("mv_tag_L1_generate").disabled
        at = _click(at, "Generate tag file")
        written = list((redirect_tags / "Test").glob("*.xlsx"))
        assert len(written) == 1, [s.value for s in at.error]
        assert any("Tag written to" in s.value for s in at.success)

    def test_what_it_writes_is_the_links_own_schedule(self, redirect_tags):
        _click(_fill(_tagging()), "Generate tag file")
        path = next((redirect_tags / "Test").glob("*.xlsx"))
        ws = openpyxl.load_workbook(path)["BIDS"]
        # PPT HE7-22 at 60 MW, on the sheet's EPT rows (HE10-24 + the first
        # star) — see data/tagfiles/bilateral.py.
        assert [r for r in range(14, 41) if ws.cell(r, 4).value is not None] == list(
            range(23, 39)
        )
        assert ws.cell(23, 4).value == 60.0
        assert ws["D8"].value == "GWA"

    def test_generating_the_same_tag_twice_says_so_rather_than_replacing_it(
        self, redirect_tags
    ):
        at = _click(_fill(_tagging()), "Generate tag file")
        at = _click(_in_dialog(at), "Generate tag file")
        assert any("already exists" in e.value for e in at.error)
        assert len(list((redirect_tags / "Test").glob("*.xlsx"))) == 1

    def test_unless_the_trader_ticks_overwrite(self, redirect_tags):
        at = _click(_fill(_tagging()), "Generate tag file")
        at.checkbox("mv_tag_L1_overwrite").set_value(True)
        at = _click(_in_dialog(at).run(), "Generate tag file")
        assert any("Tag written to" in s.value for s in at.success)
        assert len(list((redirect_tags / "Test").glob("*.xlsx"))) == 1

    def test_a_write_that_fails_is_reported_not_swallowed(self, redirect_tags, monkeypatch):
        def boom(tag, overwrite=False):
            raise RuntimeError("the drive is not mounted")

        monkeypatch.setattr(tagfile, "write_tag_file", boom)
        at = _click(_fill(_tagging()), "Generate tag file")
        assert any("the drive is not mounted" in e.value for e in at.error)
