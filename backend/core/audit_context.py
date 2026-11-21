"""Per-run AUDIT scope — the transaction-provenance seam.

Every transaction mutation (create/update/delete/restore) funnels through the ledger
write choke points in ``services/ledger.py``. To record *who* changed a row, *through
what surface*, *driven by what request*, and link it back to the originating chat —
the ledger needs context that only the UPPER layers possess (the chat session id, the
acting connector, the user's message, the HTTP route). Threading that as parameters
would mean a wide signature change across ``create_transaction`` / ``bulk_*`` /
``update_transaction`` and every caller and test.

So this is a ContextVar seam, a direct clone of ``core.email_consumer``: the upper
layer (``ai.assistant`` for chat, ``connectors.chat.run`` for messaging channels,
``api.ledger`` for the UI/REST, ``ai.automation_runner_adapter`` for Automation runs, ``mcp.adapter``
for MCP, ``services.scheduling`` for recurring posts, ``services.import_common`` for
imports) SETS the scope before its write and CLEARS it in a ``finally``; the ledger
choke point READS it at persist time and stamps the audit event.

It lives in ``core`` (a leaf — stdlib only) for the SAME reason ``core.email_consumer``
and ``core.auto_apply_scope`` do: it is a seam shared across a layer boundary. The
setters are called from ``api``/``ai`` WITHOUT those modules being imported by
``services``/``db`` (import-linter Contracts 1 & 3); the ledger (``services``) only READS.

Propagation mirrors ``core.email_consumer``: the upper layer ``await``s straight down
into the write (no fresh ``contextvars.Context`` on the path), so a scope set above is
visible at the ledger. The default is ``None`` — a path that never sets a scope (a test,
a one-off script, an un-instrumented entrypoint) resolves to ``None`` and the ledger
records a minimal fallback event (actor "system", surface "unknown"). No write is ever
blocked by a missing scope.

DESIGN: ``.claude/rfcs/transaction-provenance.md``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class AuditScope:
    """The context for the current mutation, set by the entrypoint that owns it.

    - ``actor``    — WHO: "user" | "ai" | "system"
    - ``surface``  — THROUGH WHAT: "ui" | "chat:web" | "chat:telegram" | "chat:slack"
      | "chat:discord" | "chat:whatsapp" | "mcp" | "automation"
      | "import:<format>" | "migration:<name>"
    - ``session_id`` — chat session id (deep link), when applicable
    - ``sender_id``  — channel sender id (telegram/slack/...), when applicable
    - ``intent``     — truncated (≤``INTENT_MAX_LEN``) originating user message/request
    - ``note``       — freeform (model name, "bulk import", ...)
    - ``automation`` — the NAMED automation that fired this run, when the actor was triggered
      rather than acting directly: the Automation TITLE behind the run ("Weekly Fraud
      Checking") or posted a recurring template — a snapshot for display. The actor is WHO did
      the write; this is the automation that *initiated* it ("via <automation>").
    - ``automation_id`` — the stable scheduled-task id behind ``automation`` (the durable,
      linkable reference; the title can be renamed/deleted). Capture both: id to link, title
      to show. Mirrors how an Automation is captured as ``automation_id`` + ``automation``.
    - ``automation_run_id`` — the concrete Automation run that owns this mutation. Event
      triggers use it with ``automation_id`` to prevent a run from feeding its own writes
      back into itself while retaining provenance for other Automation chains.
    """

    actor: str
    surface: str
    session_id: Optional[str] = None
    sender_id: Optional[str] = None
    intent: Optional[str] = None
    note: Optional[str] = None
    automation: Optional[str] = None
    automation_id: Optional[str] = None
    automation_run_id: Optional[str] = None


INTENT_MAX_LEN = 280

_scope: ContextVar[Optional[AuditScope]] = ContextVar("audit_scope", default=None)


def _truncate_intent(text: Optional[str]) -> Optional[str]:
    """Clamp a user-message snippet to ``INTENT_MAX_LEN`` chars (with an ellipsis)."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) <= INTENT_MAX_LEN:
        return text
    return text[: INTENT_MAX_LEN - 1].rstrip() + "…"


def set_audit_scope(
    actor: str,
    surface: str,
    *,
    session_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    intent: Optional[str] = None,
    note: Optional[str] = None,
    automation: Optional[str] = None,
    automation_id: Optional[str] = None,
    automation_run_id: Optional[str] = None,
) -> "Token[Optional[AuditScope]]":
    """Bind the audit scope for the current run. Call before the write; clear in a
    ``finally`` via ``clear_audit_scope(token)``, passing the ``Token`` this call returns,
    so a NESTED set-then-clear (a run dispatched mid-turn that recurses into a fresh
    ``assistant.run``) restores the caller's OUTER scope instead of nulling it out from
    under the still-running outer turn (CONC-H5) — ``_scope.set(None)`` unconditionally
    would do exactly that. ``intent`` is truncated defensively so callers needn't
    pre-clamp."""
    return _scope.set(
        AuditScope(
            actor=actor,
            surface=surface,
            session_id=session_id,
            sender_id=sender_id,
            intent=_truncate_intent(intent),
            note=note,
            automation=automation,
            automation_id=automation_id,
            automation_run_id=automation_run_id,
        )
    )


def set_audit_scope_if_absent(
    actor: str,
    surface: str,
    *,
    session_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    intent: Optional[str] = None,
    note: Optional[str] = None,
    automation: Optional[str] = None,
    automation_id: Optional[str] = None,
    automation_run_id: Optional[str] = None,
) -> bool:
    """Set the scope ONLY when none is already active. For "ambient" setters (an import or an
    MCP write) that must not clobber a richer scope a higher entrypoint already set — e.g. a
    statement imported from within a chat turn keeps the chat session + intent. Returns True if
    it set the scope (caller owns the clear), False if a scope was already active (caller must
    NOT clear — it belongs to the outer entrypoint)."""
    if _scope.get() is not None:
        return False
    set_audit_scope(
        actor,
        surface,
        session_id=session_id,
        sender_id=sender_id,
        intent=intent,
        note=note,
        automation=automation,
        automation_id=automation_id,
        automation_run_id=automation_run_id,
    )
    return True


def get_audit_scope() -> Optional[AuditScope]:
    """The active scope, or ``None`` when no entrypoint set one (test/script/uninstrumented
    path). The ledger maps ``None`` to a minimal fallback event — it never blocks a write."""
    return _scope.get()


def clear_audit_scope(token: "Optional[Token[Optional[AuditScope]]]" = None) -> None:
    """Reset the scope (entrypoint ``finally`` / turn end).

    Pass the ``Token`` returned by the paired ``set_audit_scope`` call to RESTORE the
    exact prior value (correct under nesting — CONC-H5); omitting it resets to ``None``,
    preserved as the default for callers that never nest (a top-level entrypoint) or that
    used ``set_audit_scope_if_absent`` (which only ever sets a scope when none was
    active, so there is no outer value to restore).

    Belt-and-suspenders (finding 1): ``Token.reset()`` requires the CURRENT
    ``contextvars.Context`` to be the exact one the token was created in — it raises
    ``ValueError`` if called from a different Context, which happens when an abandoned
    generator (a client disconnect mid-turn) is force-closed later by asyncio's
    finalizer in a freshly forked Context. The caller's own generator-lifecycle fix
    (``assistant.run`` awaiting ``gen.aclose()`` directly) is the primary fix — this is
    the backstop so a reset from ANY foreign Context still falls back to ``set(None)``
    instead of raising and skipping the caller's own turn-end cleanup (e.g.
    ``_finalise_checkpoint()``, which runs right after this in ``_run_turn``'s
    ``finally``)."""
    if token is not None:
        try:
            _scope.reset(token)
        except ValueError:
            _scope.set(None)
    else:
        _scope.set(None)


# The Rules behind the write in progress — set by "Apply to past matches" / "Run rules" around
# each edit, so the audit event names the Rules (and the fields each changed), not only the
# person who pressed the button. A create carries its Rules explicitly instead.
_rules_applied: ContextVar[Optional[list[dict]]] = ContextVar("audit_rules_applied", default=None)


@contextmanager
def rules_applied(rules: list[dict]) -> Iterator[None]:
    """Attribute every audit event recorded inside this block to ``rules``
    (``[{automation_id, automation_name, fields}]``)."""
    token = _rules_applied.set(list(rules) or None)
    try:
        yield
    finally:
        _rules_applied.reset(token)


def get_rules_applied() -> Optional[list[dict]]:
    return _rules_applied.get()
