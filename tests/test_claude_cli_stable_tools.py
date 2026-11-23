"""A tool loaded mid-conversation must not cost the Claude CLI its live session.

The tool manifest rides the system prompt, and a live session is keyed on its spawn argv. So,
before ``claude_cli_stable_tool_prompt``, a ``find_tools`` load changed the key: the next round
spawned a fresh process that re-wrote the whole ~40K prompt prefix to cache (QA 2026-11-18:
41-44K cache writes on every tool-loading turn, 1-2K on the others). With the flag on, the
session is keyed WITHOUT the manifest and a newly loaded tool's schema rides the next message
instead. Anthropic's ``defer_loading`` and Claude Code's own ToolSearch work the same way.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from backend.ai.models import claude_live_session as cls
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from tests.fake_exe import python_cli

# The text protocol's own suite: it pins `claude_cli_native_tools` off, whatever the default.
pytestmark = pytest.mark.flags(claude_cli_native_tools=False)

USER = {"role": "user", "content": "what did I spend?"}
REPLY = {"role": "assistant", "content": "hi"}
FOLLOW = {"role": "user", "content": "and last month?"}
AGAIN = {"role": "user", "content": "and the month before?"}

TOOL_A = {"name": "list_accounts", "description": "List accounts.", "input_schema": {}}
TOOL_B = {
    "name": "get_spending_summary",
    "description": "Spending for one month.",
    "input_schema": {"type": "object", "properties": {"month": {"type": "integer"}}},
}


def _fake_cli(tmp_path):
    """A `claude` stand-in recording each spawn's system prompt and every stdin line."""
    script = tmp_path / "claude"
    script = python_cli(
        script,
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            here = pathlib.Path(__file__).resolve().parent
            args = sys.argv[1:]
            system = ""
            if "--system-prompt-file" in args:
                system = pathlib.Path(args[args.index("--system-prompt-file") + 1]).read_text()
            with open(here / "spawns.jsonl", "a") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "system": system}) + "\\n")
            for line in sys.stdin:
                with open(here / "received.jsonl", "a") as fh:
                    fh.write(json.dumps({"pid": os.getpid(), "line": line}) + "\\n")
                for ev in (
                    {"type": "system", "subtype": "init", "apiKeySource": "none"},
                    {"type": "stream_event", "event": {"type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "hi"}}},
                    {"type": "result", "result": "hi", "total_cost_usd": 0.01,
                     "usage": {"input_tokens": 1, "output_tokens": 1}},
                ):
                    sys.stdout.write(json.dumps(ev) + "\\n")
                sys.stdout.flush()
            """
        ).strip(),
    )
    return script


def _rows(tmp_path, name):
    path = tmp_path / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _sent(tmp_path) -> list[str]:
    """The text of each message written to a CLI process, in order."""
    out = []
    for row in _rows(tmp_path, "received.jsonl"):
        content = json.loads(row["line"])["message"]["content"]
        out.append("".join(part.get("text") or "" for part in content))
    return out


@pytest.fixture(autouse=True)
def _sessions_on(monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "1")
    cls._pools.clear()
    yield
    for pool in cls._pools.values():
        for session in pool.all():
            if session.proc is not None and session.proc.returncode is None:
                session.proc.kill()
    cls._pools.clear()


async def _round(provider, messages, tools):
    return [
        e
        async for e in provider.stream_chat(
            messages, tools=tools, system="SYS", model="haiku", effort="medium"
        )
    ]


@pytest.mark.flags(claude_cli_stable_tool_prompt=True)
async def test_a_newly_loaded_tool_rides_the_next_message_on_the_same_process(tmp_path):
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))

    await _round(provider, [USER], [TOOL_A])
    await _round(provider, [USER, REPLY, FOLLOW], [TOOL_A, TOOL_B])

    spawns = _rows(tmp_path, "spawns.jsonl")
    assert len(spawns) == 1, "loading a tool must not spawn a second process"
    assert "### list_accounts" in spawns[0]["system"]
    first, second = _sent(tmp_path)
    assert "### get_spending_summary" not in first
    assert "### get_spending_summary" in second, "the new tool's schema rides the message"
    assert '"month"' in second
    assert "### list_accounts" not in second, "a tool the process already holds is not re-sent"
    assert "and last month?" in second
    await cls.close_all()


@pytest.mark.flags(claude_cli_stable_tool_prompt=True)
async def test_a_tool_is_announced_once_then_never_again(tmp_path):
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))

    await _round(provider, [USER], [TOOL_A])
    await _round(provider, [USER, REPLY, FOLLOW], [TOOL_A, TOOL_B])
    await _round(provider, [USER, REPLY, FOLLOW, REPLY, AGAIN], [TOOL_B, TOOL_A])

    assert len(_rows(tmp_path, "spawns.jsonl")) == 1, "tool ORDER is not a reason to respawn"
    third = _sent(tmp_path)[2]
    assert "###" not in third
    assert "and the month before?" in third
    await cls.close_all()


@pytest.mark.flags(claude_cli_stable_tool_prompt=True)
async def test_a_removed_tool_starts_a_fresh_process(tmp_path):
    """The process still believes in a tool it was told about — it cannot be un-told."""
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))

    await _round(provider, [USER], [TOOL_A, TOOL_B])
    await _round(provider, [USER, REPLY, FOLLOW], [TOOL_B])

    spawns = _rows(tmp_path, "spawns.jsonl")
    assert len(spawns) == 2
    assert "### list_accounts" not in spawns[1]["system"]
    await cls.close_all()


@pytest.mark.flags(claude_cli_stable_tool_prompt=True)
async def test_a_changed_tool_schema_starts_a_fresh_process(tmp_path):
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))
    changed = {**TOOL_A, "description": "List accounts, now with balances."}

    await _round(provider, [USER], [TOOL_A])
    await _round(provider, [USER, REPLY, FOLLOW], [changed])

    spawns = _rows(tmp_path, "spawns.jsonl")
    assert len(spawns) == 2
    assert "now with balances" in spawns[1]["system"]
    await cls.close_all()


@pytest.mark.flags(claude_cli_stable_tool_prompt=False)
async def test_flag_off_a_loaded_tool_still_respawns_with_the_full_manifest(tmp_path):
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))

    await _round(provider, [USER], [TOOL_A])
    await _round(provider, [USER, REPLY, FOLLOW], [TOOL_A, TOOL_B])

    spawns = _rows(tmp_path, "spawns.jsonl")
    assert len(spawns) == 2
    assert "### get_spending_summary" in spawns[1]["system"]
    await cls.close_all()
