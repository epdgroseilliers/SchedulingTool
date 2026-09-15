"""Streamlit rendering, one module per page section.

Each `render_*` function owns one visual piece of the page and returns
plain values (widget results, collected dicts) rather than reaching into
module-level globals — `app.py` wires them together in page order. Session
state keys that cross module boundaries (block ids, the parse-once guard,
flash messages) are centralized in `ui.session`.
"""
