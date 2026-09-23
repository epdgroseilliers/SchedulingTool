"""data/bidfiles/swpw.py — the SWPW bid file writer.

Never touches Z:\\ — every test saves to tmp_path and points target_path()
and vba_shell_path() there via monkeypatch, so this suite runs the same
with the network drive unmounted as with it mounted, and never embeds any
of the desk's own files.
"""

import zipfile
from datetime import date

import openpyxl
import pytest

import data.bidfiles.swpw as swpw

FLOW = date(2026, 9, 23)

SHORT_LINE = {"pse": "AZPS", "code": "AZPS", "price": -1.0, "mw_by_hour": {h: 100.0 for h in range(11, 19)}}
LONG_LINE = {"pse": "CISO", "code": "CISO", "price": 50.0, "mw_by_hour": {h: 25.0 for h in range(4, 10)}}

FAKE_VBA_BYTES = b"NOT-A-REAL-VBA-PROJECT-JUST-TEST-BYTES"


def _write_fake_shell(path):
    """A synthetic stand-in for the real macro-enabled TEST.xlsm — a plain
    workbook (which already has valid _rels/.rels etc., since openpyxl
    wrote it) with an xl/vbaProject.bin entry appended after the fact.

    It doesn't matter that this isn't a *real* VBA project, or that this
    file wouldn't itself look macro-enabled if opened directly: the writer
    only ever copies xl/vbaProject.bin's raw bytes across, never validates
    them, and never reads the shell's own content-types — see
    data/bidfiles/swpw.py's _save_as_macro_enabled().
    """
    openpyxl.Workbook().save(path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("xl/vbaProject.bin", FAKE_VBA_BYTES)
    return path


class TestOfficialFilename:
    def test_matches_the_real_naming_exactly(self):
        # Real examples on disk: "...- 23 September 2026.xlsm",
        # "...- 1 September 2026.xlsm" — day has no leading zero.
        assert swpw.official_filename(date(2026, 9, 23)) == (
            "BID officiel Bilateral SPP - 23 September 2026.xlsm"
        )
        assert swpw.official_filename(date(2026, 9, 1)) == (
            "BID officiel Bilateral SPP - 1 September 2026.xlsm"
        )


class TestBuildWorkbook:
    def _ws(self, short_lines, long_lines):
        wb = swpw.build_workbook(FLOW, short_lines, long_lines)
        return wb["BIDS"]

    def test_the_date_is_written_as_a_literal_not_a_formula(self):
        # The real files use =TODAY()+1; this always generates for a
        # specific chosen flow date, so it's written as a plain value —
        # see data/bidfiles/swpw.py's module docstring.
        ws = self._ws([SHORT_LINE], [])
        assert ws["C5"].value == FLOW
        assert ws["C5"].number_format == "mm-dd-yy"

    def test_header_naming_matches_the_real_convention(self):
        ws = self._ws([SHORT_LINE], [LONG_LINE])
        assert ws["D5"].value == "SPP-SHORT(AZPS)"
        assert ws["F5"].value == "SPP-LONG(CISO)"  # long starts right after short

    def test_mw_and_price_only_appear_on_hours_that_actually_flow(self):
        ws = self._ws([SHORT_LINE], [])
        # SHORT_LINE flows PPT HE11-18. Rows are addressed in PPT here and
        # converted on the way in — see TestPptHourRows for the EPT shift.
        assert ws.cell(row=swpw.ppt_hour_row(10), column=4).value is None
        assert ws.cell(row=swpw.ppt_hour_row(11), column=4).value == 100.0
        assert ws.cell(row=swpw.ppt_hour_row(11), column=5).value == -1.0
        assert ws.cell(row=swpw.ppt_hour_row(18), column=4).value == 100.0
        assert ws.cell(row=swpw.ppt_hour_row(19), column=4).value is None

    def test_two_short_lines_get_two_separate_column_pairs(self):
        second = {"pse": "TEPC", "code": "TEPC", "price": 0.0, "mw_by_hour": {h: 40.0 for h in range(1, 25)}}
        ws = self._ws([SHORT_LINE, second], [])
        assert ws["D5"].value == "SPP-SHORT(AZPS)"
        assert ws["F5"].value == "SPP-SHORT(TEPC)"

    def test_the_same_code_can_appear_twice_unmerged(self):
        # Confirmed from real history: two separate bid lines can share a
        # GCA/LCA code and are never folded into one column.
        a = {"pse": "AZPS", "code": "GWA", "price": -1.0, "mw_by_hour": {7: 50.0}}
        b = {"pse": "BPAT", "code": "GWA", "price": 0.0, "mw_by_hour": {7: 30.0}}
        ws = self._ws([a, b], [])
        assert ws["D5"].value == ws["F5"].value == "SPP-SHORT(GWA)"
        row = swpw.ppt_hour_row(7)
        assert ws.cell(row=row, column=4).value == 50.0  # D
        assert ws.cell(row=row, column=6).value == 30.0  # F

    def test_missing_sides_produce_no_columns_or_banner_for_that_side(self):
        ws = self._ws([SHORT_LINE], [])
        assert ws["T4"].value is None  # no LONG banner at all

    def test_the_four_flag_rows_always_get_the_defaults_seen_in_every_real_file(self):
        ws = self._ws([SHORT_LINE], [])
        assert ws["D37"].value is False and ws["E37"].value is False
        assert ws["D38"].value is None and ws["E38"].value is None
        assert ws["D39"].value == 2 and ws["E39"].value == 2
        assert ws["D40"].value is False and ws["E40"].value is False

    def test_hours_outside_the_position_stay_blank(self):
        # SHORT_LINE is PPT HE11-18, so nothing reaches the starred rows
        # (PPT HE22-24) or the first EPT hours (the previous PPT day).
        ws = self._ws([SHORT_LINE], [])
        for row in list(swpw.STAR_ROWS) + [10, 11, 12]:
            assert ws.cell(row=row, column=4).value is None

    def test_the_static_column_c_furniture_is_present(self):
        ws = self._ws([SHORT_LINE], [])
        assert ws["C1"].value == swpw.SHEET_TITLE
        assert ws["C8"].value == "HE EPT"
        assert ws["C10"].value == 1
        assert ws["C33"].value == 24
        assert ws["C34"].value == "1*"
        assert ws["C42"].value == "IsPassif"

    def test_it_opens_back_up_clean(self, tmp_path):
        # Round-trips through a real save/load, not just in-memory checks.
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [LONG_LINE])
        path = tmp_path / "roundtrip.xlsm"
        wb.save(path)
        reopened = openpyxl.load_workbook(path)["BIDS"]
        assert reopened["D5"].value == "SPP-SHORT(AZPS)"
        assert reopened["F5"].value == "SPP-LONG(CISO)"  # long starts right after the one short pair


