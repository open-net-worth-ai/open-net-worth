"""PendingAnalysisStore — durable ledger of crash/news events awaiting LLM analysis.

Each row records one enqueued event awaiting analysis. The ~60s analysis sweep claims rows
atomically, runs the LLM synthesis, updates the Intel signal, and marks done. Failed rows are
retried up to 3 times, then permanently failed.

Per-subject coalescing (INT14b): `upsert_pending` is the enqueue choke every producer (crash
watch, the fast/slow news-spike pollers, the material-news significance screen) now routes
through — it MERGES a trigger into an existing UNCLAIMED `pending` row for the same subject
instead of always appending a new one, so a hot subject's many triggers analyze together instead
of spawning a redundant row (and a redundant LLM synthesis) per trigger. See `upsert_pending`'s
own docstring for the merge rules and `recent_material_events` for how the INT12 significance-
screen cooldown keeps counting distinct screen events correctly under coalescing.

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``pending_analysis``) via ``PayloadTableStore``.
Checkpoint-EXCLUDED (``ai/checkpoint._EXCLUDED_TABLES``) — runtime scheduler state (drain/fill
cycle), never snapshotted. ``claim``/``mark_failed`` are the CAS-gated primitives (Design Note
3, plan 4c); `upsert_pending`'s merge-by-subject scan is a direct read-modify-write inside one
transaction. The old ``fail_closed_on_corrupt=False`` opt-out has no DB-era equivalent —
dropped, not ported.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import pending_analysis

_MAX_MATERIAL_EVENTS = 20  # INT14b security fix (post-QA gate): a defensive cap on how many
# material_news events a single coalesced row can accumulate in `payload.material_events`. The
# INT12 subject-level floor (news_screen._SUBJECT_SCREEN_FLOOR=3) already bounds this in NORMAL
# operation, but a row that's never drained (a stuck sweep, a disabled analyst) shouldn't grow
# unbounded regardless — keeps only the MOST RECENT _MAX_MATERIAL_EVENTS entries.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PendingAnalysisStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(pending_analysis, id_field="id", engine=engine)

    def add(
        self,
        subject: str,
        ticker: str,
        signal_id: str,
        trigger: str,
        payload: dict,
    ) -> dict:
        """Append a new pending analysis row, UNCONDITIONALLY — no coalescing. Every real producer
        now goes through `upsert_pending` instead (INT14b); this stays as the low-level primitive
        (used directly by tests that want to seed a raw row)."""
        row: dict = {
            "id": new_id("pan"),
            "subject": subject,
            "ticker": ticker,
            "signal_id": signal_id,
            "trigger": trigger,
            "payload": payload,
            "status": "pending",
            "attempts": 0,
            "created_at": _now(),
        }
        self.insert(row)
        return row

    def upsert_pending(
        self,
        subject: str,
        ticker: str,
        signal_id: str,
        trigger: str,
        payload: dict,
    ) -> dict:
        """Enqueue a trigger for `subject`, COALESCING into an existing UNCLAIMED `pending` row for
        the same subject instead of appending a new one (INT14b — a single hot subject used to
        spawn one row per trigger, e.g. 230 news_spike + 107 material_news rows for one Uber/KSTL
        story, each re-reading the same handful of headlines and producing a near-identical
        analysis). A `claimed`/`done` row is left ALONE — coalescing only targets a still-open
        `pending` row, so once a subject's analysis is in-flight or complete, the next trigger
        opens a fresh row (the subject is "hot again", not stale news for an already-drafted
        analysis).

        On merge:
          * `created_at` stays the EARLIEST arrival — `analyst._row_age_hours`'s staleness guard
            (INT14a) measures how long the subject has been waiting for an analysis, not how
            recently it was last pinged.
          * `triggers` accumulates every distinct trigger kind that has contributed so far
            (informational — lets a caller/test see the full history at a glance).
          * `trigger`/payload go "material_news"-STICKY: once ANY merge carries a material_news
            trigger — the only kind that carries an actual headline, never a bare price/volume
            ping — the row's `trigger` becomes (and stays) "material_news", so analyst.py's
            existing `>>> NEW` marker check (``row["trigger"] == "material_news"``) and event
            framing keep working UNMODIFIED. A later material_news merge overwrites the marker
            fields (headline/published_at/first_seen/cluster_tokens/reason) with its OWN — "the
            MOST RECENT material_news trigger's headline is the marker" — while a non-material
            merge (crash/news_spike) can never clobber them, since its own payload simply carries
            no such keys (a plain ``{**old, **new}`` shallow merge leaves them untouched).
          * each material_news merge additionally APPENDS its own ``{at, cluster_tokens}`` onto
            ``payload["material_events"]`` — this is what `recent_material_events` reads so the
            INT12 subject-floor/fingerprint cooldown keeps counting each DISTINCT screen event
            correctly even though several of them may now live on the SAME coalesced row (see
            that method's docstring for why a naive row-count would under/over-count here).
        """
        now = _now()
        with self._use_conn(None) as conn:
            candidates = list(
                conn.execute(
                    select(self._table).where(
                        self._table.c.payload["subject"].as_string() == subject,
                        self._table.c.payload["status"].as_string() == "pending",
                    )
                )
            )
            if candidates:
                row = candidates[0]  # at most one pending row per subject, by construction
                merged = _merge_pending_row(row.payload, trigger, payload, ticker, signal_id, now)
                self._fire_hook(conn, [(row.id, dict(row._mapping))])
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == row.id).values(payload=merged)
                )
                return merged
            new_row = {
                "id": new_id("pan"),
                "subject": subject,
                "ticker": ticker,
                "signal_id": signal_id,
                "trigger": trigger,
                "triggers": [trigger],
                "payload": _seed_payload(trigger, payload, now),
                "status": "pending",
                "attempts": 0,
                "created_at": now,
            }
            self._fire_hook(conn, [(new_row["id"], None)])
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table).values(**self._insert_row(seq, new_row["id"], new_row))
            )
        return new_row

    # Every read below filters IN SQL (json-path predicates work on both SQLite's JSON1
    # and Postgres' JSON) instead of `self.all()` + a Python filter. `.all()` decodes the
    # payload of every row; at 4.7k rows / 4.9MB this table had accumulated, one call cost
    # seconds of pure JSON parsing — and the analyst sweep ran several per scheduler tick
    # ON THE EVENT LOOP (loop_watch: repeated hard-BLOCKED frames + a 33s stall, live log
    # 2026-08-24). A queue/log store's reads must scale with the MATCHING rows, not the
    # table's whole history.

    def _rows_where(self, *predicates) -> list[dict]:
        with self._eng().connect() as conn:
            return [r.payload for r in conn.execute(select(self._table).where(*predicates))]

    def pending(self, limit: int | None = None) -> list[dict]:
        """All rows with status=='pending', oldest first."""
        rows = self._rows_where(self._table.c.payload["status"].as_string() == "pending")
        rows.sort(key=lambda r: r.get("created_at") or "")
        return rows[:limit] if limit is not None else rows

    def stale_claims(self, cutoff_iso: str) -> list[dict]:
        """Every row with status=='claimed' whose `claimed_at` is older than `cutoff_iso` —
        a claim is a lease with no built-in expiry (owner incident 2026-08-29: a worker that
        dies or hangs mid-flight leaves the row claimed FOREVER, invisible to both `pending()`
        and `has_active()`'s own callers; 126 such rows were found live, some 70+ days old).
        The caller reclaims each via the existing `mark_failed` retry path — a stale claim
        isn't a new failure category, it's the SAME failure `mark_failed` already handles."""
        rows = self._rows_where(self._table.c.payload["status"].as_string() == "claimed")
        return [r for r in rows if (r.get("claimed_at") or "") < cutoff_iso]

    def claim(self, row_id: str) -> bool:
        """Atomically flip pending→claimed. Returns False if already claimed/gone."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, row_id)
            if existing is None or existing["payload"].get("status") != "pending":
                return False
            merged = {**existing["payload"], "status": "claimed", "claimed_at": _now()}
            self._fire_hook(conn, [(row_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == row_id).values(payload=merged)
            )
        return True

    def mark_done(self, row_id: str) -> None:
        self.update_by_id(row_id, {"status": "done", "done_at": _now()})

    def mark_failed(self, row_id: str, reason: str) -> None:
        """Increment attempts. Requeues to pending if attempts < 3, else permanently fails.

        Stamps `failed_at` on the PERMANENT-failure transition only (never on a requeue) — the
        cooldown signal `recently_failed` reads (owner report 2026-08-30): a subject whose row
        just exhausted all 3 attempts and permanently failed is otherwise immediately eligible
        again, since `has_active` only looks at pending/claimed status — `upsert_pending` opens
        a BRAND NEW row for the same subject the very next staleness check, restarting another
        3-attempt cycle with zero cooldown. Live-observed as a genuine retry storm: a
        heavily-covered instrument whose current evidence makes the provider's own structured-
        output retries unreliable got re-enqueued and re-failed continuously, back-to-back, for
        many consecutive outer cycles, burning real CLI turns/cost without ever succeeding.

        Stores a redacted short reason (type name only) — never raw exception text."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, row_id)
            if existing is None:
                return
            attempts = existing["payload"].get("attempts", 0) + 1
            status = "pending" if attempts < 3 else "failed"
            merged = {
                **existing["payload"],
                "status": status,
                "attempts": attempts,
                "last_error": reason[:80],
            }
            if status == "failed":
                merged["failed_at"] = _now()
            self._fire_hook(conn, [(row_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == row_id).values(payload=merged)
            )

    def recently_failed(self, subject: str, cutoff_iso: str) -> bool:
        """True if `subject` has a PERMANENTLY-failed row (`mark_failed`'s attempts>=3 branch)
        whose `failed_at` is at or after `cutoff_iso` — the cooldown check `_enqueue_stale_briefs`
        and `trigger_on_demand_brief_refresh` use alongside `has_active` before opening a fresh
        row for a subject, so a subject that just exhausted its 3-attempt budget gets a genuine
        breathing-room window instead of being re-enqueued on the very next tick/page-open (see
        `mark_failed`'s own docstring for the retry-storm this closes). A row with no `failed_at`
        (pre-fix data) never matches — treated as not-recently-failed, never as a false positive."""
        for r in self._rows_where(self._table.c.payload["subject"].as_string() == subject):
            if r.get("status") == "failed" and (r.get("failed_at") or "") >= cutoff_iso:
                return True
        return False

    def delete_finished_older_than(
        self,
        cutoff_iso: str,
        *,
        failed_cutoff_iso: str | None = None,
        limit: int | None = None,
    ) -> int:
        """Delete every DONE or permanently-FAILED row whose own completion timestamp is
        older than `cutoff_iso` — this table never deleted a finished row before, so it grew
        forever (a done/failed row has no further use once its audit/cooldown window has
        passed; `recently_failed`'s own cooldown check only ever looks back a few hours).
        `done_at`/`failed_at` are each status's own completion stamp (`mark_done`/
        `mark_failed`) — a `pending`/`claimed` row is never a candidate, whatever its
        `created_at`. Returns the number of rows removed.

        `failed_cutoff_iso` gives FAILED rows their own (shorter) window — default: the same
        `cutoff_iso`. `limit` deletes at most that many rows per call, oldest first, so a big
        backlog drains over several calls instead of one long write transaction (a refusal
        storm left ~16k failed rows, 2026-11-19)."""
        failed_cutoff = failed_cutoff_iso or cutoff_iso
        query = (
            select(self._table.c.id, self._table.c.seq, self._table.c.payload)
            .where(
                or_(
                    and_(
                        self._table.c.payload["status"].as_string() == "done",
                        self._table.c.payload["done_at"].as_string() < cutoff_iso,
                    ),
                    and_(
                        self._table.c.payload["status"].as_string() == "failed",
                        self._table.c.payload["failed_at"].as_string() < failed_cutoff,
                    ),
                )
            )
            .order_by(self._table.c.seq)
        )
        if limit is not None:
            query = query.limit(limit)
        with self._use_conn(None) as conn:
            stale = list(conn.execute(query))
            if not stale:
                return 0
            self._fire_hook(conn, [(r.id, dict(r._mapping)) for r in stale])
            conn.execute(sa_delete(self._table).where(self._table.c.id.in_([r.id for r in stale])))
        return len(stale)

    def count_done_since(self, iso_cutoff: str) -> int:
        """Count rows with status=='done' and done_at >= iso_cutoff."""
        with self._eng().connect() as conn:
            return int(
                conn.execute(
                    select(func.count())
                    .select_from(self._table)
                    .where(
                        self._table.c.payload["status"].as_string() == "done",
                        self._table.c.payload["done_at"].as_string() >= iso_cutoff,
                    )
                ).scalar_one()
            )

    def has_recent(self, subject: str, trigger: str, since_iso: str) -> bool:
        """True if any row with this subject+trigger is pending, OR was completed since since_iso.

        Used by the news-spike cooldown gate: prevents re-enqueueing a subject that already has an
        in-flight or recently-completed analysis for the same trigger type."""
        for r in self._rows_where(
            self._table.c.payload["subject"].as_string() == subject,
            self._table.c.payload["trigger"].as_string() == trigger,
        ):
            if r.get("status") in ("pending", "claimed"):  # claimed = in-flight (sweep processing)
                return True
            if r.get("status") == "done" and (r.get("done_at") or "") >= since_iso:
                return True
        return False

    def has_active(self, subject: str) -> bool:
        """True if `subject` currently has ANY row pending or claimed, across every trigger —
        the "is this subject already being handled" guard shared by
        `analyst._enqueue_stale_briefs`'s per-tick candidate walk and
        `analyst.trigger_on_demand_brief_refresh`'s on-demand top-up, so the two producers
        never disagree about what counts as already-in-flight for a subject. Deliberately
        broader than a pending-only check (the pre-on-demand-trigger shape of
        `_enqueue_stale_briefs`'s own guard): a `claimed` row means a synthesis call is
        ACTIVELY running for this subject right now (either the sweep's own drain or an
        earlier on-demand call still in flight) — softening/duplicating it with a second
        synthetic nudge is exactly what this guard exists to prevent. Unlike `has_recent`,
        this is NOT trigger-scoped and does NOT also count a recently-`done` row — once a
        pass finishes, the subject is fair game for a fresh nudge on its own later cadence."""
        return any(
            r.get("status") in ("pending", "claimed")
            for r in self._rows_where(self._table.c.payload["subject"].as_string() == subject)
        )

    def recent_material_events(self, subject: str, since_iso: str) -> list[dict]:
        """Every DISTINCT material_news screen event enqueued for `subject` within the window,
        flattened across every row regardless of status — used by news_screen's cluster-
        fingerprint-aware cooldown (INT12): unlike has_recent's blanket yes/no gate, the caller
        needs the actual fingerprints to (a) compare a new cluster's token fingerprint against
        each recently-screened event's own stored fingerprint — a re-syndicated copy of an
        already-screened story stays suppressed while a genuinely new development on the same
        subject still screens — and (b) enforce a subject-level floor on distinct-cluster screens
        per window (anti cluster-splitting-spam).

        Generalizes the pre-INT14b `recent_rows(subject, "material_news", since_iso)` contract:
        before per-subject coalescing, every material_news trigger got its OWN row, so "1 row = 1
        event, timestamped by created_at" was implicit. A coalesced row can now hold SEVERAL such
        events (its top-level `trigger` field is "material_news"-sticky, so it can't be used to
        filter by trigger any more — this method doesn't try to). Scanning `payload.
        material_events` instead of counting ROWS is what keeps the floor/fingerprint checks
        correct: if 3 distinct material_news triggers coalesce into ONE still-pending row, a naive
        row-count would see just 1 event and let a 4th, 5th, ... slip past the floor.

        A row that predates this field (a legacy/un-migrated row, or one seeded directly via
        `add()` — every existing test that constructs a bare material_news row this way) is treated
        as exactly one event, timestamped by the row's own `created_at` and fingerprinted by
        `payload.cluster_tokens` if present — the EXACT pre-coalescing behavior, so an un-migrated
        pending_analysis.json (INT14b ships with no data migration) keeps working unchanged."""
        since = since_iso or ""
        out: list[dict] = []
        for row in self._rows_where(self._table.c.payload["subject"].as_string() == subject):
            payload = row.get("payload") or {}
            events = payload.get("material_events")
            if events is None:
                if row.get("trigger") != "material_news":
                    continue
                events = [
                    {
                        "at": row.get("created_at") or "",
                        "cluster_tokens": payload.get("cluster_tokens") or [],
                    }
                ]
            out.extend(ev for ev in events if (ev.get("at") or "") >= since)
        return out


def _seed_payload(trigger: str, payload: dict, now: str) -> dict:
    """Payload for a BRAND NEW pending row (INT14b). Stamps `material_events` up front for a
    material_news trigger so every material_news row, coalesced or not, has the same shape going
    forward (`recent_material_events`'s legacy fallback only matters for rows minted before this
    change / seeded directly via the raw `add()`)."""
    payload = dict(payload or {})
    if trigger == "material_news":
        payload["material_events"] = [
            {"at": now, "cluster_tokens": payload.get("cluster_tokens") or []}
        ]
    return payload


def _merge_pending_row(
    existing: dict, trigger: str, payload: dict, ticker: str, signal_id: str, now: str
) -> dict:
    """Fold one more trigger into an already-pending row for the same subject (INT14b). See
    `PendingAnalysisStore.upsert_pending`'s docstring for the full merge-rules rationale."""
    merged_payload = {**(existing.get("payload") or {}), **(payload or {})}
    triggers = sorted(set(existing.get("triggers") or [existing.get("trigger")]) | {trigger})
    sticky_material = trigger == "material_news" or existing.get("trigger") == "material_news"
    if trigger == "material_news":
        events = list(merged_payload.get("material_events") or [])
        if not events and existing.get("trigger") == "material_news":
            # A legacy pending row (pre-INT14b, or seeded directly via `add()`) carries an
            # IMPLICIT single event of its own that was never stamped into `material_events` —
            # synthesize it (mirrors `recent_material_events`'s own read-side fallback) before
            # appending the new one, so a later floor check doesn't silently undercount by one.
            events = [
                {
                    "at": existing.get("created_at") or "",
                    "cluster_tokens": (existing.get("payload") or {}).get("cluster_tokens") or [],
                }
            ]
        events.append({"at": now, "cluster_tokens": payload.get("cluster_tokens") or []})
        # Defensive cap (INT14b security fix) — keep only the most recent _MAX_MATERIAL_EVENTS;
        # a never-drained row shouldn't grow this list unboundedly.
        merged_payload["material_events"] = events[-_MAX_MATERIAL_EVENTS:]
    return {
        **existing,
        "ticker": ticker or existing.get("ticker") or "",
        "signal_id": signal_id or existing.get("signal_id") or "",
        "trigger": "material_news" if sticky_material else trigger,
        "triggers": triggers,
        "payload": merged_payload,
        # created_at / id / status / attempts intentionally untouched — earliest arrival preserved.
    }
