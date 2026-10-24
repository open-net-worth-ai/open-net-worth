"""PriceHistoryStore on ``price_points`` (plan 2, task 3) — behavioral parity with the
retired JSON-keyed-dict implementation, plus the import/export cutover contract.

Fixtures below are HAND-COMPUTED against ``backend/db/price_history_store.py``'s exact
pre-cutover semantics (read line by line before writing these): ``as_of_maps``'s per-day
scan (breaks at the first point past the target day; does not assume ``days`` is sorted),
``prior_to``'s strict-before anchor, and ``record_backfill_series``'s skip-existing +
newest-N cap (added-count is computed BEFORE the cap trims the stored series).
"""

from __future__ import annotations

import json

import pytest


def _series(store, pid: str) -> list[dict]:
    return store.series(pid)


# ── record / record_many — upsert, one point per (pid, day) ───────────────────


def test_record_many_upserts_one_point_per_day(db_isolated):
    """Last write wins on (pid, date) — the composite PK's invariant."""
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record_many({"AAPL": 100.0, "MSFT": 200.0}, day="2026-06-01", source="yahoo")
    store.record_many({"AAPL": 105.0}, day="2026-06-01", source="live")  # same day, replaces
    assert _series(store, "AAPL") == [{"date": "2026-06-01", "price": 105.0, "source": "live"}]
    assert _series(store, "MSFT") == [{"date": "2026-06-01", "price": 200.0, "source": "yahoo"}]

    store.record_many({"AAPL": 110.0}, day="2026-06-02", source="live")
    assert _series(store, "AAPL") == [
        {"date": "2026-06-01", "price": 105.0, "source": "live"},
        {"date": "2026-06-02", "price": 110.0, "source": "live"},
    ]


