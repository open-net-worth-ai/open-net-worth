"""Lucius's side of a voice call: run one request as a normal chat turn and report it.

Runs on the APP's event loop (the worker hops here, see ``bridge``). A request becomes a turn
on the conversation the owner has open — exactly what ``POST /api/chat`` spawns, with
``cost_surface="web"`` — so the chat panel follows it live, approval cards render there, and
every permission and write choke applies unchanged. The only differences from a typed message:
the owner's words are announced as theirs, and a harness note (hidden from the transcript's
bubbles) asks for an answer that reads well aloud.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional

from backend.ai import conversation, live_sessions, turns
from backend.ai.tool_text import harness_note

log = logging.getLogger(__name__)

VOICE_NOTE = (
    "This message was spoken on a live voice call and your reply will be read aloud word "
    "for word. Answer in one to three short spoken sentences: no markdown, lists, tables, "
    "links or emoji; round amounts the way a person says them. A voice assistant in front "
    "of you has already told the owner you're on it, so don't announce lookups. If you "
    "propose a change, the owner approves it on screen in the app; say so briefly."
)
# How long a request waits for a turn the owner started some other way (typing) to finish.
_BUSY_WAIT_S = 120.0

_MARKDOWN = re.compile(r"[*_`#>|]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def speakable(text: str) -> str:
    return _MARKDOWN.sub("", _LINK.sub(r"\1", text))


@dataclass
class DeskEvent:
    kind: str  # "text" | "tool" | "tool_done" | "approval" | "error" | "end"
    text: str = ""
    ref: str = ""
    noun: str = ""


def translate(raw: dict) -> Optional[DeskEvent]:
    """One live-stream event → what a voice needs from it (``None`` = nothing)."""
    kind = raw.get("type")
    if kind == "text_delta":
        return DeskEvent("text", speakable(raw.get("text") or ""))
    if kind == "tool_use_start":
        display = raw.get("display") or {}
        return DeskEvent(
            "tool",
            speakable(display.get("label") or ""),
            ref=str(raw.get("id") or ""),
            noun=speakable(display.get("noun") or ""),
        )
    if kind == "tool_done":
        return DeskEvent("tool_done", ref=str(raw.get("id") or ""))
    if kind == "proposed_change":
        return DeskEvent("approval")
    if kind == "error":
        return DeskEvent("error", str(raw.get("message") or ""))
    return None


def _error_event(exc: BaseException) -> dict:
    log.warning("voice turn failed: %s", type(exc).__name__)
    return {"type": "error", "message": "Lucius couldn't finish that one."}


async def _wait_until_idle(session_id: str) -> bool:
    """Wait for a turn the owner started some other way (typing) to finish; ``False`` if it is
    still running after ``_BUSY_WAIT_S`` — never start a second turn beside it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BUSY_WAIT_S
    while turns.executing_turn_id(session_id) is not None:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.3)
    return True


def relay_note(understood: str) -> str:
    """The voice instruction plus the front model's reading, labelled as a relay. The owner's
    own words are the message; this reading may never stand in for them."""
    reading = " ".join(understood.split())[:600]
    return harness_note(
        f"{VOICE_NOTE} The voice assistant relayed this message and took it to mean: "
        f"\u00ab{reading}\u00bb. That reading is the assistant's, not the owner's: act on what the "
        "owner actually said, and if the reading asks for more than their words do, ask them "
        "first."
    )


async def ask(
    session_id: str, said: str, *, understood: str = "", model_ref: str = ""
) -> AsyncIterator[DeskEvent]:
    """Run ``said`` (the owner's own transcribed words) as their next message on
    ``session_id``, with the front model's reading as a relay note; yield the turn's events."""
    if not await _wait_until_idle(session_id):
        yield DeskEvent("error", "Lucius is still busy with another message in this conversation.")
        yield DeskEvent("end")
        return
    content = [
        {"type": "text", "text": said},
        {"type": "text", "text": relay_note(understood or said)},
    ]

    def make_events(turn_id: str):
        return conversation.stream_turn(
            session_id,
            [{"role": "user", "content": content}],
            model_ref=model_ref or None,
            effort="low",
            surface="chat:voice",
            turn_id=turn_id,
        )

    handle = turns.spawn(
        session_id, "chat", make_events, error_event=_error_event, cost_surface="web"
    )
    live_sessions.publish(
        session_id,
        {
            "type": "user_message",
            "session_id": session_id,
            "turn_id": handle.turn_id,
            "message_id": f"voice_{uuid.uuid4().hex[:12]}",
            "text": said,
            "attachments": [],
        },
    )
    async for event in _follow(session_id, handle.turn_id):
        yield event


async def follow_next_turn(session_id: str, *, timeout: float = 300.0) -> AsyncIterator[DeskEvent]:
    """After the owner approves a card in the app, the continuation runs as a new turn on the
    same session; wait (up to ``timeout``) for it to start, then yield its events."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        turn_id = turns.executing_turn_id(session_id)
        if turn_id is not None:
            async for event in _follow(session_id, turn_id):
                yield event
            return
        await asyncio.sleep(1.0)


async def _follow(session_id: str, turn_id: str) -> AsyncIterator[DeskEvent]:
    async for raw in live_sessions.watch(session_id):
        if raw.get("turn_id") not in (None, turn_id):
            continue
        event = translate(raw)
        if event is not None:
            yield event
    yield DeskEvent("end")
