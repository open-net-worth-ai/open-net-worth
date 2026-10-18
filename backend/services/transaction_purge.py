"""Delete a transaction PERMANENTLY — the row and every copy of its contents the app holds.

An ordinary delete is a soft delete: the row keeps its contents behind ``deleted`` so checkpoint
undo (Lucius's turns) and the re-import guard work. That is the right default and it stays. This
is the explicit other verb, for a transaction the owner wants GONE (ADR
``2026-10-17-delete-permanently-erases-a-transactions-contents``). One unit of work:

* the ordinary delete choke first (``bulk_delete_transactions`` composed on this conn), so its
  refusals hold — a Starting Balance is still refused — and a live row's re-import guard is
  written exactly as a plain delete writes it;
* refused OUTRIGHT, before anything changes, while an investment or commitment fact still
  points at the row (a lot, a vest, a dividend, a commitment's payment): a soft delete leaves
  the row for those to read, a purge would not;
* every merge tombstone folded into it goes too — the same payment from another source;
* the row and its splits/refs are hard-deleted (``TransactionStore.delete``);
* its edit history is cut to stubs (``TransactionAuditStore.redact_for_txns``) plus ONE
  ``delete`` event saying it was deleted permanently;
* its re-import guard keeps only the reference and accounts — the date, description and amount
  it stored for display go;
* every checkpoint image of the row (``write_log``) becomes "did not exist", so no Undo can
  bring it back; review-queue, dedup-decision, correction and merge-journal rows naming it are
  dropped;
* its email snapshot and its bank or broker snapshot go when no other transaction holds that
  reference; its payee goes when nothing else names it and the owner never configured it; the
  checkpoint images of every row this removes become "did not exist" too.

After commit: receipts linked only to it are deleted (their files too), receipts shared with
another transaction are unlinked — ``receipts`` owns that write and its file-after-commit
order. Never touched: files the owner uploaded (a statement still shows the line), backups
already made, Lucius conversations that mention it. On SQLite the purge runs with
``secure_delete`` on and then VACUUMs, so freed pages do not keep the bytes — a WAL checkpoint
alone does not (a page freed EARLIER, with ``secure_delete`` off, still carries them into the
file).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Table, cast, delete, select, update
from sqlalchemy import Text as SAText

from backend.core.errors import ConflictError, ValidationError
from backend.db import tables
from backend.db.engine import locked_unit_of_work
from backend.db.transaction_audit_store import TransactionAuditStore

logger = logging.getLogger(__name__)

# A row these still point at is part of an investment or commitment fact.
_BLOCKING_TABLES = (
    "lots",
    "realized_events",
    "income_events",
    "vest_events",
    "grants",
    "owned_assets",
    "option_contracts",
    "positions",
    "commitments",
)
# Rows that exist only ABOUT the purged transaction: dropped with it.
_DEPENDENT_TABLES = ("match_reviews", "match_decisions", "corrections")
# The stores that can name a payee by id (an Automation's payee condition, a recurring stream,
# a commitment, a target, an alert, a correction, a review). A payee one of them names is kept.
# An explicit list, not every table: an account force-delete erases hundreds of payees at once
# under the ledger lock.
_PAYEE_REFERRERS = (
    "automations",
    "recurring_streams",
    "commitments",
    "targets",
    "alerts",
    "envelopes",
    "corrections",
    "match_reviews",
    "match_decisions",
)
_DISPLAY_FIELDS = ("date", "description", "amount", "currency")
_PURGE_NOTE = "Deleted permanently. Its details were erased."


def _table(name: str) -> Table:
    return tables.metadata.tables[name]


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _naming(
    conn: Connection, name: str, tokens: set[str], column: str = "payload"
) -> list[tuple[dict, set[str]]]:
    """Rows of ``name`` whose JSON ``column`` holds one of ``tokens`` as a quoted value, each
    with the tokens it holds. ONE pass over the table whatever the number of tokens — an
    account force-delete erases thousands of rows under the ledger lock. Ids are opaque unique
    tokens (``txn_<hex>``), so a whole quoted value cannot match by accident."""
    table = _table(name)
    if not tokens or column not in table.c:
        return []
    found: list[tuple[dict, set[str]]] = []
    for row in conn.execute(select(table, cast(table.c[column], SAText).label("_text"))):
        hits = tokens.intersection(_QUOTED.findall(row._text or ""))
        if hits:
            found.append(({k: v for k, v in row._mapping.items() if k != "_text"}, hits))
    return found


def _blockers(conn: Connection, ids: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in _BLOCKING_TABLES:
        for _row, hits in _naming(conn, name, set(ids)):
            for txn_id in hits:
                if name not in found.setdefault(txn_id, []):
                    found[txn_id].append(name)
    return found


def _refuse_if_blocked(conn: Connection, ids: list[str]) -> None:
    blocked = _blockers(conn, ids)
    if blocked:
        detail = "; ".join(f"{t} is used by {', '.join(names)}" for t, names in blocked.items())
        raise ConflictError(
            "can't delete permanently while other records still use it — undo the trade, "
            f"vest, dividend or payment first, or use an ordinary delete ({detail})"
        )


def _with_tombstones(conn: Connection, ids: list[str]) -> list[str]:
    """``ids`` plus every merge tombstone folded into one of them, transitively."""
    t = _table("transactions")
    out, frontier = list(dict.fromkeys(ids)), list(dict.fromkeys(ids))
    while frontier:
        rows = conn.execute(select(t.c.id).where(t.c.merged_into.in_(frontier))).all()
        frontier = [r.id for r in rows if r.id not in out]
        out += frontier
    return out


def tombstones_of(conn: Connection, ids: list[str]) -> list[dict]:
    """The merge tombstones folded into ``ids`` that are not among them (another account's
    copy of the same payment), refused like a purge while a fact still names one. For the
    account force-delete, which must erase them with the rows they were folded into."""
    from backend.services.ledger import transactions

    extra = [t for t in _with_tombstones(conn, ids) if t not in set(ids)]
    _refuse_if_blocked(conn, extra)
    return [r for r in (transactions.find_by_id(t, conn=conn) for t in extra) if r is not None]


def _forget_checkpoint_images(conn: Connection, ids: list[str], gone: dict[str, set[str]]) -> int:
    """Every checkpoint image of an erased row becomes "did not exist": a restore's replay then
    deletes the pk and never re-inserts it (``ai.checkpoint._restore._replay_write_log``).
    ``gone`` maps each other table to the pks this erase removed from it (snapshots, payees,
    journal and review rows), whose images would otherwise bring their copies back."""
    wl = _table("write_log")
    id_set, changed = set(ids), 0
    for row, _hits in _naming(conn, "write_log", id_set, column="before_payload"):
        image = row["before_payload"] or {}
        mine = (row["table_name"] == "transactions" and row["pk"] in id_set) or (
            row["table_name"] in ("transaction_splits", "transaction_external_refs")
            and image.get("txn_id") in id_set
        )
        # A payload-table image is the stored ROW: ``txn_id`` lives inside its ``payload``.
        payload = {**(image.get("payload") or {})}
        if row["table_name"] == "rejected_identities" and payload.get("txn_id") in id_set:
            for key in _DISPLAY_FIELDS:
                payload.pop(key, None)
            new_image = {**image, "payload": payload}
        elif mine or row["pk"] in gone.get(row["table_name"], ()):
            new_image = None
        else:
            continue
        conn.execute(
            update(wl)
            .where(wl.c.checkpoint_id == row["checkpoint_id"], wl.c.seq == row["seq"])
            .values(before_payload=new_image)
        )
        changed += 1
    for name, pks in gone.items():
        if pks:
            changed += conn.execute(
                update(wl)
                .where(wl.c.table_name == name, wl.c.pk.in_(sorted(pks)))
                .values(before_payload=None)
            ).rowcount
    return changed


def _strip_rejection_details(conn: Connection, ids: list[str]) -> None:
    t = _table("rejected_identities")
    for row, _hits in _naming(conn, "rejected_identities", set(ids)):
        payload = row["payload"] or {}
        if payload.get("txn_id") not in set(ids):
            continue
        clean = {k: v for k, v in payload.items() if k not in _DISPLAY_FIELDS}
        conn.execute(update(t).where(t.c.id == row["id"]).values(payload=clean))


def _drop_dependents(conn: Connection, ids: list[str], gone: dict[str, set[str]]) -> int:
    dropped = 0
    for name in _DEPENDENT_TABLES:
        table = _table(name)
        doomed = [row["id"] for row, _hits in _naming(conn, name, set(ids))]
        if doomed:
            conn.execute(delete(table).where(table.c.id.in_(doomed)))
            gone.setdefault(name, set()).update(doomed)
            dropped += len(doomed)
    return dropped


def _drop_merge_journal(conn: Connection, ids: list[str], gone: dict[str, set[str]]) -> int:
    """A purged row can never be un-merged, and the journal holds its before-image to allow
    exactly that — so either side's entry goes."""
    t = _table("merge_journal")
    side = t.c.absorbed_id.in_(ids) | t.c.canonical_id.in_(ids)
    doomed = [r.absorbed_id for r in conn.execute(select(t.c.absorbed_id).where(side))]
    if doomed:
        conn.execute(delete(t).where(t.c.absorbed_id.in_(doomed)))
        gone.setdefault("merge_journal", set()).update(doomed)
    return len(doomed)


