"""Bank data sync — Enable Banking and Plaid through the SAME booker (ADR 2026-11-13).

Each provider here differs only by ``Provider`` config; its rows are built by its own adapter's
``_txn_to_row``, so these runs book exactly what the real adapters would hand over."""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import bank_data_sync
from backend.services import ledger as ledger_service
from backend.services.aggregation import enable_banking as eb_svc
from backend.services.aggregation import plaid as plaid_svc
from backend.services.aggregation.providers import PROVIDERS, FetchWindow
from backend.services.bank_data_sync import runner, settings
from tests.bank_sync_fakes import days_ago

pytestmark = pytest.mark.flags(bank_data_sync=True)


def _ready(monkeypatch, store_cls) -> None:
    monkeypatch.setattr(store_cls, "is_configured", lambda self: True)
    store_cls().update({"enabled": True})


def _account(source: str, ref: str, currency: str) -> str:
    return accounts_service.create_account(
        {
            "name": f"ACME {source}",
            "kind": "asset",
            "type": "cash",
            "currency": currency,
            "initial_balance": 500,
            "external_ids": [{"source": source, "ref": ref}],
        }
    )["id"]


def _live(account_id: str) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def test_the_config_is_all_that_differs():
    gc, eb, plaid = PROVIDERS["gocardless"], PROVIDERS["enable_banking"], PROVIDERS["plaid"]
    assert (gc.data_sync, gc.sync_interval_hours, gc.daily_reads) == (True, 12, 4)
    assert (eb.data_sync, eb.sync_interval_hours, eb.daily_reads) == (True, 12, 4)
    assert (plaid.data_sync, plaid.sync_interval_hours, plaid.daily_reads) == (True, 6, None)
    assert not any(
        PROVIDERS[k].data_sync for k in ("simplefin", "lunch_flow", "lunch_money", "ynab", "alpaca")
    )


# ── Enable Banking ──────────────────────────────────────────────────────────────────


@pytest.fixture
def eb(db_isolated, monkeypatch):
    from backend.db import EnableBankingConfigStore

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "EUR")
    _ready(monkeypatch, EnableBankingConfigStore)
    bank = {"rows": [], "reads": []}

    def fetch_window(store, since, before, account, *, accounts_only=False):
        accounts = [{"provider_account_id": "uid-1", "name": "ACME Giro", "currency": "EUR"}]
        if accounts_only:
            return FetchWindow(accounts=accounts)
        bank["reads"].append(account)
        rows = [eb_svc._txn_to_row(t, "uid-1", "EUR") for t in bank["rows"]]
        return FetchWindow(accounts=accounts, rows=[r for r in rows if r])

    monkeypatch.setattr(eb_svc, "fetch_window", fetch_window)
    bank["account"] = _account("enable_banking", "uid-1", "EUR")
    settings.save("enable_banking", mode="data")
    return bank


def _eb_txn(amount: float, indicator: str, ref: str, status: str = "BOOK") -> dict:
    return {
        "entry_reference": ref,
        "transaction_amount": {"amount": f"{amount:.2f}", "currency": "EUR"},
        "credit_debit_indicator": indicator,
        "booking_date": days_ago(3),
        "status": status,
        "remittance_information": [f"ACME {ref}"],
    }


def test_enable_banking_books_posted_rows_once_and_leaves_pending(eb):
    eb["rows"] = [
        _eb_txn(19.99, "DBIT", "e1"),
        _eb_txn(1200.00, "CRDT", "e2"),
        _eb_txn(7.50, "DBIT", "e3", status="PDNG"),
    ]
    result = runner.run_now(
        PROVIDERS["enable_banking"], "manual", runner.opted_accounts("enable_banking")
    )
    assert result["counts"]["booked"] == 2
    assert result["counts"]["pending_skipped"] == 1
    kinds = sorted(t["kind"] for t in _live(eb["account"]))
    assert kinds == ["expense", "income"]
    again = runner.run_now(
        PROVIDERS["enable_banking"], "manual", runner.opted_accounts("enable_banking")
    )
    assert again["counts"]["booked"] == 0
    assert eb["reads"] == ["uid-1", "uid-1"]


