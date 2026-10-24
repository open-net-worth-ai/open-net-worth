"""Failure-isolated structured telemetry for market-provider selection and soak evidence."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from backend.db.market_data_observability_store import MarketDataObservabilityStore

_LOG = logging.getLogger(__name__)
_pending = threading.local()


def emit(event: str, **fields: object) -> None:
    """Persist and log one secret-safe event; telemetry can never fail market data.

    Inside ``batched()`` on this thread the event is held and written with the rest of the
    batch when it closes."""
    try:
        buffer = getattr(_pending, "rows", None)
        if buffer is None:
            row = MarketDataObservabilityStore().record(event, **fields)
        else:
            row = MarketDataObservabilityStore.build_row(event, **fields)
            buffer.append(row)
        _LOG.info("market_data_event %s", json.dumps(row, sort_keys=True, separators=(",", ":")))
    except Exception as error:  # pragma: no cover - defensive operational isolation
        _LOG.debug("market-data telemetry unavailable: %s", type(error).__name__)


@contextmanager
def batched() -> Iterator[None]:
    """Collect this thread's events and persist them in ONE write on exit. A live price fetch
    emits a dozen or more events (a request, a selection or stale-serve per coin, the quota
    check, fallbacks), and each was its own read-modify-write of the whole 24-hour soak
    document — the bulk of a fetch's CPU on a throttled host, and serialized writes that
    contended for the database. Same events, same timestamps, same counts. Re-entrant: an
    inner batch joins the outer one."""
    if getattr(_pending, "rows", None) is not None:
        yield
        return
    _pending.rows = []
    try:
        yield
    finally:
        rows, _pending.rows = _pending.rows, None
        try:
            MarketDataObservabilityStore().record_rows(rows)
        except Exception as error:  # pragma: no cover - defensive operational isolation
            _LOG.debug("market-data telemetry unavailable: %s", type(error).__name__)


def summary() -> dict:
    """Return the bounded public-safe 24-hour soak counters."""
    try:
        return MarketDataObservabilityStore().summary(hours=24)
    except Exception:  # pragma: no cover - status remains available during telemetry failure
        return {
            "window_hours": 24,
            "since": None,
            "event_count": 0,
            "provider_calls": {},
            "provider_successes": {},
            "provider_failures": {},
            "fallbacks": 0,
            "stale_serves": 0,
            "unavailable_responses": 0,
            "http_429s": 0,
            "coalesced_requests": 0,
            "validation_rejections": 0,
            "quota_denials": 0,
            "latency": {},
        }
