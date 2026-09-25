# Tests

```
pip install -r requirements-dev.txt
cd tests/frontend && npm install && cd ../..   # optional, see below
pytest                  # fast tier — no live DB required
pytest --run-db         # also run tests marked `db` (live, read-only)
```

601 tests total: all but 16 run with no network dependency beyond what
rendering the app already needs (see below); those 16 are marked `db` and
skipped unless `--run-db` is passed. Six more need node + jsdom and skip
cleanly without them.

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
    test_matching.py        Phase 2: He re-expansion, legs, links, open
                             positions, market squares — the flow date's peak
                             flag is passed in, so this needs no calendar
    test_bidfiles.py        Phase 2: grouping a market's links by
                             counterparty into SHORT/LONG, and validating a
                             trader's split of one — pure, no filesystem
  data/
    test_trade_string.py    the broker-string parser — pure, the most
                             heavily-exercised code in the app
    test_bilateral.py       pure: He-string collapsing, schedule compression,
                             price/int formatting, generated-SQL shape
                             (checked against a fake connection)
    test_bilateral_db.py    marked db: live lookups against MAGAPPSERVER
                             (resolve_and_validate, load_market_full_names)
    test_matching_reads.py  the flow-date book query: row conversion, SQL
                             shape, degrade-to-empty — plus one db-marked
                             read against the live table. Named _reads
                             because tests/ has no package __init__ files, so
                             a second test_matching.py would collide with the
                             domain one.
    test_swpw_bid.py        the SWPW bid file writer — filename convention,
                             workbook structure, the overwrite guard. Every
                             save goes to tmp_path via monkeypatch; never Z:\
  ui/                       exercised through streamlit.testing.v1.AppTest —
                             runs the real app.py script
    test_layout.py           page structure: entry row, economics row order
    test_schedule_ui.py      auto-populate, IsDAM date-sync guards,
                              Generate/Clear, block add/remove, that a
                              hand-typed value sticks on the *first* try
                              (the editor's seed must not move under it),
                              and the Paste MW popover
    test_paste_ui.py         paste -> fill -> build schedule, rejection,
                              trade-date anchoring, WSPP forms
    test_clear_button.py     the Clear button and the post-Add-Trade reset
    test_actions_ui.py       local Add Trade, Preview DB Insert (db),
                              past-date gate, the real-insert path (mocked)
    test_scheduling_view.py  the Phase 2 page end to end: filters, the flow
                              date surviving a trip to the other page, board
                              events, the schedule popup, market chips. Runs
                              pages/1_Scheduling_View.py with the bilateral
                              DB stubbed to an empty book, so the board is
                              driven entirely by trades seeded into
                              session_state.trades
    test_board_payload.py    what gets handed to the board component — pure
                              functions, checked directly
    test_board_frontend.py   runs tests/frontend/board_checks.js (below), and
                              checks the Python/JS event contract still lines
                              up on both sides
    test_nav.py              the shared header: a button to the other page on
                              each, and the sidebar page list suppressed
    test_bidfile.py          the SWPW bid-file dialog end to end: opening it
                              from a chip click, filling in and generating,
                              the overwrite-confirm gate, splitting, and that
                              it sees a position regardless of filters/hides.
                              Every write redirected to tmp_path; never Z:\
    test_bidgrid.py          what the bid grid component is handed
                              (build_payload, the index column, the modal's
                              width) and what it makes of each event
                              (rebalancing a split, dropping one, the replay
                              guard)
    test_multiday_links.py   a trade spanning several flow dates: one link
                              per day, created on every day both trades
                              flow *later in the session being traded* —
                              never a day before the one linked on — and
                              independent afterwards: editing or deleting
                              one day leaves the others alone, and the
                              bid-file builder starts each day from its own
                              position. The session horizon is pinned by a
                              fixture: what it covers otherwise depends on
                              the weekday the suite runs
    test_bidgrid_frontend.py runs tests/frontend/bid_grid_checks.js, and
                              checks the Python/JS event contract — plus that
                              the column widths Python sizes the modal by
                              still match the component's own CSS
  frontend/
    board_checks.js         the trade board component driven in jsdom: real
                             pointer gestures against the real index.html
    bid_grid_checks.js      the bid grid component driven in jsdom: its
                             three-level header, the blank-vs-zero rule, and
                             the patch-in-place that keeps the caret where
                             the trader left it
    package.json            its one dependency (jsdom)
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
detection, and the direct `data.bilateral` / `data.matching` lookup tests. If
neither DB is reachable, only `tests/domain` (minus the one `db`-marked
class), `tests/data/test_trade_string.py`, `test_bilateral.py`,
`test_matching_reads.py`, `test_swpw_bid.py`, `tests/ui/test_board_payload.py`
and `tests/ui/test_board_frontend.py` will pass.

The Scheduling View's tests stub the **bilateral** read
(`ui.scheduling.state.load_trades_for_flow_date`) rather than carrying the
`db` marker, so the page's own behaviour is tested against a book the test
controls — but rendering it still reaches the WECC calendar for the flow
date's peak flag, like the rest of the `ui/` tier.

## Testing a custom component

AppTest never renders a custom component's iframe, so each of the two — the
trade board and the bid grid — is covered from two sides. The board is the
example below; the grid works exactly the same way, through `mv_bidgrid`
events and `tests/frontend/bid_grid_checks.js`. They have separate
`seq`/`instance` watermarks because both are live at once: the bid-file
dialog opens over the board.

- **Its events, through the real page.** A component's value lands in
  `session_state` under its key like any widget's, so
  `at.session_state["mv_board"] = {"type": "link_request", ...}` is exactly
  what the frontend does when a trader drags something. That makes the whole
  loop — drag, popup, link, open position — testable in
  `test_scheduling_view.py`. Every event needs a `seq` higher than the last
  one applied, or the page will (correctly) ignore it as a replay; the
  `_emit` helper there handles that. An event from a *different* frame
  carries a different `instance` id and resets that watermark — which is
  what keeps a page navigation (which rebuilds the iframe and restarts its
  counter) from silently swallowing the next several interactions.
- **Its interior, in jsdom.** `tests/frontend/board_checks.js` loads the real
  `index.html`, stubs the Streamlit host, and fires actual pointer events to
  check dragging, drop targeting, the same-side refusal, and the `revision`
  guard that stops a rebuild snapping a dragged square back. Run it directly
  with `node tests/frontend/board_checks.js`, or through pytest once
  `npm install` has been run in `tests/frontend`.

Prefer moving a decision *out* of the component when you can — anything
computed in `ui/scheduling/board.py` or `bidgrid.py` gets ordinary Python
tests, and only what genuinely needs a browser has to go through jsdom. The
rebalancing a typed MW causes is the clearest case: it lives in
`domain.bidfiles`, so the grid only has to report the cell that changed.

Both `_checks.js` files print `PASS`/`FAIL` per check and end with
`ALL CHECKS PASSED`; the pytest wrappers assert on that *and* on a minimum
number of PASS lines, so a harness that silently stops early still fails.

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
