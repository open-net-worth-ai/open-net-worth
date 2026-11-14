"""A bank connector's data-sync settings.

Stored per connector in ``bank_sync_settings`` (id = provider key): ``mode`` (``off`` | ``data``),
``excluded_account_ids`` (the app accounts the user unticked — Sync is on by default, so only the
opt-outs are kept, keyed by app account id because that survives a provider relink),
``lucius_followup`` (built, hidden in the UI for now), ``set_up`` (turned on at least once) and
``interval_hours`` (the schedule the user chose; absent = the provider's default). The sync and
a Lucius Automation are independent: nothing here detects, pauses or refuses one for the other
(ADR 2026-11-13, Amendment 6)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import NotFoundError
from backend.db.bank_sync_store import BankSyncSettingsStore
from backend.services.aggregation import providers

MODES = ("off", "data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def capable_providers() -> list[providers.Provider]:
    """The offered providers whose rows the data sync may book (``Provider.data_sync``)."""
    return [
        providers.PROVIDERS[k] for k in providers.PROVIDER_KEYS if providers.PROVIDERS[k].data_sync
    ]


def provider(key: str) -> providers.Provider:
    """The data-sync-capable provider ``key`` — anything else is a 404, never a guess."""
    found = next((p for p in capable_providers() if p.key == key), None)
    if found is None:
        raise NotFoundError(f"{key!r} has no sync without AI.")
    return found


def get(key: str) -> dict:
    row = BankSyncSettingsStore().find_by_id(key) or {}
    return {
        "id": key,
        "mode": row.get("mode") if row.get("mode") in ("off", "data") else "off",
        "excluded_account_ids": list(row.get("excluded_account_ids") or []),
        "lucius_followup": bool(row.get("lucius_followup")),
        # Turned on at least once: its page shows the set-up sync from then on, on or off.
        "set_up": bool(row.get("set_up")) or row.get("mode") == "data",
        # How often it runs, when the user chose; None keeps the provider's default.
        "interval_hours": row.get("interval_hours")
        if isinstance(row.get("interval_hours"), int)
        else None,
        "updated_at": row.get("updated_at"),
    }


_INTERVALS = (1, 6, 12, 24)


def interval_choices(p) -> list[int]:
    """The schedules a bank may run on. A bank with a daily read limit is never offered one
    that would need more reads than it allows (4 a day → every 6 hours at the most)."""
    floor = 24 // p.daily_reads if p.daily_reads else 1
    return [h for h in _INTERVALS if h >= floor]


def interval_hours(p) -> int:
    """How often a bank's sync runs: the user's choice, else the provider's default."""
    chosen = get(p.key)["interval_hours"]
    return chosen if chosen in interval_choices(p) else p.sync_interval_hours


def save(key: str, **changes) -> dict:
    row = {**get(key), **changes, "id": key, "updated_at": _now()}
    BankSyncSettingsStore().upsert(row)
    return row


def is_data(key: str) -> bool:
    return get(key)["mode"] == "data"


def mode(key: str) -> str:
    """``data`` while the sync is on, else ``off``."""
    return "data" if is_data(key) else "off"
