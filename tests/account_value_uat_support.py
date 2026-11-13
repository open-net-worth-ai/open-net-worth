"""Shared helpers for the PR #93 UAT suites (tests/test_account_value_uat_*.py) — synthetic data.

The reference book (REF): Main Checking (cash) 10,000.00 and Old 401(k) (retirement) typed
8,000.00, both opened 2026-01-01; ACME is priced 604.00 and BETA 50.25. Helpers are copied in
the style of tests/test_account_value*.py, which the other suites import from directly.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.db.transaction_store import TransactionStore
from backend.services import account_value as av
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import positions as positions_service
from tests import test_account_value as base
from tests.test_account_value import _account, _add_holding, _instrument, _shell, _value

book = base.book  # the shared isolated-book fixture
__all__ = [
    "book",
    "client",
    "plaid",
    "eur_book",
    "fresh_db",
    "_account",
    "_add_holding",
    "_instrument",
    "_shell",
    "_value",
]

REPORTED = 123456.78


def today() -> str:
    return user_today().isoformat()


@pytest.fixture
def client(book):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def tool(fn, payload):
    text, _ = asyncio.run(fn(payload))
    return json.loads(text)


def install_rates(monkeypatch, tmp_path, rates: dict) -> None:
    """Make ``rates`` (units of base per unit of currency, base 1.0) known everywhere."""
    from backend.core import fx as core_fx
    from backend.services import fx_cache
    from backend.services import lots as lots_service

    path = tmp_path / "fx.json"
    path.write_text(json.dumps({"rates": rates}), encoding="utf-8")
    monkeypatch.setattr(fx_cache, "_FX_CACHE_FILE", path)
    monkeypatch.setattr(fx_cache, "base_currency", lambda: "USD")  # imported by name there
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: dict(rates))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: dict(rates))


@pytest.fixture
def eur_book(book, monkeypatch, tmp_path):
    """The book with 1 EUR = 1.10 USD known everywhere a rate is read."""
    install_rates(monkeypatch, tmp_path, {"USD": 1.0, "EUR": 1.10})
    return book


def set_price(instrument: dict, price: float) -> None:
    from backend.db.instrument_store import InstrumentStore

    InstrumentStore().touch(instrument["id"], {"manual_price": price})


def seed_history(instrument: dict, price: float, days: list[str]) -> None:
    """Price-history points, so a past date's net worth values holdings (as a real book has)."""
    from backend.db.price_history_store import PriceHistoryStore

    for day in days:
        PriceHistoryStore().record(instrument["price_source_id"], price, day=day)


def lots_of(position_id: str) -> list[dict]:
    from backend.db.lot_store import LotStore

    return [lot for lot in LotStore().all() if lot.get("position_id") == position_id]


def events_of(position_id: str) -> list[dict]:
    from backend.db.realized_event_store import RealizedEventStore

    return [e for e in RealizedEventStore().all() if e.get("position_id") == position_id]


@pytest.fixture
def fresh_db(book, tmp_path, monkeypatch, _sqlite_golden_head):
    """Call it to throw the book away and start on an empty migrated database — so one test can
    build the SAME scenario twice, flag off then flag on."""
    from tests.db.conftest import point_engine_at

    count = {"n": 0}

    def switch():
        count["n"] += 1
        point_engine_at(
            f"sqlite:///{tmp_path}/second_{count['n']}.db", monkeypatch, _sqlite_golden_head
        )

    return switch


def differential(fresh_db, scenario) -> tuple[dict, dict]:
    """Run ``scenario()`` (builds a book, returns nothing) with the flag off, then on a fresh
    database with it on. Returns ``(off_figures, on_figures)`` — cash on hand and Ready to Assign
    must be identical: this PR changes valuation, never budgeting."""
    from backend.core.feature_flags import override

    with override(allow_brokerage_total_valuation=False):
        scenario()
        off = figures()
    fresh_db()
    with override(allow_brokerage_total_valuation=True):
        scenario()
        on = figures()
    return off, on


def assert_budget_untouched(off: dict, on: dict) -> None:
    assert on["cash"] == off["cash"], (off, on)
    assert on["rta"] == off["rta"], (off, on)


def cash_excludes_investments() -> None:
    """Cash on hand is Σ cash accounts only — never an investment or retirement account."""
    cash_total = round(
        sum(
            ledger_service.account_with_balance(a)["balance"]
            for a in ledger_service.accounts.query_accounts()
            if a["type"] == "cash"
        ),
        2,
    )
    assert figures()["cash"] == cash_total


