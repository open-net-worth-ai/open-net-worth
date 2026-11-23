"""A benchmark the owner searched for (issue #223): any ticker the app can price may be the ONE
benchmark, alongside the grouped regional roster. Synthetic instruments only (ACME)."""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError, ValidationError


@pytest.fixture()
def store(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    return InstrumentStore()


def _acme(store, **over) -> dict:
    spec = {
        "symbol": "ACME.DE",
        "name": "ACME Europe Index ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "EUR",
        "price_source": "yahoo",
        "price_source_id": "ACME.DE",
        **over,
    }
    inst, _ = store.find_or_create(spec)
    return inst


def test_roster_is_grouped_by_region_in_picker_order(db_isolated, monkeypatch):
    from backend.services import risk_metrics

    monkeypatch.setattr(risk_metrics, "preferred_benchmark", lambda profile=None: "SPY")
    groups = [e["group"] for e in risk_metrics.benchmark_roster()]
    order = list(dict.fromkeys(g for g in groups if g))
    assert order == ["US", "World", "Europe", "Asia & emerging", "Crypto"]
    assert groups[-1] == ""  # "No benchmark" last


def test_a_priced_ticker_is_a_valid_benchmark(store):
    from backend.services.risk_metrics import is_benchmark_key, preferred_benchmark

    _acme(store)
    assert is_benchmark_key("ACME.DE")
    assert is_benchmark_key("EXS1") and is_benchmark_key("")
    assert not is_benchmark_key("NOPE.XX")
    assert preferred_benchmark({"preferred_benchmark": "ACME.DE"}) == "ACME.DE"


def test_an_unpriced_or_unknown_benchmark_falls_back_to_spy(store):
    from backend.services.risk_metrics import is_benchmark_key, preferred_benchmark

    _acme(store, symbol="ACMEM", price_source="manual", price_source_id="")
    assert not is_benchmark_key("ACMEM")
    assert preferred_benchmark({"preferred_benchmark": "ACMEM"}) == "SPY"
    assert preferred_benchmark({"preferred_benchmark": "NOPE.XX"}) == "SPY"


def test_the_picker_names_a_typed_in_benchmark_first(store, monkeypatch):
    from backend.services import risk_metrics

    _acme(store)
    monkeypatch.setattr(risk_metrics, "preferred_benchmark", lambda profile=None: "ACME.DE")
    first = risk_metrics.benchmark_roster()[0]
    assert first == {
        "key": "ACME.DE",
        "label": "ACME.DE • ACME Europe Index ETF",
        "symbol": "ACME.DE",
        "group": "Your benchmark",
    }


def test_prepare_fetches_history_and_returns_the_entry(store, monkeypatch):
    from backend.services.benchmark_seed import prepare_custom_benchmark

    fetched: list[str] = []
    monkeypatch.setattr(
        "backend.services.prices.backfill_history", lambda pid: fetched.append(pid) or 10
    )
    inst = _acme(store)
    entry = prepare_custom_benchmark(inst["id"])
    assert fetched == ["ACME.DE"]
    assert entry["key"] == "ACME.DE" and entry["group"] == "Your benchmark"


def test_prepare_refuses_what_cannot_be_compared(store, monkeypatch):
    from backend.services.benchmark_seed import prepare_custom_benchmark

    monkeypatch.setattr("backend.services.prices.backfill_history", lambda pid: 0)
    manual = _acme(store, symbol="ACMEM", price_source="manual", price_source_id="acme-manual")
    with pytest.raises(ValidationError):
        prepare_custom_benchmark(manual["id"])
    with pytest.raises(NotFoundError):
        prepare_custom_benchmark("ins_missing")


def test_prepare_on_a_roster_listing_returns_the_roster_entry(store, monkeypatch):
    from backend.services.benchmark_seed import prepare_custom_benchmark, seed_benchmark_instruments

    monkeypatch.setattr("backend.services.prices.backfill_history", lambda pid: 0)
    seed_benchmark_instruments()
    dax = store.find_by_symbol("EXS1")
    assert prepare_custom_benchmark(dax["id"])["group"] == "Europe"


def test_a_typed_in_benchmark_is_kept_fresh_with_the_others(store, monkeypatch):
    from backend.services import benchmark_seed, risk_metrics

    _acme(store)
    monkeypatch.setattr(risk_metrics, "preferred_benchmark", lambda profile=None: "ACME.DE")
    assert "ACME.DE" in benchmark_seed.reference_price_ids()
    monkeypatch.setattr(risk_metrics, "preferred_benchmark", lambda profile=None: "SPY")
    assert "ACME.DE" not in benchmark_seed.reference_price_ids()


def test_followed_portfolios_compare_against_the_typed_in_listing(store):
    from backend.services.followed_portfolios.performance import _benchmark_symbol

    _acme(store)
    _acme(
        store,
        symbol="ACMEC",
        asset_class="crypto",
        price_source="coingecko",
        price_source_id="acme-coin",
    )
    assert _benchmark_symbol("ACME.DE") == "ACME.DE"
    assert _benchmark_symbol("ACMEC") is None  # a coin is no yardstick for a stock portfolio
    assert _benchmark_symbol("SPY") == "SPY"
    assert _benchmark_symbol("BTC") is None


def test_profile_accepts_a_priced_ticker_and_refuses_an_unknown_one(store):
    from pydantic import ValidationError as PydanticValidationError

    from backend.api.profile import ProfileBody

    _acme(store)
    assert ProfileBody(preferred_benchmark="ACME.DE").preferred_benchmark == "ACME.DE"
    assert ProfileBody(preferred_benchmark="EXW1").preferred_benchmark == "EXW1"
    with pytest.raises(PydanticValidationError):
        ProfileBody(preferred_benchmark="NOPE.XX")


def test_a_roster_key_finds_a_fund_already_held_under_its_exchange_symbol(store):
    """A European book that already held the MSCI World fund as `EUNL.DE` keeps that record when
    the roster is seeded (find_or_create dedupes on the listing), so the roster key `EUNL` must
    find it by listing — by symbol it never would."""
    from backend.services.benchmark_seed import seed_benchmark_instruments
    from backend.services.returns import _resolve_benchmark_instrument
    from backend.services.risk_metrics import benchmark_instrument

    held = _acme(store, symbol="EUNL.DE", name="Held MSCI World", price_source_id="EUNL.DE")
    seed_benchmark_instruments()
    assert benchmark_instrument("EUNL")["id"] == held["id"]
    assert _resolve_benchmark_instrument("EUNL")["id"] == held["id"]