class TestPptHourRows:
    """The sheet's hour column is EPT, the app is PPT. These pin the
    placement against the desk's own real files, which is where the
    mapping was worked out from in the first place."""

    def _filled_slots(self, ws, col):
        """Which hour labels (EPT HE numbers, or '1*'/'2*'/'3*') a column
        actually has values on — the same view used to read the real
        files."""
        return [
            ws.cell(row=r, column=3).value
            for r in range(swpw.HOUR_FIRST_ROW, swpw.STAR_ROWS[-1] + 1)
            if ws.cell(row=r, column=col).value is not None
        ]

    def test_ppt_he1_lands_on_ept_he4(self):
        assert swpw.ppt_hour_row(1) == swpw.HOUR_FIRST_ROW + 3

    def test_the_last_three_ppt_hours_land_on_the_starred_rows(self):
        assert swpw.ppt_hour_row(22) == swpw.STAR_ROWS[0]
        assert swpw.ppt_hour_row(23) == swpw.STAR_ROWS[1]
        assert swpw.ppt_hour_row(24) == swpw.STAR_ROWS[2]

    def test_a_full_ppt_day_matches_the_real_gwa_column(self):
        # The real SPP-SHORT(GWA) column on 23 September 2026 fills exactly
        # EPT HE4-24 plus 1*/2*/3* — 24 slots for a 24-hour PPT day.
        full_day = {"pse": "GWA", "code": "GWA", "price": 0.0,
                    "mw_by_hour": {h: 40.0 for h in range(1, 25)}}
        ws = swpw.build_workbook(FLOW, [full_day], [])["BIDS"]
        assert self._filled_slots(ws, 4) == list(range(4, 25)) + ["1*", "2*", "3*"]

    def test_an_ll_position_matches_the_real_ciso_column(self):
        # The real SPP-LONG(CISO) column fills EPT HE4-9 plus 2*/3*, which
        # is PPT HE1-6 + HE23-24 — the off-peak shape from domain.shapes,
        # and a nonsense shape if the column were read as PPT.
        off_peak = {"pse": "CISO", "code": "CISO", "price": 50.0,
                    "mw_by_hour": {**{h: 25.0 for h in range(1, 7)}, 23: 25.0, 24: 25.0}}
        ws = swpw.build_workbook(FLOW, [], [off_peak])["BIDS"]
        assert self._filled_slots(ws, 4) == [4, 5, 6, 7, 8, 9, "2*", "3*"]

    def test_no_ppt_hour_is_lost_or_doubled_up(self):
        rows = [swpw.ppt_hour_row(h) for h in range(1, 25)]
        assert len(set(rows)) == 24


