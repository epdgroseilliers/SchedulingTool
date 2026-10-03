"""domain/tag_recipes.py — the route a tag follows, as a grouping key.

Pure: no database, no Streamlit, no clock. EXAMPLE is TagIndex 47185747,
the real GWA>SWPW tag behind the desk's own
`Tag_dynamique_bilateral - … - ABEX-SWPW GWA.xlsx`, so the golden signature
below is a fact about the desk's data rather than about this test.
"""

import copy
from datetime import date

from domain.tag_recipes import (
    SIGNATURE_VERSION,
    chain_summary,
    counterparties_of,
    endpoints_of,
    group_by_recipe,
    rank_recipes,
    recipe_id,
    recipe_signature,
    route_summary,
    tag_query,
    transmission_providers,
    wire_summary,
)
from domain.tags import blank_tag

EXAMPLE = {
    "tag_index": 47185747,
    "tag_id": "GWA_MAG0014361562_SWPW",
    "gca": "GWA",
    "lca": "SWPW",
    "flow_date": date(2026, 9, 29),
    "market_path": [
        {"index": 1, "pse": "RRWE01", "product": "G-F"},
        {"index": 2, "pse": "ABEX", "product": ""},
        {"index": 3, "pse": "MAG001", "product": "L"},
    ],
    "physical_path": [
        {"index": 1, "ms_index": 1, "type": "G", "tp": "",
         "por_name": "RIMROCK", "por_ca": "GWA", "pod_name": "", "pod_ca": "",
         "reservations": []},
        {"index": 2, "ms_index": 2, "type": "T", "tp": "MATL",
         "por_name": "WWA", "por_ca": "MATL",
         "pod_name": "MATL.NWMT", "pod_ca": "MATL",
         "reservations": [{"product": "1-NS", "contract": "110752124"}]},
        {"index": 3, "ms_index": 3, "type": "T", "tp": "NWMT",
         "por_name": "MATL.NWMT", "por_ca": "NWMT",
         "pod_name": "CROSSOVER", "pod_ca": "NWMT",
         "reservations": [{"product": "7-F", "contract": "109267045"}]},
        {"index": 4, "ms_index": 3, "type": "T", "tp": "SWPP",
         "por_name": "CROSSOVER", "por_ca": "SWPP",
         "pod_name": "WAUW", "pod_ca": "SWPP",
         "reservations": [{"product": "6-NN", "contract": "110751640"}]},
        {"index": 5, "ms_index": 3, "type": "L", "tp": "",
         "por_name": "", "por_ca": "",
         "pod_name": "SWPW_HUB", "pod_ca": "SWPW",
         "reservations": []},
    ],
}

GOLDEN = (
    "GWA>SWPW || MS RRWE01:G-F > ABEX:- > MAG001:L || "
    "PS 1:G RIMROCK(GWA) ; "
    "2:T MATL WWA(MATL)>MATL.NWMT(MATL) ; "
    "3:T NWMT MATL.NWMT(NWMT)>CROSSOVER(NWMT) ; "
    "3:T SWPP CROSSOVER(SWPP)>SWPP-INTO-SWPW(SWPP) ; "
    "3:L SWPW_HUB(SWPW)"
)


def record(**over):
    out = copy.deepcopy(EXAMPLE)
    out.update(over)
    return out


class TestTheSignature:
    def test_the_desks_own_abex_swpw_route(self):
        assert recipe_signature(EXAMPLE) == GOLDEN

    def test_the_format_is_versioned(self):
        # So bumping it forces these strings to be re-read rather than
        # silently re-baselined, and two corpora can't be compared as
        # though they were built the same way.
        assert SIGNATURE_VERSION == 3


