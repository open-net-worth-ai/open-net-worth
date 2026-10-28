"""Per-turn chat-session id — a ContextVar leaf (ad-hoc-code-execution RFC §4.2).

``assistant.run`` sets the active conversation's session id here for the duration of a
turn so a tool handler can scope a per-conversation decision to it WITHOUT the session id
being threaded through every ``execute_tool`` call. Mirrors the ``core.email_consumer`` /
``core.auto_apply_scope`` ContextVar seams.

Used to attribute a turn to its conversation — e.g. routing a confirm-card grant to the
Automation that owns the run. Default ``None`` (no active turn / a headless or background
path that never set it), so reading it off the chat path is a pure no-op.

Imports nothing internal — a leaf, so it can never create an import cycle.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_session_id: ContextVar[Optional[str]] = ContextVar("chat_session_id", default=None)


def set_session_id(session_id: Optional[str]) -> None:
    """Bind the active conversation's session id for this turn."""
    _session_id.set(session_id)


def get_session_id() -> Optional[str]:
    """The active conversation's session id, or ``None`` when no turn set it."""
    return _session_id.get()


def clear_session_id() -> None:
    """Clear the binding at turn end so it never leaks into the next turn."""
    _session_id.set(None)


# Session IDs (or prefixes) used by background/headless paths that must never appear in the
# user-facing conversation list. The rows are kept intact (the agents still read and write
# them); only the LISTING — and anything that searches "the user's conversations" — hides them.
# Exact IDs: "scheduled" (legacy; may still exist on old instances).
# Prefix: "scheduled-" (per-run sessions from the prompt_lucius runner).
INTERNAL_SESSION_IDS: frozenset[str] = frozenset({"scheduled"})
INTERNAL_SESSION_PREFIXES: tuple[str, ...] = ("scheduled-",)


# Sessions a run of Lucius owns rather than the person typing: a Capture of a notification, an
# Automation run (and the follow-ups typed into it), a headless or scheduled turn. Their text is
# not the person's own words, so they may close a Recent memory entry but never add one.
RUN_SESSION_PREFIXES: tuple[str, ...] = ("capture-", "automation-", "headless-", "scheduled-")


def is_run_session(session_id: Optional[str]) -> bool:
    """True for a session a Capture, Automation, headless or scheduled run owns."""
    return bool(session_id) and str(session_id).startswith(RUN_SESSION_PREFIXES)


def is_internal_session(session_id: str) -> bool:
    """True when ``session_id`` identifies a background/headless session (never shown to the user)."""
    if session_id in INTERNAL_SESSION_IDS:
        return True
    return any(session_id.startswith(p) for p in INTERNAL_SESSION_PREFIXES)
