"""The demo book must carry investment theses, rebuilt whole by every nightly build.

The hosted demo is thrown away and regenerated daily, so a thesis added to it by hand vanishes
the next morning. These tests pin what the SEED must produce: a thesis for the holdings, written
through the real choke, that survives the full build and reads sensibly against the book's own
prices.

Two builds, because they answer different questions:

* the FULL book at one anchor (the expensive one, like ``tests/test_demo_book.py``) — the theses
  exist for held instruments and nothing later in the build removes them;
* a LIGHT build (instruments + price history + the Singapore ETFs, then ``theses.seed``) at two
  anchors, the second with every price scaled the way an online build anchored on a different
  live quote would be — so a level that was hard-coded rather than derived from the series
  cannot hide.
"""

from __future__ import annotations

import shutil
from datetime import date

import pytest

FULL_ANCHOR = date(2026, 8, 23)
# (anchor, price scale): the second stands in for an online build whose live quotes differ.
LIGHT_BUILDS = [(date(2026, 8, 23), 1.0), (date(2026, 3, 2), 1.6)]
UNCOVERED = {"BND", "ETH"}


def _isolate(tmp_path_factory, name: str):
    """Redirect the engine to a fresh migrated database, with the same isolation (and for the
    same reasons) as ``tests/test_demo_book.py``. Returns (monkeypatch, live db, golden db)."""
    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from backend.services import fx_cache

    tmp = tmp_path_factory.mktemp(name)
    live, golden = tmp / f"{name}.db", tmp / f"{name}.golden.db"
    url = f"sqlite:///{live}"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("ONW_DATABASE_URL", url)
    monkey.setenv("ONW_QA_MODE", "1")
    import backend.core.config as _config

    monkey.setattr(_config, "_settings", None)
    monkey.setattr(fx_cache, "_FX_CACHE_FILE", tmp / "fx_cache.json")
    eng.dispose_engine()
    command.upgrade(make_config(url), "head")
    return monkey, live, golden


@pytest.fixture(scope="module")
def _full(tmp_path_factory):
    from backend.db import engine as eng

    monkey, live, golden = _isolate(tmp_path_factory, "full")
    try:
        from backend.scripts.demo.build import build

        report = build(today=FULL_ANCHOR, offline=True)
        eng.dispose_engine()
        shutil.copyfile(live, golden)
        yield {"report": report, "live": live, "golden": golden}
    finally:
        eng.dispose_engine()
        monkey.undo()
        eng.dispose_engine()


@pytest.fixture(scope="module", params=LIGHT_BUILDS, ids=lambda b: f"{b[0].isoformat()}x{b[1]}")
def _light(request, tmp_path_factory):
    from backend.db import engine as eng

    anchor, scale = request.param
    monkey, live, golden = _isolate(tmp_path_factory, "light")
    try:
        from backend.scripts.demo import investments, markets, singapore, theses
        from backend.scripts.demo._timeline import Timeline

        monkey.setattr(
            markets,
            "live_anchors",
            lambda specs=None: {
                s["symbol"]: s["anchor"] * scale
                for s in (markets.INSTRUMENTS if specs is None else specs)
            },
        )
        tl = Timeline.anchored_on(anchor)
        investments.create_instruments(tl)
        anchors = markets.live_anchors()
        investments.record_history(
            {
                s["symbol"]: markets.price_series(s, tl, anchors[s["symbol"]])
                for s in markets.INSTRUMENTS
            }
        )
        singapore._seed_etfs(tl, offline=False)
        written = theses.seed(tl)
        eng.dispose_engine()
        shutil.copyfile(live, golden)
        yield {"tl": tl, "scale": scale, "written": written, "live": live, "golden": golden}
    finally:
        eng.dispose_engine()
        monkey.undo()
        eng.dispose_engine()


def _restore(built) -> None:
    """The suite's autouse fixtures reset tables on the live engine; put the golden book back."""
    from backend.db import engine as eng

    eng.dispose_engine()
    shutil.copyfile(built["golden"], built["live"])


@pytest.fixture
def full_book(_full):
    _restore(_full)
    return _full["report"]


@pytest.fixture
def light_book(_light):
    _restore(_light)
    return _light


