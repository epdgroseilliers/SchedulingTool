"""The trade board: a bidirectional Streamlit custom component.

This is the canvas PROJECT.md's Phase 2 spec asks for and plain Streamlit
can't produce — movable squares, a hover `+` that drags into a link, and
clickable connecting lines. It sits outside the `ui/` split on purpose (as
that doc anticipated): it owns its own DOM and its own interaction state,
and `ui.scheduling.board` is the thin adapter that feeds it and reads its
events back.

**No build step.** The frontend is one static `index.html` speaking
Streamlit's component postMessage protocol directly, rather than a React
app behind npm. That keeps it in the same repo, editable with a text editor,
and installable with nothing but `pip install -r requirements.txt` — which
matters more here than the ergonomics of a framework would.

**The event contract.** The component's value is the last event it emitted,
each carrying a monotonic `seq` so Python can tell a fresh event from the
same value being replayed on an unrelated rerun (Streamlit hands back the
last value on every run, and won't rerun at all for an identical one):

    {"seq": 4, "type": "select",       "key": "db:1" | None}
    {"seq": 5, "type": "move",         "positions": {"db:1": [x, y], ...}}
    {"seq": 6, "type": "link_request", "from": "db:1", "to": "db:2"}
    {"seq": 7, "type": "link_request", "from": "db:1", "market": "CAISO"}
    {"seq": 8, "type": "link_click",   "link_id": "L1"}

**`revision` is the redraw switch.** The component rebuilds only when
`revision` changes, so the squares a trader has dragged stay put through the
reruns caused by their own dragging. Anything that genuinely changes the
board — the flow date, the trades, the links, the selection — has to be part
of whatever the caller hashes into it.
"""

from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "frontend"

_component = components.declare_component("mag_trade_board", path=str(_FRONTEND))


def trade_board(squares, links, markets, positions, selected, revision, height=240, key="mv_board"):
    """Render the board and return the last event, or None.

    `squares` / `links` / `markets` are plain JSON-able dicts — see
    ui.scheduling.board.build_payload for their shape. `positions` is
    {square key: [x, y]} in board pixels, which the component echoes back on
    a move so the caller can persist it. `height` is a *floor*: the canvas
    sizes itself to the day's busier side.
    """
    return _component(
        squares=squares,
        links=links,
        markets=markets,
        positions=positions,
        selected=selected,
        revision=revision,
        height=height,
        key=key,
        default=None,
    )