def ref_book() -> dict:
    """Main Checking 10,000.00 + Old 401(k) 8,000.00 + priced ACME/BETA, all opened 2026-01-01."""
    checking = _account(name="Main Checking", type_="cash", total=10000.0)
    k401 = _account()
    return {
        "checking": checking,
        "k401": k401,
        "acme": _instrument("ACME", 604.0),
        "beta": _instrument("BETA", 50.25),
    }


def hydrated(account_id: str) -> dict:
    return ledger_service.account_with_balance(ledger_service.accounts.find_by_id(account_id))


def balance(account_id: str) -> float:
    return round(av.ledger_balance(account_id), 2)


def holdings(account_id: str) -> float:
    return av.holdings_value(ledger_service.accounts.find_by_id(account_id))


def figures() -> dict:
    """Net worth, cash on hand and Ready to Assign, from the real services."""
    from backend.services import cashflow, planning

    nw = ledger_service.net_worth("USD")
    return {
        "nw": nw["net_worth"],
        "nw_excl_retirement": nw["net_worth_excl_retirement"],
        "cash": cashflow.cash_balance_base("USD"),
        "rta": round(float(planning.budget_month(today()[:7])["ready_to_assign"]), 2),
    }


def rows(description: str | None = None, account_id: str | None = None) -> list[dict]:
    out = [
        t
        for t in TransactionStore().all()
        if not t.get("deleted")
        and (description is None or t.get("description") == description)
        and (account_id is None or account_id in (t.get("from_account_id"), t.get("to_account_id")))
    ]
    return sorted(out, key=lambda t: (t.get("date", ""), t.get("created_at", "")))


def signed(txn: dict, account_id: str) -> float:
    """The row's effect on ``account_id`` in its own currency."""
    if txn.get("to_account_id") == account_id:
        return round(float(txn["to_amount"]), 2)
    return -round(float(txn["from_amount"]), 2)


def carve_rows(account_id: str) -> list[dict]:
    return [
        t
        for t in rows(account_id=account_id)
        if t.get("kind") == "adjustment" and t.get("description") != "Starting Balance"
    ]


def acquire(position_id, qty=5, unit_cost=604.0, funding=None, acquired_at="2026-10-02", **extra):
    from backend.services import lots as lots_service

    change = {
        "type": "acquire",
        "position_id": position_id,
        "quantity": qty,
        "unit_cost": unit_cost,
        "cost_currency": "USD",
        "acquired_at": acquired_at,
        **extra,
    }
    if funding:
        change["funding_account_id"] = funding
    out = lots_service.apply_lot_change(change)
    assert out.get("ok"), out
    return out


class _Store:
    def __init__(self, enabled=True):
        self._enabled = enabled

    def is_configured(self):
        return True

    def get(self):
        return {"enabled": self._enabled}


@dataclass
class FakeProvider:
    """A connector that reports balances — no network. A balance is a number, or a dict
    ``{balance, currency, ...}`` overriding the entry of one account."""

    balances: dict = field(default_factory=dict)
    currency: str = "USD"
    enabled: bool = True
    fail: str | None = None
    complete: bool = True
    calls: int = 0
    key: str = "plaid"
    label: str = "Plaid"
    disabled: bool = False

    def store(self):
        return _Store(self.enabled)

    def account_balances(self):
        self.calls += 1
        if self.fail:
            raise ValidationError(self.fail)
        accounts = []
        for ref, bal in self.balances.items():
            entry = {"provider_account_id": ref, "balance": bal, "currency": self.currency}
            if isinstance(bal, dict):
                entry = {"provider_account_id": ref, "currency": self.currency, **bal}
            accounts.append(entry)
        return {"accounts": accounts, "complete": self.complete}

    def classify_error(self, exc):
        return "reconnect_needed" if "ITEM_LOGIN_REQUIRED" in str(exc) else "error"


@pytest.fixture
def plaid(book, monkeypatch):
    from backend.services.aggregation import providers

    fake = FakeProvider(balances={"pa-1": REPORTED})
    monkeypatch.setitem(providers.PROVIDERS, "plaid", fake)
    return fake


def link_account(acct_id: str, ref="pa-1", source="plaid"):
    link = [{"source": source, "ref": ref, "name": "Brokerage", "mask": "4321"}]
    accounts_service.update_account(acct_id, {"external_ids": link})


def linked(total=0.0, name="Brokerage X", ref="pa-1", **extra) -> dict:
    acct = _account(name=name, type_="investment", total=total, **extra)
    link_account(acct["id"], ref)
    return acct


def sync_state(account_id: str) -> dict:
    return hydrated(account_id)["value_sync"]


def sync_rows() -> list[dict]:
    return rows("Total from Plaid")


def position_of(account_id: str, instrument_id: str) -> dict:
    return next(
        r
        for r in positions_service.hydrated()
        if r.get("account_id") == account_id and r.get("instrument_id") == instrument_id
    )
