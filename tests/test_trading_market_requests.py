"""Agents choose current markets within the delegated scope, without future-data access."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.ai import trading_runner_adapter as adapter
from backend.core.errors import ConflictError
from backend.core.trading_runtime import bind_trading_context
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services import trading
from backend.services.trading import delegation, price_feed
from backend.services.trading.market_requests import request_market_data

ACME = {"symbol": "ACME", "venue": "simulation", "asset_class": "stock", "currency": "USD"}


@pytest.fixture(autouse=True)
def isolated(db_isolated, monkeypatch):
    monkeypatch.setattr(price_feed.prices, "fetch_history_result", lambda *_: {"points": []})


def workspace(mandate=None, *, mode="paper"):
    now = datetime.now(timezone.utc)
    data = {
        "workspace": {
            "name": "Synthetic research allocation",
            "instructions": "Choose instruments and research them.",
            "mandate": {"market_data_source": "yahoo", **(mandate or {})},
        },
        "environment": {
            "mode": mode,
            "start_at": (now - timedelta(minutes=1)).isoformat(),
            "end_at": (now + timedelta(days=30)).isoformat(),
        },
    }
    if mode == "backtest":
        data["environment"]["observations"] = [
            {
                "instrument": ACME,
                "timestamp": (now - timedelta(minutes=1)).isoformat(),
                "price": "75",
            }
        ]
    return asyncio.run(delegation.create_delegation(data))


def quote_provider(symbols):
    return {
        "prices": {symbol: 75 for symbol in symbols},
        "as_of": {symbol: datetime.now(timezone.utc).isoformat() for symbol in symbols},
    }


def test_empty_current_universe_gives_agent_a_planning_turn(monkeypatch):
    result = workspace()
    seen = []

    async def planning(run_id):
        seen.append(run_id)
        return {"run_id": run_id, "status": "planning"}

    monkeypatch.setattr(adapter, "run_lucius_cycle", planning)
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda *_: pytest.fail("No instrument was requested"),
    )
    outcomes = asyncio.run(adapter.run_due_trading_agents())
    assert seen == [result["run"]["id"]]
    assert outcomes["items"][0]["status"] == "planning"
    assert (
        delegation.workspace_overview(result["workspace"]["id"])["current_work"]["state"]
        != "blocked"
    )


@pytest.mark.parametrize(
    "instrument", [ACME, {**ACME, "symbol": "COIN/USD", "asset_class": "crypto"}]
)
def test_requested_markets_persist_and_continue_refreshing_without_changing_authority(
    monkeypatch, instrument
):
    result = workspace()
    run_id = result["run"]["id"]
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", quote_provider)
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_history_result",
        lambda *_: {
            "points": [
                {"date": stamp, "open": 74, "high": 76, "low": 73, "close": 75, "volume": 1000}
            ]
        },
    )
    requested = asyncio.run(request_market_data(run_id, {"instruments": [instrument]}))
    assert requested["refresh"]["received"] == 2
    stored = TradingStore("runs").required(run_id)
    assert stored["subscribed_instruments"] == [instrument]
    assert stored["workspace_snapshot"]["mandate"]["instruments"] == []
    assert stored["initial_capital"] == result["run"]["initial_capital"]
    assert stored["mode"] == result["run"]["mode"]
    assert stored["live_authorized"] is False
    assert price_feed.refresh_run_market_data(run_id)["received"] >= 1
    assert trading.get_market_data(run_id)["items"][-1]["instrument"] == instrument
    assert any(row.get("open") == "74" for row in trading.get_market_data(run_id)["items"])


@pytest.mark.parametrize(
    ("mandate", "instrument", "message"),
    [
        ({"allowed_asset_classes": ["stock"]}, {**ACME, "asset_class": "crypto"}, "asset class"),
        ({"instruments": [ACME]}, {**ACME, "symbol": "OTHER"}, "explicit mandate"),
        ({}, {**ACME, "currency": "EUR"}, "allocated account"),
        ({}, {**ACME, "venue": "other"}, "allocated account"),
        ({"market_data_source": "supplied"}, ACME, "reviewed data source"),
    ],
)
def test_requests_cannot_expand_reviewed_authority(monkeypatch, mandate, instrument, message):
    result = workspace(mandate)
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda *_: pytest.fail("Scope violation reached network"),
    )
    with pytest.raises(ConflictError, match=message):
        asyncio.run(request_market_data(result["run"]["id"], {"instruments": [instrument]}))
    assert "subscribed_instruments" not in TradingStore("runs").required(result["run"]["id"])


def test_historical_and_cross_run_requests_are_refused_before_network(monkeypatch):
    historical = workspace(mode="backtest")
    monkeypatch.setattr(
        price_feed.prices, "fetch_yahoo_quotes", lambda *_: pytest.fail("Future data accessed")
    )
    with pytest.raises(ConflictError, match="Historical"):
        asyncio.run(request_market_data(historical["run"]["id"], {"instruments": [ACME]}))
    paper = workspace()
    with (
        bind_trading_context(historical["run"]["id"], "test"),
        pytest.raises(ConflictError, match="another run"),
    ):
        asyncio.run(request_market_data(paper["run"]["id"], {"instruments": [ACME]}))


def test_environment_failure_is_visible_and_success_clears_only_that_blocker(monkeypatch):
    result = workspace({"instruments": [ACME]})
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", lambda *_: {"prices": {}})
    assert asyncio.run(adapter.run_due_trading_agents())["items"] == []
    failed = delegation.workspace_overview(result["workspace"]["id"])
    assert failed["current_work"]["state"] == "blocked"
    assert "no usable quotes" in failed["current_work"]["blocker"]
    with trading_transaction() as conn:
        TradingStore("runs").update_by_id(
            result["run"]["id"], {"usage_state": "unknown"}, conn=conn
        )
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", quote_provider)

    async def no_paid_turn(*args, **kwargs):
        return {"status": "billing_still_unknown"}

    monkeypatch.setattr(adapter, "run_lucius_cycle", no_paid_turn)
    asyncio.run(adapter.run_due_trading_agents())
    assert TradingStore("runs").required(result["run"]["id"])["environment_error"] is None
    assert (
        delegation.workspace_overview(result["workspace"]["id"])["current_work"]["state"]
        == "blocked"
    )


def test_boot_ownership_reclaims_unexpired_orphan_lease_and_preserves_billing_gate():
    result = workspace()
    run_id = result["run"]["id"]
    lease = trading.claim_agent_cycle(run_id, paid=True)
    trading.recover_runs()
    assert trading.get_run(run_id)["agent_lease_id"] == lease["lease_id"]
    trading.recover_runs(boot_owned=True)
    recovered = trading.get_run(run_id)
    assert recovered["agent_lease_id"] is None
    assert recovered["usage_state"] == "unknown"
    overview = delegation.workspace_overview(result["workspace"]["id"])
    assert overview["current_work"]["state"] == "blocked"
    assert "billing" in overview["current_work"]["blocker"]
    with pytest.raises(ConflictError, match="billing reconciliation"):
        trading.claim_agent_cycle(run_id, paid=True)
