"""Mid-turn message injection queue — concurrent-conversations RFC, injection phase.

While Lucius is mid-turn on a conversation, a message typed into that SAME conversation
is queued here instead of opening a second turn. The running turn drains this queue at
its own natural checkpoints (``assistant._run_turn``, right after a tool round's results
land and once more before the turn would otherwise end) and splices what it finds into
the SAME turn — see ``assistant._drain_injected_messages`` for the fencing, abort-stale-
pending, and audit-rebind mechanics that make that safe.

**Routing, and why this module alone is not enough (security review finding 7a).** The
caller (``api.chat``) decides "enqueue here" vs "spawn a new turn" by asking
``backend.ai.turns`` whether a turn is CURRENTLY REGISTERED for the session — never by
asking ``live_sessions.is_live()``, which also reads True while a turn is legitimately
PARKED awaiting the user's own approval decision (``assistant.has_pending_approval``);
injecting into that dead end would silently swallow the message with nothing left to
drain it. See ``turns.executing_turn_id``.

**Race-freedom.** ``enqueue()`` here and the caller's own registration check are both
plain, synchronous dict/list operations with no ``await`` between them — so is
``drain()`` and the running turn's own "have anything arrived?" decision at each of its
checkpoints. On a single-threaded, cooperative event loop, two such atomic spans can only
interleave whole-before-whole, never partially: either the caller's check-then-enqueue
completes before the turn's next drain (the turn sees it), or the turn's drain-and-decide
completes first (finding nothing) and the turn proceeds toward ending — in which case the
caller's OWN registration check, running after, correctly sees "not running" and falls
back to spawning a fresh turn instead. Neither ordering loses the message. The one gap
this does NOT close is a turn that CRASHES or is CANCELLED with something still queued
(no checkpoint left to reach) — ``api.chat`` claims any such residue on the NEXT POST for
that session and acknowledges it only after the assistant durably adopts its visible and
private invocation entries. Setup failure or cancellation nacks the exact objects back in
front of later arrivals (acceptance C3, C4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class QueuedSkillInvocation:
    skill_id: str
    argument_text: str = ""
    source: Literal["user", "model"] = "user"

    def __post_init__(self) -> None:
        if self.source not in {"user", "model"}:
            raise ValueError("queued skill invocation source must be 'user' or 'model'")


@dataclass(frozen=True)
class QueuedMessage:
    client_id: str
    text: str
    direct_invocations: tuple[QueuedSkillInvocation, ...] = ()
    queued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class QueueClaim:
    """Exclusive, reversible ownership of queued messages for one fresh turn."""

    session_id: str
    messages: tuple[QueuedMessage, ...]


_queues: dict[str, list[QueuedMessage]] = {}
_claims: dict[str, QueueClaim] = {}


def enqueue(
    session_id: str,
    client_id: str,
    text: str,
    *,
    direct_invocations: tuple[QueuedSkillInvocation, ...] = (),
) -> None:
    """Append one message to session_id's queue, FIFO (acceptance C4 — delivery order
    matches send order)."""
    if not session_id or not text:
        return
    _queues.setdefault(session_id, []).append(
        QueuedMessage(
            client_id=client_id,
            text=text,
            direct_invocations=direct_invocations,
        )
    )


def drain(session_id: str) -> list[QueuedMessage]:
    """Pop and return everything queued for session_id, in arrival order. Empty when
    nothing is queued — never raises, so a caller can call this unconditionally on every
    checkpoint without a separate ``has_pending`` guard."""
    return _queues.pop(session_id, [])


def claim(session_id: str) -> QueueClaim | None:
    """Temporarily transfer this session's queue to a fresh turn.

    The exact ``QueuedMessage`` objects remain owned here until ``ack``.  A failed or
    cancelled turn calls ``nack`` to prepend them ahead of messages that arrived while
    the claim was active, preserving global FIFO order without copying or duplication.
    """
    if session_id in _claims:
        return None
    messages = _queues.pop(session_id, [])
    if not messages:
        return None
    claimed = QueueClaim(
        session_id=session_id,
        messages=tuple(messages),
    )
    _claims[session_id] = claimed
    return claimed


def ack(claimed: QueueClaim) -> bool:
    """Permanently release an active claim after durable transcript adoption."""
    if _claims.get(claimed.session_id) is not claimed:
        return False
    _claims.pop(claimed.session_id, None)
    return True


def nack(claimed: QueueClaim) -> bool:
    """Return an active claim to the queue, ahead of later arrivals, exactly once."""
    if _claims.get(claimed.session_id) is not claimed:
        return False
    _claims.pop(claimed.session_id, None)
    later = _queues.get(claimed.session_id, [])
    _queues[claimed.session_id] = [*claimed.messages, *later]
    return True


def withdraw(session_id: str, client_id: str) -> bool:
    """Take back one message no turn has claimed yet. ``False`` once a turn has it (claimed or
    delivered): it is then that turn's. A voice call adds to its running turn, and a turn can end
    just before the words arrive; they then start the next turn instead, never sit here."""
    queued = _queues.get(session_id)
    if not queued:
        return False
    kept = [m for m in queued if m.client_id != client_id]
    if len(kept) == len(queued):
        return False
    if kept:
        _queues[session_id] = kept
    else:
        _queues.pop(session_id, None)
    return True


def has_pending(session_id: str) -> bool:
    return bool(_queues.get(session_id))


def _reset_for_tests() -> None:
    _queues.clear()
    _claims.clear()


__all__ = [
    "QueuedMessage",
    "QueuedSkillInvocation",
    "QueueClaim",
    "ack",
    "claim",
    "drain",
    "enqueue",
    "has_pending",
    "nack",
    "withdraw",
]