class TestWriteBidFile:
    @pytest.fixture(autouse=True)
    def _redirect_to_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(swpw, "BID_FOLDER", tmp_path)
        monkeypatch.setattr(swpw, "TEST_MODE", True)
        monkeypatch.setattr(swpw, "TEST_FILENAME", "test-output.xlsm")
        # write_bid_file's default vba_shell_path() is BID_FOLDER /
        # VBA_SHELL_NAME, which now points here — give it something to find.
        _write_fake_shell(tmp_path / swpw.VBA_SHELL_NAME)
        self.tmp_path = tmp_path

    def test_it_writes_to_target_path(self):
        path = swpw.write_bid_file(FLOW, [SHORT_LINE], [], overwrite=False)
        assert path == self.tmp_path / "test-output.xlsm"
        assert path.is_file()

    def test_it_refuses_to_silently_overwrite(self):
        swpw.write_bid_file(FLOW, [SHORT_LINE], [], overwrite=False)
        with pytest.raises(FileExistsError):
            swpw.write_bid_file(FLOW, [SHORT_LINE], [], overwrite=False)

    def test_overwrite_true_replaces_it(self):
        swpw.write_bid_file(FLOW, [SHORT_LINE], [], overwrite=False)
        swpw.write_bid_file(FLOW, [SHORT_LINE, SHORT_LINE], [], overwrite=True)
        ws = openpyxl.load_workbook(self.tmp_path / "test-output.xlsm")["BIDS"]
        assert ws["F5"].value is not None  # the second line's column exists now


class TestSaveAsMacroEnabled:
    """The fix for "cannot open file because the file format or file
    extension is not valid": a plain workbook saved to a .xlsm name has the
    right name but the wrong content, and Excel refuses it outright. This
    checks the actual mechanism that makes the output a genuine
    macro-enabled file, not just a renamed one.
    """

    def test_the_output_is_declared_macro_enabled(self, tmp_path):
        shell = _write_fake_shell(tmp_path / "shell.xlsm")
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [])
        out = tmp_path / "output.xlsm"
        swpw._save_as_macro_enabled(wb, out, shell_path=shell)

        with zipfile.ZipFile(out) as z:
            content_types = z.read("[Content_Types].xml").decode("utf-8")
            assert "macroEnabled.main" in content_types
            assert z.read("xl/vbaProject.bin") == FAKE_VBA_BYTES
            rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
            assert "vbaProject" in rels

    def test_it_still_reopens_with_the_data_intact(self, tmp_path):
        shell = _write_fake_shell(tmp_path / "shell.xlsm")
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [LONG_LINE])
        out = tmp_path / "output.xlsm"
        swpw._save_as_macro_enabled(wb, out, shell_path=shell)

        reopened = openpyxl.load_workbook(out, keep_vba=True)["BIDS"]
        assert reopened["D5"].value == "SPP-SHORT(AZPS)"
        assert reopened["F5"].value == "SPP-LONG(CISO)"

    def test_a_missing_shell_raises_instead_of_writing_a_broken_file(self, tmp_path):
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [])
        out = tmp_path / "output.xlsm"
        with pytest.raises(RuntimeError, match="VBA shell"):
            swpw._save_as_macro_enabled(wb, out, shell_path=tmp_path / "does-not-exist.xlsm")
        assert not out.exists()

    def test_a_locked_output_file_says_so_rather_than_blaming_the_shell(self, tmp_path):
        # Regression: the shell-open and the save used to share one
        # try/except, so a locked output file (the usual cause: it's open
        # in Excel) was reported as an unreadable VBA shell — pointing at
        # entirely the wrong thing.
        shell = _write_fake_shell(tmp_path / "shell.xlsm")
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [])
        locked = tmp_path / "locked-dir"  # a directory can't be overwritten as a file
        locked.mkdir()
        with pytest.raises(RuntimeError) as exc:
            swpw._save_as_macro_enabled(wb, locked, shell_path=shell)
        assert "VBA shell" not in str(exc.value)
        assert "open in Excel" in str(exc.value)

    def test_a_corrupt_shell_raises_the_same_way(self, tmp_path):
        bad = tmp_path / "not-a-zip.xlsm"
        bad.write_text("this is not a zip file")
        wb = swpw.build_workbook(FLOW, [SHORT_LINE], [])
        with pytest.raises(RuntimeError, match="VBA shell"):
            swpw._save_as_macro_enabled(wb, tmp_path / "output.xlsm", shell_path=bad)

    def test_vba_shell_path_follows_bid_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(swpw, "BID_FOLDER", tmp_path)
        assert swpw.vba_shell_path() == tmp_path / swpw.VBA_SHELL_NAME

    def test_write_bid_file_produces_a_genuinely_macro_enabled_file(self, tmp_path, monkeypatch):
        # End-to-end through the real entry point, not just the helper.
        monkeypatch.setattr(swpw, "BID_FOLDER", tmp_path)
        monkeypatch.setattr(swpw, "TEST_MODE", True)
        monkeypatch.setattr(swpw, "TEST_FILENAME", "e2e.xlsm")
        _write_fake_shell(tmp_path / swpw.VBA_SHELL_NAME)

        path = swpw.write_bid_file(FLOW, [SHORT_LINE], [], overwrite=False)
        with zipfile.ZipFile(path) as z:
            assert "macroEnabled.main" in z.read("[Content_Types].xml").decode("utf-8")
            assert z.read("xl/vbaProject.bin") == FAKE_VBA_BYTES
