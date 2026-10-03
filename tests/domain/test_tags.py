"""domain/tags.py — what a bilateral e-Tag carries, what a link implies
about it, and what has to be filled in before one can be written. Pure: no
Streamlit, no filesystem, no workbook."""

from datetime import date

import pytest

from domain.matching import BUY, SELL, TradeLeg
from domain.tags import (
    GENERATOR_PRODUCT,
    PATH_UNKNOWN,
    LOAD_PRODUCT,
    MAG_PSE,
    blank_tag,
    carbon_copy_rows,
    clean_rows,
    day_folder_name,
    default_market_path,
    default_path_string,
    default_tag,
    folder_covers,
    lookupable_aref,
    market_path_rows,
    safe_name,
    sink_pse,
    source_pse,
    tag_errors,
    tag_filename,
    transmission_rows,
)

FLOW = date(2026, 9, 25)


def leg(direction, pse, source="db", hours=range(7, 23), mw=25.0):
    return TradeLeg(
        key=f"{source}:{pse}",
        source=source,
        direction=direction,
        pse=pse,
        por_pod="Market" if source == "market" else "PALOVERDE500",
        flow_date=FLOW,
        mw_by_hour={h: mw for h in hours},
    )


def filled_tag(**over):
    """A tag with the least that passes validation, so each test can break
    exactly one thing."""
    tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {1: 10.0}, FLOW)
    tag["source"]["gca"] = "GWA"
    tag["sink"]["lca"] = "BPAT"
    tag.update(over)
    return tag


class TestTheChainALinkImplies:
    def test_mag_stands_between_two_counterparties(self):
        path = default_market_path(leg(BUY, "ABEX"), leg(SELL, "BPAT"))
        assert [row["pse"] for row in path] == ["ABEX", MAG_PSE, "BPAT"]

    def test_the_ends_carry_the_generator_and_load_products(self):
        path = default_market_path(leg(BUY, "ABEX"), leg(SELL, "BPAT"))
        assert path[0]["product"] == GENERATOR_PRODUCT
        assert path[-1]["product"] == LOAD_PRODUCT
        # A wheel-through in the middle carries neither.
        assert path[1]["product"] == ""

    def test_selling_into_a_market_puts_mag_at_the_load_end(self):
        path = default_market_path(
            leg(BUY, "ABEX"), leg(SELL, "SWPW", source="market")
        )
        assert [(row["pse"], row["product"]) for row in path] == [
            ("ABEX", GENERATOR_PRODUCT),
            (MAG_PSE, LOAD_PRODUCT),
        ]

    def test_buying_from_a_market_puts_mag_at_the_generator_end(self):
        path = default_market_path(
            leg(BUY, "SWPW", source="market"), leg(SELL, "BPAT")
        )
        assert [(row["pse"], row["product"]) for row in path] == [
            (MAG_PSE, GENERATOR_PRODUCT),
            ("BPAT", LOAD_PRODUCT),
        ]

    def test_market_to_market_is_mag_at_both_ends(self):
        # What the desk's own SWPW-SWPP template holds: MAG001 G-F, MAG001 L.
        path = default_market_path(
            leg(BUY, "SWPP", source="market"), leg(SELL, "SWPW", source="market")
        )
        assert [(row["pse"], row["product"]) for row in path] == [
            (MAG_PSE, GENERATOR_PRODUCT),
            (MAG_PSE, LOAD_PRODUCT),
        ]


