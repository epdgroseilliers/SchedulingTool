# Tests

```
pip install -r requirements-dev.txt
pytest                  # fast tier — no live DB required
pytest --run-db         # also run tests marked `db` (live, read-only)
```

202 tests total: 187 run with no network dependency at all beyond what
rendering the app already needs (see below); 15 more are marked `db` and
skipped unless `--run-db` is passed.

## Layout

```
tests/
  conftest.py              no_real_db_writes fixture (see Safety, below)
  domain/                  pure logic — no Streamlit, no DB, instant
    test_options.py
    test_grid.py
    test_shapes.py           parse_shape/build_schedule(flat|custom) are pure;
                              build_schedule(on_peak|off_peak) is marked db
    test_trade.py
  data/
    test_trade_string.py    the broker-string parser — pure, the most
                             heavily-exercised code in the app
    test_bilateral.py       pure: He-string collapsing, schedule compression,
                             price/int formatting, generated-SQL shape
                             (checked against a fake connection)
    test_bilateral_db.py    marked db: live lookups against MAGAPPSERVER
                             (resolve_and_validate, load_market_full_names)
  ui/                       exercised through streamlit.testing.v1.AppTest —
                             runs the real app.py script
    test_layout.py           page structure: entry row, economics row order
    test_schedule_ui.py      auto-populate, IsDAM date-sync guards,
                              Generate/Clear, block add/remove
    test_paste_ui.py         paste -> fill -> build schedule, rejection,
                              trade-date anchoring, WSPP forms
    test_actions_ui.py       local Add Trade, Preview DB Insert (db),
                              past-date gate, the real-insert path (mocked)
```

`conftest.py` at the **repo root** (not under `tests/`) exists so the repo
root is on `sys.path` before any test imports `app.py` or a `ui`/`domain`/
`data` module, however deep the test file sits — and to register the `db`
marker's opt-in skip.

## The `db` marker isn't the whole story

Opting out of `--run-db` does **not** make the `ui/` tier fully
network-independent. Rendering the Schedule section reaches the **app's own**
WECC calendar SQL Server unconditionally — HL is a new block's default
Shape, and a block auto-populates on first render — so nearly every `ui/`
test already depends on that connection working, the same as running the
app itself does. The `db` marker specifically covers the **bilateral**
server (`MAGAPPSERVER`, `PhysiqueBilateral`): Preview DB Insert, duplicate
detection, and the direct `data.bilateral` lookup tests. If neither DB is
reachable, only `tests/domain` (minus the one `db`-marked class) and
`tests/data/test_trade_string.py` / `test_bilateral.py` will pass.

## Safety: nothing here writes to the compliance database

`insert_trade()` performs a real, committed `INSERT` into
`PhysiqueBilateral.west.BilateralTrades` — a production-adjacent compliance
table, not a sandbox. **No test in this suite calls it for real.**

- Local-only trades (`Input in DB` unticked) never reach it — the
  submit path doesn't touch the bilateral DB at all in that case.
- **Preview DB Insert** is safe by construction: `ui.actions.handle_submit`
  returns before `insert_trade()` is ever called on that path (it only runs
  the read-only lookups and duplicate check).
- The one test that exercises a *real* submit with `Input in DB` ticked
  (`test_actions_ui.py::TestRealInsertPathIsMocked`) uses the
  `no_real_db_writes` fixture, which monkeypatches `ui.actions.insert_trade`
  and `ui.actions.create_compliance_folders` to recording fakes — so it
  verifies the wiring (flash messages, `db_trade_ids`, folder-creation
  calls) without ever opening a socket or touching the UNC compliance
  folders.

If you add a test that clicks **Add Trade** with **Input in DB** ticked,
request the `no_real_db_writes` fixture. There is no other sanctioned way
to exercise that path in this suite.
