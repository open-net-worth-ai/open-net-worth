"""Followed portfolios — the one write choke, the views, REST, the flag, the Intel story and the
Lucius tools. Information only: nothing here reads an account or sizes a trade. Sources are
faked: no test here reaches SEC or the House Clerk."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.core.errors import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from backend.db.followed_portfolio_store import MAX_FOLLOWS, FollowedPortfolioStore
from backend.db.intel_stories_store import IntelStoriesStore
from backend.services.followed_portfolios import (
    closes_store,
    edgar_13f,
    filing_store,
    service,
    signals,
)

pytestmark = pytest.mark.flags(followed_portfolios=True)


def _snap(filing_id, filed, *positions):
    rows = [
        {"key": s, "symbol": s, "name": f"{s.title()} Corp", "value": v, "qty": q}
        for s, v, q in positions
    ]
    return {
        "filing_id": filing_id,
        "as_of": filed,
        "filed_at": filed,
        "total_value": sum(r["value"] for r in rows),
        "positions": rows,
        "unread_reports": 0,
    }


class FakeSource:
    """Stands in for ``edgar_13f`` / ``house_ptr``: serves whatever ``filings`` holds."""

    def __init__(self):
        self.filings = [_snap("f1", "2026-10-01", ("ACME", 600, 60), ("BOLT", 400, 40))]
        self.error: Exception | None = None

    def fetch(self, ref, known=None, *_):
        if self.error:
            raise self.error
        current = self.filings[-1]
        if known and (known.get("current") or {}).get("filing_id") == current["filing_id"]:
            return {"unchanged": True}
        previous = self.filings[-2] if len(self.filings) > 1 else None
        return {
            "name": "Acme Capital",
            "subtitle": "13F filer",
            "current": current,
            "previous": previous,
            "cusip_map": {},
        }

    def search(self, query):
        return [
            {
                "kind": "fund_13f",
                "source_ref": "777",
                "name": "Acme Capital",
                "subtitle": "13F filer",
            }
        ]


@pytest.fixture(autouse=True)
def _filings_kept_apart(monkeypatch, tmp_path):
    from backend.services.followed_portfolios import preview

    monkeypatch.setattr(filing_store, "_DIR", tmp_path / "filings")
    monkeypatch.setattr(closes_store, "_DIR", tmp_path / "closes")
    # Each test starts with no card's last figures (they outlive the 12 hours by design).
    monkeypatch.setattr(preview, "_last", {})


@pytest.fixture
def source(db_isolated, monkeypatch):
    fake = FakeSource()
    monkeypatch.setitem(service._SOURCES, "fund_13f", fake)
    monkeypatch.setitem(service._SOURCES, "house_member", fake)
    monkeypatch.setitem(service._SOURCES, "etf_daily", fake)
    monkeypatch.setattr(service.form4, "fetch_json", lambda *a, **k: {})
    monkeypatch.setitem(service.preview._SOURCES, "fund_13f", fake)
    monkeypatch.setitem(service.preview._SOURCES, "house_member", fake)
    monkeypatch.setattr(service.house_ptr, "search", lambda q: [])
    monkeypatch.setattr(service.edgar_13f, "search", fake.search)
    monkeypatch.setattr(
        "backend.services.prices.company_names",
        lambda symbols, fetch_missing=False: {s: f"{s.title()} Incorporated" for s in symbols},
    )
    monkeypatch.setattr(
        "backend.services.prices.company_sectors",
        lambda symbols, fetch_missing=False: {s: "Technology" for s in symbols},
    )
    monkeypatch.setattr(service.preview, "_cache", {})
    monkeypatch.setattr(service.preview, "_summaries", {})
    return fake


@pytest.fixture
def client(source):
    from backend.api.followed_portfolios import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _apply(**change):
    return service.apply_followed_portfolio_change(change)


def _follow(ref="777"):
    return _apply(op="follow", kind="fund_13f", source_ref=ref)["follow"]


def call(tool, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


# ── the choke ───────────────────────────────────────────────────────────────────────


def test_follow_stores_the_model_and_hides_the_caches(source):
    follow = _follow()
    assert follow["id"].startswith("fol_")
    assert follow["status"] == "ok" and follow["error"] is None
    assert [(h["symbol"], round(h["weight"], 2)) for h in follow["model"]["holdings"]] == [
        ("ACME", 0.6),
        ("BOLT", 0.4),
    ]
    assert "cusip_map" not in follow and "current" not in follow and "reports" not in follow
    assert "sleeve" not in follow and "settings" not in follow
    assert len(FollowedPortfolioStore().all()) == 1


def test_follow_twice_is_refused(source):
    _follow()
    with pytest.raises(ConflictError, match="already follow"):
        _follow("000777")


def test_follow_refuses_a_portfolio_with_nothing_readable(source):
    source.filings = [_snap("f1", "2026-10-01")]
    with pytest.raises(ValidationError, match="no holdings"):
        _follow()
    assert FollowedPortfolioStore().all() == []


def test_follow_is_capped(source, monkeypatch):
    for i in range(MAX_FOLLOWS):
        _follow(str(1000 + i))
    with pytest.raises(ConflictError, match="Unfollow one first"):
        _follow("5")


def test_follow_source_failure_writes_nothing(source):
    source.error = ExternalServiceError("Could not reach SEC EDGAR. Try again later.")
    with pytest.raises(ExternalServiceError):
        _follow()
    assert FollowedPortfolioStore().all() == []


def test_unknown_kind_and_op_are_refused(source):
    with pytest.raises(ValidationError):
        _apply(op="follow", kind="senator", source_ref="1")
    for gone in ("launch", "update", "mark_placed"):
        with pytest.raises(ValidationError):
            _apply(op=gone, id="fol_x")
    with pytest.raises(NotFoundError):
        _apply(op="refresh", id="fol_missing")


def test_refresh_with_no_new_filing_changes_nothing_and_raises_no_story(source):
    fid = _follow()["id"]
    out = _apply(op="refresh", id=fid)
    assert out["new_filing"] is False
    assert IntelStoriesStore().all() == []


def test_refresh_new_filing_updates_the_model_and_raises_exactly_one_story(source):
    fid = _follow()["id"]
    source.filings.append(_snap("f2", "2026-11-01", ("ACME", 900, 90), ("COG", 100, 10)))
    out = _apply(op="refresh", id=fid)
    assert out["new_filing"] is True
    changes = {h["symbol"]: h["change"] for h in out["follow"]["model"]["holdings"]}
    assert changes == {"ACME": "increased", "COG": "new"}
    exited = out["follow"]["model"]["exited"]
    assert [(x["symbol"], x["prev_shares"]) for x in exited] == [("BOLT", 40.0)]
    m = out["follow"]["model"]
    assert (m["total_value"], m["prev_total_value"]) == (1000.0, 1000.0)
    assert (m["positions"], m["prev_positions"]) == (2, 2)
    stories = IntelStoriesStore().all()
    assert len(stories) == 1
    assert stories[0]["label"] == "Acme Capital filed a new 13F"
    assert "1 new, 1 increased, 1 exited" in stories[0]["current_summary"].lower()
    assert _apply(op="refresh", id=fid)["new_filing"] is False
    row = FollowedPortfolioStore().find_by_id(fid)
    assert signals.emit_new_filing_story(row, out["follow"]["model"]) is False
    assert len(IntelStoriesStore().all()) == 1


def test_refresh_failure_keeps_the_previous_model_and_shows_the_error(source):
    fid = _follow()["id"]
    source.error = ExternalServiceError("Could not reach SEC EDGAR. Try again later.")
    out = _apply(op="refresh", id=fid)["follow"]
    assert out["status"] == "error" and "SEC EDGAR" in out["error"]
    assert [h["symbol"] for h in out["model"]["holdings"]] == ["ACME", "BOLT"]
    source.error = None
    assert _apply(op="refresh", id=fid)["follow"]["status"] == "ok"


def test_refresh_never_replaces_a_good_model_with_an_empty_one(source):
    fid = _follow()["id"]
    source.filings.append(_snap("f2", "2026-11-01"))
    out = _apply(op="refresh", id=fid)
    assert out["new_filing"] is False
    assert out["follow"]["status"] == "error"
    assert [h["symbol"] for h in out["follow"]["model"]["holdings"]] == ["ACME", "BOLT"]


def test_refresh_all_survives_one_failing_follow(source, monkeypatch):
    a, b = _follow("1")["id"], _follow("2")["id"]
    real = service.apply_followed_portfolio_change

    def flaky(change):
        if change["id"] == a:
            raise RuntimeError("boom")
        return real(change)

    monkeypatch.setattr(service, "apply_followed_portfolio_change", flaky)
    assert service.refresh_all() == {"checked": 1, "new_filings": 0}
    assert b


def test_a_follow_links_to_its_filing_at_the_source(source):
    fund = {
        "kind": "fund_13f",
        "source_ref": "777",
        "current": {"filing_id": "0000000000-26-000002"},
    }
    assert (
        service.source_url(fund)
        == "https://www.sec.gov/Archives/edgar/data/777/000000000026000002/"
    )
    member = {
        "kind": "house_member",
        "source_ref": "example|pat",
        "id": "fol_x",
        "name": "Pat Example",
        "current": {"filing_id": "20000002"},
        "reports": {"20000002": {"year": 2026}},
        "activity": [
            {"doc_id": "20000002", "date": "2026-01-05"},
            {"doc_id": "../x", "date": "2026-01-06"},
        ],
    }
    url = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000002.pdf"
    assert service.source_url(member) == url
    assert [t["report_url"] for t in service.view(member)["activity"]] == [url, None]
    assert service.source_url({"kind": "fund_13f", "source_ref": "x", "current": {}}) is None


def test_preview_reads_an_unfollowed_portfolio_once_and_follows_reuse_the_stored_row(
    source, monkeypatch
):
    from backend.services.followed_portfolios import preview

    preview._cache.clear()
    fetched = []
    real = source.fetch

    quarters: list[int] = []

    def counting(ref, known=None, q=None, *_):
        fetched.append(ref)
        quarters.append(q)
        return real(ref, known)

    monkeypatch.setattr(source, "fetch", counting)
    # The page's older filings are read on a background thread; here, at once, so the test
    # sees every read in order.
    monkeypatch.setattr(
        preview, "_extend_later", lambda kind, ref: preview.preview_row(kind, ref, full=True)
    )
    priced: list[bool] = []
    monkeypatch.setattr(
        service.performance,
        "performance_for",
        lambda row, every_holding=True: (
            priced.append(every_holding)
            or {
                "benchmark": "SPY",
                "periods": {
                    "12M": {
                        "status": "ok",
                        "return": 0.12,
                        "benchmark_return": 0.08,
                        "series": [{"portfolio": i / 100} for i in range(100)],
                    }
                },
                "trade_returns": {},
            }
        ),
    )
    card = service.preview_summary("fund_13f", "777")
    assert card["follow_id"] is None and card["name"] == "Acme Capital"
    assert (card["return_12m"], card["benchmark_12m"]) == (0.12, 0.08)
    assert len(card["spark"]) == preview._SPARK_POINTS and card["holdings_count"] == 2
    assert card["top"][0]["symbol"] == "ACME"
    page = service.preview_page("fund_13f", "777")
    assert page["names"]["ACME"] == "Acme Incorporated"
    assert page["sectors"]["ACME"] == "Technology"
    assert page["follow"]["model"]["holdings"] and page["follow_id"] is None
    # The card reads the five quarters a year's return needs; the page opens on that read and
    # extends it once to the whole history (its kept filings are not downloaded again).
    assert quarters == [preview.CARD_QUARTERS, edgar_13f.HISTORY_QUARTERS]
    assert page["history_complete"] is False
    again = service.preview_page("fund_13f", "777")
    assert again["history_complete"] is True and len(fetched) == 2
    # The card and the page price only the copy's line; every holding's return is its own ask.
    assert set(priced) == {False}
    service.preview_returns("fund_13f", "777")
    assert priced[-1] is True and len(fetched) == 2
    assert FollowedPortfolioStore().all() == []
    fid = _follow()["id"]
    assert service.preview_summary("fund_13f", "777")["follow_id"] == fid
    assert len(fetched) == 3
    # A priced card's figures ride on the directory, so the whole list can sort without fetching.
    listed = service.directory("acme")["results"][0]
    assert (listed["return_12m"], listed["latest_at"]) == (0.12, card["latest_at"])
    assert card["latest_at"]


def test_price_history_cleans_the_symbol_and_refuses_free_text(client, monkeypatch):
    asked = []

    def fake(symbol, timeframe):
        asked.append((symbol, timeframe))
        return [{"date": "2026-01-02", "price": 10.123456}, {"date": "2026-01-05", "price": None}]

    monkeypatch.setattr("backend.services.prices.fetch_history", fake)
    got = client.get("/api/followed-portfolios/price-history", params={"symbol": "brk.b"}).json()
    assert got == {"symbol": "BRK-B", "points": [{"date": "2026-01-02", "price": 10.1235}]}
    assert asked == [("BRK-B", "5Y"), ("BRK-B", "1Y")]
    bad = client.get("/api/followed-portfolios/price-history", params={"symbol": "A; B"})
    assert bad.status_code == 422


def test_card_moves(source):
    from backend.services.followed_portfolios import preview

    row = {"kind": "fund_13f", "source_ref": "1067983"}
    model = {
        "holdings": [
            {
                "symbol": "ACME",
                "change": "increased",
                "change_value": 5.0,
                "shares_change_pct": 0.5,
            },
            {
                "symbol": "BOLT",
                "change": "reduced",
                "change_value": -9.0,
                "shares_change_pct": -0.2,
            },
            {"symbol": "COG", "change": "unchanged", "change_value": 0},
        ]
    }
    assert [(m["symbol"], m["verb"], m["up"]) for m in preview.latest_moves(row, model)] == [
        ("BOLT", "Trimmed", False),
        ("ACME", "Added", True),
    ]
    member = {
        "kind": "house_member",
        "source_ref": "x|y",
        "activity": [
            {"symbol": None, "counted": False, "side": "buy", "date": "2026-10-03"},
            {"symbol": "ACME", "counted": True, "side": "sell", "date": "2026-10-02"},
        ],
    }
    assert preview.latest_moves(member, None) == [
        {"symbol": "ACME", "verb": "Sold", "detail": "2026-10-02", "up": False}
    ]


def test_a_funds_card_leads_with_its_dated_trades_after_the_quarter(source):
    """A fund's dated trades (its Form 4s) after its latest quarter are what it did most
    recently: the card shows them, newest first, one per stock and direction."""
    from backend.services.followed_portfolios import preview

    model = {
        "as_of": "2026-06-30",
        "holdings": [
            {"symbol": "ACME", "change": "increased", "change_value": 5.0, "shares_change_pct": 0.5}
        ],
    }
    row = {
        "kind": "fund_13f",
        "source_ref": "1",
        "activity": [
            {"symbol": "BOLT", "counted": True, "side": "buy", "date": "2026-10-30"},
            {"symbol": "BOLT", "counted": True, "side": "buy", "date": "2026-11-03"},
            {"symbol": "CRUX", "counted": True, "side": "sell", "date": "2026-10-27"},
            {"symbol": "OLDR", "counted": True, "side": "sell", "date": "2026-05-01"},
        ],
    }
    assert preview.latest_moves(row, model) == [
        {"symbol": "BOLT", "verb": "Bought", "detail": "2026-11-03", "up": True},
        {"symbol": "CRUX", "verb": "Sold", "detail": "2026-10-27", "up": False},
    ]
    # Nothing dated after the quarter: the quarter's own changes, as before.
    row["activity"] = row["activity"][3:]
    assert [m["symbol"] for m in preview.latest_moves(row, model)] == ["ACME"]


def test_a_new_dated_trade_refreshes_a_cached_card(source, monkeypatch):
    """The card cache is keyed by the day, the quarterly filing AND the newest trade: a new
    Form 4 with no new quarter must not leave yesterday's moves on the card."""
    from backend.services.followed_portfolios import preview

    built: list[int] = []
    monkeypatch.setattr(preview, "_summaries", {})
    monkeypatch.setattr(preview, "_save_disk", lambda: None)
    monkeypatch.setattr(
        preview, "_summary", lambda row: built.append(1) or {"return_12m": 0.1, "moves": []}
    )
    row = {
        "kind": "fund_13f",
        "source_ref": "1",
        "current": {"filing_id": "q2"},
        "activity": [{"filed_at": "2026-10-29"}],
    }
    preview.summary(row)
    preview.summary(row)
    assert len(built) == 1
    row["activity"].append({"filed_at": "2026-11-03"})
    preview.summary(row)
    assert len(built) == 2


