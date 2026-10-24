"""The review list: everything the sync would not apply on its own. One item per entity
(ledger transaction, or month + category Assigned), keyed so filing it again replaces it rather
than piling up, and closed the moment the two sides agree again."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from backend.db.ynab_sync_store import YnabConflictStore
from backend.services.ynab_sync import recorder

CONFLICT = "conflict"  # both sides changed it and they disagree
UNSUPPORTED = "unsupported"  # one side changed it in a way the other side's API cannot take
FAILED = "failed"  # the other side refused the change; the reason is YNAB's or the ledger's own

_store = YnabConflictStore()


def key_id(key: str) -> str:
    return "c_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def open_keys() -> dict[str, dict]:
    return {c["key"]: c for c in _store.open_items()}


def file(kind: str, key: str, title: str, reason: str, onw, ynab, **extra) -> dict:
    item = {
        "id": key_id(key),
        "kind": kind,
        "key": key,
        "title": title,
        "reason": reason,
        "onw": onw,
        "ynab": ynab,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolved_at": None,
        **extra,
    }
    saved = _store.upsert(item)
    recorder.conflict(item)
    return saved


def close(key: str) -> None:
    _store.delete(key_id(key))


def get(conflict_id: str) -> dict | None:
    item = _store.find_by_id(conflict_id)
    return item if item and not item.get("resolved_at") else None


def listing() -> list[dict]:
    return sorted(_store.open_items(), key=lambda c: c["created_at"])
