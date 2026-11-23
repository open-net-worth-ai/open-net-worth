"""Issue #214 — a round that is only call-syntax residue is shown as the answer.

Haiku 5.5 over the Claude CLI, after ``search_connector_directory`` ran, wrote a final round
that was not an answer: the tail of a call tag cut mid-name, an echoed ``<reasoning_effort>``
marker and a bare ``Assistant`` turn label. The reader saw exactly that as the reply.

Each test drives the same path a live turn takes — the streaming scanner (what the screen
shows), ``strip_tool_syntax`` (what is saved) and ``completion_nudge`` (whether the turn may
end) — over the residue, with synthetic text.
"""

from __future__ import annotations

import pytest

from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.tool_text import LiveLineFilter, strip_tool_syntax
from backend.ai.turn_completion import completion_nudge

RESIDUES = [
    pytest.param(
        '_directory">\n\n<reasoning_effort>15</reasoning_effort>\n\nAssistant', id="issue-214"
    ),
    pytest.param('<tool_call>search_connector_directory">', id="name-and-quote-after-tag"),
    pytest.param("<reasoning_effort>15</reasoning_effort>", id="echoed-effort-marker"),
    pytest.param("Assistant", id="bare-role-label"),
    pytest.param('name="get_connector_status">', id="attribute-tail"),
    pytest.param("_placeholder_do_not_use", id="invented-placeholder-name"),
    pytest.param("ERROR: Invalid JSON in : Expected property name", id="imitated-tool-error"),
    pytest.param("<parse> </parse>", id="parse-wrapper"),
    pytest.param("\u200b\u200b<", id="invisible-and-lone-angle"),
]


def _streamed(text: str) -> str:
    """What the chat shows, one character at a time, through both live filters."""
    scanner, live = ToolCallScanner(), LiveLineFilter()
    out = ""
    for ch in text:
        out += live.feed("".join(scanner.feed(ch)))
    return out + live.feed(scanner.flush()) + live.flush()


@pytest.mark.parametrize("residue", RESIDUES)
def test_the_saved_reply_holds_no_residue(residue):
    assert strip_tool_syntax(residue).strip() == ""


@pytest.mark.parametrize("residue", RESIDUES)
def test_the_live_screen_shows_no_residue(residue):
    assert _streamed(residue).strip() == ""


@pytest.mark.parametrize("residue", RESIDUES)
def test_a_round_of_only_residue_is_not_an_answer(residue):
    assert completion_nudge(residue, [], nudges_used=0) is not None


def test_a_real_answer_beside_residue_is_kept():
    text = (
        "You can link it under Connectors.\n\n<reasoning_effort>15</reasoning_effort>\n\nAssistant"
    )
    assert strip_tool_syntax(text).strip() == "You can link it under Connectors."


@pytest.mark.parametrize(
    "prose",
    [
        "The Assistant can help you link a bank.",
        "Set your reasoning effort to High in the model picker.",
    ],
)
def test_prose_that_only_mentions_the_words_is_untouched(prose):
    assert strip_tool_syntax(prose) == prose
    assert _streamed(prose).strip() == prose


def test_a_leading_replacement_character_is_dropped_from_the_answer():
    assert strip_tool_syntax("\ufffdYou can link it under Connectors.") == (
        "You can link it under Connectors."
    )
    assert _streamed("\ufffdYou can link it.\n").strip() == "You can link it."


@pytest.mark.parametrize(
    "prose",
    [
        "Write the tag as <b>bold</b> and close it.",
        "Names starting with an underscore, like _private, are hidden.",
        'Use href="x"> only inside a link.',
        "ERROR: the bank rejected the import because the file was empty.",
    ],
)
def test_prose_that_resembles_a_fragment_is_untouched(prose):
    assert strip_tool_syntax(prose) == prose
    assert _streamed(prose).strip() == prose