def test_a_members_whole_history_starts_the_quarter_before_their_first_trade():
    """A member has no quarters: ALL starts at the quarter-end before their oldest counted
    trade, where Trading Activity's whole-range period starts, so the chart covers it."""
    from backend.services.followed_portfolios import performance

    member = {
        "activity": [
            {"date": "2024-05-14", "counted": True},
            {"date": "2023-01-02", "counted": False},
            {"date": "2025-01-03", "counted": True},
        ]
    }
    assert performance._history_start(member) == "2024-03-31"
    assert (
        performance._history_start({"history": [{"as_of": "2026-06-30"}, {"as_of": "2025-06-30"}]})
        == "2025-06-30"
    )
    assert performance._history_start({"activity": []}) is None


def test_preview_route_and_its_demo_lock(client, monkeypatch):
    from backend.api.demo_lockdown import is_demo_locked

    monkeypatch.setattr(service, "preview_summary", lambda kind, ref: {"name": ref})
    monkeypatch.setattr(service, "preview_page", lambda kind, ref: {"page": ref})
    got = client.get("/api/followed-portfolios/preview", params={"kind": "fund_13f", "ref": "7"})
    assert got.json() == {"name": "7"}
    full = client.get(
        "/api/followed-portfolios/preview", params={"kind": "fund_13f", "ref": "7", "full": True}
    )
    assert full.json() == {"page": "7"}
    bad = client.get("/api/followed-portfolios/preview", params={"kind": "senator", "ref": "7"})
    assert bad.status_code == 422
    assert is_demo_locked("GET", "/api/followed-portfolios/preview")
    monkeypatch.setattr(service, "preview_returns", lambda kind, ref: {"returns": ref})
    got = client.get(
        "/api/followed-portfolios/preview/returns", params={"kind": "fund_13f", "ref": "7"}
    )
    assert got.json() == {"returns": "7"}
    assert is_demo_locked("GET", "/api/followed-portfolios/preview/returns")


