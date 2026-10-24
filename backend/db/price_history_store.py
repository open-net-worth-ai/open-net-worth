"""PriceHistoryStore — persisted daily price series per instrument price_source_id.

DB-backed (spec 2026-08-05, cutover plan 2 task 3): the first store to become a REAL
relational table (`price_points`) rather than a JSON blob, because its data genuinely is
rows — one (instrument, date, price, source) point per day. This is the instrument-price
analogue of fx_cache.json (which stays FX-only). It gives two things that don't exist
without it: (1) net-worth history, and (2) a stable prior-day close for day-over-day P&L.
Sync callers (prompts/cashflow/briefing) that can't do an async fetch read `latest()`
here instead of showing the stale CSV price.

Same public surface as the retired JSON-keyed-dict implementation — callers unchanged.
Composite PK `(price_source_id, date)` is the "one point per day, last write wins"
invariant, now DB-enforced instead of hand-rolled dedup-on-save.

Write-locking: concurrent writers exist (live poller, per-position backfill threads,
startup sweep). A single MODULE-LEVEL lock serializes every mutating method's full
read -> modify -> write cycle inside one transaction — there is only one price_points
table, so "per path" (the old JsonStore-borrowed lock) collapses to "one lock" (the
Plan-1 EvidenceStore pattern; see that module's docstring for the full argument).

NOT checkpoint-captured: `price_points` sits in ai.checkpoint._EXCLUDED_TABLES, and
this store never calls `core.io.fire_db_write_hook` — parity with today, where its
`_save` never fired the file write-hook either (append-only cache data, not per-turn
AI state; a turn rollback must not delete/resurrect recorded market prices).

See .claude/rfcs/instrument-position-data-model.md.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, and_, delete, func, insert, select

from backend.db.tables import price_points

# Maximum points kept per instrument (matches prune keep-window + a small buffer).
# Enforced on write in record_backfill_series to prevent unbounded growth from a
# misconfigured or adversarial upstream.
_MAX_POINTS_PER_INSTRUMENT = 2000

# Sanity ceiling: any price above this is almost certainly a data error (e.g. an
# un-adjusted share price in the billions). Filters corrupt upstream data before persist.
_PRICE_CEILING = 1_000_000_000.0

# One process-wide lock for the one price_points table — see the module docstring.
_write_lock = threading.Lock()


def _today() -> str:
    """The trading-day boundary. Anchored to UTC, NOT the host machine's local clock — a
    server running east of the exchange (e.g. Singapore) has its local midnight fall in the
    middle of a still-open NYSE session, which silently split one session's live ticks
    across two `date` rows and made `prior_to_map()` anchor to an intraday snapshot from
    earlier THAT SAME session instead of the real previous close. UTC midnight is always
    hours after the US market close, so it never splits a regular session."""
    return datetime.now(timezone.utc).date().isoformat()


def _is_valid_price(p) -> bool:
    """A price is valid iff it is a finite float strictly greater than zero and below the
    sanity ceiling. Non-finite values (NaN/inf) are rejected here rather than relying on
    a downstream cast's passthrough behaviour."""
    try:
        f = float(p)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0 and f < _PRICE_CEILING


def _row_to_point(row) -> dict:
    return {"date": row.date, "price": row.price, "source": row.source}


