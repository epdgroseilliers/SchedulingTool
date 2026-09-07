# Streamlit Electricity Trade Scheduling Helper - MVP Build Spec

## 1. Project Goal

Build a lightweight Python + Streamlit dashboard that helps an electricity trader enter, map, and track the scheduling status of daily physical electricity trades.

The tool is intended to reduce operational mistakes such as:

- Forgetting to submit an e-tag
- Forgetting to reserve transmission
- Forgetting to bid or schedule into an ISO
- Leaving a sell unmapped to a buy/source
- Overselling or double-allocating MWs for a specific hour
- Losing track of split trades across multiple counterparties, hours, or markets

The MVP should be simple, functional, and easy to run locally.

The target user is an electricity trader operating primarily in Western power markets, including:

- CAISO / CISO
- CENACE
- WECC
- SPP
- AESO

The first version is for one user, but the design should not prevent later team use.

---

## 2. Technology Requirements

Use the following stack:

- Python 3.11+
- Streamlit
- SQLite for local persistence
- Pandas for table manipulation
- SQLAlchemy for database access
- Plotly or native Streamlit charts only if useful

Keep the app easy to install and run.

The project should include:

```text
trade_scheduler/
  app.py
  db.py
  models.py
  services.py
  validation.py
  seed_data.py
  requirements.txt
  README.md
  data/
    trade_scheduler.db
```

The exact file structure can be adjusted if there is a better simple architecture, but avoid over-engineering.

---

## 3. Core Product Concept

The app should help the user answer:

1. What trades did I enter today?
2. Which buys are mapped to which sells, ISO sinks, or internal uses?
3. Which MWs and hours remain unmapped?
4. Which trades or mappings still require action?
5. Which actions are pending, submitted, confirmed, waived, or problematic?
6. Are there any scheduling risks before deadlines?

The most important design principle is:

> Every MW-hour should be accounted for.

The system should treat trades as commercial records, but scheduling as hourly MW obligations.

For example, a 50 MW HE 7-22 trade should be stored internally as hourly records for HE 7, HE 8, etc., because partial-hour and partial-MW mapping is common.

---

## 4. MVP Scope

Build the following Streamlit pages:

1. Daily Dashboard
2. Trade Entry / Trade Blotter
3. Mapping Board
4. Action Queue
5. Exceptions / Final Checklist
6. Admin / Reference Data

Do not build external integrations in the MVP.

Do not connect to actual e-tag, ISO, transmission, or ETRM systems yet.

The MVP should be manual-first:

- User manually enters trades
- User manually maps supply to demand
- User manually marks actions as pending/submitted/confirmed/waived
- App automatically calculates remaining MWs, mapping completeness, and exceptions

---

## 5. Key Definitions

### 5.1 Trade

A trade is the original deal entered by the trader.

Examples:

- Buy 50 MW HE 7-22 Palo Verde from Counterparty A
- Sell 25 MW HE 7-10 COB to Counterparty B
- Sink 25 MW HE 11-22 into CAISO
- Internal supply or internal demand

### 5.2 Trade Slice

A trade slice is an hourly representation of a trade.

Each trade should automatically generate one slice per hour in the trade's hour range.

Example:

A trade entered as:

```text
Buy 100 MW HE 7-22
```

Should generate hourly slices:

```text
HE 7: 100 MW
HE 8: 100 MW
...
HE 22: 100 MW
```

Trade slices are what get mapped.

### 5.3 Mapping

A mapping links supply to demand for a specific date, hour, and MW amount.

Examples:

- Buy slice -> Sell slice
- Buy slice -> CAISO sink slice
- Internal supply -> Sell slice
- Buy slice -> Internal demand

Mappings must not exceed the available MW on the supply slice or the needed MW on the demand slice unless the user explicitly overrides the validation. For the MVP, do not allow overmapping.

### 5.4 Action

An action is a scheduling task that must be completed.

Examples:

- Map trade
- Reserve transmission
- Submit e-tag
- Submit CAISO bid
- Submit CENACE schedule
- Verify path
- Confirm tag
- Check transmission reservation

Actions can be attached to:

- A trade
- A trade slice
- A mapping

---

## 6. Data Model

