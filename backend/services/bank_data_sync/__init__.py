"""Bank data sync — book a bank connector's rows without AI (ADR
``2026-11-13-a-bank-connector-can-book-its-rows-without-ai``).

THE flag seam for ``bank_data_sync``: every entry point here reads the flag once. Off, the
routes answer 404, the scheduler tick does nothing, and no connector is in data mode — so the
Automations-side refusal never fires and today's Lucius-only behaviour runs unchanged.

Modules: ``settings`` (mode, opt-outs, the one-mode rule), ``budget`` (read budget + schedule),
``booker`` (rows → one ledger write), ``reviews`` (the review list), ``runner`` (one run),
``followup`` (the optional Lucius categorisation)."""

from __future__ import annotations

import logging
from typing import Optional

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import import_runs
from backend.services.bank_data_sync import budget, reviews, runner, settings

logger = logging.getLogger(__name__)

FLAG = "bank_data_sync"


def is_on() -> bool:
    from backend.services.feature_flags import enabled

    return enabled(FLAG)


def _require_on() -> None:
    if not is_on():
        raise NotFoundError("Bank sync without AI is off (Settings → Developer → Experimental).")


def _iso(dt) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt is not None else None


def _accounts(p, key: str) -> list[dict]:
    from backend.services import accounts as accounts_service

    excluded = set(settings.get(key)["excluded_account_ids"])
    out = []
    for a in accounts_service.linked_accounts_for_source(key):
        left, next_at = budget.reads_left(p, a.get("provider_account_id") or "")
        out.append(
            {
                "account_id": a.get("linked_account_id"),
                "name": a.get("name") or "",
                "mask": a.get("mask") or "",
                "sync": a.get("linked_account_id") not in excluded,
                "reads_left": left,
                "next_read_at": _iso(next_at),
            }
        )
    return out


def _connector_ready(p) -> bool:
    try:
        store = p.store()
        return bool(store.is_configured() and store.get().get("enabled"))
    except Exception:  # noqa: BLE001 — a broken config reads as not ready, never a 500
        return False


def _summary(p) -> dict:
    s, st = settings.get(p.key), budget.state(p.key)
    mode = settings.mode(p.key)
    last = import_runs.latest(p.key, ("sync",))
    return {
        "provider": p.key,
        "label": p.label,
        "mode": mode,
        "ready": _connector_ready(p),
        "lucius_followup": s["lucius_followup"],
        "interval_hours": p.sync_interval_hours,
        "daily_reads": p.daily_reads,
        "last_run": import_runs.summary(last) if last else None,
        "next_run_at": st.get("next_due_at") if mode == "data" else None,
        "last_status": st.get("last_status"),
        "last_error": st.get("last_error"),
        "running": import_runs.has_running(p.key),
        "to_review": reviews.open_count(p.key),
        "lucius_automations": [
            {"id": a["id"], "name": a.get("name"), "status": a.get("status")}
            for a in settings.lucius_syncs(p.key, ("active", "paused"))
        ],
    }


def overview() -> list[dict]:
    """Every data-sync-capable connector — the Automations → Data sync list."""
    _require_on()
    return [_summary(p) for p in settings.capable_providers()]


def status(key: str) -> dict:
    _require_on()
    p = settings.provider(key)
    return {**_summary(p), "accounts": _accounts(p, key)}


def set_mode(key: str, mode: str) -> dict:
    """Switch a connector's sync mode; says what it did to Lucius Automations."""
    _require_on()
    p = settings.provider(key)
    if mode not in settings.MODES:
        raise ValidationError("mode must be off, data or lucius")
    paused: list[str] = []
    resumed: Optional[str] = None
    if mode == "data":
        paused = settings.pause_lucius_syncs(key)
        settings.save(key, mode="data")
        budget.schedule_from_now(p)
    else:
        settings.save(key, mode="off")
        if mode == "lucius":
            resumed = settings.resume_lucius_sync(key)
        else:
            paused = settings.pause_lucius_syncs(key)
    needs_template = mode == "lucius" and resumed is None and not settings.lucius_syncs(key)
    return {
        **status(key),
        "paused_automations": paused,
        "resumed_automation": resumed,
        "needs_template": needs_template,
    }


