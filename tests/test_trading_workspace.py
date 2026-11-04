"""Behavioral contracts for the unified trading environment, using synthetic capital."""

import asyncio
import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.trading_api import router
from backend.core.errors import ConflictError, ValidationError
from backend.core.trading_runtime import bind_trading_context
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services import trading
from backend.services.trading.providers import register_live_adapter

INSTRUMENT = {"symbol": "ACME", "venue": "simulation", "asset_class": "stock", "currency": "USD"}
T0 = "2026-01-05T14:30:00+00:00"
T1 = "2026-01-05T14:31:00+00:00"
T2 = "2026-01-05T14:32:00+00:00"
END = "2026-01-05T15:00:00+00:00"


@pytest.fixture(autouse=True)
def isolated(db_isolated):
    yield
    register_live_adapter("mcp-test", None)


def call(awaitable):
    return asyncio.run(awaitable)


def make_run(*, mode="backtest", mandate=None, account=None, observations=None, name="Agent One"):
    workspace = trading.create_workspace(
        {"name": "Synthetic experiment", "agent_label": name, "mandate": mandate or {}}
    )
    start, end = T0, END
    if mode != "backtest":
        current = datetime.now(timezone.utc)
        start, end = (
            (current - timedelta(minutes=1)).isoformat(),
            (current + timedelta(days=30)).isoformat(),
        )
        observations = (
            observations
            if observations is not None
            else [{"instrument": INSTRUMENT, "timestamp": current.isoformat(), "price": "100"}]
        )
    run = trading.create_run(
        workspace["id"],
        {
            "mode": mode,
            "start_at": start,
            "end_at": end,
            "live_authorized": mode == "live",
            "accounts": [account or {"capital": "1000"}],
            "observations": observations
            if observations is not None
            else [
                {"instrument": INSTRUMENT, "timestamp": T0, "price": "100", "volume": "10"},
                {"instrument": INSTRUMENT, "timestamp": T1, "price": "110", "volume": "2"},
                {"instrument": INSTRUMENT, "timestamp": T2, "price": "120", "volume": "10"},
            ],
        },
    )
    return run


def start(run):
    return call(trading.apply_run_action(run["id"], {"action": "start"}))


def order(run, request="order-one", quantity="3", side="buy", **extra):
    account = trading.get_account(run["id"])["items"][0]
    return {
        "account_id": account["id"],
        "instrument": INSTRUMENT,
        "side": side,
        "quantity": quantity,
        "request_id": request,
        **extra,
    }


def test_store_crud_and_book_scope():
    store = TradingStore("workspaces")
    store.insert({"id": "tw_synthetic", "scope_id": "", "name": "Test"})
    assert store.required("tw_synthetic")["name"] == "Test"
    store.update_by_id("tw_synthetic", {"name": "Changed"})
    assert store.list_scope("")[0]["name"] == "Changed"
    assert store.delete("tw_synthetic")
    assert store.all() == []


def test_backtest_does_not_expose_future_data_or_fill_same_observation():
    run = make_run()
    start(run)
    assert "observations" not in trading.get_run(run["id"])
    assert len(trading.get_market_data(run["id"])["items"]) == 1
    submitted = call(trading.submit_order(run["id"], order(run)))
    assert submitted["status"] == "open"
    assert trading.list_fills(run["id"])["total"] == 0
    call(trading.apply_run_action(run["id"], {"action": "advance"}))
    assert trading.list_orders(run["id"])["items"][0]["filled_quantity"] == "2"
    call(trading.apply_run_action(run["id"], {"action": "advance"}))
    assert trading.list_orders(run["id"])["items"][0]["status"] == "filled"
    assert trading.list_fills(run["id"])["total"] == 2
    assert trading.get_performance(run["id"])["equity"] == 1020


def test_available_at_filters_delayed_publication():
    run = make_run(
        observations=[
            {"instrument": INSTRUMENT, "timestamp": T0, "available_at": T1, "price": "100"}
        ]
    )
    assert trading.get_market_data(run["id"])["total"] == 0
    start(run)
    call(trading.apply_run_action(run["id"], {"action": "advance"}))
    assert trading.get_market_data(run["id"])["total"] == 1


