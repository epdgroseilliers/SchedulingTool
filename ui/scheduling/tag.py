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
from data.bilateral import hours_to_he_string
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
    DEFAULT_TAG_STATE,
    PRODUCTS,
    TAG_STATES,
    lookupable_aref,
    market_end_fields,
    path_string_line,
    reservation_covers,
    schedule_prefix,
    strip_schedule_prefix,
    split_difference,
    tag_errors,
)
from ui.scheduling.state import (
    LINK_DIALOG,
    arm_dialog,
    ensure_tag,
    tag_for,
    tag_keys_for,
)
from ui.session import widget_defaults

#: Height of each list editor, in pixels: its header plus four rows — one
#: more than an ordinary market path needs, so there's always a blank row
#: to type into without the table growing under the one below it. Rows are
#: shorter than Streamlit's default because the popup is sized to hold
#: everything at once without scrolling (see ui.scheduling.links).
ROW_HEIGHT = 28

#: Rows an editor shows when it holds fewer than that — a tag opens with
#: an empty transmission block, and a one-line slot is a worse thing to
#: type into than a short table.
MIN_TABLE_ROWS = 3

#: An editor's header row, which `row_height` doesn't govern — measured in
#: Chrome at 35px, with three more for the frame. Short by even a few and
#: the last row keeps a scrollbar, which is the whole thing this avoids.
HEADER_HEIGHT = 38

#: How far back "Lookup old tags" searches. A rolling window rather than a
#: fixed start — the same one the reservation lookup's own 92%/median-one-day
#: and the route-repetition figures (87% market path, 43% whole route) were
#: measured over, and recent enough that a route the desk has moved on from
#: doesn't crowd out the ones it still runs.
HISTORY_WINDOW_DAYS = 365

#: How many historical routes "Lookup old tags" shows at once. Every one is
#: a button a trader has to read before pressing, so more than a handful
#: defeats the point of a shortlist — and each one is four lines, so the
#: page size is what decides whether the popup fits on a screen.
LOOKUP_LIMIT = 4

