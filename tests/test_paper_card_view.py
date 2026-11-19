"""The list's card carries what the Paper Trading card draws: the largest holdings by weight (its
ring), how many it holds, and its two newest buys or sells (its trade lines)."""

from __future__ import annotations

from backend.services.paper_trading.service import apply_paper_change


def test_card_has_weights_count_and_recent_trades(paper_client):
    pf = apply_paper_change({"op": "create", "name": "Card", "cash": 5000, "home_currency": "USD"})
    pid = pf["portfolio"]["id"]
    for side, qty in (("buy", 4), ("buy", 2), ("sell", 1)):
        apply_paper_change(
            {
                "op": "place",
                "portfolio_id": pid,
                "order": {"asset_key": "inst:inst_acme", "side": side, "quantity": qty},
            }
        )

    out = paper_client.get("/api/paper/portfolios").json()
    card = next(c for c in out["portfolios"] if c["id"] == pid)
    assert card["holdings_count"] == 1
    assert len(card["weights"]) == 1 and 0 < card["weights"][0]["weight"] <= 1
    # Newest first, and only buys and sells: the opening deposit is not a trade.
    assert [t["side"] for t in card["recent_trades"]] == ["sell", "buy"]
    assert all(t["label"] for t in card["recent_trades"])


def test_an_archived_card_has_no_live_weights(paper_client):
    pf = apply_paper_change({"op": "create", "name": "Old", "cash": 5000, "home_currency": "USD"})
    apply_paper_change({"op": "archive", "id": pf["portfolio"]["id"]})
    out = paper_client.get("/api/paper/portfolios").json()
    card = next(c for c in out["archived"] if c["id"] == pf["portfolio"]["id"])
    assert card["weights"] == [] and card["holdings_count"] == 0
    assert card["recent_trades"] == []
