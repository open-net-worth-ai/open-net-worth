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
