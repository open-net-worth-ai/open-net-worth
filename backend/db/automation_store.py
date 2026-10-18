"""Repository for canonical Automation configuration rows.

PLAN 4D TASK 2: DB-backed (Tier-1, book-scoped, table ``automations``) via
``BookPayloadTableStore`` — spec:139-140 lists automations explicitly as Tier-1.
``all_persisted``/``find_persisted_by_id`` read the RAW (unfiltered) table via ``super().all()``
/``super().find_by_id()`` — tombstone rows (``status="deleting"``) included — while the
OVERRIDDEN ``all``/``find_by_id``/``active`` filter to ``LIVE_STATUSES`` in Python, exactly as
the JsonStore-era versions did over ``self._load_items()``.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

from backend.core.ids import new_id
from backend.core.urls import normalize_host
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import automations

LIVE_STATUSES = frozenset({"active", "paused", "completed"})
DELETING_STATUS = "deleting"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_tz() -> tzinfo:
    """Return the server timezone used by legacy timezone-less schedules."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tz())
    return parsed.astimezone(timezone.utc)


def _copy_dict(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _copy_dicts(value: object) -> list[dict]:
    return (
        [dict(item) for item in value]
        if isinstance(value, list) and all(isinstance(item, dict) for item in value)
        else []
    )


def _normalize_execution(value: object) -> dict:
    execution = {"kind": "lucius"} if value is None else _copy_dict(value)
    if execution != {"kind": "lucius"}:
        raise ValueError("Automation execution must run Lucius")
    return execution


def _normalize_always_allowed(value: object) -> list[dict]:
    normalized: list[dict] = []
    for item in _copy_dicts(value):
        row = {
            "src": str(item.get("src") or "").strip(),
            "desc": str(item.get("desc") or "").strip(),
        }
        capability = item.get("capability")
        if isinstance(capability, str) and capability.strip():
            row["capability"] = capability.strip()
        tools = item.get("tools")
        if isinstance(tools, list):
            row["tools"] = list(
                dict.fromkeys(str(tool).strip() for tool in tools if str(tool).strip())
            )
        normalized.append(row)
    return normalized


def _normalize_fetch_hosts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    hosts = (normalize_host(item) for item in value if isinstance(item, str))
    return list(dict.fromkeys(host for host in hosts if host))


def _make_automation(data: dict) -> dict:
    """Normalize a persisted Automation row and discard unrecognised fields."""
    now = _now()
    status = str(data.get("status") or "active")
    if status not in LIVE_STATUSES:
        raise ValueError(f"invalid automation status {status!r}")
    return {
        "id": data.get("id") or new_id("automation"),
        "name": str(data.get("name") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "instructions": str(data.get("instructions") or "").strip(),
        "status": status,
        "trigger": _copy_dict(data.get("trigger")),
        "trigger_revision": str(data.get("trigger_revision") or new_id("triggerrev")),
        "execution": _normalize_execution(data.get("execution")),
        "permission_mode": str(data.get("permission_mode") or "manual"),
        "always_allowed": _normalize_always_allowed(data.get("always_allowed")),
        "fetch_hosts": _normalize_fetch_hosts(data.get("fetch_hosts")),
        "model": str(data.get("model") or "").strip(),
        "effort": str(data.get("effort") or "medium").strip() or "medium",
        "max_runs": data.get("max_runs"),
        "run_count": int(data.get("run_count") or 0),
        "next_run_at": data.get("next_run_at"),
        "last_run_at": data.get("last_run_at"),
        "last_status": data.get("last_status"),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
        "provenance": _copy_dict(data.get("provenance")),
        "stats": _copy_dict(data.get("stats")),
    }


class AutomationStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(automations, id_field="id", engine=engine)

    def all_persisted(self) -> list[dict]:
        """Return configuration rows including internal deletion tombstones."""
        return super().all()

    def find_persisted_by_id(self, automation_id: str) -> dict | None:
        """Find a row including a deletion tombstone used to resume cleanup."""
        return super().find_by_id(automation_id)

    def all(self) -> list[dict]:
        """Return only Automations that may be published to callers."""
        return [row for row in super().all() if row.get("status") in LIVE_STATUSES]

    def find_by_id(self, automation_id: str) -> dict | None:
        row = self.find_persisted_by_id(automation_id)
        if row is None or row.get("status") == DELETING_STATUS:
            return None
        return row

    def active(self) -> list[dict]:
        return [row for row in super().all() if row.get("status") in LIVE_STATUSES]

    def due(self, now_iso: str) -> list[dict]:
        return [
            row
            for row in super().all()
            if row.get("status") == "active"
            and row.get("next_run_at")
            and _is_due(row["next_run_at"], now_iso)
        ]

    def create(self, data: dict) -> dict:
        return self.insert(_make_automation(data))


def _is_due(next_run_at: str, now_iso: str) -> bool:
    """Compare canonical ISO instants; malformed persisted data fails closed."""
    return _parse_instant(next_run_at) <= _parse_instant(now_iso)
