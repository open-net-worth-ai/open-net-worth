"""A holding carved out of a Brokerage total before it has a price is carved at its COST — the
only figure there is — and trued up to its market value once the first price lands.

ADR 2026-11-10-an-investment-account-is-valued-by-its-brokerage-total-or-its-live-holdings
(amendment: unpriced holdings).

Without this, 5 ACME bought at 400 and added to an 8,000 total before ACME was priced took
2,000 out of the total; once priced at 604 the account read 8,000 − 2,000 + 3,020 = 9,020. Now
the carve-out remembers what it took for each unpriced holding (``note``) and the scheduler's
tick books the difference once the price is there (``settle``), so the account reads 8,000
again. Positions only — nothing here touches a holding that already had a price.

State lives in ``runtime_state`` (checkpoint-excluded): ``{"pending": {position_id:
{"account_id", "value"}}}``, where ``value`` is the cost figure the carve-out used, in the
account's currency.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

logger = logging.getLogger(__name__)

NAMESPACE = "account_value_cost_carves"


class _Pending(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__(NAMESPACE, table=runtime_state, engine=engine)


def _unpriced(row: dict, currency: str) -> bool:
    from backend.services import ledger as ledger_service

    if row.get("price"):
        return False
    try:
        qty = float(row.get("quantity") or 0)
    except (TypeError, ValueError):
        return False
    return qty > 0 and not ledger_service.row_base_value(row, currency)


def note(account: dict, rows: list[dict]) -> None:
    """After a carve-out on ``account``: remember the figure each of its unpriced holdings was
    carved at (``rows``: the hydrated vested rows the carve-out valued it with)."""
    from backend.services import account_value as av
    from backend.services import ledger as ledger_service

    currency = av.account_currency(account)
    found = {
        r["position_id"]: av._row_value(r, currency, ledger_service.row_base_value)
        for r in rows
        if r.get("account_id") == account.get("id")
        and r.get("position_id")
        and _unpriced(r, currency)
    }
    if not found:
        return
    store = _Pending()
    pending = dict(store.get().get("pending") or {})
    for pid, value in found.items():
        pending[pid] = {"account_id": account["id"], "value": round(value, 2)}
    store.save({"pending": pending})


def settle() -> list[str]:
    """Book the difference for every remembered holding that has a price now. Returns the ids
    of the accounts written. A no-op while the flag is off; cheap when nothing is pending."""
    from backend.services import account_value as av

    if not av.enabled():
        return []
    store = _Pending()
    pending = dict(store.get().get("pending") or {})
    if not pending:
        return []
    rows = {r.get("position_id"): r for r in av._vested_rows()}
    written: list[str] = []
    keep: dict[str, dict] = {}
    for pid, entry in pending.items():
        outcome = _settle_one(entry, rows.get(pid))
        if outcome == "wait":
            keep[pid] = entry
        elif outcome == "booked":
            written.append(entry["account_id"])
    store.save({"pending": keep})
    if written:
        from backend.core import data_events

        data_events.publish(["*"])
    return written


def _settle_one(entry: dict, row: Optional[dict]) -> str:
    """``wait`` (still unpriced), ``booked``, or ``dropped`` (gone, moved, or no longer a
    Brokerage total account — nothing to true up)."""
    from backend.core import audit_context
    from backend.services import account_value as av
    from backend.services import ledger as ledger_service

    account = ledger_service.accounts.find_by_id(entry.get("account_id") or "")
    if row is None or account is None or row.get("account_id") != account.get("id"):
        return "dropped"
    if not av.applies(account) or av.source_of(account) != av.TOTAL:
        return "dropped"
    currency = av.account_currency(account)
    if _unpriced(row, currency):
        return "wait"
    market = round(ledger_service.row_base_value(row, currency), 2)
    diff = round(market - float(entry.get("value") or 0), 2)
    if diff == 0:
        return "dropped"
    token = audit_context.set_audit_scope("system", av.SURFACE)
    try:
        av.book_adjustment(account, -diff, "Holding priced — total kept")
    except Exception:  # noqa: BLE001 — retried next tick, never lost
        logger.exception("account_value_cost: true-up failed for %s", account.get("id"))
        return "wait"
    finally:
        audit_context.clear_audit_scope(token)
    return "booked"