class TestWhatItIgnores:
    """The whole idea: two tags a month apart on the same route are one
    recipe."""

    def test_the_dates_and_the_tag_id(self):
        other = record(
            flow_date=date(2026, 3, 1), tag_index=1, tag_id="GWA_MAG0019999999_SWPW",
            start_time="whenever",
        )
        assert recipe_signature(other) == GOLDEN

    def test_every_reservation_number(self):
        other = record()
        for segment in other["physical_path"]:
            for reservation in segment["reservations"]:
                reservation["contract"] = "999999999"
        assert recipe_signature(other) == GOLDEN

    def test_whitespace_and_case(self):
        other = record()
        other["gca"] = " gwa "
        other["market_path"][0]["pse"] = "rrwe01 "
        other["physical_path"][1]["tp"] = " matl"
        assert recipe_signature(other) == GOLDEN


class TestWhatItSeparates:
    def _differs(self, other):
        assert recipe_signature(other) != GOLDEN

    def test_a_different_point_of_delivery(self):
        other = record()
        other["physical_path"][3]["pod_name"] = "PNPK"
        self._differs(other)

    def test_a_different_energy_product(self):
        other = record()
        other["market_path"][0]["product"] = "G-NF"
        self._differs(other)

    def test_a_different_provider(self):
        other = record()
        other["physical_path"][2]["tp"] = "AESO"
        self._differs(other)

    def test_a_segment_paid_for_by_a_different_market_step(self):
        # The ms prefix is the only thing that distinguishes who paid for
        # which leg when the wires themselves are the same.
        other = record()
        other["physical_path"][2]["ms_index"] = 2
        self._differs(other)

    def test_a_wheel_through_being_dropped_from_the_chain(self):
        # ABEX carries no energy product, so a signature that omitted blanks
        # would read this three-party chain as the two-party one.
        other = record()
        other["market_path"] = [
            other["market_path"][0], other["market_path"][2]
        ]
        self._differs(other)


class TestTheAwkwardSegments:
    def test_however_a_segment_was_paid_for(self):
        # How many reservations covered a leg, and what each was bought
        # under, is the day's paperwork — not the route.
        one = record()
        one["physical_path"][1]["reservations"] = [
            {"product": "7-F", "contract": "1"},
            {"product": "1-NS", "contract": "2"},
        ]
        two = record()
        two["physical_path"][1]["reservations"] = [{"product": "2-NH", "contract": "9"}]
        assert recipe_signature(one) == recipe_signature(two)

    def test_even_a_segment_with_no_reservation_at_all(self):
        other = record()
        other["physical_path"][1]["reservations"] = []
        assert recipe_signature(other) == recipe_signature(EXAMPLE)

    def test_a_tag_with_no_path_at_all_still_has_a_signature(self):
        assert recipe_signature({"gca": "GWA", "lca": "SWPW"}) == (
            "GWA>SWPW || MS - || PS -"
        )


class TestTheShortHandle:
    def test_it_is_stable(self):
        assert recipe_id(GOLDEN) == recipe_id(GOLDEN)

    def test_two_routes_get_two_handles(self):
        assert recipe_id(GOLDEN) != recipe_id(GOLDEN + " ; extra")


class TestWhoWeTradedWith:
    """MAG's neighbours in the chain — a set, because MAG is in the middle
    of a wheel a third of the time and appears twice in 15% of chains."""

    def _chain(self, *pses):
        return {"market_path": [{"pse": p, "product": ""} for p in pses]}

    def test_mag_at_the_load_end_has_one_neighbour(self):
        assert counterparties_of(EXAMPLE) == ("ABEX",)

    def test_mag_in_the_middle_has_two(self):
        assert counterparties_of(self._chain("APS01", "MAG001", "EPEC01")) == (
            "APS01", "EPEC01"
        )

    def test_mag_at_the_generator_end_has_one(self):
        assert counterparties_of(self._chain("MAG001", "BPAP01")) == ("BPAP01",)

    def test_mag_twice_in_a_wheel_gathers_both_sides(self):
        assert counterparties_of(
            self._chain("RRWE01", "MSCG01", "MAG001", "MSCG01", "AESO")
        ) == ("MSCG01",)

    def test_mag_at_both_ends_has_nobody(self):
        assert counterparties_of(self._chain("MAG001", "MAG001")) == ()

    def test_a_tag_we_only_watched_has_nobody(self):
        # OATI holds tags MAG merely observed as a control area. Real, not
        # a bug.
        assert counterparties_of(self._chain("APS01", "EPEC01")) == ()


