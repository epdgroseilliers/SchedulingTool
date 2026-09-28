"""The Tag tab of the link popup: the fields a bilateral e-Tag needs that
the board can't know, and the button that writes the workbook.

Everything the link already answers — which hours, at what MW, on which
flow date — is filled in from it and shown, not asked for. What's left is
the path: the control areas, the PSE chain, the transmission reservations
and who gets a copy.

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

import pandas as pd
import streamlit as st

import data.tagfiles.bilateral as tagfile
from domain.tags import CC_TYPES, PRODUCTS, tag_errors
from ui.scheduling.state import ensure_tag
from ui.session import widget_defaults

#: Height of each list editor, in pixels: its header plus four rows — one
#: more than an ordinary market path needs, so there's always a blank row
#: to type into without the table growing under the one below it.
TABLE_HEIGHT = 178


def _key(link_id, name):
    return f"mv_tag_{link_id}_{name}"


def _text(container, part, field, label, link_id, name, placeholder=None):
    """One text field, read into the tag as it's collected."""
    key = _key(link_id, name)
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
    first render, or the first one after a page navigation threw it away —
    so it never moves under a trader mid-edit. See the module docstring.

    Rendered on `container`, never on the bare `st`: a data editor written
    to the page while a column is open lands *under* the columns at full
    width, which is how the market path and the carbon copy ended up
    stacked instead of side by side.
    """
    seed_key, widget_key = _key(link_id, f"{name}_seed"), _key(link_id, name)
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


def _render_half(container, part, link_id, side, ca_field, ca_label, point_label):
    container.markdown(f"**{side}**")
    row = container.columns(2)
    _text(row[0], part, "market", "Market", link_id, f"{side}_market")
    _text(row[1], part, ca_field, ca_label, link_id, f"{side}_{ca_field}")
    row = container.columns(2)
    _text(row[0], part, "point", point_label, link_id, f"{side}_point")
    _text(row[1], part, "pse", "PSE", link_id, f"{side}_pse")
    row = container.columns(2)
    _text(row[0], part, "contract", "Contract", link_id, f"{side}_contract")
    _text(row[1], part, "comment", "PSE comment", link_id, f"{side}_comment")


def _render_generate(tag, link_id):
    errors = tag_errors(tag)
    if errors:
        # One box rather than one per message, as in the bid-file builder:
        # most of these are "not filled in yet" on a tag just opened.
        st.error("\n".join(f"- {error}" for error in errors))

    path = tagfile.target_path(tag)
    st.caption(
        f"Writes `{path.name}` to `{path.parent}`"
        + ("  ·  test folder, not the day's own" if tagfile.TEST_MODE else "")
    )

    left, right = st.columns([1, 1], vertical_alignment="center")
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
            st.error(
                f"{path.name} already exists. Give this tag another name, or "
                "tick Overwrite."
            )
        except Exception as e:
            st.error(f"Could not write the tag file: {e}")
        else:
            st.success(f"Tag written to {written}")


def render_tag(link, buy_leg, sell_leg):
    """The whole tab. The link supplies the schedule; everything collected
    here is written straight into this link's tag as it's typed."""
    tag = ensure_tag(link, buy_leg, sell_leg)
    link_id = link.link_id

    head = st.columns([2, 2, 3], vertical_alignment="bottom")
    _text(head[0], tag, "name", "Tag name", link_id, "name")
    _text(head[1], tag, "label", "Header", link_id, "label")
    head[2].caption(
        f"{link.flow_date} · HE {link.he_label} · {link.mwh:,.0f} MWh, "
        "from the link — both halves of the sheet carry it."
    )

    source, sink = st.columns(2)
    _render_half(source, tag["source"], link_id, "Source", "gca", "GCA", "Source")
    _render_half(sink, tag["sink"], link_id, "Sink", "lca", "LCA", "Sink")

    st.divider()
    path_col, cc_col = st.columns([3, 2])
    path_col.markdown("**Market path** — generator first, load last")
    tag["market_path"] = _table(
        path_col,
        link_id,
        "market_path",
        tag["market_path"],
        ("pse", "product", "contract"),
        {
            "pse": st.column_config.TextColumn("PSE", width="small"),
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

    st.markdown("**Transmission** — one reservation per row")
    tag["transmissions"] = _table(
        st,
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

    st.divider()
    _render_generate(tag, link_id)
