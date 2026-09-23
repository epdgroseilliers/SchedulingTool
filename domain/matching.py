"""Phase 2 — matching buy legs against sell legs for a single flow date.

Pure logic: no Streamlit, no DB, so every rule here is unit-testable without
AppTest or a live connection. `ui/scheduling/` supplies the trades (from
`data.matching` and the running session) and the flow date's on-/off-peak
flag; this module turns them into squares, links, and the open-position
arithmetic the view is built around.

Two shapes of thing live here:

**Legs** — one square on the board. A `BilateralTrades` row stores a date
*range* plus a single He value, so a leg is that row *re-expanded back down
to one flow date* — the reverse of what `data.bilateral.compress_schedule()`
does on the way in. A trade spanning three days is three legs, one per day,
each with that day's own hours (an `'HL'` row simply doesn't flow on an
off-peak day, and produces no leg at all).

**Links** — a buy key paired with a sell key plus the MW-per-hour allocated
to that pairing. Deliberately a standalone record rather than a field on
either side, because the matching is many-to-many: one buy can be covered by
several sells and vice versa. A leg's "open" position is whatever its own
schedule still has left after every link touching it is subtracted.
"""

from dataclasses import dataclass, field

from data.bilateral import hours_to_he_string
from domain.shapes import parse_shape

#: Hours each standard He label covers, by the flow date's peak status. These
#: mirror domain.shapes.build_schedule going the other way — an 'HL' row
#: covers HE7-22 on a peak day and nothing at all on an off-peak one, and an
#: 'LL' row covers the whole day when the whole day is off-peak.
PEAK_HOURS = frozenset(range(7, 23))
SHOULDER_HOURS = frozenset(list(range(1, 7)) + [23, 24])
ALL_HOURS = frozenset(range(1, 25))

#: Markets a trader can park an open position against. A market is a sink
#: node, not a trade — see market_legs().
MARKETS = ["CAISO", "SWPW", "SWPP", "AESO", "CEN"]

BUY, SELL = "Buy", "Sell"


def expand_he(he, is_peak):
    """The hour-endings a stored He value covers on one flow date.

    `is_peak` is that date's WECC on-peak flag; None means the calendar had
    no row for it, which only matters for the labels whose meaning depends
    on it. Returns a set of HE, possibly empty — an 'HL' row on an off-peak
    day legitimately flows zero hours that day.

    Raises ValueError on an He value that can't be read, or on one whose
    meaning needs a peak flag that isn't available; the caller reports those
    rather than guessing at a schedule.
    """
    label = (he or "").strip().upper()
    if label in ("HL", "LL"):
        if is_peak is None:
            raise ValueError(
                f"He is '{label}', which needs the WECC calendar, and there is "
                "no calendar row for this flow date."
            )
        if label == "HL":
            return set(PEAK_HOURS) if is_peak else set()
        return set(SHOULDER_HOURS) if is_peak else set(ALL_HOURS)
    if label == "ATC":
        return set(ALL_HOURS)

    # Anything else is explicit hours, written the way hours_to_he_string
    # produces them: comma-joined ranges and bare single hours.
    hours = set()
    for part in label.split(","):
        part = part.strip()
        if not part:
            continue
        kind, start_he, end_he = parse_shape(part)
        if kind != "custom":
            raise ValueError(f"Could not read the hours '{he}'.")
        hours.update(range(start_he, end_he + 1))
    if not hours:
        raise ValueError(f"Could not read the hours '{he}'.")
    return hours


@dataclass
class TradeLeg:
    """One square: a single trade's flow on a single date.

    `mw_by_hour` holds only the hours that actually flow, so an empty one
    means this trade doesn't reach this date and the leg is dropped before
    it ever reaches the board.
    """

    key: str
    source: str  # "db" | "session" | "market"
    direction: str  # BUY | SELL
    pse: str  # counterparty, or the market name for a market square
    por_pod: str
    flow_date: object
    mw_by_hour: dict = field(default_factory=dict)
    price: str = ""
    index: str = ""
    trade_id: object = None

    @property
    def is_market(self):
        return self.source == "market"

    @property
    def hours(self):
        return sorted(self.mw_by_hour)

    @property
    def he_label(self):
        return hours_to_he_string(self.hours)

    @property
    def mwh(self):
        return sum(self.mw_by_hour.values())

    @property
    def mw_label(self):
        """'100 MW' for a flat leg, '50–100 MW' for one whose MW steps
        during the day (a hand-edited Phase 1 grid does this)."""
        if not self.mw_by_hour:
            return "0 MW"
        low, high = min(self.mw_by_hour.values()), max(self.mw_by_hour.values())
        return f"{low:g} MW" if low == high else f"{low:g}–{high:g} MW"


