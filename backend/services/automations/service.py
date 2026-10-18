"""Automation configuration validation and the single mutation choke."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.urls import normalize_host
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import DELETING_STATUS, AutomationStore

from .price_symbols import pin_price_instrument
from .references import resolve_trigger_references
from .schedules import compute_next_run, validate_trigger

CleanupHandler = Callable[..., dict]
_cleanup_handler: Optional[CleanupHandler] = None
_EDITABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "instructions",
        "status",
        "trigger",
        "permission_mode",
        "always_allowed",
        "fetch_hosts",
        "model",
        "effort",
        "max_runs",
        "provenance",
    }
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def register_automation_cleanup(handler: Optional[CleanupHandler]) -> None:
    """Register the higher-layer session/artifact/pending-plan cleanup adapter."""
    global _cleanup_handler
    _cleanup_handler = handler


def validated_trigger(raw: Any, *, now: datetime | None = None, previous: object = None) -> dict:
    """Names resolve to ids first, then the trigger validates — so "Grab" and its id meet as
    one payee in the conflict check, and a dedupe sees ids, not spellings."""
    previous_trigger = previous if isinstance(previous, dict) else None
    if isinstance(raw, dict):
        raw = resolve_trigger_references(raw, previous=previous_trigger)
    try:
        return validate_trigger(raw, now=now)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_automation(
    value: object, *, now: datetime | None = None, previous_trigger: object = None
) -> dict:
    if not isinstance(value, dict):
        raise ValidationError("automation must be an object")
    clean = dict(value)
    name = str(clean.get("name") or "").strip()
    instructions = str(clean.get("instructions") or "").strip()
    if not name:
        raise ValidationError("automation name is required")
    if not instructions:
        raise ValidationError("automation instructions are required")
    trigger = validated_trigger(clean.get("trigger"), now=now, previous=previous_trigger)
    execution = _validate_execution(clean.get("execution"))
    permission_mode = str(clean.get("permission_mode") or "auto").strip()
    if permission_mode not in {"manual", "auto", "skip"}:
        raise ValidationError("automation permission_mode must be manual, auto, or skip")
    always_allowed = _validate_always_allowed(clean.get("always_allowed") or [])
    fetch_hosts = _validate_fetch_hosts(clean.get("fetch_hosts") or [])
    status = clean.get("status") or "active"
    if status not in {"active", "paused", "completed"}:
        raise ValidationError("automation status must be active or paused")
    max_runs = clean.get("max_runs")
    if max_runs is not None and (
        isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1
    ):
        raise ValidationError("automation max_runs must be a positive integer or null")
    effort = str(clean.get("effort") or "medium").strip()
    if effort not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        raise ValidationError("automation effort is invalid")
    provenance = clean.get("provenance") or {}
    if not isinstance(provenance, dict):
        raise ValidationError("automation provenance must be an object")
    return {
        **clean,
        "name": name,
        "description": str(clean.get("description") or "").strip(),
        "instructions": instructions,
        "status": status,
        "trigger": trigger,
        "execution": execution,
        "permission_mode": permission_mode,
        "always_allowed": always_allowed,
        "fetch_hosts": fetch_hosts,
        "model": str(clean.get("model") or "").strip(),
        "effort": effort,
        "max_runs": max_runs,
        "provenance": dict(provenance),
    }


_MAX_FETCH_HOSTS = 50


def _validate_fetch_hosts(value: object) -> list[str]:
    """The websites ``fetch_page`` may open unasked in this Automation's runs — the owner's
    list, so a bad entry is refused by name rather than silently dropped or widened."""
    if not isinstance(value, list):
        raise ValidationError("automation fetch_hosts must be a list of website names")
    hosts: list[str] = []
    for item in value:
        host = normalize_host(item) if isinstance(item, str) else ""
        if not host:
            raise ValidationError(f"not a website name: {item!r} (for example store.example.com)")
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > _MAX_FETCH_HOSTS:
        raise ValidationError(f"an Automation can list at most {_MAX_FETCH_HOSTS} websites")
    return hosts


def _validate_always_allowed(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValidationError("automation always_allowed permissions must be a list")
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("automation always_allowed permission must be an object")
        unknown = set(item) - {"src", "desc", "capability", "tools"}
        if unknown:
            raise ValidationError(
                f"unknown automation permission fields: {', '.join(sorted(unknown))}"
            )
        source = str(item.get("src") or "").strip()
        description = str(item.get("desc") or "").strip()
        if not source or not description:
            raise ValidationError(
                "automation always_allowed permission requires source and description"
            )
        permission = {"src": source, "desc": description}
        if "capability" in item:
            capability = item.get("capability")
            if not isinstance(capability, str) or not capability.strip():
                raise ValidationError("automation permission capability must be a non-empty string")
            permission["capability"] = capability.strip()
        if "tools" in item:
            tools = item.get("tools")
            if not isinstance(tools, list) or any(
                not isinstance(tool, str) or not tool.strip() for tool in tools
            ):
                raise ValidationError("automation permission tools must be a list of tool names")
            permission["tools"] = list(dict.fromkeys(tool.strip() for tool in tools))
        result.append(permission)
    return result


def _validate_execution(value: object) -> dict:
    execution = {"kind": "lucius"} if value is None else value
    if execution != {"kind": "lucius"}:
        raise ValidationError("Automation execution kind must be Lucius")
    return {"kind": "lucius"}


def apply_automation_change(change: dict) -> dict:
    op = change.get("op")
    store = AutomationStore()
    now = now_utc()
    if op == "create":
        clean = validate_automation(change.get("automation") or change, now=now)
        clean["trigger"] = pin_price_instrument(clean["trigger"])
        _refuse_caller_completed(clean["status"])
        clean["trigger_revision"] = new_id("triggerrev")
        clean["next_run_at"] = (
            compute_next_run(clean["trigger"], now, created_at=clean.get("created_at"))
            if clean["status"] == "active"
            else None
        )
        return store.create(clean)

    automation_id = change.get("id")
    existing = store.find_persisted_by_id(automation_id) if automation_id else None
    if existing is None:
        raise NotFoundError(f"automation {automation_id!r} not found")
    if existing.get("status") == DELETING_STATUS:
        if op == "delete":
            return _resume_hard_delete(store, existing)
        raise NotFoundError(f"automation {automation_id!r} not found")
    if op == "pause":
        return store.update_by_id(
            automation_id,
            {"status": "paused", "next_run_at": None, "updated_at": now.isoformat()},
        )
    if op == "resume":
        trigger = validate_trigger(existing["trigger"], now=now)
        next_run = compute_next_run(trigger, now, created_at=existing.get("created_at"))
        _refuse_rearming_without_a_run(existing, trigger, next_run)
        return store.update_by_id(
            automation_id,
            {"status": "active", "next_run_at": next_run, "updated_at": now.isoformat()},
        )
    if op == "update":
        changes = dict(change.get("changes") or {})
        unknown = set(changes) - _EDITABLE_FIELDS
        if unknown:
            raise ValidationError(f"unknown automation fields: {', '.join(sorted(unknown))}")
        _refuse_caller_completed(changes.get("status"), existing.get("status"))
        if changes.get("status") == "completed":
            changes.pop("status")  # an unchanged echo of the row, not a request
        if _trigger_really_changes(existing, changes, now):
            changes.setdefault("status", "active")
        merged = validate_automation(
            {**existing, **changes}, now=now, previous_trigger=existing.get("trigger")
        )
        if "trigger" in changes:
            merged["trigger"] = pin_price_instrument(merged["trigger"])
        patch = {key: merged[key] for key in _EDITABLE_FIELDS if key in changes}
        if {"status", "trigger"}.intersection(changes):
            patch["next_run_at"] = (
                compute_next_run(merged["trigger"], now, created_at=existing.get("created_at"))
                if merged["status"] == "active"
                else None
            )
            if merged["status"] == "active":
                _refuse_rearming_without_a_run(existing, merged["trigger"], patch["next_run_at"])
        patch["updated_at"] = now.isoformat()
        trigger_changed = "trigger" in changes and merged["trigger"] != existing.get("trigger")
        if trigger_changed:
            patch["trigger_revision"] = new_id("triggerrev")
            from .events import clear_trigger_runtime_state, trigger_state_change_guard

            with trigger_state_change_guard():
                updated = store.update_by_id(automation_id, patch)
                clear_trigger_runtime_state(automation_id)
                return updated
        return store.update_by_id(automation_id, patch)
    if op == "delete":
        return _hard_delete(store, existing)
    raise ValidationError("unknown Automation change")


def _refuse_caller_completed(status: object, current: object = None) -> None:
    if status == "completed" and current != "completed":
        raise ValidationError(
            "automation status 'completed' is set when a schedule runs out, never by a caller"
        )


def _trigger_really_changes(existing: dict, changes: dict, now: datetime) -> bool:
    """Only a NEW trigger re-arms a finished Automation — an editor re-sends the trigger on
    every save, and a rename must not try (and fail) to resume a spent one-shot."""
    if existing.get("status") != "completed" or "trigger" not in changes:
        return False
    try:
        trigger = validated_trigger(changes["trigger"], now=now, previous=existing.get("trigger"))
    except (ValueError, ValidationError):
        return False  # the merge below raises the real validation error
    return trigger != existing.get("trigger")


def _refuse_rearming_without_a_run(existing: dict, trigger: dict, next_run: str | None) -> None:
    """A finished schedule re-armed as-is would sit ``active`` and never run again."""
    if existing.get("status") == "completed" and trigger.get("kind") == "schedule" and not next_run:
        raise ValidationError(
            "this automation has no future run — give it a new date or schedule to run it again"
        )


def preview_automation_updates(updates: list[dict]) -> list[dict]:
    """Build a read-only display diff projection for proposed automation updates.

    For each update:
    1. Resolves the stored row via AutomationStore.find_by_id(id).
    2. Raises NotFoundError if absent.
    3. Returns the user-facing name and current updated_at as baseline.
    4. For each editable field present in the patch, normalizes and compares values.
    5. Omits unchanged fields and preserves update order.
    """
    store = AutomationStore()
    now = now_utc()
    previews: list[dict] = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        automation_id = item.get("id")
        if not automation_id:
            raise NotFoundError("automation '' not found")
        existing = store.find_by_id(str(automation_id))
        if existing is None:
            raise NotFoundError(f"automation {automation_id!r} not found")

        changes_raw = {k: v for k, v in item.items() if k in _EDITABLE_FIELDS and k != "id"}
        unknown = set(item.keys()) - _EDITABLE_FIELDS - {"id"}
        if unknown:
            raise ValidationError(f"unknown automation fields: {', '.join(sorted(unknown))}")

        merged = validate_automation(
            {**existing, **changes_raw}, now=now, previous_trigger=existing.get("trigger")
        )
        field_changes: dict[str, dict[str, object]] = {}

        # Preserve update order from item keys
        for key in item:
            if key in _EDITABLE_FIELDS and key in changes_raw:
                old_val = existing.get(key)
                new_val = merged.get(key)
                if old_val != new_val:
                    field_changes[key] = {
                        "old": old_val,
                        "new": new_val,
                    }

        previews.append(
            {
                "id": str(automation_id),
                "name": existing.get("name") or str(automation_id),
                "baseline_updated_at": existing.get("updated_at"),
                "changes": field_changes,
            }
        )
    return previews


def _hard_delete(store: AutomationStore, automation: dict) -> dict:
    from .runs import assert_automation_delete_safe

    assert_automation_delete_safe(automation["id"])
    automation_id = automation["id"]
    runs_store = AutomationRunStore()
    runtime = AutomationRuntimeStore().get_for_automation(automation_id) or {}
    claimed_run_id = runtime.get("running_run_id")
    if claimed_run_id and runs_store.find_by_id(str(claimed_run_id)) is None:
        raise ConflictError(
            "automation is currently starting; wait for the run to be recorded before deleting it"
        )
    runs = runs_store.for_automation(automation_id, limit=1_000_000, offset=0)
    session_ids = sorted({str(row["session_id"]) for row in runs if row.get("session_id")})
    if session_ids:
        if _cleanup_handler is None:
            raise ConflictError("automation cleanup ownership could not be proved")
        context = {"automation": dict(automation), "runs": [dict(row) for row in runs]}
        proof = _cleanup_handler(context, phase="prove")
        if not isinstance(proof, dict) or proof.get("ok") is not True:
            raise ConflictError("automation cleanup ownership could not be proved")
        owned = sorted({str(value) for value in proof.get("owned_session_ids") or []})
        if owned != session_ids:
            raise ConflictError("automation cleanup ownership could not be proved")

    deletion = {
        "version": 1,
        "requested_at": now_utc().isoformat(),
        "runs": [
            {"id": str(row["id"]), "session_id": row.get("session_id")}
            for row in runs
            if row.get("id")
        ],
        "owned_session_ids": session_ids,
    }
    tombstone = store.update_by_id(
        automation_id,
        {
            "status": DELETING_STATUS,
            "next_run_at": None,
            "deletion": deletion,
            "updated_at": now_utc().isoformat(),
        },
    )
    if tombstone is None:
        raise ConflictError(f"automation {automation_id!r} changed during delete")
    return _resume_hard_delete(store, tombstone)


def _resume_hard_delete(store: AutomationStore, automation: dict) -> dict:
    automation_id = str(automation.get("id") or "")
    deletion = automation.get("deletion")
    if not isinstance(deletion, dict) or deletion.get("version") != 1:
        raise ConflictError("automation deletion manifest is invalid")
    raw_runs = deletion.get("runs")
    if not isinstance(raw_runs, list) or any(not isinstance(row, dict) for row in raw_runs):
        raise ConflictError("automation deletion manifest is invalid")
    runs = [dict(row) for row in raw_runs]
    session_ids = sorted(
        {str(value) for value in deletion.get("owned_session_ids") or [] if str(value)}
    )
    manifested_session_ids = sorted(
        {str(row["session_id"]) for row in runs if row.get("session_id")}
    )
    if session_ids != manifested_session_ids:
        raise ConflictError("automation deletion manifest is invalid")
    context = {"automation": dict(automation), "runs": runs}
    if session_ids:
        if _cleanup_handler is None:
            raise ConflictError("automation dependency cleanup failed")
        cleaned = _cleanup_handler(context, phase="delete")
        if not isinstance(cleaned, dict) or cleaned.get("ok") is not True:
            raise ConflictError("automation dependency cleanup failed")

    runtime_store = AutomationRuntimeStore()
    runs_store = AutomationRunStore()
    runtime_store.delete_for_automation(automation_id)
    if runtime_store.get_for_automation(automation_id) is not None:
        raise ConflictError("automation runtime cleanup failed")
    runs_store.delete_for_automation(automation_id)
    if runs_store.for_automation(automation_id, limit=1, offset=0):
        raise ConflictError("automation run cleanup failed")
    if not store.delete(automation_id):
        raise ConflictError(f"automation {automation_id!r} changed during delete")
    return {"ok": True, "id": automation_id}


def create_system_automation(
    spec: dict,
    *,
    system_key: str,
    reconcile_existing: bool = False,
) -> tuple[dict, bool]:
    """Idempotently seed one Lucius Automation under a stable system identity."""
    key = str(system_key or "").strip()
    if not key:
        raise ValidationError("system Automation key is required")
    for row in AutomationStore().all_persisted():
        if (row.get("provenance") or {}).get("system_key") == key:
            if row.get("status") == DELETING_STATUS:
                return row, False
            if reconcile_existing:
                seeded = dict(spec)
                seeded["execution"] = {"kind": "lucius"}
                seeded["provenance"] = {
                    **dict(seeded.get("provenance") or {}),
                    "source": "system",
                    "system_key": key,
                }
                normalized = validate_automation({**row, **seeded}, now=now_utc())
                patch = {
                    field: normalized[field]
                    for field in _EDITABLE_FIELDS.intersection(seeded)
                    if normalized[field] != row.get(field)
                }
                if patch:
                    return (
                        apply_automation_change(
                            {"op": "update", "id": row["id"], "changes": patch}
                        ),
                        True,
                    )
            return row, False
    seeded = dict(spec)
    seeded["execution"] = {"kind": "lucius"}
    seeded["provenance"] = {
        **dict(seeded.get("provenance") or {}),
        "source": "system",
        "system_key": key,
    }
    return apply_automation_change({"op": "create", "automation": seeded}), True


def seed_vest_reminder(*, time: str = "09:00") -> bool:
    return _seed_best_effort(
        {
            "name": "Vest calendar reminder",
            "description": "Checks for equity vesting dates and sends reminders.",
            "instructions": (
                "Call get_upcoming_vests without a window limit. Keep only unvested tranches "
                "whose vest date is today or earlier. If none exist, take no action. Otherwise "
                "call telegram_send_message or webhook_send_message (whichever is available) "
                "once with each vest date, symbol and unit count, clearly marking past dates "
                "overdue and directing me to record the vest in Equity."
            ),
            "trigger": {"kind": "schedule", "type": "daily", "time": time},
            "execution": {"kind": "lucius"},
            "effort": "low",
            "permission_mode": "auto",
            "always_allowed": [_notify_permission("Send due-vest reminders.")],
            "provenance": {"source": "system", "system_key": "vest_reminder"},
        },
        system_key="vest_reminder",
    )


def seed_coupon_reminder(*, time: str = "09:00") -> bool:
    return _seed_best_effort(
        {
            "name": "Bond coupon reminder",
            "description": "Checks for bond coupon dates and sends reminders.",
            "instructions": (
                "Call get_fixed_income and inspect every holding's next coupon. If no coupon "
                "falls from today through seven days from today, take no action. Otherwise call "
                "telegram_send_message or webhook_send_message (whichever is available) once "
                "with each bond name, coupon date and amount per unit when available."
            ),
            "trigger": {"kind": "schedule", "type": "daily", "time": time},
            "execution": {"kind": "lucius"},
            "effort": "low",
            "permission_mode": "auto",
            "always_allowed": [_notify_permission("Send upcoming bond-coupon reminders.")],
            "provenance": {"source": "system", "system_key": "coupon_reminder"},
        },
        system_key="coupon_reminder",
    )


def _notify_permission(description: str) -> dict:
    return {
        "src": "Open Net Worth",
        "desc": description,
        "capability": "notify",
        "tools": ["telegram_send_message", "webhook_send_message"],
    }


def _categorize_permission(description: str) -> dict:
    return {
        "src": "Open Net Worth",
        "desc": description,
        "capability": "categorize",
        "tools": ["update_transactions"],
    }


def _seed_best_effort(
    spec: dict,
    *,
    system_key: str,
    reconcile_existing: bool = False,
) -> bool:
    try:
        _, created = create_system_automation(
            spec,
            system_key=system_key,
            reconcile_existing=reconcile_existing,
        )
        return created
    except Exception:
        return False


# Kept on the mutation service surface for thin routes/tools; implementation lives
# in ``runs`` so configuration validation and execution lifecycle remain focused.
from .runs import run_automation, run_due_automations  # noqa: E402,F401