def _refs(rows: list[dict]) -> set[tuple[str, str]]:
    return {
        (str(ref.get("source")), str(ref.get("ref")))
        for row in rows
        for ref in row.get("external_refs") or []
        if isinstance(ref, dict) and ref.get("source") and ref.get("ref")
    }


def _still_held(conn: Connection, source: str, ref: str) -> bool:
    refs = _table("transaction_external_refs")
    held = select(refs.c.ref_id).where(refs.c.source == source, refs.c.ref == ref)
    return conn.execute(held).first() is not None


def _unbracketed(raw: str) -> str:
    s = (raw or "").strip()
    return s[1:-1] if len(s) >= 2 and s[0] == "<" and s[-1] == ">" else s


def _drop_email_snapshots(conn: Connection, rows: list[dict], gone: dict[str, set[str]]) -> int:
    """A snapshot of an email no remaining transaction (live or deleted) was booked from —
    keyed by the internal id, or, stored before that scheme, by the raw Message-ID
    (``email_snapshots._find_snapshot``)."""
    from backend.core.email_ids import internal_email_id

    snapshots = _table("email_snapshots")
    orphaned = {ref for source, ref in _refs(rows) if source == "email"}
    orphaned = {ref for ref in orphaned if not _still_held(conn, "email", ref)}
    if not orphaned:
        return 0
    doomed = set(orphaned) | {_unbracketed(ref) for ref in orphaned}
    for row in conn.execute(select(snapshots.c.id, snapshots.c.payload)):
        payload = row.payload or {}
        raw = payload.get("raw_message_id") or payload.get("message_id") or ""
        if raw and internal_email_id(raw) in orphaned:
            doomed.add(row.id)
    hit = [r.id for r in conn.execute(select(snapshots.c.id).where(snapshots.c.id.in_(doomed)))]
    if hit:
        conn.execute(delete(snapshots).where(snapshots.c.id.in_(hit)))
        gone.setdefault("email_snapshots", set()).update(hit)
    return len(hit)


