"""Owned-assets disposal — the ``dispose`` op (one ledger transaction for the proceeds + the
archive, in ONE unit of work) and the proceeds-account picker it validates against.
Dispatched ONLY by the write choke (``services.owned_assets.apply_owned_asset_change``); the op
returns the stored row and the choke projects it.
"""

from __future__ import annotations

from sqlalchemy import Connection

from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.engine import locked_unit_of_work
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.tables import owned_assets as owned_assets_table
from backend.db.tables import rejected_identities as rejected_identities_table
from backend.db.tables import transactions as transactions_table
from backend.db.tables import txn_audit as txn_audit_table
from backend.services import ledger as ledger_service
from backend.services import owned_assets_rules as rules
from backend.services import transaction_links
from backend.services.fx_cache import load_fx_cache
from backend.services.owned_assets_summary import LedgerSnapshot

CASH_TYPES = frozenset({"cash"})


def _takes_proceeds(account: dict, base: str) -> bool:
    """A non-archived asset account of a genuine cash type IN THE BOOK'S BASE CURRENCY — the
    ONE rule behind both the proceeds picker and dispose's own check. Base-currency-only (M1
    security fix): dispose's `proceeds` figure is entered in base currency (an owned asset's
    `currency` is always the base at create time — see the store's docstring) and posted as a
    same-currency leg; a foreign-currency account would silently relabel that base amount as
    its own currency, an FX misstatement rather than a real conversion."""
    return (
        not account.get("archived")
        and account.get("kind") == "asset"
        and account.get("type") in CASH_TYPES
        and (account.get("currency") or "").upper() == base
    )


def cash_accounts(ledger: LedgerSnapshot | None = None) -> list[dict]:
    """The dispose-proceeds picker: every account that `_takes_proceeds`, with its balance.
    `ledger` is the page's shared snapshot; omitted, one is loaded."""
    if ledger is None:
        ledger = LedgerSnapshot(ledger_service.base_currency(), rates=load_fx_cache())
    return [
        {"id": a["id"], "name": a.get("name") or "", "balance_base": ledger.balance_base(a)}
        for a in ledger.accounts
        if _takes_proceeds(a, ledger.base)
    ]


def _proceeds_account(asset: dict, account_id: str | None) -> dict:
    base = ledger_service.base_currency()
    account = AccountStore().find_by_id(account_id) if account_id else None
    if account is None or not _takes_proceeds(account, base):
        raise ValidationError(f"Pick a cash account in {base}.", field="account_id")
    # M1: the picker already excludes a foreign-currency account, but this asks again, by the
    # asset's OWN currency rather than the book's current base — the honest check for what
    # dispose is actually about to do (relabel `proceeds`, a bare number, as this account's
    # currency). Defense in depth against a stale link or a direct (non-picker) caller.
    currency = (asset.get("currency") or "").upper()
    if (account.get("currency") or "").upper() != currency:
        raise ValidationError(f"Pick a cash account in {currency}.", field="account_id")
    return account


def _post_proceeds(
    store: OwnedAssetsStore,
    asset: dict,
    proceeds: float,
    account: dict,
    disposed_on: str,
    conn: Connection,
    *,
    rebooking: bool = False,
) -> dict | None:
    txn_data = {
        "date": disposed_on,
        "kind": "investment",
        "description": f"Sale proceeds — {asset['name']}",
        "to_account_id": account["id"],
        "to_amount": proceeds,
        "to_currency": account.get("currency") or ledger_service.base_currency(),
        "source": "manual",
        "external_refs": [{"source": "asset_disposal", "ref": asset["id"]}],
    }
    meta: dict = {}
    # A deleted proceeds row left its identity remembered; the user is booking it back on purpose.
    txn = ledger_service.create_transaction(txn_data, conn=conn, meta_out=meta, resurrect=rebooking)
    # H3: the ledger's own external_refs dedup can fire even though the asset read as
    # not-yet-archived — a proceeds row for this asset already booked by an earlier commit
    # (two disposes can no longer interleave through this choke, which reads under the same
    # lock it writes with, but the ledger row is the ground truth). `idempotent_existing_id`
    # means NOTHING was written by THIS call; rewriting the disposal record with this call's
    # inputs would silently disagree with the transaction actually on file, so nothing
    # changes and the row as committed is handed back.
    if meta.get("idempotent_existing_id"):
        return store.find_by_id(asset["id"], conn=conn)
    return store.archive(
        asset["id"],
        {
            "disposed_on": disposed_on,
            "disposal_proceeds": proceeds,
            "disposal_txn_id": txn["id"],
            "disposal_account_id": account["id"],
        },
        conn=conn,
    )


def _proceeds_row_is_gone(asset: dict, conn: Connection) -> bool:
    """A sale that had proceeds and no longer has the row for them can book them again, or the
    asset would stay sold with its money missing and no way to put it back."""
    if not asset.get("disposal_proceeds"):
        return False
    txn_id = asset.get("disposal_txn_id")
    return not txn_id or transaction_links.is_gone(
        ledger_service.transactions.find_by_id(txn_id, conn=conn)
    )


def _repeat_sale(asset: dict, proceeds: float, account_id: str, disposed_on: str) -> dict:
    """Idempotency lives HERE, not just at the ledger's external_refs dedup: that dedup only
    no-ops the TRANSACTION, so a second dispose call with a different account_id/date still
    fell through to `archive` and rewrote the disposal fields on an asset that was never
    actually re-sold. An identical retry is a true no-op (the existing asset, untouched); a
    retry with DIFFERENT inputs is refused — you cannot sell the same asset twice."""
    same = (
        asset.get("disposal_proceeds") == proceeds
        and asset.get("disposal_account_id") == account_id
        and asset.get("disposed_on") == disposed_on
    )
    if same:
        return asset
    sold_on = asset.get("disposed_on")
    when = f" on {rules.day_label(sold_on)}" if sold_on else ""
    raise ValidationError(f"This asset was already sold{when} — a sale can't be recorded twice.")


def dispose(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    """One ledger transaction for the proceeds (none for a write-off) and the archive — which
    also releases the asset's loan — read, checked and written in ONE locked unit of work."""
    proceeds = rules.proceeds(change.get("proceeds"))
    disposed_on = (
        rules.parse_day(change.get("date"), field="date", future=rules.FUTURE_SALE)
        or user_today().isoformat()
    )
    with locked_unit_of_work(
        owned_assets_table, transactions_table, txn_audit_table, rejected_identities_table
    ) as conn:
        asset = rules.require_asset(store, asset_id, conn=conn)
        account = _proceeds_account(asset, change.get("account_id"))
        if asset.get("archived"):
            if proceeds > 0 and _proceeds_row_is_gone(asset, conn):
                return _post_proceeds(
                    store, asset, proceeds, account, disposed_on, conn, rebooking=True
                )
            return _repeat_sale(asset, proceeds, account["id"], disposed_on)
        rules.refuse_sale_before_purchase(disposed_on, asset)
        if proceeds > 0:
            return _post_proceeds(store, asset, proceeds, account, disposed_on, conn)
        return store.archive(
            asset_id,
            {
                "disposed_on": disposed_on,
                "disposal_proceeds": 0.0,
                "disposal_txn_id": None,
                "disposal_account_id": account["id"],
            },
            conn=conn,
        )
