"""A fake GoCardless for the bank data sync tests: real GoCardless row shapes (its own
``_txn_to_row``), an in-memory bank, and a log of every transaction read per account."""

from __future__ import annotations

from datetime import timedelta

from backend.core.user_time import user_today
from backend.services import accounts as accounts_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import FetchWindow

ACC_MAIN = "acc-main-0001"
ACC_SAVER = "acc-saver-0002"


def days_ago(n: int) -> str:
    return (user_today() - timedelta(days=n)).isoformat()


def txn(amount: float, on: str, text: str, *, tid: str | None = None, **extra) -> dict:
    out = {
        "bookingDate": on,
        "transactionAmount": {"amount": f"{amount:.2f}", "currency": "GBP"},
        "remittanceInformationUnstructured": text,
        **extra,
    }
    if tid:
        out["transactionId"] = tid
    return out


class FakeBank:
    """``booked``/``pending`` per provider account; ``reads`` logs each transaction read."""

    def __init__(self):
        self.booked: dict[str, list[dict]] = {ACC_MAIN: [], ACC_SAVER: []}
        self.pending: dict[str, list[dict]] = {ACC_MAIN: [], ACC_SAVER: []}
        self.reads: list[str] = []
        self.fail_with: Exception | None = None

    def fetch_window(self, store, since, before, account, *, accounts_only=False):
        ids = [a for a in self.booked if account is None or a == account]
        accounts = [
            {"provider_account_id": a, "name": f"ACME {a[-4:]}", "mask": a[-4:], "currency": "GBP"}
            for a in ids
        ]
        if accounts_only:
            return FetchWindow(accounts=accounts)
        rows = []
        for a in ids:
            self.reads.append(a)
            if self.fail_with is not None:
                raise self.fail_with
            for t in self.booked[a]:
                row = gocardless_svc._txn_to_row(t, a, "GBP", pending=False)
                if row and since <= row["date"] <= before:
                    rows.append(row)
            for t in self.pending[a]:
                row = gocardless_svc._txn_to_row(t, a, "GBP", pending=True)
                if row and since <= row["date"] <= before:
                    rows.append(row)
        return FetchWindow(accounts=accounts, rows=rows)


def configure_gocardless() -> None:
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    store = GoCardlessConfigStore()
    store.update({"secret_id": "SID", "secret_key": "SKEY", "enabled": True})
    key = store.add_bank(
        "req-1",
        institution_id="ACME_BANK_GB",
        institution_name="ACME Bank",
        country="GB",
        consent_expires_at=None,
    )
    store.update_bank(key, status="LN", account_ids=[ACC_MAIN, ACC_SAVER])


def linked_account(name: str, provider_account_id: str, balance: float = 1000.0) -> str:
    return accounts_service.create_account(
        {
            "name": name,
            "kind": "asset",
            "type": "cash",
            "currency": "GBP",
            "initial_balance": balance,
            "external_ids": [{"source": "gocardless", "ref": provider_account_id}],
        }
    )["id"]
