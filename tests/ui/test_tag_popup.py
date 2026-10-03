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
from dialog_helpers import let_go_of_closed_widgets
from domain.tags import MAG_PSE
from ui.scheduling.state import BIDFILE_DIALOG, LINK_DIALOG, PENDING_TAG

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")
FLOW = date.today() + timedelta(days=1)


#: What the desk's own mapping table answers for the counterparties these
#: tests trade. Stubbed rather than read live so the fast tier keeps working
#: with no connection to the mapping server; the live wiring is covered by
#: tests/data/test_markets.py's db-marked tests.
PSE_CODES = {"AZPS": "APS01", "BPAT": "BPAP01", "EPE": "EPEC01"}


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )
    monkeypatch.setattr(
        "ui.scheduling.state.pse_for_market", lambda market: PSE_CODES.get(market)
    )
    monkeypatch.setattr(
        "ui.scheduling.tag.known_pse_codes", lambda: sorted(PSE_CODES.values()) + [MAG_PSE]
    )
    monkeypatch.setattr("ui.scheduling.tag.reservation", fake_reservation)


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
    return let_go_of_closed_widgets(at)


def _tagging(trades=(BUY_LEG, SELL_LEG), frm="session:0", to="session:1", market=None):
    """A link, opened again by clicking it — which is where tagging starts."""
    at = _run(trades)
    request = {"type": "link_request", "from": frm}
    request["market" if market else "to"] = market or to
    at = _click(_emit(at, request), "Create link")
    at = _emit(_in_dialog(at), {"type": "link_click", "link_id": "L1"})
    return _in_dialog(at).run()


def _drawing(trades=(BUY_LEG, SELL_LEG)):
    """A link dragged but not yet created — the popup open on it."""
    at = _emit(_run(trades), {
        "type": "link_request", "from": "session:0", "to": "session:1"
    })
    return _in_dialog(at)


def _tag(at, link_id="L1"):
    return at.session_state["mv_tags"][link_id]


def _fill(at, link_id="L1", gca="GWA", lca="BPAT"):
    at.text_input(f"mv_tag_{link_id}_Source_gca").set_value(gca)
    at.text_input(f"mv_tag_{link_id}_Sink_lca").set_value(lca)
    return _in_dialog(at).run()


#: The one OASIS reservation these tests know about — the real one from the
#: desk's September ABEX-SWPW tag. Stubbed so the fast tier needs no
#: connection; the live lookup is covered by tests/data/test_reservations.py.
OASIS = {
    # Still good on the flow date these tests use.
    "109267045": {
        "aref": "109267045",
        "path": "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/",
        "provider": "NWMT", "por": "MATL.NWMT", "pod": "CROSSOVER",
        "status": "CONFIRMED", "ts_class": "FIRM", "granted_mw": 40.0,
        "first_date": FLOW - timedelta(days=2), "last_date": FLOW + timedelta(days=2),
    },
    # Long expired — a number off an old tag, which is the ordinary case.
    "110616287": {
        "aref": "110616287",
        "path": "W/TEPC/TEPC-AZPS/TEPC.SYS-SAGUARO500/",
        "provider": "TEPC", "por": "TEPC.SYS", "pod": "SAGUARO500",
        "status": "CONFIRMED", "ts_class": "FIRM", "granted_mw": 25.0,
        "first_date": FLOW - timedelta(days=90), "last_date": FLOW - timedelta(days=60),
    },
}


def fake_reservation(aref):
    return OASIS.get((aref or "").strip())


def _type_reservation(at, *arefs, link_id="L1"):
    """Add a transmission row per reference, the way the editor reports one."""
    key = _transmission_key(at, link_id)
    at.session_state[key] = {
        "edited_rows": {}, "deleted_rows": [],
        "added_rows": [{"reservation": a} for a in arefs],
    }
    return _in_dialog(at).run()


def _transmission_key(at, link_id="L1"):
    """The editor's key, rebuilt the way ui.scheduling.tag builds it — it
    carries a version so a filled path can be shown without deleting the
    state of a widget still on screen."""
    ver_key = f"mv_tag_{link_id}_transmissions_ver"
    ver = at.session_state[ver_key] if ver_key in at.session_state else 0
    return f"mv_tag_{link_id}_transmissions" + (f"_v{ver}" if ver else "")


def _transmissions(at, link_id="L1"):
    return at.session_state["mv_tags"][link_id]["transmissions"]