def _by_ticker() -> dict[str, dict]:
    """Every thesis keyed by its instrument's SYMBOL (``list_theses`` keys on the price source,
    which for a coin is "BITCOIN"), each carrying its instrument's ``price_source_id``."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services import investment_theses

    instruments = {i["id"]: i for i in InstrumentStore().all()}
    out = {}
    for t in investment_theses.list_theses():
        inst = instruments[t["instrument_id"]]
        out[inst["symbol"]] = {**t, "price_source_id": inst["price_source_id"]}
    return out


def _close_on(tl, thesis: dict) -> float:
    from backend.db.price_history_store import PriceHistoryStore
    from backend.scripts.demo import markets

    return markets.price_on(PriceHistoryStore().series(thesis["price_source_id"]), tl.today)


# ── The full build ────────────────────────────────────────────────────────────


def test_the_full_build_writes_the_theses_and_they_survive_it(full_book):
    from backend.scripts.demo import theses

    found = _by_ticker()
    assert full_book["theses"] == len(theses.THESES) == len(found) >= 8
    assert set(found) == {spec[0] for spec in theses.THESES}


def test_every_thesis_is_on_a_holding_the_book_actually_holds(full_book):
    found = _by_ticker()
    assert all(t["held"] for t in found.values()), [k for k, t in found.items() if not t["held"]]
    assert not UNCOVERED & set(found), "BND and ETH are left without a thesis on purpose"


def test_the_theses_span_roles_and_cover_a_singapore_etf(full_book):
    found = _by_ticker()
    assert {t["role"] for t in found.values()} == {"core", "satellite", "speculative"}
    assert {"ES3.SI", "EUNL.DE"} <= set(found)
    assert found["ES3.SI"]["currency"] == "SGD" and found["EUNL.DE"]["currency"] == "EUR"


def test_a_thesis_is_a_real_one_not_a_stub(full_book):
    from backend.services import thesis_judgment

    for ticker, t in _by_ticker().items():
        assert len(t["reason"]) > 60 and len(t["breaks_if"]) > 30, ticker
        assert thesis_judgment.holding_period(t["reason"]), f"{ticker} states no holding period"


def test_a_diversified_index_fund_has_no_stop(full_book):
    found = _by_ticker()
    for ticker in ("VOO", "VXUS", "ES3.SI", "EUNL.DE"):
        assert found[ticker].get("stop_below") is None, ticker


def test_the_employer_thesis_names_the_last_vest_year_from_the_ladder(full_book):
    from backend.db.grant_store import GrantStore

    last = max(tr["vest_date"] for g in GrantStore().all() for tr in (g.get("tranches") or []))
    assert last[:4] in _by_ticker()["NET"]["reason"]


def test_a_trim_level_matches_the_price_alert_on_the_same_ticker(full_book):
    from backend.db.alert_store import AlertStore

    found = _by_ticker()
    checked = 0
    for alert in AlertStore().all():
        thesis = found.get(alert["ticker"])
        if alert["op"] == ">=" and thesis and thesis.get("trim_above"):
            assert thesis["trim_above"] == pytest.approx(alert["value"], rel=0.015), alert["ticker"]
            checked += 1
    assert checked >= 5


# ── Levels follow the book's own prices, at any anchor ───────────────────────


def test_levels_are_consistent_with_the_close_at_the_anchor(light_book):
    from backend.services import thesis_judgment

    tl, stances = light_book["tl"], set()
    found = _by_ticker()
    assert light_book["written"] == len(found)
    for ticker, thesis in found.items():
        close = _close_on(tl, thesis)
        j = thesis_judgment.judge(thesis, close, held=True, currency=thesis["currency"])
        for key, level in j["levels"].items():
            assert 0.5 * close <= level["price"] <= 1.5 * close, (ticker, key)
        assert not j["sell_ok"] and not j["trim_ok"], f"{ticker} opens the demo past a level"
        stances.add(j["stance"])
    assert len(stances) >= 2, "the demo should not read the same on every ticker"


def test_levels_scale_with_the_price_the_history_was_anchored_on(light_book):
    tl, scale = light_book["tl"], light_book["scale"]
    found = _by_ticker()
    aapl = found["AAPL"]
    close = _close_on(tl, aapl)
    assert close == pytest.approx(241.0 * scale, rel=0.01)
    assert aapl["trim_above"] == pytest.approx(close * 1.15, rel=0.01)
    assert aapl["buy_below"] == pytest.approx(close * 0.88, rel=0.01)
    btc = found["BTC"]
    assert btc["trim_above"] == pytest.approx(_close_on(tl, btc) * 1.30, rel=0.01)


def test_the_rounded_levels_keep_their_order(light_book):
    for ticker, t in _by_ticker().items():
        if t.get("stop_below") and t.get("trim_above"):
            assert t["stop_below"] < t["trim_above"], ticker
        if t.get("buy_above"):
            assert t["buy_above"] < t["buy_below"], ticker
