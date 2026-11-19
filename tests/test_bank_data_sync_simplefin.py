"""Bank data sync — SimpleFIN (ADR 2026-11-13, Amendment 18), through the real fetch path with
only the Bridge's ``/accounts`` answer faked. Synthetic ACME data only."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from backend.core.user_time import user_zone
from backend.db import SimpleFinConfigStore
from backend.services import accounts as accounts_service
from backend.services import import_runs
from backend.services import ledger as ledger_service
from backend.services.aggregation import simplefin as svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import runner, settings
from tests.bank_sync_fakes import days_ago

SF = PROVIDERS["simplefin"]
# Its accounts hold foreign currencies: seed synthetic rates, never a live FX fetch.
pytestmark = [pytest.mark.flags(bank_data_sync=True), pytest.mark.usefixtures("fx_rates")]


def _ts(day: str) -> int:
    return int(
        datetime.combine(datetime.fromisoformat(day).date(), time(12), user_zone()).timestamp()
    )


def _txn(tid: str, amount: str, day: str, *, pending: bool = False) -> dict:
    if pending:  # the protocol: posted "may be 0" while pending, transacted_at says when
        return {
            "id": tid,
            "posted": 0,
            "transacted_at": _ts(day),
            "amount": amount,
            "description": f"ACME {tid}",
        }
    return {"id": tid, "posted": _ts(day), "amount": amount, "description": f"ACME {tid}"}


def _sf_account(aid: str, txns: list[dict], currency: str = "USD") -> dict:
    return {
        "id": aid,
        "name": f"ACME {aid}",
        "conn_id": "C1",
        "currency": currency,
        "balance": "500.00",
        "balance-date": _ts(days_ago(0)),
        "transactions": txns,
    }


@pytest.fixture
def bridge(db_isolated, monkeypatch):
    """The Bridge's answer (``feed``) and every request made to it (``calls``)."""
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        SimpleFinConfigStore, "access_url", lambda self: "https://u:p@bridge.example/sf"
    )
    monkeypatch.setattr(SimpleFinConfigStore, "is_configured", lambda self: True)
    SimpleFinConfigStore().update({"enabled": True})
    state: dict = {"calls": [], "feed": {"errlist": [], "connections": [], "accounts": []}}

    def fetch_accounts(
        access_url, start_date=None, end_date=None, account=None, balances_only=False
    ):
        state["calls"].append({"start": start_date, "end": end_date, "account": account})
        return state["feed"]

    monkeypatch.setattr(svc, "fetch_accounts", fetch_accounts)
    state["chk"] = _book_account("A1")
    state["sav"] = _book_account("A2")
    settings.save("simplefin", mode="data")
    return state


def _book_account(ref: str) -> str:
    return accounts_service.create_account(
        {
            "name": f"ACME {ref}",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 500,
            "external_ids": [{"source": "simplefin", "ref": ref}],
        }
    )["id"]


def _run() -> dict:
    svc._forget_shared()  # each run is its own read, as minutes apart in life
    return runner.run_now(SF, "manual", runner.opted_accounts("simplefin"))


def _live(account_id: str) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def test_simplefin_is_offered_to_the_bank_sync():
    assert SF in settings.capable_providers()


def test_two_accounts_are_one_request_per_window_slice_not_one_each(bridge):
    bridge["feed"]["accounts"] = [
        _sf_account("A1", [_txn("t1", "-12.50", days_ago(3))]),
        _sf_account("A2", [_txn("t2", "100.00", days_ago(2))]),
    ]

    result = _run()

    assert result["counts"]["booked"] == 2
    assert all(c["account"] is None for c in bridge["calls"])
    slices = {(c["start"], c["end"]) for c in bridge["calls"]}
    assert len(bridge["calls"]) == len(slices), "each account asked the Bridge again"
    assert all(e - s <= 44 * 86400 + 3600 for s, e in slices)


def test_a_row_posted_as_zero_is_pending_and_booked_uncleared(bridge):
    bridge["feed"]["accounts"] = [
        _sf_account("A1", [_txn("p1", "-4.20", days_ago(1), pending=True)]),
        _sf_account("A2", []),
    ]

    _run()

    [row] = _live(bridge["chk"])
    assert row["cleared"] is False


def test_a_connection_the_bridge_needs_signing_into_asks_for_it_and_books_nothing(bridge):
    bridge["feed"] = {
        "errlist": [{"code": "con.auth", "msg": "ACME Bank needs you to sign in", "conn_id": "C1"}],
        "connections": [{"conn_id": "C1", "name": "ACME Bank"}],
        "accounts": [_sf_account("A1", [_txn("t1", "-12.50", days_ago(3))])],
    }

    result = _run()

    assert result["status"] == "reconnect_needed"
    assert _live(bridge["chk"]) == []
    assert "SimpleFIN Bridge" in " ".join(import_runs.get(result["run_id"])["warnings"])


def test_the_bridges_rate_limit_stops_the_run(bridge):
    bridge["feed"] = {
        "errors": ["Too many requests. SimpleFIN data is only refreshed once every 24 hours."],
        "accounts": [],
    }

    result = _run()

    assert result["status"] == "rate_limited"
    assert _live(bridge["chk"]) == []


def test_a_custom_currency_account_books_nothing(bridge):
    bridge["feed"]["accounts"] = [
        _sf_account("A1", [_txn("t1", "-500", days_ago(3))], currency="https://example.com/points"),
        _sf_account("A2", [_txn("t2", "10.00", days_ago(2))]),
    ]

    _run()

    assert _live(bridge["chk"]) == []
    assert len(_live(bridge["sav"])) == 1


def test_an_account_whose_data_is_incomplete_books_no_first_import(bridge):
    bridge["feed"] = {
        "errlist": [
            {"code": "act.missingdata", "msg": "Incomplete transaction listing", "account_id": "A1"}
        ],
        "accounts": [
            _sf_account("A1", [_txn("t1", "-12.50", days_ago(3))]),
            _sf_account("A2", [_txn("t2", "100.00", days_ago(2))]),
        ],
    }

    _run()

    assert _live(bridge["chk"]) == [], "a first import the Bridge said is incomplete was booked"
    assert len(_live(bridge["sav"])) == 1, "another account's read was held back"


def test_a_lapsed_subscription_is_the_users_to_fix(bridge, monkeypatch):
    def refused(*a, **k):
        raise svc.ExternalServiceError(svc._STATUS_TEXT[402])

    monkeypatch.setattr(svc, "fetch_accounts", refused)

    result = _run()

    assert result["status"] == "auth_error"
    assert "payment" in " ".join(import_runs.get(result["run_id"])["warnings"])
