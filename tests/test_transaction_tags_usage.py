"""The tag picker's options: every tag on a live transaction, most used first."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.services import ledger as svc


def _expense(date: str, tags: list[str]) -> dict:
    return {
        "kind": "expense",
        "date": date,
        "description": f"POS {date}",
        "category": None,
        "from_account_id": "acc_chk",
        "from_amount": 5.0,
        "from_currency": "SGD",
        "tags": tags,
    }


@pytest.fixture
def client(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    rows = [
        _expense("2026-05-01", ["travel", "work"]),
        _expense("2026-06-01", ["travel"]),
        _expense("2026-06-10", ["gift"]),
        _expense("2026-06-20", ["secret"]),  # soft-deleted below
        _expense("2026-06-21", []),
    ]
    created = [svc.create_transaction(r, allow_duplicate=True) for r in rows]
    svc.transactions.soft_delete(created[3]["id"])
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def test_lists_live_tags_most_used_then_most_recent(client):
    res = client.get("/api/transactions/tags")
    assert res.status_code == 200
    assert res.json()["tags"] == [
        {"tag": "travel", "count": 2, "last_used": "2026-06-01"},
        {"tag": "gift", "count": 1, "last_used": "2026-06-10"},
        {"tag": "work", "count": 1, "last_used": "2026-05-01"},
    ]
