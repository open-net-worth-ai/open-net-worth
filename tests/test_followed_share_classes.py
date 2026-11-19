"""One company is one holding: a fund's share classes of the same issuer read as one row, and
each ticker's exchange is its own quote's, never a guess."""

from __future__ import annotations

from backend.services.followed_portfolios import performance, share_classes


def _pos(key, symbol, value, cls):
    return {"key": key, "symbol": symbol, "value": value, "qty": value / 10, "share_class": cls}


ROW = {
    "current": {
        "positions": [
            _pos("000001107", "ACMC", 70.0, "C"),
            _pos("000001305", "ACMA", 30.0, "A"),
            # A warrant from the same issuer names no class: it stays its own line.
            _pos("000001115", "ACMW", 5.0, None),
            _pos("000002104", "BOLT", 50.0, None),
        ]
    },
    "previous": None,
    "history": [
        {
            "as_of": "2026-03-31",
            "filed_at": "2026-05-15",
            "qty": {"ACMA": 3.0, "ACMC": 7.0},
            "value": {"ACMA": 30.0, "ACMC": 70.0},
        }
    ],
    "activity": [{"symbol": "ACMA", "date": "2026-04-01"}],
}


def test_classes_of_one_issuer_read_under_the_larger_class():
    assert share_classes.lead_symbols(ROW) == {"ACMA": "ACMC"}
    row = share_classes.grouped(ROW)
    assert [p["symbol"] for p in row["current"]["positions"]] == ["ACMC", "ACMC", "ACMW", "BOLT"]
    assert row["history"][0]["value"] == {"ACMC": 100.0}
    assert row["activity"][0]["symbol"] == "ACMC"


def test_a_row_with_nothing_to_fold_is_returned_as_is():
    row = {"current": {"positions": [_pos("000002104", "BOLT", 50.0, None)]}}
    assert share_classes.grouped(row) is row


def test_exchange_is_the_tickers_own_quote_or_nothing(monkeypatch):
    monkeypatch.setattr(
        performance.prices,
        "fetch_quote_snapshots",
        lambda symbols: {"ACME": {"exchange": "NasdaqGS"}, "BOLT": {"price": 1.0}},
    )
    assert performance.listing_exchanges(["ACME", "BOLT", ""]) == {"ACME": "NasdaqGS"}


def test_a_quote_failure_shows_no_exchange_and_never_fails_the_page(monkeypatch):
    def boom(symbols):
        raise RuntimeError("down")

    monkeypatch.setattr(performance.prices, "fetch_quote_snapshots", boom)
    assert performance.listing_exchanges(["ACME"]) == {}
