"""The link popup, and the detail strip under the board.

One modal serves every job the spec asks of it: it opens with a suggested
schedule when a link is dragged into existence, it opens with the existing
schedule — plus Delete — when a link is clicked, and either way the path
string and the tag it becomes are on the same surface underneath
(ui.scheduling.tag). That's deliberate; creating a link, editing it, telling
the other scheduler about it and tagging it are one train of thought, and a
separate surface for each would be four things to learn.
"""

import streamlit as st

from data.bilateral import hours_to_he_string
from domain.matching import links_for, open_by_hour, suggest_allocation
from ui.scheduling.state import (
    LINK_DIALOG,
    PENDING_TAG,
    add_tag,
    arm_dialog,
    close_popup,
    dialog_was_dismissed,
    commit_link,
    find_link,
    focused_key,
    hide_leg,
    leg_or_market,
    legs_by_key,
    remove_link,
    remove_tag,
    tag_for,
    tag_keys_for,
    update_link,
)
from ui.scheduling.tag import (
    recipe_was_applied,
    render_generate,
    render_path_string,
    render_tag,
    render_tag_lookup,
    render_tag_state,
    split_errors,
)
from ui.scheduling.widgets import hours_editor as _hours_editor


#: How wide the link popup is drawn. `st.dialog` offers only small and
#: large, and large is still too narrow to put the route lookup beside the
#: tag fields — which is the whole reason for splitting them. Same targeted
#: CSS as ui/scheduling/bidgrid.py uses on the bid-file modal, capped at the
#: viewport so a small screen scrolls inside the columns rather than off the
#: side. Only one modal is ever open at a time, so the two can't collide.
DIALOG_WIDTH_PX = 1600

#: The who-and-where line against the tagging-state radio. The radio is
#: three options wide and wants a row of its own on a narrow screen rather
#: than wrapping under the sentence beside it.
HEADER_SPLIT = [3, 2]

#: The lookup against the tag fields. The lookup is text that wraps; the tag
#: fields are two halves of a form plus three editors, and want the room.
POPUP_SPLIT = [2, 3]


#: Everything in the popup is packed tighter than Streamlit's defaults, so
#: the whole tag fits on one screen. Scoped to the dialog, so the pages
#: behind it keep the ordinary spacing.
#:
#: Tighter, not airless. The first pass at this fitted in about two thirds
#: of a screen and read as one undifferentiated block — there is room for
#: roughly a quarter more height than that, and it is spent here, on the
#: gaps that tell one thing from the next.
#:
#: Only padding, gaps and type sizes. **Never zero a margin or a min-height
#: on text** — a label, a caption or a markdown paragraph. Streamlit sizes
#: those containers from exactly those properties, so zeroing one collapses
#: the container to nothing while its text still paints, and it lands on top
#: of whatever comes next. That reads as a *tighter* layout in a height
#: measurement and as a broken one on screen, which is why every rule here
#: was checked against a screenshot and not just against a number.
_TIGHTEN = """
D div[data-testid='stVerticalBlock']{gap:.55rem}
D div[data-testid='stHorizontalBlock']{gap:.8rem}
D label[data-testid='stWidgetLabel'] p{font-size:.72rem;line-height:1.3}
D div[data-baseweb='base-input'] input{padding:.2rem .5rem;font-size:.85rem;height:1.9rem}
D div[data-testid='stCaptionContainer'] p{font-size:.74rem;line-height:1.35}
D div[data-testid='stMarkdownContainer'] p{line-height:1.35}
D hr{margin:.6rem 0}
D details summary{padding:.35rem .6rem}
D div[data-testid='stExpanderDetails']{padding-top:.45rem}
D div[data-testid='stCodeBlock'] pre{padding:.55rem .7rem}
D div[data-testid='stButton'] button{padding:.25rem .7rem;min-height:2rem}
D div[class*='st-key-route_']{padding:.55rem .75rem}
"""


def _dialog_css():
    """Wider, and packed tighter than Streamlit's defaults everywhere — the
    popup has to hold the hours, the path string, the route lookup and the
    whole tag form at once, and a default 1rem gap between every element
    spends several hundred pixels on nothing."""
    scope = "div[data-testid='stDialog'] div[role='dialog'] "
    tightened = "".join(
        line.replace("D ", scope, 1) for line in _TIGHTEN.strip().splitlines()
    )
    return (
        "<style>div[data-testid='stDialog'] div[role='dialog']{"
        f"width:min({DIALOG_WIDTH_PX}px,97vw);"
        f"max-width:min({DIALOG_WIDTH_PX}px,97vw);"
        "}" + tightened + "</style>"
    )


