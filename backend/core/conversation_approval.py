"""Turn-local approval MODE for one ordinary Lucius conversation.

The chat twin of ``core.automation_runner``'s ``AutomationApprovalContext``, and deliberately
the same shape: a ContextVar bound for the life of a turn, never a grant-store row. A mode
chosen for one conversation must not leak into another, must not survive the conversation,
and must never become a standing global grant — which is exactly what putting it in
``tool_permissions`` would make it (RFC ``2026-08-27-per-conversation-approval-mode``).

WHAT IT IS NOT: this decides whether the owner is ASKED, never WHAT MAY BE CALLED. It is not
a reachability list, it cannot widen a tool offer, and ``blocked`` is refused in
``registry.dispatch`` before any of this is consulted.

WHO MAY SET IT: a human, through REST or a Telegram owner's ``/approvals``. No tool writes it, and no tool schema advertises it.
That is the load-bearing security property, not a nicety — a conversation reads untrusted
text all day (a fetched page, an email body, an MCP result), so a model able to set its own
mode turns a prompt injection into an approvals-off switch. Automations' own
``permission_mode`` carries the same rule for the same reason.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

# The three modes, shared verbatim with an Automation's ``permission_mode`` — one vocabulary
# for one concept, so a person who has learned the Automations control already knows this one.
MANUAL = "manual"
AUTO = "auto"
SKIP = "skip"
MODES = (MANUAL, AUTO, SKIP)

#: What a conversation with no stored choice gets. ``manual`` is a NO-OP by construction (see
#: ``conversation_gate_verdict``), so an existing conversation behaves exactly as it did.
DEFAULT_MODE = MANUAL


def normalize_mode(value: object) -> str:
    """Coerce anything to a valid mode, falling back to ``manual``.

    Fails toward ASKING: an unreadable, absent or unrecognised value must never resolve to
    ``skip``. Callers store free-form JSON on the session row, so this is the only place the
    vocabulary is enforced.
    """
    text = str(value or "").strip().lower()
    return text if text in MODES else DEFAULT_MODE


@dataclass(frozen=True)
class ConversationApprovalContext:
    """The mode in force for the turn currently running in ``session_id``."""

    session_id: str
    mode: str = DEFAULT_MODE


_context: ContextVar[ConversationApprovalContext | None] = ContextVar(
    "conversation_approval_context", default=None
)


def bind_conversation_approval(context: ConversationApprovalContext) -> Token:
    return _context.set(context)


def reset_conversation_approval(token: Token) -> None:
    _context.reset(token)


def get_conversation_approval() -> ConversationApprovalContext | None:
    return _context.get()
