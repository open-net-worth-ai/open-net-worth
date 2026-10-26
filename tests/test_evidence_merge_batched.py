"""``EvidenceStore.merge_hits`` folds every row before its first write and writes in batches —
the end state must be exactly what the per-record update/delete/insert loop produced.

Why it changed: SQLite takes the database-wide write lock at a transaction's first write, and the
per-record loop then ran hundreds of statements while holding it. Under GIL contention one RSS
scan held the lock for seconds, and a statement commit waited ~20s behind it (2026-10-26).
"""

from __future__ import annotations

import copy
import random

import pytest
from sqlalchemy import delete, event, func, insert, select, update

from backend.db.evidence_io import derive_columns, subject_rows
from backend.db.evidence_store import EvidenceStore, _merge_row
from backend.db.tables import evidence, evidence_subjects


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def _row(rng: random.Random, rid: str) -> dict:
    stamp = f"2026-06-{rng.randrange(1, 28):02d}T00:00:00+00:00"
    return {
        "id": rid,
        "url": f"https://news.example/{rid}",
        "domain": rng.choice(["news.example", "wire.example", ""]),
        "title": f"title {rng.randrange(100)}",
        "snippet": "s",
        "published_at": "",
        "image": None,
        "provider": "p",
        "kind": "news",
        "subjects": rng.sample(["AAA", "BBB", "CCC", "topic:rates"], rng.randrange(0, 4)),
        "queries": rng.sample(["q1", "q2", "q3"], rng.randrange(0, 3)),
        "first_seen": stamp,
        "last_seen": stamp,
        "seen_count": 1,
        "expires_at": rng.choice(["2099-01-01T00:00:00+00:00", ""]),
    }


def _reference_merge(conn, records: list[dict]) -> list[dict]:
    """The pre-batching ``merge_hits`` body, verbatim."""
    ids = [r.get("id") for r in records]
    merged: list[dict] = []
    existing = {
        r.id: r.payload
        for r in conn.execute(
            select(evidence.c.id, evidence.c.payload).where(evidence.c.id.in_(ids))
        )
    }
    next_seq = conn.execute(select(func.coalesce(func.max(evidence.c.seq), -1))).scalar_one() + 1
    for rec in records:
        rid = rec.get("id")
        if rid in existing:
            row = _merge_row(existing[rid], rec)
            conn.execute(update(evidence).where(evidence.c.id == rid).values(**derive_columns(row)))
            conn.execute(delete(evidence_subjects).where(evidence_subjects.c.evidence_id == rid))
        else:
            row = rec
            conn.execute(insert(evidence).values(**derive_columns(row), seq=next_seq))
            next_seq += 1
        subs = subject_rows(row)
        if subs:
            conn.execute(insert(evidence_subjects), subs)
        existing[rid] = row
        merged.append(row)
    return merged


def _dump(conn) -> tuple:
    ev = [
        (r.id, r.seq, r.expires_at, r.domain, r.created_at, r.payload)
        for r in conn.execute(select(evidence).order_by(evidence.c.id))
    ]
    subs = sorted((r.evidence_id, r.subject) for r in conn.execute(select(evidence_subjects)))
    return ev, subs


def _reset(conn, ev, subs) -> None:
    conn.execute(delete(evidence_subjects))
    conn.execute(delete(evidence))
    for rid, seq, exp, dom, created, payload in ev:
        conn.execute(
            insert(evidence).values(
                id=rid, seq=seq, expires_at=exp, domain=dom, created_at=created, payload=payload
            )
        )
    for eid, subject in subs:
        conn.execute(insert(evidence_subjects).values(evidence_id=eid, subject=subject))


@pytest.mark.parametrize("seed", range(8))
def test_batched_merge_equals_per_record_merge(store, seed):
    rng = random.Random(seed)
    seeded = [_row(rng, f"ev_{i}") for i in range(30)]
    store.merge_hits(copy.deepcopy(seeded))
    incoming = [_row(rng, f"ev_{rng.randrange(60)}") for _ in range(40)]
    if seed % 2:
        # The contract says de-duplicated by id; the old loop still had defined behavior for a
        # repeat (merge onto the row it just wrote), and so must this.
        incoming += [_row(rng, incoming[0]["id"]), _row(rng, "ev_new_twice")] * 2
    eng = store._eng()
    with eng.begin() as conn:
        before = _dump(conn)

    with eng.begin() as conn:
        expected_rows = _reference_merge(conn, copy.deepcopy(incoming))
        expected = _dump(conn)
        _reset(conn, *before)

    got_rows = store.merge_hits(copy.deepcopy(incoming))
    with eng.begin() as conn:
        got = _dump(conn)

    assert got == expected
    assert got_rows == expected_rows


def test_one_merge_holds_the_write_lock_for_a_handful_of_statements(store):
    rng = random.Random(99)
    store.merge_hits([_row(rng, f"ev_{i}") for i in range(150)])
    writes: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    eng = store._eng()
    event.listen(eng, "before_cursor_execute", _count)
    try:
        store.merge_hits([_row(rng, f"ev_{i}") for i in range(75, 300)])  # half update, half new
    finally:
        event.remove(eng, "before_cursor_execute", _count)
    assert len(writes) <= 4  # update, subject delete, insert, subject insert — was ~900