def _drop_connector_snapshots(conn: Connection, rows: list[dict], gone: dict[str, set[str]]) -> int:
    """The bank or broker's raw record behind a booked row, keyed ``source:ref``
    (``connector_snapshot_store.snapshot_key``), when no remaining transaction holds the ref.
    A later fetch can stash it again, unbooked: that copy comes from the provider, not us."""
    from backend.db.connector_snapshot_store import snapshot_key

    snapshots = _table("connector_snapshots")
    keys = [
        snapshot_key(source, ref)
        for source, ref in _refs(rows)
        if source != "email" and not _still_held(conn, source, ref)
    ]
    if not keys:
        return 0
    hit = [r.id for r in conn.execute(select(snapshots.c.id).where(snapshots.c.id.in_(keys)))]
    if hit:
        conn.execute(delete(snapshots).where(snapshots.c.id.in_(hit)))
        gone.setdefault("connector_snapshots", set()).update(hit)
    return len(hit)


def _configured(payee: dict) -> bool:
    """A payee the owner shaped by hand. A learned default category is not that: booking one
    categorised transaction writes it."""
    from backend.services.payees import _normalize_payee

    extra_aliases = set(payee.get("aliases") or []) - {_normalize_payee(payee.get("name") or "")}
    return bool(
        payee.get("rules")
        or extra_aliases
        or payee.get("hidden")
        or payee.get("autocategorize") is False
    )


