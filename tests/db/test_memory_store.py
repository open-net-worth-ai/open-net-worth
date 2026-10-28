"""MemoryStore on Core (plan 4d, task 2) — DB-CRUD round trip plus the active_facts
time_bound-expiry filter (Lucius's cross-session memory, user_id-scoped per spec:276 — see
the store's own docstring for why this table carries NO book_id).
"""

from __future__ import annotations

from backend.db.memory_store import MemoryStore


def test_insert_and_all_facts_newest_updated_first(db_engine):
    store = MemoryStore(engine=db_engine)
    store.insert({"id": "mem_a", "text": "prefers dark mode", "updated_at": "2026-01-01"})
    store.insert({"id": "mem_b", "text": "based in SGT", "updated_at": "2026-03-01"})
    facts = store.all_facts()
    assert [f["id"] for f in facts] == ["mem_b", "mem_a"]


def test_remove_by_id(db_engine):
    store = MemoryStore(engine=db_engine)
    store.insert({"id": "mem_a", "text": "x"})
    assert store.remove_by_id("mem_a") is True
    assert store.remove_by_id("mem_a") is False
    assert store.all() == []


def test_no_book_id_column(db_engine):
    from backend.db.tables import memory

    assert "book_id" not in memory.c


# ── Domain parity: active_facts excludes expired time_bound items ──────────────────────────


def test_active_facts_excludes_expired_time_bound_items(db_engine):
    store = MemoryStore(engine=db_engine)
    store.insert(
        {
            "id": "mem_durable",
            "text": "always shown",
            "tier": "durable",
            "state": "live",
            "updated_at": "2026-01-01",
        }
    )
    store.insert(
        {
            "id": "mem_expired",
            "text": "stale",
            "tier": "time_bound",
            "expires_at": "2026-01-01",
            "state": "live",
            "updated_at": "2026-01-01",
        }
    )
    store.insert(
        {
            "id": "mem_future",
            "text": "still relevant",
            "tier": "time_bound",
            "expires_at": "2099-01-01",
            "state": "live",
            "updated_at": "2026-01-01",
        }
    )
    active_ids = {f["id"] for f in store.active_facts(today="2026-06-01")}
    assert active_ids == {"mem_durable", "mem_future"}


def test_active_facts_are_live_ones_and_remembered_adds_the_unsorted(db_engine):
    store = MemoryStore(engine=db_engine)
    for fid, extra in (
        ("mem_live", {"state": "live"}),
        ("mem_unsorted", {"state": "unsorted"}),
        ("mem_legacy", {}),
        ("mem_retired", {"state": "retired"}),
        ("mem_stale", {"state": "live", "expires_at": "2026-01-01"}),
    ):
        store.insert({"id": fid, "content": fid, "updated_at": "2026-01-01", **extra})
    assert {f["id"] for f in store.active_facts(today="2026-06-01")} == {"mem_live"}
    assert {f["id"] for f in store.remembered_facts(today="2026-06-01")} == {
        "mem_live",
        "mem_unsorted",
        "mem_legacy",
    }
