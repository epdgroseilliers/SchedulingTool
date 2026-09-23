"""ui/scheduling/bidfile.py — the SWPW bid-file builder popup, through the
Scheduling View page.

The board is a custom component, so a chip click reaches Python exactly the
way any board event does: `session_state["mv_board"] = {...}` — see
tests/ui/test_scheduling_view.py's own docstring. Every write goes to
tmp_path via the `redirect_swpw` fixture — never Z:\\.
"""

import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

import data.bidfiles.swpw as swpw

PAGE_PATH = str(Path(__file__).resolve().parents[2] / "pages" / "1_Scheduling_View.py")
FLOW = date.today() + timedelta(days=1)


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


@pytest.fixture
def redirect_swpw(tmp_path, monkeypatch):
    monkeypatch.setattr(swpw, "BID_FOLDER", tmp_path)
    monkeypatch.setattr(swpw, "TEST_MODE", True)
    monkeypatch.setattr(swpw, "TEST_FILENAME", "test-output.xlsm")
    # write_bid_file needs a VBA-shell donor to produce a genuine
    # macro-enabled file (see data/bidfiles/swpw.py's _save_as_macro_enabled)
    # — a plain workbook with a dummy xl/vbaProject.bin entry is enough,
    # since only its raw bytes get copied, never validated.
    shell = tmp_path / swpw.VBA_SHELL_NAME
    openpyxl.Workbook().save(shell)
    with zipfile.ZipFile(shell, "a") as z:
        z.writestr("xl/vbaProject.bin", b"test-only, not a real vba project")
    return tmp_path


def hourly_trade(direction, counterparty, location, hours, mw, flow_date=FLOW):
    return {
        "direction": direction, "counterparty": counterparty, "location": location,
        "index": None, "price": 73.0, "is_monthly": False,
        "schedule": [(flow_date, h, float(mw)) for h in hours],
    }


BUY_LEG = hourly_trade("Buy", "AZPS", "PALOVERDE500", range(7, 23), 100)


def _run(trades=()):
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.session_state["trades"] = list(trades)
    return at.run()


def _emit(at, event, seq=None):
    event = dict(event)
    event["seq"] = seq if seq is not None else at.session_state["mv_last_seq"] + 1
    at.session_state["mv_board"] = event
    return at.run()


def _click(at, label):
    [b for b in at.button if b.label == label][0].click().run()
    return at


def _link_to_swpw(at, frm="session:0", market="SWPW"):
    at = _emit(at, {"type": "link_request", "from": frm, "market": market})
    return _click(at, "Create link")


def _fill_line(at, market, side, pse, code, price, index=0):
    at.text_input(key=f"mv_bf_code_{market}_{side}_{pse}_{index}").set_value(code).run()
    at.number_input(key=f"mv_bf_price_{market}_{side}_{pse}_{index}").set_value(price).run()
    return at


class TestOpeningThePopup:
    def test_clicking_the_swpw_chip_opens_it(self, no_bilateral_db):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["mv_bidfile_market"] == "SWPW"
        assert [t.key for t in at.text_input] == ["mv_bf_code_SWPW_SHORT_AZPS_0"]

    def test_clicking_an_unsupported_market_says_so_instead_of_nothing(self, no_bilateral_db):
        at = _emit(
            _run([BUY_LEG]),
            {"type": "link_request", "from": "session:0", "market": "AESO"},
        )
        _click(at, "Create link")
        at = _emit(at, {"type": "chip_click", "market": "AESO"})
        assert not at.exception, [e.value for e in at.exception]
        assert any("AESO" in i.value and "yet" in i.value.lower() for i in at.info)

    def test_a_market_with_no_open_position_says_so(self, no_bilateral_db):
        at = _emit(_run([BUY_LEG]), {"type": "chip_click", "market": "SWPW"})
        assert not at.exception, [e.value for e in at.exception]
        assert any("No open position" in i.value for i in at.info)

    def test_a_click_never_starts_a_link(self, no_bilateral_db):
        # The click/drag threshold in the frontend is what makes this safe;
        # this just confirms the Python side agrees a bare click never
        # creates a link on its own.
        at = _emit(_run([BUY_LEG]), {"type": "chip_click", "market": "SWPW"})
        assert at.session_state["mv_links"] == []
        assert at.session_state["mv_pending"] is None

    def test_opening_the_bidfile_closes_any_link_popup(self, no_bilateral_db):
        second = hourly_trade("Sell", "BPAT", "MIDC", range(7, 23), 60)
        at = _emit(
            _run([BUY_LEG, second]),
            {"type": "link_request", "from": "session:0", "to": "session:1"},
        )
        assert at.session_state["mv_pending"] is not None
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert at.session_state["mv_pending"] is None


