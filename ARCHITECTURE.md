# Architecture

Three layers, one direction of dependency: `ui` depends on `domain` and
`data`; `domain` depends on `data`; `data` depends on neither. Nothing here
depends back on `app.py`.

```
app.py        Page entry point. Wires ui.* render functions together in
              page order. No business logic, no direct st.session_state
              access beyond what ui.session exposes.

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

ui/           Streamlit rendering, one module per page section. Each
              render_*() function returns what it collected (a dict, a
              tuple) rather than reaching into module globals — app.py
              threads the values through.
  session.py     Session-state init and the shared helpers: flash
                 messages, widget_defaults() (for widgets the paste-box
                 parser can fill), block/version key helpers.
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
`ui/` and `app.py` are exercised through
[`streamlit.testing.v1.AppTest`](https://docs.streamlit.io/develop/api-reference/app-testing),
which runs the real script and lets you set widget values and inspect
`session_state`.
