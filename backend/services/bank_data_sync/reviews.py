"""The data sync's review list: what the bank changed after booking, and what it could not book
without asking (ADR 2026-11-13).

Kinds:

* ``modified`` — the bank changed a booked row (``connector_snapshots`` reports it ONCE, so it is
  kept here until decided);
* ``vanished`` — a booked row the bank no longer lists (two complete misses);
* ``posted`` — a pending row booked earlier settled at a different amount;
* ``duplicate`` — a new bank row the duplicate guard matched to a row already in the book.

The ledger changes only when the user accepts an item. A ``duplicate`` or ``posted`` item, open or
dismissed, also holds its ref back from booking, so a re-run neither re-asks nor books it."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.untrusted import sanitize_untrusted
from backend.db.bank_sync_store import BankSyncReviewStore

KINDS = ("modified", "vanished", "posted", "duplicate")
HOLDING_KINDS = ("duplicate", "posted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value):
    """Bank text shown back on screen is untrusted; numbers and dates pass through."""
    if isinstance(value, str):
        return sanitize_untrusted(value, max_len=500)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _open_item(store: BankSyncReviewStore, provider: str, ref: str, kind: str) -> Optional[dict]:
    return next(
        (
            r
            for r in store.for_provider(provider, "open")
            if r.get("ref") == ref and r.get("kind") == kind
        ),
        None,
    )


def record(
    provider: str,
    kind: str,
    ref: str,
    *,
    run_id: Optional[str],
    txn_id: Optional[str] = None,
    account_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> dict:
    """Add an item, or refresh the open one for the same ``(provider, ref, kind)`` — a row the
    bank corrected twice is one decision, showing the newest version."""
    if kind not in KINDS:
        raise ValueError(f"unknown review kind {kind!r}")
    store = BankSyncReviewStore()
    fields = {
        "txn_id": txn_id,
        "account_id": account_id,
        "run_id": run_id,
        "detail": _clean(detail or {}),
        "updated_at": _now(),
    }
    existing = _open_item(store, provider, ref, kind)
    if existing is not None:
        return store.update_by_id(existing["id"], fields)
    item = {
        "id": "bsr_" + uuid.uuid4().hex[:16],
        "provider": provider,
        "kind": kind,
        "ref": ref,
        "status": "open",
        "created_at": _now(),
        "resolved_at": None,
        "resolution": None,
        **fields,
    }
    return store.insert(item)


def record_changes(provider: str, changes: list[dict], run_id: Optional[str]) -> int:
    """``modified``/``vanished`` from a fetch (``connector_snapshots``) — booked rows only."""
    n = 0
    for change in changes or []:
        kind, ref = change.get("kind"), str(change.get("ref") or "")
        if kind not in ("modified", "vanished") or not ref or not change.get("booked_txn_id"):
            continue
        record(
            provider,
            kind,
            ref,
            run_id=run_id,
            txn_id=change.get("booked_txn_id"),
            detail={
                k: change.get(k)
                for k in ("before", "after", "current", "user_edited", "last_seen")
                if k in change
            },
        )
        n += 1
    return n


def held_refs(provider: str) -> set[str]:
    """Refs a ``duplicate``/``posted`` item holds back from booking (open or dismissed)."""
    return {
        r["ref"]
        for r in BankSyncReviewStore().for_provider(provider)
        if r.get("kind") in HOLDING_KINDS and r.get("status") in ("open", "applying", "dismissed")
    }


STALE_CLAIM = timedelta(minutes=10)


def _release_stale_claims(store: BankSyncReviewStore, provider: Optional[str]) -> None:
    """A decision cut short (the server stopped mid-write) leaves its item ``applying``; after
    ``STALE_CLAIM`` it is open again rather than lost from the list."""
    cutoff = (datetime.now(timezone.utc) - STALE_CLAIM).isoformat(timespec="seconds")
    for r in store.for_provider(provider, "applying"):
        if (r.get("claimed_at") or "") < cutoff:
            store.update_by_id(r["id"], {"status": "open"})


def open_items(provider: Optional[str] = None) -> list[dict]:
    """Open items, newest first, each with ``current`` re-read from the ledger now — what you
    decide on is the booked row as it is, not as it was when the bank's change arrived."""
    from backend.services.ledger import booked_row_snapshot

    store = BankSyncReviewStore()
    _release_stale_claims(store, provider)
    items = sorted(
        store.for_provider(provider, "open"), key=lambda r: r.get("created_at") or "", reverse=True
    )
    for item in items:
        if item.get("txn_id"):
            fresh = booked_row_snapshot(item["txn_id"], prefer_account_id=item.get("account_id"))
            item["detail"] = {**(item.get("detail") or {}), "current": _clean(fresh)}
    return items


def open_count(provider: Optional[str] = None) -> int:
    return len(BankSyncReviewStore().for_provider(provider, "open"))