def test_reservations_prevent_overspending_and_cancel_releases_them():
    run = make_run()
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run, quantity="8")))
    assert trading.get_account(run["id"])["items"][0]["available_cash"] == "200"
    with pytest.raises(ConflictError, match="Insufficient"):
        call(trading.submit_order(run["id"], order(run, request="second", quantity="3")))
    call(trading.cancel_order(run["id"], submitted["id"]))
    assert trading.get_account(run["id"])["items"][0]["available_cash"] == "1000"


def test_same_request_returns_original_and_different_payload_is_refused():
    run = make_run()
    start(run)
    first = call(trading.submit_order(run["id"], order(run)))
    assert call(trading.submit_order(run["id"], order(run)))["id"] == first["id"]
    with pytest.raises(ConflictError, match="different order"):
        call(trading.submit_order(run["id"], order(run, quantity="4")))


def test_foreign_account_and_unfunded_short_sale_refused():
    first, second = make_run(), make_run()
    start(first)
    with pytest.raises(ConflictError, match="outside"):
        call(trading.submit_order(first["id"], order(second)))
    with pytest.raises(ConflictError, match="quantity"):
        call(trading.submit_order(first["id"], order(first, side="sell")))


def test_pause_cancels_orders_and_prevents_agent_submissions():
    run = make_run()
    start(run)
    call(trading.submit_order(run["id"], order(run)))
    paused = call(trading.apply_run_action(run["id"], {"action": "pause"}))
    assert paused["state"] == "paused"
    assert trading.list_orders(run["id"])["items"][0]["status"] == "canceled"
    with pytest.raises(ConflictError, match="not running"):
        call(trading.submit_order(run["id"], order(run, request="after-pause")))
    assert call(trading.apply_run_action(run["id"], {"action": "resume"}))["state"] == "running"


def test_limits_and_fee_slippage_are_enforced():
    run = make_run(mandate={"fee_bps": "10", "slippage_bps": "10", "max_order_notional": "300"})
    start(run)
    with pytest.raises(ConflictError, match="notional"):
        call(trading.submit_order(run["id"], order(run, quantity="4")))
    call(trading.submit_order(run["id"], order(run, quantity="1")))
    call(trading.apply_run_action(run["id"], {"action": "advance"}))
    fill = trading.list_fills(run["id"])["items"][0]
    assert float(fill["price"]) == pytest.approx(110.11)
    assert float(fill["fees"]) == pytest.approx(0.11011)


def test_completion_expires_open_orders_and_simulations_never_book_real_money():
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    run = make_run()
    start(run)
    call(trading.submit_order(run["id"], order(run, order_type="limit", limit_price="10")))
    completed = call(trading.apply_run_action(run["id"], {"action": "advance", "until": END}))
    assert completed["state"] == "completed"
    assert trading.list_orders(run["id"])["items"][0]["status"] == "expired"
    assert AccountStore().all() == []
    assert TransactionStore().all() == []


def test_paper_uses_same_order_contract_and_current_clock():
    run = make_run(mode="paper")
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run, quantity="1")))
    assert submitted["status"] == "filled"
    assert trading.get_performance(run["id"])["equity"] == 1000
    with pytest.raises(ValidationError, match="current time"):
        call(trading.apply_run_action(run["id"], {"action": "advance", "until": END}))


def test_historical_dataset_is_frozen_and_future_paper_quotes_refused():
    historical = make_run()
    with pytest.raises(ConflictError, match="immutable"):
        trading.ingest_observations(
            historical["id"], [{"instrument": INSTRUMENT, "timestamp": T0, "price": "90"}]
        )
    paper = make_run(mode="paper")
    with pytest.raises(ValidationError, match="future"):
        trading.ingest_observations(
            paper["id"],
            [
                {
                    "instrument": INSTRUMENT,
                    "timestamp": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    "price": "90",
                }
            ],
        )


