"""Turning the desk's tag history into routes it repeats.

`domain/tags.py` is about the tag the app *builds* — a mutable dict edited
field by field, on its way out to a workbook. This is the other direction:
an immutable record read back out of OATI, reduced to a grouping key, so
that 6,951 tags become the few hundred routes the desk actually runs.

A **recipe signature** is that key: a single line that is identical for two
tags following the same route and different for two that don't. It carries
the control areas, the PSE chain with its energy products, the physical path,
and the transmission product on each segment. It deliberately leaves out
reservation numbers, times and MW — those are what changes every day, and
folding them in would make every tag its own recipe.

Every normalization below is a decision about which two tags count as the
same route, which is why each one is written down rather than left to the
reader of the code.
"""

import hashlib
from datetime import date

from domain.tags import MAG_PSE, control_area_for_market, market_path_rows

#: Bump when the format changes, so two corpora can't silently be compared
#: as though they were built the same way.
#:
#: 2: SWPP's interchangeable delivery points fold together (EQUIVALENT_POINTS).
#: 3: the transmission product no longer separates routes.
SIGNATURE_VERSION = 3

#: What an empty field renders as. Never omitted: a blank energy product
#: marks a wheel-through, and dropping it would collapse a three-party chain
#: onto a two-party one.
BLANK = "-"

#: Segment types, as OATI_TagPS spells them.
GENERATION, TRANSMISSION, LOAD = "G", "T", "L"

#: Earlier than any flow date — the sort sentinel for a record whose start
#: time OATI left null.
_EPOCH = date.min

SECTION = " || "
MARKET_STEP = " > "

#: Between two wheels of a route shown on screen. Not the signature's own
#: separator: this one is only ever read, never compared.
WIRE_STEP = " · "
PHYSICAL_STEP = " ; "


def _norm(value):
    """Stripped and upper-cased. OATI's codes are already upper at source;
    folding case guards against drift, at the cost of merging two names that
    differ only in case — which does not happen in this data."""
    text = (value or "").strip().upper()
    return text or BLANK


#: Points that are one place, keyed by the control area they're in.
#:
#: SWPP delivers into SWPW at CRSP, WAUW, TSGT and CSU, and the desk treats
#: those as the same delivery — so a route through one is not a different
#: route from the same route through another, and splitting them only buries
#: how often a route has really been run (CRSP 1,791 segments in a year,
#: WAUW 720, CSU 12, TSGT 4).
#:
#: Keyed by control area because a name is only unique inside one: WAUW is
#: also a control area of its own, with a load point of the same name — 69
#: of those in a year — and that one is a real destination, not this.
EQUIVALENT_POINTS = {
    "SWPP": {"CRSP": "SWPP-INTO-SWPW", "WAUW": "SWPP-INTO-SWPW",
             "TSGT": "SWPP-INTO-SWPW", "CSU": "SWPP-INTO-SWPW"},
}


def canonical_point(name, control_area):
    """One name for points that are the same place — see EQUIVALENT_POINTS.

    Only the *signature* folds them: a route is shown by the real point its
    latest tag actually used, because that is a place a scheduler can go and
    check, and this is not.
    """
    name, area = _norm(name), _norm(control_area)
    return EQUIVALENT_POINTS.get(area, {}).get(name, name)


def market_step(segment):
    """'RRWE01:G-F' — one link of the PSE chain."""
    return f"{_norm(segment.get('pse'))}:{_norm(segment.get('product'))}"


def physical_step(segment):
    """One segment of the physical path, by type: a generation or load
    point in its control area, or a provider and the POR>POD pair it
    wheels between.

    **The transmission product is deliberately not here.** Whether a leg
    was bought firm or non-firm is a property of the day's paperwork, not
    of the route — the same wires between the same points are the same
    route however that day's capacity happened to be bought, and splitting
    on it only buried how often a route had really been run. The *energy*
    product does separate routes, and lives on the market path instead
    (market_step) where it belongs.
    """
    kind = _norm(segment.get("type"))
    ms = segment.get("ms_index")
    por_ca, pod_ca = _norm(segment.get("por_ca")), _norm(segment.get("pod_ca"))
    por = canonical_point(segment.get("por_name"), por_ca)
    pod = canonical_point(segment.get("pod_name"), pod_ca)
    if kind == GENERATION:
        return f"{ms}:G {por}({por_ca})"
    if kind == LOAD:
        return f"{ms}:L {pod}({pod_ca})"
    return f"{ms}:{kind} {_norm(segment.get('tp'))} {por}({por_ca})>{pod}({pod_ca})"


