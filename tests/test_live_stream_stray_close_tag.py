"""A stray closing tag never reaches the chat — live, not only once the turn is saved.

Live 2026-10-30 (chat, claude_cli): a reply streamed "</tool_" above a Read doc row and
"call>" below it; the saved message was clean. The model closed its call twice:

    <tool_call>{"name": "read_doc", "input": {...}}</tool_call></tool_call>

`ToolCallScanner` consumed the block and handed the second, unmatched `</tool_call>` on as
text. `LiveLineFilter` held its first few characters as a short line, the call's
`tool_use_start` flushed them, and the rest of the tag streamed after the row — split in two.
`strip_tool_syntax` already drops the tag from the SAVED text, which is why a reload was fine.
"""

from __future__ import annotations

import pytest

from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.tool_text import LiveLineFilter

READ = '<tool_call>{"name": "read_doc", "input": {"doc_id": "connectors/external-mcp"}}</tool_call>'


def _live(raw: str, chunk: int) -> tuple[str, list[str]]:
    """The chat as claude_cli_provider + assistant.run stream it: a captured call flushes the
    line filter, then its row is drawn."""
    scanner, line = ToolCallScanner(), LiveLineFilter()
    shown: list[str] = []
    emitted = 0
    for i in range(0, len(raw), chunk):
        for visible in scanner.feed(raw[i : i + chunk]):
            shown.append(line.feed(visible))
        while emitted < len(scanner.calls):
            shown.append(line.flush(has_calls=True))
            shown.append(f"[{scanner.calls[emitted]['name']}]")
            emitted += 1
    shown.append(line.feed(scanner.flush()) + line.flush(has_calls=bool(scanner.calls)))
    return "".join(shown), [c["name"] for c in scanner.calls]


@pytest.mark.parametrize("chunk", [1, 3, 5, 7, 1000])
@pytest.mark.parametrize(
    "stray", ["</tool_call>", "</call>", "</function_calls>", "</invoke>", "\n</tool_call>\n"]
)
def test_a_closing_tag_with_nothing_open_is_never_shown(chunk, stray):
    shown, calls = _live(f"\n\n{READ}{stray}", chunk)
    assert "<" not in shown and ">" not in shown, shown
    assert calls == ["read_doc"]


@pytest.mark.parametrize("chunk", [1, 4, 1000])
def test_the_call_after_a_doubled_close_still_runs(chunk):
    shown, calls = _live(f"{READ}\n</tool_call>\n{READ.replace('external-mcp', 'x')}", chunk)
    assert "tool_" not in shown and "call>" not in shown, shown
    assert calls == ["read_doc", "read_doc"]


@pytest.mark.parametrize("chunk", [1, 3, 1000])
def test_prose_around_the_stray_tag_is_kept(chunk):
    shown, _ = _live(f"Reading the page now.\n{READ}</tool_call>\nHere is how.", chunk)
    assert shown.replace("[read_doc]", "").split("\n") == [
        "Reading the page now.",
        "",
        "Here is how.",
    ]


@pytest.mark.parametrize("chunk", [1, 2, 1000])
def test_prose_with_angle_brackets_is_untouched(chunk):
    text = "Net worth < S$1M, and </b> is just a tag name in a sentence: a < b > c."
    shown, calls = _live(text, chunk)
    assert shown == text and calls == []
