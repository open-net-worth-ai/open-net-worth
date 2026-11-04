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
        "currencies": {symbol: "USD" for symbol in symbols},
        "timestamp_verified": {symbol: True for symbol in symbols},
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


def test_source_proof_failure_blocks_new_orders_using_fresh_legacy_quote_but_not_idempotent_lookup(
    monkeypatch,
):
    result = workspace({"instruments": [ACME]})
    run_id = result["run"]["id"]
    stamp = datetime.now(timezone.utc).isoformat()
    trading.ingest_observations(
        run_id,
        [
            {
                "instrument": ACME,
                "timestamp": stamp,
                "price": "75",
                "source": "yahoo",
            }
        ],
    )
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbol: 75 for symbol in symbols},
            "as_of": {symbol: stamp for symbol in symbols},
            "currencies": {symbol: "USD" for symbol in symbols},
            "timestamp_verified": {symbol: True for symbol in symbols},
        },
    )
    price_feed.refresh_run_market_data(run_id)
    account = trading.get_account(run_id)["items"][0]
    intent = {
        "account_id": account["id"],
        "instrument": ACME,
        "side": "buy",
        "quantity": "1",
        "request_id": "prior-authorized-order",
    }
    prior = asyncio.run(trading.submit_order(run_id, intent))
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbols[0]: 75},
            "as_of": {symbols[0]: datetime.now(timezone.utc).isoformat()},
            "timestamp_verified": {symbols[0]: True},
        },
    )
    assert asyncio.run(adapter.run_due_trading_agents())["items"] == []
    assert trading.get_market_data(run_id)["items"][-1]["price"] == "75"
    with pytest.raises(ConflictError, match="environment has an unresolved error"):
        asyncio.run(
            trading.submit_order(run_id, {**intent, "request_id": "new-order-after-source-failure"})
        )
    assert asyncio.run(trading.submit_order(run_id, intent))["id"] == prior["id"]
    assert trading.list_orders(run_id)["total"] == 1


@pytest.mark.parametrize("foreign_currency", ["GBP", None])
def test_mixed_valid_and_rejected_symbols_keep_per_instrument_authority_for_orders_and_valuation(
    monkeypatch, foreign_currency
):
    from backend.services.trading.market import instrument_key

    foreign = {**ACME, "symbol": "FOREIGN"}
    result = workspace({"instruments": [ACME, foreign]})
    run_id = result["run"]["id"]
    stamp = datetime.now(timezone.utc).isoformat()
    trading.ingest_observations(
        run_id,
        [
            {"instrument": instrument, "timestamp": stamp, "price": "75", "source": "yahoo"}
            for instrument in (ACME, foreign)
        ],
    )
    account = trading.get_account(run_id)["items"][0]
    intent = {
        "account_id": account["id"],
        "instrument": foreign,
        "side": "buy",
        "quantity": "1",
        "request_id": "existing-legacy-order",
    }
    with pytest.raises(ConflictError, match="verified current-source authority"):
        asyncio.run(trading.submit_order(run_id, intent))
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbol: 75 for symbol in symbols},
            "as_of": {symbol: stamp for symbol in symbols},
            "currencies": {symbol: "USD" for symbol in symbols},
            "timestamp_verified": {symbol: True for symbol in symbols},
        },
    )
    price_feed.refresh_run_market_data(run_id)
    original = trading.get_market_data(run_id)["items"]
    assert all(row.get("source_version") is None for row in original)
    prior = asyncio.run(trading.submit_order(run_id, intent))
    assert trading.get_performance(run_id)["valuation_complete"] is True
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbol: 75 for symbol in symbols},
            "as_of": {symbol: stamp for symbol in symbols},
            "currencies": {
                symbol: "USD" if symbol == "ACME" else foreign_currency for symbol in symbols
            },
            "timestamp_verified": {symbol: True for symbol in symbols},
        },
    )
    refresh = price_feed.refresh_run_market_data(run_id)
    assert refresh.get("status") != "unavailable"
    stored = TradingStore("runs").required(run_id)
    assert not stored.get("environment_error")
    assert stored["market_quote_authority"][instrument_key(ACME)]["status"] == "verified"
    assert stored["market_quote_authority"][instrument_key(foreign)]["status"] == "unavailable"
    with pytest.raises(ConflictError, match="verified current-source authority"):
        asyncio.run(trading.submit_order(run_id, {**intent, "request_id": "new-rejected-order"}))
    assert asyncio.run(trading.submit_order(run_id, intent))["id"] == prior["id"]
    asyncio.run(
        trading.submit_order(
            run_id, {**intent, "instrument": ACME, "request_id": "allowed-good-order"}
        )
    )
    marked = trading.get_account(run_id)["items"][0]["positions"][instrument_key(foreign)]
    assert marked["market_price"] is None
    assert marked["market_value"] is None
    assert marked["unrealized_pnl"] is None
    valuation = trading.get_performance(run_id)
    assert valuation["valuation_complete"] is False
    assert valuation["equity"] is None
    assert valuation["pnl"] is None
    assert valuation["unrealized_pnl"] is None
    assert trading.get_market_data(run_id)["items"] == original