def _side_text(leg, verb):
    """"Buy from AZPS at PALOVERDE500" — who and where, and nothing else.

    No MW and no hours: both are in the editor immediately below, and a
    header that repeats the grid underneath it is a header nobody reads.
    """
    if leg is None:
        return f"{verb} — (not on the board)"
    if leg.is_market:
        return f"{verb} the **{leg.pse}** market"
    return f"{verb} **{leg.pse}** at {leg.por_pod}"


def _open_note(leg, links):
    """How much of that side is still unlinked. Kept — unlike the MW above —
    because it is the one number the editor can't show: it comes from the
    *other* links on the leg, not from this one. A market has no schedule of
    its own, so it has nothing open."""
    if leg is None or leg.is_market:
        return None
    return f"{leg.pse} {sum(open_by_hour(leg, links).values()):,.0f} MWh open"


#: How many tags one link may be split into. Not a technical limit: a
#: schedule split five ways is five paths to keep straight in one popup, and
#: a trader who needs more has a different problem from the one this solves.
MAX_TAGS = 6


def _tag_hours(container, tag_key, tag, flow_date):
    """One split tag's own share of the hours.

    Seeded once and then left alone, like every other editor in this popup:
    the grid's own data is part of the widget's identity, so re-seeding it
    from what the trader is typing renames the widget and drops the next
    edit (see ui.scheduling.tag._table, which is the same trap). The seed
    is `mv_tag_`-prefixed so it is swept with the rest when a popup opens,
    and rebuilt from the tag, which is what actually holds the hours.
    """
    seed_key = f"mv_tag_{tag_key}_hours_seed"
    if seed_key not in st.session_state:
        st.session_state[seed_key] = dict(tag.get("mw_by_hour") or {})
    with container:
        return _hours_editor(
            st.session_state[seed_key], flow_date, key=_hours_key(tag_key)
        )


def _hours_key(tag_key):
    """This tag's hour grid, under a name that changes when the fill box
    rewrites it DASH a renamed widget is the only thing that makes a grid show
    what Python just put in it (see ui.scheduling.tag._bump, same trap)."""
    version = st.session_state.get(f"mv_tag_{tag_key}_hours_ver", 0)
    return f"mv_tag_{tag_key}_hours" + (f"_v{version}" if version else "")


def _quick_fill(container, tag_key, tag, link_mw_by_hour):
    """One MW across every hour the link runs.

    Most tags carry the same MW all day, and typing it into twenty-four
    cells is twenty-four chances to fat-finger one. The hours it fills are
    the **link's**, not all twenty-four: an hour the link doesn't run is an
    hour this tag can't have, and filling it would only hand the trader a
    check to clear.
    """
    hours = [h for h, mw in (link_mw_by_hour or {}).items() if mw]
    box, go = container.columns([1, 1], vertical_alignment="bottom")
    mw = box.number_input(
        "MW every hour",
        min_value=0.0,
        value=None,
        step=1.0,
        placeholder="MW",
        label_visibility="collapsed",
        key=f"mv_tag_{tag_key}_fill",
    )
    if go.button(
        "Fill",
        width="stretch",
        disabled=mw is None or not hours,
        help=f"Put this MW on every hour the link runs (HE "
             f"{hours_to_he_string(hours)})" if hours else "This link has no hours",
        key=f"mv_tag_{tag_key}_fill_go",
    ):
        if mw is None or not hours:
            return          # the button is disabled for both; belt and braces
        filled = {int(h): float(mw) for h in hours}
        tag["mw_by_hour"] = filled
        st.session_state[f"mv_tag_{tag_key}_hours_seed"] = filled
        key = f"mv_tag_{tag_key}_hours_ver"
        st.session_state[key] = st.session_state.get(key, 0) + 1
        arm_dialog(LINK_DIALOG)
        st.rerun()


def _render_tag_pane(container, tag_key, buy, sell, hours, flow_date, editing):
    """One tag, whole: the path string in the open, the route lookup beside
    the fields behind their expander.

    The same three surfaces whether the link carries one tag or five - the
    only thing a split changes is which hours they are about, and that is
    settled before this is called.
    """
    render_path_string(container, tag_key, buy, sell, hours, flow_date)

    # Side by side below the full-width half, because both are tall and
    # stacking them is what made the popup scroll: together they cost
    # the height of the taller one rather than the sum of the two.
    #
    # container.columns rather than `with container:` — the single-tag case
    # hands this the page itself, and the st module is not a context
    # manager even though every container it hands out is.
    lookup_col, tag_col = container.columns(POPUP_SPLIT)
    render_tag_lookup(lookup_col, tag_key, buy, sell, hours, flow_date)

    # Open on a link being revisited, shut on one being drawn - unless
    # "Use this route" just filled it, in which case it's worth seeing
    # right away. Drawing a link is about the hours, the path string and
    # perhaps a lookup; the tag fields are the next sitting.
    with tag_col.expander(
        "Tag details", expanded=bool(editing) or recipe_was_applied(tag_key)
    ):
        render_tag(st.container(), tag_key, buy, sell, hours, flow_date)