class TestTheHumanLabel:
    def test_it_names_the_wires_in_order(self):
        assert route_summary(EXAMPLE) == "GWA>SWPW via MATL>NWMT>SWPP"

    def test_providers_are_not_repeated(self):
        other = record()
        other["physical_path"][3]["tp"] = "MATL"
        assert transmission_providers(other) == ("MATL", "NWMT")

    def test_a_route_with_no_wheel_is_just_its_ends(self):
        assert route_summary({"gca": "BPAT", "lca": "CISO"}) == "BPAT>CISO"


class TestGrouping:
    def test_the_same_route_twice_is_one_recipe(self):
        recipes = group_by_recipe([EXAMPLE, record(tag_index=2)])
        assert len(recipes) == 1
        assert recipes[0]["count"] == 2

    def test_most_used_first(self):
        other = record(gca="BPAT")
        recipes = group_by_recipe([EXAMPLE, record(tag_index=2), other])
        assert [r["count"] for r in recipes] == [2, 1]

    def test_it_spans_the_dates_it_was_used_on(self):
        recipes = group_by_recipe([
            record(flow_date=date(2026, 3, 1)),
            record(flow_date=date(2026, 9, 29), tag_index=2),
        ])
        assert recipes[0]["first_flow_date"] == date(2026, 3, 1)
        assert recipes[0]["last_flow_date"] == date(2026, 9, 29)

    def test_it_collects_every_counterparty_the_route_was_used_with(self):
        second = record(tag_index=2)
        second["market_path"][1]["pse"] = "EEMU24"
        recipes = group_by_recipe([EXAMPLE, second])
        # Two different chains, so two recipes — but each keeps its own.
        assert {r["counterparties"] for r in recipes} == {("ABEX",), ("EEMU24",)}

    def test_samples_are_newest_first_and_capped(self):
        records = [
            record(tag_index=i, flow_date=date(2026, 9, i)) for i in (1, 2, 3, 4)
        ]
        recipes = group_by_recipe(records, samples=2)
        assert [s["flow_date"].day for s in recipes[0]["samples"]] == [4, 3]

    def test_a_record_with_no_flow_date_does_not_break_the_sort(self):
        recipes = group_by_recipe([record(flow_date=None), record(tag_index=2)])
        assert recipes[0]["count"] == 2

    def test_nothing_in_gives_nothing_out(self):
        assert group_by_recipe([]) == []


def record_with_path(physical_path, **over):
    out = {"gca": "GWA", "lca": "SWPW", "market_path": [],
           "physical_path": physical_path}
    out.update(over)
    return out


class _Leg:
    """Just enough of domain.matching.TradeLeg for tag_query — it reads one
    attribute and one property."""

    def __init__(self, pse, is_market):
        self.pse, self.is_market = pse, is_market


def market_leg(market):
    return _Leg(market, True)


def counterparty_leg(pse):
    return _Leg(pse, False)


def _tag(gca="", lca="", market_path=()):
    tag = blank_tag()
    tag["source"]["gca"] = gca
    tag["sink"]["lca"] = lca
    tag["market_path"] = [
        {"pse": pse, "product": "", "contract": ""} for pse in market_path
    ]
    return tag


