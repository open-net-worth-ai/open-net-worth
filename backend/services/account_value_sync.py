"""A linked Brokerage total account follows the total its connector reports — without AI.

ADR 2026-11-10-an-investment-account-is-valued-by-its-brokerage-total-or-its-live-holdings
(amends 2026-09-05-connectors-are-data-sources-lucius-books for BALANCES only: no connector
transaction is booked here, and no model is called).

Each run reads every balance-reporting connector's account list once (Plaid: the free
``/accounts/get``) and, per linked investment/retirement account:

* Brokerage total — when the reported total differs from the last one applied, books ONE
  adjustment so holdings + ledger balance == the reported total. An unchanged total writes
  nothing, so tracked holdings move with the market between readings.
* Live holdings — records the reading for comparison and never writes to the ledger.

A missing balance, another currency, or a failed read writes nothing to the ledger. A reported
0, or a drop of more than a quarter since the last applied total, is HELD until the user says
whether it is real (``confirm_held``). Every write runs under the audit surface
``account_value_sync``.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.errors import NotFoundError, ValidationError
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state
from backend.services import account_value as av

logger = logging.getLogger(__name__)

INTERVAL = timedelta(hours=6)
HOLD_DROP = 0.25  # a drop of more than a quarter is held
HOLD_JUMP = 4.0  # so is a total more than 4× the reference
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_RUN_LOCK = threading.Lock()


class _RunState(NamespacedSettingsStore):
    """When the sync last ran — runtime state, checkpoint-excluded: a rewind must not re-run it."""

    def __init__(self, engine: Optional[object] = None):
        super().__init__(av.SURFACE, table=runtime_state, engine=engine)


def eligible_accounts() -> list[dict]:
    """Live investment/retirement accounts linked to a connector that reports balances."""
    from backend.services import ledger as ledger_service

    return [
        a
        for a in ledger_service.accounts.query_accounts()
        if av.applies(a) and av.balance_link(a) is not None
    ]


def _same_link(account: dict) -> dict:
    """The stored reading, if it belongs to the account's CURRENT link — else ``{}``."""
    link = av.balance_link(account) or {}
    stored = account.get("value_sync") or {}
    if stored.get("source") == link.get("source") and stored.get("ref") == link.get("ref"):
        return stored
    return {}


def run_if_due(now: Optional[datetime] = None) -> bool:
    """The scheduler tick: a full run every ``INTERVAL``, and within a minute for an account that
    has never been read on its current link. Cheap when not due — one small state read."""
    if not av.enabled():
        return False
    accounts = eligible_accounts()
    if not accounts:
        return False
    now = now or datetime.now(timezone.utc)
    state = _RunState()
    last = _parse(state.get().get("last_run_at"))
    due = last is None or now - last >= INTERVAL
    unread = [a for a in accounts if not _same_link(a)]
    if not due and not unread:
        return False
    run(accounts if due else unread)
    if due:
        state.save({"last_run_at": now.isoformat(timespec="seconds")})
    return True