def test_frozen_run_definition_and_fair_comparison():
    first = make_run(name="First")
    second = make_run(name="Second")
    trading.update_workspace(first["workspace_id"], {"instructions": "Changed future mandate"})
    assert trading.get_run(first["id"])["workspace_snapshot"]["instructions"] == ""
    compared = trading.create_comparison(
        {"name": "Comparable", "run_ids": [first["id"], second["id"]]}
    )
    assert compared["comparable"]
    assert len(compared["results"]) == 2
    unequal = make_run(account={"capital": "900"})
    with pytest.raises(ConflictError, match="different"):
        trading.create_comparison({"name": "Unfair", "run_ids": [first["id"], unequal["id"]]})


def test_usage_is_runtime_owned_idempotent_and_agent_lease_is_exclusive():
    run = make_run()
    start(run)
    trading.record_decision(run["id"], {"summary": "Observe", "model_cost_usd": "100"})
    assert trading.get_run(run["id"])["model_cost_usd"] == "0"
    trading.record_agent_usage(run["id"], {"receipt_id": "receipt-one", "model_cost_usd": "1.25"})
    trading.record_agent_usage(run["id"], {"receipt_id": "receipt-one", "model_cost_usd": "1.25"})
    assert trading.get_run(run["id"])["model_cost_usd"] == "1.25"
    lease = trading.claim_agent_cycle(run["id"])
    with pytest.raises(ConflictError, match="cycle"):
        trading.claim_agent_cycle(run["id"])
    trading.release_agent_cycle(run["id"], lease["lease_id"])
    assert trading.claim_agent_cycle(run["id"])["lease_id"] != lease["lease_id"]


def test_revoked_external_grant_is_checked_inside_order_transaction():
    run = make_run()
    start(run)
    digest = hashlib.sha256(b"synthetic-test-token").hexdigest()
    with trading_transaction() as conn:
        TradingStore("grants").insert(
            {
                "id": run["id"],
                "scope_id": run["id"],
                "token_hash": digest,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            },
            conn=conn,
        )
    with bind_trading_context(run["id"], "external", digest):
        call(trading.submit_order(run["id"], order(run)))
        TradingStore("grants").update_by_id(run["id"], {"token_hash": None})
        with pytest.raises(ConflictError, match="revoked"):
            call(trading.submit_order(run["id"], order(run, request="after-revoke")))


class FakeBroker:
    def __init__(self, timeout=False):
        self.timeout = timeout
        self.submissions = 0
        self.orders = {}
        self.account = {
            "provider_id": "synthetic-provider",
            "broker_account_id": "synthetic-broker",
            "currency": "USD",
            "cash": "1000",
            "positions": {},
            "allocation_isolated": True,
            "capabilities": ["client_order_id", "lookup_order", "fills", "cancel"],
        }

    async def inspect_account(self, account):
        return deepcopy(self.account)

    async def submit(self, account, order):
        self.submissions += 1
        receipt = {
            "broker_order_id": "broker-" + order["id"],
            "client_order_id": order["id"],
            "status": "open",
            "fills": [],
        }
        self.orders[order["id"]] = receipt
        if self.timeout:
            raise TimeoutError("Accepted remotely, acknowledgement lost")
        return receipt

    async def cancel(self, account, order):
        receipt = self.orders[order["id"]]
        receipt["status"] = "canceled"
        return receipt

    async def reconcile(self, account, orders):
        return {"account": deepcopy(self.account), "orders": list(self.orders.values())}


def make_live(broker):
    register_live_adapter("mcp-test", broker)
    return make_run(
        mode="live",
        account={
            "capital": "1000",
            "connector_id": "mcp-test",
            "broker_account_id": "synthetic-broker",
        },
    )


def test_live_timeout_is_unknown_and_retries_do_not_submit_twice():
    broker = FakeBroker(timeout=True)
    run = make_live(broker)
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run, quantity="1")))
    assert submitted["status"] == "submission_unknown"
    assert trading.get_run(run["id"])["state"] == "needs_reconciliation"
    assert call(trading.submit_order(run["id"], order(run, quantity="1")))["id"] == submitted["id"]
    assert broker.submissions == 1
    reconciled = call(trading.reconcile_run(run["id"]))
    assert reconciled["state"] == "running"
    assert trading.list_orders(run["id"])["items"][0]["status"] == "open"