def render_popup(legs, links, flow_date):
    """Show the schedule modal if one is open. Everything it does ends in a
    rerun, which is also what dismisses it."""
    pending = st.session_state.mv_pending
    editing = st.session_state.mv_editing
    if not pending and not editing:
        return
    # Every button in here closes the popup before rerunning, so a page run
    # that still finds it open means it was dismissed instead.
    if dialog_was_dismissed(LINK_DIALOG):
        close_popup()
        return

    @st.dialog("Link schedule", width="large")
    def _dialog():
        st.markdown(_dialog_css(), unsafe_allow_html=True)
        if editing:
            link = find_link(editing)
            if link is None:
                close_popup()
                st.rerun()
            buy = leg_or_market(link.buy_key, legs, flow_date)
            sell = leg_or_market(link.sell_key, legs, flow_date)
            # The link's own MW is added back before asking what's open, or
            # every hour it already holds would read as fully committed.
            without_this = [ln for ln in links if ln.link_id != link.link_id]
            start_from = dict(link.mw_by_hour)
        else:
            link = None
            buy = leg_or_market(pending["buy_key"], legs, flow_date)
            sell = leg_or_market(pending["sell_key"], legs, flow_date)
            without_this = links
            start_from = None

        if buy is None or sell is None:
            st.error("One side of this link is no longer on the board.")
            if st.button("Close"):
                close_popup()
                st.rerun()
            return

        # A link still being drawn gets its tag — and its path string, and
        # its state — too, filed under PENDING_TAG until Create link gives
        # it an id.
        tag_key = link.link_id if link else PENDING_TAG

        # Who and where on the left, who still has to tag it on the right:
        # the header line was half empty and the radio is the one control
        # that belongs with the link rather than with the tag's own fields.
        who, state = st.columns(HEADER_SPLIT, vertical_alignment="center")
        who.markdown(
            f"{_side_text(buy, 'Buy from')} · {_side_text(sell, 'sell to')}"
        )

        # One surface, top to bottom, in the order the work happens: the
        # hours, then the path string that goes out on chat, then — behind an
        # expander — the tag fields, which only the links MAG has to tag ever
        # need. (They were two tabs briefly; a tab hid half of what a trader
        # was in the middle of deciding.)
        if start_from is None:
            start_from = suggest_allocation(buy, sell, without_this)
            if start_from:
                st.caption(
                    "Suggested: every hour both sides still have open, at "
                    "the smaller of the two remaining MW. Overwrite any hour."
                )
            else:
                st.warning(
                    "No open hour in common — every hour these two share is "
                    "already allocated elsewhere. Type the hours you want, or "
                    "cancel."
                )

        allocation = _hours_editor(
            start_from, flow_date, key=f"mv_alloc_{editing or 'new'}"
        )
        # Drawn into the header column above, which is why it comes after
        # the hours: ensure_tag wants the schedule, and the schedule is
        # whatever the editor just returned.
        render_tag_state(state, tag_key, buy, sell, allocation, flow_date)
        open_notes = [
            note
            for note in (_open_note(buy, without_this), _open_note(sell, without_this))
            if note
        ]
        totals, split = st.columns([5, 1], vertical_alignment="center")
        totals.caption(
            " · ".join(
                [f"{sum(allocation.values()):,.0f} MWh on this link"] + open_notes
            )
        )
        # Next to the link's own total, because that is what a split has to
        # keep adding up to.
        if len(tag_keys_for(tag_key) or [tag_key]) < MAX_TAGS and split.button(
            "Split", width="stretch", help="Tag part of this schedule on another path"
        ):
            add_tag(tag_key, buy, sell, flow_date)
            arm_dialog(LINK_DIALOG)
            st.rerun()

        # Under the line that says what the link carries, because that is
        # the number the tags have to add up to. Written into further down,
        # once the tabs have reported their shares.
        check = st.empty()

        st.divider()

        # One tag, or several: a schedule can be split across paths, and
        # each path is its own tag with its own hours, its own string and
        # its own fields. The tab strip only appears once there is more
        # than one - a lone tab is a label for a choice nobody made.
        keys = tag_keys_for(tag_key) or [tag_key]
        if len(keys) == 1:
            # Its hours *are* the link's, and follow it: there is nothing to
            # share them with, so a second grid saying the same thing would
            # only be a second grid to keep in step.
            _render_tag_pane(st, keys[0], buy, sell, allocation, flow_date, editing)
            shares = [allocation]
        else:
            shares = []
            labels = [f"Tag {n}" for n, _ in enumerate(keys, 1)]
            for pane, key in zip(st.tabs(labels), keys):
                tag = tag_for(key)
                hours = _tag_hours(pane, key, tag, flow_date)
                tag["mw_by_hour"] = {int(h): float(mw) for h, mw in hours.items()}
                shares.append(hours)

                # The same shape as the link's own total above it, so the
                # two read as the same kind of line: what this one carries,
                # what you can put on it in one go, and the one thing you
                # can do to it.
                carried, fill, drop = pane.columns(
                    [4, 2, 1], vertical_alignment="center"
                )
                carried.caption(f"{sum(hours.values()):,.0f} MWh on this tag")
                _quick_fill(fill, key, tag, allocation)
                if key != keys[0] and drop.button(
                    "Remove",
                    width="stretch",
                    help="Drop this tag. Its hours go back to being untagged.",
                    key=f"mv_tag_{key}_remove",
                ):
                    remove_tag(key)
                    arm_dialog(LINK_DIALOG)
                    st.rerun()
                _render_tag_pane(pane, key, buy, sell, hours, flow_date, editing)

        # Drawn into the slot left above, next to the schedule it is about:
        # the tabs have to report their shares before it can be worked out,
        # and by then the top of the popup is long since written.
        mismatch = split_errors(allocation, shares)
        if mismatch:
            check.error("\n".join(f"- {line}" for line in mismatch))

        st.divider()

        cols = st.columns([1, 1, 1, 4], vertical_alignment="center")
        render_generate(cols[3], tag_key, blocked=bool(mismatch))
        if cols[0].button(
            "Save" if editing else "Create link", type="primary", width="stretch"
        ):
            if editing:
                update_link(editing, allocation)
            else:
                commit_link(buy, sell, allocation)
            st.rerun()
        if editing and cols[1].button("Delete", width="stretch"):
            remove_link(editing)
            st.rerun()
        if cols[2 if editing else 1].button("Cancel", width="stretch"):
            close_popup()
            st.rerun()

    _dialog()


