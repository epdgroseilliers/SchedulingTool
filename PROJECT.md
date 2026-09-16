# Project context

This file is project/product context: what this tool is for, what exists
today, and where it's headed. **ARCHITECTURE.md** is the companion piece —
the code map (module layout, dependency direction, where new logic goes).
Read both before starting new work; this one for *why* and *what's next*,
that one for *where*.

## What this is

A Streamlit trade-scheduling tool for MAG Energy Solutions' west desk. It
replaces a legacy Excel/VBA macro (`InputTrades` / `importTradeSQL`) that
traders used to hand-enter bilateral power trades into a SQL Server
back office database. Single page today (`app.py`), one trader's session at
a time, no auth model beyond whatever the deployment environment provides.

## Phase 1 — Add Trade (done)

The current, working scope: enter a bilateral trade, build its hourly
schedule, and write it to the back office database.

### What a trader does

1. Set **Trade Date** and **IsDAM** (day-ahead vs. real-time) — this is
   context set *before* anything else, because IsDAM drives the schedule's
   default flow date and everything downstream reads it.
2. Either **paste a broker string** from ICE Chat (`APS SELLS/MAG BUYS
   100 MWS HE18-HE21 PV FIXED $73 flow 9/15 wspp sched c`) and let it fill
   the form, or fill the form by hand. The paste box sits beside Trade
   Date/IsDAM for exactly this reason — it's the fast path, they're the
   slow path's context.
3. Review/adjust the economics row (counterparty, location, index, price)
   and the back-office fields (communication, specified source, WSPP
   contract type, IsNWS, IsSourceNonCaiso) — one compact row, since the
   parser fills these correctly the large majority of the time and this is
   now a glance-and-confirm surface, not primary entry.
4. Confirm the **Schedule** — one or more date-range blocks, each with a
   Shape (`HL`/`LL`/`ATC`/a custom hour range) and MW. A block
   auto-populates its grid the first time it's shown (no explicit
   "Generate" click needed for the default case); the grid is still
   hand-editable afterward for irregular shapes.
5. **Add Trade** — validates, and optionally (**Input in DB** checked)
   writes to `PhysiqueBilateral.west.BilateralTrades` on the
   `MAGAPPSERVER\SQLSERVER` box (a different server from the app's own
   `data.db.get_engine()`, which is why `get_bilateral_engine()` exists
   separately). **Preview DB Insert** runs the identical pipeline —
   lookups, duplicate check — stopping just short of the actual write, so
   what's previewed is exactly what a real submit would attempt.

### The broker-string parser

