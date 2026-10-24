"""Cross-process rolling-minute CoinGecko request reservations.

Persisted as ``state.quota`` on the ``coingecko`` connector row; every write is ONE serialised
transaction (``price_source_store.update_state``), so independent workers share one window.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable

from backend.db import price_source_store


class CoinGeckoQuotaStore:
    @staticmethod
    def _transact(now: float, work: Callable[[dict], tuple[dict, object]]):
        """Run ``work(state) -> (new state, result)`` against the current quota in one
        transaction and return its result."""
        outcome: list = []

        def change(state: dict) -> dict:
            quota, result = work(CoinGeckoQuotaStore._state(state.get("quota") or {}, now))
            outcome.append(result)
            return {**state, "quota": quota}

        price_source_store.update_state(price_source_store.COINGECKO, change)
        return outcome[0]

    @staticmethod
    def _state(doc: dict, now: float) -> dict:
        def number(key: str) -> float:
            try:
                return float(doc.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def integer(key: str) -> int:
            try:
                return max(0, int(doc.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        calls = []
        for raw in doc.get("calls", []):
            try:
                timestamp = float(raw)
            except (TypeError, ValueError):
                continue
            if timestamp > now - 60:
                calls.append(timestamp)
        return {
            "calls": sorted(calls),
            "cooldown_until": number("cooldown_until"),
            "probe_lease_until": number("probe_lease_until"),
            "failure_streak": integer("failure_streak"),
            "generation": integer("generation"),
            "last_ok_at": number("last_ok_at"),
        }

    def reserve(self, limit: int, now: float, *, probe_lease_seconds: int | float = 30) -> dict:
        """Reserve one slot in a shared 60-second rolling window before HTTP I/O."""
        if limit <= 0:
            return {"allowed": False, "retry_after": 60, "calls_last_minute": 0}
        # An open circuit refuses without the serialized write transaction below: while
        # CoinGecko is down every price check asked, and each ask took the database's write
        # lock just to be told no (live server 2026-10-24: `database is locked`). Exact, not a
        # heuristic — a cooldown is only ever cleared by a probe, which needs it expired first.
        current = self._state(
            price_source_store.read(price_source_store.COINGECKO)["state"].get("quota") or {}, now
        )
        if current["cooldown_until"] > now:
            return {
                "allowed": False,
                "retry_after": max(1, int(math.ceil(current["cooldown_until"] - now))),
                "calls_last_minute": len(current["calls"]),
            }

        def work(state: dict) -> tuple[dict, dict]:
            calls = state["calls"]
            if state["cooldown_until"] > now:
                return state, {
                    "allowed": False,
                    "retry_after": max(1, int(math.ceil(state["cooldown_until"] - now))),
                    "calls_last_minute": len(calls),
                }
            if len(calls) >= limit:
                return state, {
                    "allowed": False,
                    "retry_after": max(1, int(math.ceil(calls[0] + 60 - now))),
                    "calls_last_minute": len(calls),
                }
            probe = state["failure_streak"] > 0
            if probe and state["probe_lease_until"] > now:
                return state, {
                    "allowed": False,
                    "retry_after": max(1, int(math.ceil(state["probe_lease_until"] - now))),
                    "calls_last_minute": len(calls),
                }
            if probe:
                state["generation"] += 1
                state["probe_lease_until"] = now + max(1.0, float(probe_lease_seconds))
            calls.append(float(now))
            return state, {
                "allowed": True,
                "retry_after": 0,
                "calls_last_minute": len(calls),
                "probe": probe,
                "generation": state["generation"],
            }

        return self._transact(now, work)

    def set_cooldown(self, seconds: int, now: float) -> float:
        """Compatibility wrapper for recording a provider failure."""
        return self.record_failure(seconds, now)

    def record_failure(
        self,
        seconds: int | float,
        now: float,
        *,
        generation: int | None = None,
    ) -> float:
        """Open/reopen the circuit with bounded exponential backoff after a failure."""

        def work(state: dict) -> tuple[dict, float]:
            if generation is not None and generation != state["generation"]:
                return state, state["cooldown_until"]
            state["failure_streak"] += 1
            state["generation"] += 1
            exponent = min(state["failure_streak"] - 1, 10)
            delay = min(300.0, max(1.0, float(seconds)) * (2**exponent))
            state["cooldown_until"] = max(state["cooldown_until"], now + delay)
            state["probe_lease_until"] = 0.0
            return state, state["cooldown_until"]

        return self._transact(now, work)

    def record_success(self, now: float, *, generation: int | None = None) -> None:
        """Close the circuit only for a response from its current generation."""

        def work(state: dict) -> tuple[dict, None]:
            state["last_ok_at"] = max(state["last_ok_at"], float(now))
            if generation is None or generation == state["generation"]:
                state["failure_streak"] = 0
                state["cooldown_until"] = 0.0
                state["probe_lease_until"] = 0.0
            return state, None

        self._transact(now, work)

    def release_probe(self, now: float, *, generation: int | None = None) -> None:
        """Release a half-open lease without changing provider health.

        Used for client-side HTTP errors such as 404: the request consumed quota, but it
        does not prove the provider is down and must not strand the provider circuit open.
        """

        def work(state: dict) -> tuple[dict, None]:
            if generation is None or generation == state["generation"]:
                state["probe_lease_until"] = 0.0
            return state, None

        self._transact(now, work)

    def public_state(self, now: float) -> dict:
        """Bounded state for safe health reporting; stale quota timestamps are left out. A pure
        read — the next reservation prunes what is stored."""
        state = self._state(
            price_source_store.read(price_source_store.COINGECKO)["state"].get("quota") or {}, now
        )
        return {
            "calls_last_minute": len(state["calls"]),
            "cooldown_until": state["cooldown_until"],
            "failure_streak": state["failure_streak"],
            "last_ok_at": (
                datetime.fromtimestamp(state["last_ok_at"], timezone.utc).isoformat()
                if state["last_ok_at"]
                else None
            ),
        }

    def calls_last_minute(self, now: float) -> int:
        return self.public_state(now)["calls_last_minute"]
