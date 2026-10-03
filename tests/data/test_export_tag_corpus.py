"""scripts/export_tag_corpus.py — the corpus written to disk.

Driven with a monkeypatched fetch_frames, so no database is touched and the
same fake tags produce the same files every run. Everything is written to
tmp_path; nothing here may write inside the repo, the same discipline
test_swpw_bid.py and test_tagfile.py keep for Z:\\ and Y:\\West.
"""

import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import export_tag_corpus as exporter  # noqa: E402

TAG_ROWS = [
    {"TagIndex": 1, "TagID": "GWA_MAG0011_SWPW", "TagCode": "1", "GCA": "GWA",
     "LCA": "SWPW", "CPSE": "MAG001", "StartTime": datetime(2026, 9, 29, 8, 0),
     "StopTime": datetime(2026, 9, 30, 7, 0), "CreationTime": None,
     "PseComment": "", "LastAction": "IMPLEMENTED", "TagCompositeState": 7},
    {"TagIndex": 2, "TagID": "GWA_MAG0012_SWPW", "TagCode": "2", "GCA": "GWA",
     "LCA": "SWPW", "CPSE": "MAG001", "StartTime": datetime(2026, 9, 28, 8, 0),
     "StopTime": datetime(2026, 9, 29, 7, 0), "CreationTime": None,
     "PseComment": "", "LastAction": "IMPLEMENTED", "TagCompositeState": 7},
    {"TagIndex": 3, "TagID": "BPAT_MAG0013_CISO", "TagCode": "3", "GCA": "BPAT",
     "LCA": "CISO", "CPSE": "MAG001", "StartTime": datetime(2026, 9, 28, 8, 0),
     "StopTime": datetime(2026, 9, 29, 7, 0), "CreationTime": None,
     "PseComment": "", "LastAction": "IMPLEMENTED", "TagCompositeState": 7},
]


def ms(tag_index, *pairs):
    return [
        {"TagIndex": tag_index, "TagMSIndex": i, "PSEcode": pse,
         "EnergyProduct": product, "ContractNumberList": ""}
        for i, (pse, product) in enumerate(pairs, start=1)
    ]


MS_ROWS = (
    ms(1, ("RRWE01", "G-F"), ("ABEX", ""), ("MAG001", "L"))
    + ms(2, ("RRWE01", "G-F"), ("ABEX", ""), ("MAG001", "L"))
    + ms(3, ("BPAP01", "G-F"), ("MAG001", "L"))
)
PS_ROWS = [
    {"TagIndex": t, "TagPSIndex": 1, "MSIndex": 1, "MSPSEcode": "X", "Type": "T",
     "TPcode": "MATL", "CAcode": "", "POR_Name": "WWA", "POR_CAcode": "MATL",
     "POD_Name": "MATL.NWMT", "POD_CAcode": "MATL", "MOcode": ""}
    for t in (1, 2, 3)
]
TA_ROWS = [
    {"TagIndex": t, "TagTAIndex": 1, "ParentSegmentIndex": 9, "ParentSegmentRef": 1,
     "TP": "MATL", "TransProduct": "1-NS", "ContractNumber": f"1107521{t}",
     "TransCustCode": "ABEX", "NITSResourceName": ""}
    for t in (1, 2, 3)
]


@pytest.fixture(autouse=True)
def fake_history(monkeypatch):
    monkeypatch.setattr(
        exporter, "fetch_frames",
        lambda start, stop, all_actions=False: {
            "tag": TAG_ROWS, "ms": MS_ROWS, "ps": PS_ROWS, "ta": TA_ROWS
        },
    )


def run(tmp_path, *extra):
    argv = ["--start", "2026-09-01", "--out", str(tmp_path), "--quiet", *extra]
    assert exporter.main(argv) == 0
    return tmp_path