def recipe_signature(record):
    """The canonical route string for one assembled tag.

    Identical for two tags that differ only in date, MW and reservation
    number. See the module docstring for what it does and does not carry.
    """
    chain = MARKET_STEP.join(
        market_step(step) for step in record.get("market_path") or []
    )
    physical = PHYSICAL_STEP.join(
        physical_step(step) for step in record.get("physical_path") or []
    )
    return SECTION.join(
        [
            f"{_norm(record.get('gca'))}>{_norm(record.get('lca'))}",
            f"MS {chain}" if chain else "MS -",
            f"PS {physical}" if physical else "PS -",
        ]
    )


def recipe_id(signature):
    """A short stable handle for a signature — a file name and a join key,
    not a security boundary."""
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]


def counterparties_of(record, mag_pse=MAG_PSE):
    """The PSEs MAG stands next to in the market path.

    Not "the counterparty", which has no answer here: across the desk's 2026
    West tags MAG is last in 34% of chains, in the middle in 31%, first in
    19%, and appears *more than once* in 15% — a wheel such as
    `RRWE01 > MSCG01 > MAG001 > MSCG01 > AESO`. Two thirds of tags have one
    neighbour and a third have two, so this answers with a sorted tuple and
    lets the caller decide.

    Empty when MAG isn't on the path at all, which is a real case rather
    than a bug: OATI holds tags MAG merely observed as a control area.
    """
    chain = [step.get("pse", "") for step in record.get("market_path") or []]
    neighbours = set()
    for i, pse in enumerate(chain):
        if pse != mag_pse:
            continue
        if i > 0 and chain[i - 1] != mag_pse:
            neighbours.add(chain[i - 1])
        if i + 1 < len(chain) and chain[i + 1] != mag_pse:
            neighbours.add(chain[i + 1])
    return tuple(sorted(neighbours))


def transmission_providers(record):
    """The TP codes a route wheels over, in path order, without repeats —
    'MATL > NWMT > SWPP'."""
    seen = []
    for step in record.get("physical_path") or []:
        tp = (step.get("tp") or "").strip()
        if tp and tp not in seen:
            seen.append(tp)
    return tuple(seen)


def route_summary(record):
    """'GWA>SWPW via MATL>NWMT>SWPP' — a one-line human label. Lossy on
    purpose, and never a grouping key."""
    providers = transmission_providers(record)
    route = f"{record.get('gca') or BLANK}>{record.get('lca') or BLANK}"
    return f"{route} via {'>'.join(providers)}" if providers else route


def endpoints_of(record):
    """('RIMROCK (GWA)', 'SWPW_HUB (SWPW)') — where a route physically
    starts and ends, which is what a scheduler recognises a route by. The
    control areas alone (route_summary) don't distinguish two routes out of
    the same area by different plants."""
    source = sink = ""
    for segment in record.get("physical_path") or []:
        kind = _norm(segment.get("type"))
        if kind == GENERATION and not source:
            source = _point(segment.get("por_name"), segment.get("por_ca"))
        elif kind == LOAD:
            sink = _point(segment.get("pod_name"), segment.get("pod_ca"))
    return source, sink


def _point(name, control_area):
    name, control_area = _norm(name), _norm(control_area)
    if name == BLANK:
        return control_area if control_area != BLANK else ""
    return f"{name} ({control_area})" if control_area != BLANK else name


