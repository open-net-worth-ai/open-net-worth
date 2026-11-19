"""``GET /api/paper/positions?instrument_id=`` — every live paper position in one instrument,
across active paper portfolios, for the asset page's Position table. Each row says simulated and
names its portfolio; the real position in the same instrument, and net worth, are untouched."""

from __future__ import annotations

import json

from backend.services import ledger as ledger_service
from backend.services import positions as positions_service
from backend.services.paper_trading.service import apply_paper_change


def _real_figures() -> str:
    rows = [r for r in positions_service.hydrated() if r.get("instrument_id") == "inst_acme"]
    return json.dumps(
        {"positions": rows, "net_worth": ledger_service.net_worth("USD")},
        sort_keys=True,
        default=str,
    )


def test_paper_positions_in_one_instrument(paper_client, monkeypatch):
    from backend.services import fx_cache

    monkeypatch.setattr(fx_cache, "load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.35})
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": "inst_acme",
            "account_name": "Real Broker",
            "account_type": "investment",
            "quantity": 7,
            "cost_basis": 90.0,
            "cost_currency": "USD",
            "acquired_at": "2026-10-01",
        }
    )
    assert res.get("ok"), res
    before = _real_figures()

    a = apply_paper_change({"op": "create", "name": "A", "cash": 5000, "home_currency": "USD"})
    b = apply_paper_change({"op": "create", "name": "B", "cash": 5000, "home_currency": "USD"})
    old = apply_paper_change({"op": "create", "name": "Old", "cash": 5000, "home_currency": "USD"})
    for pf, qty in ((a, 3), (b, 2), (old, 9)):
        apply_paper_change(
            {
                "op": "place",
                "portfolio_id": pf["portfolio"]["id"],
                "order": {"asset_key": "inst:inst_acme", "side": "buy", "quantity": qty},
            }
        )
    apply_paper_change({"op": "archive", "id": old["portfolio"]["id"]})

    out = paper_client.get("/api/paper/positions", params={"instrument_id": "inst_acme"}).json()
    assert out["simulated"] is True
    by_name = {r["portfolio_name"]: r for r in out["positions"]}
    assert set(by_name) == {"A", "B"}  # an archived run is not a live position
    assert by_name["A"]["quantity"] == 3 and by_name["B"]["quantity"] == 2
    row = by_name["A"]
    assert {
        "portfolio_id",
        "value_home",
        "currency",
        "return_pct",
        "short_options",
        "cover",
    } <= set(row)
    assert row["simulated"] is True and row["value_home"] > 0

    trades = out["trades"]
    # Each trade names its portfolio; an archived run's history is not shown.
    assert sorted(t["portfolio_name"] for t in trades) == ["A", "B"]
    assert all(t["kind"] == "buy" and t["label"].startswith("Bought") for t in trades)

    # A position sold out keeps its history: the trade list still has both of A's trades.
    apply_paper_change(
        {
            "op": "place",
            "portfolio_id": a["portfolio"]["id"],
            "order": {"asset_key": "inst:inst_acme", "side": "sell", "quantity": 3},
        }
    )
    out = paper_client.get("/api/paper/positions", params={"instrument_id": "inst_acme"}).json()
    assert {r["portfolio_name"] for r in out["positions"]} == {"B"}
    a_trades = [t for t in out["trades"] if t["portfolio_name"] == "A"]
    assert [t["kind"] for t in a_trades] == ["sell", "buy"]  # newest first

    assert _real_figures() == before  # the real position and net worth are unchanged


def test_an_unknown_instrument_is_a_404(paper_client):
    assert (
        paper_client.get("/api/paper/positions", params={"instrument_id": "inst_nope"}).status_code
        == 404
    )
