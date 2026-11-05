"""Tests for the Claude CLI live session.

The behaviour that matters is not "does it reuse a process" but "does it REFUSE to reuse one
whenever the transcript was rewritten behind the model's back". Folding, decay, checkpoint
restore and rehydration all rewrite history; a session that survived one would answer from a
transcript we no longer hold. See ADR 2026-08-01-claude-cli-live-sessions.
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap

import pytest

from backend.ai.models import claude_live_session as cls
from backend.ai.models import cli_live_session

USER = {"role": "user", "content": "what did I spend?"}
REPLY = {"role": "assistant", "content": "You spent $10."}
FOLLOW = {"role": "user", "content": "and last month?"}


# ── prefix matching: the invalidation rule ────────────────────────────────────


def test_a_session_matches_an_append_only_continuation():
    s = cls.LiveSession([], {}, "/tmp")
    s.consumed = cls.fingerprints([USER])
    assert s.matches(cls.fingerprints([USER, REPLY, FOLLOW]))


def test_a_rewritten_message_breaks_the_match():
    """Context folding replaces earlier turns with a summary — that must MISS."""
    s = cls.LiveSession([], {}, "/tmp")
    s.consumed = cls.fingerprints([USER, REPLY])
    folded = [{"role": "user", "content": "[summary of earlier turns]"}, REPLY]
    assert not s.matches(cls.fingerprints(folded))


def test_a_truncated_history_breaks_the_match():
    """A checkpoint restore can make the transcript SHORTER than what the CLI holds."""
    s = cls.LiveSession([], {}, "/tmp")
    s.consumed = cls.fingerprints([USER, REPLY, FOLLOW])
    assert not s.matches(cls.fingerprints([USER]))


def test_an_edited_tool_result_breaks_the_match():
    before = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "42 rows"}],
    }
    after = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "7 rows"}],
    }
    s = cls.LiveSession([], {}, "/tmp")
    s.consumed = cls.fingerprints([USER, before])
    assert not s.matches(cls.fingerprints([USER, after]))


def test_a_different_image_breaks_the_session_match():
    """Two image blocks are not interchangeable just because both render as attachments."""
    first = {
        "role": "user",
        "content": [{"type": "image", "source": {"media_type": "image/png", "data": "Zmlyc3Q="}}],
    }
    second = {
        "role": "user",
        "content": [{"type": "image", "source": {"media_type": "image/png", "data": "c2Vjb25k"}}],
    }
    assert cls.fingerprints([first]) != cls.fingerprints([second])


def test_a_fresh_session_never_matches():
    """An empty prefix must not match everything — that would skip the whole transcript."""
    assert not cls.LiveSession([], {}, "/tmp").matches(cls.fingerprints([USER]))


# ── what actually gets sent ───────────────────────────────────────────────────


def test_only_the_unseen_tail_is_sent():
    text = cls.pending_messages([USER, REPLY, FOLLOW], consumed=1)
    assert "and last month?" in text
    # The CLI generated its own reply; sending it back would duplicate its words at it.
    assert "You spent $10." not in text
    assert "what did I spend?" not in text


def test_a_first_turn_sends_the_whole_transcript():
    text = cls.pending_messages([USER, REPLY, FOLLOW], consumed=0)
    assert "what did I spend?" in text and "and last month?" in text


def test_an_alert_pushed_after_the_reply_is_sent_to_the_session():
    """The CLI wrote its reply, not the alert that landed after it, and the person may be
    answering that alert."""
    alert = {"role": "assistant", "content": "ACME fell 9% today."}
    text = cls.pending_messages([USER, REPLY, alert, FOLLOW], consumed=1)
    assert "You spent $10." not in text
    assert "ACME fell 9% today." in text
    assert "and last month?" in text


# ── the real thing, over pipes ────────────────────────────────────────────────


def _fake_cli(tmp_path, reply_text="hi"):
    """A `claude` stand-in that answers per stdin line and records what it received."""
    script = tmp_path / "claude"
    events = [
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": reply_text},
            },
        },
        {
            "type": "result",
            "result": reply_text,
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    ]
    payload = "\n".join(json.dumps(e) for e in events)
    script.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import json, sys, pathlib
            here = pathlib.Path(__file__).resolve().parent
            log = here / "received.jsonl"
            for line in sys.stdin:
                with open(log, "a") as fh:
                    fh.write(line)
                sys.stdout.write({payload!r} + "\\n")
                sys.stdout.flush()
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _argv(cli):
    return [sys.executable, str(cli)]


@pytest.fixture(autouse=True)
def _clean_pool():
    """Sync so it can guard the pure-logic tests too; kills children directly."""

    def purge():
        for pool in cls._pools.values():
            for session in pool.all():
                proc = session.proc
                if proc is not None and proc.returncode is None:
                    try:
                        proc.kill()
                    except (ProcessLookupError, OSError):
                        pass
        cls._pools.clear()

    purge()
    yield
    purge()


async def _run_turn(argv, messages, tmp_path):
    session, pending, images, reused = await cls.acquire(argv, {"PATH": "/usr/bin:/bin"}, messages)
    await session.send(pending, images=images)
    events = [e async for e in session.events()]
    await cls.release(argv, session, messages, ok=any(e.get("type") == "result" for e in events))
    return session, pending, reused


async def test_the_second_turn_reuses_the_process_and_sends_only_the_new_message(tmp_path):
    cli = _fake_cli(tmp_path)
    argv = _argv(cli)

    s1, sent1, reused1 = await _run_turn(argv, [USER], tmp_path)
    pid1 = s1.proc.pid
    assert reused1 is False
    assert "what did I spend?" in sent1

    s2, sent2, reused2 = await _run_turn(argv, [USER, REPLY, FOLLOW], tmp_path)
    assert reused2 is True, "the second turn must land on the live process"
    assert s2.proc.pid == pid1
    # THE measurement that motivated this: only the new message crosses the wire.
    assert "and last month?" in sent2
    assert "what did I spend?" not in sent2
    await cls.close_all()


async def test_a_live_session_sends_an_image_only_when_that_message_is_new(tmp_path):
    cli = _fake_cli(tmp_path)
    argv = _argv(cli)
    image_user = {
        "role": "user",
        "content": [
            {"type": "text", "text": "read this"},
            {
                "type": "image",
                "source": {"media_type": "image/png", "data": "aW1hZ2U="},
            },
        ],
    }

    first, _sent, reused = await _run_turn(argv, [image_user], tmp_path)
    pid = first.proc.pid
    second, _sent, reused_second = await _run_turn(argv, [image_user, REPLY, FOLLOW], tmp_path)

    rows = [json.loads(line) for line in (tmp_path / "received.jsonl").read_text().splitlines()]
    assert reused is False
    assert reused_second is True and second.proc.pid == pid
    assert [part["type"] for part in rows[0]["message"]["content"]] == ["text", "image"]
    assert [part["type"] for part in rows[1]["message"]["content"]] == ["text"]
    await cls.close_all()


async def test_a_rewritten_transcript_spawns_a_fresh_process(tmp_path):
    cli = _fake_cli(tmp_path)
    argv = _argv(cli)

    s1, _sent, _r = await _run_turn(argv, [USER], tmp_path)
    pid1 = s1.proc.pid

    # A fold: the earlier turn is replaced by a summary. The session must NOT be reused.
    folded = [{"role": "user", "content": "[summary of earlier turns]"}, FOLLOW]
    s2, sent2, reused2 = await _run_turn(argv, folded, tmp_path)
    assert reused2 is False
    assert s2.proc.pid != pid1
    assert "[summary of earlier turns]" in sent2, "a fresh session gets the FULL transcript"
    await cls.close_all()


async def test_a_failed_turn_retires_the_session(tmp_path):
    cli = _fake_cli(tmp_path)
    argv = _argv(cli)
    session, pending, images, _ = await cls.acquire(argv, {"PATH": "/usr/bin:/bin"}, [USER])
    await session.send(pending, images=images)
    async for _ in session.events():
        pass
    # ok=False stands in for "no result event" — the session state is unknown.
    await cls.release(argv, session, [USER], ok=False)
    assert not session.alive
    assert cls.pool().find(argv, cls.fingerprints([USER, REPLY, FOLLOW])) is None


async def test_a_live_session_start_failure_removes_its_workdir(tmp_path, monkeypatch):
    workdir = tmp_path / "failed-live-start"

    def make_workdir(**_kwargs):
        workdir.mkdir()
        return str(workdir)

    async def fail_start(self):
        raise OSError("cannot start claude")

    monkeypatch.setattr(cli_live_session.tempfile, "mkdtemp", make_workdir)
    monkeypatch.setattr(cls.LiveSession, "start", fail_start)

    with pytest.raises(OSError, match="cannot start claude"):
        await cls.acquire(["claude"], {}, [USER])

    assert not workdir.exists()
    assert cls.pool().all() == []


async def test_a_different_system_prompt_never_shares_a_session(tmp_path):
    """system/model/effort/tools are spawn-time FLAGS — a session cannot serve both."""
    cli = _fake_cli(tmp_path)
    argv_a = _argv(cli) + ["--system-prompt", "A"]
    argv_b = _argv(cli) + ["--system-prompt", "B"]

    s1, _s, _r = await _run_turn(argv_a, [USER], tmp_path)
    s2, _s2, reused = await _run_turn(argv_b, [USER, REPLY, FOLLOW], tmp_path)
    assert reused is False
    assert s2.proc.pid != s1.proc.pid
    await cls.close_all()


async def test_idle_sessions_are_reaped(tmp_path, monkeypatch):
    cli = _fake_cli(tmp_path)
    argv = _argv(cli)
    s1, _sent, _r = await _run_turn(argv, [USER], tmp_path)
    assert s1.alive

    # Age the session rather than patching `time.monotonic` — that name is the stdlib
    # module's, and monkeypatching it would also skew asyncio's own scheduling.
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSION_IDLE", "30")
    s1.last_used -= 10_000
    doomed = cls.pool().reap_idle()
    for d in doomed:
        await d.close()
    assert s1 in doomed and not s1.alive


async def test_the_idle_cutoff_is_read_at_reap_time(monkeypatch):
    """The pool must look `_idle_seconds` up late, so patching the module function works."""
    monkeypatch.setattr(cls, "_idle_seconds", lambda: 12345.0)
    assert cls.pool()._idle_seconds() == 12345.0


def test_sessions_can_be_switched_off(monkeypatch):
    assert cls.sessions_enabled() is True
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "0")
    assert cls.sessions_enabled() is False
