"""One AppTest workaround, shared by every test that closes a dialog.

Not a helper for the app — a helper for the harness. Importable as a plain
module because pytest puts each test file's own directory on `sys.path`
(tests/ has no package `__init__` files, by design — see tests/README.md).
"""


def let_go_of_closed_widgets(at, prefix="mv_tag_"):
    """Let AppTest take a step after a dialog's widgets have gone away.

    Streamlit discards a widget's state on any run that doesn't render it,
    and closing a modal does exactly that to every field inside it. AppTest,
    though, still holds the *previous* run's element tree and asks
    session_state for the value of every widget in it before running again —
    so a widget that legitimately went away makes the next `run()` raise
    KeyError, naming a field the app stopped caring about a run ago.

    Handing the departed ones a value gets past that. Nothing about the app
    is being faked: the tag itself lives in `mv_tags`, which is plain state
    and survives all of this, and the browser has no such problem because it
    simply stops sending widgets it no longer shows.
    """
    for elements, gone_value in ((at.text_input, ""), (at.checkbox, False)):
        for element in elements:
            key = element.key or ""
            if not key.startswith(prefix):
                continue
            try:
                element.value
            except KeyError:
                at.session_state[key] = gone_value
    return at