def render_detail(legs, links, flow_date):
    """The focused square, under the board: one line always, the hour grids
    behind an expander so the board still owns the screen."""
    key = focused_key()
    if not key:
        st.caption(
            "Click a square for its detail · drag a square to move it · drag "
            "its **+** onto the other side (or a market chip) to link."
        )
        return

    leg = legs_by_key(legs).get(key)
    if leg is None:
        return
    own = links_for(leg.key, links)
    line, clear = st.columns([5, 1], vertical_alignment="center")
    line.markdown(
        f"**{leg.direction} {leg.pse}** · {leg.por_pod} · HE {leg.he_label} · "
        f"{leg.mw_label} · {leg.mwh:,.0f} MWh · "
        f"**{sum(open_by_hour(leg, links).values()):,.0f} MWh open** · "
        f"{len(own)} link(s)"
    )
    # The same thing the × on the square does. Two routes to it on purpose:
    # the × is quick once you know it's there, this one is findable.
    if clear.button(
        "✕ Clear",
        width="stretch",
        help="Take this square off the board. Nothing is deleted — Restore "
        "brings it back.",
    ):
        hide_leg(leg.key)
        st.rerun()

    with st.expander("Hour by hour"):
        st.caption("This trade's schedule")
        _hours_editor(leg.mw_by_hour, flow_date, key=f"mv_d_{leg.key}", disabled=True)
        for ln in own:
            other = legs_by_key(legs).get(ln.other_key(leg.key))
            st.caption(
                f"{ln.link_id} → {other.pse if other else '(not shown)'} · "
                f"{ln.mwh:,.0f} MWh"
            )
            _hours_editor(
                ln.mw_by_hour, flow_date, key=f"mv_dl_{leg.key}_{ln.link_id}", disabled=True
            )
        st.caption("Still open")
        _hours_editor(
            open_by_hour(leg, links), flow_date, key=f"mv_do_{leg.key}", disabled=True
        )