def test_unfollow_deletes(source):
    fid = _follow()["id"]
    _apply(op="unfollow", id=fid)
    assert service.list_follows()["follows"] == []


# ── directory, REST ──────────────────────────────────────────────────────


def test_directory_lists_featured_then_searches_and_marks_followed(source):
    featured = service.directory("")["results"]
    assert featured and all(r["featured"] and not r["followed"] for r in featured)
    _follow()
    found = service.directory("acme")["results"]
    assert found == [
        {
            "kind": "fund_13f",
            "source_ref": "777",
            "name": "Acme Capital",
            "subtitle": "13F filer",
            "featured": False,
            "followed": True,
            # A followed portfolio's card is read from the database: nothing to fetch.
            "ready": True,
        }
    ]


def test_directory_survives_a_source_that_is_down(source, monkeypatch):
    def boom(q):
        raise ExternalServiceError("down")

    monkeypatch.setattr(service.edgar_13f, "search", boom)
    assert service.directory("acme")["results"] == []


def test_performance_route_serves_the_services_answer_and_404s_for_a_stranger(client, monkeypatch):
    fid = _follow()["id"]
    asked = []

    def fake(row):
        asked.append(row["id"])
        return {"benchmark": "SPY", "periods": {}, "trade_returns": {}}

    monkeypatch.setattr(service.performance, "performance_for", fake)
    got = client.get(f"/api/followed-portfolios/{fid}/performance")
    assert got.status_code == 200 and got.json()["benchmark"] == "SPY" and asked == [fid]
    assert client.get("/api/followed-portfolios/fol_missing/performance").status_code == 404


