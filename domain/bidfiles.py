"""Turning the board's SWPW links into the lines a bid file needs.

Pure: no Streamlit, no filesystem — `data/bidfiles/swpw.py` is the write
side. This only answers two questions: which counterparties currently have
an open position through a given market chip (`market_groups`), and whether
a trader's edited-and-split version of that position is complete and
internally consistent (`validate_split`).

The SHORT/LONG rule is the desk's own, not derived from anything in the
link data itself — see PROJECT.md:

    linked to a MAG buy  -> SHORT position (Source > market)
    linked to a MAG sale -> LONG position  (market > Sink)

because a physical buy needs the seller's generation wheeled *into* the
market (an export/short at the GCA), and a physical sale needs power
wheeled *out of* the market into the buyer's load (an import/long at the
LCA).
"""

from domain.matching import BUY, SELL, market_leg_key

SHORT, LONG = "SHORT", "LONG"

#: What a fresh bid line's price starts at, per side. Asked for by the desk
#: rather than derived from anything — a short opens at 0, a long at 50 —
#: and overwritable per line in the builder; this only saves typing the
#: usual case.
DEFAULT_PRICE = {SHORT: 0.0, LONG: 50.0}

#: The app works in PPT throughout (domain.options.TIME_ZONE), but the bid
#: file's hour column is labeled "HE EPT" — Eastern. Both are US zones on
#: the same DST schedule, so the offset is a constant 3 hours all year and
#: needs no timezone library.
PPT_TO_EPT_OFFSET = 3


def ppt_to_ept(he_ppt):
    """Where a PPT hour-ending lands on an EPT hour column.

    Returns (he_ept, spills_to_next_day). PPT HE1 is EPT HE4, and the last
    three hours of a PPT day (HE22-24) fall after midnight Eastern, so they
    land on HE1-3 of the *next* EPT day.

    That last part is what the bid file's `1*`/`2*`/`3*` rows are for — a
    question the file's own history left open until the arithmetic
    explained it. The desk's real files confirm it twice over: a full-day
    GWA position fills exactly 24 slots running EPT HE4-24 plus those three
    stars, and an LL position fills EPT HE4-9 plus `2*`/`3*`, which is
    precisely PPT HE1-6 + HE23-24 — the off-peak shape from
    domain.shapes.build_schedule — and is a nonsense shape read any other
    way.
    """
    shifted = he_ppt + PPT_TO_EPT_OFFSET
    if shifted <= 24:
        return shifted, False
    return shifted - 24, True


def market_groups(market, legs, links, flow_date):
    """{(side, pse): {hour: mw}} for every link touching `market`'s two
    possible chips (its BUY-side leg and its SELL-side leg — see
    domain.matching.market_legs), aggregated by the counterparty on the
    *other* end of each link and which side of the bid file it belongs on.

    A counterparty with more than one link to this market (e.g. two
    separate trades both wheeling through it) is summed into one entry —
    this is the "aggregated per counterparty" starting point a trader then
    edits and, if needed, splits across more than one GCA/LCA.
    """
    by_key = {lg.key: lg for lg in legs}
    market_keys = {market_leg_key(market, d, flow_date) for d in (BUY, SELL)}

    groups = {}
    for ln in links:
        if ln.buy_key in market_keys:
            real_key = ln.sell_key
        elif ln.sell_key in market_keys:
            real_key = ln.buy_key
        else:
            continue
        real_leg = by_key.get(real_key)
        if real_leg is None or real_leg.is_market:
            continue
        side = SHORT if real_leg.direction == BUY else LONG
        bucket = groups.setdefault((side, real_leg.pse), {})
        for h, mw in ln.mw_by_hour.items():
            bucket[h] = bucket.get(h, 0.0) + float(mw)
    return groups


def is_active(mw_by_hour):
    """Whether a split line actually carries any MW — an untouched extra
    split slot (added via "+ Split" and never filled in) carries none, and
    is silently dropped rather than demanding a code and price for a line
    that would bid zero everything anyway."""
    return any(mw for mw in (mw_by_hour or {}).values())


def blank_line(side, mw_by_hour=None):
    """A bid line with nothing filled in but the side's default price."""
    return {
        "code": "",
        "price": DEFAULT_PRICE[side],
        "mw_by_hour": dict(mw_by_hour or {}),
    }