def _numbers(at, link_id="L1"):
    """The "# trans" cells that actually got filled in."""
    return [
        row["reservation"] for row in _transmissions(at, link_id) if row["reservation"]
    ]


#: The opening string these two counterparties imply, in their PSE codes.
OPENING_PATH = "??-APS01-MAG001-BPAP01-??"

#: One round later, as a scheduler would paste it back — the desk's own
#: wording, from the TODO.
CHAT_PATH = (
    "HE1-6; Mead MAG (G @ SWPW) (WALC tx PPK>MEAD, 7f 110704717) - MAG - "
    "EEMU - TNSK - MGM sink"
)


def _path_entry_key(at, link_id="L1"):
    """The paste box's key, rebuilt the way ui.scheduling.tag builds it. It
    carries a version because the box empties itself after each round."""
    ver_key = f"mv_tag_{link_id}_path_entry_ver"
    ver = at.session_state[ver_key] if ver_key in at.session_state else 0
    return f"mv_tag_{link_id}_path_entry" + (f"_v{ver}" if ver else "")


def _paste_path(at, text, link_id="L1"):
    at.text_input(_path_entry_key(at, link_id)).set_value(text)
    return _in_dialog(at).run()


def _shown_path(at):
    """What the popup is displaying as the latest path — the copyable block,
    not the box."""
    return [block.value for block in at.code]


def _tag_expander(at):
    return [block for block in at.expander if block.label == "Tag details"][0]


#: One assembled historical tag (the shape data.tags_history.assemble()
#: produces), sharing both of _tagging()'s counterparties (APS01, BPAP01 —
#: AZPS and BPAT's codes under PSE_CODES above) so "Lookup old tags" finds
#: it. The reservation number is the fixture OASIS already knows, so
#: applying this route exercises the existing OASIS fill too.
HISTORICAL_TAG = {
    "tag_index": 1,
    "gca": "GWA",
    "lca": "SWPW",
    "flow_date": date(2026, 9, 1),
    "market_path": [
        {"pse": "APS01", "product": "G-F", "contracts": ""},
        {"pse": "MAG001", "product": "", "contracts": ""},
        {"pse": "BPAP01", "product": "L", "contracts": ""},
    ],
    "physical_path": [
        {"type": "G", "tp": "", "por_name": "RIMROCK", "por_ca": "GWA",
         "pod_name": "", "pod_ca": "", "reservations": []},
        # One of each kind a reservation number can be: still good, long
        # expired, not a reference at all, and one of someone else's.
        {"type": "T", "tp": "NWMT", "por_name": "MATL.NWMT", "por_ca": "NWMT",
         "pod_name": "CROSSOVER", "pod_ca": "NWMT",
         "reservations": [{"contract": "109267045", "product": "7-F"}]},
        {"type": "T", "tp": "TEPC", "por_name": "TEPC.SYS", "por_ca": "TEPC",
         "pod_name": "SAGUARO500", "pod_ca": "TEPC",
         "reservations": [{"contract": "110616287", "product": "7-F"}]},
        {"type": "T", "tp": "EPE", "por_name": "EPE.SYS", "por_ca": "EPE",
         "pod_name": "PVPV", "pod_ca": "EPE",
         "reservations": [{"contract": "EPE5PVPV", "product": "7-F"}]},
        {"type": "T", "tp": "WALC", "por_name": "PPK", "por_ca": "WALC",
         "pod_name": "MEAD", "pod_ca": "WALC",
         "reservations": [{"contract": "999999999", "product": "7-F"}]},
        {"type": "L", "tp": "", "por_name": "", "por_ca": "",
         "pod_name": "SWPW_HUB", "pod_ca": "SWPW", "reservations": []},
    ],
}

#: A route between counterparties this link never touches — shares no PSE
#: with AZPS/BPAT — so it must never surface regardless of anything else
#: about it.
UNRELATED_TAG = {
    "tag_index": 2,
    "gca": "OTHER",
    "lca": "OTHER",
    "flow_date": date(2026, 9, 1),
    "market_path": [
        {"pse": "ABEX", "product": "G-F", "contracts": ""},
        {"pse": "MAG001", "product": "", "contracts": ""},
        {"pse": "EEMU24", "product": "L", "contracts": ""},
    ],
    "physical_path": [],
}


def _stub_history(monkeypatch, records=(), error=None, capture=None):
    def fake(start, stop):
        if capture is not None:
            capture["start"], capture["stop"] = start, stop
        return list(records), error

    monkeypatch.setattr("ui.scheduling.tag.load_west_tags", fake)