def run(accounts: Optional[list[dict]] = None) -> dict:
    """Read and apply. Returns ``{changed: [account ids], checked: n}``. One run at a time: a
    second caller while one is in progress skips (``skipped: True``) rather than booking the same
    difference twice (UAT ROB-03 — ``max_instances=1`` guards only the scheduler's own tick)."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {"changed": [], "checked": 0, "skipped": True}
    try:
        return _run(accounts)
    finally:
        _RUN_LOCK.release()


def _run(accounts: Optional[list[dict]]) -> dict:
    from backend.core import audit_context, data_events

    accounts = eligible_accounts() if accounts is None else accounts
    by_source: dict[str, list[dict]] = {}
    for a in accounts:
        by_source.setdefault((av.balance_link(a) or {}).get("source", ""), []).append(a)
    changed: list[str] = []
    token = audit_context.set_audit_scope("system", av.SURFACE)
    try:
        for source, accts in by_source.items():
            reading = _read(source)
            for a in accts:
                try:
                    if _apply(a, reading):
                        changed.append(a["id"])
                except Exception:  # noqa: BLE001 — one account never sinks the rest
                    logger.exception("account_value_sync: %s failed", a.get("id"))
                    _record_failure(a)
    finally:
        audit_context.clear_audit_scope(token)
    if changed:
        data_events.publish(["*"])
    return {"changed": changed, "checked": len(accounts)}


def _read(source: str) -> dict:
    """``{status, accounts: {ref: account}, complete}`` — status ``ok`` | ``paused`` |
    ``not_configured`` | ``reconnect`` | ``error`` (with ``error_code``)."""
    from backend.services.aggregation.providers import PROVIDERS

    provider = PROVIDERS.get(source)
    if provider is None or provider.disabled:
        return {"status": "not_configured"}
    store = provider.store()
    if not store.is_configured():
        return {"status": "not_configured"}
    if not store.get().get("enabled"):
        return {"status": "paused"}
    try:
        got = provider.account_balances()
    except Exception as exc:  # noqa: BLE001 — classified, never propagated
        code = provider.classify_error(exc)
        logger.warning("account_value_sync: %s read failed (%s)", source, code)
        status = "reconnect" if code == "reconnect_needed" else "error"
        return {"status": status, "error_code": code}
    by_ref = {
        str(a.get("provider_account_id")): a
        for a in got.get("accounts") or []
        if a.get("provider_account_id")
    }
    return {"status": "ok", "accounts": by_ref, "complete": bool(got.get("complete", True))}


def _apply(account: dict, reading: dict) -> bool:
    """Apply one reading to one account. Returns True when anything was written."""
    link = av.balance_link(account) or {}
    prev = _same_link(account)
    base = {"source": link.get("source"), "ref": link.get("ref")}
    status = reading.get("status")
    if status in ("paused", "not_configured"):
        return False  # the account shows its link's own state; nothing new to record
    if status != "ok":
        return _record(
            account,
            prev,
            {**prev, **base, "status": status, "error_code": reading.get("error_code")},
        )
    found = (reading.get("accounts") or {}).get(str(link.get("ref")))
    if found is None:
        miss = "error" if not reading.get("complete", True) else "not_found"
        return _record(account, prev, {**prev, **base, "status": miss})
    total, currency = _reported(found)
    if total is None:
        return _record(account, prev, {**prev, **base, "status": "no_balance"})
    own = (account.get("currency") or "").upper()
    if not currency or currency != own:
        # Fails CLOSED: a missing currency, a non-ISO one (Plaid's "DOGE", SimpleFIN's URL for a
        # points currency) or another ISO code is never booked as the account's own (security
        # review 2026-11-10). Only a clean ISO code is echoed back for the account to show.
        shown = currency if currency and _CURRENCY.match(currency) else None
        return _record(
            account,
            prev,
            {**prev, **base, "status": "currency_mismatch", "reported_currency": shown},
        )
    if av.ensure_source(account) == av.HOLDINGS:
        return _record_comparison(account, prev, base, total)
    return _apply_total(account, prev, base, total)


def _reported(found: dict) -> tuple[Optional[float], str]:
    """The reported total (None when missing, unparseable or not finite — NaN/Infinity survive
    ``float()``) and its currency, raw and upper-cased ("" when absent)."""
    raw, currency = found.get("balance"), str(found.get("currency") or "").strip().upper()
    if found.get("currency_inferred"):
        currency = ""  # the provider sent none; its module filled in the base (Yodlee)
    try:
        total = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        total = None
    if total is None or not math.isfinite(total):
        return None, currency[:64]
    return round(total, 2), currency[:64]


def _record_comparison(account: dict, prev: dict, base: dict, total: float) -> bool:
    if prev.get("status") == "compare" and prev.get("reported_total") == total:
        return False
    return _record(
        account,
        prev,
        {**base, "status": "compare", "reported_total": total, "changed_at": av.now_iso()},
    )


def _apply_total(account: dict, prev: dict, base: dict, total: float) -> bool:
    last = prev.get("total")
    if last == total:
        # Unchanged — nothing to book (re-booking would only chase tracked holdings' price
        # moves). After a failed read, just say it's current again.
        recovered = {**prev, **base, "status": "fresh"}
        recovered.pop("error_code", None)
        recovered.pop("held_total", None)  # the figure we held came back to the applied one
        # ...and so did the one the user kept their value over: if it is reported AGAIN later,
        # that is a new event to ask about, never one to ignore silently (UAT SB-04).
        recovered.pop("dismissed_total", None)
        return _record(account, prev, recovered)
    if total in (prev.get("dismissed_total"), prev.get("held_total")):
        return False  # the user already kept the old value over this figure, or is deciding
    current = _current_value(account)
    if current is None:
        return _record(account, prev, {**prev, **base, "status": "error", "error_code": "no_rate"})
    if _suspicious(total, last, current):
        held = {
            **prev,
            **base,
            "status": "held_back",
            "held_total": total,
            "checked_at": av.now_iso(),
        }
        return _record(account, prev, held)
    if _book_total(account, total) is False:
        return _record(account, prev, {**prev, **base, "status": "error", "error_code": "no_rate"})
    fresh = {**base, "status": "fresh", "total": total, "changed_at": av.now_iso()}
    return _record(account, prev, fresh, force=True)


def _suspicious(total: float, last: Optional[float], current: float) -> bool:
    """Held for the user, never booked on its own:

    * a reported 0 that would wipe a value the account has (a 0 on an account already worth 0
      changes nothing to ask about — QA 2026-11-10, "Keep $0.00 / Use $0.00");
    * a drop of more than a quarter, or a jump past ``HOLD_JUMP``×, from the last applied total
      — or, for the FIRST reading on a link, from the account's current value (security review
      2026-11-10: linking the wrong provider account must not silently rewrite a typed total).
      An account worth nothing yet (the reported $0 case) takes its first total without asking.
    """
    if total == 0:
        return round(current, 2) != 0
    ref = last if last is not None else current
    if total < 0:
        # A brokerage worth less than nothing is margin debt — real only if it was already so
        # (UAT SB-09: a first −500 on an empty account was booked without asking).
        return not (last is not None and last < 0)
    if not ref or ref <= 0:
        return False
    return total < ref * (1 - HOLD_DROP) or total > ref * HOLD_JUMP


def _current_value(account: dict) -> Optional[float]:
    held = av._value_or_none(account)
    return None if held is None else held + av.ledger_balance(account["id"])


def _book_total(account: dict, total: float) -> Optional[dict] | bool:
    """Book the adjustment that makes the account worth ``total``; ``False`` when its holdings
    can't be valued in its currency (no rate) — nothing is booked then."""
    from backend.services.accounts import provider_label

    held = av._value_or_none(account)
    if held is None:
        return False
    target = round(total - held, 2)
    diff = round(target - av.ledger_balance(account["id"]), 2)
    label = provider_label((av.balance_link(account) or {}).get("source")) or "the connector"
    return av.book_adjustment(account, diff, f"Total from {label}")