def rebalance_hour(values, edited_index, value, total):
    """One hour's MW across a counterparty's split lines, after the trader
    typed `value` into line `edited_index`.

    `values` is what each line carries for that hour now; the return is the
    same list with the edit applied and the *other* lines absorbing the
    difference, so the hour still totals `total` — what the underlying
    trades actually call for. That's the whole point of a split: it changes
    *how* a position is bid, never *how much*, so a trader should only ever
    have to type one side of it.

    The residual spreads across the other lines in proportion to what they
    already carry (with two lines — the common case — that just means the
    other one takes the rest), and lands entirely on the first of them when
    they're all still empty, which is the state a fresh "+ Split" leaves.

    An entry larger than the day's own MW is *not* trimmed back: the other
    lines go to zero and `validate_split` reports the overrun, rather than
    silently rewriting a number the trader just typed.
    """
    out = [max(0.0, float(v or 0.0)) for v in values]
    out[edited_index] = max(0.0, float(value or 0.0))
    others = [i for i in range(len(out)) if i != edited_index]
    if not others:
        return out

    residual = round(float(total) - out[edited_index], 4)
    if residual <= 0:
        for i in others:
            out[i] = 0.0
        return out

    carried = sum(out[i] for i in others)
    if carried <= 0:
        out[others[0]] = residual
        return out
    for i in others:
        out[i] = round(out[i] * residual / carried, 4)
    # Any rounding remainder goes on the last line so the hour still adds
    # up exactly, rather than leaving a 0.0001 that reads as a real error.
    out[others[-1]] = round(out[others[-1]] + residual - sum(out[i] for i in others), 4)
    return out


def split_mismatches(group_mw_by_hour, lines):
    """{hour: (allocated, expected)} for every hour a counterparty's active
    splits don't add back up to the schedule underneath them.

    Separate from `validate_split` so the same arithmetic can be shown
    *where* it went wrong — the grid tints those hours — as well as said in
    words underneath.
    """
    active = [ln for ln in lines if is_active(ln.get("mw_by_hour"))]
    if not active:
        return {}

    bad = {}
    all_hours = set(group_mw_by_hour) | {h for ln in active for h in ln["mw_by_hour"]}
    for h in sorted(all_hours):
        allocated = sum(ln["mw_by_hour"].get(h, 0.0) for ln in active)
        expected = group_mw_by_hour.get(h, 0.0)
        if abs(allocated - expected) > 0.01:
            bad[h] = (allocated, expected)
    return bad


def validate_split(side, pse, group_mw_by_hour, lines):
    """Errors for one counterparty's split, or [] if it's ready to bid.

    `lines` is that counterparty's edited rows: dicts with "code", "price",
    "mw_by_hour". Every *active* line (see is_active) needs a code and a
    price; and across all active lines, every hour must add back up to
    exactly what the underlying schedule carries — a split narrows *how*
    the position is bid, never *how much*.
    """
    errors = []
    active = [ln for ln in lines if is_active(ln.get("mw_by_hour"))]
    if not active:
        return errors  # nothing to bid for this counterparty; fine, skip it

    for ln in active:
        if not (ln.get("code") or "").strip():
            errors.append(f"{pse} ({side}): every split needs a GCA/LCA code.")
        if ln.get("price") is None:
            errors.append(f"{pse} ({side}): every split needs a price.")

    for h, (allocated, expected) in split_mismatches(group_mw_by_hour, lines).items():
        errors.append(
            f"{pse} ({side}) HE{h}: splits total {allocated:g} MW, the "
            f"schedule calls for {expected:g} MW."
        )
    return errors


def build_bid_lines(groups, split_state):
    """(short_lines, long_lines, errors) ready for data.bidfiles.swpw.

    `groups` is market_groups()'s output. `split_state` is
    {(side, pse): [{"code", "price", "mw_by_hour"}, ...]} — a counterparty
    absent from it (never split) is treated as one line covering its whole
    aggregate, code and price still to be filled in.

    A line is only included when *both* it's active (is_active) and it
    passed validation — a line reported as an error never silently makes it
    into the file half-filled.
    """
    short_lines, long_lines, errors = [], [], []
    for (side, pse), agg in groups.items():
        lines = split_state.get((side, pse)) or [
            {"code": "", "price": None, "mw_by_hour": dict(agg)}
        ]
        errors += validate_split(side, pse, agg, lines)
        for ln in lines:
            if not is_active(ln.get("mw_by_hour")):
                continue
            code = (ln.get("code") or "").strip()
            if not code or ln.get("price") is None:
                continue  # already reported above; never emit a broken line
            entry = {
                "pse": pse,
                "code": code,
                "price": float(ln["price"]),
                "mw_by_hour": dict(ln["mw_by_hour"]),
            }
            (short_lines if side == SHORT else long_lines).append(entry)

    if not errors and not short_lines and not long_lines:
        errors.append("No open position through this market — nothing to bid.")
    return short_lines, long_lines, errors