class TestTheQueryBuiltFromATag:
    """What rank_recipes is handed — read straight off the tag on screen,
    no parsing involved."""

    def test_the_counterparties_are_the_non_mag_ends(self):
        query = tag_query(_tag(market_path=["ABEX", "MAG001", "BPAT"]))
        assert query["counterparties"] == frozenset({"ABEX", "BPAT"})

    def test_mag_in_the_middle_of_a_wheel_is_still_excluded(self):
        query = tag_query(
            _tag(market_path=["ABEX", "MSCG01", "MAG001", "MSCG01", "AESO"])
        )
        assert "MAG001" not in query["counterparties"]

    def test_comparison_is_case_insensitive(self):
        query = tag_query(_tag(gca="gwa", lca="Swpw", market_path=["abex", "mag001"]))
        assert query["gca"] == "GWA"
        assert query["lca"] == "SWPW"
        assert query["counterparties"] == frozenset({"ABEX"})

    def test_an_untouched_tag_still_has_counterparties(self):
        # The whole point: a lookup means something before anything is typed.
        query = tag_query(_tag(market_path=["ABEX", "MAG001", "BPAT"]))
        assert query["gca"] is None
        assert query["lca"] is None
        assert query["counterparties"]

    def test_a_blank_tag_has_nothing_at_all(self):
        assert tag_query(blank_tag()) == {
            "gca": None, "lca": None,
            "counterparties": frozenset(), "fixed": frozenset(),
        }


class TestRankingRecipesAgainstAQuery:
    def recipe(self, **over):
        base = {
            "recipe_id": "r", "gca": "GWA", "lca": "SWPW", "count": 1,
            "last_flow_date": date(2026, 9, 1),
            "counterparties": ("ABEX",),
        }
        base.update(over)
        return base

    def test_no_shared_counterparty_is_excluded_even_with_matching_gca(self):
        recipes = [self.recipe(gca="GWA", counterparties=("EEMU24",))]
        query = {"gca": "GWA", "lca": None, "counterparties": frozenset({"ABEX"})}
        assert rank_recipes(recipes, query) == []

    def test_a_shared_counterparty_alone_is_enough_to_appear(self):
        recipes = [self.recipe(gca="OTHER", lca="OTHER")]
        query = {"gca": None, "lca": None, "counterparties": frozenset({"ABEX"})}
        assert rank_recipes(recipes, query) == recipes

    def test_a_matching_gca_outranks_a_bare_counterparty_match(self):
        no_gca = self.recipe(recipe_id="no-gca", gca="OTHER")
        with_gca = self.recipe(recipe_id="with-gca", gca="GWA")
        query = {"gca": "GWA", "lca": None, "counterparties": frozenset({"ABEX"})}
        ranked = rank_recipes([no_gca, with_gca], query)
        assert [r["recipe_id"] for r in ranked] == ["with-gca", "no-gca"]

    def test_gca_and_lca_both_matching_outranks_only_one(self):
        both = self.recipe(recipe_id="both", gca="GWA", lca="SWPW")
        one = self.recipe(recipe_id="one", gca="GWA", lca="OTHER")
        query = {"gca": "GWA", "lca": "SWPW", "counterparties": frozenset({"ABEX"})}
        ranked = rank_recipes([one, both], query)
        assert [r["recipe_id"] for r in ranked] == ["both", "one"]

    def test_ties_break_on_count_then_recency(self):
        older = self.recipe(
            recipe_id="older", count=5, last_flow_date=date(2026, 1, 1)
        )
        more_used = self.recipe(recipe_id="more-used", count=9)
        newer = self.recipe(
            recipe_id="newer", count=5, last_flow_date=date(2026, 9, 1)
        )
        query = {"gca": None, "lca": None, "counterparties": frozenset({"ABEX"})}
        ranked = rank_recipes([older, more_used, newer], query)
        assert [r["recipe_id"] for r in ranked] == ["more-used", "newer", "older"]

    def test_limit_truncates(self):
        recipes = [self.recipe(recipe_id=str(i)) for i in range(8)]
        query = {"gca": None, "lca": None, "counterparties": frozenset({"ABEX"})}
        assert len(rank_recipes(recipes, query, limit=3)) == 3

    def test_an_empty_query_matches_nothing(self):
        # Nothing to share a counterparty with — the hard filter, not a bug.
        query = {"gca": None, "lca": None, "counterparties": frozenset()}
        assert rank_recipes([self.recipe()], query) == []


