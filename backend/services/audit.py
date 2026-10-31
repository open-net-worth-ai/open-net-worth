"""Transaction provenance / audit trail — the logic layer.

Records a structured audit event at the ledger write choke point for every transaction
mutation, and reads them back (with a best-effort forensic fallback for rows that predate
the feature). All the policy lives here; ``db/transaction_audit_store.py`` is dumb storage
and ``core/audit_context.py`` is the context seam the entrypoints set.

Layering: imports only ``db`` / ``core`` / ``models``. It must NOT import ``services.ledger``
(``ledger`` imports THIS — one direction only, no cycle).

DESIGN: ``.claude/rfcs/transaction-provenance.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.core.audit_context import get_audit_scope
from backend.core.ids import new_id
from backend.core.write_receipt import record_write
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.db.transaction_store import TransactionStore
from backend.models.audit import AUDIT_ACTORS, AUDIT_OPS

logger = logging.getLogger(__name__)

_audit = TransactionAuditStore()
_txns = TransactionStore()

# Fields excluded from a before/after diff — volatile/derived noise, not user intent.
_DIFF_IGNORE = frozenset({"created_at", "updated_at"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Recording (called from the ledger choke point) ──────────────────────────────


def changed_fields(
    before: Optional[dict], after: Optional[dict]
) -> tuple[Optional[dict], Optional[dict]]:
    """Sparse field diff: returns ``(before_changed, after_changed)`` containing only the
    keys whose value differs (ignoring volatile keys). Either side may be ``None`` when
    one of the inputs is ``None`` (create → before None; delete → after None)."""
    if before is None or after is None:
        return None, None
    b_diff: dict = {}
    a_diff: dict = {}
    keys = (set(before) | set(after)) - _DIFF_IGNORE
    for k in keys:
        bv = before.get(k)
        av = after.get(k)
        # None <-> "" is not a meaningful change — both render as "—" in the UI, and a
        # legacy pre-split row's memo silently normalizes None -> "" on TransactionStore's
        # first append/rebuild (make_transaction), which otherwise logs a spurious "memo
        # None -> ''" diff on that row's first edit for a field the user never touched.
        if (bv is None and av == "") or (av is None and bv == ""):
            continue
        if bv != av:
            b_diff[k] = bv
            a_diff[k] = av
    if not a_diff:
        return None, None
    return b_diff, a_diff


def _build_event(
    scope,
    op: str,
    txn_id: str,
    *,
    source: Optional[str],
    before: Optional[dict],
    after: Optional[dict],
    note: Optional[str],
    automation_applied: Optional[dict] = None,
) -> dict:
    """Assemble one event dict from the active scope (or the system/unknown fallback)."""
    actor = (scope.actor if scope else None) or "system"
    if actor not in AUDIT_ACTORS:
        actor = "system"
    event = {
        "id": new_id("evt"),
        "txn_id": txn_id,
        "op": op,
        "ts": _now(),
        "actor": actor,
        "surface": (scope.surface if scope else None) or "unknown",
        "source": source,
        "session_id": scope.session_id if scope else None,
        "sender_id": scope.sender_id if scope else None,
        "intent": scope.intent if scope else None,
        "automation": (scope.automation if scope else None),
        "automation_id": (scope.automation_id if scope else None),
        "before": before,
        "after": after,
        "note": note or (scope.note if scope else None),
    }
    # Sparse: only creates whose stored category was decided by an Automation carry this —
    # {automation_id, automation_name (at write time), category_from,
    # category_to}. The rule runs inside the create, so without this stamp the override
    # is invisible (the row is simply "born" with the rule's category).
    if automation_applied:
        event["automation_applied"] = automation_applied
    return event


def record_txn_event(
    op: str,
    txn_id: str,
    *,
    source: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    note: Optional[str] = None,
    automation_applied: Optional[dict] = None,
    conn: object | None = None,
) -> Optional[dict]:
    """Append one audit event for a transaction mutation. Reads the active ``AuditScope``
    (set by the calling entrypoint) for actor/surface/session/intent; when no scope is
    active (test/script/uninstrumented path) records a minimal fallback (``system`` /
    ``unknown``). Best-effort: NEVER raises — a provenance failure must not break a ledger
    write. Returns the stored event (or ``None`` if it was skipped/failed).

    ``conn`` (plan 3 task 4 — the money-core choke seam): when the caller already holds an
    open ``locked_unit_of_work`` transaction spanning ``transactions``/``txn_audit`` (a
    ledger write choke), it passes that connection through so THIS append rides the exact
    same transaction as the row it documents — a crash lands neither or both, never a
    committed ledger write with no audit trail. ``None`` (every non-choke caller, and every
    caller before this task) is unchanged: ``TransactionAuditStore.append`` opens its own
    short transaction, exactly as before. An opaque passthrough only — this module never
    imports a DB driver to type it (mirrors ``core.io.fire_db_write_hook``'s own contract).

    The NEVER-RAISES swallow below stays honest on BOTH dialects (fix round 1, plan 3 task
    4): when ``conn`` is given, ``TransactionAuditStore.append`` runs its writes inside a
    SAVEPOINT (``conn.begin_nested()``, mirroring ``record_db_before``'s own Postgres fix)
    so a failing audit statement can never poison the CALLER's shared transaction — without
    it, a swallowed failure here would still abort the whole choke's transaction on
    Postgres, and the choke's own later COMMIT would raise, rolling back a ledger write
    that had already logically succeeded."""
    if op not in AUDIT_OPS:
        logger.warning("audit: unknown op %r for txn %s — skipping", op, txn_id)
        return None
    try:
        event = _build_event(
            get_audit_scope(),
            op,
            txn_id,
            source=source,
            before=before,
            after=after,
            note=note,
            automation_applied=automation_applied,
        )
        record_write(op, txn_id, automation_applied=automation_applied)
        return _audit.append(event, conn=conn)
    except Exception:  # pragma: no cover - defensive: provenance never breaks a write
        logger.exception("audit: failed to record %s event for txn %s", op, txn_id)
        return None


def record_txn_events(
    op: str,
    rows: list[dict],
    *,
    note: Optional[str] = None,
    automation_applied_by_id: Optional[dict[str, dict]] = None,
    conn: object | None = None,
) -> None:
    """Record the SAME ``op`` for many transactions in ONE store write (the bulk
    create/delete paths). ``rows`` are stored transaction dicts (need ``id`` + ``source``).
    ``automation_applied_by_id`` maps transaction id to its Automation category
    provenance payload. Best-effort: NEVER raises. ``conn`` — see ``record_txn_event``'s
    docstring; same passthrough contract, including the SAVEPOINT isolation
    (``TransactionAuditStore.append_many``) that keeps this swallow dialect-safe inside a
    shared choke transaction."""
    if op not in AUDIT_OPS or not rows:
        return
    try:
        scope = get_audit_scope()
        events = [
            _build_event(
                scope,
                op,
                r["id"],
                source=r.get("source"),
                before=None,
                after=None,
                note=note,
                automation_applied=(automation_applied_by_id or {}).get(r["id"]),
            )
            for r in rows
            if r.get("id")
        ]
        for e in events:
            record_write(op, e["txn_id"], automation_applied=e.get("automation_applied"))
        _audit.append_many(events, conn=conn)
    except Exception:  # pragma: no cover - defensive: provenance never breaks a write
        logger.exception("audit: failed to record bulk %s events (%d rows)", op, len(rows))


def record_txn_reverts(
    images: dict[str, tuple[Optional[dict], Optional[dict]]],
    *,
    note: str,
    conn: object | None = None,
) -> None:
    """One ``revert`` event per transaction a checkpoint restore changed — ``images`` maps
    a txn id to its ``(current, restored)`` payloads, either side ``None`` when the row is
    absent. ``txn_audit`` is checkpoint-excluded so the replay never rewinds the trail, which
    left the reverted edit reading as the row's last word; this is the row that says it was
    undone. Not a write-receipt entry: the restore reports itself. Best-effort: NEVER raises.
    """
    try:
        scope = get_audit_scope()
        events = []
        for txn_id, (current, restored) in images.items():
            if current is None and restored is None:
                continue
            if current is not None and restored is not None:
                if changed_fields(current, restored) == (None, None):
                    continue
            row = restored or current or {}
            events.append(
                _build_event(
                    scope,
                    "revert",
                    txn_id,
                    source=row.get("source"),
                    before=current,
                    after=restored,
                    note=note,
                )
            )
        if events:
            _audit.append_many(events, conn=conn)
    except Exception:  # pragma: no cover - defensive: provenance never breaks a restore
        logger.exception("audit: failed to record revert events (%d rows)", len(images))


# ── Reading (trace + forensic fallback) ─────────────────────────────────────────


def trace(txn_id: str) -> dict:
    """Return the provenance of a transaction: ``{txn_id, events, reconstructed}``.

    Recorded events win. If none exist (a row created before this feature shipped, or
    pre-backfill), fall back to a best-effort reconstruction from the row's own fields,
    flagged ``reconstructed=True``."""
    events = _audit.for_txn(txn_id)
    if events:
        return {"txn_id": txn_id, "events": events, "reconstructed": False}
    return {"txn_id": txn_id, "events": reconstruct(txn_id), "reconstructed": True}


def _infer_actor_surface(row: dict) -> tuple[str, str]:
    """Best-effort actor+surface from a transaction's self-describing fields, for rows
    with no recorded audit. Cheap and deterministic (no log/session scan)."""
    from backend.core.txn_sources import primary_source

    source = (row.get("source") or "").strip().lower()
    external_source = (primary_source(row) or "").strip().lower()
    if source == "recurring" or external_source == "recurring":
        return "system", "recurring"
    if external_source == "email":
        return "agent", "agent:email"
    if source == "import" or row.get("batch_id"):
        return "user", "import"
    if source == "ai":
        return "ai", "chat:web"
    # "manual" and anything else → a human acting through the app.
    return "user", "ui"


# ── Activity timeline (Accounts redesign — the drawer's provenance + activity blocks) ──

# audit op → the design's activity-verb key (data.jsx ACTION_VERB). `create` is refined by
# the row's display source (an import reads "imported", a reconcile adjustment "reconciled").
_OP_VERB = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
    "restore": "restored",
    "revert": "reverted",
    "merge": "merged",
    "unmerge": "unmerged",
    "attach_refs": "linked a source",
    "detach_ref": "unlinked a source",
}
# audit actor (closed set) → the design's actor TYPE id (drives the timeline icon + badge).
_ACTOR_ID = {"user": "you", "ai": "lucius", "agent": "agent", "system": "system"}
# actor type → its fixed display name (agents resolve to their own name instead).
_ACTOR_NAME = {"you": "You", "lucius": "Lucius", "system": "System", "automation": "Schedule"}
# chat surface channel → its human label (the "via <channel>" qualifier). Web is the default
# interactive context and shows no via (it would be noise).
_CHANNEL_LABELS = {
    "telegram": "Telegram",
    "slack": "Slack",
    "discord": "Discord",
    "whatsapp": "WhatsApp",
}

# Capture surfaces (``capture:<source>``, backend/ai/capture.py): a client that forwarded a
# bank alert for Lucius to log. The one shipped is the iOS Shortcut; an Android DIY client
# (Tasker, MacroDroid — docs/shortcuts/ios-tap-to-log.md, "Android") arrives with whatever
# source it named, so an unknown one is shown humanised rather than dropped.
_CAPTURE_LABELS = {
    "ios-shortcut": "iOS Shortcut",
    "ios-wallet": "iOS Apple Pay",
    "ios-notification": "iOS Notification",
}


def _capture_label(source: str) -> str:
    known = _CAPTURE_LABELS.get(source)
    if known:
        return known
    return source.replace("-", " ").replace("_", " ").strip().title() or "Capture"


def _event_action(op: str, disp_source: str) -> str:
    verb = _OP_VERB.get(op, op or "updated")
    if op == "create":
        if disp_source == "import":
            return "imported"
        if disp_source == "reconcile":
            return "reconciled"
    return verb


def _event_via(event: dict, actor_name: str) -> str | None:
    """A 'via …' qualifier for the timeline, ONLY when it adds information beyond the actor.
    Priority: (1) the NAMED automation that fired the run ("Weekly Fraud Checking") — the
    actor is the agent, this is the scheduled task that triggered it; (2) a non-web chat
    channel ("Telegram"); (3) a bare recurring/schedule trigger. An agent surface alone is
    NOT a via (the actor already names the agent), and web chat / direct UI have no via."""
    automation = (event.get("automation") or "").strip()
    if automation and automation != actor_name:
        return automation
    surface = (event.get("surface") or "").strip()
    if surface.startswith("chat:"):
        channel = surface.split(":", 1)[1].strip().lower()
        return _CHANNEL_LABELS.get(channel)  # web (and any unknown) → None
    if surface.startswith("recurring:"):
        return "Schedule" if actor_name != "Schedule" else None
    if surface.startswith("capture:"):
        # "Added by Lucius • via iOS Shortcut": the actor wrote it, the capture client
        # started it — the same shape as a chat channel.
        return _capture_label(surface.split(":", 1)[1].strip().lower())
    return None


def _timeline_actor(event: dict) -> tuple[str, str]:
    """``(type, display name)`` for a timeline event's actor. A named agent resolves to its
    own display name (e.g. "Transaction & Trade Logger"); Lucius / You / System / Schedule
    use their fixed labels. The type drives the drawer's icon + badge; the name is shown
    inline. Mirrors the "Added by" resolution so the create row reads identically."""
    surface = (event.get("surface") or "").strip()
    actor = (event.get("actor") or "").strip().lower()
    if surface.startswith("agent:") or actor == "agent":
        agent_id = surface.split(":", 1)[1] if surface.startswith("agent:") else ""
        # Agents were retired 2026-08-02; there is no record left to resolve a live name
        # from, so a historical row falls back to the name IT recorded at write time.
        del agent_id
        return "agent", (event.get("note") or "").strip() or "Agent"
    if surface.startswith("recurring:") or actor == "automation":
        return "automation", "Schedule"
    type_id = _ACTOR_ID.get(actor, actor or "system")
    return type_id, _ACTOR_NAME.get(type_id, type_id.capitalize())


def _added_by_name(create_ev: dict | None, disp_source: str) -> str:
    """The 'Added by' name for the drawer. A scheduled run resolves to "Schedule";
    everything else (you, Lucius, bank feed, import…) uses the source label.

    A HISTORICAL row written by the retired Agent product carries an ``agent:<id>`` surface.
    There is no record left to resolve, so it falls back to the name recorded on the event
    note at write time, then to the generic label — the drawer never invents one."""
    from backend.core.txn_sources import added_by_label

    label = added_by_label(disp_source)
    surface = ((create_ev or {}).get("surface") or "").strip()
    if surface.startswith("agent:"):
        return ((create_ev or {}).get("note") or "").strip() or label
    if surface.startswith("recurring:"):
        return "Schedule"
    return label


def _import_context(row: dict) -> dict | None:
    """Resolve a row's import provenance for DISPLAY: batch → its statement record →
    the original file. Read-only, best-effort — provenance display must never break the
    drawer. Applies retroactively to events recorded before this enrichment existed."""
    batch_id = row.get("batch_id")
    if not batch_id:
        return None
    try:
        from backend.db.statement_store import StatementStore
        from backend.services import ledger as _ledger

        batch = _ledger.import_batches.find_by_id(batch_id)
        out: dict = {"batchId": batch_id}
        if batch:
            out["description"] = batch.get("description")
        stmt_id = (batch or {}).get("statement_ref_id")
        if stmt_id:
            # The batch's statement_ref_id is the DURABLE "this was a statement import"
            # signal — it outlives the statement record (the user may delete the file;
            # provenance must not silently degrade to an unlabeled batch).
            out["statementBacked"] = True
            stmt = StatementStore().find_by_id(stmt_id)
            if stmt:
                out["statementId"] = stmt_id
                out["filename"] = stmt.get("filename")
        return out
    except Exception:  # pragma: no cover — display-only
        logger.exception("audit: import-context resolution failed")
        return None


def creation_provenance(txn_ids: list[str]) -> dict[str, dict]:
    """``{txn_id: {"via": …, "automation_id": …}}`` for a batch of rows — the same "via" the
    drawer's timeline shows for the create event, resolved in one query for the review queue.
    Rows with no recorded create (audit retention, legacy) are simply absent."""
    from backend.core.txn_sources import display_source

    events = _audit.creation_events(txn_ids)
    out: dict[str, dict] = {}
    for txn_id, ev in events.items():
        via = _event_via(ev, _added_by_name(ev, display_source(ev.get("source"))))
        if via or ev.get("automation_id"):
            out[txn_id] = {"via": via, "automation_id": ev.get("automation_id")}
    return out


def activity(txn_id: str) -> dict | None:
    """Provenance + activity timeline for one transaction, shaped for the redesign drawer:
    ``{source, addedBy, createdAt, updatedAt,
       activity:[{action, actor, actorName, via?, at, note?}]}``. ``actor`` is the type id
    (icon/badge); ``actorName`` is the resolved display name (a named agent's own name).

    Built from the recorded ``txn_audit.json`` events (best-effort reconstruction for rows
    that predate the feature — ``trace`` handles that). Returns ``None`` when the transaction
    does not exist (the router maps that to 404). Read-only — never writes."""
    from backend.core.txn_sources import display_source

    row = _txns.find_by_id(txn_id)
    if row is None:
        return None
    from backend.services.ledger import opening_txn_ids

    disp_source = display_source(
        row.get("source"), kind=row.get("kind"), is_opening=row.get("id") in opening_txn_ids()
    )
    import_ctx = _import_context(row)
    events = trace(txn_id).get("events") or []

    timeline: list[dict] = []
    for e in events:
        actor_type, actor_name = _timeline_actor(e)
        op = e.get("op", "")
        # Per-edit field diff (the audit log keeps before/after) — what actually changed, for the drawer.
        before, after = e.get("before"), e.get("after")
        b_diff, a_diff = (None, None)
        if before is not None and after is not None:
            b_diff, a_diff = changed_fields(before, after)
        action = _event_action(op, disp_source)
        # Review-queue decisions read as decisions, not bookkeeping: the stored truth IS the
        # needs_review flip (update) or the delete of a still-flagged row, so the refinement is
        # display-only and applies retroactively to already-recorded events.
        if op == "update" and a_diff and "needs_review" in a_diff:
            if a_diff["needs_review"]:
                action = "flagged"
            elif (b_diff or {}).get("needs_review"):
                action = "approved"
        elif op == "delete" and row.get("needs_review"):
            action = "rejected"
        if a_diff:
            # The flag itself never renders as a field edit — the verb carries it.
            a_diff = {k: v for k, v in a_diff.items() if k != "needs_review"}
        item = {
            "action": action,
            "actor": actor_type,
            "actorName": actor_name,
            "at": e.get("ts") or "",
        }
        via = _event_via(e, actor_name)
        if via:
            item["via"] = via
            # The stable id behind a named automation — lets the client link the "via" back to
            # the scheduled task (display is the title; this is the durable reference).
            if e.get("automation_id"):
                item["automationId"] = e["automation_id"]
        note = (e.get("note") or "").strip()
        # Suppress a note that merely repeats the actor name (agent rows record the agent's
        # own name as the note — redundant once the actor shows it).
        if note and note != actor_name:
            item["note"] = note
        # A raw "batch:imp_…" note is machine residue — when the batch resolves to a
        # statement, the drawer shows "from <file>" instead (import_ctx below).
        if import_ctx and note.startswith("batch:"):
            item.pop("note", None)
            item["import"] = import_ctx
        if a_diff:
            item["changes"] = [
                {"field": k, "from": (b_diff or {}).get(k), "to": a_diff[k]} for k in a_diff
            ]
        # A transaction Automation decided this row's category inside the create — render
        # it as a note plus a category field diff, so the drawer shows
        # the override/fill that the bare "created" event otherwise hides.
        automation_applied = e.get("automation_applied")
        if automation_applied:
            automation_name = (automation_applied.get("automation_name") or "").strip()
            automation_note = (
                f"Automation '{automation_name}' set the category"
                if automation_name
                else "An Automation set the category"
            )
            # Compose, never clobber — an MCP-tool create's own note ("mcp tool:…") has no
            # `via` fallback and would otherwise be lost.
            item["note"] = (
                f"{item['note']} • {automation_note}" if item.get("note") else automation_note
            )
            item["changes"] = (item.get("changes") or []) + [
                {
                    "field": "category",
                    "from": automation_applied.get("category_from"),
                    "to": automation_applied.get("category_to"),
                }
            ]
        timeline.append(item)

    create_ev = next(
        (e for e in events if e.get("op") in ("create", "restore")),
        events[0] if events else None,
    )
    created_at = (create_ev or {}).get("ts") or row.get("created_at") or ""
    updated_at = (events[-1].get("ts") if events else "") or created_at
    added_by = _added_by_name(create_ev, disp_source)
    via = _event_via(create_ev or {}, added_by)
    # Only a statement-BACKED batch is a statement import — bulk AI fixes and legacy CSV
    # batches also stamp batch_id, and must not be relabeled.
    if import_ctx and import_ctx.get("statementBacked") and not via:
        via = "Statement import"
    return {
        "source": disp_source,
        "addedBy": added_by,
        # The automation/channel that initiated the create — drives "Added by … • via X".
        "via": via,
        "import": import_ctx,
        "automationId": (create_ev or {}).get("automation_id"),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "activity": timeline,
    }


def reconstruct(txn_id: str) -> list[dict]:
    """Synthesize a single best-effort ``create`` event from a transaction's own fields
    (``source`` / ``created_at`` / ``batch_id`` / ``external_refs[0]``). Returns ``[]``
    if the transaction does not exist. NEVER scans logs/sessions (that deeper, costly
    reconstruction is an operator-only follow-on per the RFC)."""
    row = _txns.find_by_id(txn_id)
    if row is None:
        return []
    actor, surface = _infer_actor_surface(row)
    return [
        {
            "id": f"evt_recon_{txn_id}",
            "txn_id": txn_id,
            "op": "create",
            "ts": row.get("created_at") or "",
            "actor": actor,
            "surface": surface,
            "source": row.get("source"),
            "session_id": None,
            "sender_id": None,
            "intent": None,
            "before": None,
            "after": None,
            "note": "reconstructed from row fields",
        }
    ]
