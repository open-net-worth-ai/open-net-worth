"""No tool's model-facing text decides whether a call needs the user's approval.

That is the permission setting's job (and, under Auto, the judge's). A description that said
"APPROVE-TIER: confirm with the user before sending" made the Auto judge ask about a Telegram
message because "the tool itself marks it as needing confirmation", and told an unattended
Automation run to check with a user who was not there. Lines describing what happens IF the call
asks first are fine; lines telling the model to ask, or claiming a card always appears, are not.
"""

from __future__ import annotations

import re

import backend.ai.tools  # noqa: F401 — import for the @tool side effect (registry fill)
from backend.ai.tools.registry import build_tool_schemas

_TELLS_THE_MODEL_TO_ASK = re.compile(
    r"approve[- ]?tier"
    r"|confirm\b[^.]{0,60}\bwith the user"
    r"|confirm (intent|first)"
    r"|ask (the user )?for (explicit )?confirmation"
    r"|without (explicit |user )?(confirmation|approval)"
    r"|wait for the user to confirm"
    r"|for the user to confirm"
    r"|after the user confirms"
    r"|(this|and) raises (a|one) confirm(ation)? card"
    r"|asks the user for permission",
    re.IGNORECASE,
)


def _descriptions(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield path, value
            else:
                yield from _descriptions(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _descriptions(value, f"{path}[{index}]")


def test_no_tool_text_tells_the_model_to_ask_for_approval():
    offenders = [
        f"{schema['name']}{path}: …{match.group(0)}…"
        for schema in build_tool_schemas()
        for path, text in _descriptions(schema, "")
        for match in [_TELLS_THE_MODEL_TO_ASK.search(text)]
        if match
    ]
    assert offenders == []


def test_the_guard_catches_the_wording_it_exists_for():
    for text in (
        "APPROVE-TIER: confirm with the user before sending.",
        "Confirm the details with the user before calling.",
        "On an interactive turn this raises a confirm card (no write yet).",
        "Returns a change for the user to confirm.",
        "One call covers them all, and raises ONE confirm card for the whole batch.",
    ):
        assert _TELLS_THE_MODEL_TO_ASK.search(text), text
    for text in (
        "If the call asks the user first, their answer runs it server-side.",
        "Copy it off the broker confirmation.",
        "Call list_categories first to confirm it doesn't already exist.",
    ):
        assert not _TELLS_THE_MODEL_TO_ASK.search(text), text
