"""Correcting or deleting a recorded dividend from the holding's Dividends table.

A dividend's income event and the cash row it booked change together, in ONE unit of work —
the same rules a lot or a sale correction follows (services/lots._edit_cash_row):

- The row is edited in place, so it keeps its category, cleared and reconciled state, source
  and refs. Its description follows the figures only while it is still the app's own words.
- A row on the same account in another currency moves at the rate it was first booked at.
- A row the user turned into a transfer is a real movement: it is left alone.
- No account chosen ("Don't add") lets go of the row; choosing one for a dividend with no
  row books one.

A reinvested (DRP) dividend has no cash row; its shares are a lot, corrected in Lots.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.engine import Connection

from backend.core.errors import NotFoundError, ValidationError
from backend.core.fx import base_currency, to_base
from backend.db.engine import locked_unit_of_work
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.tables import transactions as txns_tbl
from backend.db.tables import txn_audit as txn_audit_tbl
from backend.services import transaction_links

_KINDS = ("dividend", "interest", "distribution")
_FIELDS = (
    "paid_at",
    "income_kind",
    "gross_amount",
    "tax_withheld",
    "currency",
    "account_id",
    "note",
    "quantity",
    "amount_per_unit",
)


# A blank in one of these is a mistake, never "clear it": a null gross read as 0 deleted the cash.
_REQUIRED = ("paid_at", "income_kind", "gross_amount", "tax_withheld", "currency")


def amend_income(change: dict) -> dict:
    """Correct one income event and the cash row it booked. RAISES before any write."""
    store = IncomeEventStore()
    patch = {k: change[k] for k in _FIELDS if k in change}
    blank = [k for k in _REQUIRED if k in patch and patch[k] in (None, "")]
    if blank:
        raise ValidationError(f"{', '.join(blank)} can't be blank")
    first = _find(store, change)
    _validated({**first, **patch}, patch, drp=first.get("is_drp"))  # refuse before any write
    # PRE-transaction, like the lot choke: a payee minted inside the unit of work would be
    # written on its own connection, outside the locks (review, 2026-11-11).
    payee_id = _payee_for_new_row(first, patch)
    with locked_unit_of_work(txns_tbl, txn_audit_tbl, *transaction_links.LOCK_TABLES) as conn:
        ev = store.find_by_id(first["id"], conn=conn) or first  # an edit since the first read wins
        new = _validated({**ev, **patch}, patch, drp=ev.get("is_drp"))
        net = round(new["gross_amount"] - new["tax_withheld"], 2)
        updates = _event_updates(ev, new, net)
        txn_id = None
        if not ev.get("is_drp"):
            txn_id = _sync_cash(ev, new, net, patch, payee_id=payee_id, conn=conn)
        store.update_by_id(ev["id"], {**updates, "txn_id": txn_id}, conn=conn)
    base_amount = updates["base_amount"]
    return {
        "ok": True,
        "event_id": ev["id"],
        "position_id": ev.get("position_id"),
        "txn_id": txn_id,
        "base_amount": base_amount,
    }


def delete_income_and_cash(change: dict) -> dict:
    """Delete an income event and the cash row that is still its own (a transfer stays)."""
    store = IncomeEventStore()
    ev = _find(store, change)
    with locked_unit_of_work(txns_tbl, txn_audit_tbl, *transaction_links.LOCK_TABLES) as conn:
        row = _live_row(ev.get("txn_id"), conn=conn)
        removed = None
        if row and _own_cash(row):
            from backend.services import ledger as ledger_service

            ledger_service.bulk_delete_transactions([row["id"]], conn=conn, reject_identities=False)
            removed = row["id"]
        store.delete(ev["id"], conn=conn)
    return {"ok": True, "event_id": ev["id"], "removed_txn_id": removed}


def _event_updates(ev: dict, new: dict, net: float) -> dict:
    from backend.services import lots as lots_service

    base = base_currency().upper()
    base_amount = round(to_base(net, new["currency"], base, rates=lots_service._load_rates()), 2)
    return {
        **{k: new.get(k) for k in _FIELDS},
        # "Don't add" drops the cash, not which account the dividend was for.
        "account_id": new.get("account_id") or ev.get("account_id"),
        "base_amount": base_amount,
        "base_currency": base,
        "fx_rate": round(base_amount / net, 6) if net else ev.get("fx_rate"),
    }


def _payee_for_new_row(ev: dict, patch: dict) -> Optional[str]:
    """The payee a row booked by this correction will carry — only when one may be booked:
    an account is chosen and the dividend has no live cash row."""
    if ev.get("is_drp") or not patch.get("account_id") or _live_row(ev.get("txn_id")):
        return None
    from backend.services import payee_registry

    return payee_registry.resolve_payee_id(_description({**ev, **patch}))


def _find(store: IncomeEventStore, change: dict) -> dict:
    event_id = change.get("event_id") or change.get("id")
    ev = store.find_by_id(event_id) if event_id else None
    if not ev:
        raise NotFoundError("income event not found", event_id=event_id)
    return ev


def _validated(new: dict, patch: dict, *, drp: bool = False) -> dict:
    if drp and patch.get("account_id"):
        raise ValidationError(
            "a reinvested dividend has no cash — its shares are a lot; correct them in Lots"
        )
    try:
        when = date.fromisoformat(str(new.get("paid_at") or "")[:10])
    except ValueError as exc:
        raise ValidationError("paid_at must be a date, YYYY-MM-DD") from exc
    kind = new.get("income_kind") or "dividend"
    if kind not in _KINDS:
        raise ValidationError(f"income_kind must be one of: {', '.join(_KINDS)}")
    gross = round(float(new.get("gross_amount") or 0), 2)
    tax = round(float(new.get("tax_withheld") or 0), 2)
    if gross < 0 or tax < 0:
        raise ValidationError("amounts can't be negative")
    if tax > gross:
        raise ValidationError("tax withheld can't be more than the gross amount")
    currency = str(new.get("currency") or "").upper()
    if not currency:
        raise ValidationError("currency is required")
    account_id = new.get("account_id") or None
    if account_id and "account_id" in patch:
        from backend.services.accounts import require_account_id

        account_id = require_account_id(account_id)
    if "account_id" in patch and not patch.get("account_id"):
        account_id = None
    return {
        **new,
        "paid_at": when.isoformat(),
        "income_kind": kind,
        "gross_amount": gross,
        "tax_withheld": tax,
        "currency": currency,
        "account_id": account_id,
    }


def _live_row(txn_id: Optional[str], *, conn: Connection | None = None) -> Optional[dict]:
    if not txn_id:
        return None
    from backend.services import ledger as ledger_service

    row = ledger_service.transactions.find_by_id(txn_id, conn=conn)
    return None if transaction_links.is_gone(row) else row


def _own_cash(row: dict) -> bool:
    return row.get("kind") != "transfer" and bool(row.get("to_account_id"))


def _description(ev: dict) -> str:
    from backend.services.income import _txn_description

    inst = InstrumentStore().find_by_id(ev.get("instrument_id") or "") or {}
    return _txn_description(inst, ev.get("income_kind") or "dividend")


_CASH_FIELDS = ("paid_at", "income_kind", "gross_amount", "tax_withheld", "currency", "account_id")


def _sync_cash(
    ev: dict, new: dict, net: float, patch: dict, *, payee_id: Optional[str], conn: Connection
) -> Optional[str]:
    """The cash row this dividend has after the correction, or None. Where the cash goes is
    the account picked now, else the row's own account: a dividend whose row was deleted
    gets none back from a note edit."""
    from backend.services import ledger as ledger_service

    row = _live_row(ev.get("txn_id"), conn=conn)
    if not any(k in patch for k in _CASH_FIELDS):
        return row["id"] if row else None
    if "account_id" in patch:
        account_id = new.get("account_id")
    else:
        account_id = row.get("to_account_id") if row and _own_cash(row) else None
    if not account_id or net <= 0:
        if row and _own_cash(row):
            ledger_service.bulk_delete_transactions([row["id"]], conn=conn, reject_identities=False)
            return None
        return row["id"] if row else None
    if not row:
        return _book({**new, "account_id": account_id}, net, payee_id=payee_id, conn=conn)
    if not _own_cash(row):
        return row["id"]
    changes: dict = {
        "to_account_id": account_id,
        "to_amount": net,
        "to_currency": new["currency"],
        "date": new["paid_at"],
    }
    row_ccy = (row.get("to_currency") or "").upper()
    old_net = round(float(ev.get("gross_amount") or 0) - float(ev.get("tax_withheld") or 0), 2)
    if row["to_account_id"] == account_id and row_ccy and row_ccy != new["currency"] and old_net:
        changes["to_amount"] = round(float(row.get("to_amount") or 0) * net / old_net, 2)
        changes["to_currency"] = row_ccy
    if (row.get("description") or "") in {"", _description(ev)}:
        changes["description"] = _description(new)
    ledger_service.update_transaction(row["id"], changes, labels_as_given=True, conn=conn)
    return row["id"]


def _book(new: dict, net: float, *, payee_id: Optional[str], conn: Connection) -> Optional[str]:
    from backend.services import ledger as ledger_service

    inst = InstrumentStore().find_by_id(new.get("instrument_id") or "") or {}
    txn = ledger_service.create_transaction(
        {
            "date": new["paid_at"],
            "kind": "income",
            "description": _description(new),
            "to_account_id": new["account_id"],
            "to_amount": net,
            "to_currency": new["currency"],
            "tags": ["dividend"],
            "ticker": inst.get("price_source_id") or inst.get("symbol") or None,
            "source": "manual",
            **({"payee_id": payee_id} if payee_id else {}),
        },
        conn=conn,
    )
    return txn.get("id")