def test_rest_round_trip(client):
    assert client.get("/api/followed-portfolios").json() == {"follows": []}
    found = client.get("/api/followed-portfolios/directory", params={"q": "acme"}).json()
    assert found["results"][0]["source_ref"] == "777"
    made = client.post("/api/followed-portfolios", json={"kind": "fund_13f", "source_ref": "777"})
    assert made.status_code == 200
    fid = made.json()["follow"]["id"]
    again = client.post("/api/followed-portfolios", json={"kind": "fund_13f", "source_ref": "777"})
    assert again.status_code == 409
    listed = client.get("/api/followed-portfolios").json()["follows"]
    assert set(listed[0]) == {
        "id",
        "kind",
        "source_ref",
        "name",
        "subtitle",
        "status",
        "error",
        "last_checked_at",
        "source_url",
        "delay_days",
        "delay_basis",
        "model",
        "history",
        "activity",
    }
    assert client.post(f"/api/followed-portfolios/{fid}/refresh").json()["new_filing"] is False
    assert client.patch(f"/api/followed-portfolios/{fid}", json={}).status_code == 405
    assert client.get(f"/api/followed-portfolios/{fid}/mirror").status_code == 404
    assert client.delete(f"/api/followed-portfolios/{fid}").status_code == 200
    assert client.delete(f"/api/followed-portfolios/{fid}").status_code == 404
    bad = client.post("/api/followed-portfolios", json={"kind": "senator", "source_ref": "1"})
    assert bad.status_code == 422