Use SQLite tables.

### 6.1 `trades`

Stores raw trade entries.

Fields:

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| trade_id | TEXT UNIQUE | Human-readable ID, e.g. T-0001 |
| trade_date | DATE | Date the deal was made |
| flow_date | DATE | Date power flows |
| market | TEXT | CAISO, CENACE, WECC, SPP, AESO, Other |
| direction | TEXT | Buy, Sell, ISO Sink, ISO Source, Internal Supply, Internal Demand |
| counterparty | TEXT | Counterparty or internal book |
| product | TEXT | On-Peak, Off-Peak, Flat, Custom |
| start_he | INTEGER | First hour ending, 1-24 or 1-25 if later DST support added |
| end_he | INTEGER | Last hour ending, inclusive |
| mw | REAL | Positive MW quantity |
| price | REAL NULL | Optional |
| source | TEXT NULL | Source/POR/location |
| sink | TEXT NULL | Sink/POD/location |
| path | TEXT NULL | Transmission path if known |
| requires_mapping | INTEGER | 0/1 |
| requires_tag | INTEGER | 0/1 |
| requires_transmission | INTEGER | 0/1 |
| requires_bid | INTEGER | 0/1 |
| status | TEXT | New, Needs Mapping, Partially Mapped, Mapped, Needs Scheduling, Scheduled, Complete, Problem |
| notes | TEXT NULL | Free text |
| created_at | DATETIME | Timestamp |
| updated_at | DATETIME | Timestamp |

### 6.2 `trade_slices`

Stores hourly supply/demand slices generated from trades.

Fields:

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| slice_id | TEXT UNIQUE | Human-readable ID, e.g. S-000001 |
| trade_id | TEXT FK | Parent trade_id |
| flow_date | DATE | Flow date |
| hour_ending | INTEGER | HE 1-24 |
| direction_class | TEXT | Supply or Demand |
| slice_type | TEXT | Buy, Sell, ISO Sink, ISO Source, Internal Supply, Internal Demand |
| location | TEXT NULL | Use source for supply, sink for demand when possible |
| mw | REAL | Original MW for this hour |
| status | TEXT | Open, Partially Mapped, Mapped |
| created_at | DATETIME | Timestamp |
| updated_at | DATETIME | Timestamp |

Direction class logic:

- Supply: Buy, ISO Source, Internal Supply
- Demand: Sell, ISO Sink, Internal Demand

### 6.3 `mappings`

Stores links between supply and demand slices.

Fields:

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| mapping_id | TEXT UNIQUE | Human-readable ID, e.g. M-000001 |
| supply_slice_id | TEXT FK | Must point to a Supply slice |
| demand_slice_id | TEXT FK | Must point to a Demand slice |
| flow_date | DATE | Must match both slices |
| hour_ending | INTEGER | Must match both slices |
| mw | REAL | MW allocated |
| path | TEXT NULL | Optional path |
| notes | TEXT NULL | Free text |
| created_at | DATETIME | Timestamp |
| updated_at | DATETIME | Timestamp |

Validation:

- Supply and demand slices must have the same flow_date and hour_ending.
- MW must be positive.
- MW cannot exceed remaining supply MW.
- MW cannot exceed remaining demand MW.

### 6.4 `actions`

Stores scheduling tasks.

Fields:

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| action_id | TEXT UNIQUE | Human-readable ID, e.g. A-000001 |
| related_trade_id | TEXT NULL | Optional |
| related_slice_id | TEXT NULL | Optional |
| related_mapping_id | TEXT NULL | Optional |
| action_type | TEXT | Map, Reserve Transmission, Submit Tag, Submit Bid, Submit Schedule, Verify Path, Confirm, Other |
| market | TEXT NULL | Market |
| due_time | DATETIME NULL | Optional deadline |
| owner | TEXT NULL | User/person responsible |
| status | TEXT | Not Required, Required, Pending, In Progress, Submitted, Confirmed, Failed, Rejected, Waived |
| reference | TEXT NULL | Tag ID, reservation number, bid ID, etc. |
| notes | TEXT NULL | Free text |
| created_at | DATETIME | Timestamp |
| updated_at | DATETIME | Timestamp |

