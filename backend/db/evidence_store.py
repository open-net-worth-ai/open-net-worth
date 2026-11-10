"""EvidenceStore — durable cache of news/web evidence behind Intel scans.

A plain flat-list JsonStore keyed by a stable URL hash. The dedup-MERGE and GC are the only
non-trivial parts: re-surfacing a URL must UNION its subjects/queries and bump last_seen rather
than replace the row (so first_seen — the "found yesterday" anchor — is preserved). The merge runs
inside one read-modify-write under the store's per-path lock, so two concurrent scans can't lose an
update (the bulk_patch precedent). Field POLICY (canonical key, TTL, which fields) lives in
services/evidence; this store only owns the atomic mechanics.

Checkpoint-EXCLUDED, instance-local: a derived cache rebuildable from its sources (see ai/checkpoint).
"""

from __future__ import annotations

import threading

from sqlalchemy import bindparam, delete, func, insert, select, update

from backend.db.evidence_io import derive_columns, subject_rows
from backend.db.tables import evidence, evidence_subjects

# The JsonStore era serialized EVERY mutating call (across ALL instances, since
# _lock_for(path) keyed a process-wide lock by file path — see json_store.py) so two
# concurrent scans (e.g. intel_agent's asyncio.gather of two gather_rss calls sharing
# one store, each hopping to a worker thread via asyncio.to_thread) could never lose an
# update or race on a computed value. merge_hits's next_seq is exactly such a computed
# value (SELECT max(seq)+1, THEN insert) — under SQLite's default deferred-transaction
# locking, two overlapping transactions can both read the same max(seq) before either
# holds the write lock, then both try to insert the same seq and one hits a UNIQUE
# violation (reproduced by test_merge_hits_concurrent_writers_no_seq_collision). A
# single process-wide lock is the exact equivalent of the old per-path lock — there is
# only one evidence table, so "per path" collapses to "one lock" — and matches its
# scope precisely: in-process thread-safety, not multi-process (the old lock never
# provided that either).
_write_lock = threading.Lock()
_IN_CHUNK = 500  # ids per DELETE … IN (…) — well under SQLite's bound-parameter limit


def _merge_row(cur: dict, rec: dict) -> dict:
    """Combine an incoming hit into an existing row: union subjects/queries, keep the EARLIEST
    first_seen, take the newer last_seen/expires_at and freshest content, increment seen_count."""
    # Enforce the "first_seen is set once and only moves earlier" invariant by comparison, not by
    # call-order (a hand-edited/migrated future-dated row must not clobber the real anchor). ISO-UTC
    # strings with the same +00:00 suffix compare chronologically.
    first_seens = [f for f in (cur.get("first_seen"), rec.get("first_seen")) if f]
    return {
        **cur,
        "title": rec.get("title") or cur.get("title", ""),
        "snippet": rec.get("snippet") or cur.get("snippet", ""),
        "published_at": rec.get("published_at") or cur.get("published_at", ""),
        "image": rec.get("image") if rec.get("image") is not None else cur.get("image"),
        "provider": rec.get("provider") or cur.get("provider", ""),
        "source": rec.get("source") or cur.get("source", ""),
        # Backfill the publisher homepage onto rows ingested before we captured it, so the
        # favicon/domain fix reaches existing Google-News rows on their next poll.
        "source_url": rec.get("source_url") or cur.get("source_url", ""),
        "via": rec.get("via") or cur.get("via", ""),
        "guid": cur.get("guid") or rec.get("guid", ""),  # keep the first stable id
        "author": rec.get("author") or cur.get("author", ""),
        "subjects": sorted(set(cur.get("subjects") or []) | set(rec.get("subjects") or [])),
        "queries": sorted(set(cur.get("queries") or []) | set(rec.get("queries") or [])),
        "first_seen": min(first_seens) if first_seens else "",
        "last_seen": max(cur.get("last_seen", ""), rec.get("last_seen", "")),
        "expires_at": max(cur.get("expires_at", ""), rec.get("expires_at", "")),
        "seen_count": int(cur.get("seen_count") or 0) + 1,
    }