class PriceHistoryStore:
    def __init__(self, engine=None):
        self._engine = engine

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is NEVER cached here — resolved fresh via
        ``get_engine()`` on every call (the identical bug found and fixed in all six
        money stores + ``PayloadTableStore`` + ``EvidenceStore``, plan 3 task 4 and its
        fix round 1 — this was the LAST remaining stale instance repo-wide, routed to
        plan 3 task 5). This store is a real, live long-lived MODULE singleton in
        production (``services.equity.price_history = PriceHistoryStore()``, constructed
        once at import time and shared for the process's whole lifetime) — caching the
        lazily-resolved default permanently would pin it to whichever engine happened to
        be live the FIRST time it was ever used, silently ignoring every later
        ``db_isolated``-style redirect (env var + ``engine.dispose_engine()``) — exactly
        the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    # ── Reads ────────────────────────────────────────────────────────────────

    def series(self, price_source_id: str, since: str | None = None) -> list[dict]:
        q = select(price_points).where(price_points.c.price_source_id == price_source_id)
        if since:
            q = q.where(price_points.c.date >= since)
        q = q.order_by(price_points.c.date)
        with self._eng().connect() as conn:
            return [_row_to_point(r) for r in conn.execute(q)]

    def latest(self, price_source_id: str) -> dict | None:
        """Most recent point (the last-known price for sync callers)."""
        q = (
            select(price_points)
            .where(price_points.c.price_source_id == price_source_id)
            .order_by(price_points.c.date.desc())
            .limit(1)
        )
        with self._eng().connect() as conn:
            row = conn.execute(q).first()
        return _row_to_point(row) if row else None

    def latest_map(self) -> dict[str, float]:
        """{price_source_id: last_known_price} in a single read — for hot-path callers
        (hydrated(), /api/prices) that would otherwise do one read per instrument."""
        return self._latest_per_id(None)

    def _latest_per_id(self, before: str | None) -> dict[str, float]:
        """{price_source_id: price of its newest point (strictly before `before`, if given)}.

        A loose index scan, so the cost follows the number of INSTRUMENTS, not points: a
        recursive CTE walks the distinct ids off the composite PK ``(price_source_id, date)``
        one ``MIN(id) > previous`` seek at a time, and each id's newest point is one indexed
        ``ORDER BY date DESC LIMIT 1`` probe. The ``GROUP BY ... MAX(date)`` join it replaces
        scanned every point — O(history), on every hydration (live server 2026-10-24:
        >500ms loop blocks in ``hydration_context`` on a throttled vCPU). Same answer: the
        PK makes (id, date) unique, and ``price`` is NOT NULL, so a NULL subquery result
        means exactly "no point before the boundary" and is omitted, as the join omitted it."""
        pid = price_points.c.price_source_id
        ids = select(func.min(pid).label("pid")).cte("ids", recursive=True)
        ids = ids.union_all(
            select(select(func.min(pid)).where(pid > ids.c.pid).scalar_subquery()).where(
                ids.c.pid.is_not(None)
            )
        )
        newest = select(price_points.c.price).where(pid == ids.c.pid)
        if before is not None:
            newest = newest.where(price_points.c.date < before)
        newest = newest.order_by(price_points.c.date.desc()).limit(1).scalar_subquery()
        q = select(ids.c.pid, newest.label("price")).where(ids.c.pid.is_not(None))
        with self._eng().connect() as conn:
            return {r.pid: r.price for r in conn.execute(q) if r.price is not None}

    def prior_to(self, price_source_id: str, day: str | None = None) -> dict | None:
        """Most recent point strictly before `day` (default today) — the day-P&L anchor.
        Returns None until the series has a genuine prior-day point, so callers fall back
        to the live previousClose during the bootstrap window (RFC resolved-decision #4)."""
        day = day or _today()
        q = (
            select(price_points)
            .where(price_points.c.price_source_id == price_source_id, price_points.c.date < day)
            .order_by(price_points.c.date.desc())
            .limit(1)
        )
        with self._eng().connect() as conn:
            row = conn.execute(q).first()
        return _row_to_point(row) if row else None

    def _prior_to_map_for_day(self, day: str) -> dict[str, float]:
        return self._latest_per_id(day)

    def prior_to_map(
        self, day: str | None = None, days: dict[str, str] | None = None
    ) -> dict[str, float]:
        """{price_source_id: most-recent close strictly before its trading day} in a bounded
        number of reads — the day-P&L anchor for hot-path callers (hydrated(), /api/prices)
        that would otherwise call prior_to() once per instrument. Series with no prior point
        are omitted, so callers fall back to the provider previousClose (priority #1) or skip
        day-P&L during the bootstrap window.

        `days` (optional) overrides the boundary PER price_source_id — e.g. each instrument's
        OWN exchange trading-day date, computed by the caller from its market timezone —
        instead of one global `day`/`_today()` for every instrument. A single boundary is
        wrong whenever an exchange's session straddles it (the bug this exists to avoid: a
        server's local midnight, or even UTC midnight for an exchange like ASX during
        daylight saving, can land in the middle of a still-open session and split it across
        two `date` rows). Ids sharing a boundary (the common case — same exchange) still
        resolve in ONE grouped read, not one per instrument. An id in `days` ALWAYS uses its
        own boundary, even if that means being omitted (never falls back to the global day's
        possibly-wrong answer)."""
        default_day = day or _today()
        out = self._prior_to_map_for_day(default_day)
        if days:
            pids_by_day: dict[str, set[str]] = {}
            for pid, d in days.items():
                pids_by_day.setdefault(d, set()).add(pid)
                out.pop(pid, None)
            for d, pids in pids_by_day.items():
                resolved = self._prior_to_map_for_day(d)
                for pid in pids:
                    if pid in resolved:
                        out[pid] = resolved[pid]
        return out

    def as_of_maps(self, days: list[str]) -> dict[str, dict[str, float]]:
        """For each day in `days`, {price_source_id: most-recent close ON OR BEFORE that day},
        in ONE indexed read. Powers net-worth history — valuing held quantities at each
        historical date. A day with no point on/before it omits that instrument (the market
        series can't predate price collection; no backfill). `days` need not be sorted or
        unique; each day's answer is independent (the retired JSON implementation's semantics,
        exactly — see test_as_of_maps_single_read_semantics).

        Shapes (this ran hot enough to matter — 39–75k row-by-row fetches per request before,
        2026-08-12): a SINGLE day (the today-valuation call sites on every stats/analytics
        request) resolves as one grouped-max join on the (price_source_id, date) PK — ~one row
        per instrument, never the whole table. MANY days (history builders) do one bulk read
        bounded at max(days), fetched in one batch, then a single merged walk per series —
        O(points + days), not O(points × days)."""
        out: dict[str, dict[str, float]] = {d: {} for d in days}
        if not days:
            return out
        unique_days = sorted(out)
        if len(unique_days) == 1:
            day = unique_days[0]
            latest = (
                select(
                    price_points.c.price_source_id,
                    func.max(price_points.c.date).label("mx"),
                )
                .where(price_points.c.date <= day)
                .group_by(price_points.c.price_source_id)
                .subquery()
            )
            q = select(price_points.c.price_source_id, price_points.c.price).select_from(
                price_points.join(
                    latest,
                    and_(
                        price_points.c.price_source_id == latest.c.price_source_id,
                        price_points.c.date == latest.c.mx,
                    ),
                )
            )
            with self._eng().connect() as conn:
                out[day] = {r.price_source_id: r.price for r in conn.execute(q).fetchall()}
            return out
        q = (
            select(price_points.c.price_source_id, price_points.c.date, price_points.c.price)
            .where(price_points.c.date <= unique_days[-1])
            .order_by(price_points.c.price_source_id, price_points.c.date)
        )
        with self._eng().connect() as conn:
            rows = conn.execute(q).fetchall()
        by_pid: dict[str, list[tuple[str, float]]] = {}
        for pid, point_date, price in rows:
            by_pid.setdefault(pid, []).append((point_date, price))
        for pid, pts in by_pid.items():
            i = 0
            chosen = None
            for d in unique_days:
                while i < len(pts) and pts[i][0] <= d:
                    chosen = pts[i][1]
                    i += 1
                if chosen is not None:
                    out[d][pid] = chosen
        return out

    def earliest_date(self) -> str | None:
        """Oldest recorded date across all series — the start of the market value series."""
        with self._eng().connect() as conn:
            return conn.execute(select(func.min(price_points.c.date))).scalar_one()

    def latest_date(self) -> str | None:
        """Most recent recorded date across all series — the session date of the last price
        snap. Used to surface the 'as of' date for day-change labels: when this date is before
        today the last recorded session was not today (weekend / holiday) and the change shown
        in the UI reflects that session, not the current calendar day."""
        with self._eng().connect() as conn:
            return conn.execute(select(func.max(price_points.c.date))).scalar_one()

    # ── Writes ───────────────────────────────────────────────────────────────

    def record(
        self, price_source_id: str, price: float, day: str | None = None, source: str = ""
    ) -> None:
        """Upsert one day's point (last write wins for that date)."""
        if not price_source_id or price is None:
            return
        self.record_many({price_source_id: price}, day=day, source=source)

    def record_many(
        self,
        prices: dict[str, float],
        day: str | None = None,
        source: str = "",
        days: dict[str, str] | None = None,
    ) -> None:
        """Batch upsert today's points for many instruments in a single transaction.

        `days` (optional) assigns each price_source_id ITS OWN day — e.g. that instrument's
        exchange trading-day date — instead of the single `day`/`_today()` every instrument
        otherwise shares. An id absent from `days` still uses `day`/`_today()`. Grouped by
        distinct day value, so instruments sharing a boundary (the common case — same
        exchange, or no override at all) still land in one delete+insert, not one per
        instrument."""
        if not prices:
            return
        default_day = day or _today()
        days = days or {}
        valid = {
            pid: (round(float(price), 8), days.get(pid, default_day))
            for pid, price in prices.items()
            if pid and _is_valid_price(price)
        }
        if not valid:
            return
        by_day: dict[str, dict[str, float]] = {}
        for pid, (price, d) in valid.items():
            by_day.setdefault(d, {})[pid] = price
        with _write_lock, self._eng().begin() as conn:
            for d, day_prices in by_day.items():
                conn.execute(
                    delete(price_points).where(
                        price_points.c.price_source_id.in_(day_prices.keys()),
                        price_points.c.date == d,
                    )
                )
                conn.execute(
                    insert(price_points),
                    [
                        {"price_source_id": pid, "date": d, "price": price, "source": source}
                        for pid, price in day_prices.items()
                    ],
                )

    def delete_series(self, price_source_id: str) -> int:
        """Drop every recorded point for one price_source_id — a valuation re-bind that
        changes what the id means (or orphans it) calls this so the series can be
        re-established in the new terms instead of mixing denominations under one key.
        Returns the number of points deleted."""
        if not price_source_id:
            return 0
        with _write_lock, self._eng().begin() as conn:
            result = conn.execute(
                delete(price_points).where(price_points.c.price_source_id == price_source_id)
            )
            return int(result.rowcount or 0)

    def record_backfill_series(
        self, price_source_id: str, points: list[dict], source: str = "backfill"
    ) -> int:
        """Insert historical (date, price) pairs for one instrument, skipping dates that
        already have a point (preserves existing ``source="live"`` closes and any prior
        backfill) AND skipping a repeated date within `points` itself (first occurrence
        wins). Returns the count of points newly written — computed BEFORE the cap below
        trims the stored series, so it can exceed the final series length.

        ``points`` is a list of ``{"date": "YYYY-MM-DD", "price": float}`` in any order.
        The write lock is held across the full read -> modify -> write cycle (one
        transaction) so concurrent writers (live poller + startup sweep + per-position
        threads) cannot lose updates. Points with non-finite, zero, negative, or
        ceiling-exceeding prices are dropped. The stored series is capped at
        _MAX_POINTS_PER_INSTRUMENT (keeping the newest)."""
        if not price_source_id or not points:
            return 0
        with _write_lock, self._eng().begin() as conn:
            existing_dates = {
                r.date
                for r in conn.execute(
                    select(price_points.c.date).where(
                        price_points.c.price_source_id == price_source_id
                    )
                )
            }
            to_insert = []
            added = 0
            for point in points:
                d = point.get("date", "")
                p = point.get("price")
                if not d or not _is_valid_price(p) or d in existing_dates:
                    continue
                to_insert.append(
                    {
                        "price_source_id": price_source_id,
                        "date": d,
                        "price": round(float(p), 8),
                        "source": source,
                    }
                )
                existing_dates.add(d)
                added += 1
            if not added:
                return 0
            conn.execute(insert(price_points), to_insert)
            self._enforce_cap(conn, price_source_id)
        return added

    @staticmethod
    def _enforce_cap(conn, price_source_id: str) -> None:
        """Keep only the newest _MAX_POINTS_PER_INSTRUMENT rows for one pid — called
        inside the caller's own open write transaction (no separate lock/txn)."""
        total = conn.execute(
            select(func.count())
            .select_from(price_points)
            .where(price_points.c.price_source_id == price_source_id)
        ).scalar_one()
        excess = total - _MAX_POINTS_PER_INSTRUMENT
        if excess <= 0:
            return
        stale_dates = [
            r.date
            for r in conn.execute(
                select(price_points.c.date)
                .where(price_points.c.price_source_id == price_source_id)
                .order_by(price_points.c.date)
                .limit(excess)
            )
        ]
        conn.execute(
            delete(price_points).where(
                price_points.c.price_source_id == price_source_id,
                price_points.c.date.in_(stale_dates),
            )
        )

    def prune(self, keep_days: int = 1100) -> None:
        """Drop points older than keep_days from the newest point of EACH series
        (default ~3y, anchored per-series, not to a global "today"). Keeps the table
        bounded; safe to call periodically."""
        with _write_lock, self._eng().begin() as conn:
            pids = [
                r.price_source_id
                for r in conn.execute(select(price_points.c.price_source_id).distinct())
            ]
            for pid in pids:
                newest = conn.execute(
                    select(func.max(price_points.c.date)).where(
                        price_points.c.price_source_id == pid
                    )
                ).scalar_one()
                if not newest:
                    continue
                try:
                    cutoff = (
                        (datetime.fromisoformat(newest) - timedelta(days=keep_days))
                        .date()
                        .isoformat()
                    )
                except Exception:
                    continue
                conn.execute(
                    delete(price_points).where(
                        price_points.c.price_source_id == pid, price_points.c.date < cutoff
                    )
                )
