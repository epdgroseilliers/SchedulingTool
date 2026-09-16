"""domain.options: mostly static data, but a few invariants are worth
locking in so a future edit can't silently break them."""

from data.bilateral import TIME_ZONES
from domain.options import (
    COMMUNICATION_METHODS,
    COUNTERPARTIES,
    DEFAULT_COMMUNICATION,
    DEFAULT_SPECIFIED_SOURCE,
    DEFAULT_WSPP_CONTRACT,
    LOCATIONS,
    MIDC_POR_PODS,
    PRICING_NODE_POR_PODS,
    RARE_FIELD_DEFAULTS,
    RARE_FIELD_LABELS,
    SPECIFIED_SOURCES,
    TIME_ZONE,
    WSPP_CONTRACT_TYPES,
    default_index,
)


def test_time_zone_is_one_the_db_accepts():
    # The module already asserts this at import time; re-asserted here so a
    # test failure (not an ImportError at collection time) is what a future
    # regression looks like.
    assert TIME_ZONE in TIME_ZONES


def test_default_wspp_contract_is_a_real_option():
    assert DEFAULT_WSPP_CONTRACT in WSPP_CONTRACT_TYPES


def test_placeholder_defaults_are_actual_list_entries():
    # None is the "no selection" placeholder in these lists — the defaults
    # must point at an entry that's actually there, or default_index()
    # can't find it.
    assert DEFAULT_COMMUNICATION in COMMUNICATION_METHODS
    assert DEFAULT_SPECIFIED_SOURCE in SPECIFIED_SOURCES


def test_midc_por_pods_is_the_same_list_used_in_pricing_node_map():
    assert PRICING_NODE_POR_PODS["MIDC"] is MIDC_POR_PODS


def test_rare_field_labels_cover_every_default():
    assert set(RARE_FIELD_LABELS) == set(RARE_FIELD_DEFAULTS)


def test_option_lists_are_nonempty():
    assert COUNTERPARTIES
    assert LOCATIONS


class TestDefaultIndex:
    def test_found(self):
        assert default_index(["C", "B"], "C") == 0
        assert default_index(["C", "B"], "B") == 1

    def test_not_found_returns_none(self):
        assert default_index(["C", "B"], "Z") is None

    def test_none_default_found_when_present(self):
        assert default_index([None, "ICE"], None) == 0