class TestTheChainIsWrittenInPseCodes:
    """A leg's `pse` is a BilateralMarket.MarketName — the desk's internal
    name, AZPS — and a tag's market path is written in PSE codes, APS01. The
    lookup is passed in so domain/ stays free of the database."""

    CODES = {"AZPS": "APS01", "EPE": "EPEC01", "BPAT": "BPAP01"}

    def pse_for(self, market):
        return self.CODES.get(market)

    def test_both_ends_come_back_as_codes(self):
        path = default_market_path(leg(BUY, "AZPS"), leg(SELL, "EPE"), self.pse_for)
        assert [row["pse"] for row in path] == ["APS01", MAG_PSE, "EPEC01"]

    def test_a_market_the_lookup_does_not_know_keeps_its_own_name(self):
        # Better a legible starting point than an empty cell — ABEX really
        # does tag as ABEX, and a counterparty missing from the mapping is
        # a gap in the mapping, not a reason to refuse the row.
        path = default_market_path(leg(BUY, "ABEX"), leg(SELL, "EPE"), self.pse_for)
        assert [row["pse"] for row in path] == ["ABEX", MAG_PSE, "EPEC01"]

    def test_with_no_lookup_at_all_it_behaves_as_it_did_before(self):
        path = default_market_path(leg(BUY, "AZPS"), leg(SELL, "EPE"))
        assert [row["pse"] for row in path] == ["AZPS", MAG_PSE, "EPE"]

    def test_a_market_end_is_still_mag(self):
        # SWPW is a place, not a counterparty: it has no PSE of its own and
        # the lookup correctly answers None for it.
        path = default_market_path(
            leg(BUY, "AZPS"), leg(SELL, "SWPW", source="market"), self.pse_for
        )
        assert [row["pse"] for row in path] == ["APS01", MAG_PSE]

    def test_the_lookup_reaches_a_whole_tag(self):
        tag = default_tag(
            leg(BUY, "AZPS"), leg(SELL, "EPE"), {1: 1.0}, FLOW, pse_for=self.pse_for
        )
        assert [row["pse"] for row in tag["market_path"]] == [
            "APS01", MAG_PSE, "EPEC01"
        ]
        # The Market cells keep the internal name — that is what the desk
        # writes in them, and it is what the back office keys on.
        assert tag["source"]["market"] == "AZPS"


class TestTheSheetsTwoPseCellsAreDerived:
    def test_the_source_cell_is_the_head_of_the_path(self):
        tag = filled_tag(market_path=[
            {"pse": "RRWE01", "product": "G-F"},
            {"pse": "ABEX", "product": ""},
            {"pse": MAG_PSE, "product": "L"},
        ])
        assert source_pse(tag) == "RRWE01"

    def test_the_sink_cell_is_the_tail(self):
        tag = filled_tag(market_path=[
            {"pse": "APS01", "product": "G-F"},
            {"pse": MAG_PSE, "product": ""},
            {"pse": "EPEC01", "product": "L"},
        ])
        assert sink_pse(tag) == "EPEC01"

    def test_a_mag_end_is_blank_not_mag(self):
        # What the desk's own files hold: MAG sinking into SWPW leaves the
        # sink PSE cell empty rather than writing MAG001 into it.
        tag = filled_tag(market_path=[
            {"pse": "RRWE01", "product": "G-F"},
            {"pse": MAG_PSE, "product": "L"},
        ])
        assert (source_pse(tag), sink_pse(tag)) == ("RRWE01", "")

    def test_both_blank_when_mag_stands_at_both_ends(self):
        tag = filled_tag(market_path=[
            {"pse": MAG_PSE, "product": "G-F"},
            {"pse": MAG_PSE, "product": "L"},
        ])
        assert (source_pse(tag), sink_pse(tag)) == ("", "")

    def test_an_empty_path_answers_blank_rather_than_raising(self):
        tag = blank_tag(FLOW)
        assert (source_pse(tag), sink_pse(tag)) == ("", "")

    def test_a_one_row_path_is_both_ends(self):
        tag = filled_tag(market_path=[{"pse": "APS01", "product": "G-F"}])
        assert (source_pse(tag), sink_pse(tag)) == ("APS01", "APS01")

    def test_neither_is_a_field_a_trader_can_type(self):
        # Removing them is the point: one code, one place.
        assert "pse" not in blank_tag(FLOW)["source"]
        assert "pse" not in blank_tag(FLOW)["sink"]


class TestWhatALinkFillsIn:
    def test_the_schedule_comes_from_the_link(self):
        tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {7: 25.0, 8: 30.0}, FLOW)
        assert tag["mw_by_hour"] == {7: 25.0, 8: 30.0}
        assert tag["flow_date"] == FLOW

    def test_both_ends_name_their_market(self):
        tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {1: 1.0}, FLOW)
        assert tag["source"]["market"] == "ABEX"
        assert tag["sink"]["market"] == "BPAT"

    def test_the_tag_is_named_after_the_two_ends(self):
        tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {1: 1.0}, FLOW)
        assert tag["name"] == "ABEX-BPAT"

    def test_nothing_else_is_guessed(self):
        # The board knows no control areas, no PSE codes and no
        # reservations, and a tag that pretended otherwise would be one a
        # scheduler has to check rather than fill.
        tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {1: 1.0}, FLOW)
        assert tag["source"]["gca"] == ""
        assert tag["sink"]["lca"] == ""
        assert tag["transmissions"] == []
        assert tag["carbon_copy"] == []