def set_account_sync(key: str, account_id: str, sync: bool) -> dict:
    _require_on()
    p = settings.provider(key)
    linked = {a["account_id"] for a in _accounts(p, key)}
    if account_id not in linked:
        raise NotFoundError("That account isn't linked to this connector.")
    excluded = set(settings.get(key)["excluded_account_ids"])
    excluded.discard(account_id) if sync else excluded.add(account_id)
    settings.save(key, excluded_account_ids=sorted(excluded))
    return status(key)


def set_followup(key: str, enabled: bool) -> dict:
    _require_on()
    settings.provider(key)
    settings.save(key, lucius_followup=bool(enabled))
    return status(key)


def _when(dt) -> str:
    from backend.core.user_time import user_zone

    if dt is None:
        return "a later time"
    zone = user_zone()
    return (dt.astimezone(zone) if zone else dt).strftime("%H:%M")


def run_now(key: str) -> dict:
    """ "Sync now": refused (409, with the reason) unless data mode is on, the connector is set
    up, and at least one ticked account has a read left today. Runs in the background."""
    _require_on()
    p = settings.provider(key)
    if not settings.is_data(key):
        raise ConflictError(f"{p.label} isn't set to Sync without AI.")
    runner.refuse_unless_ready(p)
    accounts = runner.opted_accounts(key)
    if not accounts:
        raise ConflictError("No account is ticked to sync.")
    ready, spent = runner.readable(p, accounts)
    if not ready:
        at = runner.next_read_at(p, spent)
        raise ConflictError(
            f"{p.label} banks let the app read each account {p.daily_reads} times a day, and "
            f"those reads are used. The next one is possible at about {_when(at)}."
        )
    run = runner.start(p, "manual", ready)
    runner.finish_in_background(p, run, ready)
    return {"run_id": run["id"], "accounts": len(ready), "skipped_for_limit": len(spent)}


def run_if_due() -> bool:
    """The scheduler tick. Cheap when nothing is due; nothing at all with the flag off."""
    if not is_on():
        return False
    ran = False
    for p in settings.capable_providers():
        try:
            ran = _tick(p) or ran
        except Exception:  # noqa: BLE001 — one connector never stops the others
            logger.exception("bank_data_sync: %s tick failed", p.key)
    return ran


def _tick(p) -> bool:
    if not settings.is_data(p.key) or not budget.is_due(p) or import_runs.has_running(p.key):
        return False
    if not _connector_ready(p):
        budget.update(p.key, next_due_at=_iso(budget.now() + budget.interval(p)))
        return False
    ready, spent = runner.readable(p, runner.opted_accounts(p.key))
    if not ready:
        at = runner.next_read_at(p, spent) or budget.now() + budget.interval(p)
        budget.update(p.key, next_due_at=_iso(at))
        return False
    runner.run_now(p, "scheduled", ready)
    return True


def list_runs(key: str) -> list[dict]:
    _require_on()
    settings.provider(key)
    return [r for r in import_runs.list_runs(key) if r.get("kind") == "sync"]


def refuse_automation_beside_data_sync(automation: dict) -> None:
    """Called by the Automations choke before an Automation becomes active. No-op when off."""
    if is_on():
        settings.refuse_beside_data(automation)


# ── the review list ─────────────────────────────────────────────────────────────────


def list_reviews(provider: Optional[str] = None) -> list[dict]:
    _require_on()
    if provider:
        settings.provider(provider)
    labels = {p.key: p.label for p in settings.capable_providers()}
    return [
        {**r, "provider_label": labels.get(r.get("provider"), "")}
        for r in reviews.open_items(provider)
    ]


def accept_review(item_id: str) -> dict:
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.accept(item_id)


def dismiss_review(item_id: str) -> dict:
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.dismiss(item_id)


def book_review(item_id: str) -> dict:
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.book_separately(item_id)


async def run_followups() -> int:
    """The scheduler tick's async half: the queued "Let Lucius categorise what's left" turns,
    on the main event loop. Nothing while the flag is off or no switch is on."""
    if not is_on():
        return 0
    from backend.services.bank_data_sync import followup

    return await followup.run_pending()
