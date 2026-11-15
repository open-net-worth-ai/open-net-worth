"""Paper trading end to end through the REST router: create, a market order that waits for the
open and fills, start over keeping history (PRD acceptance criteria 3, 4, 9)."""

from __future__ import annotations

from backend.services.paper_trading import engine, qa_quotes


def test_create_trade_wait_fill_start_over(paper_client):
    created = paper_client.post(
        "/api/paper/portfolios",
        json={"name": "Buy-zone test", "cash": 10_000, "home_currency": "USD"},
    )
    assert created.status_code == 200, created.text
    pid = created.json()["portfolio"]["id"]
    assert created.json()["simulated"] is True

    qa_quotes.set_state("closed")
    placed = paper_client.post(
        f"/api/paper/portfolios/{pid}/orders",
        json={"asset_key": "inst:inst_acme", "side": "buy", "quantity": 10, "note": "In my zone"},
    ).json()
    assert placed["order"]["status"] == "open"
    assert "Market closed" in placed["order"]["state_reason"]

    qa_quotes.set_state("open")
    assert engine.tick()["filled"] == 1
    page = paper_client.get(f"/api/paper/portfolios/{pid}").json()
    [holding] = page["holdings"]
    assert holding["quantity"] == 10
    fill = page["orders"][0]["fill"]
    assert fill["price"] == 100.02  # the ask
    assert fill["basis"] == "quote" and fill["source"] == "QA fixture" and fill["quote_as_of"]

    over = paper_client.post(f"/api/paper/portfolios/{pid}/start-over").json()
    assert over["archived"]["status"] == "archived"
    listing = paper_client.get("/api/paper/portfolios").json()
    assert [p["id"] for p in listing["archived"]] == [pid]
    old = paper_client.get(f"/api/paper/portfolios/{pid}").json()
    assert any(e["kind"] == "buy" for e in old["activity"])
    assert old["orders"][0]["status"] == "filled"
    new = paper_client.get(f"/api/paper/portfolios/{over['portfolio']['id']}").json()
    assert new["holdings"] == [] and new["figures"]["cash_home"] == 10_000
