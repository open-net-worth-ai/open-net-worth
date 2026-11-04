import pytest

from backend.core.errors import ConflictError
from backend.services.trading import price_feed


def configured_run(mode="paper"):
    return {
        "mode": mode,
        "observations": [],
        "workspace_snapshot": {
            "mandate": {
                "market_data_source": "yahoo",
                "instruments": [
                    {
                        "symbol": "EXAMPLE/USD",
                        "venue": "simulation",
                        "asset_class": "crypto",
                        "currency": "USD",
                    },
                ],
            }
        },
    }


def test_backtest_never_reaches_current_price_provider(monkeypatch):
    monkeypatch.setattr(price_feed, "_feed_snapshot", lambda _: configured_run("backtest"))
    monkeypatch.setattr(
        price_feed.prices, "fetch_yahoo_quotes", lambda *_: pytest.fail("future-data leak")
    )
    with pytest.raises(ConflictError, match="Historical"):
        price_feed.refresh_run_market_data("synthetic")


def test_feed_preserves_provider_quote_time_instead_of_making_it_fresh(monkeypatch):
    captured = []
    monkeypatch.setattr(price_feed, "_feed_snapshot", lambda _: configured_run())
    monkeypatch.setattr(price_feed, "record_activity", lambda *_: None)
    monkeypatch.setattr(
        price_feed,
        "ingest_observations",
        lambda _, rows: captured.extend(rows) or {"received": len(rows)},
    )
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbols[0]: 10},
            "as_of": {symbols[0]: "2026-01-05T15:00:00Z"},
        },
    )
    assert price_feed.refresh_run_market_data("synthetic")["received"] == 1
    assert captured[0]["timestamp"] == "2026-01-05T15:00:00Z"
    assert captured[0]["available_at"] != captured[0]["timestamp"]


def test_missing_provider_timestamp_does_not_create_a_tradeable_observation(monkeypatch):
    monkeypatch.setattr(price_feed, "_feed_snapshot", lambda _: configured_run())
    monkeypatch.setattr(price_feed, "record_activity", lambda *_: None)
    monkeypatch.setattr(
        price_feed, "ingest_observations", lambda *_: pytest.fail("invented timestamp")
    )
    monkeypatch.setattr(
        price_feed.prices, "fetch_yahoo_quotes", lambda symbols: {"prices": {symbols[0]: 10}}
    )
    result = price_feed.refresh_run_market_data("synthetic")
    assert result["received"] == 0
    assert result["missing"] == ["EXAMPLE-USD"]


def test_current_feed_reads_persisted_private_dataset_and_repeated_quote_is_idempotent(
    db_isolated, monkeypatch
):
    from datetime import datetime, timezone

    from backend.services import trading
    from tests.test_trading_workspace import make_run

    run = make_run(mode="paper", mandate=configured_run()["workspace_snapshot"]["mandate"])
    assert "observations" not in trading.get_run(run["id"])
    stamp = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {
            "prices": {symbols[0]: 10},
            "as_of": {symbols[0]: stamp},
        },
    )
    assert price_feed.refresh_run_market_data(run["id"])["received"] == 1
    assert price_feed.refresh_run_market_data(run["id"])["received"] == 1
    items = trading.get_market_data(run["id"])["items"]
    assert sum(row["instrument"]["symbol"] == "EXAMPLE/USD" for row in items) == 1