def test_live_reconciliation_books_each_fill_once_and_flags_outside_activity():
    broker = FakeBroker()
    run = make_live(broker)
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run, quantity="2")))
    receipt = broker.orders[submitted["id"]]
    fill = {
        "execution_id": "synthetic-fill-one",
        "quantity": "1",
        "price": "100",
        "fees": "1",
        "side": "buy",
        "instrument": INSTRUMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "broker_order_id": receipt["broker_order_id"],
    }
    receipt.update(status="partially_filled", fills=[fill])
    broker.account.update(
        cash="899",
        positions={
            "simulation:stock:ACME:USD": {
                "instrument": INSTRUMENT,
                "quantity": "1",
                "cost_basis": "101",
            }
        },
    )
    assert call(trading.reconcile_run(run["id"]))["state"] == "running"
    call(trading.reconcile_run(run["id"]))
    assert trading.list_fills(run["id"])["total"] == 1
    assert trading.get_account(run["id"])["items"][0]["cash"] == "899"
    broker.account["cash"] = "898"
    assert call(trading.reconcile_run(run["id"]))["state"] == "needs_reconciliation"


def test_live_activation_requires_adapter_and_isolated_account():
    run = make_run(mode="live")
    with pytest.raises(ConflictError, match="identity"):
        start(run)
    broker = FakeBroker()
    broker.account["allocation_isolated"] = False
    another = make_live(broker)
    with pytest.raises(ConflictError, match="isolated"):
        start(another)


def test_alias_connectors_cannot_allocate_same_broker_account_twice():
    broker = FakeBroker()
    first = make_live(broker)
    start(first)
    register_live_adapter("mcp-alias", broker)
    second = make_run(
        mode="live",
        account={
            "capital": "1000",
            "connector_id": "mcp-alias",
            "broker_account_id": "synthetic-broker",
            "venue": "another-venue-label",
        },
    )
    with pytest.raises(ConflictError, match="already managed"):
        start(second)
    register_live_adapter("mcp-alias", None)


def test_duplicate_account_allocations_in_one_run_are_refused():
    broker = FakeBroker()
    register_live_adapter("mcp-test", broker)
    workspace = trading.create_workspace({"name": "Duplicate funding"})
    current = datetime.now(timezone.utc)
    allocation = {
        "capital": "1000",
        "connector_id": "mcp-test",
        "broker_account_id": "synthetic-broker",
    }
    run = trading.create_run(
        workspace["id"],
        {
            "mode": "live",
            "live_authorized": True,
            "start_at": (current - timedelta(minutes=1)).isoformat(),
            "end_at": (current + timedelta(days=1)).isoformat(),
            "accounts": [allocation, allocation],
        },
    )
    with pytest.raises(ConflictError, match="allocated twice"):
        start(run)


def test_live_restart_never_resubmits_order_and_blocks_until_reconciled():
    broker = FakeBroker()
    run = make_live(broker)
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run)))
    TradingStore("orders").update_by_id(submitted["id"], {"status": "submitting"})
    trading.recover_runs()
    assert trading.get_run(run["id"])["state"] == "needs_reconciliation"
    assert trading.list_orders(run["id"])["items"][0]["status"] == "submission_unknown"
    assert broker.submissions == 1


