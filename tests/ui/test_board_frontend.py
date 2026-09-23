"""Runs tests/frontend/board_checks.js — the only test of the trade board
component's interior.

AppTest never renders a custom component's iframe, so everything the
component decides for itself (dragging, which square a drop landed on, the
rebuild guard that keeps a dragged square where it was put) is invisible to
the rest of the suite. That's checked here instead, by driving the real
index.html in jsdom.

Skipped when node or jsdom isn't installed, so the Python-only workflow
stays intact:

    cd tests/frontend && npm install
"""

import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND_TESTS = Path(__file__).resolve().parents[1] / "frontend"
CHECKS = FRONTEND_TESTS / "board_checks.js"


def _node():
    return shutil.which("node")


def _jsdom_installed():
    return (FRONTEND_TESTS / "node_modules" / "jsdom").is_dir()


pytestmark = pytest.mark.skipif(
    not _node() or not _jsdom_installed(),
    reason="needs node and jsdom — run `npm install` in tests/frontend",
)


@pytest.fixture(scope="module")
def result():
    return subprocess.run(
        [_node(), str(CHECKS)],
        cwd=str(FRONTEND_TESTS),
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestBoardComponent:
    def test_every_check_passes(self, result):
        failed = [ln for ln in result.stdout.splitlines() if ln.startswith("FAIL")]
        assert result.returncode == 0, "\n".join(
            failed + [result.stdout[-2000:], result.stderr[-2000:]]
        )
        assert "ALL CHECKS PASSED" in result.stdout

    def test_it_actually_ran_a_meaningful_number_of_them(self, result):
        # A harness that silently stops early would otherwise pass above.
        assert len([ln for ln in result.stdout.splitlines() if ln.startswith("PASS")]) >= 25


class TestTheEventContractMatchesPython:
    """The event names the component emits, against the ones
    ui.scheduling.board.handle_event knows how to apply. Nothing enforces
    this across the language boundary, so a rename on either side would
    otherwise just make the board quietly stop responding."""

    def test_every_emitted_event_type_is_handled(self):
        import re

        js = (
            Path(__file__).resolve().parents[2]
            / "components" / "trade_board" / "frontend" / "index.html"
        ).read_text(encoding="utf-8")
        emitted = set(re.findall(r'emit\(\{\s*type:\s*"([a-z_]+)"', js))
        assert emitted == {
            "select",
            "move",
            "link_request",
            "link_click",
            "dismiss",
            "chip_click",
        }, emitted

        handler = (
            Path(__file__).resolve().parents[2] / "ui" / "scheduling" / "board.py"
        ).read_text(encoding="utf-8")
        for name in emitted:
            assert f'kind == "{name}"' in handler, f"board.py ignores {name!r}"

    def test_the_component_wrapper_passes_every_argument_the_frontend_reads(self):
        import re

        js = (
            Path(__file__).resolve().parents[2]
            / "components" / "trade_board" / "frontend" / "index.html"
        ).read_text(encoding="utf-8")
        read = set(re.findall(r"args\.([a-z_]+)", js))
        wrapper = (
            Path(__file__).resolve().parents[2]
            / "components" / "trade_board" / "__init__.py"
        ).read_text(encoding="utf-8")
        for name in read:
            assert f"{name}=" in wrapper, f"the frontend reads args.{name}, nothing sends it"
