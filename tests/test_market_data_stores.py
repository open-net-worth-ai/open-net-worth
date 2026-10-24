"""Persistent settings, quota, and last-good crypto-series cache behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from multiprocessing import get_context

import pytest
from sqlalchemy import create_engine

from backend.db.coingecko_quota_store import CoinGeckoQuotaStore
from backend.db.market_data_config_store import MarketDataConfigStore
from backend.db.market_data_observability_store import MarketDataObservabilityStore
from backend.db.market_provider_circuit_store import MarketProviderCircuitStore
from backend.db.market_series_cache_store import MarketSeriesCacheStore


def _iso(seconds: int = 0) -> str:
    return (datetime(2026, 8, 12, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _series(*, as_of: str = _iso(), points: list | None = None) -> dict:
    return {
        "provider": "yahoo",
        "as_of": as_of,
        "points": points if points is not None else [{"date": as_of, "price": 100.0}],
    }


def _child_engine_options(db_url: str) -> dict:
    if db_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def _reserve_from_independent_process(db_url: str, ready, start, results) -> None:
    try:
        ready.put(True)
        start.wait(10)
        results.put(CoinGeckoQuotaStore().reserve(4, 10_000.0))
    except Exception as error:  # pragma: no cover - assertion receives the child outcome
        results.put({"error": type(error).__name__})


def _reserve_half_open_from_independent_process(
    db_url: str, ready, start, results, now: float
) -> None:
    try:
        ready.put(True)
        start.wait(10)
        results.put(CoinGeckoQuotaStore().reserve(4, now))
    except Exception as error:  # pragma: no cover - assertion receives the child outcome
        results.put({"error": type(error).__name__})


def _acquire_provider_probe_from_independent_process(
    db_url: str, ready, start, results, now: float
) -> None:
    try:
        ready.put(True)
        start.wait(10)
        results.put(MarketProviderCircuitStore("yahoo_history").try_acquire(now))
    except Exception as error:  # pragma: no cover - assertion receives the child outcome
        results.put({"error": type(error).__name__})


def _update_config_from_independent_process(
    db_url: str, ready, start, results, changes: dict
) -> None:
    try:
        ready.put(True)
        start.wait(10)
        MarketDataConfigStore().update(changes)
        results.put({"ok": True})
    except Exception as error:  # pragma: no cover - assertion receives the child outcome
        results.put({"error": type(error).__name__})


def _put_cache_from_independent_process(
    db_url: str, ready, start, results, canonical_id: str
) -> None:
    engine = create_engine(db_url, **_child_engine_options(db_url))
    try:
        ready.put(True)
        start.wait(10)
        MarketSeriesCacheStore(engine=engine).put(canonical_id, "USD", "1D", _series())
        results.put({"ok": True})
    except Exception as error:  # pragma: no cover - assertion receives the child outcome
        results.put({"error": type(error).__name__})
    finally:
        engine.dispose()


def test_child_engine_options_are_dialect_aware():
    assert _child_engine_options("sqlite:////tmp/onw.db") == {
        "connect_args": {"check_same_thread": False}
    }
    assert _child_engine_options("postgresql+psycopg://user:pass@localhost/onw") == {}


def test_config_defaults_ignore_the_environment_and_public_never_returns_key(
    db_isolated, monkeypatch
):
    monkeypatch.setenv("COINGECKO_API_KEY", "env-secret")
    monkeypatch.setenv("COINGECKO_API_KEY_TIER", "pro")
    monkeypatch.setenv("YAHOO_PRIMARY_FOR_MAJOR_CRYPTO", "false")

    store = MarketDataConfigStore()

    assert store.runtime() == {
        "coingecko_api_key": "",
        "coingecko_api_key_tier": "demo",
        "yahoo_primary_for_major_crypto": True,
    }

    store.update({"coingecko_api_key": "demo-secret", "yahoo_primary_for_major_crypto": False})
    assert store.runtime()["coingecko_api_key"] == "demo-secret"
    assert store.public() == {
        "has_coingecko_key": True,
        "coingecko_api_key_tier": "demo",
        "yahoo_primary_for_major_crypto": False,
    }
    assert "secret" not in repr(store.public())


def test_config_lives_on_the_two_price_source_rows(db_isolated):
    from backend.db.connector_store import ConnectorStore

    store = MarketDataConfigStore()
    store.update(
        {
            "coingecko_api_key": "pro-secret",
            "coingecko_api_key_tier": "pro",
            "yahoo_primary_for_major_crypto": False,
        }
    )

    rows = ConnectorStore()
    assert rows.get("coingecko")["config"] == {"tier": "pro"}
    assert rows.secrets("coingecko") == {"api_key": "pro-secret"}
    assert rows.get("yahoo_finance")["config"] == {"primary_for_major_crypto": False}
    assert rows.secrets("yahoo_finance") == {}
    assert "pro-secret" not in repr(rows.get("coingecko"))


def test_config_absent_key_preserves_saved_secret(db_isolated):
    store = MarketDataConfigStore()
    store.update({"coingecko_api_key": "keep-me"})
    # coingecko_api_key omitted entirely — a patch touching an unrelated field must not
    # disturb the saved key.
    store.update({"yahoo_primary_for_major_crypto": False})

    assert store.runtime() == {
        "coingecko_api_key": "keep-me",
        "coingecko_api_key_tier": "demo",
        "yahoo_primary_for_major_crypto": False,
    }
    assert store.public()["has_coingecko_key"] is True


def test_config_blank_key_keeps_and_null_clears_saved_secret(db_isolated):
    """The one secret rule every connector shares: a blank string keeps the saved key, ``None``
    clears it. Since CoinGecko's Remove action refuses outright, an explicit ``None`` is the
    only way to revoke a leaked/compromised saved key."""
    store = MarketDataConfigStore()
    store.update({"coingecko_api_key": "leaked-secret"})
    assert store.public()["has_coingecko_key"] is True

    store.update({"coingecko_api_key": ""})
    store.update({"coingecko_api_key": "   "})
    assert store.runtime()["coingecko_api_key"] == "leaked-secret"

    store.update({"coingecko_api_key": None})

    assert store.runtime()["coingecko_api_key"] == ""
    assert store.public()["has_coingecko_key"] is False


def test_config_tier_defaults_to_demo_and_is_independently_settable(db_isolated):
    store = MarketDataConfigStore()
    assert store.public()["coingecko_api_key_tier"] == "demo"

    # The tier can change even with no key saved yet — a closed UI choice, not a value that
    # only makes sense once a key exists.
    store.update({"coingecko_api_key_tier": "pro"})
    assert store.public()["coingecko_api_key_tier"] == "pro"

    store.update({"coingecko_api_key": "pro-secret"})
    assert store.public() == {
        "has_coingecko_key": True,
        "coingecko_api_key_tier": "pro",
        "yahoo_primary_for_major_crypto": True,
    }


def test_config_unrecognized_tier_falls_back_to_demo(db_isolated):
    store = MarketDataConfigStore()
    store.update({"coingecko_api_key_tier": "enterprise"})
    assert store.public()["coingecko_api_key_tier"] == "demo"


def test_config_rejects_control_characters_without_echoing_secret(db_isolated):
    secret = "demo\nsecret"
    with pytest.raises(ValueError) as error:
        MarketDataConfigStore().update({"coingecko_api_key": secret})
    assert secret not in str(error.value)


def test_quota_refuses_fifth_keyless_call_atomically(db_isolated):
    quota = CoinGeckoQuotaStore()
    now = 10_000.0

    for _ in range(4):
        assert quota.reserve(4, now)["allowed"] is True
    denied = quota.reserve(4, now)
    assert denied == {"allowed": False, "retry_after": 60, "calls_last_minute": 4}


def test_quota_refuses_twenty_sixth_demo_call_atomically(db_isolated):
    quota = CoinGeckoQuotaStore()
    now = 10_000.0
    for _ in range(25):
        assert quota.reserve(25, now)["allowed"] is True
    denied = quota.reserve(25, now)
    assert denied == {"allowed": False, "retry_after": 60, "calls_last_minute": 25}


def test_quota_serializes_independent_process_reservations(db_isolated):
    quota = CoinGeckoQuotaStore()
    for _ in range(3):
        assert quota.reserve(4, 10_000.0)["allowed"] is True

    context = get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_reserve_from_independent_process,
            args=(db_isolated, ready, start, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert sorted(result["allowed"] for result in outcomes) == [False, True]
    assert all("error" not in result for result in outcomes)
    assert CoinGeckoQuotaStore().calls_last_minute(10_000.0) == 4


def test_quota_cooldown_expiry_admits_one_cross_process_half_open_probe(db_isolated):
    quota = CoinGeckoQuotaStore()
    quota.record_failure(17, 10_000.0)

    context = get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_reserve_half_open_from_independent_process,
            args=(db_isolated, ready, start, results, 10_018.0),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert sorted(result["allowed"] for result in outcomes) == [False, True]
    allowed = next(result for result in outcomes if result["allowed"])
    assert allowed["probe"] is True
    assert all("error" not in result for result in outcomes)


def test_quota_half_open_success_closes_and_failure_reopens_with_backoff(db_isolated):
    quota = CoinGeckoQuotaStore()
    quota.record_failure(10, 10_000.0)

    probe = quota.reserve(4, 10_011.0)
    assert probe["allowed"] is True
    assert probe["probe"] is True
    assert quota.reserve(4, 10_011.0)["allowed"] is False

    quota.record_failure(10, 10_012.0)
    reopened = quota.public_state(10_012.0)
    assert reopened["failure_streak"] == 2
    assert reopened["cooldown_until"] == 10_032.0
    assert quota.reserve(4, 10_031.0)["allowed"] is False

    next_probe = quota.reserve(4, 10_033.0)
    assert next_probe["allowed"] is True
    assert next_probe["probe"] is True
    quota.record_success(10_034.0)

    closed = quota.public_state(10_034.0)
    assert closed["failure_streak"] == 0
    assert closed["cooldown_until"] == 0.0
    assert closed["last_ok_at"] is not None
    assert quota.reserve(4, 10_035.0).get("probe", False) is False


def test_quota_open_circuit_refuses_without_a_write_transaction(db_isolated, monkeypatch):
    """While CoinGecko is down every price check (60s, unchanged) asked for a slot, and each
    ask took the database's write lock just to be refused (live server 2026-10-24). An open
    circuit now answers from a plain read — the same refusal — and the half-open probe after
    the cooldown still goes through the serialized path."""
    from backend.db import price_source_store

    quota = CoinGeckoQuotaStore()
    quota.record_failure(10, 10_000.0)
    writes = []
    real_update = price_source_store.update_state
    monkeypatch.setattr(
        price_source_store,
        "update_state",
        lambda cid, change: writes.append(cid) or real_update(cid, change),
    )

    refused = quota.reserve(4, 10_005.0)
    assert refused == {"allowed": False, "retry_after": 5, "calls_last_minute": 0}
    assert writes == []

    probe = quota.reserve(4, 10_011.0)
    assert probe["allowed"] is True and probe["probe"] is True
    assert len(writes) == 1


def test_provider_circuit_admits_one_cross_process_probe_and_success_closes(db_isolated):
    circuit = MarketProviderCircuitStore("yahoo_history")
    circuit.record_failure(10_000.0, retry_after=10)

    context = get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_acquire_provider_probe_from_independent_process,
            args=(db_isolated, ready, start, results, 10_011.0),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert sorted(result["allowed"] for result in outcomes) == [False, True]
    assert next(result for result in outcomes if result["allowed"])["probe"] is True

    circuit.record_success(10_012.0)
    acquired = circuit.try_acquire(10_013.0)
    assert {key: acquired[key] for key in ("allowed", "probe", "retry_after")} == {
        "allowed": True,
        "probe": False,
        "retry_after": 0,
    }
    assert circuit.public_state(10_013.0)["failure_streak"] == 0


def test_provider_circuit_namespace_is_an_internal_identifier_only(db_isolated):
    with pytest.raises(ValueError, match="internal identifier"):
        MarketProviderCircuitStore("../../secret_config")


def test_provider_half_open_failure_reopens_with_bounded_backoff(db_isolated):
    circuit = MarketProviderCircuitStore("yahoo_history")
    circuit.record_failure(10_000.0, retry_after=10)
    probe = circuit.try_acquire(10_011.0)

    circuit.record_failure(10_012.0, retry_after=10, generation=probe["generation"])

    assert circuit.try_acquire(10_031.0)["allowed"] is False
    next_probe = circuit.try_acquire(10_033.0)
    assert next_probe["allowed"] is True
    assert next_probe["probe"] is True


def test_late_success_cannot_close_a_newer_provider_failure(db_isolated):
    circuit = MarketProviderCircuitStore("yahoo_history")
    earlier = circuit.try_acquire(10_000.0)
    later = circuit.try_acquire(10_000.0)

    circuit.record_failure(10_001.0, generation=later["generation"])
    circuit.record_success(10_002.0, generation=earlier["generation"])

    state = circuit.public_state(10_002.0)
    assert state["failure_streak"] == 1
    assert state["cooldown_until"] > 10_002.0
    assert state["last_ok_at"] == 10_002.0


@pytest.mark.parametrize(
    ("store", "fail", "acquire"),
    [
        (
            lambda: MarketProviderCircuitStore("yahoo_history"),
            lambda circuit, now, generation=None: circuit.record_failure(
                now, retry_after=1, generation=generation
            ),
            lambda circuit, now: circuit.try_acquire(now, probe_lease_seconds=1),
        ),
        (
            CoinGeckoQuotaStore,
            lambda quota, now, generation=None: quota.record_failure(1, now, generation=generation),
            lambda quota, now: quota.reserve(4, now, probe_lease_seconds=1),
        ),
    ],
)
def test_expired_probe_result_cannot_override_a_newer_probe(db_isolated, store, fail, acquire):
    circuit = store()
    fail(circuit, 10_000.0)
    expired_probe = acquire(circuit, 10_002.0)
    newer_probe = acquire(circuit, 10_004.0)

    circuit.record_success(10_005.0, generation=newer_probe["generation"])
    fail(circuit, 10_006.0, generation=expired_probe["generation"])

    state = circuit.public_state(10_006.0)
    assert state["failure_streak"] == 0
    assert state["cooldown_until"] == 0.0


def test_config_serializes_disjoint_independent_process_patches(db_isolated):
    context = get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_update_config_from_independent_process,
            args=(db_isolated, ready, start, results, changes),
        )
        for changes in (
            {"coingecko_api_key": "demo-key"},
            {"yahoo_primary_for_major_crypto": False},
        )
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert outcomes == [{"ok": True}, {"ok": True}]
    assert MarketDataConfigStore().runtime() == {
        "coingecko_api_key": "demo-key",
        "coingecko_api_key_tier": "demo",
        "yahoo_primary_for_major_crypto": False,
    }


def test_cache_serializes_disjoint_independent_process_puts(db_isolated):
    context = get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_put_cache_from_independent_process,
            args=(db_isolated, ready, start, results, canonical_id),
        )
        for canonical_id in ("bitcoin", "ethereum")
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert outcomes == [{"ok": True}, {"ok": True}]
    assert {
        (row["canonical_id"], row["currency"], row["timeframe"])
        for row in MarketSeriesCacheStore().all()
    } == {("bitcoin", "USD", "1D"), ("ethereum", "USD", "1D")}


def test_cache_round_trips_last_good_and_refuses_empty_or_older_overwrites(db_isolated):
    cache = MarketSeriesCacheStore()
    cache.put("bitcoin", "USD", "1D", _series(as_of=_iso(20)))
    cache.put("bitcoin", "USD", "1D", _series(as_of=_iso(30), points=[]))
    cache.put("bitcoin", "USD", "1D", _series(as_of=_iso(10)))

    row = cache.get("bitcoin", "USD", "1D")
    assert row is not None
    assert row["provider"] == "yahoo"
    assert row["as_of"] == _iso(20)
    assert row["points"] == [{"date": _iso(20), "price": 100.0}]
    assert row["stored_at"]
    assert row["last_accessed_at"]


def test_cache_returns_stale_last_good_until_retention_prune(db_isolated):
    cache = MarketSeriesCacheStore()
    cache.put("bitcoin", "USD", "1D", _series(as_of=_iso()))
    assert cache.get("bitcoin", "USD", "1D") is not None

    cache.prune(now=datetime.now(timezone.utc) + timedelta(days=31))
    assert cache.get("bitcoin", "USD", "1D") is None


def test_cache_prunes_after_30_days_and_caps_at_200_entries(db_isolated):
    cache = MarketSeriesCacheStore()
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    cache.put("old", "USD", "1D", _series(as_of=old))
    for index in range(201):
        cache.put(f"coin-{index}", "USD", "1D", _series(as_of=_iso(index)))

    rows = cache.all()
    assert len(rows) == 200
    assert cache.get("old", "USD", "1D") is None
    assert cache.get("coin-0", "USD", "1D") is None
    assert cache.get("coin-200", "USD", "1D") is not None


def test_observability_retains_bounded_events_without_losing_soak_counts(db_isolated, monkeypatch):
    import backend.db.market_data_observability_store as subject

    monkeypatch.setattr(subject, "_MAX_EVENTS", 3)
    store = MarketDataObservabilityStore()
    now = 2_000_000_000.0
    store.record("provider_request", provider="yahoo", canonical_id="bitcoin", now=now)
    store.record(
        "provider_failure",
        provider="yahoo",
        canonical_id="bitcoin",
        reason="empty_payload",
        latency_ms=12.5,
        now=now + 1,
    )
    store.record("fallback", provider="coingecko", canonical_id="bitcoin", now=now + 2)
    store.record("provider_request", provider="coingecko", canonical_id="bitcoin", now=now + 3)
    store.record(
        "http_429",
        provider="coingecko",
        canonical_id="bitcoin",
        status_code=429,
        remaining_budget=0,
        now=now + 4,
    )
    store.record("stale_serve", provider="coingecko", canonical_id="bitcoin", now=now + 5)

    assert len(store.recent_events()) == 3
    summary = store.summary(hours=24, now=now + 10)
    assert summary["provider_calls"] == {"coingecko": 1, "yahoo": 1}
    assert summary["provider_successes"] == {}
    assert summary["provider_failures"] == {"yahoo": 1}
    assert summary["fallbacks"] == 1
    assert summary["stale_serves"] == 1
    assert summary["unavailable_responses"] == 0
    assert summary["http_429s"] == 1
    assert summary["coalesced_requests"] == 0
    assert summary["validation_rejections"] == 0
    assert summary["event_count"] == 6


def test_observability_batch_writes_once_and_stores_what_single_records_would(
    db_isolated, monkeypatch
):
    """A live price fetch emits a dozen-plus events; each used to be its own read-modify-write
    of the 24-hour soak document (live server 2026-10-24). A batch must land the identical
    events and bucket counts — across a minute boundary — in ONE write."""
    from backend.services import market_data_observability as observability

    now = 60 * 33_333_333 - 1.0  # the next second opens a new minute bucket
    events = [
        ("provider_request", {"provider": "yahoo", "timeframe": "QUOTE", "now": now}),
        ("quota_denied", {"provider": "coingecko", "cooldown_seconds": 55, "now": now}),
        ("stale_serve", {"provider": "coingecko", "canonical_id": "bitcoin", "now": now + 1}),
        (
            "unavailable",
            {"canonical_id": "solana", "reason": "upstream_unavailable", "now": now + 1},
        ),
        ("provider_success", {"provider": "yahoo", "latency_ms": 250.0, "now": now + 2}),
    ]
    store = MarketDataObservabilityStore()
    for event, fields in events:
        store.record(event, **fields)
    expected = store.get()
    store.save({})

    writes = []
    real_save = MarketDataObservabilityStore.save
    monkeypatch.setattr(
        MarketDataObservabilityStore,
        "save",
        lambda self, doc, **kw: writes.append(1) or real_save(self, doc, **kw),
    )
    with observability.batched():
        for event, fields in events:
            observability.emit(event, **fields)
        assert writes == []
    assert len(writes) == 1
    assert store.get() == expected


def test_observability_summary_excludes_events_outside_requested_window(db_isolated):
    store = MarketDataObservabilityStore()
    now = 2_000_000_000.0
    store.record("provider_request", provider="coingecko", now=now - 25 * 3600)
    store.record("provider_request", provider="yahoo", now=now - 60)

    assert store.summary(hours=24, now=now)["provider_calls"] == {"yahoo": 1}


def test_quota_and_circuit_are_state_on_the_price_source_rows(db_isolated):
    from backend.db.connector_store import ConnectorStore

    CoinGeckoQuotaStore().reserve(4, 10_000.0)
    MarketProviderCircuitStore("yahoo_history").record_failure(10_000.0, retry_after=10)
    MarketProviderCircuitStore("yahoo_health").record_success(10_001.0)

    coingecko = ConnectorStore().get("coingecko")["state"]
    yahoo = ConnectorStore().get("yahoo_finance")["state"]

    assert coingecko["quota"]["calls"] == [10_000.0]
    assert set(yahoo["circuit"]) == {"yahoo_history", "yahoo_health"}
    assert yahoo["circuit"]["yahoo_history"]["failure_streak"] == 1
    assert "circuit" not in coingecko


def test_telemetry_stays_in_its_runtime_state_namespace_not_the_connector_rows(db_isolated):
    """Events are written on every provider call and the log holds hundreds of them; a
    ``connectors`` row is rewritten whole on each write and parsed on every list read."""
    from sqlalchemy import text

    from backend.db.connector_store import ConnectorStore
    from backend.db.engine import get_engine

    store = MarketDataObservabilityStore()
    store.record("provider_request", provider="coingecko", now=2_000_000_000.0)
    store.record("provider_request", provider="yahoo", now=2_000_000_001.0)
    store.record("unavailable", canonical_id="bitcoin", now=2_000_000_002.0)

    with get_engine().connect() as conn:
        namespaces = {r[0] for r in conn.execute(text("SELECT namespace FROM runtime_state"))}
    assert "market_data_observability" in namespaces
    for connector_id in ("coingecko", "yahoo_finance"):
        row = ConnectorStore().get(connector_id)
        assert row is None or "observability" not in row["state"]
    summary = store.summary(hours=24, now=2_000_000_010.0)
    assert summary["provider_calls"] == {"coingecko": 1, "yahoo": 1}
    assert summary["unavailable_responses"] == 1
    assert summary["event_count"] == 3


def test_health_writes_leave_the_settings_untouched(db_isolated):
    config = MarketDataConfigStore()
    config.update({"coingecko_api_key": "keep-me", "coingecko_api_key_tier": "pro"})

    CoinGeckoQuotaStore().record_failure(10, 10_000.0)
    MarketProviderCircuitStore("yahoo_history").record_failure(10_000.0)
    MarketDataObservabilityStore().record("fallback", provider="yahoo", now=2_000_000_000.0)

    assert config.runtime() == {
        "coingecko_api_key": "keep-me",
        "coingecko_api_key_tier": "pro",
        "yahoo_primary_for_major_crypto": True,
    }


def test_provider_circuit_must_belong_to_a_price_source(db_isolated):
    with pytest.raises(ValueError, match="price source"):
        MarketProviderCircuitStore("unknown_vendor")