def test_live_book_projection_cash_identity_replay_and_restore(monkeypatch):
    from backend.core import fx
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger, lots

    monkeypatch.setattr(lots, "_load_rates", lambda: {"USD": 1, "SGD": 1})
    monkeypatch.setattr(lots, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"USD": 1})
    book_account = AccountStore().create(
        {"name": "Synthetic custodian", "type": "investment", "currency": "USD"}
    )
    InstrumentStore().create(
        {
            "symbol": "ACME",
            "name": "Synthetic stock",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "ACME",
        }
    )
    broker = FakeBroker()
    register_live_adapter("mcp-test", broker)
    run = make_run(
        mode="live",
        account={
            "capital": "1000",
            "connector_id": "mcp-test",
            "broker_account_id": "synthetic-broker",
            "book_account_id": book_account["id"],
        },
    )
    start(run)
    submitted = call(trading.submit_order(run["id"], order(run, quantity="1")))
    receipt = broker.orders[submitted["id"]]
    receipt.update(
        status="filled",
        fills=[
            {
                "execution_id": "book-fill-one",
                "quantity": "1",
                "price": "100",
                "fees": "0",
                "side": "buy",
                "instrument": INSTRUMENT,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "broker_order_id": receipt["broker_order_id"],
            }
        ],
    )
    broker.account.update(
        cash="900",
        positions={
            "simulation:stock:ACME:USD": {
                "instrument": INSTRUMENT,
                "quantity": "1",
                "cost_basis": "100",
            }
        },
    )
    call(trading.reconcile_run(run["id"]))
    fill = trading.list_fills(run["id"])["items"][0]
    assert fill["book_status"] == "booked"
    assert len(LotStore().all()) == 1
    assert len(TransactionStore().all()) == 1
    ref = trading.execution_external_ref(fill)
    cash = TransactionStore().by_external_ref(ref["source"], ref["ref"])
    assert cash is not None
    imported = ledger.create_transaction(
        {
            "kind": "investment",
            "date": fill["timestamp"][:10],
            "from_account_id": book_account["id"],
            "from_amount": 100,
            "from_currency": "USD",
            "description": "Synthetic broker confirmation",
            "external_refs": [ref],
        },
        allow_duplicate=True,
    )
    assert imported["id"] == cash["id"]
    assert trading.project_live_fills(run["id"])["items"][0]["result"]["idempotent"]
    assert len(LotStore().all()) == 1
    assert len(TransactionStore().all()) == 1
    # A restored book can lose its local lot/cash projection while broker evidence survives.
    for lot in LotStore().all():
        LotStore().delete(lot["id"])
    for position in PositionStore().all():
        PositionStore().delete(position["id"])
    for txn in TransactionStore().all():
        TransactionStore().delete(txn["id"])
    assert trading.project_live_fills(run["id"])["items"][0]["status"] == "booked"
    assert len(LotStore().all()) == 1
    assert len(TransactionStore().all()) == 1
    assert broker.submissions == 1


def test_paper_fills_at_latest_quote_instead_of_historical_cheapest():
    current = datetime.now(timezone.utc)
    quotes = [
        {
            "instrument": INSTRUMENT,
            "timestamp": (current - timedelta(seconds=20)).isoformat(),
            "price": "1",
        },
        {"instrument": INSTRUMENT, "timestamp": current.isoformat(), "price": "100"},
    ]
    run = make_run(mode="paper", observations=quotes)
    start(run)
    call(trading.submit_order(run["id"], order(run, quantity="1")))
    assert trading.list_fills(run["id"])["items"][0]["price"] == "100"


def test_api_create_run_action_order_read_and_error_envelopes():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    created = client.post("/api/trading/workspaces", json={"name": "Synthetic dashboard"})
    assert created.status_code == 201
    wid = created.json()["id"]
    assert client.get("/api/trading/workspaces").json()["total"] == 1
    assert client.get(f"/api/trading/workspaces/{wid}").status_code == 200
    assert (
        client.patch(f"/api/trading/workspaces/{wid}", json={"instructions": "Observe"}).status_code
        == 200
    )
    run = client.post(
        f"/api/trading/workspaces/{wid}/runs",
        json={
            "mode": "backtest",
            "start_at": T0,
            "end_at": END,
            "observations": [{"instrument": INSTRUMENT, "timestamp": T0, "price": "100"}],
        },
    ).json()
    rid = run["id"]
    assert client.get(f"/api/trading/workspaces/{wid}/runs").json()["total"] == 1
    assert (
        client.post(f"/api/trading/runs/{rid}/actions", json={"action": "start"}).status_code == 200
    )
    submitted = client.post(f"/api/trading/runs/{rid}/orders", json=order(run)).json()
    assert client.delete(f"/api/trading/runs/{rid}/orders/{submitted['id']}").status_code == 200
    for path in [
        "environment",
        "accounts",
        "market-data",
        "orders",
        "fills",
        "events",
        "performance",
    ]:
        assert client.get(f"/api/trading/runs/{rid}/{path}").status_code == 200
    assert client.get("/api/trading/runs/missing").status_code == 404
    assert (
        client.post(f"/api/trading/runs/{rid}/actions", json={"action": "start"}).status_code == 409
    )
