"""Synced transactions dated before an account's Starting Balance. The row itself is booked like
any other, but here it counts as history already inside the Starting Balance, while YNAB adds it
to the balance. Moving the Starting Balance is the person's call: the sync files one review item
per account, explaining both outcomes, and never moves it on its own."""

from __future__ import annotations

from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.ynab_sync import conflicts, units
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.context import Context

KIND = "before_start"
_LEGS = ("from_account_id", "to_account_id")


def key(account_id: str) -> str:
    return f"start:{account_id}"


def _early_rows(account_id: str, start: str, txns: list[dict], skip: set[str]) -> list[dict]:
    """Synced rows on this account dated before ``start``. ``skip``: the Starting Balance rows,
    and rows waiting on their own review item (the two sides do not agree on them yet)."""
    return [
        t
        for t in txns
        if not t.get("deleted")
        and t["id"] not in skip
        and units.ynab_refs(t)
        and account_id in (t.get(leg) for leg in _LEGS)
        and ledger_service.is_before_tracking_start(t.get("date"), start)
    ]


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}".strip()


def _item(acct: dict, start: str, rows: list[dict], txns: list[dict]) -> dict:
    earliest = min(t["date"] for t in rows)
    now = ledger_service.account_balance(acct, txns, start)
    counted = ledger_service.account_balance(acct, txns, earliest)
    cur = acct.get("currency") or ""
    many, app = len(rows) != 1, current().name
    reason = (
        f"{len(rows)} synced transaction{'s are' if many else ' is'} dated before "
        f"{acct['name']}'s Starting Balance ({start}). Open Net Worth treats "
        f"{'them' if many else 'it'} as history already inside the Starting Balance, so the "
        f"balance stays {_money(now, cur)}. {app} adds {'them' if many else 'it'} to the balance. "
        f"Move the Starting Balance to {earliest}: its amount stays the same, "
        f"{'they count' if many else 'it counts'}, and the balance becomes "
        f"{_money(counted, cur)}, as in {app}. "
        f"Keep as history: nothing changes here, and {acct['name']} stays "
        f"{_money(abs(counted - now), cur)} apart from {app}."
    )
    return {
        "start": start,
        "earliest": earliest,
        "count": len(rows),
        "balance_now": now,
        "balance_counted": counted,
        "currency": cur,
        "txn_ids": sorted(t["id"] for t in rows),
        "reason": reason,
    }


def _skipped(open_items: dict) -> set[str]:
    waiting = {c["onw_txn_id"] for c in open_items.values() if c.get("onw_txn_id")}
    return set(ledger_service.opening_txn_ids()) | waiting


def review(ctx: Context, st: dict) -> None:
    """File, refresh or close each synced account's item; acknowledged rows are not asked again."""
    kept: dict[str, list[str]] = st.setdefault("kept_as_history", {})
    txns = ledger_service.transactions.all()
    open_items = conflicts.open_keys()
    skip = _skipped(open_items)
    for acct in ledger_service.accounts.query_accounts():
        aid = acct["id"]
        if aid not in ctx.ynab_acct:
            continue
        start = ledger_service.opening_date(acct, txns)
        rows = _early_rows(aid, start, txns, skip) if start else []
        ids = {t["id"] for t in rows}
        if aid in kept:
            kept[aid] = sorted(ids & set(kept[aid]))
            if not kept[aid]:
                del kept[aid]
        if not ids or ids <= set(kept.get(aid, [])):
            if key(aid) in open_items:
                conflicts.close(key(aid))
            continue
        item = _item(acct, start, rows, txns)
        if (open_items.get(key(aid)) or {}).get("reason") == item["reason"]:
            continue  # the same rows and the same figures: already filed as it stands
        reason = item.pop("reason")
        conflicts.file(
            KIND, key(aid), acct["name"], reason, None, None, entity="start", account_id=aid, **item
        )


def resolve(item: dict, keep: str) -> None:
    """``ynab``: count the rows, moving the Starting Balance's date back and keeping its amount.
    ``onw``: keep them as history, and remember that so the sync does not ask again."""
    aid = item["account_id"]
    acct = ledger_service.accounts.find_by_id(aid)
    if acct is None:
        return
    txns = ledger_service.transactions.all()
    start = ledger_service.opening_date(acct, txns)
    rows = _early_rows(aid, start, txns, _skipped(conflicts.open_keys())) if start else []
    if not rows:
        return
    if keep == "ynab":
        amount = ledger_service.opening_amount(acct, txns)
        ledger_service.set_account_opening(aid, amount, min(t["date"] for t in rows))
        return
    st = sync_state.load()
    kept = st.setdefault("kept_as_history", {})
    kept[aid] = sorted(set(kept.get(aid, [])) | {t["id"] for t in rows})
    sync_state.save(st)
