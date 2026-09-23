# Architecture

Three layers, one direction of dependency: `ui` depends on `domain` and
`data`; `domain` depends on `data`; `data` depends on neither. Nothing here
depends back on `app.py`.

```
app.py        Entry point, and the Add Trade page. Wires ui.* render
              functions together in page order. No business logic, no direct
              st.session_state access beyond what ui.session exposes.

pages/        Additional pages, Streamlit's multipage convention. Each is as
              thin as app.py.
  1_Scheduling_View.py   Phase 2: matching buys against sells for one flow
                         date. Wires ui.scheduling.* together.

domain/       Pure business logic — no Streamlit import anywhere in this
              package. Every function takes plain arguments and returns
              plain values, so it's unit-testable without AppTest.
  options.py    Option lists (COUNTERPARTIES, LOCATIONS, ...) and the
                constants/defaults tied to them.
  grid.py       The HE x Date grid: building it, and converting between
                its wide (data_editor) and long (date, he, mw) shapes.
  shapes.py     Shape parsing (HL/LL/ATC/custom range) and expanding a
                shape into actual per-date hours via the WECC calendar.
  trade.py      Trade-level rules: price formatting, the DAM/RT date
                default, and the validation/warnings the legacy macro ran
                before writing to the DB.
  matching.py   Phase 2: re-expanding a stored date-range + He row back
                down to one flow date's hours (the reverse of
                data.bilateral.compress_schedule), the buy/sell link model,
                and the open-position arithmetic over it.
  bidfiles.py   Phase 2: grouping a market's links by counterparty into
                SHORT/LONG bid lines, and validating a trader's split of
                one into more than one GCA/LCA. Market-agnostic; the
                per-market file format lives in data/bidfiles/.

ui/           Streamlit rendering, one module per page section. Each
              render_*() function returns what it collected (a dict, a
              tuple) rather than reaching into module globals — app.py
              threads the values through.
  session.py     Session-state init and the shared helpers: flash
                 messages, widget_defaults() (for widgets the paste-box
                 parser can fill), block/version key helpers.
  nav.py         The header both pages share: one button to the other page,
                 and the CSS that hides Streamlit's sidebar page list.
  paste.py       The broker-string paste box: calls data.trade_string,
                 fills the form's session_state keys on a clean parse.
  trade_fields.py  Trade Date/IsDAM, the economics row (counterparty/
                 location/index/price), the back-office row, and the
                 rarely-used "Other attributes" expander.
  schedule.py    The Schedule section: date-range blocks, Generate/Clear,
                 the data_editor grid.
  actions.py     The action row (Add block/Add Trade/Preview/Input in DB)
                 and what each button does — validates, then either
                 previews or writes a trade.
  preview.py     The "DB Insert Preview" panel.
  trades_list.py The Trades history list at the bottom of the page.
  scheduling/    The Scheduling View's own sections — a package rather than
                 one module because it's a second page, not another band of
                 the first.
    state.py       Its session state (links and hidden squares are
                   session-only) and assembling a flow date's legs from the
                   DB + this session's trades.
    filters.py     Flow date, source/PSE/POR-POD, Refresh, Restore hidden.
    board.py       The adapter for the trade_board component: legs+links ->
                   its JSON payload, and its events -> state changes.
    links.py       The schedule popup (create and edit are one modal) and
                   the detail strip under the board.
    bidfile.py     The downstream bid-file builder, opened by a plain click
                   on a market chip (dragging it still starts a link) —
                   SWPW only so far.
    widgets.py     The one shared bit of rendering between links.py and
                   bidfile.py: the one-row, HE1..HE24 hour editor.

data/         External state: the database and the WECC calendar. Nothing
              in this package is specific to how the page looks.
  db.py          SQLAlchemy engines (two servers: the app's own, and the
                 one holding PhysiqueBilateral).
  calendar.py    WECC on-/off-peak calendar, cached.
  bilateral.py   Everything that reads or writes
                 PhysiqueBilateral.west.BilateralTrades — schedule
                 compression, DB lookups, duplicate check, the insert
                 itself, compliance folder creation.
  trade_string.py  The broker-string parser (deterministic, no LLM) —
                 pure and DB-free itself; ui.paste supplies it the option
                 lists and the full-name lookup.
  matching.py    The read side of BilateralTrades: what's on the book for
                 one flow date. Separate from bilateral.py's write side on
                 purpose — a read path has no business importing the insert.
  bidfiles/      Writing the desk's downstream market bid files — one
                 module per market, since each has its own real spreadsheet
                 format. swpw.py is the first; see PROJECT.md for what's
                 confirmed from the desk's own files versus still open.

components/   Streamlit custom components — the one place in the repo that
              owns its own DOM. Outside the ui/ split by nature: a component
              can't be a render_*() returning plain values, because the
              browser, not the script, drives it.
  trade_board/   The Scheduling View's canvas: movable squares, drag-to-link,
                 clickable link lines. One static index.html speaking the
                 component postMessage protocol — no npm build step. Knows
                 nothing about trades; ui.scheduling.board translates.
```

## Where new logic goes

- A new business rule or calculation with no UI dependency → `domain/`,
  and it should be directly callable from a plain script or test with no
  Streamlit runtime needed.
- A new widget, page section, or anything touching `st.session_state` →
  `ui/`, as a `render_*()` function returning what the rest of the page
  needs from it.
- A new external data source (another table, another calendar) → `data/`.

## Testing

`domain/` functions can be called directly — no Streamlit runtime needed.
`ui/` and the pages are exercised through
[`streamlit.testing.v1.AppTest`](https://docs.streamlit.io/develop/api-reference/app-testing),
which runs the real script and lets you set widget values and inspect
`session_state`.

`components/` needs a third approach, because AppTest never renders a custom
component's iframe. Two halves, split deliberately so as much as possible
falls on the testable side:

- Everything the component *could* decide but doesn't — what a square says,
  which ones are dimmed, when the board may rebuild — lives in
  `ui/scheduling/board.py` and is tested as plain functions.
- What only a browser can do is driven through jsdom by
  `tests/frontend/board_checks.js` (run by `tests/ui/test_board_frontend.py`,
  skipped without node + jsdom).

A component's value lands in `session_state` under its key like any
widget's, so `at.session_state["mv_board"] = {...}` is exactly what the real
frontend does when a trader drags something — which is how the page's
event handling is tested end to end.