class TestWhatARouteLooksLikeOnScreen:
    """route_summary names control areas; a scheduler recognises a route by
    where it physically starts, ends and wheels."""

    def test_the_two_physical_ends(self):
        assert endpoints_of(EXAMPLE) == ("RIMROCK (GWA)", "SWPW_HUB (SWPW)")

    def test_a_point_with_no_control_area_is_just_the_point(self):
        record = record_with_path([
            {"type": "G", "por_name": "RIMROCK", "por_ca": "", "reservations": []},
            {"type": "L", "pod_name": "SWPW_HUB", "pod_ca": "", "reservations": []},
        ])
        assert endpoints_of(record) == ("RIMROCK", "SWPW_HUB")

    def test_a_route_with_no_physical_path_has_no_ends(self):
        assert endpoints_of({"gca": "GWA", "lca": "SWPW"}) == ("", "")

    def test_every_wheel_in_path_order(self):
        assert wire_summary(EXAMPLE) == (
            "MATL WWA>MATL.NWMT · NWMT MATL.NWMT>CROSSOVER · "
            "SWPP CROSSOVER>WAUW"
        )

    def test_the_real_point_is_shown_even_where_the_signature_folds_it(self):
        # WAUW, not SWPP-INTO-SWPW: a scheduler can go and look at WAUW.
        assert "WAUW" in wire_summary(EXAMPLE)
        assert "SWPP-INTO-SWPW" not in wire_summary(EXAMPLE)

    def test_generation_and_load_are_not_wheels(self):
        assert "RIMROCK" not in wire_summary(EXAMPLE)
        assert "SWPW_HUB" not in wire_summary(EXAMPLE)

    def test_a_route_that_wheels_nowhere_summarises_to_nothing(self):
        record = record_with_path([
            {"type": "G", "por_name": "RIMROCK", "por_ca": "GWA", "reservations": []},
            {"type": "L", "pod_name": "SWPW_HUB", "pod_ca": "SWPW", "reservations": []},
        ])
        assert wire_summary(record) == ""

    def test_a_grouped_recipe_carries_both(self):
        # Exact for the whole recipe, not just the sample they're read off:
        # the physical path is part of the signature.
        recipe = group_by_recipe([EXAMPLE])[0]
        assert recipe["endpoints"] == ("RIMROCK (GWA)", "SWPW_HUB (SWPW)")
        assert recipe["wires"].startswith("MATL WWA>MATL.NWMT")


class TestAMarketEndSettlesItsControlArea:
    """A link into a market is MAG sinking there — the LCA is not a guess."""

    def test_selling_into_a_market_fixes_the_lca(self):
        query = tag_query(
            _tag(market_path=["EPEC01", "MAG001"]),
            sell_leg=market_leg("SWPW"),
        )
        assert query["lca"] == "SWPW"
        assert query["fixed"] == frozenset({"lca"})

    def test_buying_from_a_market_fixes_the_gca(self):
        query = tag_query(
            _tag(market_path=["MAG001", "EPEC01"]),
            buy_leg=market_leg("SWPW"),
        )
        assert query["gca"] == "SWPW"
        assert query["fixed"] == frozenset({"gca"})

    def test_caiso_is_the_one_market_that_tags_under_another_name(self):
        query = tag_query(_tag(), sell_leg=market_leg("CAISO"))
        assert query["lca"] == "CISO"

    def test_a_counterparty_end_settles_nothing(self):
        query = tag_query(
            _tag(gca="GWA"), buy_leg=counterparty_leg("ABEX"),
            sell_leg=counterparty_leg("BPAT"),
        )
        assert query["fixed"] == frozenset()
        assert query["gca"] == "GWA"

    def test_a_market_end_outranks_whatever_was_typed_there(self):
        # The board knows better: the link ends at SWPW whatever the box says.
        query = tag_query(_tag(lca="CISO"), sell_leg=market_leg("SWPW"))
        assert query["lca"] == "SWPW"


