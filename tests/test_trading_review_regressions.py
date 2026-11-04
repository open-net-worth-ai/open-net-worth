import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.errors import ConflictError
from backend.core.sensitive import NON_RESTORABLE_TABLES
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services import trading
from backend.services.trading.providers import register_live_adapter
from tests.test_trading_workspace import (
    INSTRUMENT,
    T1,
    FakeBroker,
    make_live,
    make_run,
    order,
    start,
)


@pytest.fixture(autouse=True)
def isolated(db_isolated):
    yield
    register_live_adapter("mcp-test", None)


async def test_marketable_sell_limit_cannot_understate_quote_notional():
    run = make_run(mode="paper", mandate={"max_order_notional": "100"})
    await trading.apply_run_action(run["id"], {"action": "start"})
    for i in range(10):
        await trading.submit_order(run["id"], order(run, request=f"buy-{i}", quantity="1"))
    with pytest.raises(ConflictError, match="notional"):
        await trading.submit_order(
            run["id"],
            order(
                run,
                request="sell-all",
                side="sell",
                quantity="10",
                order_type="limit",
                limit_price="1",
            ),
        )


async def test_recovered_order_obeys_stop_policy_after_late_acknowledgement():
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowBroker(FakeBroker):
        async def submit(self, account, intent):
            receipt = await super().submit(account, intent)
            entered.set()
            await release.wait()
            return receipt

        async def cancel(self, account, intent):
            if not intent.get("broker_order_id"):
                raise ConflictError("Submission identity not yet known")
            return await super().cancel(account, intent)

    broker = SlowBroker()
    run = make_live(broker)
    await trading.apply_run_action(run["id"], {"action": "start"})
    submit = asyncio.create_task(trading.submit_order(run["id"], order(run, quantity="1")))
    await entered.wait()
    await trading.apply_run_action(run["id"], {"action": "stop"})
    release.set()
    ack = await submit
    settled = await trading.reconcile_run(run["id"])
    assert broker.orders[ack["id"]]["status"] == "canceled"
    assert settled["state"] == "stopped"
    assert broker.submissions == 1


def test_comparison_does_not_rank_different_historical_clock_positions():
    first, second = make_run(), make_run(name="Agent Two")
    start(first)
    start(second)
    asyncio.run(trading.apply_run_action(first["id"], {"action": "advance", "until": T1}))
    comparison = trading.create_comparison(
        {"name": "Synthetic comparison", "run_ids": [first["id"], second["id"]]}
    )
    assert comparison["comparable"] is False
    assert any("environment times" in reason for reason in comparison["reasons"])


