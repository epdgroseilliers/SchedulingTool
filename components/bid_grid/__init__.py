"""The bid grid: the bid-file builder's editable table, as a Streamlit
custom component.

Built for the same reason `trade_board` was — plain Streamlit can't produce
it. The desk wants this laid out like the workbook it writes: the two sides
side by side in one window, and above each counterparty's columns a
three-level header (counterparty > GCA/LCA > MW & Price) with a `+` on the
code cell that splits it into another pair. `st.data_editor` can do neither:
it *flattens* a pandas MultiIndex into single-level column names
(`streamlit/elements/widgets/data_editor.py::_fix_column_headers`), and a
header cell is not somewhere a widget can go.

**No build step**, like the board: one static `index.html` speaking
Streamlit's postMessage protocol, editable with a text editor and installed
by `pip install -r requirements.txt` alone.

**The rebalancing stays in Python.** The component reports what the trader
did and redraws what it's handed; `domain.bidfiles.rebalance_hour` decides
what a typed MW does to the rest of a counterparty's split. One authority,
already tested, rather than the same rule written twice.

**The event contract** — each stamped with a `seq` and the per-frame
`instance` id, exactly as the board's is, and for the same reason (see
components/trade_board/__init__.py):

    {"type": "mw",      "side": "SHORT", "pse": "AZPS", "line": 1, "hour": 7, "value": 40.0}
    {"type": "price",   "side": "SHORT", "pse": "AZPS", "line": 0, "value": -1.0}
    {"type": "code",    "side": "SHORT", "pse": "AZPS", "line": 0, "value": "PALOVERDE"}
    {"type": "split",   "side": "SHORT", "pse": "AZPS", "line": 0}
    {"type": "unsplit", "side": "SHORT", "pse": "AZPS", "line": 1}

**`revision` is the redraw switch**, again as on the board. The component
patches cell values in place while the column structure is unchanged, so
the cell being typed into is never yanked out from under the trader; it
rebuilds only when that structure changes.
"""

from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "frontend"

_component = components.declare_component("mag_bid_grid", path=str(_FRONTEND))


def bid_grid(hours, sides, revision, key="mv_bidgrid"):
    """Render the grid and return the last event, or None.

    `hours` is the index column ([{"he", "ept"}, ...]) and `sides` the
    panels — see ui.scheduling.bidgrid.build_payload for their shape.
    """
    return _component(
        hours=hours,
        sides=sides,
        revision=revision,
        key=key,
        default=None,
    )