class TestAFixedControlAreaRulesRoutesOut:
    def recipe(self, **over):
        base = {
            "recipe_id": "r", "gca": "GWA", "lca": "SWPW", "count": 1,
            "last_flow_date": date(2026, 9, 1), "counterparties": ("EPEC01",),
        }
        base.update(over)
        return base

    def test_the_wrong_sink_is_excluded_however_often_we_ran_it(self):
        # The bug this fixes: an EPE-MAG-SWPW link was offered CISO routes.
        into_ciso = self.recipe(recipe_id="ciso", lca="CISO", count=99)
        into_swpw = self.recipe(recipe_id="swpw", lca="SWPW", count=1)
        query = {
            "gca": None, "lca": "SWPW", "counterparties": frozenset({"EPEC01"}),
            "fixed": frozenset({"lca"}),
        }
        ranked = rank_recipes([into_ciso, into_swpw], query)
        assert [r["recipe_id"] for r in ranked] == ["swpw"]

    def test_the_wrong_source_is_excluded_too(self):
        query = {
            "gca": "SWPW", "lca": None, "counterparties": frozenset({"EPEC01"}),
            "fixed": frozenset({"gca"}),
        }
        assert rank_recipes([self.recipe(gca="CISO")], query) == []

    def test_a_typed_control_area_still_only_ranks(self):
        # Not fixed: a field being filled in shouldn't rule routes out.
        query = {
            "gca": None, "lca": "SWPW", "counterparties": frozenset({"EPEC01"}),
            "fixed": frozenset(),
        }
        assert rank_recipes([self.recipe(lca="CISO")], query)

    def test_a_query_with_no_fixed_key_at_all_still_works(self):
        query = {"gca": None, "lca": None, "counterparties": frozenset({"EPEC01"})}
        assert rank_recipes([self.recipe()], query)


def wheel(tp, por, pod, aref=None, product="7-F", ca="TEPC"):
    return {
        "type": "T", "tp": tp, "ms_index": 1,
        "por_name": por, "por_ca": ca, "pod_name": pod, "pod_ca": ca,
        "reservations": [] if aref is None
        else [{"contract": aref, "product": product}],
    }


class TestWhatTheWireSummaryFoldsAway:
    """OATI records a path in more detail than anyone reads it in."""

    def test_one_reservations_legs_are_one_wheel(self):
        # The real shape of TagIndex 45055176: three legs, one reservation.
        record = record_with_path([
            wheel("TEPC", "PALOVERDE500", "PINALWEST500", "107305499"),
            wheel("TEPC", "PINALWEST500", "VAIL345", "107305499"),
            wheel("TEPC", "VAIL345", "GREENLEE345", "107305499"),
        ])
        assert wire_summary(record) == "TEPC PALOVERDE500>GREENLEE345"

    def test_two_reservations_over_the_same_wire_stay_two_wheels(self):
        record = record_with_path([
            wheel("TEPC", "A", "B", "111111111"),
            wheel("TEPC", "B", "C", "222222222"),
        ])
        assert wire_summary(record) == "TEPC A>B · TEPC B>C"

    def test_legs_with_no_reservation_are_never_folded_together(self):
        # "Both have nothing" is not "both have the same thing".
        record = record_with_path([
            wheel("TEPC", "A", "B"), wheel("TEPC", "B", "C"),
        ])
        assert wire_summary(record) == "TEPC A>B · TEPC B>C"

    def test_a_segment_that_goes_nowhere_is_not_a_wheel(self):
        # 12% of transmission segments: bookkeeping inside one point.
        record = record_with_path([
            wheel("SRP", "PALOVERDE500", "PALOVERDE500", "99005"),
            wheel("TEPC", "PALOVERDE500", "VAIL345", "107305499"),
        ])
        assert wire_summary(record) == "TEPC PALOVERDE500>VAIL345"

    def test_nor_is_a_run_of_legs_that_comes_back_where_it_started(self):
        record = record_with_path([
            wheel("TEPC", "A", "B", "111111111"),
            wheel("TEPC", "B", "A", "111111111"),
        ])
        assert wire_summary(record) == ""

    def test_firm_and_non_firm_over_the_same_wire_are_one_route(self):
        # The day's paperwork, not the route — and so neither grouped on
        # nor shown. The *energy* product is the one that counts: see
        # TestTheChainOnACard.
        firm = record_with_path([wheel("MATL", "WWA", "MATL.NWMT", "1", "7-F")])
        secondary = record_with_path([wheel("MATL", "WWA", "MATL.NWMT", "2", "1-NS")])
        assert recipe_signature(firm) == recipe_signature(secondary)
        assert wire_summary(firm) == wire_summary(secondary)


