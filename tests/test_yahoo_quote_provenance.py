"""The direct quote facade reports source units and actual selected-price time provenance."""

from datetime import datetime, timezone

import pytest

from backend.services import prices


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    monkeypatch.setattr(prices, "_cache_market_from_quotes", lambda *_: None)
    monkeypatch.setattr(prices, "_extended_hours_from_quote", lambda *_: {})


@pytest.mark.parametrize("currency", ["USD", "GBP", "GBp", None])
def test_batch_currency_preserves_provider_unit_and_verified_observed_time(monkeypatch, currency):
    stamp = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(
        prices,
        "_yf_batch_quote",
        lambda _: {
            "ACME": {"regularMarketPrice": 75, "regularMarketTime": stamp, "currency": currency}
        },
    )
    monkeypatch.setattr(
        prices, "_fetch_one", lambda *_: pytest.fail("Verified batch already has the price")
    )
    result = prices.fetch_yahoo_quotes(["ACME"])
    assert result["currencies"]["ACME"] == currency
    assert result["timestamp_verified"]["ACME"] is True
    assert result["as_of"]["ACME"] == datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def test_unstamped_fallback_never_claims_verified_time_or_currency(monkeypatch):
    monkeypatch.setattr(prices, "_yf_batch_quote", lambda _: {})
    monkeypatch.setattr(prices, "_fetch_one", lambda sym: (sym, 75, 74))
    result = prices.fetch_yahoo_quotes(["ACME"])
    assert result["prices"]["ACME"] == 75
    assert result["as_of"]["ACME"] is not None
    assert result["timestamp_verified"]["ACME"] is False
    assert result["currencies"]["ACME"] is None


def test_invalid_batch_price_cannot_lend_timestamp_to_fallback_price(monkeypatch):
    stamp = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(
        prices,
        "_yf_batch_quote",
        lambda _: {
            "ACME": {"regularMarketPrice": None, "regularMarketTime": stamp, "currency": "USD"}
        },
    )
    monkeypatch.setattr(prices, "_fetch_one", lambda sym: (sym, 75, 74))
    result = prices.fetch_yahoo_quotes(["ACME"])
    assert result["currencies"]["ACME"] == "USD"
    assert result["timestamp_verified"]["ACME"] is False
