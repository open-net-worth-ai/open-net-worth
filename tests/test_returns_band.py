"""Returns for one asset type of the book (``portfolio_returns(band=...)``): that band's value
each day and only that band's buys and sells, so money moved between types is a withdrawal from
one slice and a deposit into the other, never a gain or a loss of either (2026-11-13)."""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import portfolio_history as ph
from backend.services import returns as r

# Day 1: 100 in stocks, 100 in crypto. Day 2: 50 of stocks sold and the 50 put into crypto, at
# unchanged prices. Day 3: stocks +10%, crypto flat.
_SERIES = [
    {"date": "2026-03-01", "value_base": 200.0, "classes": {"equity": 100.0, "crypto": 100.0}},
    {"date": "2026-03-02", "value_base": 200.0, "classes": {"equity": 50.0, "crypto": 150.0}},
    {"date": "2026-03-03", "value_base": 205.0, "classes": {"equity": 55.0, "crypto": 150.0}},
]
_FLOWS = [
    ("2026-03-02", 50.0, "inst_stock"),  # the sale: proceeds back to the pocket
    ("2026-03-02", -50.0, "inst_coin"),  # the buy: cash out of the pocket
]


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setattr(
        ph, "portfolio_value_series", lambda *a, **k: {"series": _SERIES, "data_quality": {}}
    )
    monkeypatch.setattr(ph, "inception_date", lambda: "2026-03-01")
    monkeypatch.setattr(r.PriceHistoryStore, "earliest_date", lambda self: "2026-03-01")
    monkeypatch.setattr(r, "base_currency", lambda: "USD")
    monkeypatch.setattr(r, "_load_rates_safe", lambda loader: {})
    monkeypatch.setattr(r, "tagged_cash_flows", lambda *a, **k: (list(_FLOWS), False))
    monkeypatch.setattr(r, "cash_flows", lambda *a, **k: ([(d, v) for d, v, _ in _FLOWS], False))
    monkeypatch.setattr(
        r, "_instrument_bands", lambda: {"inst_stock": "equity", "inst_coin": "crypto"}
    )


def _twr(band):
    out = r.portfolio_returns(date_from="2026-03-01", date_to="2026-03-03", band=band)
    return out, out["twr"]["period_return"]


def test_stocks_slice_gains_only_what_stocks_earned(book):
    out, ret = _twr("equity")
    # The sale is a withdrawal, not a 50% loss: only day 3's +10% is a return.
    assert ret == pytest.approx(0.10, abs=1e-6)
    assert out["band"] == "equity"


def test_crypto_slice_is_flat_though_it_doubled_in_size(book):
    _out, ret = _twr("crypto")
    # The 50 moved in is a deposit, not a 50% gain.
    assert ret == pytest.approx(0.0, abs=1e-6)


def test_whole_book_is_unchanged_by_the_option(book):
    out, ret = _twr(None)
    assert out["band"] is None
    assert ret == pytest.approx(0.025, abs=1e-6)  # 200 → 205, the move between types nets out


def test_bands_held_lists_what_the_book_holds_largest_first(book):
    out, _ret = _twr(None)
    assert out["bands_held"] == ["crypto", "equity"]


def test_an_unknown_asset_type_is_refused(book):
    with pytest.raises(ValidationError):
        r.portfolio_returns(date_from="2026-03-01", date_to="2026-03-03", band="bond")


# ── All assets: the book plus owned assets ─────────────────────────────────────────────────
# A house bought on day 2 for 1000 (cost), revalued to 1100 on day 3. The investments are flat.

_FLAT = [
    {"date": f"2026-03-0{d}", "value_base": 200.0, "classes": {"equity": 200.0}} for d in (1, 2, 3)
]
_HOUSE = {
    "id": "asset_house",
    "category": "property",
    "currency": "USD",
    "cost": 1000.0,
    "cost_date": "2026-03-02",
    "marks": [{"id": "m1", "date": "2026-03-03", "value": 1100.0}],
}


def _all_assets(monkeypatch, assets, series=_FLAT, rates=None):
    monkeypatch.setattr(
        ph, "portfolio_value_series", lambda *a, **k: {"series": series, "data_quality": {}}
    )
    monkeypatch.setattr(ph, "inception_date", lambda: "2026-03-01")
    monkeypatch.setattr(r.PriceHistoryStore, "earliest_date", lambda self: "2026-03-01")
    monkeypatch.setattr(r, "base_currency", lambda: "USD")
    monkeypatch.setattr(r, "_load_rates_safe", lambda loader: dict(rates or {}))
    monkeypatch.setattr(r, "cash_flows", lambda *a, **k: ([], False))
    monkeypatch.setattr(
        r.owned_assets, "OwnedAssetsStore", lambda: type("S", (), {"all": lambda s: assets})()
    )
    return r.portfolio_returns(date_from="2026-03-01", date_to="2026-03-03", band="all_assets")


def test_a_purchase_is_neither_gain_nor_loss_and_a_revaluation_is_return(monkeypatch):
    out = _all_assets(monkeypatch, [_HOUSE])
    assert [p["value"] for p in out["_value_pts"]] == [200.0, 1200.0, 1300.0]
    assert out["_flows"] == [("2026-03-02", -1000.0)]
    # Only the +100 revaluation on 1200 is a return; the purchase day is flat.
    assert out["twr"]["period_return"] == pytest.approx(100 / 1200, abs=1e-6)
    assert out["period_gain_base"] == pytest.approx(100.0)
    assert out["has_owned_assets"] is True


def test_a_sold_asset_returns_its_proceeds_not_a_loss(monkeypatch):
    sold = {**_HOUSE, "disposed_on": "2026-03-03", "disposal_proceeds": 1000.0}
    out = _all_assets(monkeypatch, [sold])
    assert [p["value"] for p in out["_value_pts"]] == [200.0, 1200.0, 200.0]
    assert out["period_gain_base"] == pytest.approx(0.0)


def test_a_foreign_sale_is_returned_in_base_currency(monkeypatch):
    from backend.core.fx import to_base

    rates = {"USD": 1.0, "EUR": 0.5}
    eur = {**_HOUSE, "currency": "EUR", "disposed_on": "2026-03-03", "disposal_proceeds": 1000.0}
    out = _all_assets(monkeypatch, [eur], rates=rates)
    cost = to_base(1000.0, "EUR", "USD", rates)
    assert cost != 1000.0
    assert out["_flows"] == [("2026-03-02", -cost), ("2026-03-03", cost)]
    # Sold for what it cost: no gain, whatever the currency.
    assert out["period_gain_base"] == pytest.approx(0.0)


def test_an_asset_nothing_values_is_left_out(monkeypatch):
    out = _all_assets(monkeypatch, [{**_HOUSE, "marks": [], "cost": None}])
    assert out["has_owned_assets"] is False
    assert out["period_gain_base"] == pytest.approx(0.0)


def test_other_bands_do_not_touch_owned_assets(book, monkeypatch):
    monkeypatch.setattr(
        r.owned_assets, "OwnedAssetsStore", lambda: type("S", (), {"all": lambda s: [_HOUSE]})()
    )
    out, ret = _twr("equity")
    assert ret == pytest.approx(0.10, abs=1e-6)
