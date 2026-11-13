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
    from backend.services import ledger as ledger_service

    excluded = set(settings.get(key)["excluded_account_ids"])
    rows = {r.get("id"): r for r in accounts_service.all_account_rows()}
    booked = ledger_service.booked_from(key)
    from backend.services.bank_data_sync import access

    granted = access.by_account(p)
    out = []
    for a in accounts_service.linked_accounts_for_source(key):
        left, next_at = budget.reads_left(p, a.get("provider_account_id") or "")
        row = rows.get(a.get("linked_account_id")) or {}
        bank = granted.get(str(a.get("provider_account_id") or "")) or {}
        out.append(
            {
                "account_id": a.get("linked_account_id"),
                # Your account's own name; the bank's name for it is the detail.
                "name": row.get("name") or a.get("name") or "",
                # The book account's own last digits (filled from the bank's at link time).
                "last_four": row.get("last_four") or "",
                # The bank it's at (filled in when it was linked): one connector can hold several.
                "bank": row.get("institution") or "",
                "bank_name": a.get("name") or "",
                "mask": a.get("mask") or "",
                "sync": a.get("linked_account_id") not in excluded,
                "reads_left": left,
                "next_read_at": _iso(next_at),
                # History brought in: whether anything from this bank is booked here yet.
                "imported": a.get("linked_account_id") in booked,
                # What came from this bank, counted in the book: the import step's line.
                "booked": (booked.get(a.get("linked_account_id") or "") or {}).get("count", 0),
                "booked_from": (booked.get(a.get("linked_account_id") or "") or {}).get("first"),
                "booked_to": (booked.get(a.get("linked_account_id") or "") or {}).get("last"),
                # When the bank's access for this app ends (a European bank grants it for a set
                # time), and whether it has: the page warns before the sync quietly stops.
                "access_ends_at": bank.get("ends_at"),
                "access_ended": bool(bank.get("ended")),
                "access_renew_soon": bool(bank.get("renew_soon")),
            }
        )
    return out


def _stage(mode: str, accounts: list[dict], set_up: bool = False) -> str:
    """Where setup stands — the steps on the bank's Data sync page: ``accounts`` (nothing linked
    or ticked yet) → ``history`` (bring the history in) → ``sync`` (turn the sync on) → ``done``
    (the sync is on, a Lucius Automation syncs the bank, or the sync was turned on once — turning
    it off again leaves a set-up sync, as a budget app's does). Turning the sync on before
    importing is fine: its first run brings the history in."""
    if mode in ("data", "lucius") or set_up:
        return "done"
    ticked = [a for a in accounts if a["sync"]]
    if not ticked:
        return "accounts"
    return "sync" if any(a["imported"] for a in ticked) else "history"


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
        "connected": _set_up(p) or mode == "data",
        "lucius_followup": s["lucius_followup"],
        "interval_hours": settings.interval_hours(p),
        "intervals": settings.interval_choices(p),
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


def _set_up(p) -> bool:
    try:
        return bool(p.store().is_configured())
    except Exception:  # noqa: BLE001 — a broken config is not "set up"
        return False


def overview() -> list[dict]:
    """Every bank connector that can sync without AI — the Automations → Data sync list. One
    whose connector isn't set up yet (``connected`` false) is offered under "Add a data sync",
    as the budget apps are, so the sync is findable before its connector exists."""
    _require_on()
    out = []
    for p in settings.capable_providers():
        s = _summary(p)
        # A connected bank's accounts ride along, so the Accounts list can mark each synced one.
        accounts = _accounts(p, p.key) if s["connected"] else []
        # Its setup stage too: the list shows a card only for a sync that's set up, as for the
        # budget apps; one connected but never set up is still offered under "Add a data sync".
        stage = _stage(s["mode"], accounts, settings.get(p.key)["set_up"])
        out.append({**s, "accounts": accounts, "stage": stage})
    return out


def status(key: str) -> dict:
    _require_on()
    p = settings.provider(key)
    summary, accounts = _summary(p), _accounts(p, key)
    return {
        **summary,
        "accounts": accounts,
        "history_days": p.history_days or p.lookback_days,
        "stage": _stage(summary["mode"], accounts, settings.get(key)["set_up"]),
    }


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
        settings.save(key, mode="data", set_up=True)
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


def set_interval(key: str, hours: int) -> dict:
    """How often the sync runs. A bank with a daily read limit offers only what it allows."""
    _require_on()
    p = settings.provider(key)
    if hours not in settings.interval_choices(p):
        choices = ", ".join(str(h) for h in settings.interval_choices(p))
        raise ValidationError(f"{p.label} can sync every {choices} hours.")
    settings.save(key, interval_hours=hours)
    budget.reschedule(p)
    return status(key)


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


def _start(key: str, *, needs_data: bool) -> dict:
    """Start one run now, in the background — refused (409, with the reason) unless the
    connector is set up and at least one ticked account has a read left today."""
    p = settings.provider(key)
    if needs_data and not settings.is_data(key):
        raise ConflictError(f"The {p.label} sync is off.")
    if not needs_data and settings.mode(key) == "lucius":
        raise ConflictError(
            f"A Lucius Automation syncs {p.label}. Pause it first, so its rows aren't booked twice."
        )
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


def run_now(key: str) -> dict:
    """ "Sync now" — only while Sync without AI is on."""
    _require_on()
    return _start(key, needs_data=True)


def import_history(key: str) -> dict:
    """Setup step 2, "Bring in your history": one run now, before the sync is turned on. A
    ticked account never imported gets everything the bank serves and its start moves back
    (``booker.align_first_imports``); one imported before just catches up. Safe to run again.
    Refused while a Lucius Automation syncs the bank."""
    _require_on()
    return _start(key, needs_data=False)


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


def accept_review(item_id: str, seen: Optional[str] = None) -> dict:
    """``seen`` is the item's ``updated_at`` as shown: a newer version is refused (409)."""
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.accept(item_id, seen)


def dismiss_review(item_id: str, seen: Optional[str] = None) -> dict:
    """``seen`` is the item's ``updated_at`` as shown: a newer version is refused (409)."""
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.dismiss(item_id, seen)


def book_review(item_id: str, seen: Optional[str] = None) -> dict:
    """``seen`` is the item's ``updated_at`` as shown: a newer version is refused (409)."""
    _require_on()
    from backend.services.bank_data_sync import review_actions

    return review_actions.book_separately(item_id, seen)


async def run_followups() -> int:
    """The scheduler tick's async half: the queued "Let Lucius categorise what's left" turns,
    on the main event loop. Nothing while the flag is off or no switch is on."""
    if not is_on():
        return 0
    from backend.services.bank_data_sync import followup

    return await followup.run_pending()
