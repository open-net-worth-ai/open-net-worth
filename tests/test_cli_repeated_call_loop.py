"""A CLI reply that repeats one tool call is a loop, and ends at once.

Live 2026-11-14 (chat, claude_cli:sonnet): the reply wrote ``<invoke name="run_screen_command">
</invoke>`` ~2,200 times in 300 s. The scanner HOLDS an empty XML call (it is usually a first stab
the model corrects with arguments), and an identical repeat was dropped, so the hold was never
released, no call was ever parsed, the post-call grace never started and the round ran to the
300 s ceiling. Now a repeat releases the held call, and a third copy marks the reply looping —
which the provider's ``_until_call_grace`` ends immediately.
"""

from __future__ import annotations

import asyncio

from backend.ai.models.claude_cli_provider import _until_call_grace
from backend.ai.models.cli_call_grace import PostCallWatch
from backend.ai.models.cli_tool_protocol import ToolCallScanner

_LOOP_UNIT = '\n<invoke name="run_screen_command">\n</invoke>\n\n\n'


def _scan(text: str, step: int = 12) -> ToolCallScanner:
    scanner = ToolCallScanner()
    for i in range(0, len(text), step):
        list(scanner.feed(text[i : i + step]))
    scanner.flush()
    return scanner


def test_a_repeated_empty_call_runs_once_and_marks_the_reply_looping():
    scanner = _scan(_LOOP_UNIT * 50)
    assert scanner.calls == [{"name": "run_screen_command", "input": {}}]
    assert scanner.looping is True


def test_the_loop_is_seen_by_the_third_copy():
    scanner = ToolCallScanner()
    list(scanner.feed(_LOOP_UNIT * 2))
    assert scanner.looping is False
    assert scanner.calls == [{"name": "run_screen_command", "input": {}}]
    list(scanner.feed(_LOOP_UNIT))
    assert scanner.looping is True


def test_an_empty_first_stab_corrected_with_arguments_still_runs_only_the_real_call():
    scanner = _scan(
        '<invoke name="create_account"></invoke>\nWait, I must use the required format.\n'
        '<tool_call>{"name": "create_account", "input": {"name": "ACME"}}</tool_call>'
    )
    assert scanner.calls == [{"name": "create_account", "input": {"name": "ACME"}}]
    assert scanner.looping is False


def test_a_single_empty_call_is_a_read_not_a_loop():
    scanner = _scan('<invoke name="list_accounts"></invoke>')
    assert scanner.calls == [{"name": "list_accounts", "input": {}}]
    assert scanner.looping is False


def test_a_correction_into_the_right_dialect_is_two_copies_not_a_loop():
    scanner = _scan(
        '<invoke name="get_payees"><parameter name="q">acme</parameter></invoke>\n'
        '<tool_call>{"name": "get_payees", "input": {"q": "acme"}}</tool_call>'
    )
    assert scanner.calls == [{"name": "get_payees", "input": {"q": "acme"}}]
    assert scanner.looping is False


def test_a_looping_reply_ends_the_round_at_once():
    """The provider's round reader stops as soon as the scanner says looping, without waiting
    for the grace (30 s) — let alone the 300 s ceiling the live loop ran to."""
    scanner = ToolCallScanner()
    watch = PostCallWatch(grace=30.0)

    async def stream():
        for _ in range(1000):
            list(scanner.feed(_LOOP_UNIT))
            yield {"type": "stream_event", "event": {"type": "content_block_delta"}}

    async def read() -> int:
        seen = 0
        async for _ in _until_call_grace(stream(), scanner, watch):
            seen += 1
        return seen

    seen = asyncio.run(read())
    assert watch.cut is True
    assert seen < 10
