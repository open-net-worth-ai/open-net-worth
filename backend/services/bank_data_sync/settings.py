"""A bank connector's data-sync settings and the one-mode rule.

Stored per connector in ``bank_sync_settings`` (id = provider key): ``mode`` (``off`` | ``data``),
``excluded_account_ids`` (the app accounts the user unticked — Sync is on by default, so only the
opt-outs are kept, keyed by app account id because that survives a provider relink) and
``lucius_followup``. "Lucius" is never stored: a connector is in Lucius mode when an active Lucius
Automation syncs it, a fact the Automations table already holds (ADR 2026-11-13)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.core.errors import ConflictError, NotFoundError
from backend.db.bank_sync_store import BankSyncSettingsStore
from backend.services.aggregation import providers

MODES = ("off", "data", "lucius")
_SYNC_VERBS = ("fetch", "sync", "book", "import")


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
        "updated_at": row.get("updated_at"),
    }


def save(key: str, **changes) -> dict:
    row = {**get(key), **changes, "id": key, "updated_at": _now()}
    BankSyncSettingsStore().upsert(row)
    return row


def is_data(key: str) -> bool:
    return get(key)["mode"] == "data"


# ── Lucius sync Automations ─────────────────────────────────────────────────────────


def syncs_connector(automation: dict, key: str) -> bool:
    """Whether ``automation`` is a Lucius sync of connector ``key``: it names the connector's
    fetch tool (in its words or its always-allowed tools), or names the connector and fetches,
    syncs, books or imports. A heuristic over the user's own words — the ref floor still stops
    a double booking when it misses."""
    tool = f"{key}_fetch_transactions"
    allowed = {
        t for entry in automation.get("always_allowed") or [] for t in (entry.get("tools") or [])
    }
    if tool in allowed:
        return True
    text = f"{automation.get('name') or ''}\n{automation.get('instructions') or ''}".lower()
    if tool in text:
        return True
    label = providers.PROVIDERS[key].label.lower() if key in providers.PROVIDERS else key
    return label in text and any(verb in text for verb in _SYNC_VERBS)


def lucius_syncs(key: str, statuses: tuple[str, ...] = ("active",)) -> list[dict]:
    from backend.db.automation_store import AutomationStore

    return [
        a
        for a in AutomationStore().all()
        if a.get("status") in statuses and syncs_connector(a, key)
    ]


def mode(key: str) -> str:
    """``data`` when stored so; else ``lucius`` when an active Lucius Automation syncs it."""
    if is_data(key):
        return "data"
    return "lucius" if lucius_syncs(key) else "off"


def refuse_beside_data(automation: dict) -> None:
    """The reverse of the one-mode rule: an Automation about to be ACTIVE that syncs a connector
    in data mode is refused, naming the switch that changes it."""
    for p in capable_providers():
        if is_data(p.key) and syncs_connector(automation, p.key):
            raise ConflictError(
                f"{p.label} already syncs without AI, so this Automation would book its rows "
                f"twice. To let Lucius sync it instead, choose Lucius on the {p.label} connector."
            )


def pause_lucius_syncs(key: str) -> list[str]:
    """Pause every active Lucius sync of ``key``; the names of those paused."""
    from backend.services.automations.service import apply_automation_change

    names = []
    for a in lucius_syncs(key):
        apply_automation_change({"op": "pause", "id": a["id"]})
        names.append(a.get("name") or "Untitled")
    return names


def resume_lucius_sync(key: str) -> Optional[str]:
    """Resume the ONE paused Lucius sync of ``key``, if there is exactly one; its name."""
    from backend.services.automations.service import apply_automation_change

    paused = lucius_syncs(key, ("paused",))
    if len(paused) != 1:
        return None
    apply_automation_change({"op": "resume", "id": paused[0]["id"]})
    return paused[0].get("name") or "Untitled"
