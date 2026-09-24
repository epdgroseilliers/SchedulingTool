"""Runs tests/frontend/bid_grid_checks.js — the only test of the bid grid
component's interior.

Same arrangement as test_board_frontend.py, and for the same reason: AppTest
never renders a custom component's iframe, so the three-level header, the
blank-vs-zero rule and the patch-in-place that keeps the caret where it was
are invisible to the rest of the suite.

Skipped when node or jsdom isn't installed, so the Python-only workflow
stays intact:

    cd tests/frontend && npm install
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_TESTS = ROOT / "tests" / "frontend"
CHECKS = FRONTEND_TESTS / "bid_grid_checks.js"
COMPONENT = ROOT / "components" / "bid_grid"


def _node():
    return shutil.which("node")


def _jsdom_installed():
    return (FRONTEND_TESTS / "node_modules" / "jsdom").is_dir()


@pytest.fixture(scope="module")
def result():
    return subprocess.run(
        [_node(), str(CHECKS)],
        cwd=str(FRONTEND_TESTS),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(
    not _node() or not _jsdom_installed(),
    reason="needs node and jsdom — run `npm install` in tests/frontend",
)
class TestBidGridComponent:
    def test_every_check_passes(self, result):
        failed = [ln for ln in result.stdout.splitlines() if ln.startswith("FAIL")]
        assert result.returncode == 0, "\n".join(
            failed + [result.stdout[-2000:], result.stderr[-2000:]]
        )
        assert "ALL CHECKS PASSED" in result.stdout

    def test_it_actually_ran_a_meaningful_number_of_them(self, result):
        # A harness that silently stops early would otherwise pass above.
        assert len([ln for ln in result.stdout.splitlines() if ln.startswith("PASS")]) >= 30


class TestTheEventContractMatchesPython:
    """The event names the component emits, against the ones
    ui.scheduling.bidgrid.handle_event knows how to apply. Nothing enforces
    this across the language boundary, so a rename on either side would
    otherwise just make the grid quietly stop responding."""

    def test_every_emitted_event_type_is_handled(self):
        js = (COMPONENT / "frontend" / "index.html").read_text(encoding="utf-8")
        emitted = set(re.findall(r'emit\(\{\s*type:\s*"([a-z_]+)"', js))
        assert emitted == {"mw", "price", "code", "split", "unsplit"}, emitted

        handler = (ROOT / "ui" / "scheduling" / "bidgrid.py").read_text(encoding="utf-8")
        for name in emitted:
            assert f'kind == "{name}"' in handler, f"bidgrid.py ignores {name!r}"

    def test_the_component_wrapper_passes_every_argument_the_frontend_reads(self):
        js = (COMPONENT / "frontend" / "index.html").read_text(encoding="utf-8")
        read = set(re.findall(r"args\.([a-z_]+)", js))
        wrapper = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        for name in read:
            assert f"{name}=" in wrapper, f"the frontend reads args.{name}, nothing sends it"

    def test_the_column_widths_python_sizes_the_modal_by_match_the_css(self):
        # ui.scheduling.bidgrid sizes the modal from these, and the grid is
        # drawn from the CSS — they're the same two numbers in two files, so
        # a change to one has to be a change to both.
        from ui.scheduling.bidgrid import DATA_COL_PX, INDEX_COL_PX

        css = (COMPONENT / "frontend" / "index.html").read_text(encoding="utf-8")
        assert f"col.ixc {{ width: {INDEX_COL_PX}px; }}" in css
        assert f"col.datac {{ width: {DATA_COL_PX}px; }}" in css
