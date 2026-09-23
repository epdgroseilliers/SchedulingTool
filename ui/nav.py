"""The header strip both pages share: which page you're on, and one click
to the other.

Streamlit's own multipage navigation is a list in the sidebar, which means
switching pages costs opening a drawer first — too much for two pages a
trader moves between constantly. So the sidebar nav is hidden and replaced
with a single always-visible button, and the page's own top padding is
trimmed with it: on the Scheduling View the board is the page, and every row
above it is a row the board doesn't get.
"""

import streamlit as st

PAGES = {
    "add_trade": {"path": "app.py", "label": "Add Trade", "icon": "⚡"},
    "scheduling": {
        "path": "pages/1_Scheduling_View.py",
        "label": "Scheduling View",
        "icon": "🔗",
    },
}

_CHROME_CSS = """
<style>
  /* Streamlit's own top bar is fixed and floats *over* the page, so simply
     shrinking the container's top padding slid this header underneath it.
     Since the sidebar and its page list are hidden too, that bar has
     nothing left to offer — it goes, and this header becomes the top of
     the page. */
  [data-testid="stHeader"], [data-testid="stToolbar"] { display: none; }
  [data-testid="stSidebar"], [data-testid="stSidebarNav"] { display: none; }
  .block-container { padding-top: 1.1rem; padding-bottom: 1rem; }
</style>
"""


def render_nav(current):
    """Draw the header for the page named by `current` (a key of PAGES).

    Returns nothing — the only thing it can do is navigate, and
    st.switch_page doesn't come back.
    """
    st.html(_CHROME_CSS)
    here = PAGES[current]
    other = next(p for key, p in PAGES.items() if key != current)

    title, _, go = st.columns([3, 4, 1.4], vertical_alignment="center")
    title.markdown(f"##### {here['icon']} {here['label']}")
    if go.button(
        f"{other['icon']} {other['label']}",
        width="stretch",
        help=f"Go to {other['label']}",
    ):
        st.switch_page(other["path"])
