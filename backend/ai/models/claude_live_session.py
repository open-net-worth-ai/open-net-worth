"""A live ``claude -p`` process reused across the turns of ONE conversation.

The Claude CLI transport spawns a process per model turn and re-sends the whole transcript
each time. That is correct but expensive: the CLI pays its own start-up, and every token of
history is billed as fresh input. Measured on the real binary, a second message sent into an
already-running process cost **2 input tokens with 2,690 read from cache** — the prior
context is served entirely from the CLI's prompt cache.

OpenClaw reuses the CLI the same way (``ClaudeLiveSession``, "to avoid reinitializing context
per turn"), and reports that Anthropic staff confirmed OpenClaw-style ``claude -p`` reuse is
sanctioned. Unlike Codex's ``app-server``, which multiplexes many threads over ONE process,
``claude -p --input-format stream-json`` is one process per conversation — so reuse here
necessarily means a live process per chat.

**The hazard, and the rule.** Once the CLI holds the conversation, it — not
``assistant.run`` — is the authority on what the model has seen. But our transcript is not
append-only: context folding, decay, checkpoint restore and rehydration all REWRITE history
behind the model's back, and a live session cannot see any of that. A session that survived
such a rewrite would silently diverge from the transcript we persist.

So a session is keyed by **the exact prefix of the conversation it has already consumed**
(:func:`_fingerprint` per message). Every turn re-checks that the incoming ``messages`` still
begin with that prefix. Any rewrite — fold, decay, restore, edit, branch — changes the
prefix, no session matches, and the turn starts a fresh process with the full transcript.
Drift is therefore structurally impossible rather than merely unlikely: we never *decide* to
invalidate, the match simply fails. ADR ``2026-08-01-claude-cli-live-sessions``.

The CLI's own reply is never re-sent: it already holds it. On the next turn the reply appears
in our list and the matcher steps over it, but not over an alert pushed after it.

Sessions are UNCAPPED in count (owner's call, 2026-08-01) — a live chat keeps its process.
They are still reaped when idle, because a process nobody has spoken to in half an hour is
dead weight and its chat loses nothing by re-sending on return.

The transport-agnostic half — fingerprinting, the pool, prefix matching, acquire/release —
lives in ``cli_live_session`` and is shared with the Antigravity transport; this module keeps
only what is Claude's: the stream-json user message, the ``result`` terminator and the env
knobs. Its public names are unchanged.
"""

from __future__ import annotations

import json
from typing import Optional

from backend.ai.image_payloads import decode_image_payloads

from . import cli_live_session
from .cli_live_session import SessionPool, fingerprints, pending_turn  # noqa: F401

# A child that emits nothing for this long, first event included, is wedged (a stalled first
# turn once sat silent for the whole 300 s ceiling and then answered in 35 s on a retry).
STALL_ENV = "ONW_CLAUDE_CLI_STALL"
STALL_DEFAULT = 120.0


def sessions_enabled() -> bool:
    """``ONW_CLAUDE_CLI_SESSIONS=0`` returns the transport to a process per turn."""
    return cli_live_session.env_flag("ONW_CLAUDE_CLI_SESSIONS")


def _idle_seconds() -> float:
    return cli_live_session.env_seconds(
        "ONW_CLAUDE_CLI_SESSION_IDLE", cli_live_session.DEFAULT_IDLE_SECONDS, floor=30.0
    )


class LiveSession(cli_live_session.LiveSession):
    """One long-lived ``claude -p`` child, holding one conversation."""

    LABEL = "Claude Code CLI"
    LOG_PREFIX = "claude_cli"
    TIMEOUT_ENV = "ONW_CLAUDE_CLI_TIMEOUT"
    TIMEOUT_DEFAULT = 300.0
    STALL_ENV = STALL_ENV
    STALL_DEFAULT = STALL_DEFAULT

    def is_final(self, event: dict) -> bool:
        return event.get("type") == "result"

    async def send(self, text: str, images: Optional[list[dict]] = None) -> None:
        decode_image_payloads(images or [])
        content: list[dict] = [{"type": "text", "text": text}]
        for image in images or []:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.get("media_type") or "image/jpeg",
                        "data": image.get("data") or "",
                    },
                }
            )
        payload = json.dumps(
            {"type": "user", "message": {"role": "user", "content": content}},
            ensure_ascii=False,
        )
        await self._write((payload + "\n").encode("utf-8"))


_pools: "dict[int, SessionPool]" = {}


def pool() -> SessionPool:
    return cli_live_session.pool_for(_pools, lambda: _idle_seconds())


def pending_messages(messages: list[dict], consumed: int) -> str:
    """Render only what the session has NOT seen, as one user message."""
    return pending_turn(messages, consumed)[0]


async def acquire(
    argv: list[str], env: dict, messages: list[dict], key_argv: Optional[list[str]] = None
) -> "tuple[LiveSession, str, list[dict], bool]":
    """Get a session for this turn — see :func:`cli_live_session.acquire`."""
    return await cli_live_session.acquire(
        pool(), LiveSession, "onw-claude-cli-", argv, env, messages, key_argv
    )


async def release(
    key_argv: list[str], session: LiveSession, messages: list[dict], ok: bool
) -> None:
    await cli_live_session.release(pool(), key_argv, session, messages, ok)


async def close_all() -> None:
    await cli_live_session.close_all(_pools)
