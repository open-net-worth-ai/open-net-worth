import pytest

from backend.core.errors import ConflictError
from backend.services.trading import price_feed


@pytest.fixture(autouse=True)
def no_market_network(monkeypatch):
    monkeypatch.setattr(price_feed.prices, "fetch_history_result", lambda *_: {"points": []})


def configured_run(mode="paper"):
    return {
        "mode": mode,
        "observations": [],
        "accounts": [{"venue": "simulation", "currency": "USD"}],
        "workspace_snapshot": {
            "mandate": {
                "market_data_source": "yahoo",
                "allowed_asset_classes": ["stock", "crypto"],
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


def test_legacy_repeated_quote_preserves_first_metadata_and_still_detects_price_changes(
    db_isolated, monkeypatch
):
    from datetime import datetime, timezone

    from backend.services import trading
    from tests.test_trading_workspace import make_run

    stamp = datetime.now(timezone.utc).isoformat()
    instrument = configured_run()["workspace_snapshot"]["mandate"]["instruments"][0]
    run = make_run(
        mode="paper",
        mandate=configured_run()["workspace_snapshot"]["mandate"],
        observations=[{"instrument": instrument, "timestamp": stamp, "price": "10"}],
    )
    quotes = {"prices": {"EXAMPLE-USD": 10}, "as_of": {"EXAMPLE-USD": stamp}}
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", lambda _: quotes)
    assert price_feed.refresh_run_market_data(run["id"])["received"] == 1
    row = trading.get_market_data(run["id"])["items"][0]
    assert row["source"] == "supplied"
    assert row["received_at"] is None
    quotes["prices"]["EXAMPLE-USD"] = 11
    with pytest.raises(ConflictError, match="rewritten"):
        price_feed.refresh_run_market_data(run["id"])


def test_feed_captures_actual_provider_bars_but_not_incomplete_or_naive_time_bars(monkeypatch):
    from datetime import datetime, timedelta, timezone

    captured = []
    current = datetime.now(timezone.utc)
    monkeypatch.setattr(price_feed, "_feed_snapshot", lambda _: configured_run())
    monkeypatch.setattr(price_feed, "record_activity", lambda *_: None)
    monkeypatch.setattr(
        price_feed,
        "ingest_observations",
        lambda _, rows: captured.extend(rows) or {"received": len(rows)},
    )
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", lambda _: {})
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_history_result",
        lambda *_: {
            "points": [
                {"date": date, "open": 9, "high": 11, "low": 8, "close": 10, "volume": 100}
                for date in (
                    (current - timedelta(minutes=10)).isoformat(),
                    current.isoformat(),
                    "2026-01-01",
                )
            ]
        },
    )
    assert price_feed.refresh_run_market_data("synthetic")["received"] == 1
    assert captured[0]["source"] == "yahoo"
    assert captured[0]["open"] == "9"
    assert captured[0]["price"] == captured[0]["close"] == "10"
    assert captured[0]["available_at"] == captured[0]["received_at"]


def test_provider_bar_correction_does_not_rewrite_first_recorded_evidence(db_isolated, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from backend.services import trading
    from tests.test_trading_workspace import make_run

    run = make_run(mode="paper", mandate=configured_run()["workspace_snapshot"]["mandate"])
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    value = {"date": stamp, "open": 9, "high": 12, "low": 8, "close": 10, "volume": 100}
    monkeypatch.setattr(price_feed.prices, "fetch_yahoo_quotes", lambda _: {})
    monkeypatch.setattr(price_feed.prices, "fetch_history_result", lambda *_: {"points": [value]})
    assert price_feed.refresh_run_market_data(run["id"])["received"] == 1
    first = trading.get_market_data(run["id"])["items"][-1]
    value["close"] = 11
    assert price_feed.refresh_run_market_data(run["id"])["received"] == 0
    assert trading.get_market_data(run["id"])["items"][-1] == first
    event = trading.list_events(run["id"])["items"][-1]
    assert event["payload"]["bar_revisions_retained_original"] == 1


def test_bar_failure_does_not_discard_a_valid_quote(monkeypatch):
    captured, events = [], []
    monkeypatch.setattr(price_feed, "_feed_snapshot", lambda _: configured_run())
    monkeypatch.setattr(price_feed, "record_activity", lambda *args: events.append(args))
    monkeypatch.setattr(
        price_feed,
        "ingest_observations",
        lambda _, rows: captured.extend(rows) or {"received": len(rows)},
    )
    monkeypatch.setattr(
        price_feed.prices,
        "fetch_yahoo_quotes",
        lambda symbols: {"prices": {symbols[0]: 10}, "as_of": {symbols[0]: "2026-01-05T15:00:00Z"}},
    )

    def unavailable(*_):
        raise TimeoutError("synthetic provider failure")

    monkeypatch.setattr(price_feed.prices, "fetch_history_result", unavailable)
    assert price_feed.refresh_run_market_data("synthetic")["received"] == 1
    assert captured[0]["source"] == "yahoo"
    assert captured[0]["received_at"]
    assert events[0][1] == "market_bars_unavailable"