class EvidenceStore:
    """DB-backed (spec 2026-08-05, cutover stage 1). Same surface as the old
    JsonStore subclass — callers unchanged. payload is verbatim source of truth;
    expires_at/domain/subjects are derived indexes recomputed on every write."""

    def __init__(self, engine=None):
        self._engine = engine

    def _eng(self):
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is NEVER cached here — resolved fresh via
        ``get_engine()`` on every call (fix round 1, plan 3 task 4 — the identical bug
        found and fixed in all six money stores and ``PayloadTableStore``). Caching the
        lazily-resolved default permanently would pin a long-lived module-singleton
        instance to whichever engine happened to be live the FIRST time it was ever
        used, silently ignoring every later ``db_isolated``-style redirect (env var +
        ``engine.dispose_engine()``) — exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    def all(self) -> list[dict]:
        with self._eng().connect() as conn:
            return [r.payload for r in conn.execute(select(evidence).order_by(evidence.c.seq))]

    def find_by_id(self, id_val: str) -> dict | None:
        with self._eng().connect() as conn:
            row = conn.execute(select(evidence.c.payload).where(evidence.c.id == id_val)).first()
        return row.payload if row else None

    def count(self) -> int:
        with self._eng().connect() as conn:
            return conn.execute(select(func.count()).select_from(evidence)).scalar_one()

    def for_subjects(
        self,
        subjects: list[str],
        *,
        fresh_only: bool = True,
        now: str = "",
        rows: list[dict] | None = None,
    ) -> list[dict]:
        """Rows tagged with ANY of `subjects`. fresh_only drops rows whose expires_at < now
        (lock-free read). `now` is an ISO-UTC string; pass "" to disable the freshness gate.

        `rows`, when given, is filtered directly instead of querying the store — kept from
        the file-era latency fix (bc08fdbf) for callers that load once and query for several
        subject sets in a row (analyst sweep). Semantics match the SQL path: ANY-subject
        match + the same freshness gate, original order preserved."""
        want = [s for s in (subjects or [])]
        if not want:
            return []
        if rows is not None:
            want_set = set(want)
            out = []
            for row in rows:
                if not want_set.intersection(row.get("subjects") or []):
                    continue
                if fresh_only and now and (row.get("expires_at") or "") < now:
                    continue
                out.append(row)
            return out
        matching = select(evidence_subjects.c.evidence_id).where(
            evidence_subjects.c.subject.in_(want)
        )
        q = select(evidence).where(evidence.c.id.in_(matching)).order_by(evidence.c.seq)
        if fresh_only and now:
            q = q.where(evidence.c.expires_at >= now)
        with self._eng().connect() as conn:
            return [r.payload for r in conn.execute(q)]

    def merge_hits(self, records: list[dict]) -> list[dict]:
        """Atomic dedup-merge by id: existing id → _merge_row; new id → insert. One
        transaction. `records` must already be de-duplicated by id (the service unions
        same-scan duplicates before calling). Returns the resulting rows, input order."""
        if not records:
            return []
        ids = [r.get("id") for r in records]
        merged: list[dict] = []
        with _write_lock, self._eng().begin() as conn:
            existing = {
                r.id: r.payload
                for r in conn.execute(
                    select(evidence.c.id, evidence.c.payload).where(evidence.c.id.in_(ids))
                )
            }
            next_seq = (
                conn.execute(select(func.coalesce(func.max(evidence.c.seq), -1))).scalar_one() + 1
            )
            # Every row is folded BEFORE the first write: SQLite takes the database-wide write
            # lock at the first INSERT/UPDATE, and a per-record statement loop held it for
            # seconds under GIL contention (one RSS scan = hundreds of statements), stalling
            # every other writer in the app — a statement commit waited ~20s behind it.
            stored = set(existing)
            final: dict[str, dict] = {}
            new_seq: dict[str, int] = {}
            for rec in records:
                rid = rec.get("id")
                if rid in existing:
                    row = _merge_row(existing[rid], rec)  # module-level helper, unchanged
                else:
                    row = rec
                    new_seq[rid] = next_seq
                    next_seq += 1
                existing[rid] = row
                final[rid] = row
                merged.append(row)
            self._write_merged(conn, final, stored, new_seq)
        return merged

    @staticmethod
    def _write_merged(conn, final: dict, stored: set, new_seq: dict) -> None:
        """Write each id's FINAL row in a handful of batched statements — the same end state the
        per-record update/delete/insert sequence produced."""
        updated = [rid for rid in final if rid in stored]
        if updated:
            conn.execute(
                update(evidence).where(evidence.c.id == bindparam("_id")),
                [{**derive_columns(final[rid]), "_id": rid} for rid in updated],
            )
            for i in range(0, len(updated), _IN_CHUNK):
                conn.execute(
                    delete(evidence_subjects).where(
                        evidence_subjects.c.evidence_id.in_(updated[i : i + _IN_CHUNK])
                    )
                )
        inserted = [rid for rid in final if rid not in stored]
        if inserted:
            conn.execute(
                insert(evidence),
                [{**derive_columns(final[rid]), "seq": new_seq[rid]} for rid in inserted],
            )
        subs = [s for rid in final for s in subject_rows(final[rid])]
        if subs:
            conn.execute(insert(evidence_subjects), subs)

    def set_image(self, row_id: str, image: str) -> bool:
        """Record an article's og:image on its row — payload only, no seen_count/last_seen
        bump (this is the full-text pass, not a scan). False when the row doesn't exist."""
        with _write_lock, self._eng().begin() as conn:
            row = conn.execute(select(evidence.c.payload).where(evidence.c.id == row_id)).first()
            if row is None:
                return False
            payload = {**row.payload, "image": image}
            conn.execute(
                update(evidence).where(evidence.c.id == row_id).values(**derive_columns(payload))
            )
        return True

    def prune(self, now: str, *, keep_domains: frozenset[str] = frozenset()) -> int:
        """Drop rows whose expires_at < now, except evergreen keep_domains. Returns # removed.
        Subjects rows go with their parent evidence row (FK CASCADE)."""
        with _write_lock, self._eng().begin() as conn:
            q = delete(evidence).where(evidence.c.expires_at < now)
            if keep_domains:
                q = q.where(evidence.c.domain.notin_(list(keep_domains)))
            return conn.execute(q).rowcount