def _drop_orphan_payees(conn: Connection, payee_ids: set[str], gone: dict[str, set[str]]) -> int:
    payees, txns = _table("payees"), _table("transactions")
    candidates: set[str] = set()
    for payee_id in payee_ids:
        row = conn.execute(select(payees).where(payees.c.id == payee_id)).first()
        if row is None or _configured(row.payload or {}):
            continue
        if conn.execute(select(txns.c.id).where(txns.c.payee_id == payee_id)).first():
            continue
        candidates.add(payee_id)
    for name in _PAYEE_REFERRERS:
        for _row, hits in _naming(conn, name, candidates):
            candidates -= hits
    if candidates:
        conn.execute(delete(payees).where(payees.c.id.in_(sorted(candidates))))
        gone.setdefault("payees", set()).update(candidates)
    return len(candidates)


@contextmanager
def secure_delete(conn: Connection) -> Iterator[None]:
    """SQLite zero-fills the cells the enclosed deletes free. On BEFORE the first write — the
    soft delete and the audit rewrite free cells too — and back to what it was after, since the
    pooled connection outlives this purge."""
    if conn.dialect.name != "sqlite":
        yield
        return
    before = conn.exec_driver_sql("PRAGMA secure_delete").scalar()
    conn.exec_driver_sql("PRAGMA secure_delete = ON")
    try:
        yield
    finally:
        conn.exec_driver_sql(f"PRAGMA secure_delete = {int(before or 0)}")


def _purge_rows(conn: Connection, ids: list[str]) -> dict:
    from backend.services import ledger
    from backend.services.ledger import transactions

    rows = {txn_id: transactions.find_by_id(txn_id, conn=conn) for txn_id in ids}
    missing = [txn_id for txn_id, row in rows.items() if row is None]
    if missing:
        raise ValidationError(f"Transaction not found: {', '.join(missing)}")
    every = _with_tombstones(conn, ids)
    _refuse_if_blocked(conn, every)
    live = [txn_id for txn_id, row in rows.items() if not row.get("deleted")]
    if live:
        ledger.bulk_delete_transactions(live, conn=conn)
    doomed = [r for r in (transactions.find_by_id(t, conn=conn) for t in every) if r is not None]
    for row in doomed:
        transactions.delete(row["id"], conn=conn)
    from backend.services import audit as audit_service

    TransactionAuditStore().redact_for_txns(every, conn=conn)
    for txn_id in ids:
        audit_service.record_txn_event(
            "delete",
            txn_id,
            source=rows[txn_id].get("source"),
            before=None,
            after=None,
            note=_PURGE_NOTE,
            conn=conn,
        )
    return {"purged": every, **erase_traces(conn, every, doomed, audit_done=True)}


