"""Fixtures shared across the ui/ test tier."""

from pathlib import Path

import pytest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def never_the_real_tag_folder(tmp_path):
    """Point the tag writer at a temp folder for **every** test.

    `data.tagfiles.bilateral.TEST_MODE` used to be what kept generated tags
    off `Y:\\West\\TAG Bilateral WEST` — it sent them all to one Test
    subfolder. It is off now that the desk wants its files in the session's
    own folder, which leaves nothing between a test that forgets to
    redirect and a real tag appearing on the share for schedulers to pick
    up. So the redirect is no longer something a test has to remember.

    Tests that assert *where* a tag landed still take their own
    `redirect_tags` fixture; this only makes sure the ones that don't can't
    reach the share either.

    Deliberately not through `monkeypatch`: requesting it here creates it
    before a module's own autouse fixtures, which reverses the order their
    teardowns run in. tests/data/test_markets.py clears a Streamlit cache
    on the way out and found a plain fake still patched over it, failing on
    `.clear()` — a fixture meant to be invisible should not be able to do
    that to another module.
    """
    import data.tagfiles.bilateral as tagfile

    original = tagfile.TAG_FOLDER
    tagfile.TAG_FOLDER = tmp_path / "tags-under-test"
    yield
    tagfile.TAG_FOLDER = original


@pytest.fixture
def no_real_db_writes(monkeypatch):
    """Replace the two functions that touch the outside world on a real
    submit — insert_trade (a live INSERT) and create_compliance_folders (a
    real UNC filesystem write) — with recording fakes.

    Required by any test that clicks "Add Trade" with "Input in DB" ticked.
    Never call the real insert_trade in a test: it's a committed write to a
    production-adjacent compliance database, not something a test suite
    should be able to do by accident. Preview DB Insert doesn't need this —
    it stops before insert_trade() is ever called (see ui.actions.handle_submit).
    """
    calls = {"insert_trade": [], "create_compliance_folders": []}

    def fake_insert_trade(rows):
        calls["insert_trade"].append(rows)
        return list(range(90001, 90001 + len(rows)))

    def fake_create_compliance_folders(trade, trade_ids):
        calls["create_compliance_folders"].append((trade, trade_ids))
        return [], [], []  # created, existing, failed — no filesystem touched

    monkeypatch.setattr("ui.actions.insert_trade", fake_insert_trade)
    monkeypatch.setattr("ui.actions.create_compliance_folders", fake_create_compliance_folders)
    return calls