#: How many it ranks behind that page, for "Next 4". Ranking is free —
#: every recipe is scored either way and `limit` only truncates the sorted
#: list — so the deeper pages cost nothing until they are asked for, and
#: asking for one re-reads nothing. The lookup itself stays as fast as it
#: was: what it costs is the OATI read and the grouping, both of which
#: happen once however many pages are kept.
LOOKUP_DEPTH = 20


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

    **A changed row count re-seeds the editor and renames it** — see
    seed_is_stale, and the trace in PROJECT.md. Without that, deleting a row
    undid itself the next time the trader touched anything.
    """
    seed_key = _key(link_id, f"{name}_seed")
    widget_key = _widget_key(link_id, name)
    seed = st.session_state.get(seed_key)
    fresh = seed is None or widget_key not in st.session_state
    if not fresh and seed_is_stale(seed, rows):
        _bump(link_id, name)
        widget_key = _widget_key(link_id, name)
        fresh = True
    if fresh:
        st.session_state[seed_key] = pd.DataFrame(rows or [], columns=list(columns))
    edited = container.data_editor(
        st.session_state[seed_key],
        key=widget_key,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        height=table_height(rows),
        row_height=ROW_HEIGHT,
        column_config=column_config,
    )
    return edited.to_dict("records")


def seed_is_stale(seed, rows):
    """Whether this editor has to be re-seeded and renamed with it.

    True exactly when the row count has moved — which is exactly when
    `table_height` moves, and **`height` is one of the properties Streamlit
    hashes into a data editor's element id**. So a row added or deleted
    renames the widget whether or not anyone asks it to, and the diff the
    trader had accumulated is addressed to a name that no longer exists.

    That is not a theory. Traced in Chrome, deleting the middle row of a
    three-row market path and then touching any other field:

        run 1  in=3  state={deleted_rows: [1]}  out=2   <- the deletion
        run 2  in=2  state={deleted_rows: [1]}  out=3   <- and back again

    On run 2 the row count had dropped to 2, so the height dropped from 150
    to 122, so the id changed, so the diff was looked up under a name that
    held nothing and the three-row seed came back untouched. Streamlit then
    wrote the empty diff over the real one, and the deletion was gone for
    good.

    Re-seeding at that moment is what makes the rename lossless: the tag
    already holds the edit, because every run reads the editor's output
    straight into it, so a clean widget over a fresh seed shows exactly
    what the trader did.

    False while a trader is only typing, which is the other half of the
    rule: a seed that moved on every keystroke would rename the widget
    mid-edit and drop the *next* edit — the "every value has to be typed
    twice" bug the module docstring is about.
    """
    return seed is None or len(seed.index) != len(rows or [])


def table_height(rows):
    """Tall enough to show every row this editor holds, with no scrollbar.

    A market path or a transmission block you have to scroll is one you
    can misread — a wheel you can't see is a wheel you forget to check —
    so the editor grows and the popup grows with it, rather than the rows
    hiding inside a fixed frame.

    What it has to fit: the header, every row, and the blank one a dynamic
    editor keeps at the bottom to add with.
    """
    shown = max(len(rows or []) + 1, MIN_TABLE_ROWS)
    return HEADER_HEIGHT + shown * ROW_HEIGHT


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


def _render_fields(container, tag, link_id):
    """Everything the sheet asks for that isn't a list, in three rows.

    Not a head row plus two blocks of five stacked fields, which is the
    same twelve boxes in nearly twice the height — the popup is sized to
    show the whole tag at once (see ui.scheduling.links), and that is what
    used to make it scroll. Each label names its own side rather than
    leaning on a heading above it.

    No PSE field on either half: that end's PSE is the end of the market
    path — see domain.tags.source_pse. And no caption repeating the flow
    date, the hours and the MW: they are on screen in the same popup, a few
    centimetres up, and the sheet carries that schedule on both halves
    whether or not a line here says so.
    """
    source, sink = tag["source"], tag["sink"]
    row = container.columns(6)
    _text(row[0], tag, "name", "Tag name", link_id, "name")
    _text(row[1], tag, "label", "Header", link_id, "label")
    _text(row[2], source, "market", "Source market", link_id, "Source_market")
    _text(row[3], source, "gca", "GCA", link_id, "Source_gca")
    _text(row[4], sink, "market", "Sink market", link_id, "Sink_market")
    _text(row[5], sink, "lca", "LCA", link_id, "Sink_lca")

    row = container.columns(4)
    _text(row[0], source, "point", "Source", link_id, "Source_point")
    _text(row[1], source, "contract", "Source contract", link_id, "Source_contract")
    _text(row[2], sink, "point", "Sink", link_id, "Sink_point")
    _text(row[3], sink, "contract", "Sink contract", link_id, "Sink_contract")

    row = container.columns(2)
    _text(row[0], source, "comment", "Source PSE comment", link_id, "Source_comment")
    _text(row[1], sink, "comment", "Sink PSE comment", link_id, "Sink_comment")


def split_errors(link_mw_by_hour, schedules):
    """Why these tags aren't this link's schedule yet - empty when they add
    up, which is the only state a tag may be written from.

    The arithmetic is domain.tags.split_difference; this is the sentence a
    trader reads, built here because the HE-range formatter it wants lives
    in data (see that function's own docstring).

    One line per direction rather than one per hour: a split is usually
    wrong across a whole block of hours at once, and twenty-four lines
    saying the same thing is not a message anyone reads. Over and short are
    kept apart because they are different mistakes - one tags MW the desk
    does not have, the other leaves some untagged.
    """
    difference = split_difference(link_mw_by_hour, schedules)
    if not difference:
        return []

    errors = []
    for hours, over in (
        ([h for h, d in difference.items() if d > 0], True),
        ([h for h, d in difference.items() if d < 0], False),
    ):
        if not hours:
            continue
        total = abs(sum(difference[h] for h in hours))
        errors.append(
            f"The tags carry {'more' if over else 'less'} than this link on "
            f"HE {hours_to_he_string(hours)} — {total:,.0f} MWh "
            f"{'too much' if over else 'short'}."
        )
    return errors


def render_generate(container, tag_key, blocked=False):
    """Generate, and the overwrite tick beside it — in the popup's own
    action row, next to Create link and Cancel.

    Down there rather than at the foot of the tag fields because it is the
    same kind of thing as the buttons it now sits with, and because the tag
    column is what decides whether the popup fits on a screen: a block that
    reads just as well in a row that was half empty costs nothing there.

    **One file, holding the tags marked ready.** A link split across
    several paths is finished a path at a time, so the trader says which
    of them are done and those go out together — which is how the desk's
    own split files read, several tags side by side in one workbook
    (data.tagfiles.bilateral). Marking one ready and generating is how a
    single tag of a split goes out on its own.

    Where the file goes is on the button's tooltip. What *stops* it being
    written stays where it can be fixed: each ready tag's own errors in
    its fields (render_tag), and a split that doesn't add up under the
    link's own schedule (`blocked`). A tag nobody marked ready is not
    checked at all — it isn't in the file.
    """
    tags = [tag_for(key) for key in tag_keys_for(tag_key)]
    tags = [tag for tag in tags if tag is not None and tag.get("ready", True)]
    link_id = tag_key

    left, right = container.columns([1, 1], vertical_alignment="center")
    overwrite = left.checkbox(
        "Overwrite if it exists", key=_key(link_id, "overwrite")
    )
    if not tags:
        right.button(
            "Generate tag file",
            type="primary",
            width="stretch",
            disabled=True,
            help="No tag of this link is marked ready.",
            key=_key(link_id, "generate"),
        )
        return

    path = tagfile.target_path(tags)
    where = f"Writes {path.name} to {path.parent}"
    if len(tags) > 1:
        where += f"  ·  {len(tags)} tags in the one file"
    if tagfile.TEST_MODE:
        where += "  ·  test folder, not the day's own"

    if right.button(
        "Generate tag file",
        type="primary",
        width="stretch",
        disabled=blocked or any(tag_errors(tag) for tag in tags),
        help=where,
        key=_key(link_id, "generate"),
    ):
        try:
            written = tagfile.write_tag_file(tags, overwrite=overwrite)
        except FileExistsError:
            container.error(
                f"{path.name} already exists. Give this tag another name, "
                "or tick Overwrite."
            )
        except Exception as e:
            container.error(f"Could not write {path.name}: {e}")
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

    - **Fill the same reference twice.** A blank Path is filled once per
      reference; empty it and it stays empty, because a cell that comes
      back on the next rerun is not a lookup being helpful, it is a lookup
      overruling someone. (Once per *reference*, not per row: no tag in the
      desk's own history — 0 of 2,685 — carries the same reservation number
      on two transmission segments, so the two amount to the same thing.)
      What it does still do is *correct* a path of its own that has gone
      stale: change the number beside one and the path follows, since
      nobody typed it and it now stands behind nothing.
    - **Ask about anything that isn't a reference.** `FCATBTEP` and `GF` go
      in the same column and are left alone — see domain.tags.lookupable_aref.
    - **Treat "not found" as a problem.** The summary holds *our* reservations;
      a segment wheeled on a counterparty's is not in it, which is normal on
      about three references in ten.
    """
    written = st.session_state.setdefault(_key(link_id, "oasis_paths"), {})
    resolved, unknown, changed = [], [], False

    # A circuit breaker, not a rule. Each fill asks for a rerun, so anything
    # that made a fill repeat would spin the page rather than fail — and a
    # spinning browser is the worst way for this to go wrong. The rename in
    # _bump is what actually makes a fill settle; this bounds the damage if
    # that ever stops being true. A tag has at most thirteen transmission
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
        if path and path not in written.values():
            continue  # the scheduler's own wording wins
        if not path and aref in written:
            continue  # we filled this one once and they emptied it
        if path != found["path"]:
            row["path"] = found["path"]
            changed = True
        written[aref] = found["path"]

    if changed:
        st.session_state[_key(link_id, "oasis_fills")] = fills + 1
    return resolved, unknown, changed


def _render_oasis_note(container, resolved, unknown):
    """What the lookup found, in one line each — the capacity especially,
    since tagging more MW than the reservation grants is the kind of error
    that only surfaces days later.

    It says what the reservation *is*, not what the lookup did with it: the
    Path cell is filled once and is the scheduler's afterwards (see
    _fill_paths_from_oasis), so a line claiming the path had just been
    filled would be wrong every run but the first.
    """
    for found in resolved:
        granted = f"{found['granted_mw']:,.0f} MW" if found["granted_mw"] else "—"
        container.caption(
            f"`{found['aref']}` · {found['provider']} · {found['ts_class']} · "
            f"{found['status'].lower()} · {granted} granted"
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
    # Only the path half is kept. What comes back from the chat carries
    # the schedule in front of it too, and this tag's own hours are the
    # ones that belong there — see domain.tags.strip_schedule_prefix.
    tag["path_string"] = strip_schedule_prefix(pasted)
    _bump(link_id, "path_entry")
    return True


def render_tag_state(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date):
    """Who still has to tag this link — MAG, the counterparty, or nobody
    because it's done.

    Up beside the who-and-where line rather than down among the tag fields,
    because it is the one thing here that is just as true of a link MAG
    never tags at all — and because it is what the board colours its line
    by, which is the whole point of asking (ui.scheduling.board). Set by
    hand: nothing on the board can work it out yet.

    One state per *link*, written onto every tag it carries: the board
    draws one line for a link however many paths it is split across, and a
    line can only be one colour.

    The rerun is deliberate and is why this doesn't just write the tag and
    carry on. A dialog is a fragment, so a widget inside it reruns only the
    dialog — the board behind it would keep the old colour until the popup
    was closed. Re-arming first is what keeps the popup open across it, the
    same way the OASIS fill does (see render_tag).
    """
    # follow_link=False: this is the link's header, and on a link split
    # across several tags the first one owns its own share of the hours —
    # the state radio has no business overwriting it with the link's total.
    tag = ensure_tag(
        tag_key, buy_leg, sell_leg, mw_by_hour, flow_date, follow_link=False
    )
    options = list(TAG_STATES)
    current = tag.get("state") or DEFAULT_TAG_STATE
    key = _widget_key(tag_key, "state")
    chosen = container.radio(
        "Who tags this link",
        options,
        # .get, not [], and the guard below to match: a state this build
        # doesn't know is something to show and ignore, not to crash on.
        format_func=lambda slug: TAG_STATES.get(slug, slug),
        horizontal=True,
        label_visibility="collapsed",
        key=key,
        **widget_defaults(
            key, index=options.index(current) if current in options else 0
        ),
    )
    if chosen in TAG_STATES and chosen != current:
        # On every tag of the link, not just the first: the board draws one
        # line per link and colours it by one state (see link_state), so
        # tags of one link disagreeing would be a state nobody can see.
        for key in tag_keys_for(tag_key) or [tag_key]:
            tag_for(key)["state"] = chosen
        arm_dialog(LINK_DIALOG)
        st.rerun()


def render_path_string(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date):
    """The path as it stands on chat, and the box to paste the next one into.

    Outside the tag expander on purpose: see the module docstring. The string
    starts as the only part anyone knows yet — `??-ABEX-MAG001-SWPW-??`, the
    two counterparties either side of MAG — and from there it is whatever the
    back-and-forth has produced. Nothing here parses it; it is displayed,
    copied and replaced.

    In front of it, cut off by a semicolon, is what the tag schedules:
    `50 HL; ??-ABEX-MAG001-SWPW-??`. A path says where the power goes
    and nothing about how much or when, and the desk's own line has
    carried both for as long as it has been a line. Only the path half is
    stored — the schedule is the link's, and asking the trader to keep a
    copy of it in a text box in step with the hour grid above is asking
    for the two to disagree.
    """
    tag = ensure_tag(tag_key, buy_leg, sell_leg, mw_by_hour, flow_date)
    link_id = tag_key
    _take_path_entry(tag, link_id)

    container.markdown("**Path string** — the latest one, to copy into the chat")
    # st.code rather than st.text: it carries Streamlit's own copy button,
    # it doesn't let the string be edited in place (the box below is the one
    # way to change it), and it wraps instead of truncating a long path.
    #
    # The schedule in front of it is worked out fresh every run rather than
    # stored with the path: it is the hours *this tag* currently carries,
    # and those move under it — a quick fill, a split, an hour typed into
    # the grid. A copy taken a moment after any of those has to say what
    # the tag now schedules, not what it did when the path was last pasted.
    container.code(
        path_string_line(schedule_prefix(mw_by_hour), tag.get("path_string")),
        language=None,
        wrap_lines=True,
    )

    key = _widget_key(link_id, "path_entry")
    container.text_input(
        "Paste the path back",
        key=key,
        placeholder="Paste what came back from the scheduler chat",
        label_visibility="collapsed",
        **widget_defaults(key, value=""),
    )


def _apply_recipe(tag, link_id, match, buy_leg=None, sell_leg=None):
    """Copy one historical route onto this tag: its control areas, its
    source/sink points and its market path.

    **Not its transmission rows.** A reservation number belongs to a day,
    not to a route — 92% of the desk's own references appear on exactly
    one — so last month's numbers are dead on arrival, and the rows they
    came on were being deleted more often than kept. The reservation is
    typed, and its path then fills itself from OASIS
    (_fill_paths_from_oasis), which is the one direction that doesn't put
    anything on screen nobody asked for.

    Deliberately not touched: the tag's name and header, its carbon copy
    (OATI carries none), and source/sink contract — nothing in the corpus
    maps to any of them. Nor a cell the market end itself settles: a link
    sinking into SWPW leaves the Sink cell empty whatever an old route put
    in it (domain.tags.MARKET_END_FIELDS), because that end is known and
    the route is only a guess. Either end, since a market is a market
    whichever way the power runs.

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
    settled = (market_end_fields(buy_leg), market_end_fields(sell_leg))

    if match["gca"]:
        tag["source"]["gca"] = match["gca"]
        _bump(link_id, "Source_gca")
    if match["lca"]:
        tag["sink"]["lca"] = match["lca"]
        _bump(link_id, "Sink_lca")
    for segment in sample["physical_path"]:
        if (
            segment["type"] == "G"
            and segment.get("por_name")
            and "point" not in settled[0]
        ):
            tag["source"]["point"] = segment["por_name"]
            _bump(link_id, "Source_point")
        elif (
            segment["type"] == "L"
            and segment.get("pod_name")
            and "point" not in settled[1]
        ):
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
    _bump(link_id, "market_path")


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
                limit=LOOKUP_DEPTH,
            )
        )
        st.session_state[_key(link_id, "lookup_done")] = True
        st.session_state[_key(link_id, "lookup_page")] = 0

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

    # A page at a time. The ranking is a guess, and a guess that puts the
    # right route fifth is no use if fifth can't be reached DASH which is
    # what this is for, not for browsing.
    page = st.session_state.get(_key(link_id, "lookup_page"), 0)
    shown = matches[page * LOOKUP_LIMIT:(page + 1) * LOOKUP_LIMIT]
    if not shown:                       # a page that emptied under us
        page, shown = 0, matches[:LOOKUP_LIMIT]
        st.session_state[_key(link_id, "lookup_page")] = 0

    for match in shown:
        # Keyed so the popup's CSS can find it: Streamlit turns a
        # container key into an `st-key-...` class, and the bordered box's
        # own class is a generated one that changes between releases.
        with container.container(border=True, key=f"route_{link_id}_{match['recipe_id']}"):
            cols = st.columns([5, 2], vertical_alignment="center")
            _render_match(cols[0], match)
            if cols[1].button(
                "Use this route", key=_key(link_id, f"use_{match['recipe_id']}")
            ):
                _apply_recipe(tag, link_id, match, buy_leg, sell_leg)
                st.session_state[_key(link_id, "expander_open")] = True

    _render_pager(container, link_id, page, len(matches))


def _render_pager(container, link_id, page, total):
    """Back and Next under the shortlist, and which four of how many these
    are. Only drawn when there is somewhere to go."""
    last = (total - 1) // LOOKUP_LIMIT
    if last < 1:
        return
    back, forward, where = container.columns([1, 1, 2], vertical_alignment="center")
    if back.button(
        "Back", width="stretch", disabled=page == 0, key=_key(link_id, "page_back")
    ):
        st.session_state[_key(link_id, "lookup_page")] = page - 1
        arm_dialog(LINK_DIALOG)
        st.rerun()
    if forward.button(
        "Next 4",
        width="stretch",
        disabled=page >= last,
        key=_key(link_id, "page_next"),
    ):
        st.session_state[_key(link_id, "lookup_page")] = page + 1
        arm_dialog(LINK_DIALOG)
        st.rerun()
    first = page * LOOKUP_LIMIT + 1
    where.caption(f"{first}-{min(first + LOOKUP_LIMIT - 1, total)} of {total}")



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


def render_tag(container, tag_key, buy_leg, sell_leg, mw_by_hour, flow_date):
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

    _render_fields(container, tag, link_id)

    # All three editors in one row. Stacked they cost the popup twice the
    # height for the same content, and the popup is sized to show the whole
    # tag at once (see ui.scheduling.links).
    path_col, cc_col, trans_col = container.columns([5, 4, 9])
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

    # Looked up twice, for two different moments.
    #
    # **Before the editor draws**, so a path filled from a number that
    # arrived in Python — a route just applied — lands without a rerun. A
    # rerun on that run strands every field the route filled in beside it:
    # those boxes mount for the first time as the tag expander opens, the
    # run is then thrown away, and the browser keeps the empty inputs it
    # first painted while the server goes on believing they hold the
    # route's values. Server and screen disagree, permanently, and only in
    # a real browser — which is why this is written down rather than left
    # to be rediscovered.
    _, _, changed = _fill_paths_from_oasis(tag, link_id)
    if changed:
        _bump(link_id, "transmissions")

    trans_col.markdown("**Transmission** — type the reservation, path follows")
    tag["transmissions"] = _table(
        trans_col,
        link_id,
        "transmissions",
        tag["transmissions"],
        ("path", "reservation", "mw"),
        {
            # Medium, not large: "# trans" is the column a scheduler
            # actually types in, and a large Path pushes it off the side
            # of the editor.
            "path": st.column_config.TextColumn("Path", width="medium"),
            "reservation": st.column_config.TextColumn("# trans", width="small"),
            "mw": st.column_config.TextColumn("MW", width="small"),
        },
    )
    # **And after it draws**, for a number the scheduler has just typed —
    # which only reaches the tag as the editor returns. That one does need
    # a rerun to be seen, and can safely have one: by then no field has
    # been created on this run, so there is nothing to strand.
    resolved, unknown, changed = _fill_paths_from_oasis(tag, link_id)
    if changed:
        # A fresh widget so the filled path is what the editor shows — see
        # _bump. Only ever on a run that actually changed something, so this
        # can't loop: the next one finds the path already there.
        _bump(link_id, "transmissions")
        arm_dialog(LINK_DIALOG)
        st.rerun()
    _render_oasis_note(trans_col, resolved, unknown)

    # Nothing at all on a tag nobody has marked ready: it is a path still
    # being thought about, and listing what it lacks is noise about a tag
    # that isn't going anywhere yet (see render_generate).
    errors = tag_errors(tag) if tag.get("ready", True) else []
    if errors:
        # One box rather than one per message, as in the bid-file builder:
        # most of these are "not filled in yet" on a tag just opened. Here,
        # beside the fields that would fix them — Generate itself is down
        # in the popup's action row (render_generate).
        container.error("\n".join(f"- {error}" for error in errors))
