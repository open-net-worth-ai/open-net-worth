"""A live ``agy --input-format stream-json`` process reused across one conversation.

Antigravity's headless contract (https://antigravity.google/docs/cli/headless/) reads one JSON
user event per stdin line and answers each with step updates and ONE ``result`` event, then
waits for the next line — so a single process can serve every round of a conversation. This
module is that transport's half of the shared live-session machinery in ``cli_live_session``;
the prefix-keying rule and its reasoning are in ``claude_live_session``'s docstring and ADR
``2026-10-05-antigravity-cli-live-sessions``.

The first round of a session sends the whole flattened transcript (system instructions
included — ``agy`` has no system-prompt flag, so they ride in that first message and the
session key carries their hash). Later rounds send only what is new, normally the tool results.
The prompt always travels over stdin, never argv.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from backend.core.one_shot_turn import one_shot_active

from . import cli_live_session
from .cli_live_session import SessionPool
from .cli_tool_protocol import flatten_messages_with_images

_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens")


def sessions_enabled() -> bool:
    """``ONW_ANTIGRAVITY_CLI_SESSIONS=0`` returns the transport to a process per round."""
    return cli_live_session.env_flag("ONW_ANTIGRAVITY_CLI_SESSIONS") and not one_shot_active()


def _idle_seconds() -> float:
    return cli_live_session.env_seconds(
        "ONW_ANTIGRAVITY_CLI_SESSION_IDLE", cli_live_session.DEFAULT_IDLE_SECONDS, floor=30.0
    )


def system_key(system: str) -> str:
    """Session-key discriminator for the system prompt — it is sent in message one, not argv."""
    return "__system__" + hashlib.sha256((system or "").encode("utf-8")).hexdigest()


def with_system(system: str, prompt: str) -> str:
    if not (system or "").strip():
        return prompt
    return f"# System instructions\n\n{system.strip()}\n\n---\n\n{prompt}"


def first_round(system: str):
    def render(messages: list[dict]) -> "tuple[str, list[dict]]":
        transcript, images = flatten_messages_with_images(messages)
        return with_system(system, transcript), images

    return render


class LiveSession(cli_live_session.LiveSession):
    """One long-lived ``agy`` child, holding one conversation."""

    LABEL = "Antigravity CLI"
    LOG_PREFIX = "antigravity_cli"
    TIMEOUT_ENV = "ONW_ANTIGRAVITY_CLI_TIMEOUT"
    TIMEOUT_DEFAULT = 600.0

    def __init__(self, argv: list[str], env: dict, workdir: str):
        super().__init__(argv, env, workdir)
        # `result.usage` is a running total over the whole session, not the turn's own.
        self._usage_total: dict[str, int] = {}

    def is_final(self, event: dict) -> bool:
        return event.get("event") == "result"

    def usage_delta(self, usage: dict) -> dict[str, int]:
        """This turn's own token counts: the session's running total minus the last one seen."""
        delta: dict[str, int] = {}
        for name in _USAGE_FIELDS:
            now = int(usage.get(name) or 0)
            step = now - self._usage_total.get(name, 0)
            # A counter that went DOWN means the session's totals restarted.
            delta[name] = step if step >= 0 else now
            self._usage_total[name] = now
        return delta

    async def send(self, text: str, images: Optional[list[dict]] = None) -> None:
        # The documented user event carries text only: an image keeps the transcript's
        # "[attached image N]" marker, but its bytes are not forwarded.
        payload = json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False)
        await self._write((payload + "\n").encode("utf-8"))


_pools: "dict[int, SessionPool]" = {}


def pool() -> SessionPool:
    return cli_live_session.pool_for(_pools, lambda: _idle_seconds())


async def acquire(
    argv: list[str],
    env: dict,
    messages: list[dict],
    key_argv: list[str],
    system: str,
    fresh: bool = False,
) -> "tuple[LiveSession, str, list[dict], bool]":
    """A pooled session for this round — see :func:`cli_live_session.acquire`."""
    return await cli_live_session.acquire(
        pool(),
        LiveSession,
        "onw-antigravity-cli-",
        argv,
        env,
        messages,
        key_argv,
        render_full=first_round(system),
        fresh=fresh,
    )


async def spawn(argv: list[str], env: dict) -> LiveSession:
    """An unpooled session for a one-shot run (sessions off, or a non-chat completion)."""
    return await cli_live_session.spawn(LiveSession, "onw-antigravity-cli-", argv, env)


async def release(
    key_argv: list[str], session: LiveSession, messages: list[dict], ok: bool
) -> None:
    await cli_live_session.release(pool(), key_argv, session, messages, ok)


async def close_all() -> None:
    await cli_live_session.close_all(_pools)