def _record_failure(account: dict) -> None:
    """A reading that raised is recorded as an error, so the account stops counting as unread
    and the next try waits for the 6-hour run instead of the next minute (security review
    2026-11-10: a stuck account otherwise cost a provider call every tick)."""
    link = av.balance_link(account) or {}
    prev = _same_link(account)
    base = {"source": link.get("source"), "ref": link.get("ref")}
    try:
        _record(account, prev, {**prev, **base, "status": "error", "error_code": "apply_failed"})
    except Exception:  # noqa: BLE001 — never let recording a failure fail the run
        logger.exception("account_value_sync: could not record %s's failure", account.get("id"))


def _record(account: dict, prev: dict, new: dict, *, force: bool = False) -> bool:
    new = {k: v for k, v in new.items() if v is not None}
    if not force and new == prev:
        return False
    from backend.services import ledger as ledger_service

    ledger_service.accounts.update_by_id(account["id"], {"value_sync": new})
    return True


def confirm_held(account_id: str, accept: bool) -> dict:
    """The user's answer to a held-back total: ``accept`` books it; otherwise the account keeps
    its value and that exact figure is never asked about again."""
    from backend.core import audit_context, data_events
    from backend.services import ledger as ledger_service

    account = ledger_service.accounts.find_by_id(account_id)
    if account is None:
        raise NotFoundError("Account not found")
    if not av.enabled() or not av.applies(account) or av.source_of(account) != av.TOTAL:
        raise ValidationError("Only a Brokerage total account takes a connector's total.")
    prev = _same_link(account)
    if prev.get("status") != "held_back" or prev.get("held_total") is None:
        raise ValidationError("This account has no held-back total to confirm.")
    held = prev["held_total"]
    base = {"source": prev.get("source"), "ref": prev.get("ref")}
    token = audit_context.set_audit_scope("user", av.SURFACE, note="held-back total")
    try:
        if accept:
            if _book_total(account, held) is False:
                raise ValidationError(
                    "This account's holdings can't be valued in its currency yet (no exchange "
                    "rate), so the total can't be applied. Try again once rates refresh."
                )
            new = {**base, "status": "fresh", "total": held, "changed_at": av.now_iso()}
        else:
            # Back to the last applied total, or — when there never was one — the value the
            # account already had ("kept"), never a "synced" claim with nothing behind it.
            kept = "fresh" if prev.get("total") is not None else "kept"
            new = {**prev, "status": kept, "dismissed_total": held, "held_total": None}
        _record(account, prev, new, force=True)
    finally:
        audit_context.clear_audit_scope(token)
    data_events.publish(["*"])
    fresh = ledger_service.accounts.find_by_id(account_id) or account
    return ledger_service.account_with_balance(fresh)


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
