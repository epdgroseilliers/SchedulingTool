"""data/tags_history.py — assembling the desk's tag history out of OATI.

**These tables are read-only.** TestNothingCanWriteToOati below is the part
of that guarantee that runs without a database, so it holds whether or not
anyone remembers `--run-db`.

The fake engine follows tests/data/test_matching_reads.py: its own context
manager and cursor, primed with one result set per query in call order.
"""

import re
from datetime import date, datetime, timezone

import pytest

import data.tags_history as history

WRITE_VERBS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|EXEC|INTO)\b",
    re.IGNORECASE,
)

TAG_ROWS = [{
    "TagIndex": 1, "TagID": "GWA_MAG0011_SWPW", "TagCode": "1", "GCA": "GWA",
    "LCA": "SWPW", "CPSE": "MAG001",
    "StartTime": datetime(2026, 9, 29, 7, 0), "StopTime": datetime(2026, 9, 30, 7, 0),
    "CreationTime": datetime(2026, 9, 28, 16, 0), "PseComment": "",
    "LastAction": "IMPLEMENTED", "TagCompositeState": 7,
}]
MS_ROWS = [
    {"TagIndex": 1, "TagMSIndex": 3, "PSEcode": "MAG001", "EnergyProduct": "L",
     "ContractNumberList": ""},
    {"TagIndex": 1, "TagMSIndex": 1, "PSEcode": "RRWE01", "EnergyProduct": "G-F",
     "ContractNumberList": ""},
    {"TagIndex": 1, "TagMSIndex": 2, "PSEcode": "ABEX", "EnergyProduct": "",
     "ContractNumberList": ""},
]
PS_ROWS = [
    {"TagIndex": 1, "TagPSIndex": 2, "MSIndex": 2, "MSPSEcode": "ABEX", "Type": "T",
     "TPcode": "MATL", "CAcode": "", "POR_Name": "WWA", "POR_CAcode": "MATL",
     "POD_Name": "MATL.NWMT", "POD_CAcode": "MATL", "MOcode": ""},
    {"TagIndex": 1, "TagPSIndex": 1, "MSIndex": 1, "MSPSEcode": "RRWE01", "Type": "G",
     "TPcode": "", "CAcode": "", "POR_Name": "RIMROCK", "POR_CAcode": "GWA",
     "POD_Name": "", "POD_CAcode": "", "MOcode": ""},
]
TA_ROWS = [
    {"TagIndex": 1, "TagTAIndex": 1, "ParentSegmentIndex": 99, "ParentSegmentRef": 2,
     "TP": "MATL", "TransProduct": "1-NS", "ContractNumber": "110752124",
     "TransCustCode": "ABEX", "NITSResourceName": ""},
]


class FakeConn:
    def __init__(self, results, error=None):
        self._results = list(results)
        self._error = error
        self.statements = []
        self.params = []

    def execute(self, sql, params=None):
        if self._error:
            raise self._error
        self.statements.append(str(sql))
        self.params.append(params)
        self._rows = self._results.pop(0) if self._results else []
        return self

    def mappings(self):
        return self._rows


class FakeEngine:
    def __init__(self, results=(), error=None, connect_error=None):
        self.conn = FakeConn(results, error)
        self._connect_error = connect_error

    def connect(self):
        if self._connect_error:
            raise self._connect_error
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def wire(monkeypatch, results=(TAG_ROWS, MS_ROWS, PS_ROWS, TA_ROWS), **kwargs):
    engine = FakeEngine(results, **kwargs)
    monkeypatch.setattr(history, "get_bilateral_engine", lambda: engine)
    return engine


@pytest.fixture(autouse=True)
def clear_cache():
    history.clear_cache()
    yield
    history.clear_cache()


def assembled():
    return history.assemble(TAG_ROWS, MS_ROWS, PS_ROWS, TA_ROWS)[0]


