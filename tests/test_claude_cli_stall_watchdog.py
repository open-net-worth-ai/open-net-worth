"""A Claude Code CLI that goes silent is abandoned and retried once, quietly.

Persona QA round 5: a first message produced nothing for the whole 300 s ceiling, then
failed with a message naming the CLI and an env var; Retry answered in 35 s. A stall is
bounded by its own, much shorter clock (``ONW_CLAUDE_CLI_STALL``), the stalled attempt is
re-run once on a fresh process when nothing had reached the user yet, and the user-facing
failure is a plain sentence.
"""

from __future__ import annotations

import textwrap

import pytest

from backend.ai.models import claude_live_session as cls
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.api.chat import _error_event
from backend.core.errors import ProviderTimeoutError
from tests.fake_exe import python_cli


@pytest.fixture(autouse=True)
def _short_stall(monkeypatch):
    monkeypatch.setattr(cls.LiveSession, "stall_seconds", classmethod(lambda c: 0.5))


def _init() -> dict:
    return {"type": "system", "subtype": "init", "apiKeySource": "none"}


def _delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def _result(text: str) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _fake_cli(tmp_path, spawns: list[dict]):
    """Spawn N prints ``spawns[N]['events']`` then, if ``stall``, goes silent for good."""
    script = tmp_path / "claude"
    script = python_cli(
        script,
        textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import json, sys, time, pathlib
            here = pathlib.Path(__file__).resolve().parent
            counter = here / "spawns.txt"
            n = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(n + 1))
            spec = {spawns!r}[min(n, {len(spawns)} - 1)]
            for line in sys.stdin:
                for event in spec["events"]:
                    sys.stdout.write(json.dumps(event) + "\\n")
                    sys.stdout.flush()
                if spec["stall"]:
                    time.sleep(60)
                if spec.get("exit"):
                    sys.exit(0)
            """
        ).strip(),
    )
    return script


def _spawn_count(tmp_path) -> int:
    return int((tmp_path / "spawns.txt").read_text())


async def _turn(tmp_path, spawns, monkeypatch, sessions: bool):
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "1" if sessions else "0")
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path, spawns)))
    try:
        return [
            ev
            async for ev in provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                system="",
                model="haiku",
            )
        ]
    finally:
        # This loop's sessions only: the registry is per loop, and a session is bound to its own.
        for session in cls.pool().all():
            await session.close()


@pytest.mark.parametrize("sessions", [True, False], ids=["live-session", "one-shot"])
async def test_a_silent_first_attempt_is_retried_once_and_answers(tmp_path, monkeypatch, sessions):
    events = await _turn(
        tmp_path,
        [
            {"events": [_init()], "stall": True},
            {"events": [_init(), _delta("Hello"), _result("Hello")], "stall": False},
        ],
        monkeypatch,
        sessions,
    )
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hello"]
    assert _spawn_count(tmp_path) == 2


@pytest.mark.parametrize("sessions", [True, False], ids=["live-session", "one-shot"])
async def test_a_second_stall_fails_instead_of_retrying_forever(tmp_path, monkeypatch, sessions):
    with pytest.raises(ProviderTimeoutError) as caught:
        await _turn(tmp_path, [{"events": [_init()], "stall": True}], monkeypatch, sessions)
    assert caught.value.stalled is True
    assert _spawn_count(tmp_path) == 2


@pytest.mark.parametrize("sessions", [True, False], ids=["live-session", "one-shot"])
async def test_a_stall_after_text_reached_the_user_is_not_retried(tmp_path, monkeypatch, sessions):
    with pytest.raises(ProviderTimeoutError):
        await _turn(
            tmp_path,
            [{"events": [_init(), _delta("Hel")], "stall": True}],
            monkeypatch,
            sessions,
        )
    assert _spawn_count(tmp_path) == 1


async def test_a_session_that_keeps_ending_without_a_result_gives_up(tmp_path, monkeypatch):
    """The start-up retry once reused the outer attempt counter, resetting it every pass, so a
    CLI that kept exiting without a result event was respawned forever (review 2026-10-14)."""
    import asyncio

    from backend.core.errors import ExternalServiceError

    spawns = [{"events": [_init()], "stall": False, "exit": True}]
    with pytest.raises(ExternalServiceError, match="without a result event"):
        await asyncio.wait_for(_turn(tmp_path, spawns, monkeypatch, sessions=True), timeout=30)
    assert _spawn_count(tmp_path) == 2


def test_the_failure_the_user_reads_is_the_providers_own_message():
    """The owner wants the real cause, setting name included (launch review 2026-10-15)."""
    text = (
        "Claude Code CLI timed out after 300s (raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)."
    )
    event = _error_event(ProviderTimeoutError(text))
    assert event["message"] == text