`data/trade_string.py` is a **deterministic parser, not a model** — regex
extractors that each scan the whole string and claim their span, so field
order never matters. This was a deliberate choice over an LLM: the same
string must always produce the same trade, it needs to be unit-testable
against real broker text, and a wrong guess here writes a bad row into a
back office database. Handles: which side MAG is on (explicit `MAG BUYS`, or
the mirrored verb when only the counterparty is named), counterparty
resolution through alias/exact/full-name/fuzzy matching, MW, shape/hour
range, four price forms (`FIXED $N`, bare `$N`, `for <node>±N`, `@ index
±N` — which derives the node from the location), specified source
(including `ACS` → the counterparty's Asset Controlling Supplier),
`nws`/`ncs` flags, and flow dates — numeric (`flow 9/15`, with or without
the word "date"), a range, or a bare weekday (`Mon only` → the nearest
occurrence of that weekday **on or after the Trade Date**, not the real
wall-clock date — a trader back-entering a past trade needs relative dates
to resolve against the trade they're entering, not today).

A parse either fully succeeds (fills the whole form) or fully fails (fills
nothing, shows the specific errors) — never partial. Fuzzy matches are
filled but flagged as "worth checking," never presented as certain.

### Data model note

The DB doesn't store a schedule per hour — one `BilateralTrades` row is a
date range + a single He value (`'HL'`, `'LL'`, `'ATC'`, or an explicit
range like `'7-9,11-22'`) + one MW. `data/bilateral.py`'s
`compress_schedule()` folds the app's per-hour grid into the minimum number
of such rows, per block, matching how a trader would key it into the old
spreadsheet — the block's own Shape names the He value (`'HL'` rather than
`'7-22'`) when the hours match exactly what that shape produces; a
hand-edit that breaks the pattern falls back to an explicit range.

### Known gaps / things to keep in mind

- **Option lists are seeded, not exhaustive.** `domain/options.py`'s
  `COUNTERPARTIES`/`LOCATIONS`/etc. and `data/trade_string.py`'s alias
  tables (`COUNTERPARTY_ALIASES`, `LOCATION_ALIASES`, `ACS_SOURCES`, ...)
  grow as real desk shorthand turns up. An unrecognized token is a reported
  error, never a silent guess.
- **A committed `tests/` suite now exists** (202 tests: `domain/`, `data/`
  pure and `db`-marked, `ui/` via `AppTest`) — see `tests/README.md`. Run
  `pytest` for the fast, network-independent tier, `pytest --run-db` to
  include the ones hitting a live DB. Never call `insert_trade()` for real
  in a test — see the README's Safety section and the `no_real_db_writes`
  fixture in `tests/conftest.py`. Keep growing this alongside Phase 2 rather
  than letting it fall back to ad-hoc scratch scripts.
- **Two SQL Server connections exist on purpose** (`data/db.py`):
  `get_engine()` for the app's own server, `get_bilateral_engine()` for the
  one holding `PhysiqueBilateral`. Don't collapse these — they're genuinely
  different boxes.

## Phase 2 — the Scheduling View (next, not yet started)

### The idea

A new page, called the **Scheduling View**: match buy legs against sell
legs for a single flow date at a time. Likely a natural fit for Streamlit's
multi-page app support (a `pages/` directory) rather than folding into the
single-page `app.py` — Phase 1's page stays as the trade entry point, this
is a separate view over the trades that already exist. Visually clear and
interactive is the explicit design goal — a trader's book at a glance, not
another data-entry form.

### Layout

- Shows **one flow date at a time.**
- **Filters**: Flow date, **PSE** (counterparty or market — both live in
  `BilateralMarket` already, e.g. `CAISO` sits in that table alongside real
  counterparties, so "PSE" is the natural umbrella term for the whole
  column, matching real WECC/NERC usage), and POR/POD. Filters narrow which
  trades are shown, not the underlying data.
- **Buys on the left, sells on the right. Open positions at the top.**

### The trade square

Each trade renders as a colored square showing its main facts at a
glance: counterparty, hours, total MWh, POR/POD.

**Granularity: per flow date, not per trade.** A single Phase 1
`BilateralTrades` row can span several flow dates in one date range. Since
this view shows one date at a time, a multi-day trade shows a **distinct
square on each flow date it touches**, with *that day's* specific
hour-shape and MWh — re-expanding the compressed DB row back to daily
numbers for display, the reverse of what `compress_schedule()` does going
in. This resolves the open question Phase 1 left about matching grain:
it's **per trade, per flow date**, not per whole trade and not aggregated
across counterparties/locations.

### Linking

- Links render as **thick lines between buy and sell squares.**
- **Many-to-many**: one buy can link to several sells and vice versa. The
  data model needs to represent that natively — a join/link table, not a
  foreign key on either square.
- **Creating a link**: hovering beside a square shows a **`+` button**;
  clicking it starts a new link that can be **dragged** to another square
  to complete it.
- **On completing a link** ("when a link is coupled"), a **popup suggests
  the schedule to allocate** to that link and lets the trader **overwrite
  it**. The suggestion algorithm itself is undecided — proportional split
  across a square's open links, full overlap of both schedules' hours,
  first-available-hours, or something else — this needs its own design
  pass once the rest of the view exists to design against.
- **Clicking a square** highlights it together with all its links, and
  drills down to show that trade's exact schedule *and* the schedule
  portion of each link.

### Market squares

A market (CAISO, SPP, AESO, ...) gets its own square on whichever side
it's used — buy side, sell side, or both — **the moment a trader links an
open position to it**, and is hidden otherwise. A market is a **placeholder
/ sink node**, not backed by a Phase 1 `BilateralTrades` row the way a real
counterparty's square is — it exists to represent exposure bought from or
sold into the spot market, created purely by the act of linking to it, not
by any pre-existing trade.

**The exact market list** As given: CAISO, SWPW,
SWPP, AESO, CEN. 

### Decided so far

- **Persistence: session-only for now.** Links don't need to survive
  across sessions yet — mirrors how Phase 1 itself started with
  `st.session_state.trades` before `BilateralTrades` writes existed. Revisit
  once the interaction model is validated and it's clear what a link
  actually needs to record.
- **Square granularity: per trade, per flow date** (see above) — resolves
  the grain question Phase 1's version of this doc left open.

### Still open

- **Canvas implementation.** Drag-to-link, a hover-triggered `+` button,
  and dynamic connecting lines aren't things plain Streamlit widgets do.
  Decided: **prototype both** before committing —
  (a) a **custom component** (e.g. wrapping React Flow, Cytoscape.js, or
  similar, embedded via `streamlit.components.v1`) for the literal
  drag/hover/animated-line experience, at the cost of a separate
  engineering track from the rest of the app, or
  (b) a **native Streamlit approximation** — click-select two squares and
  press "Link" instead of dragging, expand a drill-down inline instead of
  a true highlight-and-focus — faster to build, staying in the existing
  stack, but diverging from the drag/hover spec as described.
  Next step when Phase 2 starts: small spikes of both, then decide.
- **The link-schedule suggestion algorithm** (see Linking, above).
- **The exact market list and tickers** (see Market squares, above).
- **MW/hour reconciliation semantics**: does a match need its linked
  quantities to net to zero (a buy fully covered by its linked sells), or
  is partial/open linking a normal, expected state alongside the "open
  positions" row at the top?
- **Source of candidate buys/sells**: trades already written to
  `BilateralTrades`, trades in the running session's local list (Phase 1's
  `st.session_state.trades`), or both? Session-only link persistence
  doesn't by itself resolve this — it's about where the *trades* being
  matched come from, not where the *links* are kept.

### What Phase 1 already provides to build on

- `domain/trade.py`, `domain/grid.py` — the schedule/shape logic the
  per-flow-date re-expansion above will need to read (not duplicate).
- `data/bilateral.py` — the `BilateralTrades` table access; a new module
  alongside it (e.g. `data/matching.py`) is the natural home for whatever
  Phase 2's own DB access turns out to be, keeping the `data/` package's
  existing "external state only" boundary from ARCHITECTURE.md.
- The `ui/` split (one module per page section, `render_*()` returning
  plain values) is a pattern worth carrying into Phase 2's page rather than
  inventing a new convention — unless the custom-component path is chosen,
  in which case the component itself sits outside that split entirely.
