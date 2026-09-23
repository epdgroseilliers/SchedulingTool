"""The Scheduling View (Phase 2) — matching buys against sells for one flow
date at a time.

A package rather than a single `ui/` module because this is a second *page*,
not another section of the Add Trade page: `state.py` holds what the whole
view shares, and the remaining modules each render one band of it, keeping
the "one module per page section, render_*() returns plain values"
convention the rest of `ui/` follows.
"""