class TestGenerating:
    def test_filling_in_the_one_line_and_generating_writes_a_real_file(
        self, no_bilateral_db, redirect_swpw
    ):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        at = _fill_line(at, "SWPW", "SHORT", "AZPS", "AZPS", -1.0)
        _click(at, "Generate Bid File")

        assert not at.exception, [e.value for e in at.exception]
        assert any("Bid file written" in s.value for s in at.success)
        path = redirect_swpw / "test-output.xlsm"
        assert path.is_file()
        # The regression this guards against: a plain (non-macro-enabled)
        # workbook saved to a .xlsm name opens in Excel as "the file format
        # or file extension is not valid" — not just a warning, a hard
        # refusal. Confirmed by a real user hitting it.
        with zipfile.ZipFile(path) as z:
            assert "macroEnabled.main" in z.read("[Content_Types].xml").decode("utf-8")
        ws = openpyxl.load_workbook(path)["BIDS"]
        assert ws["D5"].value == "SPP-SHORT(AZPS)"
        # BUY_LEG flows PPT HE7-22; the sheet is EPT, so HE7 PPT sits on
        # EPT HE10 and HE22 PPT on the first starred row.
        assert ws.cell(row=swpw.ppt_hour_row(7), column=4).value == 100.0
        assert ws.cell(row=swpw.ppt_hour_row(7), column=5).value == -1.0
        assert ws.cell(row=swpw.ppt_hour_row(22), column=4).value == 100.0
        assert swpw.ppt_hour_row(22) == swpw.STAR_ROWS[0]
        # openpyxl always reads a date cell back as datetime.
        assert ws["C5"].value == datetime(FLOW.year, FLOW.month, FLOW.day)

    def test_generating_without_a_code_blocks_and_writes_nothing(
        self, no_bilateral_db, redirect_swpw
    ):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        _click(at, "Generate Bid File")
        assert any("code" in e.value for e in at.error)
        assert not (redirect_swpw / "test-output.xlsm").exists()

    def test_a_second_generate_asks_before_overwriting(self, no_bilateral_db, redirect_swpw):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        at = _fill_line(at, "SWPW", "SHORT", "AZPS", "AZPS", -1.0)
        _click(at, "Generate Bid File")
        _click(at, "Generate Bid File")

        assert any("already exists" in w.value for w in at.warning)
        assert not [b for b in at.button if b.label == "Overwrite and Generate"]

        at.checkbox(key="mv_bidfile_overwrite_confirm").set_value(True).run()
        assert [b for b in at.button if b.label == "Overwrite and Generate"]
        _click(at, "Overwrite and Generate")
        assert not at.exception, [e.value for e in at.exception]
        assert any("Bid file written" in s.value for s in at.success)


class TestSplitting:
    def test_the_split_button_adds_a_second_editable_line(self, no_bilateral_db):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        _click(at, "+ Split")
        assert [t.key for t in at.text_input] == [
            "mv_bf_code_SWPW_SHORT_AZPS_0",
            "mv_bf_code_SWPW_SHORT_AZPS_1",
        ]

    def test_a_reconciled_split_generates_two_columns(self, no_bilateral_db, redirect_swpw):
        at = _link_to_swpw(_run([BUY_LEG]))
        # Seeded directly rather than driven through the hour grid widget —
        # domain/bidfiles.py's own tests cover the reconciliation math
        # itself; this only checks the page wires that state through to a
        # real file correctly.
        at.session_state["mv_bidfile_splits"] = {
            ("SWPW", "SHORT", "AZPS"): [
                {"code": "AZPS", "price": -1.0, "mw_by_hour": {h: 60.0 for h in range(7, 23)}},
                {"code": "TEPC", "price": 0.0, "mw_by_hour": {h: 40.0 for h in range(7, 23)}},
            ]
        }
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert not at.error  # the split already reconciles — no complaint
        _click(at, "Generate Bid File")

        assert not at.exception, [e.value for e in at.exception]
        ws = openpyxl.load_workbook(redirect_swpw / "test-output.xlsm")["BIDS"]
        assert ws["D5"].value == "SPP-SHORT(AZPS)"
        assert ws["F5"].value == "SPP-SHORT(TEPC)"

    def test_an_unreconciled_split_is_reported_and_blocks_generation(
        self, no_bilateral_db, redirect_swpw
    ):
        at = _link_to_swpw(_run([BUY_LEG]))
        at.session_state["mv_bidfile_splits"] = {
            ("SWPW", "SHORT", "AZPS"): [
                {"code": "AZPS", "price": -1.0, "mw_by_hour": {7: 60.0}},
                {"code": "TEPC", "price": 0.0, "mw_by_hour": {7: 10.0}},  # 100 expected, only 70
            ]
        }
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert any("HE7" in e.value for e in at.error)
        _click(at, "Generate Bid File")
        assert not (redirect_swpw / "test-output.xlsm").exists()


class TestScopeAcrossFiltersAndHiding:
    def test_the_bidfile_sees_a_position_even_when_its_square_is_filtered_out(
        self, no_bilateral_db, redirect_swpw
    ):
        at = _link_to_swpw(_run([BUY_LEG]))
        at.multiselect(key="mv_pse").set_value(["NOBODY"]).run()
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert not at.exception, [e.value for e in at.exception]
        assert [t.key for t in at.text_input] == ["mv_bf_code_SWPW_SHORT_AZPS_0"]

    def test_the_bidfile_sees_a_position_even_when_its_square_is_hidden(
        self, no_bilateral_db, redirect_swpw
    ):
        at = _link_to_swpw(_run([BUY_LEG]))
        at = _emit(at, {"type": "dismiss", "key": "session:0"})
        at = _emit(at, {"type": "chip_click", "market": "SWPW"})
        assert not at.exception, [e.value for e in at.exception]
        assert [t.key for t in at.text_input] == ["mv_bf_code_SWPW_SHORT_AZPS_0"]