def chain_summary(record):
    """'RRWE01 (G-F) > ABEX > MAG001 (L)' — the PSE chain with the energy
    product each end carries.

    Shown alongside the wires because it separates recipes that are
    otherwise identical on screen: the same route bought firm and bought
    non-firm (`RRWE01:G-F` against `RRWE01:G-NF`) is two recipes, and
    without this they are two cards nobody can tell apart. 693 of the
    desk's own recipe pairs differ in nothing else.
    """
    steps = []
    for step in record.get("market_path") or []:
        pse, product = _norm(step.get("pse")), _norm(step.get("product"))
        steps.append(f"{pse} ({product})" if product != BLANK else pse)
    return MARKET_STEP.join(steps)


def wire_summary(record):
    """'MATL WWA>MATL.NWMT [7-F] · NWMT MATL.NWMT>CROSSOVER [2-NH]' — the
    wheels a route crosses, as a scheduler would describe them.

    Exact for a whole recipe rather than only for the sample it's read off:
    the physical path is part of the signature, so two tags in one recipe
    cross the same wires between the same points by construction.

    Three things are folded away, because OATI records a path in more detail
    than anyone reads it in:

    - **One reservation's legs are one wheel.** A single reservation is often
      recorded leg by leg — PALOVERDE500>PINALWEST500>VAIL345>GREENLEE345,
      all on 107305499 — and what a scheduler calls that is TEPC
      PALOVERDE500>GREENLEE345. Consecutive segments sharing a reservation
      collapse to the first POR and the last POD. Common: 2,853 runs of two
      in a year, 940 of three, 519 of four.
    - **A segment that goes nowhere isn't a wheel.** 12% of transmission
      segments have the same POR and POD (SRP PALOVERDE500>PALOVERDE500) —
      bookkeeping inside one point, nothing to read.
    - **The transmission product isn't shown**, because it no longer makes
      two routes different recipes (physical_step): it belongs to the day's
      paperwork, and the one on the newest sample would say nothing about
      the route. What *does* separate two routes is on the card — see
      chain_summary for the energy product, which is the one that counts.
    """
    hops = []
    for segment in record.get("physical_path") or []:
        if _norm(segment.get("type")) != TRANSMISSION:
            continue
        arefs = frozenset(
            _norm(r.get("contract")) for r in segment.get("reservations") or []
        )
        por = _norm(segment.get("por_name"))
        pod = _norm(segment.get("pod_name"))
        if hops and arefs and arefs == hops[-1]["arefs"]:
            # The same reservation, one leg further along.
            hops[-1]["pod"] = pod
            continue
        hops.append({
            "tp": _norm(segment.get("tp")), "por": por, "pod": pod, "arefs": arefs,
        })

    return WIRE_STEP.join(
        f"{hop['tp']} {hop['por']}>{hop['pod']}"
        for hop in hops
        if hop["por"] != hop["pod"]
    )


def group_by_recipe(records, samples=3):
    """Records folded into recipes, most used first.

    Ties break on the most recently used, so two equally common routes are
    ordered by which is still live. Samples are newest first, because a
    corpus is read to learn current practice — an example from January is
    less use than one from last week.
    """
    recipes = {}
    for record in records:
        signature = recipe_signature(record)
        recipe = recipes.get(signature)
        if recipe is None:
            recipe = recipes[signature] = {
                "recipe_id": recipe_id(signature),
                "signature": signature,
                "signature_version": SIGNATURE_VERSION,
                "summary": route_summary(record),
                "endpoints": endpoints_of(record),
                "chain": chain_summary(record),
                "wires": wire_summary(record),
                "gca": record.get("gca", ""),
                "lca": record.get("lca", ""),
                "count": 0,
                "first_flow_date": None,
                "last_flow_date": None,
                "counterparties": set(),
                "transmission_providers": transmission_providers(record),
                "_records": [],
            }
        recipe["count"] += 1
        recipe["counterparties"].update(counterparties_of(record))
        recipe["_records"].append(record)
        flow_date = record.get("flow_date")
        if flow_date:
            first, last = recipe["first_flow_date"], recipe["last_flow_date"]
            recipe["first_flow_date"] = min(first or flow_date, flow_date)
            recipe["last_flow_date"] = max(last or flow_date, flow_date)

    out = []
    for recipe in recipes.values():
        newest = sorted(
            recipe.pop("_records"),
            key=lambda r: (r.get("flow_date") or _EPOCH, r.get("tag_index") or 0),
            reverse=True,
        )
        recipe["counterparties"] = tuple(sorted(recipe["counterparties"]))
        recipe["samples"] = tuple(newest[:samples])
        out.append(recipe)

    out.sort(
        key=lambda r: (r["count"], r["last_flow_date"] or _EPOCH), reverse=True
    )
    return out


