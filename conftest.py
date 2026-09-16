"""Root conftest — loading it here (not under tests/) guarantees the repo
root is on sys.path before any test imports app.py or a `ui`/`domain`/`data`
module, however deep the test file sits.

Also defines the `db` marker's opt-in skip: tests hitting a live database —
the app's own SQL Server for the WECC calendar, or MAGAPPSERVER for
bilateral trades — are skipped unless --run-db is passed, so `pytest` alone
stays fast and network-independent.

This does NOT make the `ui/` test tier as a whole DB-free: rendering the
Schedule section calls into the WECC calendar unconditionally (HL is the
default Shape for a new block), so most ui/ tests assume that connection
works, the same as running the app does — only the specifically
bilateral-DB-dependent ui/ tests (Preview DB Insert, duplicate detection)
carry the `db` marker. See tests/README.md.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-db",
        action="store_true",
        default=False,
        help="Also run tests marked 'db' (live, read-only DB queries).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-db"):
        return
    skip_db = pytest.mark.skip(reason="needs --run-db (live bilateral SQL Server connection)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