def _clean_mw_by_hour(mw_by_hour):
    """Drop zero/negative hours and normalize to int HE -> float MW."""
    return {
        int(h): float(mw) for h, mw in mw_by_hour.items() if mw and float(mw) > 0
    }


def leg_from_row(row, flow_date, is_peak, key, source):
    """One leg from a compressed BilateralTrades-shaped row, or None if the
    row doesn't flow on `flow_date`. Raises ValueError on an unreadable He.

    `row` needs start_date/stop_date/he/mw/direction/pse/por_pod and
    optionally price/index/trade_id — the shape `data.matching` returns and
    that `data.bilateral.compress_schedule()` produces.
    """
    if not (row["start_date"] <= flow_date <= row["stop_date"]):
        return None
    hours = expand_he(row["he"], is_peak)
    if not hours:
        return None
    return TradeLeg(
        key=key,
        source=source,
        direction=row["direction"],
        pse=row.get("pse") or "—",
        por_pod=row.get("por_pod") or "—",
        flow_date=flow_date,
        mw_by_hour={int(h): float(row["mw"]) for h in sorted(hours)},
        price=row.get("price") or "",
        index=row.get("index") or "",
        trade_id=row.get("trade_id"),
    )


def legs_from_db_rows(rows, flow_date, is_peak):
    """(legs, warnings) for every BilateralTrades row covering `flow_date`.

    One square per *row*, not per trade: the table has no column grouping
    the rows of one multi-row trade, so the row is the finest grain that can
    be identified stably enough to hang a link off.
    """
    legs, warnings = [], []
    for row in rows:
        try:
            leg = leg_from_row(
                row, flow_date, is_peak, f"db:{row['trade_id']}", "db"
            )
        except ValueError as e:
            warnings.append(f"Trade {row.get('trade_id', '?')}: {e}")
            continue
        if leg is not None:
            legs.append(leg)
    return legs, warnings


def legs_from_session_trades(trades, flow_date, is_peak):
    """(legs, warnings) for trades held in this session's own list.

    Trades already written to the DB are skipped — they come back through
    legs_from_db_rows() instead, and counting them twice would double the
    book. Monthly trades have no hourly grid, so their blocks are read the
    same way a DB row is.
    """
    legs, warnings = [], []
    for i, t in enumerate(trades):
        if t.get("db_trade_ids"):
            continue
        base = {
            "direction": t.get("direction"),
            "pse": t.get("counterparty"),
            "por_pod": t.get("location"),
            "price": t.get("price"),
            "index": t.get("index"),
            "trade_id": None,
        }
        if t.get("is_monthly"):
            for j, block in enumerate(t.get("monthly_blocks", [])):
                try:
                    leg = leg_from_row(
                        {**base, **block},
                        flow_date,
                        is_peak,
                        f"session:{i}:{j}",
                        "session",
                    )
                except ValueError as e:
                    warnings.append(f"Session trade {i + 1}: {e}")
                    continue
                if leg is not None:
                    legs.append(leg)
            continue

        mw_by_hour = _clean_mw_by_hour(
            {he: mw for d, he, mw in t.get("schedule", []) if d == flow_date}
        )
        if not mw_by_hour:
            continue
        legs.append(
            TradeLeg(
                key=f"session:{i}",
                source="session",
                direction=base["direction"],
                pse=base["pse"] or "—",
                por_pod=base["por_pod"] or "—",
                flow_date=flow_date,
                mw_by_hour=mw_by_hour,
                price=base["price"] or "",
                index=base["index"] or "",
            )
        )
    return legs, warnings


# ---------------------------------------------------------------- links