def _use_route(at):
    return _click(at, "Use this route")


pytestmark = pytest.mark.usefixtures("no_bilateral_db")


class TestWhereTaggingStarts:
    def test_clicking_a_link_opens_its_tag_alongside_its_schedule(self):
        at = _tagging()
        assert not at.exception, [e.value for e in at.exception]
        assert "L1" in at.session_state["mv_tags"]

    def test_a_link_being_drawn_has_its_tag_fields_too(self):
        # One popup, so the whole job is on screen before the link exists —
        # the tag is filed under PENDING_TAG until Create link gives it an
        # id to be filed under.
        at = _drawing()
        assert list(at.session_state["mv_tags"]) == [PENDING_TAG]
        assert at.text_input("mv_tag_pending_Source_gca") is not None

    def test_what_was_typed_before_the_link_existed_moves_onto_it(self):
        at = _drawing()
        at.text_input("mv_tag_pending_Source_gca").set_value("GWA")
        at = _in_dialog(at).run()
        at = _click(at, "Create link")
        assert list(at.session_state["mv_tags"]) == ["L1"]
        assert _tag(at)["source"]["gca"] == "GWA"

    def test_abandoning_a_link_takes_its_tag_with_it(self):
        # Or the next link drawn would open holding someone else's path.
        at = _drawing()
        at.text_input("mv_tag_pending_Source_gca").set_value("GWA")
        at = _click(_in_dialog(at).run(), "Cancel")
        assert at.session_state["mv_tags"] == {}

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

    def test_the_market_path_is_written_in_pse_codes(self):
        # Not AZPS and BPAT, which are the desk's internal market names: a
        # tag's market path is PSE codes, and the mapping supplies them so
        # nobody has to know both.
        assert [row["pse"] for row in _tag(_tagging())["market_path"]] == [
            "APS01", MAG_PSE, "BPAP01"
        ]

    def test_a_link_into_a_market_puts_mag_at_that_end(self):
        at = _tagging(trades=[BUY_LEG], market="SWPW")
        assert [row["pse"] for row in _tag(at)["market_path"]] == ["APS01", MAG_PSE]


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


class TestTheReservationFillsInItsPath:
    """Only the number is typed. `PathName` in the desk's OASIS summary is
    character-for-character the string the sheet's Path column wants, so a
    path nobody types is a path nobody can mistype."""

    def test_a_reference_fills_its_path(self):
        at = _type_reservation(_fill(_tagging()), "109267045")
        rows = [r for r in _transmissions(at) if r.get("reservation")]
        assert rows[0]["path"] == "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/"

    def test_the_editor_is_shown_the_filled_path(self):
        # The fill happens in Python, so the widget has to be renamed for
        # the trader to see it — the seed alone would sit under the old diff.
        at = _type_reservation(_fill(_tagging()), "109267045")
        seed = at.session_state["mv_tag_L1_transmissions_seed"]
        assert "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/" in seed["path"].tolist()

    def test_two_rows_each_get_their_own(self):
        at = _type_reservation(_fill(_tagging()), "109267045", "110616287")
        paths = [r["path"] for r in _transmissions(at) if r.get("reservation")]
        assert paths == [
            "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/",
            "W/TEPC/TEPC-AZPS/TEPC.SYS-SAGUARO500/",
        ]

    def test_it_says_what_the_reservation_grants(self):
        # Tagging more MW than the reservation covers is the kind of error
        # that only surfaces days later.
        at = _type_reservation(_fill(_tagging()), "109267045")
        assert any("40 MW granted" in c.value for c in at.caption)

    def test_it_settles_rather_than_filling_on_every_run(self):
        at = _type_reservation(_fill(_tagging()), "109267045")
        before = at.session_state["mv_tag_L1_transmissions_ver"]
        at = _in_dialog(at).run()
        assert at.session_state["mv_tag_L1_transmissions_ver"] == before


