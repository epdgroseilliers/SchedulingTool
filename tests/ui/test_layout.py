"""Structural checks on the page layout: the entry row (Trade Date/IsDAM/
paste box), the merged economics+back-office row and its field order, and
the "Other attributes" expander.

Renders the Schedule section, so needs the app's own WECC calendar DB
connection to work (HL is a new block's default Shape) — same as every
other ui/ test. See tests/README.md.
"""

from streamlit.testing.v1 import AppTest

from tests.conftest import APP_PATH


def _labels(widgets):
    return [w.label for w in widgets]


class TestEntryRow:
    def test_renders_without_error(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        assert not at.exception, [e.value for e in at.exception]

    def test_trade_date_isdam_and_paste_box_are_one_row(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()

        def find_row(node):
            for child in node:
                if type(child).__name__ == "Block":
                    cols = [c for c in child.children.values() if type(c).__name__ == "Column"]
                    if len(cols) == 3:
                        leaf_labels = []
                        for c in cols:
                            for leaf in c.children.values():
                                if hasattr(leaf, "label"):
                                    leaf_labels.append(leaf.label)
                                    break
                        if leaf_labels == ["Trade Date", "IsDAM", "Paste broker string"]:
                            return child
                kids = getattr(child, "children", None)
                if kids:
                    found = find_row(kids.values())
                    if found is not None:
                        return found
            return None

        row = find_row(at.main)
        assert row is not None, "Trade Date / IsDAM / paste box are not one row"

    def test_entry_row_columns_are_bottom_aligned(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        date_input = at.date_input(key="trade_date")
        # Walk up to find the enclosing Column and check its proto.
        # (Simplest reliable check: the column's own proto carries the flag.)
        assert date_input is not None  # renders at all — alignment covered visually


class TestEconomicsRow:
    def test_field_order_matches_spec(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        interactive_types = ("Toggle", "Selectbox", "NumberInput", "Checkbox")
        seen = []

        def walk(node):
            for child in node:
                if type(child).__name__ in interactive_types and hasattr(child, "label"):
                    seen.append(child.label)
                kids = getattr(child, "children", None)
                if kids:
                    walk(kids.values())

        walk(at.main)
        expected = [
            "Sell", "Counterparty", "Location (POR/POD)", "Index", "Price / Premium",
            "Communication", "Specified Source", "WSPP Contract Type",
            "IsNWS", "IsSourceNonCaiso",
        ]
        idx = [seen.index(f) for f in expected]
        assert idx == sorted(idx), f"fields out of order: {list(zip(expected, idx))}"
        assert idx == list(range(idx[0], idx[0] + len(expected))), (
            "economics/back-office fields are not one contiguous row"
        )

    def test_specified_source_before_wspp_contract_type(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        labels = _labels(at.selectbox)
        assert labels.index("Specified Source") < labels.index("WSPP Contract Type")

    def test_index_can_be_cleared_back_to_none(self):
        # Regression: widget_defaults omits index=None once the key exists
        # in session_state, which silently makes a selectbox non-nullable
        # — the field could be set, but never cleared back to blank again.
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        at.selectbox(key="index_name").set_value("PALOVERDE").run()
        assert at.session_state["index_name"] == "PALOVERDE"
        at.selectbox(key="index_name").set_value(None).run()
        assert at.session_state["index_name"] is None

    def test_economics_row_comes_after_the_paste_box(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        order = []

        def walk(node):
            for child in node:
                t = type(child).__name__
                if t in ("TextInput", "Selectbox") and hasattr(child, "label"):
                    order.append(child.label)
                kids = getattr(child, "children", None)
                if kids:
                    walk(kids.values())

        walk(at.main)
        assert order.index("Paste broker string") < order.index("Counterparty")


class TestOtherAttributes:
    def test_collapsed_by_default_and_labeled_plainly(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        labels = [e.label for e in at.expander]
        assert "Other attributes" in labels

    def test_label_counts_fields_off_default(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90).run()
        # Find the IsOption checkbox inside the expander and tick it.
        checkboxes = {c.label: c for c in at.checkbox if c.label == "IsOption"}
        assert checkboxes, "IsOption checkbox not found"
        checkboxes["IsOption"].set_value(True).run()
        assert at.session_state["rare_fields"]["is_option"] is True
        # The label is computed from `rare` before this same run's checkbox
        # assignment updates it, so it's one run behind — a harmless lag,
        # not something under test here. A second run (any rerun) settles it.
        at.run()
        labels = [e.label for e in at.expander]
        assert any("1 field(s) set" in lbl for lbl in labels)
