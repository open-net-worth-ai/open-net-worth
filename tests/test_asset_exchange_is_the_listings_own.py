"""A stock's exchange is the one its OWN ticker's quote names, never a name search's guess.

The scar: the asset page filled a missing exchange from a symbol search and saved the first
result. A bare ticker's first result can be another company: "WBD" (a Nasdaq stock) came back
as a Milan listing, was saved, and the page and Lucius's lookup then named the wrong market,
which is how an order lands on the wrong listing."""

from __future__ import annotations

import pytest

from backend.services import asset_detail


class _Store:
    def __init__(self):
        self.touched: list[tuple[str, dict]] = []

    def touch(self, instrument_id, changes):
        self.touched.append((instrument_id, changes))


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(asset_detail, "InstrumentStore", lambda: s)
    return s


def _inst(**ident):
    return {"price_source_id": "ACME", "asset_class": "equity", "identifiers": ident}


def _quotes(monkeypatch, answer):
    calls = []

    def fake(symbols):
        calls.append(list(symbols))
        return answer

    monkeypatch.setattr("backend.services.prices.fetch_quote_snapshots", fake)
    # A search is never asked: its first result can be another company's listing.
    monkeypatch.setattr(
        "backend.services.prices.search_symbol",
        lambda q: {"symbol": "ACME.MI", "exchange": "Milan"},
    )
    return calls


def test_a_label_saved_from_a_search_is_replaced_by_the_tickers_own(monkeypatch, store):
    _quotes(monkeypatch, {"ACME": {"exchange": "NasdaqGS"}})
    inst = _inst(exchange="Milan", isin="XX0000000000")
    got = asset_detail._listing_exchange("inst-1", inst, inst["identifiers"], persist=True)
    assert got == "NasdaqGS"
    assert store.touched == [
        (
            "inst-1",
            {
                "identifiers": {
                    "isin": "XX0000000000",
                    "exchange_source": "quote",
                    "exchange": "NasdaqGS",
                }
            },
        )
    ]


def test_no_quote_means_no_exchange_never_the_unchecked_one(monkeypatch, store):
    _quotes(monkeypatch, {})
    inst = _inst(exchange="Milan")
    assert asset_detail._listing_exchange("inst-1", inst, inst["identifiers"], persist=True) is None
    assert store.touched == []  # asked again next time


def test_a_quote_with_no_exchange_clears_a_stale_label(monkeypatch, store):
    _quotes(monkeypatch, {"ACME": {"price": 1.0}})
    inst = _inst(exchange="Milan")
    assert asset_detail._listing_exchange("inst-1", inst, inst["identifiers"], persist=True) is None
    assert store.touched == [("inst-1", {"identifiers": {"exchange_source": "quote"}})]


def test_a_confirmed_exchange_is_read_without_asking_again(monkeypatch, store):
    calls = _quotes(monkeypatch, {"ACME": {"exchange": "NYSE"}})
    inst = _inst(exchange="NasdaqGS", exchange_source="quote")
    got = asset_detail._listing_exchange("inst-1", inst, inst["identifiers"], persist=True)
    assert got == "NasdaqGS" and calls == [] and store.touched == []


def test_a_read_only_caller_gets_the_checked_exchange_and_writes_nothing(monkeypatch, store):
    _quotes(monkeypatch, {"ACME": {"exchange": "NasdaqGS"}})
    inst = _inst(exchange="Milan")
    got = asset_detail._listing_exchange("inst-1", inst, inst["identifiers"], persist=False)
    assert got == "NasdaqGS" and store.touched == []


def test_crypto_has_no_exchange_lookup(monkeypatch, store):
    calls = _quotes(monkeypatch, {"ACME": {"exchange": "NasdaqGS"}})
    inst = {**_inst(), "asset_class": "crypto"}
    assert asset_detail._listing_exchange("inst-1", inst, {}, persist=True) is None
    assert calls == []