class TestWhatItLeavesAlone:
    def test_a_reference_that_is_not_one_is_never_looked_up(self):
        # FCATBTEP goes in the same column and is not an OASIS reference.
        at = _type_reservation(_fill(_tagging()), "FCATBTEP")
        rows = [r for r in _transmissions(at) if r.get("reservation")]
        assert not rows[0].get("path")
        assert not [c for c in at.caption if "FCATBTEP" in c.value]

    def test_a_reservation_that_is_not_ours_leaves_the_path_empty(self):
        at = _type_reservation(_fill(_tagging()), "110752124")
        rows = [r for r in _transmissions(at) if r.get("reservation")]
        assert not rows[0].get("path")

    def test_and_says_so_rather_than_failing_quietly(self):
        at = _type_reservation(_fill(_tagging()), "110752124")
        assert any("isn't one of our own" in c.value for c in at.caption)

    def test_a_path_the_scheduler_typed_is_never_overwritten(self):
        at = _fill(_tagging())
        key = _transmission_key(at)
        at.session_state[key] = {
            "edited_rows": {}, "deleted_rows": [],
            "added_rows": [{"reservation": "109267045", "path": "my own wording"}],
        }
        at = _in_dialog(at).run()
        rows = [r for r in _transmissions(at) if r.get("reservation")]
        assert rows[0]["path"] == "my own wording"

    def test_but_a_path_it_filled_follows_a_changed_reference(self):
        at = _type_reservation(_fill(_tagging()), "109267045")
        key = _transmission_key(at)
        at.session_state[key] = {
            "edited_rows": {0: {"reservation": "110616287"}},
            "added_rows": [], "deleted_rows": [],
        }
        at = _in_dialog(at).run()
        rows = [r for r in _transmissions(at) if r.get("reservation")]
        assert rows[0]["path"] == "W/TEPC/TEPC-AZPS/TEPC.SYS-SAGUARO500/"


class TestThePathString:
    """The path as it stands on chat: generated, displayed to be copied, and
    replaced by whatever comes back. Nothing here reads it — on a link MAG
    doesn't have to tag, carrying the string *is* the job."""

    def test_a_new_link_opens_with_the_only_part_anyone_knows(self):
        at = _drawing()
        assert at.session_state["mv_tags"][PENDING_TAG]["path_string"] == OPENING_PATH

    def test_it_is_shown_where_it_can_be_copied(self):
        # st.code, not st.text: it is the one element that comes with a copy
        # button, and the whole point is pasting this into a chat window.
        assert OPENING_PATH in _shown_path(_tagging())

    def test_pasting_the_next_round_replaces_it(self):
        at = _paste_path(_tagging(), CHAT_PATH)
        assert _tag(at)["path_string"] == CHAT_PATH

    def test_and_the_display_follows_on_that_same_run(self):
        # The entry is read before the display is drawn, so a paste shows up
        # without costing a rerun inside the dialog.
        at = _paste_path(_tagging(), CHAT_PATH)
        assert CHAT_PATH in _shown_path(at)
        assert OPENING_PATH not in _shown_path(at)

    def test_the_box_is_empty_again_for_the_round_after(self):
        at = _paste_path(_tagging(), CHAT_PATH)
        assert at.text_input(_path_entry_key(at)).value == ""

    def test_an_empty_box_leaves_the_path_where_it_was(self):
        at = _paste_path(_tagging(), CHAT_PATH)
        at = _paste_path(at, "   ")
        assert _tag(at)["path_string"] == CHAT_PATH

    def test_it_is_still_there_when_the_link_is_opened_again(self):
        at = _paste_path(_tagging(), CHAT_PATH)
        at = _click(at, "Cancel")
        at = _emit(_in_dialog(at), {"type": "link_click", "link_id": "L1"})
        assert CHAT_PATH in _shown_path(_in_dialog(at).run())

    def test_what_was_pasted_before_the_link_existed_moves_onto_it(self):
        at = _paste_path(_drawing(), CHAT_PATH, link_id=PENDING_TAG)
        at = _click(at, "Create link")
        assert _tag(at)["path_string"] == CHAT_PATH

    def test_a_link_into_a_market_ends_at_mag_rather_than_a_placeholder(self):
        # Selling into SWPW *is* MAG sinking it — there's nobody past that.
        at = _tagging(trades=(BUY_LEG,), frm="session:0", market="SWPW")
        assert "??-APS01-MAG001(s)" in _shown_path(at)


class TestWhereTheTagFieldsSit:
    """Behind an expander, because most links never need them — see
    ui.scheduling.tag's docstring."""

    def test_shut_on_a_link_being_drawn(self):
        assert _tag_expander(_drawing()).proto.expanded is False

    def test_open_on_a_link_being_looked_at_again(self):
        assert _tag_expander(_tagging()).proto.expanded is True

    def test_the_fields_are_reachable_either_way(self):
        # Collapsed is not absent: the widgets render, hold their state and
        # feed the tag exactly as before.
        at = _drawing()
        at.text_input("mv_tag_pending_Source_gca").set_value("GWA")
        at = _in_dialog(at).run()
        assert at.session_state["mv_tags"][PENDING_TAG]["source"]["gca"] == "GWA"

    def test_the_path_string_is_never_behind_them(self):
        at = _tagging()
        assert not [block.value for block in _tag_expander(at).code]


