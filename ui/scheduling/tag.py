"""The tag half of the link popup: the fields a bilateral e-Tag needs that
the board can't know, the button that writes the workbook, and the path
string the desk trades on chat.

Three surfaces, deliberately unequal. The **path string**
(`render_path_string`) sits in the open, directly under the hours, because on
most links it is the whole job: MAG doesn't have to tag them, and all the desk
owes the other schedulers is a path that gets a little longer each round.
Right under it, **"Lookup old tags"** (`render_tag_lookup`) turns the two
counterparties a link already names — no typing required — into the routes
the desk has actually run between them, each one a button of its own: a
guess the trader presses, never one the page presses for them. The **tag
fields** (`render_tag`) go behind an expander below both, because they only
matter on the links MAG does have to tag — still one popup and one train of
thought, but the common case is no longer buried under the rare one.

Everything the link already answers — which hours, at what MW, on which
flow date — is filled in from it and shown, not asked for. What's left is
the path: the control areas, the PSE chain, the transmission reservations
and who gets a copy. The PSE codes themselves come from the desk's own
market mapping (`data.markets`), never from the trader.

Two details are load-bearing and easy to undo by accident:

**The seed of each table can't be the table's own output.** `st.data_editor`
derives its element id from a hash of the data it is handed, so feeding the
edited frame back in renames the widget and the *next* edit — addressed to
the old name — is dropped. That's the "every value has to be typed twice"
bug from the schedule grid, and the fix is the same: a seed that doesn't
move while the trader types (`_table`).

**The tag itself lives in plain session state, not in the widgets.**
Streamlit discards a widget's state on any run that doesn't render it, and
stepping over to Add Trade is exactly such a run — so every field is copied
into the tag dict as it's read, and the widgets are re-seeded from it when
they come back empty.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import data.tagfiles.bilateral as tagfile
from data.markets import known_pse_codes
from data.reservations import reservation
from data.tags_history import load_west_tags
from domain.tag_recipes import (
    TRANSMISSION,
    group_by_recipe,
    rank_recipes,
    tag_query,
)
from domain.tags import (
    CC_TYPES,
    PRODUCTS,
    lookupable_aref,
    reservation_covers,
    tag_errors,
)
from ui.scheduling.state import LINK_DIALOG, arm_dialog, ensure_tag
from ui.session import widget_defaults

#: Height of each list editor, in pixels: its header plus four rows — one
#: more than an ordinary market path needs, so there's always a blank row
#: to type into without the table growing under the one below it.
TABLE_HEIGHT = 178

#: How far back "Lookup old tags" searches. A rolling window rather than a
#: fixed start — the same one the reservation lookup's own 92%/median-one-day
#: and the route-repetition figures (87% market path, 43% whole route) were
#: measured over, and recent enough that a route the desk has moved on from
#: doesn't crowd out the ones it still runs.
HISTORY_WINDOW_DAYS = 365

#: How many historical routes "Lookup old tags" shows. Every one is a button
#: a trader has to read before pressing, so more than a handful defeats the
#: point of a shortlist — and each one is four lines, so the list is what
#: decides whether the popup fits on a screen.
LOOKUP_LIMIT = 4


def _key(link_id, name):
    return f"mv_tag_{link_id}_{name}"


def _text(container, part, field, label, link_id, name, placeholder=None):
    """One text field, read into the tag as it's collected.

    Keyed through _widget_key rather than _key directly, like a table — most
    callers never bump their field, so this changes nothing for them, but it
    lets _apply_recipe force one open to a fresh default the same proven way
    a table does. Popping the same key instead does not reliably do it: a
    real browser's already-mounted text input doesn't necessarily take a new
    server-sent default without its key actually changing, even though the
    bare server-side check this was first tried against can't see that.
    """
    key = _widget_key(link_id, name)
    value = container.text_input(
        label,
        key=key,
        placeholder=placeholder,
        **widget_defaults(key, value=part.get(field, "")),
    )
    part[field] = value or ""


def _table(container, link_id, name, rows, columns, column_config):
    """One dynamic-row editor, seeded once and read every run.

    The seed is rebuilt only when the editor's own state is missing — its
    first render, the first one after a page navigation threw it away, or
    after `_bump` renamed the widget. So it never moves under a trader
    mid-edit. See the module docstring.

    Rendered on `container`, never on the bare `st`: a data editor written
    to the page while a column is open lands *under* the columns at full
    width, which is how the market path and the carbon copy ended up
    stacked instead of side by side.
    """
    seed_key, widget_key = _key(link_id, f"{name}_seed"), _widget_key(link_id, name)
    if seed_key not in st.session_state or widget_key not in st.session_state:
        st.session_state[seed_key] = pd.DataFrame(rows or [], columns=list(columns))
    edited = container.data_editor(
        st.session_state[seed_key],
        key=widget_key,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        height=TABLE_HEIGHT,
        column_config=column_config,
    )
    return edited.to_dict("records")


def _widget_key(link_id, name):
    version = st.session_state.get(_key(link_id, f"{name}_ver"), 0)
    return _key(link_id, name) + (f"_v{version}" if version else "")


def _bump(link_id, name):
    """Give a table a fresh widget, so a change made in Python shows up.

    A data editor folds its own accumulated edits over whatever it is
    seeded with, so re-seeding alone would leave the trader's older diff
    sitting on top of the new rows. Renaming the widget starts it clean
    instead — which is also why this is a rename rather than deleting the
    old widget's state: that state belongs to a widget still on screen for
    the rest of this run, and deleting it is what breaks the tag form (see
    ui.scheduling.state.clear_tag_widgets).
    """
    key = _key(link_id, f"{name}_ver")
    st.session_state[key] = st.session_state.get(key, 0) + 1


def _cell(value):
    """A table cell as a string. A dynamic editor hands back None for a
    cell never touched and NaN for a row added and left alone."""
    if value is None or value != value:
        return ""
    return str(value).strip()


def _render_half(container, part, link_id, side, ca_field, ca_label, point_label):
    """One half of the sheet. No PSE field: that end's PSE is the end of the
    market path — see domain.tags.source_pse.

    Stacked rather than paired two to a row: the two halves already sit
    side by side inside the popup's tag column, and Streamlit allows
    columns only one level deep — a row in here would be the second.
    """
    container.markdown(f"**{side}**")
    _text(container, part, "market", "Market", link_id, f"{side}_market")
    _text(container, part, ca_field, ca_label, link_id, f"{side}_{ca_field}")
    _text(container, part, "point", point_label, link_id, f"{side}_point")
    _text(container, part, "contract", "Contract", link_id, f"{side}_contract")
    _text(container, part, "comment", "PSE comment", link_id, f"{side}_comment")


def _render_generate(container, tag, link_id):
    errors = tag_errors(tag)
    if errors:
        # One box rather than one per message, as in the bid-file builder:
        # most of these are "not filled in yet" on a tag just opened.
        container.error("\n".join(f"- {error}" for error in errors))

    path = tagfile.target_path(tag)
    container.caption(
        f"Writes `{path.name}` to `{path.parent}`"
        + ("  ·  test folder, not the day's own" if tagfile.TEST_MODE else "")
    )

    left, right = container.columns([1, 1], vertical_alignment="center")
    overwrite = left.checkbox(
        "Overwrite if it exists", key=_key(link_id, "overwrite")
    )
    if right.button(
        "Generate tag file",
        type="primary",
        width="stretch",
        disabled=bool(errors),
        key=_key(link_id, "generate"),
    ):
        try:
            written = tagfile.write_tag_file(tag, overwrite=overwrite)
        except FileExistsError:
            container.error(
                f"{path.name} already exists. Give this tag another name, or "
                "tick Overwrite."
            )
        except Exception as e:
            container.error(f"Could not write the tag file: {e}")
        else:
            container.success(f"Tag written to {written}")


def _fill_paths_from_oasis(tag, link_id):
    """Fill each transmission row's Path from the reservation its number
    names. Returns (resolved, unknown, changed).

    Only the number is typed. `PathName` in the desk's OASIS summary is
    character-for-character the string the tag sheet's Path column carries,
    so once a reference resolves there is nothing left to copy across by
    hand — and a path nobody typed is a path nobody can mistype.

    Three things it deliberately will not do:

    - **Touch a path the scheduler wrote.** Only a blank cell is filled, or
      one this lookup itself put there for a reference since changed. What
      was typed by hand stays.
    - **Ask about anything that isn't a reference.** `FCATBTEP` and `GF` go
      in the same column and are left alone — see domain.tags.lookupable_aref.
    - **Treat "not found" as a problem.** The summary holds *our* reservations;
      a segment wheeled on a counterparty's is not in it, which is normal on
      about three references in ten.
    """
    ours = st.session_state.setdefault(_key(link_id, "oasis_paths"), {})
    resolved, unknown, changed = [], [], False

    # A circuit breaker, not a rule. Each fill asks for a rerun, so anything
    # that made a fill repeat would spin the page rather than fail — and a
    # spinning browser is the worst way for this to go wrong. The rename in
    # _bump is what actually makes a fill settle (take it out and this does
    # loop, which is why it is not merely a nicety); this bounds the damage
    # if that ever stops being true. A tag has at most thirteen transmission
    # rows, so it cannot bite in use.
    fills = st.session_state.get(_key(link_id, "oasis_fills"), 0)
    if fills > 40:
        return resolved, unknown, False

    for row in tag["transmissions"]:
        aref, path = _cell(row.get("reservation")), _cell(row.get("path"))
        if not lookupable_aref(aref):
            continue
        found = reservation(aref)
        if found is None:
            unknown.append(aref)
            continue
        resolved.append(found)
        if path and path not in ours.values():
            continue  # the scheduler's own wording wins
        if path != found["path"]:
            row["path"] = found["path"]
            changed = True
        ours[aref] = found["path"]

    if changed:
        st.session_state[_key(link_id, "oasis_fills")] = fills + 1
    return resolved, unknown, changed


def _render_oasis_note(container, resolved, unknown):
    """What the lookup found, in one line each — the capacity especially,
    since tagging more MW than the reservation grants is the kind of error
    that only surfaces days later."""
    for found in resolved:
        granted = f"{found['granted_mw']:,.0f} MW" if found["granted_mw"] else "—"
        container.caption(
            f"`{found['aref']}` · {found['provider']} · {found['ts_class']} · "
            f"{found['status'].lower()} · {granted} granted · path filled from OASIS"
        )
    for aref in unknown:
        container.caption(
            f"`{aref}` isn't one of our own OASIS reservations — a "
            "counterparty's, or a typo. Path left for you."
        )


def _pse_column():
    """The market path's PSE cell.

    A dropdown of every code the desk's mapping table knows plus every code
    its own tags have used — the mapping alone covers 87% of what the
    history actually contains, so offering only that would refuse codes
    written every week. Falls back to free text if neither lookup can be
    read, since a database outage shouldn't make the column unusable.
    """
    codes = known_pse_codes()
    if not codes:
        return st.column_config.TextColumn("PSE", width="small")
    return st.column_config.SelectboxColumn("PSE", options=codes, width="small")


def _take_path_entry(tag, link_id):
    """Move whatever was pasted into the entry box onto the tag, and hand the
    box a fresh, empty widget.

    Called *before* the path is displayed, so the display shows what was just
    pasted rather than the previous round's string — reading a widget's state
    at the top of the run is what makes that possible without a rerun.

    The box clears itself because the path is a conversation: each round you
    paste what came back from the other scheduler, and a box still holding the
    last round's text is one you have to empty before you can use it again.
    Clearing is a rename (`_bump`), never a delete — the widget is about to be
    on screen for the rest of this run.
    """
    key = _widget_key(link_id, "path_entry")
    pasted = str(st.session_state.get(key) or "").strip()
    if not pasted:
        return False
    tag["path_string"] = pasted
    _bump(link_id, "path_entry")
    return True


def render_path_string(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date):
    """The path as it stands on chat, and the box to paste the next one into.

    Outside the tag expander on purpose: see the module docstring. The string
    starts as the only part anyone knows yet — `??-ABEX-MAG001-SWPW-??`, the
    two counterparties either side of MAG — and from there it is whatever the
    back-and-forth has produced. Nothing here parses it; it is displayed,
    copied and replaced.
    """
    tag = ensure_tag(tag_key, buy_leg, sell_leg, mw_by_hour, flow_date)
    link_id = tag_key
    _take_path_entry(tag, link_id)

    container.markdown("**Path string** — the latest one, to copy into the chat")
    # st.code rather than st.text: it carries Streamlit's own copy button,
    # it doesn't let the string be edited in place (the box below is the one
    # way to change it), and it wraps instead of truncating a long path.
    container.code(tag.get("path_string") or "", language=None, wrap_lines=True)

    key = _widget_key(link_id, "path_entry")
    container.text_input(
        "Paste the path back",
        key=key,
        placeholder="Paste what came back from the scheduler chat",
        label_visibility="collapsed",
        **widget_defaults(key, value=""),
    )


def _apply_recipe(tag, link_id, match):
    """Copy one historical route onto this tag: its control areas, its
    source/sink points, its market path and its transmission reservation
    numbers — which is already enough for the OASIS lookup further down
    render_tag to fill their paths on its own next pass (see
    _fill_paths_from_oasis).

    Deliberately not touched: the tag's name and header, its carbon copy
    (OATI carries none), and source/sink contract — nothing in the corpus
    maps to any of them.

    No rerun is needed here, unlike the OASIS fill. render_tag_lookup runs
    before render_tag's own field and table calls in the popup's own
    top-to-bottom order (see links.py), so every bumped widget and the
    mutated tag are both already in place by the time those fields draw
    themselves later in this same run — the mutation lands before the
    paint, not after it.
    """
    sample = match["samples"][0] if match["samples"] else None
    if sample is None:
        return

    if match["gca"]:
        tag["source"]["gca"] = match["gca"]
        _bump(link_id, "Source_gca")
    if match["lca"]:
        tag["sink"]["lca"] = match["lca"]
        _bump(link_id, "Sink_lca")
    for segment in sample["physical_path"]:
        if segment["type"] == "G" and segment.get("por_name"):
            tag["source"]["point"] = segment["por_name"]
            _bump(link_id, "Source_point")
        elif segment["type"] == "L" and segment.get("pod_name"):
            tag["sink"]["point"] = segment["pod_name"]
            _bump(link_id, "Sink_point")

    tag["market_path"] = [
        {
            "pse": row["pse"],
            "product": row["product"],
            "contract": row.get("contracts", ""),
        }
        for row in sample["market_path"]
    ]
    tag["transmissions"], note = _carry_reservations(sample, tag.get("flow_date"))
    _bump(link_id, "market_path")
    _bump(link_id, "transmissions")
    return note


def _carry_reservations(sample, flow_date):
    """The transmission rows a historical route contributes to this tag,
    and one line on what it deliberately left blank.

    A reservation number belongs to a *day*, not to a route: 92% of the
    desk's own references appear on exactly one. So a number is only
    carried forward when it is still good — which splits three ways:

    - **Not a reference at all** (`EPE5PVPV`, a bare contract number):
      carried as-is. These aren't dated, so there is nothing to expire.
    - **A reference OASIS still covers on the flow date**: carried, and the
      lookup in render_tag fills its path from it on this same run.
    - **Anything else** — expired, or a counterparty's and so unverifiable:
      nothing is carried at all, not even the row. An empty cell is
      something a scheduler notices; a stale number is something they have
      to catch.

    **A path is never carried, only ever looked up.** The Path column is
    filled from one place, OASIS, keyed on the number beside it
    (_fill_paths_from_oasis) — so a path on screen always has a reference
    standing behind it that a scheduler can check. Copying one off an old
    route would break that: it would read exactly like a path this day's
    reservation justifies, with nothing behind it. The reverse is fine and
    expected — a reference OASIS can't answer for sits there with its path
    blank.
    """
    rows, dropped, unknown = [], 0, 0
    for segment in sample["physical_path"]:
        if segment["type"] != TRANSMISSION:
            continue
        for res in segment["reservations"]:
            number = (res.get("contract") or "").strip()
            if not number:
                continue
            if not lookupable_aref(number):
                rows.append({"path": "", "reservation": number, "mw": ""})
                continue
            found = reservation(number)
            if found is None:
                unknown += 1
            elif reservation_covers(found, flow_date):
                rows.append({"path": "", "reservation": number, "mw": ""})
            else:
                dropped += 1
    return rows, _carried_note(len(rows), dropped, unknown, flow_date)


def _carried_note(kept, dropped, unknown, flow_date):
    parts = [f"{kept} transmission row{'' if kept == 1 else 's'} from that route"]
    if dropped:
        parts.append(
            f"{dropped} reservation{'' if dropped == 1 else 's'} not carried — "
            f"not good for {flow_date}"
        )
    if unknown:
        parts.append(
            f"{unknown} not carried — not ours to check"
        )
    return " · ".join(parts)


def render_tag_lookup(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date):
    """"Lookup old tags" — what the desk has tagged before between these
    same counterparties, read from OATI (data.tags_history) and grouped
    into routes (domain.tag_recipes).

    Means something before anything is typed: the market path's two ends
    are the link's own counterparties the moment the popup opens (see
    domain.tags.default_market_path), and tag_query reads those straight
    off the tag — no path string involved, and none is read here. Filling
    in GCA/LCA afterwards only sharpens the search, and a *market* end
    settles its control area outright (tag_query again).

    Never a fill. Every match is its own "Use this route" button — a guess
    the trader presses, not one the page presses for them (see
    rank_recipes's own docstring for why a shared counterparty and a
    market end's control area are required rather than merely preferred).
    """
    tag = ensure_tag(tag_key, buy_leg, sell_leg, mw_by_hour, flow_date)
    link_id = tag_key

    if container.button("Lookup old tags", key=_key(link_id, "lookup_btn")):
        stop, start = date.today(), date.today() - timedelta(days=HISTORY_WINDOW_DAYS)
        records, error = load_west_tags(start, stop)
        st.session_state[_key(link_id, "lookup_error")] = error
        st.session_state[_key(link_id, "lookup_results")] = (
            [] if error else rank_recipes(
                group_by_recipe(records),
                tag_query(tag, buy_leg, sell_leg),
                limit=LOOKUP_LIMIT,
            )
        )
        st.session_state[_key(link_id, "lookup_done")] = True
        st.session_state[_key(link_id, "lookup_note")] = None

    if not st.session_state.get(_key(link_id, "lookup_done")):
        return

    error = st.session_state.get(_key(link_id, "lookup_error"))
    if error:
        container.warning(error)
        return

    matches = st.session_state.get(_key(link_id, "lookup_results")) or []
    if not matches:
        container.caption(
            "No historical tag shares a counterparty with this link yet."
        )
        return

    for match in matches:
        with container.container(border=True):
            cols = st.columns([5, 2], vertical_alignment="center")
            _render_match(cols[0], match)
            if cols[1].button(
                "Use this route", key=_key(link_id, f"use_{match['recipe_id']}")
            ):
                st.session_state[_key(link_id, "lookup_note")] = _apply_recipe(
                    tag, link_id, match
                )
                st.session_state[_key(link_id, "expander_open")] = True

    note = st.session_state.get(_key(link_id, "lookup_note"))
    if note:
        container.caption(note)


def _render_match(container, match):
    """One historical route, read top-down the way a scheduler checks one:
    where the power starts and ends, who's in the chain, which wires carry
    it, then how often this desk has actually run it.

    Every line is there because it separates one recipe from another. Two
    cards that read alike are two a scheduler can't choose between, so
    anything the signature groups on has to be on screen — the PSE chain's
    energy products and each wheel's transmission product especially, since
    those are the commonest thing two otherwise-identical routes differ in.
    """
    source, sink = match["endpoints"]
    container.markdown(
        f"**{source or '—'} → {sink or '—'}**  ·  "
        f"{match['gca'] or '—'}>{match['lca'] or '—'}"
    )
    container.caption(match["chain"] or "—")
    container.caption(
        match["wires"] or "no wheel — generated and delivered in one area"
    )
    container.caption(
        f"used {match['count']}× · last {match['last_flow_date']} · "
        f"{', '.join(match['counterparties']) or '—'}"
    )


def recipe_was_applied(tag_key):
    """Whether "Use this route" has filled this tag in at some point during
    this popup's current lifetime.

    Read, never popped: a dialog's body runs more than once per click (see
    ui.scheduling.state.dialog_was_dismissed's own docstring on the same
    quirk), so a one-shot "consume this flag" flash-message pattern sees it
    on the first pass and finds it already gone on the second — which is
    exactly backwards, since the *second* pass is the one whose render
    actually reaches the screen. A plain, un-popped flag survives both
    passes alike. It's cleared the ordinary way, with every other
    `mv_tag_`-prefixed key, when a popup next opens (clear_tag_widgets) —
    so it means nothing to a *different* link, or to this one revisited
    later.
    """
    return bool(st.session_state.get(_key(tag_key, "expander_open")))


def render_tag(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date, he_label):
    """The tag fields, in the right-hand half of the popup. The link
    supplies the schedule; everything collected here is written straight
    into its tag as it's typed.

    Keyed rather than given a Link, so a link still being drawn can carry a
    tag too — see ui.scheduling.state.PENDING_TAG.

    Everything renders on `container`, never on the bare `st`: this half of
    the popup is a column, and a widget written to the page instead lands
    underneath both columns at full width.
    """
    tag = ensure_tag(tag_key, buy_leg, sell_leg, mw_by_hour, flow_date)
    link_id = tag_key

    head = container.columns([2, 2, 3], vertical_alignment="bottom")
    _text(head[0], tag, "name", "Tag name", link_id, "name")
    _text(head[1], tag, "label", "Header", link_id, "label")
    head[2].caption(
        f"{flow_date} · HE {he_label} · {sum(mw_by_hour.values()):,.0f} MWh, "
        "from the schedule above — both halves of the sheet carry it."
    )

    source, sink = container.columns(2)
    _render_half(source, tag["source"], link_id, "Source", "gca", "GCA", "Source")
    _render_half(sink, tag["sink"], link_id, "Sink", "lca", "LCA", "Sink")

    path_col, cc_col = container.columns([3, 2])
    path_col.markdown("**Market path** — generator first, load last")
    tag["market_path"] = _table(
        path_col,
        link_id,
        "market_path",
        tag["market_path"],
        ("pse", "product", "contract"),
        {
            "pse": _pse_column(),
            "product": st.column_config.SelectboxColumn(
                "Product", options=[p for p in PRODUCTS if p], width="small"
            ),
            "contract": st.column_config.TextColumn("Contract"),
        },
    )
    path_col.caption(
        "The sheet's two PSE cells are this path's own ends — nothing to "
        "type twice."
    )

    cc_col.markdown("**Carbon copy**")
    tag["carbon_copy"] = _table(
        cc_col,
        link_id,
        "carbon_copy",
        tag["carbon_copy"],
        ("type", "market"),
        {
            "type": st.column_config.SelectboxColumn(
                "Type", options=[t for t in CC_TYPES if t], width="small"
            ),
            "market": st.column_config.TextColumn("Market", width="small"),
        },
    )

    container.markdown(
        "**Transmission** — type the reservation, the path follows"
    )
    tag["transmissions"] = _table(
        container,
        link_id,
        "transmissions",
        tag["transmissions"],
        ("path", "reservation", "mw"),
        {
            "path": st.column_config.TextColumn("Path", width="large"),
            "reservation": st.column_config.TextColumn("# trans", width="small"),
            "mw": st.column_config.TextColumn("MW", width="small"),
        },
    )
    resolved, unknown, changed = _fill_paths_from_oasis(tag, link_id)
    if changed:
        # A fresh widget so the filled path is what the editor shows — see
        # _bump. Only ever on a run that actually changed something, so this
        # can't loop: the next one finds the path already there.
        _bump(link_id, "transmissions")
        arm_dialog(LINK_DIALOG)
        st.rerun()
    _render_oasis_note(container, resolved, unknown)

    container.divider()
    _render_generate(container, tag, link_id)