@pytest.mark.flags(followed_portfolios=False)
def test_flag_off_hides_every_route_and_refuses_every_tool(client):
    for method, path in [
        ("get", "/api/followed-portfolios"),
        ("get", "/api/followed-portfolios/directory"),
        ("post", "/api/followed-portfolios/x/refresh"),
        ("delete", "/api/followed-portfolios/x"),
    ]:
        assert getattr(client, method)(path).status_code == 404
    for name in (
        "get_followed_portfolios",
        "set_followed_portfolios",
    ):
        text, _ = asyncio.run(execute_tool(name, {}))
        assert "switched off" in text
    assert FollowedPortfolioStore().all() == []


def test_demo_refuses_every_write_and_the_directory():
    from backend.api.demo_lockdown import is_demo_locked

    assert is_demo_locked("GET", "/api/followed-portfolios/directory")
    assert is_demo_locked("POST", "/api/followed-portfolios")
    assert is_demo_locked("POST", "/api/followed-portfolios/fol_1/refresh")
    assert is_demo_locked("DELETE", "/api/followed-portfolios/fol_1")
    assert not is_demo_locked("GET", "/api/followed-portfolios")


# ── the Lucius tools ────────────────────────────────────────────────────────────────


def test_tools_search_follow_read_refresh_and_unfollow(source):
    assert call("get_followed_portfolios", search="acme")["results"][0]["name"] == "Acme Capital"
    made = call(
        "set_followed_portfolios", follow_portfolios=[{"kind": "fund_13f", "source_ref": "777"}]
    )
    assert made["results"][0]["ok"] is True
    listed = call("get_followed_portfolios", portfolios=["acme capital"], top_n=1)
    assert [f["name"] for f in listed["follows"]] == ["Acme Capital"]
    assert [h["symbol"] for h in listed["follows"][0]["model"]["holdings"]] == ["ACME"]
    assert "sleeve" not in listed["follows"][0] and "execution" not in listed
    fresh = call("set_followed_portfolios", refresh_portfolios=["Acme Capital"])
    assert fresh["results"][0]["op"] == "refresh"
    gone = call("set_followed_portfolios", unfollow_portfolios=["Acme Capital"])
    assert gone["results"][0]["op"] == "unfollow"
    assert call("get_followed_portfolios")["follows"] == []