def tag_query(tag, buy_leg=None, sell_leg=None, mag_pse=MAG_PSE):
    """What's known about this link's route so far, in the shape
    rank_recipes wants: the counterparties from the market path's non-MAG
    ends, the GCA/LCA, and which of those two a market end *settles*.

    The counterparties are always present, even before anything is typed —
    the market path opens with them the moment the link exists (see
    domain.tags.default_market_path) — which is what lets "Lookup old tags"
    mean something on a link nobody has touched yet. Everything is upper-
    cased, matching recipe_signature's own normalization, so a query built
    from hand-typed text still compares equal to history read out of OATI.

    **A market end is a fact, not a hint.** A link selling into SWPW is MAG
    sinking the power there, so that tag's LCA *is* SWPW — and a route we
    once ran into CISO with the same counterparty is a different deal, not
    a weaker match for this one. Those ends go in `fixed`, and rank_recipes
    filters on them rather than merely preferring them. A GCA or LCA the
    trader typed stays a preference: it's a field still being filled in,
    and ruling every route out on a half-typed code helps nobody.
    """
    counterparties = frozenset(
        row["pse"].upper()
        for row in market_path_rows(tag)
        if row["pse"] and row["pse"].upper() != mag_pse
    )
    gca = (tag.get("source", {}).get("gca") or "").strip().upper() or None
    lca = (tag.get("sink", {}).get("lca") or "").strip().upper() or None

    fixed = set()
    for leg, name in ((buy_leg, "gca"), (sell_leg, "lca")):
        if leg is None or not getattr(leg, "is_market", False):
            continue
        settled = control_area_for_market(leg.pse)
        if not settled:
            continue
        fixed.add(name)
        if name == "gca":
            gca = settled
        else:
            lca = settled

    return {
        "gca": gca,
        "lca": lca,
        "counterparties": counterparties,
        "fixed": frozenset(fixed),
    }


def rank_recipes(recipes, query, limit=5):
    """Historical recipes worth showing for this query, best match first.

    A guess, never a fill — nothing here is written into a tag; the caller
    decides what a match means (see ui.scheduling.tag.render_tag_lookup).

    **Hard filter: at least one shared counterparty.** It's the one thing a
    query always has (see tag_query), so a recipe that shares none is a
    different deal, not a weaker match — showing it anyway because its GCA
    happens to match would be a coincidence dressed up as a suggestion.

    **Hard filter: a control area a market end settles** (`query["fixed"]`).
    A link into SWPW sinks there; a CISO route is simply not one of this
    link's options, however often we've run it with the same counterparty.

    A GCA or LCA the trader merely typed is a preference instead: it moves a
    recipe up without ruling the others out. Ties break the same way
    group_by_recipe's own list already does: more used, then more recently
    used.
    """
    counterparties = query.get("counterparties") or frozenset()
    fixed = query.get("fixed") or frozenset()

    scored = []
    for recipe in recipes:
        shared = counterparties & {c.upper() for c in recipe["counterparties"]}
        if not shared:
            continue
        score = 10 * len(shared)
        for name in ("gca", "lca"):
            wanted = query.get(name)
            if not wanted:
                continue
            if (recipe.get(name) or "").strip().upper() == wanted:
                score += 5
            elif name in fixed:
                score = None
                break
        if score is None:
            continue
        scored.append((score, recipe))

    scored.sort(
        key=lambda pair: (
            pair[0], pair[1]["count"], pair[1]["last_flow_date"] or _EPOCH
        ),
        reverse=True,
    )
    return [recipe for _, recipe in scored[:limit]]
