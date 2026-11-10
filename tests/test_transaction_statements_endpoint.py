"""``GET /api/transactions/{id}/statements`` — which statement(s) a row is ON.

The register has shown a whole "Source email" panel for an email-provenance row since EM1,
while a statement-linked row showed nothing — even though the `statement:` ref was sitting in
its ``external_refs`` the whole time.

The distinction this endpoint keeps: **ON is not FROM.** A row Lucius logged from a bank alert
and a statement later confirmed IS on that statement, and says so, while its ``source`` still
correctly reads ``ai``. Conflating the two was the 2026-07-31 bug.

Offline: isolated tmp_path stores. No LLM, no network.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api.ledger import router
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    acct = accounts.create(
        {"name": "Card", "kind": "liability", "type": "credit", "currency": "SGD"}
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), acct


def _txn(acct, **kw):
    data = {
        "date": "2026-06-05",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 4.5,
        "from_currency": "SGD",
        **kw,
    }
    return ledger_service.create_transaction(data, allow_duplicate=True)


def test_a_row_on_no_statement_returns_an_empty_list_not_a_404(client):
    """A hand entry, an agent booking and a bank-feed sync are all legitimately on no
    statement. The panel renders nothing — it must never substitute where the row came FROM."""
    cl, acct = client
    row = _txn(acct, source="manual")
    r = cl.get(f"/api/transactions/{row['id']}/statements")
    assert r.status_code == 200
    assert r.json() == {"statements": []}


def test_a_missing_transaction_is_a_404(client):
    cl, _ = client
    assert cl.get("/api/transactions/txn_does_not_exist/statements").status_code == 404


def test_the_endpoint_never_invents_a_link_from_the_source_field(client):
    """Guard against the 2026-07-31 conflation: a row whose SOURCE is an import but which no
    held statement covers must still report nothing, rather than echoing its own provenance."""
    cl, acct = client
    row = _txn(acct, source="import", external_refs=[{"source": "statement", "ref": "no-such"}])
    r = cl.get(f"/api/transactions/{row['id']}/statements")
    assert r.status_code == 200
    assert r.json()["statements"] == []
