"""Bounded structured market-data events and rolling soak counters."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_MAX_EVENTS = 500
_MAX_MINUTE_BUCKETS = 1_500  # 25 hours, enough for a complete 24-hour soak.
_EVENTS = frozenset(
    {
        "circuit_blocked",
        "circuit_close",
        "circuit_open",
        "coalesced",
        "fallback",
        "http_429",
        "provider_failure",
        "provider_request",
        "provider_selected",
        "provider_success",
        "quota_denied",
        "quota_reserved",
        "stale_serve",
        "unavailable",
        "validation_rejected",
    }
)
_PROVIDERS = frozenset({"coingecko", "yahoo"})


def _bounded_text(value: object, limit: int = 128) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(buckets: list[dict], row: dict) -> None:
    """Fold one event into its minute bucket (created on first use)."""
    event = row["event"]
    provider = row.get("provider")
    latency = row.get("latency_ms")
    minute = int(float(row["at"]) // 60) * 60
    bucket = next((item for item in buckets if item.get("minute") == minute), None)
    if bucket is None:
        bucket = {"minute": minute, "counts": {}, "providers": {}, "latency": {}}
        buckets.append(bucket)
    counts = bucket.setdefault("counts", {})
    counts[event] = int(counts.get(event) or 0) + 1
    if provider is not None:
        providers = bucket.setdefault("providers", {})
        per_event = providers.setdefault(event, {})
        per_event[provider] = int(per_event.get(provider) or 0) + 1
    if latency is not None and provider is not None:
        latency_doc = bucket.setdefault("latency", {}).setdefault(
            provider, {"samples": 0, "total_ms": 0.0, "max_ms": 0.0}
        )
        latency_doc["samples"] = int(latency_doc.get("samples") or 0) + 1
        latency_doc["total_ms"] = float(latency_doc.get("total_ms") or 0.0) + latency
        latency_doc["max_ms"] = max(float(latency_doc.get("max_ms") or 0.0), latency)


class MarketDataObservabilityStore(NamespacedSettingsStore):
    """Keep a diagnostic tail plus minute buckets that survive process restarts.

    The event tail is deliberately small. Minute buckets retain aggregate evidence even
    when a busy process evicts individual events, so the 24-hour provider soak remains
    measurable without storing response bodies, request parameters, or credentials.
    """

    def __init__(self, engine=None):
        super().__init__("market_data_observability", table=runtime_state, engine=engine)

    def record(
        self,
        event: str,
        *,
        provider: str | None = None,
        canonical_id: object = None,
        timeframe: object = None,
        reason: object = None,
        latency_ms: object = None,
        status_code: int | None = None,
        remaining_budget: int | None = None,
        cooldown_seconds: int | None = None,
        now: float | None = None,
    ) -> dict:
        row = self.build_row(
            event,
            provider=provider,
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason=reason,
            latency_ms=latency_ms,
            status_code=status_code,
            remaining_budget=remaining_budget,
            cooldown_seconds=cooldown_seconds,
            now=now,
        )
        self.record_rows([row])
        return dict(row)

    @staticmethod
    def build_row(
        event: str,
        *,
        provider: str | None = None,
        canonical_id: object = None,
        timeframe: object = None,
        reason: object = None,
        latency_ms: object = None,
        status_code: int | None = None,
        remaining_budget: int | None = None,
        cooldown_seconds: int | None = None,
        now: float | None = None,
    ) -> dict:
        """Validate and shape one event without persisting it (see ``record_rows``)."""
        if event not in _EVENTS:
            raise ValueError("unknown market-data observability event")
        if provider is not None and provider not in _PROVIDERS:
            raise ValueError("unknown market-data provider")
        observed = float(time.time() if now is None else now)
        row: dict[str, object] = {
            "at": observed,
            "event": event,
        }
        for key, value in (
            ("provider", provider),
            ("canonical_id", _bounded_text(canonical_id)),
            ("timeframe", _bounded_text(timeframe, 16)),
            ("reason", _bounded_text(reason, 64)),
        ):
            if value is not None:
                row[key] = value
        latency = _finite(latency_ms)
        if latency is not None and latency >= 0:
            row["latency_ms"] = round(latency, 3)
        for key, value in (
            ("status_code", status_code),
            ("remaining_budget", remaining_budget),
            ("cooldown_seconds", cooldown_seconds),
        ):
            if value is not None:
                row[key] = max(0, int(value))
        return row

    def record_rows(self, rows: list[dict]) -> None:
        """Persist already-built rows in ONE read-modify-write of the namespace document —
        the same events and bucket counts as recording each alone, for one write instead of
        one per event (a price fetch emits a dozen or more)."""
        if not rows:
            return
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            events = [item for item in doc.get("events", []) if isinstance(item, dict)]
            buckets = [item for item in doc.get("buckets", []) if isinstance(item, dict)]
            for row in rows:
                events.append(dict(row))
                _count(buckets, row)
            events = sorted(events, key=lambda item: float(item.get("at") or 0.0))[-_MAX_EVENTS:]
            buckets = sorted(buckets, key=lambda item: int(item.get("minute") or 0))[
                -_MAX_MINUTE_BUCKETS:
            ]
            self.save({"events": events, "buckets": buckets}, conn=conn)

    def recent_events(self) -> list[dict]:
        return [dict(item) for item in self.get().get("events", []) if isinstance(item, dict)]

    def summary(self, *, hours: int = 24, now: float | None = None) -> dict:
        window_hours = min(24, max(1, int(hours)))
        observed = float(time.time() if now is None else now)
        cutoff = observed - window_hours * 3600
        cutoff_minute = int(cutoff // 60) * 60
        buckets = [
            item
            for item in self.get().get("buckets", [])
            if isinstance(item, dict) and int(item.get("minute") or 0) >= cutoff_minute
        ]
        counts: dict[str, int] = {}
        providers: dict[str, dict[str, int]] = {}
        latency: dict[str, dict[str, float | int]] = {}
        for bucket in buckets:
            for event, count in bucket.get("counts", {}).items():
                counts[event] = counts.get(event, 0) + int(count or 0)
            for event, provider_counts in bucket.get("providers", {}).items():
                target = providers.setdefault(event, {})
                for provider, count in provider_counts.items():
                    target[provider] = target.get(provider, 0) + int(count or 0)
            for provider, values in bucket.get("latency", {}).items():
                target = latency.setdefault(
                    provider, {"samples": 0, "total_ms": 0.0, "max_ms": 0.0}
                )
                target["samples"] = int(target["samples"]) + int(values.get("samples") or 0)
                target["total_ms"] = float(target["total_ms"]) + float(
                    values.get("total_ms") or 0.0
                )
                target["max_ms"] = max(float(target["max_ms"]), float(values.get("max_ms") or 0.0))
        latency_summary = {
            provider: {
                "samples": int(values["samples"]),
                "average_ms": round(float(values["total_ms"]) / int(values["samples"]), 3),
                "max_ms": round(float(values["max_ms"]), 3),
            }
            for provider, values in latency.items()
            if int(values["samples"]) > 0
        }
        return {
            "window_hours": window_hours,
            "since": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            "event_count": sum(counts.values()),
            "provider_calls": providers.get("provider_request", {}),
            "provider_successes": providers.get("provider_success", {}),
            "provider_failures": providers.get("provider_failure", {}),
            "fallbacks": counts.get("fallback", 0),
            "stale_serves": counts.get("stale_serve", 0),
            "unavailable_responses": counts.get("unavailable", 0),
            "http_429s": counts.get("http_429", 0),
            "coalesced_requests": counts.get("coalesced", 0),
            "validation_rejections": counts.get("validation_rejected", 0),
            "quota_denials": counts.get("quota_denied", 0),
            "latency": latency_summary,
        }