class TestTheOpeningPathString:
    """What goes out on chat before anyone has agreed anything: the two PSEs
    either side of MAG, and a placeholder for each end still to be named."""

    CODES = {"AZPS": "APS01", "EPE": "EPEC01"}

    def test_two_counterparties_with_mag_between_them(self):
        assert (
            default_path_string(leg(BUY, "ABEX"), leg(SELL, "BPAT"))
            == "??-ABEX-MAG001-BPAT-??"
        )

    def test_it_is_written_in_the_same_codes_as_the_market_path(self):
        # The string is the market path said out loud; the two disagreeing
        # would be the desk telling a scheduler one route and tagging another.
        string = default_path_string(
            leg(BUY, "AZPS"), leg(SELL, "EPE"), self.CODES.get
        )
        assert string == "??-APS01-MAG001-EPEC01-??"

    def test_selling_into_a_market_is_mag_sinking_it(self):
        # A market end is MAG standing at it, which is a fact — and the
        # placeholders are only ever for what nobody knows yet, so there
        # isn't one past the end of the path.
        string = default_path_string(
            leg(BUY, "AZPS"), leg(SELL, "SWPW", source="market"), self.CODES.get
        )
        assert string == "??-APS01-MAG001(s)"

    def test_buying_from_one_is_mag_generating_it(self):
        string = default_path_string(
            leg(BUY, "SWPW", source="market"), leg(SELL, "AZPS"), self.CODES.get
        )
        assert string == "MAG001(g)-APS01-??"

    def test_a_market_at_both_ends_is_marked_at_both(self):
        string = default_path_string(
            leg(BUY, "SWPP", source="market"),
            leg(SELL, "SWPW", source="market"),
        )
        assert string == "MAG001(g)-MAG001(s)"

    def test_two_counterparties_keep_both_placeholders(self):
        string = default_path_string(leg(BUY, "ABEX"), leg(SELL, "BPAT"))
        assert string == "??-ABEX-MAG001-BPAT-??"
        assert "(s)" not in string and "(g)" not in string

    def test_both_ends_are_the_same_placeholder(self):
        string = default_path_string(leg(BUY, "ABEX"), leg(SELL, "BPAT"))
        assert string.startswith(f"{PATH_UNKNOWN}-")
        assert string.endswith(f"-{PATH_UNKNOWN}")

    def test_a_link_opens_carrying_it(self):
        tag = default_tag(leg(BUY, "ABEX"), leg(SELL, "BPAT"), {1: 1.0}, FLOW)
        assert tag["path_string"] == "??-ABEX-MAG001-BPAT-??"

    def test_a_blank_tag_has_no_path_at_all(self):
        # The shape carries the field; only a link can fill it, because only
        # a link knows who the two counterparties are.
        assert blank_tag(FLOW)["path_string"] == ""


class TestReadingWhatTheEditorHandsBack:
    def test_a_row_the_trader_added_and_left_alone_is_not_a_row(self):
        rows = clean_rows([{"pse": None, "product": None}], ("pse", "product"))
        assert rows == []

    def test_nan_counts_as_empty(self):
        # A dynamic data editor fills an untouched new row with NaN, which
        # is the one value that isn't equal to itself.
        nan = float("nan")
        assert clean_rows([{"pse": nan, "product": nan}], ("pse", "product")) == []

    def test_a_partly_filled_row_survives_whole(self):
        rows = clean_rows([{"pse": " MAG001 ", "product": None}], ("pse", "product"))
        assert rows == [{"pse": "MAG001", "product": ""}]

    def test_each_block_reads_its_own_fields(self):
        tag = blank_tag(FLOW)
        tag["market_path"] = [{"pse": "MAG001", "product": "L", "contract": ""}]
        tag["transmissions"] = [{"path": "W/TEPC/", "reservation": "1107", "mw": ""}]
        tag["carbon_copy"] = [{"type": "BA", "market": "EPE"}]
        assert market_path_rows(tag)[0]["product"] == "L"
        assert transmission_rows(tag)[0]["reservation"] == "1107"
        assert carbon_copy_rows(tag)[0] == {"type": "BA", "market": "EPE"}