class TestAssembly:
    def test_the_header_comes_across(self):
        record = assembled()
        assert record["tag_index"] == 1
        assert (record["gca"], record["lca"]) == ("GWA", "SWPW")
        assert record["last_action"] == "IMPLEMENTED"

    def test_the_market_path_is_in_chain_order_however_the_rows_arrive(self):
        # The fake rows are deliberately shuffled.
        assert [s["pse"] for s in assembled()["market_path"]] == [
            "RRWE01", "ABEX", "MAG001"
        ]

    def test_the_physical_path_is_ordered_by_its_market_step(self):
        assert [s["index"] for s in assembled()["physical_path"]] == [1, 2]

    def test_a_reservation_hangs_off_the_segment_that_bought_it(self):
        segments = {s["index"]: s for s in assembled()["physical_path"]}
        assert segments[2]["reservations"][0]["contract"] == "110752124"
        assert segments[1]["reservations"] == []

    def test_the_join_is_parent_segment_ref_not_parent_segment_index(self):
        # Both are ints on the same row; using the wrong one gives a result
        # set rather than an error, which is what makes it dangerous.
        ta = [dict(TA_ROWS[0], ParentSegmentIndex=1, ParentSegmentRef=2)]
        record = history.assemble(TAG_ROWS, MS_ROWS, PS_ROWS, ta)[0]
        by_index = {s["index"]: s for s in record["physical_path"]}
        assert by_index[2]["reservations"] and not by_index[1]["reservations"]

    def test_one_tags_reservation_never_reaches_another(self):
        # TagPSIndex is unique only *within* a tag, so a join that forgot
        # the tag index would cross-contaminate two routes silently.
        tags = TAG_ROWS + [dict(TAG_ROWS[0], TagIndex=2, TagID="X")]
        ps = PS_ROWS + [dict(PS_ROWS[0], TagIndex=2)]
        records = {r["tag_index"]: r for r in history.assemble(tags, MS_ROWS, ps, TA_ROWS)}
        second = [s for s in records[2]["physical_path"] if s["index"] == 2][0]
        assert second["reservations"] == []

    def test_a_tag_with_no_children_is_still_a_record(self):
        record = history.assemble(TAG_ROWS, [], [], [])[0]
        assert record["market_path"] == [] and record["physical_path"] == []

    def test_padding_typed_into_a_row_by_hand_is_stripped(self):
        ms = [dict(MS_ROWS[1], PSEcode="  RRWE01  ")]
        assert history.assemble(TAG_ROWS, ms, [], [])[0]["market_path"][0]["pse"] == (
            "RRWE01"
        )


class TestTheFlowDateIsPacific:
    """A tag's times are UTC and the desk works in PPT, so the day a tag
    covers is a conversion, not a subtraction — the offset is seven hours
    for half the year and eight for the other half."""

    def test_a_summer_day_starts_at_seven_utc(self):
        assert history.flow_date_of(datetime(2026, 9, 29, 7, 0)) == date(2026, 9, 29)

    def test_the_hour_before_it_belongs_to_the_day_before(self):
        assert history.flow_date_of(datetime(2026, 9, 29, 6, 0)) == date(2026, 9, 28)

    def test_a_winter_day_starts_an_hour_later(self):
        # Pacific is UTC-8 in January, so 07:00 UTC is still the 14th.
        assert history.flow_date_of(datetime(2026, 1, 15, 7, 0)) == date(2026, 1, 14)
        assert history.flow_date_of(datetime(2026, 1, 15, 8, 0)) == date(2026, 1, 15)

    def test_an_aware_datetime_is_honoured_rather_than_assumed_naive(self):
        aware = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)
        assert history.flow_date_of(aware) == date(2026, 9, 29)

    def test_a_missing_start_time_has_no_flow_date(self):
        assert history.flow_date_of(None) is None


class TestTheQueries:
    def test_all_four_run_on_one_connection_with_the_same_window(self, monkeypatch):
        engine = wire(monkeypatch)
        history.fetch_frames(date(2026, 9, 1), date(2026, 9, 30))
        assert len(engine.conn.statements) == 4
        assert {tuple(sorted(p)) for p in engine.conn.params} == {
            ("all_actions", "start_time", "stop_time")
        }

    def test_the_window_runs_to_the_end_of_the_last_day(self, monkeypatch):
        engine = wire(monkeypatch)
        history.fetch_frames(date(2026, 9, 1), date(2026, 9, 30))
        params = engine.conn.params[0]
        assert params["start_time"] == datetime(2026, 9, 1)
        assert params["stop_time"] == datetime(2026, 10, 1)

    def test_confirmed_only_by_default(self, monkeypatch):
        engine = wire(monkeypatch)
        history.fetch_frames(date(2026, 9, 1), date(2026, 9, 2))
        assert engine.conn.params[0]["all_actions"] == 0
        history.fetch_frames(date(2026, 9, 1), date(2026, 9, 2), all_actions=True)

    def test_each_file_queries_its_own_table(self):
        # oati_tagPS.sql arrived selecting FROM OATI_TagMS; this is that
        # regression.
        for path, table in (
            (history.TAG_SQL, "MAG.dbo.OATI_Tag AS t"),
            (history.MS_SQL, "MAG.dbo.OATI_TagMS AS ms"),
            (history.PS_SQL, "MAG.dbo.OATI_TagPS AS ps"),
            (history.TA_SQL, "MAG.dbo.OATI_TagTA AS ta"),
        ):
            assert f"FROM {table}" in path.read_text(), path.name

    def test_every_file_is_windowed(self, monkeypatch):
        # No child query may ever scan its millions of rows unrestricted.
        for path in (history.TAG_SQL, history.MS_SQL, history.PS_SQL, history.TA_SQL):
            sql = path.read_text()
            assert ":start_time" in sql and ":stop_time" in sql, path.name

    def test_the_tag_filter_is_identical_in_all_four(self):
        # It is written out four times; this is what keeps the copies honest.
        blocks = set()
        for path in (history.TAG_SQL, history.MS_SQL, history.PS_SQL, history.TA_SQL):
            sql = path.read_text()
            start = sql.index("-- >>> west tag filter")
            stop = sql.index("-- <<< west tag filter")
            blocks.add(sql[start:stop])
        assert len(blocks) == 1