def recipes_of(out):
    with open(out / "recipes.csv", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class TestTheRecipeTable:
    def test_one_row_per_route_most_used_first(self, tmp_path):
        rows = recipes_of(run(tmp_path))
        assert [r["count"] for r in rows] == ["2", "1"]
        assert [r["rank"] for r in rows] == ["1", "2"]

    def test_it_names_the_route_and_who_it_was_run_with(self, tmp_path):
        first = recipes_of(run(tmp_path))[0]
        assert (first["gca"], first["lca"]) == ("GWA", "SWPW")
        assert first["counterparties"] == "ABEX"
        assert first["transmission_providers"] == "MATL"

    def test_the_dates_span_what_the_route_was_used_on(self, tmp_path):
        first = recipes_of(run(tmp_path))[0]
        assert first["first_flow_date"] == "2026-09-28"
        assert first["last_flow_date"] == "2026-09-29"

    def test_the_signature_carries_no_reservation_number(self, tmp_path):
        for row in recipes_of(run(tmp_path)):
            assert "1107521" not in row["signature"]

    def test_the_header_is_the_documented_one(self, tmp_path):
        with open(run(tmp_path) / "recipes.csv", encoding="utf-8") as handle:
            assert handle.readline().strip().split(",") == exporter.CSV_COLUMNS


class TestTheSampleFiles:
    def test_one_per_recipe_named_by_rank(self, tmp_path):
        names = sorted(p.name for p in (run(tmp_path) / "samples").glob("*.json"))
        assert len(names) == 2
        assert names[0].startswith("0001_")

    def test_each_one_stands_alone(self, tmp_path):
        # A reader should never need the CSV open beside it.
        path = next((run(tmp_path) / "samples").glob("0001_*.json"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["signature_version"] == exporter.SIGNATURE_VERSION
        assert payload["count"] == 2
        assert payload["counterparties"] == ["ABEX"]
        assert payload["samples"][0]["market_path"][0]["pse"] == "RRWE01"

    def test_samples_are_newest_first(self, tmp_path):
        path = next((run(tmp_path) / "samples").glob("0001_*.json"))
        dates = [s["flow_date"] for s in json.loads(path.read_text())["samples"]]
        assert dates == sorted(dates, reverse=True)

    def test_how_many_to_keep_is_a_choice(self, tmp_path):
        path = next((run(tmp_path, "--samples", "1") / "samples").glob("0001_*.json"))
        assert len(json.loads(path.read_text())["samples"]) == 1

    def test_dates_come_out_as_text_not_python_objects(self, tmp_path):
        path = next((run(tmp_path) / "samples").glob("0001_*.json"))
        payload = json.loads(path.read_text())
        assert payload["last_flow_date"] == "2026-09-29"
        assert isinstance(payload["samples"][0]["start_time"], str)


class TestNarrowingIt:
    def test_min_count_drops_the_one_offs(self, tmp_path):
        assert len(recipes_of(run(tmp_path, "--min-count", "2"))) == 1

    def test_top_keeps_the_most_used(self, tmp_path):
        rows = recipes_of(run(tmp_path, "--top", "1"))
        assert len(rows) == 1 and rows[0]["count"] == "2"


class TestTheFolderExplainsItself:
    def test_a_readme_is_written(self, tmp_path):
        readme = (run(tmp_path) / "README.md").read_text(encoding="utf-8")
        assert "3 tags collapse into 2 recipes" in readme.replace("\n", " ")

    def test_it_names_the_filters_that_were_applied(self, tmp_path):
        readme = (run(tmp_path) / "README.md").read_text(encoding="utf-8")
        # What is *not* in the corpus matters as much as what is.
        assert "IMPLEMENTED" in readme and "TestTag = 0" in readme
        assert "PJM" in readme

    def test_it_says_these_records_stay_here(self, tmp_path):
        readme = (run(tmp_path) / "README.md").read_text(encoding="utf-8")
        assert "do not upload" in readme.lower()

    def test_it_says_when_the_filters_were_relaxed(self, tmp_path):
        readme = (run(tmp_path, "--include-unconfirmed") / "README.md").read_text()
        assert "--include-unconfirmed" in readme

    def test_the_folder_ignores_itself(self, tmp_path):
        # Belt and braces with the repo's own .gitignore: this survives
        # someone moving the folder.
        assert (run(tmp_path) / ".gitignore").read_text() == "*\n"


class TestKeepingItLocal:
    def test_a_shared_path_outside_the_repo_is_called_out(self, capsys):
        exporter.check_destination(Path(r"\\fileserver\share\corpus"))
        assert "shared network path" in capsys.readouterr().err

    def test_the_repo_itself_is_not_flagged(self, capsys):
        # This repository lives on the user's own network home directory,
        # so "is a UNC path" alone would warn on every ordinary run.
        exporter.check_destination(exporter.DEFAULT_OUT)
        assert capsys.readouterr().err == ""

    def test_a_local_path_is_not_flagged(self, capsys, tmp_path):
        exporter.check_destination(tmp_path)
        assert capsys.readouterr().err == ""


class TestArguments:
    def test_a_start_date_is_required(self):
        with pytest.raises(SystemExit):
            exporter.parse_args([])

    def test_it_runs_to_today_by_default(self):
        assert exporter.parse_args(["--start", "2026-01-01"]).stop == date.today()