def test_rejected_symbol_does_not_fill_a_resting_paper_order_from_fresh_legacy_observation(
    monkeypatch,
):
    foreign = {**ACME, "symbol": "FOREIGN"}
    result = workspace({"instruments": [ACME, foreign]})
    run_id = result["run"]["id"]
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", quote_provider)
    price_feed.refresh_run_market_data(run_id)
    account = trading.get_account(run_id)["items"][0]
    intent = {
        "account_id": account["id"],
        "instrument": foreign,
        "side": "buy",
        "quantity": "1",
        "order_type": "limit",
        "limit_price": "74",
        "request_id": "resting-before-source-failure",
    }
    prior = asyncio.run(trading.submit_order(run_id, intent))
    assert prior["status"] == "open"
    stamp = datetime.now(timezone.utc).isoformat()
    trading.ingest_observations(
        run_id, [{"instrument": foreign, "timestamp": stamp, "price": "73", "source": "yahoo"}]
    )
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbol: 75 for symbol in symbols},
            "as_of": {symbol: datetime.now(timezone.utc).isoformat() for symbol in symbols},
            "currencies": {symbol: "USD" if symbol == "ACME" else "GBP" for symbol in symbols},
            "timestamp_verified": {symbol: True for symbol in symbols},
        },
    )
    price_feed.refresh_run_market_data(run_id)
    asyncio.run(trading.apply_run_action(run_id, {"action": "advance"}))
    assert trading.list_fills(run_id)["total"] == 0
    assert trading.list_orders(run_id)["items"][0]["status"] == "open"
    assert asyncio.run(trading.submit_order(run_id, intent))["id"] == prior["id"]


def test_current_authority_matches_exact_quote_and_cannot_be_supplied_through_agent_requests(
    monkeypatch,
):
    from backend.core.errors import ValidationError
    from backend.services.trading.market import raw_latest_quote
    from backend.services.trading.quote_authority import quote_is_authorized
    from backend.services.trading.work_records import record_work

    result = workspace({"instruments": [ACME]})
    run_id = result["run"]["id"]
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", quote_provider)
    price_feed.refresh_run_market_data(run_id)
    run = TradingStore("runs").required(run_id)
    quote = raw_latest_quote(run, ACME)
    assert quote_is_authorized(run, ACME, quote)
    assert not quote_is_authorized(run, ACME, {**quote, "price": "74"})
    assert not quote_is_authorized(run, ACME, {**quote, "timestamp": "2025-01-01T00:00:00Z"})
    fake = {"invented": {"status": "verified", "currency": "USD", "price": "1"}}
    with pytest.raises(ValidationError, match="Invalid trading input"):
        asyncio.run(
            request_market_data(run_id, {"instruments": [ACME], "market_quote_authority": fake})
        )
    with pytest.raises(ValidationError, match="Unsupported trading runtime fields"):
        trading.update_runtime(run_id, {"market_quote_authority": fake})
    record_work(run_id, "native_event", {"market_quote_authority": fake})
    assert (
        TradingStore("runs").required(run_id)["market_quote_authority"]
        == run["market_quote_authority"]
    )