class TestNothingCanWriteToOati:
    """The read-only guarantee, checked without a database so it holds on
    every `pytest` run rather than only under --run-db."""

    def test_the_sql_files_contain_no_write_verb(self):
        # INTO is in the list on purpose: SELECT ... INTO #tmp is the one
        # write that would otherwise look innocent.
        for path in (history.TAG_SQL, history.MS_SQL, history.PS_SQL, history.TA_SQL):
            sql = "\n".join(
                line for line in path.read_text().splitlines()
                if not line.strip().startswith("--")
            )
            assert not WRITE_VERBS.search(sql), f"{path.name}: {WRITE_VERBS.search(sql)}"

    def test_the_module_builds_no_sql_of_its_own(self):
        import inspect

        source = inspect.getsource(history)
        # Every statement comes from a file, so the audit is four short
        # files rather than a call graph. (read_text() is the file being
        # read, not SQL being built.)
        assert source.count("text(") - source.count("read_text(") == 1
        assert "text(_read(path)" in source

    def test_the_module_never_opens_a_transaction(self):
        import inspect

        source = inspect.getsource(history)
        assert ".begin()" not in source
        assert ".connect()" in source

    def test_it_exposes_no_write_function(self):
        assert not [
            name for name in dir(history)
            if any(verb in name.lower() for verb in ("insert", "update", "delete", "write"))
        ]


class TestWhenOatiIsUnreachable:
    def test_it_reports_rather_than_raises(self, monkeypatch):
        wire(monkeypatch, connect_error=OSError("no route to host"))
        records, error = history.load_west_tags(date(2026, 9, 1), date(2026, 9, 2))
        assert records == []
        assert "no route to host" in error

    def test_a_failing_query_is_reported_too(self, monkeypatch):
        wire(monkeypatch, error=OSError("query timeout"))
        records, error = history.load_west_tags(date(2026, 9, 1), date(2026, 9, 2))
        assert records == [] and "query timeout" in error

    def test_a_good_read_reports_no_error(self, monkeypatch):
        wire(monkeypatch)
        records, error = history.load_west_tags(date(2026, 9, 1), date(2026, 9, 2))
        assert error is None and len(records) == 1


@pytest.mark.db
class TestAgainstTheLiveHistory:
    """Read-only queries against MAG.dbo.OATI_*."""

    WINDOW = (date(2026, 9, 22), date(2026, 9, 28))

    def test_a_week_of_west_tags_loads(self):
        history.clear_cache()
        records, error = history.load_west_tags(*self.WINDOW)
        assert error is None
        assert records
        history.clear_cache()

    def test_every_tag_has_a_route(self):
        history.clear_cache()
        records, _ = history.load_west_tags(*self.WINDOW)
        for record in records:
            assert record["gca"] and record["lca"]
            assert len(record["market_path"]) >= 2
            assert record["physical_path"]
            # Generation and load segments have nothing to reserve.
            for segment in record["physical_path"]:
                if segment["type"] in ("G", "L"):
                    assert segment["reservations"] == []
        history.clear_cache()

    def test_no_reservation_number_leaks_into_a_signature(self):
        # The live check that the grouping key really is date- and
        # number-free: a contract number in it would make every tag its own
        # recipe.
        from domain.tag_recipes import recipe_signature

        history.clear_cache()
        records, _ = history.load_west_tags(*self.WINDOW)
        for record in records[:200]:
            signature = recipe_signature(record)
            for segment in record["physical_path"]:
                for reservation in segment["reservations"]:
                    if reservation["contract"]:
                        assert reservation["contract"] not in signature
        history.clear_cache()

    def test_the_east_is_filtered_out(self):
        history.clear_cache()
        records, _ = history.load_west_tags(*self.WINDOW)
        east = {"PJM", "CPLE", "ISNE", "ERCO", "NYIS", "ONT"}
        assert not [r for r in records if r["gca"] in east or r["lca"] in east]
        history.clear_cache()
