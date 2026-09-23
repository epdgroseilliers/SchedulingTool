"""ui/nav.py — the header both pages share.

Streamlit's own page list lives in the sidebar, which makes switching pages
cost a drawer-open first. The nav replaces it with one always-visible
button, so what matters is that the button exists on both pages, points at
the other one, and that the sidebar list is actually suppressed.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "app.py")
SCHEDULING = str(ROOT / "pages" / "1_Scheduling_View.py")


@pytest.fixture
def no_bilateral_db(monkeypatch):
    monkeypatch.setattr(
        "ui.scheduling.state.load_trades_for_flow_date", lambda flow_date: ([], None)
    )


def _run(path):
    return AppTest.from_file(path, default_timeout=120).run()


class TestHeader:
    def test_add_trade_offers_a_way_to_the_scheduling_view(self):
        at = _run(APP)
        assert not at.exception, [e.value for e in at.exception]
        assert [b for b in at.button if "Scheduling View" in b.label]

    def test_the_scheduling_view_offers_a_way_back(self, no_bilateral_db):
        at = _run(SCHEDULING)
        assert not at.exception, [e.value for e in at.exception]
        assert [b for b in at.button if "Add Trade" in b.label]

    def test_each_page_names_itself(self, no_bilateral_db):
        assert any("Add Trade" in m.value for m in _run(APP).markdown)
        assert any("Scheduling View" in m.value for m in _run(SCHEDULING).markdown)

    def test_the_nav_button_is_not_the_add_trade_submit_button(self):
        # The submit button on the Add Trade page is labelled exactly "Add
        # Trade"; the nav button carries its icon, so tests and traders can
        # still tell them apart.
        at = _run(APP)
        assert [b for b in at.button if b.label == "Add Trade"]
        assert [b for b in at.button if b.label == "🔗 Scheduling View"]

    def test_the_sidebar_page_list_is_suppressed(self, no_bilateral_db):
        at = _run(SCHEDULING)
        assert any(
            'data-testid="stSidebarNav"' in h.proto.body and "display: none" in h.proto.body
            for h in at.get("html")
        )
