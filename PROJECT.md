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
   context set *before* anything else, because together they decide the
   schedule's default flow dates and everything downstream reads them. A
   real-time trade flows the day it's traded; a day-ahead one takes the
   flow dates the **WECC calendar itself pairs with that trade date**
   (`TradeDate` in `WECC_PowerCalendar_Detailed` — one trading session,
   usually one day, but a Thursday or Friday can cover the weekend). That
   pairing is read, never inferred: an earlier version extended the end
   date through runs of matching peak/off-peak days and put every Monday-
   to-Wednesday trade's end on the following Saturday. **A trade date the
   calendar has no session for at all** — a weekend, a holiday — has no
   day-ahead market to trade in, so it can only be a real-time trade:
   IsDAM unticks itself and is disabled, and the flow date is the trade
   date. It's given back the moment the trade date is a trading day again,
   so a mistyped weekend date can't silently leave the next trade RT — but
   a real-time choice the trader made themselves is never overridden.
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
   hand-editable afterward for irregular shapes. For a genuinely variable
   shape there is **Paste MW**: a column of 24 values copied straight out
   of Excel (or 24 per date, for the dates in order), which beats typing
   24 cells. It's all-or-nothing like the broker string — a count that
   doesn't match is refused rather than guessed at, since 16 values could
   be HE7-22 or HE1-16 and guessing wrong moves the energy to the wrong
   hours.
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
(including `ACS` → the counterparty's Asset Controlling Supplier, and `SS`
→ whichever specified source that counterparty means by it, Seattle City
Light's being Boundary Dam Hydro — `SS` names no plant of its own, so an
unmapped counterparty warns and leaves the field to the trader, where an
unmapped `ACS`, which *is* a claim about one, is an error), `nws`/`ncs`
flags, and flow dates — numeric (`flow 9/15`, with or without
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

- **A block's editor must be seeded with a frame that doesn't move while
  the trader types.** `st.data_editor` builds its element id from a hash of
  the data it is handed, not from `key` alone, so feeding the previous
  run's *edited* frame back in renames the widget after every accepted edit
  and the next one — addressed to the old name — is dropped. That was the
  "I have to type every value twice" bug: it skipped every other entry, not
  every one, because the seed only moved once an edit had landed. The seed
  and the edited result are now separate session keys (`block_grid_*` and
  `block_edits_*`); see `ui.schedule.get_block_grid`.
- **Option lists are seeded, not exhaustive.** `domain/options.py`'s
  `COUNTERPARTIES`/`LOCATIONS`/etc. and `data/trade_string.py`'s alias
  tables (`COUNTERPARTY_ALIASES`, `LOCATION_ALIASES`, `ACS_SOURCES`, `SS_SOURCES`, ...)
  grow as real desk shorthand turns up. An unrecognized token is a reported
  error, never a silent guess.
- **A committed `tests/` suite now exists** (500+ tests: `domain/`, `data/`
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

## Phase 2 — the Scheduling View (first version built)

Built, at `pages/1_Scheduling_View.py` plus the `components/trade_board/`
custom component. **The canvas question is settled: the custom component
won.** A native-Streamlit version was built first and rejected on use — it
was too tall to fit a screen, its squares were too big, and select-two-
squares-and-press-a-button was too much friction for a tool whose whole
value is being faster than the alternative. Those aren't things a native
version could have been tuned out of; they're what it *is*. See *How it
works now*, below, after the design it was built from.

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

### How it works now

**Where the code sits.** `domain/matching.py` (pure: re-expansion, links,
open-position arithmetic), `data/matching.py` (the flow date's book),
`components/trade_board/` (the canvas) and `components/bid_grid/` (the
bid-file builder's table), `ui/scheduling/` (the adapters between them), and
`pages/1_Scheduling_View.py` wiring it together. Adding `pages/` makes this
a Streamlit multipage app; `app.py` stays the entry script and the Add Trade
page.

**The canvas is a custom component**, `components/trade_board/` — one static
`index.html` speaking Streamlit's component postMessage protocol directly.
**No npm build step and no React**, deliberately: the whole thing stays in
this repo, editable in a text editor, and installable with nothing but
`pip install -r requirements.txt`. A build toolchain would have bought
ergonomics this doesn't need and cost a second engineering track, which was
the main argument *against* going this way in the first place.

The component owns the DOM and nothing else — it's handed squares, links and
markets as plain JSON and has no idea what a leg or a WECC calendar is.
Everything it could decide but shouldn't (what a square says, which are
dimmed, when the board may rebuild) is computed in `ui/scheduling/board.py`,
so it stays testable.

The bid-file builder's grid (`components/bid_grid/`, added later) follows
the same pattern for the same reasons — and it's worth noting the rule that
decided it both times: reach for a component only where Streamlit genuinely
can't express the thing, and hand it as little judgement as possible.

**Interaction.** Squares are **draggable** anywhere on the canvas and stay
where they're put. Hovering one shows a **`+`**; dragging that onto a square
on the other side — or onto a **market chip** on the bottom rail — opens the
schedule popup. A link can equally be **started from a market chip** and
dragged onto a square, since a market is as good a place to begin from as a
trade. **Clicking a link line** reopens the same popup to edit or delete it.
Clicking a square focuses it and dims everything not linked to it; clicking
it again undims. **`×` on a square clears it off the board** — see
*Hiding, not deleting*, below.

**The flow date is remembered for the session.** The page opens on whatever
date the trader was last looking at, and only falls back to tomorrow on the
first visit. It has to be held *outside* widget state to do that: Streamlit
discards a widget's state on any script run that doesn't render it, and a
step over to Add Trade is exactly such a run — so the widget key alone
snapped back to tomorrow on every return, however many days forward the
trader was working.

**Screen budget.** The whole page above the board is one row: flow date,
source/PSE/POR-POD filters, a refresh button, and the three numbers the desk
actually reads — **open buys, open sells, net**. The bought/sold totals were
removed as noise. An earlier version put the filters in a popover to save a
few more pixels and they were simply never found; a filter you have to go
looking for isn't a filter, so they're back on the surface with collapsed
labels. Squares are 150×46 px and the board **takes the page's full width and
sizes its height to the day**: the busier side's stack of squares, plus two
squares' worth of clear space above the market-chip rail so the links
running down into the chips stay readable. A three-trade day gets a
three-trade canvas rather than a screenful of empty grey.

**Navigation** is a single button in a shared header (`ui/nav.py`), on both
pages, pointing at the other one. Streamlit's own page list lives in the
sidebar, which made every switch cost a drawer-open first — so the sidebar,
its page list, *and* Streamlit's top bar are all hidden, and this header
becomes the actual top of the page. Hiding the top bar isn't cosmetic: it's
fixed-position and floats over the content, so trimming the container's top
padding alone slid the header underneath it.

**Filtering shows more, not less — a link is never split in two.** The PSE
and POR/POD filters narrow the board, but a leg that fails one is kept
anyway when it's directly linked to a leg that passes: filtering down to one
PSE must not hide the very counterparty a trader is actively tracking a link
against (filtering to AZPS alone still shows a CONC sale it's linked to).
Rescue is **one hop** — a leg kept only because of a link doesn't, in turn,
drag in its own other partners, or a filter in a well-connected book could
end up reconstructing the whole thing it was meant to narrow. Market chips
are never filtered out on their own account, rescued or not: they're the
rail, not squares, and a PSE filter naming a counterparty would otherwise
take away everywhere to park a position — but they're also not a hub to
*rescue through*: a real leg linked only to an always-visible market chip
isn't, on that account alone, pulled back in by some other filter.

**Clearing a square off the board (`×`) is not the same as filtering it
out**, and does *not* get rescued: it's a deliberate "get this off my view"
action (see *Hiding, not deleting*, below), so a link with a hidden end
simply isn't drawn until the square is restored. Only the PSE/POR-POD
filters get the "show more" treatment.

**The source filter (Database / This session) is not part of this.** It
decides which legs get *loaded* in the first place, before any link
bookkeeping happens, so a link spanning a DB trade and a still-unsaved
session trade could in principle be broken by unchecking one source.
Extending rescue there would mean always loading both sources regardless of
the checkboxes — a real question, not implemented, since nobody has hit it
yet and it costs an always-on DB round trip to fix speculatively.

**A multi-day trade gets one link per flow date, and the days are
independent.** A trade spanning 09/27 and 09/28 is a square on each day (see
*Granularity*, above), so it is a separate `Link` on each — carrying its own
`flow_date`, and filtered down to the day being viewed before anything
draws, totals or bids it. The link book itself spans the session.

Without that they were the *same* link: a leg's key carries no flow date
(`db:412` is the same key on every day that trade flows), so both days
resolved to one record with one `mw_by_hour`. Editing either day rewrote the
other, the second day's square read as already matched, and the second day's
bid file came up prefilled with the first day's lines.

**Confirming a link links every *later* day both trades flow**, since a
trader who has decided these two go together has decided it for the rest of
the overlap, and re-drawing the same link once per day is the busywork this
view exists to remove.

**Forward only, never backward.** A day earlier than the one in front of the
trader is a day they have already scheduled, and reaching back into it would
rewrite finished work — quietly, on a day not even on screen. So linking on
the last day a trade flows propagates nothing, which is right: there is no
later day left to cover. Each of those days gets its *own* suggestion rather than a copy of
the confirmed one — the days differ (an HL trade doesn't flow at all on an
off-peak day; a day may already be partly linked elsewhere) and a copied
allocation would over-commit the ones that differ. The dates are named in a
toast, because links appearing on days that aren't on screen — and that feed
those days' bid files — shouldn't be invisible.

**Propagation stops at the end of the session being traded**
(`domain.trade.session_horizon`): the last flow date of today's trading
session, from the same TradeDate→FlowDate pairing a block's default dates
come from. Beyond it nothing has been traded yet, so a link the app invented
out there is one with no position behind it. A deal running weeks out still
gets its link on the day it was drawn and on the rest of the session — just
not on the weeks past it.

The practical shape of that, since the sessions are short: traded on a
**Friday** (covering Saturday *and* Sunday, say), a link drawn on the
Saturday also lands on the Sunday. Traded on a **Monday**, whose session
covers only Tuesday, a link propagates nowhere at all — correctly, because
nothing beyond Tuesday has been traded. So propagation does real work
exactly on the multi-day sessions, which is where re-drawing the same link
by hand was the actual chore.

A day the market doesn't trade has no session of its own, so the most recent
one on or before it answers instead — on a Saturday, Friday's session is
still the live one. A calendar that can't be read at all yields no horizon
and propagation falls back to the trades' own range rather than refusing to
act, the same stance `domain.trade` takes on a session-less trade date;
`PROPAGATE_MAX_DAYS` is the backstop for that case alone.

**The bid-file builder is per flow date too.** Its splits are keyed by day
as well as by market and counterparty, and each day seeds from its own
position with blank codes and the side's default price. Nothing carries
across: the bid file is written per day, and a GCA agreed for one day is not
one agreed for the next.

Square positions and cleared squares are deliberately *not* per day — a leg
key is the same on every day it flows, so the board keeps its layout as the
trader steps through the dates.

**Hiding, not deleting.** `×` on a square (or *✕ Clear* on the focused
square's detail line — two routes, one findable and one fast) clears it off
the board for the session, and a *Restore N hidden* button brings them all
back. It deliberately does
**not** delete anything: `BilateralTrades` is a compliance table this view
only ever reads, and a session trade belongs to the Add Trade page's list —
neither is a scheduling view's to destroy. If real deletion is wanted, it
should be a deliberate, confirmed action on the page that owns the trade,
not a hover affordance on a board.

**Auto-layout appends.** A square with no stored position takes the first
*free* slot down its own side, skipping slots already occupied — so a trade
entered mid-session lands at the bottom of its side rather than underneath
an existing square. (Placing by count alone doesn't do this: squares that
already have a position aren't in the count, so a newcomer gets slot 0.)

**The suggestion algorithm is still a placeholder**, as flagged below: every
hour both sides still have open, at the smaller of the two remaining MW (and
for a market, the other side's whole open position, since a sink has no
schedule to overlap with). It's the variant that can't over-commit either
side, which makes it a safe thing to argue *from* rather than an answer.

**Deliberately not enforced:** over-allocation is possible, because the
trader can overwrite any suggestion. It's flagged on the square's tooltip
rather than clamped — a first answer to the reconciliation question below,
and the easiest one to reverse.

**Three things to know before editing the component.**

- `revision` is the redraw switch. The component rebuilds only when it
  changes, which is what keeps a dragged square where the trader put it
  through the reruns their own dragging causes. Anything that genuinely
  changes the board has to be hashed into `board_revision()`.
- Every event carries a `seq` **and an `instance` id**. Streamlit hands a
  component's last value back on *every* rerun, so without `seq` a drag
  would re-fire on every later rerun. But `seq` counts only within one
  loaded iframe, and Streamlit rebuilds that iframe on every page
  navigation — the counter restarts at 0 while the watermark in session
  state survives. That made a fresh frame's first events look stale and get
  silently dropped until the counter climbed past the old mark: *links that
  just didn't happen*, as often as not, with no pattern to it (it depended
  on how much you'd clicked before navigating away). The `instance` id is
  what distinguishes "counter restarted" from "event replayed".
- **Gesture state must never outlive its gesture.** A pointerup released
  outside the iframe never arrives, so a drag can be left hanging — and
  since pointerup checks `chipDown`, then `moving`, then `linking` in
  order, a stale one *hijacks the next gesture*: a forgotten chip press
  turned the next link drag into a bid-panel open and swallowed the link.
  Pointer capture keeps the release from being lost in the first place;
  `resetGesture()` on every pointerdown makes a missed one harmless.

### Downstream bid files — SWPW built, more to come

Clicking a market chip (not dragging it — that still starts a link) opens a
builder for that market's own bid file. **SWPW is the only one built**;
clicking any other market chip says so rather than doing nothing.

**The desk's own rule, not derived from the trade data:**

    linked to a MAG buy  -> SHORT position at the seller's GCA (Source > SWPW)
    linked to a MAG sale -> LONG position at the buyer's LCA  (SWPW > Sink)

because a physical buy needs the seller's generation wheeled *into* SPP (an
export/short at the GCA), and a physical sale needs power wheeled *out of*
SPP into the buyer's load (an import/long at the LCA).

**The workflow, as specified:** click the chip → one bid line per
counterparty, its 24-hour SWPW position already aggregated (every link
touching that counterparty and that market, summed) → the trader types the
GCA (short) or LCA (long) to bid at and, if the price isn't the usual one,
a price → **Generate Bid File** writes the `.xlsm`.

**The builder is laid out like the file it writes**, on the desk's own
request: both sides side by side in one window, hours down the index, and a
three-level header — counterparty, then the GCA/LCA to bid it at, then its
MW and Price columns — the workbook's own shape, down to the blue/tan SHORT
and LONG banners. Columns are exactly as wide as the numbers in them, and
the modal is sized to the grid rather than to one of `st.dialog`'s three
fixed widths (`ui.scheduling.bidgrid.dialog_width_px`).

**It is a custom component** (`components/bid_grid`), for the same reason
the board is: plain Streamlit can't produce it. `st.data_editor` *flattens*
a pandas MultiIndex into single-level column names
(`streamlit/elements/widgets/data_editor.py::_fix_column_headers`), so a
grouped header is out of reach, and a `+` button inside a header cell is not
somewhere any Streamlit widget can go. Same shape as `trade_board`: one
static `index.html`, no npm build step.

Cells are blank rather than zero where a position doesn't flow — the file
leaves those hours empty too — and **a price shows only on the hours that
carry MW**, because a price against an empty hour is a number the file would
never contain. A price is per bid line, not per hour (that's what reaches
the file), so typing it into any cell of a column sets it for the day; a new
line starts at 0 for a short and 50 for a long
(`domain.bidfiles.DEFAULT_PRICE`), the values the desk asked for. A typed
GCA/LCA is upper-cased on the way out, so what the cell shows is what the
file gets.

Beside each hour is a read-only **EPT** column showing where that hour
lands in the file. The board, the trades, and this grid are all Pacific
while the sheet is Eastern, and a trader reconciling the two shouldn't have
to do that arithmetic in their head — including the `1*`/`2*`/`3*` rows (see
the timezone note below).

**The `+` on a GCA/LCA cell** adds a second column pair for a counterparty
needing more than one code (and a `×` on that pair takes it back, returning
its MW to the first). The new MW column starts empty, and **typing into it
moves MW off the counterparty's other columns rather than adding to them**
(`domain.bidfiles.rebalance_hour`): a split changes *how* a position is bid,
never *how much*, so only one side of it is ever typed. The residual spreads
in proportion to what the other lines already carry — with two lines, the
common case, the first simply gives way. An entry larger than the day's own
MW is deliberately *not* trimmed back: the other lines go to zero, the hour
is tinted where it happened, and the validation says so — rather than
silently rewriting a number that was just typed. That validation is
unchanged: every active split needs a code and a price, and every hour must
add back up to exactly what the aggregate schedule carries — nothing
invented, nothing dropped.

Two things worth keeping about how it's wired. **The rebalancing stays in
Python**: the component reports the one cell that changed and redraws what
it's handed, so the rule lives once, in the tested place, at the cost of a
round trip per edit. And **that round trip has to be invisible** — the grid
patches cell values in place while the column structure is unchanged, never
touching the cell the caret is in (its warning tint, yes; its value, no),
and rebuilds only when a split or a new counterparty actually changes the
columns.

**The format was reverse-engineered from the desk's own files**, not from a
spec — every file under `Z:\Bids\BID Positionnement SPP\BID Bilateral SPP`
(27 days, plus a blank "TEST" template) was read before writing a line of
code. What that confirmed, and what stayed a genuine open question, is in
`data/bidfiles/swpw.py`'s own module docstring — the short version:

- Header convention `SPP-SHORT(<code>)` / `SPP-LONG(<code>)`, one MW+Price
  column-pair per bid line. The same code can legitimately appear on two
  separate, un-merged columns — confirmed live in the history (two
  different-valued "GWA" short columns on three separate days) — so a
  split never needs to merge into an existing line with the same code.
- Four rows (`Hour Transaction`, `Code de stratégie`, `Code vérification`,
  `Is NE DAM`) are the same default in literally every one of the 27 real
  files, so they're just always written that way.
- ~~The `1*`/`2*`/`3*` rows' meaning couldn't be determined.~~ **Solved —
  they're a timezone artifact.** See *Hours are EPT, the app is PPT* below.
- **Generated fresh each time**, not by copying a prior day's file: the
  desk's real files reuse the same lettered column for different GCA/LCA
  codes from one day to the next, which isn't something to reproduce
  programmatically without guessing at the manual convention behind it.

**Hours are EPT, the app is PPT.** The sheet's hour column is labeled
"HE EPT" — Eastern — while everything in this app is Pacific
(`domain.options.TIME_ZONE`). Every hour is shifted +3 on the way in
(`domain.bidfiles.ppt_to_ept`); both are US zones on the same DST
schedule, so the offset is a constant 3 hours year-round and needs no
timezone library. PPT HE1 is EPT HE4, and the last three hours of a PPT
day fall after midnight Eastern.

**That is what the `1*`/`2*`/`3*` rows are**: HE1-3 of the *next* EPT day,
where PPT HE22/23/24 land. The desk's own files confirm it twice over — a
full-day GWA position fills exactly 24 slots running EPT HE4-24 plus those
three stars, and an LL position fills EPT HE4-9 plus `2*`/`3*`, which is
precisely PPT HE1-6 + HE23-24, the off-peak shape from
`domain.shapes.build_schedule`, and a nonsense shape read any other way.
EPT HE1-3 stay empty because they belong to the *previous* PPT flow date,
which is exactly what every real file shows.

**Price is a plain trader entry**, deliberately — every real file's Price
column is a flat, manually-chosen number per line (`-1`, `0`, `50`, ...),
never something derivable from the underlying trade's own price, so the
popup just asks for it.

**Safety, matching the app's existing conventions:** `write_bid_file`
refuses to overwrite an existing file for that date without `overwrite=True`
— the popup shows the conflict and asks for an explicit tick, the same
two-step confirmation `ui.actions` already uses for a past-dated trade.

**Currently in test mode.** `data/bidfiles/swpw.py::TEST_MODE` is `True`, so
every generated file is written as `BID officiel Bilateral SPP - test
emilio.xlsm` instead of the real dated name — the desk's actual submissions
are never touched while this is being tried out. Flip `TEST_MODE` to `False`
(or call `official_filename()` directly) to go live; nothing else about the
file changes.

**Genuinely macro-enabled, not just named `.xlsm`.** A first version wrote a
plain workbook straight to a `.xlsm` name — Excel refused to open it
outright ("the file format or file extension is not valid"), not a warning
to click through. A `.xlsm`'s macro-enabled-ness is real OOXML content, not
just its extension. The fix attaches a real macro-enabled file's zip as
`Workbook.vba_archive` before saving (`data/bidfiles/swpw.py`'s
`_save_as_macro_enabled` — the same mechanism `load_workbook(path,
keep_vba=True)` uses internally); the shell's own macro content is
irrelevant, only its presence is borrowed. The blank `TEST.xlsm` template
serves as that shell, since it always exists and, unlike a dated file,
never goes away.

### Decided so far

- **Persistence: session-only for now.** Links don't need to survive
  across sessions yet — mirrors how Phase 1 itself started with
  `st.session_state.trades` before `BilateralTrades` writes existed. Revisit
  once the interaction model is validated and it's clear what a link
  actually needs to record.
- **Square granularity: per trade, per flow date** (see above) — resolves
  the grain question Phase 1's version of this doc left open.

### Still open

- ~~**Canvas implementation.**~~ **Settled: the custom component.** Both
  were built. The native-Streamlit approximation (select two squares, press
  a button; a read-only SVG map below the board) lost on use — too tall,
  squares too big, too many clicks. The component is hand-written vanilla
  JS rather than React Flow or Cytoscape, which removes the "separate
  engineering track" objection that made this a hard call.
- **The link-schedule suggestion algorithm** (see Linking, above). A
  placeholder is in place; it is explicitly not the answer.
- **The exact market list and tickers** (see Market squares, above). The
  list as given — CAISO, SWPW, SWPP, AESO, CEN — is what's wired in.
- **MW/hour reconciliation semantics**: does a match need its linked
  quantities to net to zero (a buy fully covered by its linked sells), or
  is partial/open linking a normal, expected state alongside the "open
  positions" row at the top? Assumed for now: **partial is normal**, and
  over-allocation is a warning, not a block.
- **Source of candidate buys/sells** — **first answer: both.** The view
  reads `BilateralTrades` for the flow date *and* this session's own
  `st.session_state.trades`, with a source filter to drop either. A session
  trade that carries `db_trade_ids` is skipped, so a trade written to the DB
  is counted once, from the DB.
- **Downstream bid files for markets other than SWPW** (CAISO, SWPP, AESO,
  CEN) — see below. Each has its own real format to reverse-engineer the
  same way SWPW's was; none of that work has started.
- ~~**The `1*`/`2*`/`3*` bid-file rows' meaning.**~~ **Settled: HE1-3 of
  the next EPT day**, where the last three hours of a PPT day land. See
  *Hours are EPT, the app is PPT*, above.
- **Whether SWPW's `TEST_MODE` should stay a code constant** or become a
  real "test vs. live" toggle in the UI once more than one person is using
  this — a constant is fine while it's just being tried out.

### Opened by building it

- **Grain, again: one DB row or one trade?** A Phase 1 trade writes one
  `BilateralTrades` row per He-shape/MW level (`compress_schedule`), and the
  table has no column grouping those rows back into the trade they came
  from. So a leg entered with a hand-edited grid shows as **several squares
  on the same flow date**. They're left separate, since the row is the only
  thing with a stable identity to hang a link off. Merging them by shared
  terms (trade date + PSE + POR/POD + side + price) is possible but would
  guess at which rows belong together.
- **Where do square positions belong?** They're per session, in
  `st.session_state.mv_positions`, keyed by leg key and measured in board
  pixels. Two consequences worth a decision: a trader's layout is gone when
  the session ends, and it doesn't survive a browser resize gracefully
  (positions are clamped, not rescaled). Persisting a layout — and whether
  it should be per trader or per desk — is a real question if the manual
  arrangement turns out to be something people invest in.
- **Should `×` ever really delete?** It hides, for the reasons above. If the
  answer is that a trader genuinely needs to remove a mistaken trade from
  here, that's a destructive write to a compliance table and wants a
  confirmation step and an audit trail, not a reuse of this affordance.
- ~~**Should a filtered-out square's links still be honoured?**~~
  **Settled: yes, for PSE/POR-POD, via one-hop rescue** (see *Filtering
  shows more, not less*, above). Still open: whether the **source** filter
  (Database / This session) should get the same treatment — it currently
  can't, without always loading both sources regardless of the checkboxes.
- **Refresh vs. caching.** The book is cached for 60 seconds with a manual
  Refresh. Whether that's the right trade-off depends on how many people are
  entering trades at once.
- **Auto-layout on a busy day.** Squares stack down each side and wrap into
  a second column at about 9 per column. A day with 40 legs a side will
  overflow the 520 px canvas. Options when that bites: a taller board, a
  scrolling canvas, zoom, or grouping by PSE — not worth choosing between
  until it's an actual problem.

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