class TestPointsThatAreTheSamePlace:
    """SWPP delivers into SWPW at four names the desk treats as one."""

    def into_swpw(self, point):
        return record_with_path([
            wheel("SWPP", "CROSSOVER", point, "111111111", ca="SWPP"),
        ])

    def test_two_of_them_are_one_recipe(self):
        assert recipe_signature(self.into_swpw("CRSP")) == recipe_signature(
            self.into_swpw("WAUW")
        )

    def test_all_four_are(self):
        assert len({
            recipe_signature(self.into_swpw(p))
            for p in ("CRSP", "WAUW", "TSGT", "CSU")
        }) == 1

    def test_a_point_that_is_not_one_of_them_still_separates(self):
        assert recipe_signature(self.into_swpw("CRSP")) != recipe_signature(
            self.into_swpw("MEAD")
        )

    def test_the_same_name_in_another_control_area_is_another_place(self):
        # WAUW is also a control area of its own with a load point of the
        # same name — a real destination, and not SWPP's delivery into SWPW.
        own_area = record_with_path([
            {"type": "L", "ms_index": 1, "pod_name": "WAUW", "pod_ca": "WAUW",
             "por_name": "", "por_ca": "", "reservations": []},
        ])
        assert "WAUW(WAUW)" in recipe_signature(own_area)

    def test_they_group_into_one_recipe_with_one_count(self):
        recipes = group_by_recipe([
            record_with_path(self.into_swpw("CRSP")["physical_path"], tag_index=1),
            record_with_path(self.into_swpw("WAUW")["physical_path"], tag_index=2),
        ])
        assert len(recipes) == 1
        assert recipes[0]["count"] == 2


class TestTheChainOnACard:
    """The market path, shown because it is the commonest thing two
    otherwise-identical routes differ in."""

    def test_the_pse_chain_with_its_products(self):
        assert chain_summary(EXAMPLE) == "RRWE01 (G-F) > ABEX > MAG001 (L)"

    def test_a_wheel_through_carries_no_product_and_shows_none(self):
        assert "ABEX ()" not in chain_summary(EXAMPLE)

    def test_firm_and_non_firm_are_two_different_chains(self):
        # The second half of the duplicate-looking-cards bug: these two are
        # separate recipes and used to read identically on screen.
        non_firm = record()
        non_firm["market_path"][0]["product"] = "G-NF"
        assert chain_summary(EXAMPLE) != chain_summary(non_firm)
        assert recipe_signature(EXAMPLE) != recipe_signature(non_firm)

    def test_a_grouped_recipe_carries_it(self):
        assert group_by_recipe([EXAMPLE])[0]["chain"] == chain_summary(EXAMPLE)

    def test_a_tag_with_no_market_path_summarises_to_nothing(self):
        assert chain_summary({"gca": "GWA", "lca": "SWPW"}) == ""