@dataclass
class Link:
    """A buy square paired with a sell square, plus the MW-per-hour the
    trader allocated to that pairing. Many-to-many by construction: nothing
    stops several links touching the same key, which is exactly how one buy
    gets covered by three sells."""

    link_id: str
    buy_key: str
    sell_key: str
    mw_by_hour: dict = field(default_factory=dict)

    @property
    def hours(self):
        return sorted(self.mw_by_hour)

    @property
    def he_label(self):
        return hours_to_he_string(self.hours)

    @property
    def mwh(self):
        return sum(self.mw_by_hour.values())

    def touches(self, key):
        return key in (self.buy_key, self.sell_key)

    def other_key(self, key):
        return self.sell_key if key == self.buy_key else self.buy_key


def key_for_side(direction):
    """Which of a Link's two key fields a leg of this direction occupies."""
    return "buy_key" if direction == BUY else "sell_key"


def links_for(key, links):
    return [ln for ln in links if ln.touches(key)]


def allocated_by_hour(key, links):
    """MW already committed on each hour of a leg, across every link
    touching it."""
    total = {}
    for ln in links_for(key, links):
        for h, mw in ln.mw_by_hour.items():
            total[h] = total.get(h, 0.0) + float(mw)
    return total


def open_by_hour(leg, links):
    """What's left of a leg's own schedule after its links are subtracted.
    Never negative: over-allocation is reported by over_allocated_hours(),
    not smuggled in as a negative open position."""
    allocated = allocated_by_hour(leg.key, links)
    return {
        h: mw - allocated.get(h, 0.0)
        for h, mw in leg.mw_by_hour.items()
        if mw - allocated.get(h, 0.0) > 0
    }


def over_allocated_hours(leg, links):
    """Hours where the links touching a leg commit more MW than the leg
    itself carries — possible because the trader can overwrite any
    suggested allocation, and worth flagging rather than silently clamping.
    Returns {hour: excess MW}."""
    allocated = allocated_by_hour(leg.key, links)
    return {
        h: mw - leg.mw_by_hour.get(h, 0.0)
        for h, mw in allocated.items()
        if mw - leg.mw_by_hour.get(h, 0.0) > 0
    }


def matched_mwh(leg, links):
    return sum(allocated_by_hour(leg.key, links).values())


def open_mwh(leg, links):
    return sum(open_by_hour(leg, links).values())


def is_open(leg, links):
    return open_mwh(leg, links) > 0


def suggest_allocation(buy_leg, sell_leg, links):
    """The schedule to propose when a link is first drawn: every hour both
    legs still have open, at the smaller of the two sides' remaining MW.

    This is the placeholder algorithm — PROJECT.md flags the real one as
    needing its own design pass (proportional split across a square's open
    links, first-available-hours, and others are all live options). "Full
    overlap, capped at what's still open" is the one that can't over-commit
    either side, which makes it the safe default to argue *from*. The trader
    can overwrite it hour by hour before confirming either way.

    A market is the exception: it's a sink with no schedule of its own, so
    there is nothing to overlap *with*. Linking to one means "park what's
    left of this position in the spot market", which is the other side's
    whole open schedule.
    """
    if buy_leg.is_market != sell_leg.is_market:
        real = sell_leg if buy_leg.is_market else buy_leg
        return dict(open_by_hour(real, links))

    buy_open = open_by_hour(buy_leg, links)
    sell_open = open_by_hour(sell_leg, links)
    return {
        h: min(buy_open[h], sell_open[h])
        for h in sorted(set(buy_open) & set(sell_open))
    }


def make_link(link_id, buy_leg, sell_leg, mw_by_hour):
    return Link(
        link_id=link_id,
        buy_key=buy_leg.key,
        sell_key=sell_leg.key,
        mw_by_hour=_clean_mw_by_hour(mw_by_hour),
    )


# --------------------------------------------------------- market squares


def market_leg_key(market, direction, flow_date):
    return f"market:{market}:{direction}:{flow_date.isoformat()}"


def parse_market_key(key):
    """(market, direction) for a market leg key, or None for any other key."""
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "market":
        return None
    return parts[1], parts[2]


