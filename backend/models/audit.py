"""AuditEvent — one transaction-provenance record.

A leaf model beside the other domain schemas. One ``AuditEvent`` is appended to
``txn_audit.json`` at the ledger write choke point for every transaction
create / update / delete / restore, capturing who changed the row, through what
surface, the originating chat session + user message, and a sparse before/after
field diff. The store is append-only and checkpoint-EXCLUDED (a data restore must
not erase the trail of how the data got there).

``op`` and ``actor`` are validated against closed sets so a typo can't quietly
create an un-renderable surface. ``surface`` is free-form because it carries ids
(``agent:<id>``, ``recurring:<task_id>``, ``import:<format>``).

DESIGN: ``.claude/rfcs/transaction-provenance.md``.
"""

from __future__ import annotations

from typing import Optional

from .base import ONBaseModel

# Closed sets — kept here as the single source of truth for the store + service.
AUDIT_OPS = frozenset(
    {
        "create",
        "update",
        "delete",
        "restore",
        "revert",  # a chat checkpoint restore put the row back to an earlier image
        "merge",
        "unmerge",
        "attach_refs",
        "detach_ref",
    }
)
AUDIT_ACTORS = frozenset({"user", "ai", "agent", "system"})


class AuditEvent(ONBaseModel):
    id: str  # evt_<uuid>
    txn_id: str  # the transaction this event is about
    op: str  # one of AUDIT_OPS
    ts: str  # ISO-8601 UTC — when the event was recorded
    actor: str  # one of AUDIT_ACTORS — WHO
    surface: str  # THROUGH WHAT (carries ids for agent/recurring/import/migration)
    source: Optional[str] = None  # the row's `source` field at write time
    session_id: Optional[str] = None  # chat session id (deep link)
    sender_id: Optional[str] = None  # channel sender (telegram/slack/...)
    intent: Optional[str] = None  # truncated originating user message
    before: Optional[dict] = None  # changed fields' prior values (None on create)
    after: Optional[dict] = None  # changed fields' new values (None on delete)
    note: Optional[str] = None  # freeform (model name, "bulk import", "reconstructed")
