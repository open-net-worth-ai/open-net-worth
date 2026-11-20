"""Bounded persistent last-good market-series cache (runtime state, rebuildable)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_RETENTION = timedelta(days=30)
_MAX_ENTRIES = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class MarketSeriesCacheStore(NamespacedSettingsStore):
    def __init__(self, engine=None):
        super().__init__("market_series_cache", table=runtime_state, engine=engine)

    @staticmethod
    def _key(canonical_id: str, currency: str, timeframe: str) -> tuple[str, str, str]:
        return (str(canonical_id), str(currency).upper(), str(timeframe).upper())

    @classmethod
    def _valid_result(cls, result: dict) -> bool:
        return bool(
            isinstance(result, dict)
            and isinstance(result.get("provider"), str)
            and result["provider"]
            and isinstance(result.get("points"), list)
            and result["points"]
            and _parse(result.get("as_of")) is not None
        )

    @staticmethod
    def _retained(entries: list[dict], now: datetime) -> list[dict]:
        cutoff = now - _RETENTION
        return [
            entry
            for entry in entries
            if (_parse(entry.get("stored_at")) or datetime.min.replace(tzinfo=timezone.utc))
            >= cutoff
        ]

    @classmethod
    def _cap(cls, entries: list[dict]) -> list[dict]:
        if len(entries) <= _MAX_ENTRIES:
            return entries
        # Position breaks a tie: entries are appended as written, and a coarse clock (Windows)
        # stamps a burst of writes identically — the newest must still outrank the oldest.
        ranked = sorted(
            enumerate(entries),
            key=lambda pair: (
                _parse(pair[1].get("last_accessed_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                pair[0],
            ),
            reverse=True,
        )
        return [entry for _, entry in ranked[:_MAX_ENTRIES]]

    def put(self, canonical_id: str, currency: str, timeframe: str, result: dict) -> dict | None:
        """Persist only a newer valid success; no failure may poison last-good data."""
        if not self._valid_result(result):
            return self.get(canonical_id, currency, timeframe)
        key = self._key(canonical_id, currency, timeframe)
        now = _now()
        now_iso = now.isoformat()
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            entries = self._retained(list(doc.get("entries", [])), now)
            existing = next(
                (
                    entry
                    for entry in entries
                    if self._key(
                        entry.get("canonical_id", ""),
                        entry.get("currency", ""),
                        entry.get("timeframe", ""),
                    )
                    == key
                ),
                None,
            )
            if (
                existing
                and _parse(existing.get("as_of"))
                and _parse(result["as_of"]) < _parse(existing["as_of"])
            ):
                self.save({"entries": self._cap(entries)}, conn=conn)
                return dict(existing)
            entries = [entry for entry in entries if entry is not existing]
            row = {
                "canonical_id": key[0],
                "currency": key[1],
                "timeframe": key[2],
                "provider": result["provider"],
                "as_of": result["as_of"],
                "stored_at": now_iso,
                "last_accessed_at": now_iso,
                "points": result["points"],
            }
            entries.append(row)
            self.save({"entries": self._cap(entries)}, conn=conn)
            return dict(row)

    def get(self, canonical_id: str, currency: str, timeframe: str) -> dict | None:
        key = self._key(canonical_id, currency, timeframe)
        now = _now()
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            entries = self._retained(list(doc.get("entries", [])), now)
            index = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if self._key(
                        entry.get("canonical_id", ""),
                        entry.get("currency", ""),
                        entry.get("timeframe", ""),
                    )
                    == key
                ),
                None,
            )
            row = None
            if index is not None:
                row = dict(entries[index])
                row["last_accessed_at"] = now.isoformat()
                entries[index] = row
            self.save({"entries": self._cap(entries)}, conn=conn)
            return dict(row) if row else None

    def clear(
        self,
        canonical_id: str | None = None,
        currency: str | None = None,
        timeframe: str | None = None,
    ) -> None:
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            if canonical_id is None and currency is None and timeframe is None:
                self.save({"entries": []}, conn=conn)
                return
            entries = [
                entry
                for entry in doc.get("entries", [])
                if not (
                    (canonical_id is None or entry.get("canonical_id") == canonical_id)
                    and (currency is None or entry.get("currency") == str(currency).upper())
                    and (timeframe is None or entry.get("timeframe") == str(timeframe).upper())
                )
            ]
            self.save({"entries": entries}, conn=conn)

    def prune(self, *, now: datetime | None = None) -> None:
        at = now or _now()
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            self.save(
                {"entries": self._cap(self._retained(list(doc.get("entries", [])), at))}, conn=conn
            )

    def all(self) -> list[dict]:
        with self._use_serialized_namespace_conn(None) as conn:
            doc = super().get(conn=conn)
            entries = self._cap(self._retained(list(doc.get("entries", [])), _now()))
            self.save({"entries": entries}, conn=conn)
            return [dict(entry) for entry in entries]