class TestLookingUpOldTags:
    """"Lookup old tags": the button under the path string. Reads OATI
    history (stubbed here — the live wiring is covered by
    tests/data/test_tags_history.py and tests/domain/test_tag_recipes.py)
    and ranks it against what the tag already knows. No path string is read
    anywhere in this — tag_query works off the market path and GCA/LCA."""

    def test_a_shared_counterparty_surfaces_a_route(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert any("GWA" in m.value for m in at.markdown)

    def test_it_works_before_anything_is_typed(self, monkeypatch):
        # The whole point: the counterparties are already known from the
        # market path's own default, before a single field is filled in.
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_drawing(), "Lookup old tags")
        assert not at.exception, [e.value for e in at.exception]
        assert any("GWA" in m.value for m in at.markdown)

    def test_an_unrelated_route_never_surfaces(self, monkeypatch):
        _stub_history(monkeypatch, [UNRELATED_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert "shares a counterparty" in " ".join(c.value for c in at.caption)

    def test_an_error_is_a_warning_not_a_crash(self, monkeypatch):
        _stub_history(monkeypatch, [], error="no route to host")
        at = _click(_tagging(), "Lookup old tags")
        assert not at.exception, [e.value for e in at.exception]
        assert any("no route to host" in w.value for w in at.warning)

    def test_it_searches_the_trailing_twelve_months(self, monkeypatch):
        captured = {}
        _stub_history(monkeypatch, [], capture=captured)
        _click(_tagging(), "Lookup old tags")
        assert (captured["stop"] - captured["start"]).days == 365


class TestUsingAHistoricalRoute:
    """"Use this route" — display-only until this button is pressed."""

    def _result(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        return _use_route(_click(_tagging(), "Lookup old tags"))

    def test_nothing_is_written_before_the_button_is_pressed(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert _tag(at)["source"]["gca"] == ""

    def test_it_fills_the_market_path(self, monkeypatch):
        at = self._result(monkeypatch)
        assert [row["pse"] for row in _tag(at)["market_path"]] == [
            "APS01", "MAG001", "BPAP01"
        ]

    def test_it_fills_gca_and_lca(self, monkeypatch):
        at = self._result(monkeypatch)
        assert _tag(at)["source"]["gca"] == "GWA"
        assert _tag(at)["sink"]["lca"] == "SWPW"

    def test_it_fills_the_source_and_sink_points(self, monkeypatch):
        at = self._result(monkeypatch)
        assert _tag(at)["source"]["point"] == "RIMROCK"
        assert _tag(at)["sink"]["point"] == "SWPW_HUB"

    def test_it_carries_a_reservation_still_good_for_the_flow_date(
        self, monkeypatch
    ):
        at = self._result(monkeypatch)
        assert _numbers(at) == ["109267045", "EPE5PVPV"]

    def test_and_the_oasis_lookup_fills_its_path_on_the_same_click(self, monkeypatch):
        at = self._result(monkeypatch)
        assert _transmissions(at)[0]["path"] == OASIS["109267045"]["path"]

    def test_it_leaves_carbon_copy_and_the_name_alone(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _tagging()
        at.text_input("mv_tag_L1_name").set_value("KEEP-ME")
        at = _in_dialog(at).run()
        at = _use_route(_click(at, "Lookup old tags"))
        assert _tag(at)["name"] == "KEEP-ME"
        assert _tag(at)["carbon_copy"] == []

    def test_the_expander_opens_on_a_link_still_being_drawn(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _use_route(_click(_drawing(), "Lookup old tags"))
        assert _tag_expander(at).proto.expanded is True

class TestWhichReservationNumbersCarryForward:
    """A reservation number belongs to a day, not to a route — see
    ui.scheduling.tag._carry_reservations."""

    def _result(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        return _use_route(_click(_tagging(), "Lookup old tags"))

    def test_one_still_good_on_the_flow_date_is_carried(self, monkeypatch):
        assert "109267045" in _numbers(self._result(monkeypatch))

    def test_one_that_is_not_a_reference_is_carried_as_it_stands(self, monkeypatch):
        # EPE5PVPV isn't dated, so there is nothing about it to expire.
        assert "EPE5PVPV" in _numbers(self._result(monkeypatch))

    def test_an_expired_one_is_not(self, monkeypatch):
        # The whole point: a number off a tag run two months ago is dead,
        # and a stale number is worse than an empty cell.
        assert "110616287" not in _numbers(self._result(monkeypatch))

    def test_and_neither_is_its_path(self, monkeypatch):
        # A path is only ever looked up, never copied off an old route —
        # otherwise it reads like one this day's reservation justifies,
        # with nothing behind it.
        at = self._result(monkeypatch)
        assert OASIS["110616287"]["path"] not in [
            r["path"] for r in _transmissions(at)
        ]

    def test_one_that_is_not_ours_is_not_carried_either(self, monkeypatch):
        # Unverifiable is not the same as good: 999999999 is nobody's here.
        assert "999999999" not in _numbers(self._result(monkeypatch))

    def test_no_row_ever_arrives_with_a_path_and_no_reference(self, monkeypatch):
        # The invariant: every path on screen has a reference behind it
        # that a scheduler can check. The reverse is fine.
        for row in _transmissions(self._result(monkeypatch)):
            assert not (row["path"] and not row["reservation"])

    def test_it_says_what_it_did_not_carry(self, monkeypatch):
        at = self._result(monkeypatch)
        said = " ".join(c.value for c in at.caption)
        assert "not carried" in said
        assert "not ours to check" in said


#: The same route, but sinking into CISO rather than SWPW — what an
#: EPE-MAG-SWPW link kept being offered before a market end settled its LCA.
INTO_CISO = {
    "tag_index": 3,
    "gca": "GWA",
    "lca": "CISO",
    "flow_date": date(2026, 9, 1),
    "market_path": [
        {"pse": "APS01", "product": "G-F", "contracts": ""},
        {"pse": "MAG001", "product": "L", "contracts": ""},
    ],
    "physical_path": [
        {"type": "G", "tp": "", "por_name": "PALOVERDE", "por_ca": "GWA",
         "pod_name": "", "pod_ca": "", "reservations": []},
        {"type": "L", "tp": "", "por_name": "", "por_ca": "",
         "pod_name": "CISO_HUB", "pod_ca": "CISO", "reservations": []},
    ],
}


class TestALinkIntoAMarket:
    """A market end is where MAG sinks or sources the power, so its control
    area is settled — not a field the lookup may guess around."""

    def _into_swpw(self, monkeypatch, history):
        _stub_history(monkeypatch, history)
        at = _tagging(trades=(BUY_LEG,), frm="session:0", market="SWPW")
        return _click(at, "Lookup old tags")

    def test_a_route_sinking_somewhere_else_is_not_offered(self, monkeypatch):
        at = self._into_swpw(monkeypatch, [INTO_CISO])
        assert "shares a counterparty" in " ".join(c.value for c in at.caption)

    def test_while_one_sinking_into_that_market_is(self, monkeypatch):
        into_swpw = dict(INTO_CISO, tag_index=4, lca="SWPW")
        into_swpw["physical_path"] = [
            dict(INTO_CISO["physical_path"][0]),
            {"type": "L", "tp": "", "por_name": "", "por_ca": "",
             "pod_name": "SWPW_HUB", "pod_ca": "SWPW", "reservations": []},
        ]
        at = self._into_swpw(monkeypatch, [INTO_CISO, into_swpw])
        shown = " ".join(m.value for m in at.markdown)
        assert "SWPW_HUB" in shown
        assert "CISO_HUB" not in shown


class TestWhatARouteCardShows:
    def test_where_the_power_starts_and_ends(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert any(
            "RIMROCK (GWA) → SWPW_HUB (SWPW)" in m.value for m in at.markdown
        )

    def test_the_wires_it_crosses(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        said = " ".join(c.value for c in at.caption)
        assert "NWMT MATL.NWMT>CROSSOVER" in said
        assert "TEPC TEPC.SYS>SAGUARO500" in said

    def test_and_how_often_the_desk_has_run_it(self, monkeypatch):
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert any("GWA>SWPW" in m.value for m in at.markdown)
        assert any("used 1×" in c.value for c in at.caption)

    def test_the_pse_chain_it_was_run_under(self, monkeypatch):
        # Without this two routes differing only in a firm/non-firm energy
        # product are two cards nobody can tell apart.
        _stub_history(monkeypatch, [HISTORICAL_TAG])
        at = _click(_tagging(), "Lookup old tags")
        assert any(
            "APS01 (G-F) > MAG001 > BPAP01 (L)" in c.value for c in at.caption
        )