### 6.5 `reference_rules`

Optional MVP table for simple action suggestions.

Fields:

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| market | TEXT | Market |
| source | TEXT NULL | Source location |
| sink | TEXT NULL | Sink location |
| path | TEXT NULL | Path |
| requires_tag | INTEGER | 0/1 |
| requires_transmission | INTEGER | 0/1 |
| requires_bid | INTEGER | 0/1 |
| default_due_time | TEXT NULL | e.g. 10:00 |
| notes | TEXT NULL | Free text |

This table can be simple in v1. It is okay if action creation is mostly manual.

---

## 7. Core Business Logic

### 7.1 Trade Creation

When a user creates a trade:

1. Insert the trade into `trades`.
2. Automatically generate hourly rows in `trade_slices` from `start_he` through `end_he`, inclusive.
3. Determine `direction_class`:
   - Buy -> Supply
   - ISO Source -> Supply
   - Internal Supply -> Supply
   - Sell -> Demand
   - ISO Sink -> Demand
   - Internal Demand -> Demand
4. Set slice MW equal to trade MW for each hour.
5. Create initial actions if required by checkboxes:
   - If requires_mapping: create Map action
   - If requires_tag: create Submit Tag action
   - If requires_transmission: create Reserve Transmission action
   - If requires_bid: create Submit Bid action
6. Set initial trade status:
   - If requires_mapping: Needs Mapping
   - Else if any scheduling action required: Needs Scheduling
   - Else: New

### 7.2 Remaining MW Calculation

For every supply slice:

```text
remaining_supply_mw = slice.mw - sum(mapping.mw where supply_slice_id = slice.slice_id)
```

For every demand slice:

```text
remaining_demand_mw = slice.mw - sum(mapping.mw where demand_slice_id = slice.slice_id)
```

A slice is:

- Open if mapped MW = 0
- Partially Mapped if mapped MW > 0 and mapped MW < slice MW
- Mapped if mapped MW = slice MW

### 7.3 Trade Status Calculation

A trade's mapping status should be based on its slices.

For demand trades that require mapping:

- Needs Mapping: all slices unmapped
- Partially Mapped: some but not all MW-hours mapped
- Mapped: all MW-hours fully mapped

For supply trades:

- Open or partial supply can be allowed if the user intentionally has length/open position
- Still show unmapped remaining supply clearly

Scheduling status should account for actions:

- If all required actions are Confirmed or Waived, status can move to Complete
- If any action is Failed or Rejected, status should be Problem
- If mapping is complete but scheduling actions are pending, status should be Needs Scheduling
- If actions are Submitted but not Confirmed, status should be Scheduled or Submitted, depending on the chosen status wording

Keep status logic simple and transparent.

### 7.4 Mapping Creation

When a user creates a mapping:

1. User selects a supply slice or supply trade/hour range.
2. User selects a demand slice or demand trade/hour range.
3. User enters MW.
4. App validates date/hour compatibility.
5. App validates sufficient remaining supply and demand MW.
6. App creates mapping rows.
7. App updates slice statuses.
8. App recalculates trade statuses.

For MVP, support mapping one hour at a time first.

Strongly preferred MVP enhancement:

Allow bulk mapping across matching hours.

Example:

- Supply trade: Buy 50 MW HE 7-22
- Demand trade: Sell 50 MW HE 7-22
- User enters MW = 50 and hour range HE 7-22
- App creates one mapping per hour

For partial overlap:

- Supply HE 7-22
- Demand HE 7-10
- App should allow mapping HE 7-10 only

### 7.5 Deconstructing / Splitting Trades

The user should not need a special split-trade workflow in the MVP.

Splitting is handled by mapping different MW amounts and hours from the same parent trade's hourly slices.

Example:

Original buy:

```text
Buy 100 MW HE 7-22
```

Mappings:

```text
50 MW HE 7-22 -> Sell A
25 MW HE 7-10 -> Sell B
25 MW HE 11-22 -> CAISO Sink
```

The system should show remaining MW by hour after each mapping.

---

## 8. Streamlit Page Requirements

## 8.1 Daily Dashboard

Purpose:

Show the day's operational state at a glance.

Controls:

- Flow date selector
- Market filter
- Counterparty filter
- Status filter

Metrics:

- Total supply MW-hours
- Total demand MW-hours
- Unmapped supply MW-hours
- Unmapped demand MW-hours
- Pending actions
- Overdue actions
- Failed/rejected actions

Display sections:

1. Summary metrics
2. Exception warnings
3. Pending action queue
4. Recently entered trades
5. Mapping completeness by market or hour

Important warnings:

- Demand exists with no mapped supply
- Supply exists with no mapped demand or intentional sink
- Required tag pending
- Required transmission pending
- Required bid pending
- Action overdue
- Failed/rejected action unresolved

## 8.2 Trade Entry / Trade Blotter

Purpose:

Allow fast entry and editing of trades.

Required form fields:

- trade_date, default today
- flow_date, default tomorrow or selected date
- market dropdown: CAISO, CENACE, WECC, SPP, AESO, Other
- direction dropdown: Buy, Sell, ISO Sink, ISO Source, Internal Supply, Internal Demand
- counterparty text input
- product dropdown: On-Peak, Off-Peak, Flat, Custom
- start_he integer
- end_he integer
- mw number input
- price optional number input
- source text input
- sink text input
- path text input
- checkboxes:
  - requires_mapping
  - requires_tag
  - requires_transmission
  - requires_bid
- notes text area

After submit:

- Create trade
- Generate slices
- Create initial actions as applicable
- Show success message with trade_id

Blotter table:

Show trades for selected flow_date.

Columns:

- trade_id
- market
- direction
- counterparty
- product
- hours
- mw
- source
- sink
- path
- status
- required flags
- notes

Allow basic edits if reasonable.

MVP acceptable approach:

- Edits can be handled by selecting a trade and editing fields in a form.
- If start/end/MW changes, regenerate slices only if there are no mappings yet.
- If mappings already exist, block structural edits and tell user to delete mappings first.

## 8.3 Mapping Board

Purpose:

Allow manual linking of supply to demand.

Layout:

- Flow date selector
- Hour filter or hour range selector
- Market/location filters

Show two tables side by side or stacked:

### Available Supply

Columns:

- slice_id
- parent trade_id
- hour_ending
- counterparty
- market
- location/source
- original MW
- mapped MW
- remaining MW

Only show slices with remaining MW > 0 by default.

### Open Demand

Columns:

- slice_id
- parent trade_id
- hour_ending
- counterparty
- market
- location/sink
- original MW
- mapped MW
- remaining MW

Only show slices with remaining MW > 0 by default.

Mapping form:

- Select supply trade or slice
- Select demand trade or slice
- Select hour range or single hour
- Enter MW
- Optional path
- Optional notes
- Submit mapping

Validation messages must be clear.

Examples:

- "Cannot map HE 9 because supply has only 10 MW remaining and requested MW is 25."
- "Cannot map these records because supply and demand flow dates do not match."
- "Cannot map HE 12 because demand is already fully mapped."

Also show an existing mappings table for the selected flow date.

Allow deleting a mapping in MVP.

When deleting mapping:

- Delete mapping row(s)
- Recalculate slice and trade statuses

## 8.4 Action Queue

Purpose:

Track operational tasks.

Filters:

- Flow date
- Status
- Action type
- Market
- Owner

Table columns:

- action_id
- action_type
- related trade/mapping/slice
- market
- due_time
- owner
- status
- reference
- notes

User should be able to:

- Create action manually
- Update action status
- Add reference number
- Add notes
- Mark as Waived with notes

Statuses:

- Not Required
- Required
- Pending
- In Progress
- Submitted
- Confirmed
- Failed
- Rejected
- Waived

For the MVP, editing can be done with a selected action form.

## 8.5 Exceptions / Final Checklist

Purpose:

Show only problems and unresolved items.

Sections:

1. Unmapped demand MW-hours
2. Unmapped supply MW-hours
3. Partially mapped trades
4. Pending required actions
5. Overdue actions
6. Failed/rejected actions
7. Structural issues

Structural issues include:

- Trade has no generated slices
- Mapping references missing slice
- Mapping has mismatched date/hour
- Action is required but not linked to anything

Checklist view:

For the selected flow date, show:

| Check | Status |
|---|---|
| All demand mapped | Pass/Fail |
| No overmapped supply | Pass/Fail |
| No overmapped demand | Pass/Fail |
| Required tags submitted or confirmed | Pass/Fail |
| Required transmission submitted or confirmed | Pass/Fail |
| Required bids submitted or confirmed | Pass/Fail |
| No failed/rejected actions unresolved | Pass/Fail |

## 8.6 Admin / Reference Data

Purpose:

Allow simple configuration.

MVP admin functions:

- View markets list
- View action types
- View status definitions
- Manage simple reference rules if implemented
- Reset demo database
- Load seed data

---

## 9. Validation and Exception Logic

Implement validation functions in `validation.py`.

Required functions:

```python
validate_trade_input(trade_data) -> list[str]
validate_mapping_request(supply_slice_ids, demand_slice_ids, mw) -> list[str]
get_remaining_supply_mw(slice_id) -> float
get_remaining_demand_mw(slice_id) -> float
get_unmapped_demand(flow_date) -> pandas.DataFrame
get_unmapped_supply(flow_date) -> pandas.DataFrame
get_overdue_actions(flow_date) -> pandas.DataFrame
get_failed_or_rejected_actions(flow_date) -> pandas.DataFrame
run_final_checklist(flow_date) -> dict
```

Trade validation:

- MW must be positive
- start_he and end_he must be valid
- start_he <= end_he
- market must not be blank
- direction must not be blank
- flow_date must not be blank
- counterparty should not be blank unless Internal Supply/Internal Demand

Mapping validation:

- Supply slice must be Supply
- Demand slice must be Demand
- Dates must match
- Hours must match
- MW must be positive
- MW must be less than or equal to remaining supply MW
- MW must be less than or equal to remaining demand MW

Action validation:

- Required actions should not be Waived without notes
- Failed/rejected actions should appear as exceptions
- Pending actions with due_time in the past should appear as overdue

---

## 10. Database / Service Layer

Implement clean service functions in `services.py`.

Suggested functions:

```python
create_trade(trade_data) -> str
update_trade(trade_id, trade_data) -> None
delete_trade(trade_id) -> None
regenerate_slices_for_trade(trade_id) -> None
create_slices_for_trade(trade_id) -> None
get_trades(flow_date=None) -> pandas.DataFrame
get_slices(flow_date=None, direction_class=None) -> pandas.DataFrame
get_available_supply(flow_date) -> pandas.DataFrame
get_open_demand(flow_date) -> pandas.DataFrame
create_mapping(supply_slice_id, demand_slice_id, mw, path=None, notes=None) -> str
create_bulk_mapping(supply_trade_id, demand_trade_id, start_he, end_he, mw, path=None, notes=None) -> list[str]
delete_mapping(mapping_id) -> None
get_mappings(flow_date=None) -> pandas.DataFrame
create_action(action_data) -> str
update_action(action_id, action_data) -> None
get_actions(flow_date=None, status=None) -> pandas.DataFrame
recalculate_slice_statuses(flow_date=None) -> None
recalculate_trade_statuses(flow_date=None) -> None
```

ID generation:

Use simple sequential human-readable IDs:

- T-0001
- S-000001
- M-000001
- A-000001

---

## 11. UX Priorities

The app must prioritize speed and clarity.

Important UX requirements:

1. The daily dashboard should load directly on startup.
2. Flow date should be easy to change and should persist in Streamlit session state.
3. Pending or risky items should be visually obvious.
4. Trade entry should require as few clicks as possible.
5. The mapping workflow should show remaining MW before and after mapping.
6. Error messages should explain the business issue, not just the technical issue.
7. The app should avoid hiding important exceptions behind filters.

Use Streamlit features such as:

- `st.tabs`
- `st.metric`
- `st.dataframe`
- `st.form`
- `st.selectbox`
- `st.date_input`
- `st.number_input`
- `st.checkbox`
- `st.status` or `st.toast` if useful

---

## 12. Seed Data

Include seed data that demonstrates a split trade scenario.

Example flow date can be tomorrow relative to the system date.

Seed scenario:

1. Buy 100 MW HE 7-22 at Palo Verde from Counterparty A
2. Sell 50 MW HE 7-22 to Counterparty B at COB
3. Sell 25 MW HE 7-10 to Counterparty C at Mead
4. ISO Sink 25 MW HE 11-22 into CAISO SP15
5. Leave some hours partially unmapped intentionally so the exception view has content
6. Add pending actions:
   - Reserve transmission
   - Submit tag
   - Submit CAISO bid

This seed data should make the dashboard meaningful immediately after install.

---

## 13. README Requirements

Create a README with:

1. Project purpose
2. Installation instructions
3. How to run the app
4. How to reset/load seed data
5. Explanation of core workflow
6. Known MVP limitations
7. Future enhancement ideas

Suggested commands:

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
python seed_data.py
streamlit run app.py
```

For Windows, include:

```bash
.venv\Scripts\activate
```

---

## 14. MVP Limitations to State Clearly

The first version does not need:

- Multi-user authentication
- Cloud deployment
- Live e-tag integration
- Live ISO integration
- Live transmission reservation integration
- Automated market rule engine
- DST 23/25-hour day handling
- Complex portfolio optimization
- Pricing/P&L analytics
- Approval workflow

However, the code should be organized so these can be added later.

---

## 15. Future Enhancements

Possible later improvements:

1. Multi-user support
2. User roles: trader, scheduler, manager
3. Audit trail for all changes
4. Lock completed flow days
5. Import trades from CSV or ETRM export
6. Export daily schedule report to Excel
7. Automated action suggestions by market/path
8. Deadline reminders
9. Teams/Slack/email alerts
10. E-tag status import
11. Transmission reservation status import
12. ISO bid/schedule status import
13. DST-aware hourly logic
14. Recurring counterparty/path templates
15. Comment threads on trades/actions

---

## 16. Acceptance Criteria

The MVP is complete when the user can:

1. Run the app locally with Streamlit.
2. Enter a buy or sell trade.
3. Automatically generate hourly slices from that trade.
4. View available supply by hour.
5. View open demand by hour.
6. Map supply slices to demand slices.
7. Split one trade across multiple counterparties, hours, or sinks.
8. See remaining unmapped MW by hour.
9. Create and update scheduling actions.
10. See pending, overdue, failed, or rejected actions.
11. Run a final checklist for a selected flow date.
12. Load seed data showing a realistic split trade example.

---

## 17. Implementation Guidance for the AI Agent

Build incrementally in this order:

1. Create database schema and initialization logic.
2. Create service functions for trades and slices.
3. Build basic Streamlit trade entry and blotter.
4. Add automatic hourly slice generation.
5. Add available supply and open demand calculations.
6. Build manual one-hour mapping.
7. Add bulk mapping across hour ranges.
8. Add action queue.
9. Add dashboard metrics and exception view.
10. Add seed data and README.
11. Refactor for clarity.
12. Add helpful validation and error messages.

Prioritize a working app over a perfect architecture.

Avoid overcomplicating the UI.

The trader should be able to use this under time pressure in the morning.

---

## 18. Suggested First Screen Behavior

When the app launches:

1. Initialize database if it does not exist.
2. Set default flow date to tomorrow.
3. Show Daily Dashboard first.
4. If no trades exist for the selected date, show a helpful message:

```text
No trades entered for this flow date yet. Go to Trade Entry to add your first trade, or load seed data from Admin.
```

---

## 19. Important Domain Notes

- CAISO is sometimes referred to as CISO in trading contexts; support both labels if practical.
- Some trades may not require mapping if they are purely financial or informational, but this MVP is focused on physical/scheduling-relevant trades.
- Some supply may intentionally remain open or be sunk internally. The UI should distinguish between an error and an intentional open/internal position.
- A tag/transmission/bid action being submitted is not the same as confirmed.
- Manual notes and reference numbers are important because the user may need to track external system IDs.

---

## 20. Final Product Principle

The MVP should make it hard for a scheduling obligation to disappear.

At the end of the morning, the trader should be able to look at one screen and know:

- What is mapped
- What is unmapped
- What still needs to be tagged
- What still needs transmission
- What still needs to be bid or scheduled
- What is late, rejected, or risky

