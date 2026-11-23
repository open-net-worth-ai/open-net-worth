"""`GET /api/query` — a screen's reads in one request — and the request memo under it.

The load-bearing guarantee: a source answered through the batch is the SAME answer its own GET
route gives (one definition per fact). The rest pins the request memo's rules: one read per scope,
callers own their copies, a commit inside the scope drops what it held, and nothing is memoised
outside a scope.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.core import request_memo
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch):
    from backend.services import prices as prices_service

    def _fail(*_args, **_kwargs):
        raise RuntimeError("offline (test)")

    monkeypatch.setattr(prices_service, "fetch_live", _fail)


@pytest.fixture
def book(db_isolated):
    from backend.services import ledger as ledger_svc

    bank = ledger_svc.accounts.create(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 12_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    ledger_svc.accounts.create(
        {
            "name": "Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -800.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    inst = InstrumentStore().create(
        {
            "symbol": "ACME",
            "name": "Acme Corp",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACME",
            "manual_price": 50.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": bank["id"],
            "quantity": 10.0,
            "cost_basis": 40.0,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )
    ledger_svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-01-15",
            "description": "Groceries",
            "from_account_id": bank["id"],
            "from_amount": 42.0,
            "from_currency": "SGD",
        }
    )
    return bank


def _batch(queries: list[dict]) -> dict[str, dict]:
    res = client.get("/api/query", params={"q": json.dumps({"queries": queries})})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in res.text.splitlines() if line]
    assert [x["refId"] for x in lines] == [q["refId"] for q in queries]
    return {x["refId"]: x for x in lines}


# Each source beside the GET route it stands for.
_PARITY = [
    ("net_worth", {}, "/api/net-worth"),
    ("net_worth_stats", {}, "/api/net-worth/stats"),
    ("portfolio_analytics", {}, "/api/portfolio/analytics"),
    ("commitments_summary", {}, "/api/commitments/summary"),
    ("commitments_coverage", {"months": 12}, "/api/commitments/coverage?months=12"),
    ("planning_summary", {"ym": "2026-11"}, "/api/planning/summary?ym=2026-11"),
    ("investments_brief", {}, "/api/investments/brief"),
    ("portfolio_xray", {}, "/api/portfolio/xray"),
    (
        "reports_rollup",
        {"months": 6, "merchant_limit": 10},
        "/api/reports/rollup?months=6&merchant_limit=10",
    ),
    (
        "cashflow_flow",
        {"month": "2026-10", "compact": True},
        "/api/cashflow/flow?month=2026-10&compact=true",
    ),
    ("cashflow_projections", {}, "/api/cashflow/projections"),
    ("dividends", {}, "/api/income/dividends"),
]


def test_every_source_answers_exactly_what_its_route_answers(book):
    queries = [{"refId": name, "source": name, "params": p} for name, p, _ in _PARITY]
    got = _batch(queries)
    for name, _params, url in _PARITY:
        direct = client.get(url)
        assert direct.status_code == 200, (url, direct.text)
        assert got[name]["status"] == 200, (name, got[name])
        assert got[name]["data"] == direct.json(), name


def test_identical_queries_compute_once(book, monkeypatch):
    from backend.services import networth_stats

    calls = []
    real = networth_stats.networth_stats
    monkeypatch.setattr(networth_stats, "networth_stats", lambda *a, **k: calls.append(1) or real())
    got = _batch(
        [
            {"refId": "a", "source": "net_worth_stats"},
            {"refId": "b", "source": "net_worth_stats", "params": {}},
        ]
    )
    assert len(calls) == 1
    assert got["a"]["data"] == got["b"]["data"]


def test_a_failing_source_fails_alone(book, monkeypatch):
    from backend.services import networth_stats

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(networth_stats, "networth_stats", _boom)
    got = _batch(
        [
            {"refId": "stats", "source": "net_worth_stats"},
            {"refId": "nw", "source": "net_worth"},
        ]
    )
    assert got["stats"]["status"] == 500 and "data" not in got["stats"]
    assert got["nw"]["status"] == 200


@pytest.mark.parametrize(
    "queries",
    [
        [],
        [{"refId": "a", "source": "no_such_source"}],
        [{"refId": "a", "source": "net_worth", "params": {"x": 1}}],
        [{"refId": "a", "source": "commitments_coverage", "params": {"months": "twelve"}}],
        [{"refId": "a", "source": "planning_summary", "params": {"ym": "Nov"}}],
        [{"refId": "a", "source": "net_worth"}, {"refId": "a", "source": "net_worth"}],
        [{"source": "net_worth"}],
    ],
)
def test_a_bad_batch_is_refused_whole(book, queries):
    res = client.get("/api/query", params={"q": json.dumps({"queries": queries})})
    assert res.status_code == 422


@pytest.mark.parametrize("months", [120, 0])
def test_coverage_months_out_of_range_are_clamped_like_the_route(book, months):
    # "All" on Commitments asks for as many months as the longest commitment; the route clamps
    # to 1–60, so the batch must answer it the same way instead of refusing the whole batch.
    got = _batch([{"refId": "c", "source": "commitments_coverage", "params": {"months": months}}])
    direct = client.get(f"/api/commitments/coverage?months={months}")
    assert got["c"]["status"] == 200
    assert got["c"]["data"] == direct.json()


def test_malformed_q_is_refused():
    assert client.get("/api/query", params={"q": "{not json"}).status_code == 422


# ── the request memo ─────────────────────────────────────────────────────────────────────


def test_outside_a_scope_nothing_is_memoised():
    calls = []
    request_memo.memo("k", lambda: calls.append(1))
    request_memo.memo("k", lambda: calls.append(1))
    assert len(calls) == 2


def test_one_transaction_read_per_scope_and_callers_own_their_rows(book):
    store = TransactionStore()
    with request_memo.opened() as scope:
        first = store.all()
        assert first
        first[0]["description"] = "changed by one caller"
        first.sort(key=lambda t: t["id"], reverse=True)
        second = store.all()
        assert scope.hits >= 1
        assert all(t.get("description") != "changed by one caller" for t in second)
        assert [t["id"] for t in second] == [t["id"] for t in store.all()]


def test_a_commit_inside_the_scope_drops_what_it_held(book):
    from backend.services import ledger as ledger_svc

    store = TransactionStore()
    with request_memo.opened():
        before = len(store.all())
        ledger_svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-02-01",
                "description": "Coffee",
                "from_account_id": book["id"],
                "from_amount": 25.0,
                "from_currency": "SGD",
            }
        )
        assert len(store.all()) == before + 1
