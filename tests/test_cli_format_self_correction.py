"""A model that writes its first call in the wrong dialect and corrects itself.

Persona QA, 2026-10-13 (claude_cli:sonnet, every one of four onboarding runs): the reply held
an empty ``<invoke name="create_account"></invoke>``, then "Wait, I must use the required
format.", then the proper call. The empty call ran and failed ("1 failed" under the step),
and the self-talk stayed in the chat, which the user read as the app breaking.
"""

from __future__ import annotations

import pytest

from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.tool_text import drop_call_preamble, strip_tool_syntax


def _scan(text: str) -> list[dict]:
    scanner = ToolCallScanner()
    for ch in text:
        list(scanner.feed(ch))
    scanner.flush()
    return scanner.calls


def test_an_empty_call_the_reply_then_makes_properly_is_dropped():
    calls = _scan(
        '<invoke name="create_account">\n</invoke>\n\nWait, I must use the required format.\n\n'
        '<tool_call>{"name": "create_account", "input": {"name": "DBS"}}</tool_call>'
    )
    assert calls == [{"name": "create_account", "input": {"name": "DBS"}}]


def _shown(text: str) -> tuple[str, list[dict]]:
    """What the live stream shows, one character at a time, and the calls it found."""
    scanner = ToolCallScanner()
    shown = "".join("".join(scanner.feed(ch)) for ch in text) + scanner.flush()
    return shown, scanner.calls


def test_loose_parameter_tags_never_reach_the_live_screen():
    """A digest wrote its call's arguments as text with no `<invoke>` around them, then made the
    call properly. The call ran once and the saved reply was clean, but the screen showed them."""
    shown, calls = _shown(
        '<parameter name="transactions">[{"description": "ACME", "amount": 5}]</parameter> '
        '<parameter name="import_description">Email: ACME</parameter>\n'
        "Now logging it.\n"
        '<tool_call>{"name": "add_transactions", "input": {"transactions": []}}</tool_call>'
    )
    assert "parameter" not in shown
    assert "ACME" not in shown
    assert "Now logging it." in shown
    assert calls == [{"name": "add_transactions", "input": {"transactions": []}}]


def test_parameters_inside_a_real_call_are_still_its_arguments():
    shown, calls = _shown(
        '<invoke name="add_transactions"><parameter name="note">ACME</parameter></invoke>'
    )
    assert shown == ""
    assert calls == [{"name": "add_transactions", "input": {"note": "ACME"}}]


def test_prose_that_only_mentions_a_parameter_stays_visible():
    text = "Set the <parameter> field, then check the parameter name in the form."
    assert _shown(text) == (text, [])


def test_an_unfinished_parameter_tag_is_dropped_not_half_shown():
    shown, calls = _shown('Checking.\n<parameter name="transactions">[{"description": "ACM')
    assert shown == "Checking.\n"
    assert calls == []


def test_a_genuine_zero_argument_call_still_runs():
    assert _scan('<invoke name="list_accounts"></invoke>') == [
        {"name": "list_accounts", "input": {}}
    ]


def test_a_held_call_keeps_its_place_ahead_of_a_different_call():
    calls = _scan(
        '<invoke name="list_envelopes"></invoke>'
        '<tool_call>{"name": "assign_budget", "input": {"amount": 5}}</tool_call>'
    )
    assert [c["name"] for c in calls] == ["list_envelopes", "assign_budget"]


@pytest.mark.parametrize(
    "leak",
    [
        "Wait, I must use the required format.",
        "Note: I used an incorrect format above. Correcting to the required protocol.",
        "Correcting to the required protocol.",
        "Actually, using the required format:",
        "Actually, I need to use the correct format.",
        "I need to use the proper tool call format.",
        "Wait — I must use the correct tool-call format.",
        "I need to fix the format and use the proper tool_call structure instead.",
        "ait\n\nI need to use the proper tool call format.",
        "_call>\n\nWait, I must use the required format.",
        "Correction: I need to emit the call in the proper format.",
        "Correction: I must use the proper call format.",
        "Wait — I must use the proper call format.",
        "Correction to the format: tool calls must use the tool_call block.",
    ],
)
def test_protocol_self_talk_is_cut_and_the_answer_kept(leak):
    out = strip_tool_syntax(f"Both are in, Tom.\n\n{leak}\n\nYour net worth is S$1,770.")
    assert out == "Both are in, Tom.\n\nYour net worth is S$1,770."


@pytest.mark.parametrize(
    "prose",
    [
        "Your statement uses the correct date format, so the import is fine.",
        "Use the correct format for dates: DD/MM/YYYY.",
        "Wait, that total looks off. Let me check.",
    ],
)
def test_prose_about_formats_is_untouched(prose):
    assert strip_tool_syntax(prose) == prose


@pytest.mark.parametrize(
    "lead_in", ["Actually, emitting properly:", "\n\nNow let me pull that up:\n"]
)
def test_a_lone_lead_in_to_a_call_is_dropped(lead_in):
    assert drop_call_preamble(lead_in, has_calls=True) == ""


@pytest.mark.parametrize(
    ("text", "has_calls"),
    [
        ("Here is your September breakdown:", False),  # an answer round: never touched
        ("Two things to confirm before I import:\n- the card\n- the date", True),
    ],
)
def test_answers_and_real_content_are_kept(text, has_calls):
    assert drop_call_preamble(text, has_calls=has_calls) == text


# ── live stream (LiveLineFilter) ─────────────────────────────────────────────────────
# The chat keeps what it streamed, so the save-time strip alone left the self-talk on
# screen ("Correction to my format: emitting the proper tool call blocks.", QA 2026-10-14).


def _stream(text: str, *, has_calls: bool = False) -> str:
    from backend.ai.tool_text import LiveLineFilter

    f = LiveLineFilter()
    out = "".join(f.feed(ch) for ch in text)
    return out + f.flush(has_calls=has_calls)


def test_self_talk_never_reaches_the_live_stream():
    text = "Both are in, Tom.\n\nCorrection to my format: emitting the proper tool call blocks.\nNet worth S$1,770."
    assert _stream(text) == "Both are in, Tom.\n\nNet worth S$1,770."


def test_a_long_answer_streams_before_its_line_ends():
    from backend.ai.tool_text import LiveLineFilter

    f = LiveLineFilter()
    paragraph = "Your September spending came to S$795 across eight categories, " * 3
    shown = "".join(f.feed(ch) for ch in paragraph)  # no newline yet
    assert shown == paragraph  # nothing held back beyond the first 120 characters


def test_a_lone_lead_in_to_a_call_is_dropped_live():
    assert _stream("Actually, emitting properly:", has_calls=True) == ""
    assert _stream("Here is your September:", has_calls=False) == "Here is your September:"


def test_ordinary_lines_are_untouched_live():
    text = "Wait, that total looks off.\n- Rent: S$900\n\n**Food:** S$320"
    assert _stream(text) == text
