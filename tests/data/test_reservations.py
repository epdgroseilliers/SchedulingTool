"""data/reservations.py — one OASIS reservation, by its assignment
reference.

Read-only, like everything touching MAG.dbo.OATI_*. The fake engine follows
tests/data/test_matching_reads.py.
"""

import re
from datetime import date

import pytest

import data.reservations as reservations

WRITE_VERBS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|EXEC|INTO)\b",
    re.IGNORECASE,
)

ROW = {
    "AssignmentRef": "109267045",
    "PathName": "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/",
    "Provider": "NWMT",
    "POR": "MATL.NWMT",
    "POD": "CROSSOVER",
    "Status": "CONFIRMED",
    "TsClass": "FIRM",
    "MaxGranted": 40,
    "FirstDate": date(2026, 9, 1),
    "LastDate": date(2026, 9, 30),
}


class FakeConn:
    def __init__(self, row, error=None):
        self._row, self._error = row, error
        self.params = None

    def execute(self, sql, params=None):
        if self._error:
            raise self._error
        self.params = params
        return self

    def mappings(self):
        return self

    def first(self):
        return self._row


class FakeEngine:
    def __init__(self, row=ROW, error=None, connect_error=None):
        self.conn = FakeConn(row, error)
        self._connect_error = connect_error

    def connect(self):
        if self._connect_error:
            raise self._connect_error
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def wire(monkeypatch, **kwargs):
    engine = FakeEngine(**kwargs)
    monkeypatch.setattr(reservations, "get_bilateral_engine", lambda: engine)
    return engine


@pytest.fixture(autouse=True)
def clear_cache():
    reservations.clear_cache()
    yield
    reservations.clear_cache()


class TestLookingOneUp:
    def test_it_answers_with_the_path_the_sheet_wants(self, monkeypatch):
        wire(monkeypatch)
        found = reservations.reservation("109267045")
        assert found["path"] == "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/"

    def test_and_with_what_the_reservation_actually_grants(self, monkeypatch):
        wire(monkeypatch)
        found = reservations.reservation("109267045")
        assert found["provider"] == "NWMT"
        assert found["granted_mw"] == 40.0
        assert found["status"] == "CONFIRMED"

    def test_the_reference_is_passed_as_given(self, monkeypatch):
        engine = wire(monkeypatch)
        reservations.reservation("  109267045 ")
        assert engine.conn.params == {"aref": "109267045"}

    def test_nothing_asked_for_nothing(self, monkeypatch):
        engine = wire(monkeypatch)
        assert reservations.reservation("") is None
        assert reservations.reservation(None) is None
        assert engine.conn.params is None  # no query at all


class TestWhenThereIsNoAnswer:
    def test_a_reservation_that_is_not_ours_is_not_an_error(self, monkeypatch):
        # The summary holds MAG's own reservations. A segment wheeled on a
        # counterparty's is genuinely absent — about three in ten of the
        # references on the desk's tags — and the scheduler types the path.
        wire(monkeypatch, row=None)
        assert reservations.reservation("110752124") is None

    def test_an_unreachable_database_is_not_one_either(self, monkeypatch):
        wire(monkeypatch, connect_error=OSError("no route to host"))
        assert reservations.reservation("109267045") is None

    def test_nor_is_a_failing_query(self, monkeypatch):
        wire(monkeypatch, error=OSError("timeout"))
        assert reservations.reservation("109267045") is None


class TestNothingCanWriteToOati:
    def test_the_sql_file_is_a_select(self):
        sql = "\n".join(
            line for line in reservations.SQL_PATH.read_text().splitlines()
            if not line.strip().startswith("--")
        )
        assert not WRITE_VERBS.search(sql), WRITE_VERBS.search(sql)

    def test_the_module_builds_no_sql_and_opens_no_transaction(self):
        import inspect

        source = inspect.getsource(reservations)
        assert source.count("text(") - source.count("read_text(") == 1
        assert ".begin()" not in source


@pytest.mark.db
class TestAgainstTheLiveSummary:
    def test_the_reservation_on_the_desks_own_abex_swpw_tag(self):
        # 109267045 is the "# trans" written in
        # Tag_dynamique_bilateral - September 1 2026 - ABEX-SWPW GWA.xlsx,
        # and the path below is that file's Path cell, character for
        # character. That correspondence is the whole feature.
        reservations.clear_cache()
        found = reservations.reservation("109267045")
        assert found is not None
        assert found["path"] == "WS/NWMT/NWMT-WAUW/MATL.NWMT-CROSSOVER/"
        assert found["provider"] == "NWMT"
        reservations.clear_cache()

    def test_a_reference_that_is_not_ours_answers_nothing(self):
        # 110752124 is ABEX's own reservation on that same tag.
        reservations.clear_cache()
        assert reservations.reservation("110752124") is None
        reservations.clear_cache()

    def test_an_invented_reference_answers_nothing(self):
        reservations.clear_cache()
        assert reservations.reservation("999999999") is None
        reservations.clear_cache()
