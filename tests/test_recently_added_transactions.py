"""GET /api/transactions/recently-added — the "recently added" dashboard widget's feed:
ordered by `created_at` (when a row was WRITTEN), never `date` (what it claims happened), so a
backfilled import surfaces even when its own date is weeks old.

Offline + isolated: every store is DB-backed via `db_isolated`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.db.category_store import CategoryStore
from backend.db.payee_store import PayeeStore
from backend.services import ledger as svc


@pytest.fixture
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    acct = svc.accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return {"account_id": acct["id"]}


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _seed(
    account_id: str,
    created_at: str,
    date: str = "2026-01-15",
    *,
    needs_review: bool = False,
) -> dict:
    return svc.transactions.append(
        {
            "date": date,
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": account_id,
            "from_amount": 5.0,
            "from_currency": "SGD",
            "created_at": created_at,
            "needs_review": needs_review,
        }
    )


def test_recent_by_created_at_orders_by_insertion_not_date(env):
    acct = env["account_id"]
    # Backdated txn (old `date`) inserted most RECENTLY — must still sort first.
    old_date_recent_insert = _seed(acct, "2026-03-03T00:00:00Z", date="2025-11-01")
    middle = _seed(acct, "2026-03-02T00:00:00Z")
    oldest_insert = _seed(acct, "2026-03-01T00:00:00Z")

    page = svc.transactions.recent_by_created_at(10)

    ids = [t["id"] for t in page]
    assert ids[:3] == [old_date_recent_insert["id"], middle["id"], oldest_insert["id"]]


def test_recent_by_created_at_excludes_deleted(env):
    acct = env["account_id"]
    kept = _seed(acct, "2026-03-03T00:00:00Z")
    removed = _seed(acct, "2026-03-02T00:00:00Z")
    svc.transactions.soft_delete(removed["id"])

    page = svc.transactions.recent_by_created_at(10)

    ids = {t["id"] for t in page}
    assert kept["id"] in ids
    assert removed["id"] not in ids


def test_recent_by_created_at_respects_limit(env):
    acct = env["account_id"]
    for i in range(5):
        _seed(acct, f"2026-03-0{i + 1}T00:00:00Z")

    page = svc.transactions.recent_by_created_at(2)

    assert len(page) == 2


def test_endpoint_returns_shaped_transactions(client, env):
    acct = env["account_id"]
    _seed(acct, "2026-03-03T00:00:00Z")

    resp = client.get("/api/transactions/recently-added?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert "transactions" in body
    assert len(body["transactions"]) == 1
    row = body["transactions"][0]
    assert row["base_amount"] == pytest.approx(5.0)
    assert row["base_currency"] == "SGD"


def test_review_feed_filters_before_limit_and_reports_the_full_backlog(client, env):
    acct = env["account_id"]
    oldest_pending = _seed(
        acct,
        "2026-03-01T00:00:00Z",
        date="2024-01-01",
        needs_review=True,
    )
    _seed(acct, "2026-03-04T00:00:00Z")
    newest_pending = _seed(
        acct,
        "2026-03-03T00:00:00Z",
        date="2023-01-01",
        needs_review=True,
    )
    _seed(acct, "2026-03-02T00:00:00Z")

    resp = client.get("/api/transactions/recently-added?needs_review=true&limit=1")

    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body["transactions"]] == [newest_pending["id"]]
    assert oldest_pending["id"] != newest_pending["id"]
    assert body["total"] == 2
    assert body["has_more"] is True

    second = client.get("/api/transactions/recently-added?needs_review=true&limit=1&offset=1")
    assert second.status_code == 200
    second_body = second.json()
    assert [row["id"] for row in second_body["transactions"]] == [oldest_pending["id"]]
    assert second_body["has_more"] is False


def test_automation_queue_uses_creation_origin_before_count_and_pagination(client, env):
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    def create(source, surface, *, review=True):
        token = set_audit_scope(actor="ai", surface=surface)
        try:
            return svc.create_transaction(
                {
                    "date": "2026-03-03",
                    "kind": "expense",
                    "description": source,
                    "from_account_id": env["account_id"],
                    "from_amount": 5,
                    "from_currency": "SGD",
                    "source": source,
                    "needs_review": review,
                },
                allow_duplicate=True,
            )
        finally:
            clear_audit_scope(token)

    scheduled = create("automation", "automation")
    headless = create("ai", "automation")
    # Historical AI automation rows retain their original writer; use their creation audit.
    svc.transactions.update_by_id(headless["id"], {"source": "ai"})
    for source in ("manual", "ai", "import", "agent", "feed"):
        create(source, "web")
    create("automation", "automation", review=False)
    deleted = create("automation", "automation")
    svc.transactions.soft_delete(deleted["id"])
    edited = create("manual", "web")
    token = set_audit_scope(actor="ai", surface="automation")
    try:
        svc.update_transaction(edited["id"], {"memo": "Automation edited, not created"})
    finally:
        clear_audit_scope(token)

    url = "/api/transactions/recently-added?needs_review=true&automation_only=true&limit=1"
    first = client.get(url).json()
    second = client.get(url + "&offset=1").json()
    assert first["total"] == second["total"] == 2
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert {first["transactions"][0]["id"], second["transactions"][0]["id"]} == {
        scheduled["id"],
        headless["id"],
    }
    assert first["transactions"][0]["source"] == second["transactions"][0]["source"] == "automation"
    assert client.get("/api/transactions/recently-added?needs_review=true").json()["total"] == 8
    svc.update_transaction(headless["id"], {"needs_review": False})
    remaining = client.get(url).json()
    assert remaining["total"] == 1
    assert remaining["transactions"][0]["id"] == scheduled["id"]


def test_automation_queue_names_the_firing_automation(client, env):
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    acct = env["account_id"]
    token = set_audit_scope(
        actor="ai",
        surface="automation",
        automation="Bank SMS matcher",
        automation_id="auto_sms",
        automation_run_id="run_1",
    )
    try:
        named = svc.create_transaction(
            {
                "date": "2026-01-15",
                "kind": "expense",
                "description": "Coffee",
                "from_account_id": acct,
                "from_amount": 5.0,
                "from_currency": "SGD",
                "source": "automation",
                "needs_review": True,
            }
        )
    finally:
        clear_audit_scope(token)
    token = set_audit_scope(actor="ai", surface="automation")
    try:
        unnamed = svc.create_transaction(
            {
                "date": "2026-01-16",
                "kind": "expense",
                "description": "Tea",
                "from_account_id": acct,
                "from_amount": 4.0,
                "from_currency": "SGD",
                "source": "automation",
                "needs_review": True,
            }
        )
    finally:
        clear_audit_scope(token)

    body = client.get(
        "/api/transactions/recently-added?needs_review=true&automation_only=true&limit=10"
    ).json()
    by_id = {row["id"]: row for row in body["transactions"]}
    assert by_id[named["id"]]["via"] == "Bank SMS matcher"
    assert by_id[named["id"]]["automation_id"] == "auto_sms"
    assert by_id[unnamed["id"]]["via"] is None
    assert by_id[unnamed["id"]]["automation_id"] is None

    plain = client.get("/api/transactions/recently-added?limit=10").json()
    assert "via" not in plain["transactions"][0]


@pytest.mark.parametrize("bulk", [False, True])
def test_new_automation_origin_survives_audit_retention(env, bulk):
    from backend.core.audit_context import clear_audit_scope, set_audit_scope
    from backend.db.transaction_audit_store import TransactionAuditStore

    data = {
        "date": "2026-03-03",
        "kind": "expense",
        "description": "Scheduled coffee",
        "from_account_id": env["account_id"],
        "from_amount": 5,
        "from_currency": "SGD",
        "source": "ai",
    }
    token = set_audit_scope(actor="ai", surface="automation")
    try:
        if bulk:
            svc.bulk_create_transactions([data])
        else:
            svc.create_transaction(data)
    finally:
        clear_audit_scope(token)
    TransactionAuditStore().replace_all([])
    rows, total = svc.transactions.recent_page_by_created_at(
        limit=10, needs_review=True, automation_only=True
    )
    assert total == 1
    assert rows[0]["source"] == "automation"


def test_automation_single_and_bulk_share_unattended_duplicate_screening(env):
    from backend.core.audit_context import clear_audit_scope, set_audit_scope
    from backend.core.errors import DuplicateTransactionError

    data = {
        "date": "2026-03-03",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": env["account_id"],
        "from_amount": 5,
        "from_currency": "SGD",
        "source": "manual",
    }
    existing = svc.create_transaction(data)
    token = set_audit_scope(actor="ai", surface="automation")
    try:
        with pytest.raises(DuplicateTransactionError):
            svc.create_transaction({**data, "source": "ai"})
        created, skipped = svc.bulk_create_transactions([{**data, "source": "ai"}])
    finally:
        clear_audit_scope(token)
    assert created == []
    assert skipped[0]["_duplicate_of"] == existing["id"]


def test_origin_backfill_preserves_only_proven_creates(env, monkeypatch):
    import importlib

    from backend.core.audit_context import clear_audit_scope, set_audit_scope
    from backend.db.engine import get_engine
    from backend.db.transaction_audit_store import TransactionAuditStore

    rows = []
    for surface in ("automation", "web"):
        token = set_audit_scope(actor="ai", surface=surface)
        try:
            row = svc.create_transaction(
                {
                    "date": "2026-03-03",
                    "kind": "expense",
                    "description": surface,
                    "from_account_id": env["account_id"],
                    "from_amount": 5,
                    "from_currency": "SGD",
                    "source": "ai",
                },
                allow_duplicate=True,
            )
        finally:
            clear_audit_scope(token)
        svc.transactions.update_by_id(row["id"], {"source": "ai"})
        rows.append(svc.transactions.find_by_id(row["id"]))
    token = set_audit_scope(actor="ai", surface="automation")
    try:
        svc.update_transaction(rows[1]["id"], {"memo": "Updated, not created"})
    finally:
        clear_audit_scope(token)
    migration = importlib.import_module(
        "backend.db.alembic.versions.202609192200_preserve_automation_creation_origin"
    )
    with get_engine().begin() as conn:
        monkeypatch.setattr(migration.op, "get_bind", lambda: conn)
        migration.upgrade()
        migration.upgrade()
    TransactionAuditStore().replace_all([])
    assert svc.transactions.find_by_id(rows[0]["id"]) == {**rows[0], "source": "automation"}
    assert svc.transactions.find_by_id(rows[1]["id"])["source"] == "ai"
    found, total = svc.transactions.recent_page_by_created_at(
        limit=10, needs_review=True, automation_only=True
    )
    assert total == 1
    assert found[0]["id"] == rows[0]["id"]


def test_update_endpoint_preserves_explicit_null_category_and_payee(client, env):
    category = CategoryStore().insert(
        {"id": "cat_13095ba70000", "label": "Food", "kind": "expense", "color": "#e0a032"}
    )
    payee = PayeeStore().create("Coffee shop")
    row = svc.create_transaction(
        {
            "date": "2026-03-03",
            "kind": "expense",
            "category": category["id"],
            "payee_id": payee["id"],
            "description": "Coffee",
            "from_account_id": env["account_id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
            "needs_review": True,
        }
    )

    resp = client.put(
        f"/api/transactions/{row['id']}",
        json={"category": None, "payee_id": None, "needs_review": False},
    )

    assert resp.status_code == 200
    updated = resp.json()
    assert updated["category"] is None
    assert updated["payee_id"] is None
    assert updated["needs_review"] is False


def test_update_endpoint_ignores_null_for_non_clearable_fields(client, env):
    row = svc.create_transaction(
        {
            "date": "2026-03-03",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": env["account_id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )

    resp = client.put(f"/api/transactions/{row['id']}", json={"date": None})

    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-03-03"


def test_update_endpoint_accepts_explicit_nulls_when_review_changes_leg_direction(client, env):
    row = svc.create_transaction(
        {
            "date": "2026-03-03",
            "kind": "expense",
            "description": "Correction",
            "from_account_id": env["account_id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
            "original_amount": 4.0,
            "original_currency": "USD",
        }
    )

    resp = client.put(
        f"/api/transactions/{row['id']}",
        json={
            "kind": "income",
            "from_account_id": None,
            "from_amount": None,
            "from_currency": None,
            "to_account_id": env["account_id"],
            "to_amount": 5.0,
            "to_currency": "SGD",
            "original_amount": None,
            "original_currency": None,
        },
    )

    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["kind"] == "income"
    assert updated["from_account_id"] is None
    assert updated["to_account_id"] == env["account_id"]
    assert updated["original_amount"] is None
    assert updated["original_currency"] is None


def test_update_endpoint_refuses_unknown_transaction_kind(client, env):
    row = svc.create_transaction(
        {
            "date": "2026-03-03",
            "kind": "expense",
            "description": "Correction",
            "from_account_id": env["account_id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )

    resp = client.put(f"/api/transactions/{row['id']}", json={"kind": "refundish"})

    assert resp.status_code == 422


@pytest.mark.parametrize("field", ["original_amount", "original_currency"])
def test_update_endpoint_refuses_clearing_only_half_of_original_charge(client, env, field):
    row = svc.create_transaction(
        {
            "date": "2026-03-03",
            "kind": "expense",
            "description": "Foreign coffee",
            "from_account_id": env["account_id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
            "original_amount": 4.0,
            "original_currency": "USD",
        }
    )

    resp = client.put(f"/api/transactions/{row['id']}", json={field: None})

    assert resp.status_code == 422
    stored = svc.transactions.find_by_id(row["id"])
    assert stored["original_amount"] == pytest.approx(4.0)
    assert stored["original_currency"] == "USD"


def test_exclude_openings_leaves_starting_balances_out_without_shortening_the_page(
    env, client, monkeypatch
):
    acct = env["account_id"]
    opening = _seed(acct, "2026-03-05T00:00:00Z")
    first = _seed(acct, "2026-03-04T00:00:00Z")
    second = _seed(acct, "2026-03-03T00:00:00Z")
    monkeypatch.setattr(svc, "opening_txn_ids", lambda: frozenset({opening["id"]}))

    everything = client.get("/api/transactions/recently-added?limit=2").json()
    assert [t["id"] for t in everything["transactions"]] == [opening["id"], first["id"]]

    page = client.get("/api/transactions/recently-added?limit=2&exclude_openings=true").json()
    assert [t["id"] for t in page["transactions"]] == [first["id"], second["id"]]
    assert page["total"] == 2