class TestWhatStopsATagBeingWritten:
    def test_a_filled_tag_has_nothing_to_say(self):
        assert tag_errors(filled_tag()) == []

    def test_the_gca_is_required(self):
        tag = filled_tag()
        tag["source"]["gca"] = "  "
        assert "GCA is required." in tag_errors(tag)

    def test_the_lca_is_required(self):
        tag = filled_tag()
        tag["sink"]["lca"] = ""
        assert "LCA is required." in tag_errors(tag)

    def test_a_one_sided_market_path_is_not_a_path(self):
        tag = filled_tag(market_path=[{"pse": "ABEX", "product": "G-F"}])
        assert any("at least two PSEs" in e for e in tag_errors(tag))

    def test_a_row_with_a_product_but_no_pse_is_caught(self):
        tag = filled_tag(
            market_path=[
                {"pse": "ABEX", "product": "G-F"},
                {"pse": "MAG001", "product": "L"},
                {"pse": "", "product": "L"},
            ]
        )
        assert "Every market path row needs a PSE." in tag_errors(tag)

    def test_an_hourless_link_has_nothing_to_tag(self):
        tag = filled_tag(mw_by_hour={})
        assert any("nothing to tag" in e for e in tag_errors(tag))

    @pytest.mark.parametrize(
        "field, fields, limit, what",
        [
            ("transmissions", ("path",), 13, "transmission"),
            ("market_path", ("pse",), 10, "market path"),
            ("carbon_copy", ("type",), 8, "carbon copy"),
        ],
    )
    def test_more_rows_than_the_sheet_has_room_for(self, field, fields, limit, what):
        # The blocks sit one under the other in a fixed layout, so an
        # eleventh market-path row would land on the Carbon Copy header.
        tag = filled_tag(
            **{field: [{f: f"x{i}" for f in fields} for i in range(limit + 1)]}
        )
        assert any(
            f"room for {limit} {what} rows" in e for e in tag_errors(tag)
        ), tag_errors(tag)


class TestWhichReservationNumbersAreWorthALookup:
    """The "# trans" column takes whatever a scheduler needs to write in it.
    Only a plain OASIS assignment reference is worth asking the database
    about; everything else is left alone, with no lookup and no complaint."""

    def test_a_nine_digit_reference(self):
        assert lookupable_aref("109267045")

    def test_a_seven_digit_one_too(self):
        # Older references are seven digits, they resolve, and ten of them
        # appear on the desk's own September tags.
        assert lookupable_aref("5671116")

    def test_surrounding_space_does_not_matter(self):
        assert lookupable_aref("  109267045  ")

    @pytest.mark.parametrize(
        "value",
        ["FCATBTEP", "EPEPVEX", "GF", "TEPC0503", "1-NS", "109,267,045", "", None],
    )
    def test_anything_that_is_not_a_bare_reference_is_left_alone(self, value):
        assert not lookupable_aref(value)

    @pytest.mark.parametrize("value", ["10031", "12345678", "1234567890"])
    def test_nor_is_a_number_of_the_wrong_length(self, value):
        # Contract references land in this column too, and asking OASIS
        # about one only wastes a query.
        assert not lookupable_aref(value)


class TestWhatTheFileIsCalled:
    def test_it_matches_the_desks_own_names(self):
        assert tag_filename(FLOW, "ABEX-SWPW GWA") == (
            "Tag_dynamique_bilateral - September 25 2026 - ABEX-SWPW GWA.xlsx"
        )

    def test_the_day_has_no_leading_zero(self):
        assert "September 1 2026" in tag_filename(date(2026, 9, 1), "x")

    def test_a_name_windows_would_refuse_is_made_safe(self):
        assert "/" not in tag_filename(FLOW, "SWPP/SWPW")
        assert safe_name("SWPP/SWPW") == "SWPP-SWPW"

    def test_an_unnamed_tag_is_still_a_file(self):
        assert tag_filename(FLOW, "   ").endswith(" - Tag.xlsx")


class TestWhichFolderADayBelongsIn:
    def test_a_single_day_folder(self):
        assert folder_covers("25 September 2026", FLOW)

    def test_a_two_day_folder_covers_both_of_them(self):
        assert folder_covers("25-26 September 2026", FLOW)
        assert folder_covers("25-26 September 2026", date(2026, 9, 26))
        assert not folder_covers("25-26 September 2026", date(2026, 9, 27))

    def test_the_copy_windows_leaves_behind_still_matches(self):
        assert folder_covers("25 September 2026 - Copy", FLOW)

    def test_another_month_or_year_does_not(self):
        assert not folder_covers("25 October 2026", FLOW)
        assert not folder_covers("25 September 2025", FLOW)

    def test_the_folders_that_are_not_days_are_left_alone(self):
        for name in ("Archives", "Templates", "Systematics", "Test", ""):
            assert not folder_covers(name, FLOW)

    def test_a_day_folder_that_does_not_exist_yet_is_named_for_one_day(self):
        # Never a two-day name: pairing days is a judgement about the
        # trading session, not something to invent.
        assert day_folder_name(FLOW) == "25 September 2026"
