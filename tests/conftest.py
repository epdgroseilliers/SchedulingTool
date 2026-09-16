"""Fixtures shared across the ui/ test tier."""

from pathlib import Path

import pytest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


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