def erase_traces(
    conn: Connection, txn_ids: list[str], rows: list[dict], *, audit_done: bool = False
) -> dict:
    """Everything after the hard delete, for rows ALREADY removed on ``conn`` — shared with
    the account force-delete (``ledger.delete_account``), which hard-deletes its own cascade.
    The caller must hold ``TRACE_TABLES``' locks (``locked_unit_of_work``)."""
    if not audit_done:
        TransactionAuditStore().redact_for_txns(txn_ids, conn=conn)
    _strip_rejection_details(conn, txn_ids)
    gone: dict[str, set[str]] = {}
    result = {
        "dependents": _drop_dependents(conn, txn_ids, gone)
        + _drop_merge_journal(conn, txn_ids, gone),
        "email_snapshots": _drop_email_snapshots(conn, rows, gone),
        "connector_snapshots": _drop_connector_snapshots(conn, rows, gone),
        "payees": _drop_orphan_payees(
            conn, {r["payee_id"] for r in rows if r.get("payee_id")}, gone
        ),
    }
    result["checkpoint_images"] = _forget_checkpoint_images(conn, txn_ids, gone)
    return result


# The tables ``erase_traces`` writes, for a caller's ``locked_unit_of_work``.
TRACE_TABLE_NAMES = (
    "txn_audit",
    "rejected_identities",
    "write_log",
    "payees",
    "email_snapshots",
    "connector_snapshots",
    "merge_journal",
    *_DEPENDENT_TABLES,
)


def trace_tables() -> list[Table]:
    return [_table(name) for name in TRACE_TABLE_NAMES]


def _locked_tables() -> list[Table]:
    return [_table("transactions"), *trace_tables()]


def _clean_receipts(ids: list[str]) -> list[str]:
    """Receipts linked only to a purged transaction are deleted, shared ones unlinked. Returns
    the receipt ids that could not be cleaned (reported to the owner, never hidden)."""
    from backend.services.receipts import apply_receipt_change, link_store, transactions_for_receipt

    left: list[str] = []
    purged = set(ids)
    for txn_id in ids:
        for link in link_store.by_transaction(txn_id):
            receipt_id = link["receipt_id"]
            others = set(transactions_for_receipt(receipt_id)) - purged
            change = (
                {"type": "unlink", "link_id": link["id"]}
                if others
                else {"type": "delete", "receipt_id": receipt_id, "force": True}
            )
            try:
                apply_receipt_change(change)
            except Exception:  # noqa: BLE001 — reported in the result, never silent
                logger.warning("purge: receipt %s not cleaned", receipt_id, exc_info=True)
                left.append(receipt_id)
    return left


def _vacuum(conn_engine) -> None:
    """Rebuild the SQLite file from live pages only. A WAL checkpoint alone is NOT enough: a page
    the app freed BEFORE this purge ran (with ``secure_delete`` off — the automation outbox copies
    every transaction payload, then frees it after delivery) still carries the bytes into the main
    file with the checkpoint. VACUUM drops those dead pages (mirrors ``backup._sanitise_db_bytes``).
    Best effort, like the checkpoint: the rows are already gone."""
    try:
        with conn_engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("VACUUM")
    except Exception:  # noqa: BLE001
        logger.warning("purge: VACUUM failed", exc_info=True)


def _checkpoint_wal(conn_engine) -> None:
    try:
        with conn_engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001 — best effort; the rows are already gone
        logger.warning("purge: WAL checkpoint failed", exc_info=True)


def after_commit(txn_ids: list[str]) -> list[str]:
    """The post-commit half: receipts (their own choke and file order), then the file itself —
    VACUUM (drop dead pages) and the WAL checkpoint. Returns the receipt ids not cleaned."""
    left = _clean_receipts(txn_ids)
    from backend.db.engine import get_engine

    engine = get_engine()
    _vacuum(engine)
    _checkpoint_wal(engine)
    return left


def purge_transactions(ids: list[str]) -> dict:
    """Permanently delete ``ids`` (see the module docstring). Returns what was erased."""
    ids = [str(i).strip() for i in ids or [] if str(i).strip()]
    if not ids:
        raise ValidationError("At least one transaction id is required")
    with locked_unit_of_work(*_locked_tables()) as conn, secure_delete(conn):
        result = _purge_rows(conn, list(dict.fromkeys(ids)))
    result["receipts_not_cleaned"] = after_commit(result["purged"])
    logger.info(
        "purge: %d transaction(s) erased (%d checkpoint images, %d payees)",
        len(result["purged"]),
        result["checkpoint_images"],
        result["payees"],
    )
    return result