def test_a_search_is_refused_on_the_public_demo(source, monkeypatch):
    from backend.ai.tools import followed_portfolios as tool_module

    class Demo:
        demo_mode = True

    monkeypatch.setattr(tool_module, "get_settings", lambda: Demo())
    assert "disabled on the public demo" in call("get_followed_portfolios", search="acme")["error"]
    assert call("get_followed_portfolios") == {"follows": []}


def test_tool_reports_each_failure_without_stopping(source):
    out = call(
        "set_followed_portfolios",
        refresh_portfolios=["Nobody"],
        follow_portfolios=[{"kind": "fund_13f", "source_ref": "777"}],
    )
    assert [r["ok"] for r in out["results"]] == [True, False]
    assert "No followed portfolio 'Nobody'" in out["results"][1]["error"]


def test_tools_are_write_classified():
    from backend.ai.tools.registry import is_write_tool

    assert is_write_tool("set_followed_portfolios")
    assert not is_write_tool("get_followed_portfolios")


# ── ARK ETFs, Form 4 trades and the delay figure ───────────────────────────────────


def _form4_world(monkeypatch, filings, xml_by_name):
    """Serve a submissions list and Form 4 XML; returns the list of documents fetched."""
    fetched = []
    recent = {
        "form": ["4"] * len(filings),
        "accessionNumber": [f[0] for f in filings],
        "filingDate": [f[1] for f in filings],
        "primaryDocument": [f"xslF345X06/{f[2]}" for f in filings],
    }
    monkeypatch.setattr(
        service.form4, "fetch_json", lambda url, **k: {"filings": {"recent": recent}}
    )

    def fake_bytes(url, **k):
        fetched.append(url.rsplit("/", 1)[-1])
        return xml_by_name[url.rsplit("/", 1)[-1]].encode()

    monkeypatch.setattr(service.form4, "fetch_bytes", fake_bytes)
    return fetched