def test_record_single_point_convenience_and_no_op_guards(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record("", 100.0, day="2026-06-01")  # empty pid → no-op
    store.record("AAPL", None, day="2026-06-01")  # None price → no-op
    assert _series(store, "AAPL") == []
    store.record("AAPL", 100.0, day="2026-06-01", source="yahoo")
    assert _series(store, "AAPL") == [{"date": "2026-06-01", "price": 100.0, "source": "yahoo"}]
    # round to 8 dp
    store.record("BTC", 1 / 3, day="2026-06-01")
    assert _series(store, "BTC")[0]["price"] == round(1 / 3, 8)


# ── record_backfill_series — skip existing, dedupe within-batch, cap keeps newest ──


def test_backfill_skips_existing_dates_and_caps(db_isolated):
    """added-count reflects validated+deduped writes BEFORE the cap trims the series;
    the stored series itself is capped at _MAX_POINTS_PER_INSTRUMENT, keeping the newest."""
    from backend.db.price_history_store import _MAX_POINTS_PER_INSTRUMENT, PriceHistoryStore

    store = PriceHistoryStore()
    assert store.record_backfill_series("AAPL", []) == 0  # no-op: empty points
    assert store.record_backfill_series("", [{"date": "2022-01-01", "price": 1.0}]) == 0  # no pid

    store.record("AAPL", 99.0, day="2022-01-03", source="live")
    added = store.record_backfill_series(
        "AAPL",
        [
            {"date": "2022-01-03", "price": 999.0},  # existing date → skip (live point preserved)
            {"date": "2022-01-04", "price": 151.0},  # new
            {"date": "2022-01-04", "price": 777.0},  # duplicate date WITHIN this batch → skipped
            {"date": "2022-01-05", "price": 152.0},  # new
        ],
    )
    assert added == 2
    series = _series(store, "AAPL")
    assert len(series) == 3
    by_date = {p["date"]: p for p in series}
    assert by_date["2022-01-03"] == {"date": "2022-01-03", "price": 99.0, "source": "live"}
    assert by_date["2022-01-04"]["source"] == "backfill"
    assert by_date["2022-01-04"]["price"] == 151.0

    # Cap: newest N retained, oldest evicted — added-count still counts every write attempted.
    from datetime import date, timedelta

    start = date(2015, 1, 1)
    n = _MAX_POINTS_PER_INSTRUMENT + 50
    points = [
        {"date": (start + timedelta(days=i)).isoformat(), "price": 100.0 + i} for i in range(n)
    ]
    added2 = store.record_backfill_series("SPY", points)
    assert added2 == n
    result = _series(store, "SPY")
    assert len(result) == _MAX_POINTS_PER_INSTRUMENT
    assert result[-1]["price"] == pytest.approx(100.0 + n - 1)  # newest kept
    assert result[0]["date"] == (start + timedelta(days=50)).isoformat()  # oldest 50 evicted


# ── as_of_maps — the day-scan, hand-computed on a 3-series fixture ────────────


def test_as_of_maps_single_read_semantics(db_isolated):
    """Exact parity with the JSON impl: for each pid, the day-scan takes the LAST point with
    date <= d (points are stored ascending) — computed independently per day, so an
    UNSORTED ``days`` input must still be correct (the old impl restarts its scan of each
    series from the beginning for every day; it never assumes ``days`` is sorted)."""
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record("AAPL", 100.0, day="2026-01-05")
    store.record("AAPL", 110.0, day="2026-02-10")
    store.record("AAPL", 120.0, day="2026-03-15")
    store.record("MSFT", 300.0, day="2026-02-01")
    store.record("MSFT", 320.0, day="2026-04-01")
    store.record("BTC", 60000.0, day="2026-03-01")

    # Deliberately unsorted.
    days = ["2026-04-15", "2026-01-01", "2026-02-10", "2026-03-01"]
    result = store.as_of_maps(days)

    assert set(result.keys()) == set(days)
    assert result["2026-04-15"] == {"AAPL": 120.0, "MSFT": 320.0, "BTC": 60000.0}
    assert result["2026-01-01"] == {}  # before any series has a point → omitted (no backfill)
    assert result["2026-02-10"] == {"AAPL": 110.0, "MSFT": 300.0}  # BTC's first point is later
    assert result["2026-03-01"] == {"AAPL": 110.0, "MSFT": 300.0, "BTC": 60000.0}  # BTC on-the-day

    assert store.as_of_maps([]) == {}  # no days requested → nothing to compute
    assert store.as_of_maps(["2020-01-01"]) == {"2020-01-01": {}}  # before all series → omitted


def test_as_of_maps_matches_brute_force_on_both_shapes(db_isolated):
    """The 2026-08-12 rewrite has TWO code paths (single-day grouped-max join; multi-day
    bounded read + merged walk). Both must match the definitional brute force exactly —
    including duplicate day strings, days between/before/on/after points, and unsorted
    input. This is the parity proof for the perf rewrite; if it fails, the rewrite lied."""
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    series = {
        "AAPL": [("2026-01-05", 100.0), ("2026-02-10", 110.0), ("2026-03-15", 120.0)],
        "MSFT": [("2026-02-01", 300.0), ("2026-04-01", 320.0)],
        "BTC": [("2026-03-01", 60000.0)],
        "LONE": [("2026-05-01", 7.0)],
    }
    for pid, pts in series.items():
        for day, price in pts:
            store.record(pid, price, day=day)

    def brute(days: list[str]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {d: {} for d in days}
        for d in out:
            for pid, pts in series.items():
                best = [p for pd, p in pts if pd <= d]
                if best:
                    out[d][pid] = best[-1]
        return out

    multi = ["2026-04-15", "2026-01-01", "2026-02-10", "2026-02-10", "2026-03-01", "2027-01-01"]
    assert store.as_of_maps(multi) == brute(multi)
    for single in (["2026-02-10"], ["2026-01-01"], ["2027-06-06"], ["2026-03-15", "2026-03-15"]):
        assert store.as_of_maps(single) == brute(single)


def test_as_of_maps_is_one_statement_per_call(db_isolated):
    """Efficiency regression guard: both shapes issue exactly ONE SELECT — the single-day
    shape must never regress to loading the whole table (measured 39–75k row-by-row fetches
    per request before the rewrite), and the multi-day shape must stay one bulk read."""
    from sqlalchemy import event

    from backend.db.engine import get_engine
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    for i in range(1, 10):
        store.record("AAPL", 100.0 + i, day=f"2026-01-{i:02d}")
        store.record("MSFT", 300.0 + i, day=f"2026-01-{i:02d}")

    statements: list[str] = []
    eng = get_engine()

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(eng, "before_cursor_execute", _count)
    try:
        store.as_of_maps(["2026-01-05"])
        single_day_stmts = len(statements)
        statements.clear()
        store.as_of_maps(["2026-01-03", "2026-01-07", "2026-01-05"])
        multi_day_stmts = len(statements)
    finally:
        event.remove(eng, "before_cursor_execute", _count)

    assert single_day_stmts == 1
    assert multi_day_stmts == 1


# ── prior_to / prior_to_map — strict-before, bootstrap window ─────────────────


def test_prior_to_returns_none_during_bootstrap(db_isolated):
    """Only today's point exists → no genuine prior-day anchor yet (RFC resolved-decision #4:
    callers fall back to the live previousClose during the bootstrap window)."""
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record("BTC", 70000.0, day="2026-06-02")
    assert store.prior_to("BTC", day="2026-06-02") is None
    assert store.prior_to("UNKNOWN", day="2026-06-02") is None  # no series at all

    store.record("BTC", 68000.0, day="2026-06-01")
    prior = store.prior_to("BTC", day="2026-06-02")
    assert prior == {"date": "2026-06-01", "price": 68000.0, "source": ""}

    # Strictly BEFORE — a point ON `day` itself is never the answer.
    assert store.prior_to("BTC", day="2026-06-01") is None


def test_prior_to_map_omits_series_without_a_prior_point(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record_many({"AAPL": 110.0, "MSFT": 220.0}, day="2026-06-02")
    assert store.prior_to_map(day="2026-06-02") == {}  # both bootstrap-only → omitted
    store.record("AAPL", 100.0, day="2026-06-01")
    assert store.prior_to_map(day="2026-06-02") == {"AAPL": 100.0}  # MSFT still omitted


def test_today_is_utc_anchored_not_local_clock(monkeypatch):
    """Regression (2026-08-11 PLTR incident): `_today()` must anchor to UTC, never the host
    machine's local calendar date. A server running east of US markets (e.g. Singapore,
    UTC+8) has its local midnight land in the middle of a still-open NYSE session — a
    local-date `_today()` silently split that ONE session's live ticks across two `date`
    rows, so `prior_to_map()` anchored the "previous close" to an intraday snapshot from
    earlier THAT SAME session instead of the real previous close."""
    from datetime import datetime, timezone

    import backend.db.price_history_store as phs

    # 2026-08-11 01:35 in Singapore (UTC+8) == 2026-08-10 17:35 UTC — local and UTC dates
    # disagree, which is exactly the condition that split PLTR's Monday session in two.
    fixed_utc = datetime(2026, 8, 10, 17, 35, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc if tz is not None else fixed_utc.replace(tzinfo=None)

    monkeypatch.setattr(phs, "datetime", _FixedDatetime)
    assert phs._today() == "2026-08-10"


# ── series / since, latest, latest_map ─────────────────────────────────────────


def test_series_since_filters_inclusive(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record_many({"AAPL": 1.0, "MSFT": 2.0}, day="2026-05-01")
    store.record_many({"AAPL": 1.5, "MSFT": 2.5}, day="2026-06-01")
    assert len(store.series("AAPL")) == 2
    since = store.series("AAPL", since="2026-05-15")
    assert len(since) == 1 and since[0]["date"] == "2026-06-01"
    exact = store.series("AAPL", since="2026-06-01")
    assert len(exact) == 1  # since is inclusive of the boundary date
    assert store.series("NOPE") == []


def test_latest_and_latest_map(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    assert store.latest("AAPL") is None
    assert store.latest_map() == {}
    store.record("AAPL", 100.0, day="2026-06-01", source="yahoo")
    store.record("AAPL", 105.0, day="2026-06-02", source="yahoo")
    store.record("MSFT", 300.0, day="2026-06-01", source="yahoo")
    assert store.latest("AAPL") == {"date": "2026-06-02", "price": 105.0, "source": "yahoo"}
    assert store.latest_map() == {"AAPL": 105.0, "MSFT": 300.0}


def test_latest_and_prior_maps_pick_max_date_per_instrument(db_isolated):
    """Regression guard for the index-backed GROUP BY rewrite of latest_map()/prior_to_map()
    (perf/latency-fixes): both must return the MAX(date) price PER instrument — identical to
    the old "order by (pid, date), last write wins" full-table scan — with several points per
    series so "keep only the latest" actually bites. The rewrite fetches one row per instrument
    instead of scanning the whole table; this test freezes the values it must reproduce."""
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    # Three instruments, multiple dated points each, recorded out of order.
    store.record("AAPL", 100.0, day="2026-06-01")
    store.record("AAPL", 130.0, day="2026-06-03")  # latest for AAPL
    store.record("AAPL", 120.0, day="2026-06-02")
    store.record("MSFT", 300.0, day="2026-06-02")  # latest for MSFT
    store.record("MSFT", 290.0, day="2026-06-01")
    store.record("BTC", 60000.0, day="2026-06-03")  # single point, ON the boundary below

    # latest_map: the newest price per instrument, regardless of insert order.
    assert store.latest_map() == {"AAPL": 130.0, "MSFT": 300.0, "BTC": 60000.0}

    # prior_to_map(2026-06-03): newest STRICTLY BEFORE that day. BTC's only point is on the
    # boundary → omitted; AAPL/MSFT anchor to 06-02.
    assert store.prior_to_map(day="2026-06-03") == {"AAPL": 120.0, "MSFT": 300.0}
    # Earlier boundary: only the 06-01 points qualify.
    assert store.prior_to_map(day="2026-06-02") == {"AAPL": 100.0, "MSFT": 290.0}


def test_latest_and_prior_maps_match_a_brute_force_reference(db_isolated):
    """The loose-index-scan rewrite (cost per instrument, not per point — live server
    2026-10-24) against a reference computed straight from every point: random series with
    gaps, ids that sort adjacently, and boundaries before, inside, on and after the data."""
    import random

    from backend.db.price_history_store import PriceHistoryStore

    rng = random.Random(20261024)
    store = PriceHistoryStore()
    points: dict[str, dict[str, float]] = {}
    for pid in ("A", "AA", "AAPL", "BTC", "bitcoin", "Z9", "^GSPC"):
        for _ in range(rng.randint(1, 25)):
            day = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            price = round(rng.uniform(1, 500), 4)
            store.record(pid, price, day=day)
            points.setdefault(pid, {})[day] = price

    def newest(before: str | None) -> dict[str, float]:
        out = {}
        for pid, series in points.items():
            days = [d for d in series if before is None or d < before]
            if days:
                out[pid] = series[max(days)]
        return out

    assert store.latest_map() == newest(None)
    for boundary in ("2025-12-31", "2026-01-01", "2026-03-15", "2026-07-01", "2026-12-28", "2027"):
        assert store.prior_to_map(day=boundary) == newest(boundary), boundary


# ── earliest_date / latest_date — global extrema across all series ────────────


def test_earliest_and_latest_date_across_series(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    assert store.earliest_date() is None
    assert store.latest_date() is None
    store.record("AAPL", 100.0, day="2026-04-30")
    store.record("AAPL", 120.0, day="2026-05-31")
    store.record("MSFT", 300.0, day="2026-05-15")
    assert store.earliest_date() == "2026-04-30"
    assert store.latest_date() == "2026-05-31"


# ── Validation — NaN/inf/0/negative/ceiling rejected, valid rounded to 8dp ────


def test_invalid_prices_rejected(db_isolated):
    from backend.db.price_history_store import _PRICE_CEILING, PriceHistoryStore

    store = PriceHistoryStore()
    store.record("AAPL", float("nan"), day="2026-01-01")
    store.record("AAPL", float("inf"), day="2026-01-01")
    store.record("AAPL", float("-inf"), day="2026-01-01")
    store.record("AAPL", 0.0, day="2026-01-01")
    store.record("AAPL", -5.0, day="2026-01-01")
    store.record("AAPL", _PRICE_CEILING, day="2026-01-01")  # not strictly less → rejected
    assert _series(store, "AAPL") == []
    store.record("AAPL", _PRICE_CEILING - 1, day="2026-01-01")  # just under → valid
    assert _series(store, "AAPL") == [
        {"date": "2026-01-01", "price": _PRICE_CEILING - 1, "source": ""}
    ]

    store.record_many({"MSFT": float("nan"), "GOOG": -1.0}, day="2026-01-01")
    assert _series(store, "MSFT") == []
    assert _series(store, "GOOG") == []

    added = store.record_backfill_series(
        "TSLA",
        [
            {"date": "2022-01-01", "price": float("nan")},
            {"date": "2022-01-02", "price": 0.0},
            {"date": "2022-01-03", "price": -1.0},
            {"date": "2022-01-04", "price": _PRICE_CEILING},
            {"date": "2022-01-05", "price": 150.0},
        ],
    )
    assert added == 1
    assert [p["date"] for p in _series(store, "TSLA")] == ["2022-01-05"]


# ── prune — cutoff anchored to EACH series' own newest point ──────────────────


def test_prune_drops_old_points_per_series(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.prune(keep_days=1100)  # empty store → no-op, must not raise

    # Series AAA: newest 2026-06-01, keep_days=10 → cutoff 2026-05-22.
    store.record("AAA", 1.0, day="2026-05-01")  # before cutoff → dropped
    store.record("AAA", 2.0, day="2026-05-25")  # on/after cutoff → kept
    store.record("AAA", 3.0, day="2026-06-01")
    # Series BBB: newest 2020-01-10 → its OWN cutoff (2019-12-31), independent of AAA's.
    store.record("BBB", 10.0, day="2020-01-01")
    store.record("BBB", 11.0, day="2020-01-10")

    store.prune(keep_days=10)

    assert [p["date"] for p in _series(store, "AAA")] == ["2026-05-25", "2026-06-01"]
    assert [p["date"] for p in _series(store, "BBB")] == ["2020-01-01", "2020-01-10"]


# ── Concurrency — the module-level lock replaces the old per-path file lock ───


def test_concurrent_record_backfill_series_both_persist(db_isolated):
    """Two threads writing DIFFERENT dates for the SAME pid must both survive — the
    module-level write lock (this store's analogue of the old per-path JsonStore lock)
    serializes the read-modify-write cycle so neither write is lost."""
    import threading

    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    store.record_backfill_series("AAPL", [{"date": "2022-01-01", "price": 100.0}])

    barrier = threading.Barrier(2)
    results = {}

    def _write_a():
        barrier.wait()
        results["a"] = store.record_backfill_series(
            "AAPL", [{"date": "2022-06-01", "price": 155.0}]
        )

    def _write_b():
        barrier.wait()
        results["b"] = store.record_backfill_series(
            "AAPL", [{"date": "2022-09-01", "price": 165.0}]
        )

    t1, t2 = threading.Thread(target=_write_a), threading.Thread(target=_write_b)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    dates = {p["date"] for p in _series(store, "AAPL")}
    assert {"2022-01-01", "2022-06-01", "2022-09-01"} <= dates
    assert results["a"] == 1 and results["b"] == 1


# ── price_history_io — import/export round-trip + sortedness guard ────────────


def test_round_trip_import_export_exact(db_isolated):
    """fixture dict == export after import — the migration's own verification check."""
    from backend.db.engine import get_engine
    from backend.db.price_history_io import export_price_history, import_price_history

    fixture = {
        "AAPL": [
            {"date": "2026-01-01", "price": 100.0, "source": "yahoo"},
            {"date": "2026-01-02", "price": 101.5, "source": "yahoo"},
        ],
        "BTC-USD": [{"date": "2026-01-01", "price": 45000.0, "source": "coingecko"}],
    }
    with get_engine().begin() as conn:
        n = import_price_history(conn, fixture)
    assert n == 3
    with get_engine().connect() as conn:
        assert export_price_history(conn) == fixture


def test_import_defaults_missing_source_to_empty_string(db_isolated):
    """A hand-authored fixture (no ``source`` key) round-trips through the default ``""``,
    matching the store's own default and the OLD store's lenient ``.get("source", "")`` reads."""
    from backend.db.engine import get_engine
    from backend.db.price_history_io import export_price_history, import_price_history

    raw = {"SPY": [{"date": "2026-03-05", "price": 500.0}]}
    with get_engine().begin() as conn:
        import_price_history(conn, raw)
    with get_engine().connect() as conn:
        assert export_price_history(conn) == {
            "SPY": [{"date": "2026-03-05", "price": 500.0, "source": ""}]
        }


def test_import_raises_on_unsorted_series_no_partial_write(db_isolated):
    """The importer VERIFIES sortedness and aborts — no silent normalization. The whole
    transaction rolls back: nothing partially written, including a valid sibling series."""
    from sqlalchemy import func, select

    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history
    from backend.db.tables import price_points

    bad = {
        "AAPL": [
            {"date": "2026-01-05", "price": 1.0, "source": ""},
            {"date": "2026-01-01", "price": 2.0, "source": ""},  # out of order
        ],
        "MSFT": [{"date": "2026-01-01", "price": 300.0, "source": ""}],  # otherwise valid
    }
    with pytest.raises(ValueError):
        with get_engine().begin() as conn:
            import_price_history(conn, bad)
    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(price_points)).scalar_one()
    assert count == 0  # the failed transaction rolled back entirely


# ── Full migration — absorb price_history.json, verify, rename, cutover row ───


def test_full_migration_absorbs_and_renames(tmp_path, monkeypatch):
    src = tmp_path / "price_history.json"
    fixture = {
        "AAPL": [
            {"date": "2026-01-01", "price": 100.0, "source": "yahoo"},
            {"date": "2026-02-01", "price": 105.0, "source": "yahoo"},
        ],
        "BTC-USD": [{"date": "2026-01-15", "price": 45000.0, "source": "coingecko"}],
    }
    src.write_text(json.dumps(fixture))
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.PRICE_HISTORY_SOURCE", src)
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    from backend.db.price_history_io import export_price_history

    with eng.get_engine().connect() as conn:
        assert export_price_history(conn) == fixture
    assert not src.exists()
    assert len(list(tmp_path.glob("price_history.json.migrated-*"))) == 1

    from sqlalchemy import select

    from backend.db.tables import json_cutovers

    with eng.get_engine().connect() as conn:
        row = conn.execute(
            select(json_cutovers).where(json_cutovers.c.store == "price_history")
        ).one()
    assert row.renamed == 1
    assert row.source_hash  # hash guard recorded, non-empty

    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_source_missing(tmp_path, monkeypatch):
    """Fresh install: no price_history.json to absorb — head is reached, no cutover row."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.PRICE_HISTORY_SOURCE",
        tmp_path / "does_not_exist.json",
    )
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    from backend.db.price_history_io import export_price_history

    with eng.get_engine().connect() as conn:
        assert export_price_history(conn) == {}
        from sqlalchemy import func, select

        from backend.db.tables import json_cutovers

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store == "price_history")
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


# ── _eng() must not cache a lazily-resolved engine (plan 3 task 5, INCLUSION A) ────
#
# The identical bug found and fixed in all six money stores + PayloadTableStore +
# EvidenceStore (plan 3 task 4 and its fix round 1) — PriceHistoryStore was the LAST
# remaining stale instance repo-wide, routed to this task. Unlike EvidenceStore (whose real
# callers construct fresh per-function-call), this store IS a real, live, long-lived MODULE
# singleton in production: `services/equity.py` binds `price_history = PriceHistoryStore()`
# at import time and shares it for the process's whole lifetime — the practical blast radius
# here is the SAME as the six money stores', not the smaller one EvidenceStore's own fix
# round documented.


def test_eng_default_is_never_cached_across_a_later_engine_redirect(
    db_isolated, tmp_path, monkeypatch
):
    """engine=None (the default) must be re-resolved via get_engine() on EVERY call —
    never cached after the first."""
    from backend.db import engine as eng
    from backend.db.price_history_store import PriceHistoryStore

    store = PriceHistoryStore()
    first = store._eng()
    assert first is eng.get_engine()

    _redirect_engine(tmp_path / "second.db", monkeypatch)

    second = store._eng()
    assert second is eng.get_engine()
    assert second is not first, (
        "engine=None must re-resolve per call — a cached default silently ignores a later redirect"
    )


def test_eng_injected_engine_stays_honored_across_a_later_global_redirect(
    db_isolated, tmp_path, monkeypatch
):
    """The other half of the contract: an EXPLICITLY injected engine= stays pinned even
    if the process-wide engine singleton is later redirected."""
    from backend.db import engine as eng
    from backend.db.price_history_store import PriceHistoryStore

    injected = eng.get_engine()
    store = PriceHistoryStore(engine=injected)

    _redirect_engine(tmp_path / "second.db", monkeypatch)

    assert store._eng() is injected
    assert store._eng() is not eng.get_engine()


def _redirect_engine(db_path, monkeypatch) -> None:
    """Simulates a LATER db_isolated-style redirect happening mid-test (env var +
    dispose_engine() — see tests/db/conftest.py's own db_isolated fixture)."""
    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    eng.dispose_engine()
    command.upgrade(make_config(url), "head")
