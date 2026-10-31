"""
MATH-48: the account "period net" fold used to live in three places — the router's
Python loop (once per pagination path: the fast DB-paginated path and the slow
in-Python path) and the SQL twin (`db.transaction_store.query_txns_page`). The loop
is now hoisted into `services.ledger.period_net`, and `GET /api/transactions` calls it
from BOTH paths (the fast path passes its already SQL-summed native net through via
the `net=` override instead of re-deriving it from a page).

These tests pin the delegation: for a synthetic filtered set, the route's
`period_net`/`period_net_base` must equal what `services.ledger.period_net` computes
directly over the same transactions — on both the fast (DB-paginated) and slow
(Python-filtered, forced via a `tag` filter) code paths.

Offline + isolated: every store is DB-backed via `db_isolated`; FX is a fixed,
monkeypatched table (no live cache read).

Run:
    .venv/bin/python3 -m pytest tests/test_period_net.py -q
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.services import ledger as svc

_RATES = {"SGD": 1.0, "USD": 1.35}


@pytest.fixture
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    import backend.api.ledger as api_ledger_mod

    monkeypatch.setattr(api_ledger_mod, "load_fx_cache", lambda: dict(_RATES))
    # create_transaction stamps base_amount through core.fx's loader, not the router's.
    from backend.core import fx

    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: dict(_RATES))

    # The account under test is in a NON-base currency, so period_net_base actually
    # exercises the FX conversion (not just an identity pass-through).
    acct = svc.accounts.create(
        {"name": "Vantage USD", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    other = svc.accounts.create(
        {"name": "Meridian SGD", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    today = date.today().isoformat()
    txns = [
        svc.create_transaction(
            {
                "kind": "income",
                "date": today,
                "description": "Salary",
                "to_account_id": acct["id"],
                "to_amount": 1000.0,
                "to_currency": "USD",
                "tags": ["period-net-probe"],
            }
        ),
        svc.create_transaction(
            {
                "kind": "expense",
                "date": today,
                "description": "Groceries",
                "from_account_id": acct["id"],
                "from_amount": 150.0,
                "from_currency": "USD",
                "tags": ["period-net-probe"],
            }
        ),
        svc.create_transaction(
            {
                "kind": "expense",
                "date": today,
                "description": "Rent",
                "from_account_id": acct["id"],
                "from_amount": 300.0,
                "from_currency": "USD",
                "tags": ["period-net-probe"],
            }
        ),
        # An unrelated leg on a DIFFERENT account — must not leak into account_id's fold.
        svc.create_transaction(
            {
                "kind": "expense",
                "date": today,
                "description": "Coffee",
                "from_account_id": other["id"],
                "from_amount": 5.0,
                "from_currency": "SGD",
            }
        ),
    ]
    return {"account_id": acct["id"], "other_id": other["id"], "txns": txns}


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _expected(account_id: str) -> tuple[float, float]:
    """The service helper's own answer, over the same filtered set the route sees."""
    txns = svc.transactions.query_txns(account_id=account_id)
    return svc.period_net(txns, account_id, "SGD", _RATES)


def test_fast_path_period_net_matches_service_helper(client, env):
    """Default query (no tag/q/amount filter) takes the DB-paginated fast path."""
    resp = client.get("/api/transactions", params={"account_id": env["account_id"], "limit": 50})
    assert resp.status_code == 200
    body = resp.json()

    expected_net, expected_base = _expected(env["account_id"])
    assert body["period_net"] == pytest.approx(expected_net)
    assert body["period_net_base"] == pytest.approx(expected_base)
    # 1000 - 150 - 300 = 550 USD net; the unrelated SGD leg must not appear.
    assert body["period_net"] == pytest.approx(550.0)


def test_slow_path_period_net_matches_service_helper(client, env):
    """A `tag` filter forces the slow (Python-filtered) path — same numbers must come out."""
    resp = client.get(
        "/api/transactions",
        params={"account_id": env["account_id"], "tag": "period-net-probe", "limit": 50},
    )
    assert resp.status_code == 200
    body = resp.json()

    expected_net, expected_base = _expected(env["account_id"])
    assert body["period_net"] == pytest.approx(expected_net)
    assert body["period_net_base"] == pytest.approx(expected_base)
    assert body["period_net"] == pytest.approx(550.0)


def test_period_net_base_omitted_for_unknown_account(client, env):
    """An account_id with no matching account row must omit period_net_base, matching the
    pre-refactor behavior (period_net itself is still reported — it's just 0)."""
    resp = client.get("/api/transactions", params={"account_id": "acc_does_not_exist", "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_net"] == pytest.approx(0.0)
    assert "period_net_base" not in body
