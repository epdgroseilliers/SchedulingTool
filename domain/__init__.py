"""Pure business logic: option lists, shape/schedule math, trade rules.

Nothing in this package imports streamlit or touches st.session_state — every
function here takes plain arguments and returns plain values, so it can be
unit-tested directly. Streamlit-facing code lives in `ui/`; database access
lives in `data/`.
"""