def test_stopped_run_keeps_its_final_valuation_after_quote_ttl(monkeypatch):
    from backend.services.trading import market

    run = make_run(mode="paper")
    start(run)
    asyncio.run(trading.submit_order(run["id"], order(run, quantity="1")))
    asyncio.run(trading.apply_run_action(run["id"], {"action": "stop"}))
    before = trading.get_performance(run["id"])
    monkeypatch.setattr(
        market, "now", lambda: (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    )
    after = trading.get_performance(run["id"])
    assert after["valuation_complete"]
    assert after["equity"] == before["equity"]
    assert after["as_of"] == before["as_of"]


def test_quote_retention_continues_ingestion_and_keeps_durable_provenance():
    run = make_run(mode="paper")
    stamp = datetime.now(timezone.utc) - timedelta(seconds=20001)
    rows = [
        {
            "instrument": INSTRUMENT,
            "timestamp": (stamp + timedelta(seconds=i)).isoformat(),
            "available_at": None,
            "price": "10",
            "volume": None,
        }
        for i in range(20000)
    ]
    with trading_transaction() as conn:
        TradingStore("runs").update_by_id(run["id"], {"observations": rows}, conn=conn)
    incoming = {**rows[-1], "timestamp": (stamp + timedelta(seconds=20000)).isoformat()}
    assert trading.ingest_observations(run["id"], [incoming])["received"] == 1
    saved = TradingStore("runs").required(run["id"])
    assert len(saved["observations"]) == 20000
    assert datetime.fromisoformat(
        saved["observations"][-1]["timestamp"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(incoming["timestamp"])
    events = trading.list_events(run["id"])["items"]
    assert events[-1]["payload"]["observations"][0]["price"] == "10"


def test_lifetime_drawdown_survives_bounded_chart_history():
    run = make_run()
    with trading_transaction() as conn:
        TradingStore("runs").update_by_id(
            run["id"],
            {
                "lifetime_peak_equity": "1200",
                "lifetime_max_drawdown_pct": "25",
                "valuation_history": [{"timestamp": run["current_time"], "equity": 1000}],
            },
            conn=conn,
        )
    assert trading.get_performance(run["id"])["max_drawdown_pct"] == 25


def test_clone_preserves_nonsecret_transaction_lock_singleton():
    assert "trading_control" not in NON_RESTORABLE_TABLES
    with trading_transaction() as conn:
        assert TradingStore("control").required("trading_lock", conn=conn) is not None


def test_simulator_refuses_day_orders_without_verified_session_calendar():
    run = make_run()
    start(run)
    assert trading.get_environment(run["id"])["capabilities"]["time_in_force"] == ["gtc"]
    with pytest.raises(ConflictError, match="verified session calendar"):
        asyncio.run(trading.submit_order(run["id"], order(run, time_in_force="day")))


def test_stop_never_hides_unreconciled_live_fill_booking():
    broker = FakeBroker()
    run = make_live(broker)
    start(run)
    submitted = asyncio.run(trading.submit_order(run["id"], order(run, quantity="1")))
    receipt = broker.orders[submitted["id"]]
    receipt.update(
        status="filled",
        fills=[
            {
                "execution_id": "unmapped-fill",
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
    account = trading.get_account(run["id"])["items"][0]
    with trading_transaction() as conn:
        TradingStore("accounts").update_by_id(
            account["id"], {"book_account_id": "missing-synthetic-book-account"}, conn=conn
        )
    stopped = asyncio.run(trading.apply_run_action(run["id"], {"action": "stop"}))
    assert stopped["state"] == "needs_reconciliation"
    reconciled = asyncio.run(trading.reconcile_run(run["id"]))
    assert reconciled["state"] == "needs_reconciliation"
    assert trading.list_fills(run["id"])["items"][0]["book_status"] == "needs_reconciliation"


def test_resume_rejects_unowned_broker_order_even_when_cash_is_unchanged():
    broker = FakeBroker()
    run = make_live(broker)
    start(run)
    asyncio.run(trading.apply_run_action(run["id"], {"action": "pause"}))
    broker.account["open_orders"] = [
        {"client_order_id": "outside-synthetic-order", "broker_order_id": "outside-1"}
    ]
    with pytest.raises(ConflictError, match="outside this managed"):
        asyncio.run(trading.apply_run_action(run["id"], {"action": "resume"}))
    assert trading.get_run(run["id"])["state"] == "paused"


def test_stock_and_crypto_share_execution_across_venue_currency_accounts():
    from backend.db.transaction_store import TransactionStore

    stock = {
        "symbol": "ACME",
        "venue": "synthetic-stock",
        "asset_class": "stock",
        "currency": "USD",
    }
    crypto = {
        "symbol": "EXAMPLE/EUR",
        "venue": "synthetic-crypto",
        "asset_class": "crypto",
        "currency": "EUR",
    }
    workspace = trading.create_workspace(
        {
            "name": "Cross-market synthetic mandate",
            "mandate": {"instruments": [stock, crypto], "fee_bps": "100"},
        }
    )
    run = trading.create_run(
        workspace["id"],
        {
            "mode": "backtest",
            "start_at": "2026-01-05T14:30:00Z",
            "end_at": "2026-01-05T15:00:00Z",
            "accounts": [
                {
                    "name": "Synthetic stocks",
                    "venue": stock["venue"],
                    "capital": "1000",
                    "currency": "USD",
                },
                {
                    "name": "Synthetic crypto",
                    "venue": crypto["venue"],
                    "capital": "1000",
                    "currency": "EUR",
                },
            ],
            "fx_rates": {"USD": "1", "EUR": "1.1"},
            "observations": [
                {"instrument": instrument, "timestamp": stamp, "price": price}
                for instrument, initial, final in [(stock, "100", "101"), (crypto, "10", "11")]
                for stamp, price in [("2026-01-05T14:30:00Z", initial), (T1, final)]
            ],
        },
    )
    start(run)
    before = TransactionStore().all()
    for account, instrument in zip(trading.get_account(run["id"])["items"], [stock, crypto]):
        asyncio.run(
            trading.submit_order(
                run["id"],
                {
                    "account_id": account["id"],
                    "instrument": instrument,
                    "side": "buy",
                    "quantity": "1",
                    "request_id": "cross-market",
                },
            )
        )
    asyncio.run(trading.apply_run_action(run["id"], {"action": "advance", "until": T1}))
    assert {
        fill["instrument"]["asset_class"] for fill in trading.list_fills(run["id"])["items"]
    } == {"stock", "crypto"}
    assert trading.get_performance(run["id"])["equity"] == pytest.approx(2098.869)
    assert TransactionStore().all() == before