# ── Plaid ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def plaid(db_isolated, monkeypatch):
    from backend.db import PlaidConfigStore

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    _ready(monkeypatch, PlaidConfigStore)
    bank = {"txns": []}

    def fetch_window(store, since, before, account, *, accounts_only=False):
        accounts = [
            {"provider_account_id": a, "name": f"ACME {a}", "currency": "USD"}
            for a in ("pl-chk", "pl-sav")
        ]
        if accounts_only:
            return FetchWindow(accounts=accounts)
        rows = []
        for t in bank["txns"]:
            if account and t["account_id"] != account:
                continue
            row = plaid_svc._txn_to_row(t, {"currency": "USD"}, t["account_id"])
            if row:
                rows.append(row)
        return FetchWindow(accounts=accounts, rows=rows)

    monkeypatch.setattr(plaid_svc, "fetch_window", fetch_window)
    bank["chk"] = _account("plaid", "pl-chk", "USD")
    bank["sav"] = _account("plaid", "pl-sav", "USD")
    settings.save("plaid", mode="data", excluded_account_ids=[bank["sav"]])
    return bank


def _plaid_txn(tid: str, amount: float, *, account: str = "pl-chk", **extra) -> dict:
    # Plaid: a positive amount is money OUT of the account.
    return {
        "transaction_id": tid,
        "account_id": account,
        "amount": amount,
        "iso_currency_code": "USD",
        "date": days_ago(2),
        "name": f"ACME {tid}",
        "pending": False,
        **extra,
    }


def _run_plaid() -> dict:
    return runner.run_now(PROVIDERS["plaid"], "manual", runner.opted_accounts("plaid"))


def test_plaid_books_ticked_accounts_only(plaid):
    plaid["txns"] = [_plaid_txn("t1", 25.0), _plaid_txn("t2", 99.0, account="pl-sav")]
    assert _run_plaid()["counts"]["booked"] == 1
    assert [t["description"] for t in _live(plaid["chk"])] == ["ACME t1"]
    assert _live(plaid["sav"]) == []


def _booked_pending(plaid, amount: float) -> str:
    return ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(3),
            "description": "ACME DINER",
            "from_account_id": plaid["chk"],
            "from_amount": amount,
            "from_currency": "USD",
            "source": "agent",
            "external_refs": [{"source": "plaid", "ref": "pl-chk:pend-1"}],
        }
    )["id"]


def test_a_pending_row_that_posts_at_the_same_amount_is_linked_not_rebooked(plaid):
    txn_id = _booked_pending(plaid, 30.0)
    plaid["txns"] = [_plaid_txn("post-1", 30.0, pending_transaction_id="pend-1")]
    result = _run_plaid()
    assert result["counts"]["booked"] == 0
    [row] = _live(plaid["chk"])
    assert row["id"] == txn_id
    assert {e["ref"] for e in row["external_refs"]} == {"pl-chk:pend-1", "pl-chk:post-1"}
    assert _run_plaid()["counts"]["booked"] == 0


def test_a_pending_row_that_posts_at_another_amount_waits_for_review(plaid):
    _booked_pending(plaid, 30.0)
    plaid["txns"] = [_plaid_txn("post-1", 36.0, pending_transaction_id="pend-1")]
    result = _run_plaid()
    assert result["counts"]["booked"] == 0 and result["counts"]["to_review"] == 1
    [item] = bank_data_sync.list_reviews("plaid")
    assert item["kind"] == "posted"
    [row] = _live(plaid["chk"])
    assert row["from_amount"] == pytest.approx(30.0)
    assert _run_plaid()["counts"]["to_review"] == 0  # held, not asked twice

    bank_data_sync.accept_review(item["id"])
    [row] = _live(plaid["chk"])
    assert row["from_amount"] == pytest.approx(36.0)
    assert "pl-chk:post-1" in {e["ref"] for e in row["external_refs"]}
    assert _run_plaid()["counts"]["booked"] == 0
