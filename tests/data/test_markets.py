"""data/markets.py — market name to tagging PSE code.

The fake engine follows tests/data/test_matching_reads.py: it is its own
context manager and cursor, and can be primed to raise. Both cached lookups
are cleared around every test, or one test's fake rows answer the next one's
question.
"""

import pytest

import data.markets as markets

MAP_ROWS = [
    # MarketName, MarketFullName, PSECode, MappingId
    ("AZPS", "Arizona Public Service Company", "APS01", 10),
    ("MSCG", "Morgan Stanley Capital Group, Inc.", "MSCG01", 43),
    ("MSCG", "Morgan Stanley Capital Group, Inc.", "RRWE01", 82),
    ("MAG", "MAG Energy Solutions Inc.", "MAG001", 24),
    ("MAG", "MAG Energy Solutions Inc.", "MMA", 67),
]
USAGE_ROWS = [("MAG001", 12835), ("RRWE01", 811), ("MSCG01", 650), ("PPLMS1", 143)]


class FakeEngine:
    """One connection, answering each query with whatever it was primed
    with, in call order."""

    def __init__(self, results=(), error=None):
        self._results = list(results)
        self._error = error
        self.calls = []

    def connect(self):
        if self._error:
            raise self._error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        self._rows = self._results.pop(0) if self._results else []
        return self

    def fetchall(self):
        return self._rows


@pytest.fixture(autouse=True)
def clear_caches():
    markets.clear_cache()
    yield
    markets.clear_cache()


def wire(monkeypatch, *, map_rows=MAP_ROWS, usage_rows=USAGE_ROWS, error=None):
    """Both lookups answered by one fake engine. market_pse_map calls
    pse_usage, so the map query runs first and the usage query second."""
    engine = FakeEngine([map_rows, usage_rows], error=error)
    monkeypatch.setattr(markets, "get_bilateral_engine", lambda: engine)
    return engine


class TestTheMapping:
    def test_a_market_with_one_code(self, monkeypatch):
        wire(monkeypatch)
        assert markets.pse_for_market("AZPS") == "APS01"

    def test_a_market_with_several_takes_the_one_we_use_most(self, monkeypatch):
        # RRWE01 has the higher mapping Id, so insertion order alone would
        # have picked MSCG01; our own tags say otherwise.
        wire(monkeypatch)
        assert markets.market_pse_map()["MSCG"] == ["RRWE01", "MSCG01"]
        assert markets.pse_for_market("MSCG") == "RRWE01"

    def test_our_own_code_resolves_to_the_one_the_west_desk_tags_under(
        self, monkeypatch
    ):
        wire(monkeypatch)
        assert markets.pse_for_market("MAG") == "MAG001"

    def test_a_code_the_history_has_never_seen_falls_back_to_mapping_order(
        self, monkeypatch
    ):
        wire(monkeypatch, usage_rows=[])
        assert markets.market_pse_map()["MSCG"] == ["MSCG01", "RRWE01"]

    def test_a_market_with_no_mapping_has_no_code(self, monkeypatch):
        # SWPW is a place, not a counterparty — the tag builder reads None
        # as "MAG stands at this end".
        wire(monkeypatch)
        assert markets.pse_for_market("SWPW") is None

    def test_an_unnamed_market_asks_nothing(self, monkeypatch):
        wire(monkeypatch)
        assert markets.pse_for_market(None) is None
        assert markets.pse_for_market("") is None

    def test_padding_around_a_code_is_stripped(self, monkeypatch):
        wire(monkeypatch, map_rows=[("  AZPS ", "x", " APS01  ", 10)])
        assert markets.pse_for_market("AZPS") == "APS01"


class TestWhatTheDropdownOffers:
    def test_it_is_the_mapping_and_the_history_together(self, monkeypatch):
        # PPLMS1 is used on real tags and is in no mapping row; MMA is
        # mapped and unused. Both have to be offered.
        wire(monkeypatch)
        codes = markets.known_pse_codes()
        assert "PPLMS1" in codes and "MMA" in codes

    def test_it_is_sorted_and_free_of_duplicates(self, monkeypatch):
        wire(monkeypatch)
        codes = markets.known_pse_codes()
        assert codes == sorted(set(codes))


class TestWhenTheDatabaseIsUnreachable:
    def test_the_mapping_degrades_to_nothing_rather_than_raising(self, monkeypatch):
        # The tag builder then defaults to counterparty names, as it did
        # before this lookup existed — worse, but still usable.
        wire(monkeypatch, error=OSError("no route to host"))
        assert markets.market_pse_map() == {}
        assert markets.pse_for_market("AZPS") is None

    def test_the_dropdown_falls_back_to_free_text(self, monkeypatch):
        wire(monkeypatch, error=OSError("no route to host"))
        assert markets.known_pse_codes() == []

    def test_losing_only_the_history_still_leaves_the_mapping(self, monkeypatch):
        # The tagging database is the one more likely to be slow; losing it
        # should cost the ranking, not the codes.
        wire(monkeypatch)
        monkeypatch.setattr(markets, "pse_usage", lambda: {})
        assert markets.market_pse_map()["MSCG"] == ["MSCG01", "RRWE01"]


@pytest.mark.db
class TestAgainstTheLiveMapping:
    """Read-only, against the desk's real BilateralMarket tables."""

    def test_the_counterparties_the_tag_files_use(self):
        markets.clear_cache()
        assert markets.pse_for_market("AZPS") == "APS01"
        assert markets.pse_for_market("EPE") == "EPEC01"
        assert markets.pse_for_market("BPAT") == "BPAP01"
        markets.clear_cache()

    def test_our_own_pse(self):
        markets.clear_cache()
        assert markets.pse_for_market("MAG") == "MAG001"
        markets.clear_cache()

    def test_the_dropdown_covers_codes_the_mapping_does_not_have(self):
        # PPLMS1 (Colstrip PPL) appears on real tags and in no mapping row.
        markets.clear_cache()
        codes = markets.known_pse_codes()
        assert "PPLMS1" in codes
        assert len(codes) > 150
        markets.clear_cache()
