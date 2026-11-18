"""Your real investments through the day, on a paper portfolio's first-week chart: valued at each
chart point from the holdings of that day, and chained so a buy or a sale moves the line only by
what it then earns. Synthetic tickers and prices; no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services.paper_trading import yours_intraday


class _Stub:
    """Holdings by day, and a price per moment for one synthetic ticker, ACME."""

    def __init__(self, held: dict[str, dict[str, float]], price: dict[str, float]):
        self.held, self.price = held, price

    def holdings(self, day):
        return self.held[day]

    def value(self, held, t, day):
        return held.get("acme", 0.0) * self.price[t.isoformat()]


def _moments(*stamps):
    return [(s, datetime.fromisoformat(s)) for s in stamps]


T = [f"2026-11-1{d}T{h:02d}:00:00+00:00" for d, h in ((6, 14), (6, 18), (7, 14), (7, 18))]


def test_a_steady_book_moves_with_its_prices():
    stub = _Stub({"d1": {"acme": 10}}, dict(zip(T[:3], (100.0, 110.0, 99.0))))
    day_of = dict.fromkeys(T[:3], "d1")
    out = yours_intraday._chain(stub, _moments(*T[:3]), day_of)
    assert out == {T[0]: 0.0, T[1]: 0.1, T[2]: -0.01}


def test_a_buy_is_not_a_gain():
    # Day 2 doubles the holding: the line keeps its level and then moves with the price only.
    stub = _Stub(
        {"d1": {"acme": 10}, "d2": {"acme": 20}},
        dict(zip(T, (100.0, 110.0, 110.0, 121.0))),
    )
    day_of = {T[0]: "d1", T[1]: "d1", T[2]: "d2", T[3]: "d2"}
    out = yours_intraday._chain(stub, _moments(*T), day_of)
    assert out[T[2]] == 0.1  # the buy itself adds nothing
    assert out[T[3]] == 0.21  # then +10% on the bigger holding: 1.1 × 1.1 − 1


def test_nothing_held_draws_nothing():
    stub = _Stub({"d1": {}}, dict.fromkeys(T[:2], 100.0))
    out = yours_intraday._chain(stub, _moments(*T[:2]), dict.fromkeys(T[:2], "d1"))
    assert out == {}


def test_the_qa_market_never_reads_your_book(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(yours_intraday, "get_settings", lambda: SimpleNamespace(onw_qa_mode=True))
    now = datetime(2026, 11, 16, 14, tzinfo=timezone.utc)
    stamps = [(now + timedelta(hours=h)).isoformat() for h in range(3)]
    assert yours_intraday.line(stamps) == {}