def market_legs(links, flow_date):
    """Squares for the markets this flow date's links reach.

    A market has no `BilateralTrades` row behind it — it exists *because*
    someone linked an open position to it, and its schedule is nothing more
    than the sum of those links. So it's derived fresh from the link book on
    every render rather than stored, which also means removing the last link
    to a market makes its square disappear on its own.
    """
    by_key = {}
    for ln in links:
        for key in (ln.buy_key, ln.sell_key):
            parsed = parse_market_key(key)
            if parsed is None:
                continue
            market, direction = parsed
            leg = by_key.get(key)
            if leg is None:
                leg = by_key[key] = TradeLeg(
                    key=key,
                    source="market",
                    direction=direction,
                    pse=market,
                    por_pod="Market",
                    flow_date=flow_date,
                )
            for h, mw in ln.mw_by_hour.items():
                leg.mw_by_hour[h] = leg.mw_by_hour.get(h, 0.0) + float(mw)
    return list(by_key.values())


# --------------------------------------------------------------- totals


def board_totals(legs, links):
    """The numbers the open-positions strip shows: MWh bought, sold, matched
    and still open on each side, plus the net long/short for the day."""
    buys = [lg for lg in legs if lg.direction == BUY and not lg.is_market]
    sells = [lg for lg in legs if lg.direction == SELL and not lg.is_market]
    buy_mwh = sum(lg.mwh for lg in buys)
    sell_mwh = sum(lg.mwh for lg in sells)
    return {
        "buy_mwh": buy_mwh,
        "sell_mwh": sell_mwh,
        "net_mwh": buy_mwh - sell_mwh,
        "buy_open_mwh": sum(open_mwh(lg, links) for lg in buys),
        "sell_open_mwh": sum(open_mwh(lg, links) for lg in sells),
        "linked_mwh": sum(ln.mwh for ln in links),
        "link_count": len(links),
    }


def sort_legs(legs, links):
    """Board order: open positions first (that's the top of the column the
    layout calls for), then by counterparty and POR/POD so the same square
    keeps roughly the same place between reruns."""
    return sorted(
        legs,
        key=lambda lg: (
            0 if is_open(lg, links) else 1,
            lg.is_market,
            (lg.pse or "").upper(),
            (lg.por_pod or "").upper(),
            lg.key,
        ),
    )


def filter_legs(legs, *, links=None, pses=None, por_pods=None):
    """Narrow what's shown — but never split a link in two.

    A leg that fails the filter is kept anyway when it's directly linked to
    a leg that passes, so a link is always either fully visible or (when
    neither side matches) fully gone — never left with one end drawn and
    the other missing. This is a deliberate "show more, not less" choice:
    filtering down to one PSE should not hide the very counterparty a
    trader is actively tracking a link against.

    Rescue is **one hop**: a leg kept only because it's linked to a match
    doesn't, in turn, drag in *its own* other link partners. Without that
    limit, a filter in a well-connected book could end up reconstructing
    the whole thing it was meant to narrow.

    Market legs are never filtered out on their own account, rescued or
    not: they aren't squares on the board but chips on its rail, always
    present as somewhere to park a position. A PSE filter naming a
    counterparty would otherwise take away everywhere to park one — but a
    market is also not a hub to rescue *through*: a real leg linked only to
    an always-visible market chip is not, on that account alone, rescued.

    `links` is optional and defaults to no rescue (matching the pre-rescue
    behavior) — pass the session's actual link list to get it.
    """
    if not pses and not por_pods:
        return list(legs)

    def matches(lg):
        if pses and lg.pse not in pses:
            return False
        if por_pods and lg.por_pod not in por_pods:
            return False
        return True

    kept = {lg.key: lg for lg in legs if lg.is_market or matches(lg)}
    if links:
        by_key = {lg.key: lg for lg in legs}
        # Seeded only from real matches — a leg kept purely because it's a
        # market chip isn't grounds to rescue anything through it.
        matched_keys = {k for k, lg in kept.items() if not lg.is_market}
        for ln in links:
            for near, far in ((ln.buy_key, ln.sell_key), (ln.sell_key, ln.buy_key)):
                if near in matched_keys and far in by_key and far not in kept:
                    kept[far] = by_key[far]
    return list(kept.values())