def _ownership(day="2026-11-05", code="P", shares="1000", price="10.5"):
    return (
        "<ownershipDocument><issuer><issuerName>ACME CORP</issuerName>"
        "<issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>"
        "<reportingOwner><reportingOwnerId><rptOwnerCik>0000000777</rptOwnerCik>"
        "</reportingOwnerId></reportingOwner><nonDerivativeTable>"
        f"<nonDerivativeTransaction><transactionDate><value>{day}</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>"
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        "</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )


def test_a_fund_follow_stores_its_form4_trades_and_reads_only_new_filings_later(
    source, monkeypatch
):
    fetched = _form4_world(
        monkeypatch,
        [("0000000000-26-000010", "2026-11-06", "a.xml")],
        {"a.xml": _ownership(), "b.xml": _ownership(day="2026-11-09", code="S")},
    )
    follow = _follow()
    assert fetched == ["a.xml"]
    [trade] = follow["activity"]
    assert (trade["side"], trade["shares"], trade["price"], trade["source"]) == (
        "buy",
        1000.0,
        10.5,
        "form4",
    )
    assert trade["report_url"] == "https://www.sec.gov/Archives/edgar/data/777/000000000026000010/"
    _form4_world(
        monkeypatch,
        [
            ("0000000000-26-000011", "2026-11-10", "b.xml"),
            ("0000000000-26-000010", "2026-11-06", "a.xml"),
        ],
        {"b.xml": _ownership(day="2026-11-09", code="S")},
    )
    out = _apply(op="refresh", id=follow["id"])
    assert out["new_filing"] is False
    assert [t["side"] for t in out["follow"]["activity"]] == ["sell", "buy"]
    assert len(IntelStoriesStore().all()) == 0


def test_a_form4_failure_does_not_fail_the_follow_or_the_refresh(source, monkeypatch):
    def boom(*a, **k):
        raise ExternalServiceError("down")

    monkeypatch.setattr(service.form4, "fetch_json", boom)
    fid = _follow()["id"]
    out = _apply(op="refresh", id=fid)["follow"]
    assert out["status"] == "ok" and out["activity"] == []


def test_delay_days_per_kind(source):
    from datetime import date

    today = date(2026, 11, 10)
    fund = {
        "kind": "fund_13f",
        "current": {"as_of": "2026-09-30"},
        "activity": [{"date": "2026-11-05", "counted": True}],
    }
    assert service.delay_days(fund, today) == 5
    assert service.delay_days({**fund, "activity": []}, today) == 41
    member = {
        "kind": "house_member",
        "current": {"as_of": "2026-10-30"},
        "activity": [
            {"date": "2026-10-01", "counted": True},
            {"date": "2026-11-08", "counted": False},
        ],
    }
    assert service.delay_days(member, today) == 40
    assert service.delay_days({"kind": "house_member", "activity": []}, today) is None
    etf = {"kind": "etf_daily", "current": {"as_of": "2026-11-09"}}
    assert service.delay_days(etf, today) == 1
    assert service.view({**etf, "id": "x", "source_ref": "ARKK", "name": "ARK"})["delay_basis"] == (
        "daily"
    )


def test_the_api_follows_an_etf_and_refuses_one_off_the_list(client, source):
    made = client.post("/api/followed-portfolios", json={"kind": "etf_daily", "source_ref": "arkk"})
    assert made.status_code == 200 and made.json()["follow"]["kind"] == "etf_daily"
    assert made.json()["follow"]["source_ref"] == "ARKK"
    assert made.json()["follow"]["name"] == "ARK Innovation ETF"
    bad = client.post("/api/followed-portfolios", json={"kind": "etf_daily", "source_ref": "QQQ"})
    assert bad.status_code == 422 or bad.status_code == 400


def test_an_etf_story_names_the_holdings_change_and_a_quiet_day_raises_none(source):
    fid = _follow()["id"]
    row = FollowedPortfolioStore().find_by_id(fid)
    row = {**row, "kind": "etf_daily", "name": "ARK Innovation ETF"}
    quiet = {"holdings": [{"change": "unchanged"}], "exited": [], "filed_at": "x", "as_of": "x"}
    assert signals.emit_new_filing_story(row, quiet) is False
    busy = {**quiet, "holdings": [{"change": "new"}]}
    assert signals.emit_new_filing_story(row, busy) is True
    assert IntelStoriesStore().all()[0]["label"] == "ARK Innovation ETF changed its holdings"


def test_the_owner_corrects_a_ticker_and_it_survives_a_refresh(source, client, monkeypatch):
    monkeypatch.setattr(
        service,
        "price_history",
        lambda symbol: {"symbol": symbol.upper(), "points": [] if symbol == "NOPE" else [1, 2]},
    )
    fid = _follow()["id"]
    row = FollowedPortfolioStore().find_by_id(fid)
    key = row["current"]["positions"][1]["key"]
    got = client.post(f"/api/followed-portfolios/{fid}/ticker", json={"key": key, "symbol": "crux"})
    assert got.status_code == 200
    holdings = {h["symbol"] for h in got.json()["follow"]["model"]["holdings"]}
    assert "CRUX" in holdings and "BOLT" not in holdings
    # A refresh of the same filing keeps the owner's ticker, whatever the lookup says.
    assert "CRUX" in {
        h["symbol"] for h in _apply(op="refresh", id=fid)["follow"]["model"]["holdings"]
    }
    bad = client.post(f"/api/followed-portfolios/{fid}/ticker", json={"key": key, "symbol": "NOPE"})
    assert bad.status_code == 422 and "No price found for NOPE" in bad.json()["detail"]
    missing = client.post(f"/api/followed-portfolios/{fid}/ticker", json={"key": "X", "symbol": ""})
    assert missing.status_code == 422
    # Clearing hands the holding back to the lookup (this fake source has none for it).
    cleared = _apply(op="set_ticker", id=fid, key=key, symbol="")
    assert "CRUX" not in {h["symbol"] for h in cleared["follow"]["model"]["holdings"]}
    assert FollowedPortfolioStore().find_by_id(fid)["ticker_overrides"] == {}


def test_the_background_warm_up_waits_until_copy_trading_is_used(monkeypatch, tmp_path):
    """On by default for every install, the warm-up (~15 min and ~1,500 requests cold) must not
    run on an install that never opened Copy Trading or followed anything."""
    from backend.services.followed_portfolios import preview

    calls = []
    monkeypatch.setattr(preview, "warm", lambda entries: calls.append(len(entries)) or 0)
    monkeypatch.setattr(preview, "_DISK", tmp_path / "followed_portfolio_previews.json")
    monkeypatch.setattr(service, "_featured_all", lambda: [])
    assert service.warm_featured() == 0
    assert calls == []
    (tmp_path / "followed_portfolio_previews.json").write_text("{}")
    service.warm_featured()
    assert calls == [0]


def test_a_card_past_its_time_shows_its_last_figures_and_is_made_again_off_the_request(
    monkeypatch,
):
    """After a restart or a new day a card used to come back empty while the SEC was read
    again; it now shows its last figures at once, marked stale, and a fresh one is made in the
    background (owner, 2026-11-14)."""
    from backend.services.followed_portfolios import preview

    last = {"kind": "fund_13f", "source_ref": "1067983", "name": "ACME", "return_12m": 0.1}
    monkeypatch.setattr(preview, "_summaries", {})
    monkeypatch.setattr(preview, "_last", {("fund_13f", "1067983"): (0.0, "old", last)})
    refreshed: list[tuple[str, str]] = []
    monkeypatch.setattr(service, "_refresh_card_soon", lambda k, r: refreshed.append((k, r)))
    monkeypatch.setattr(
        preview, "preview_row", lambda *a, **k: pytest.fail("must not read the SEC on the request")
    )
    out = service.preview_summary("fund_13f", "1067983")
    assert out["stale"] is True and out["return_12m"] == 0.1
    assert refreshed == [("fund_13f", "1067983")]
    assert preview.card_ready("fund_13f", "1067983")
    assert preview.cached_figures("fund_13f", "1067983")["return_12m"] == 0.1


def test_a_card_never_made_is_still_fetched_on_the_request(monkeypatch):
    from backend.services.followed_portfolios import preview

    monkeypatch.setattr(preview, "_summaries", {})
    monkeypatch.setattr(preview, "_last", {})
    monkeypatch.setattr(
        preview, "preview_row", lambda *a, **k: {"kind": "fund_13f", "source_ref": "1"}
    )
    monkeypatch.setattr(
        preview, "summary", lambda row: {"kind": "fund_13f", "source_ref": "1", "name": "ACME"}
    )
    out = service.preview_summary("fund_13f", "1")
    assert "stale" not in out and out["name"] == "ACME"
