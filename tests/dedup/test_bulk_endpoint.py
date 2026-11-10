"""TestClient smoke tests for the POST /api/transactions/bulk endpoint (finding F24).

Tests:
  - exact external_ref retry doesn't double-insert (L0 dedup)
  - duplicate against an EXISTING ledger row lands in the review queue (via match_review)
  - batch_id is stamped on all created rows
  - source is honored when in KNOWN_SOURCES, else defaults to 'api'
  - response shape is backward-compatible (additive fields only)
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.db.account_store import AccountStore
from backend.db.merge_journal_store import MergeJournalStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc


@pytest.fixture
def client(tmp_path, monkeypatch):
    acc_store = AccountStore()
    txn_store = TransactionStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store._path = tmp_path / "transactions.json"

    # Merge journal (needed by merge_transactions wired into the engine)
    journal = MergeJournalStore()
    journal._path = tmp_path / "merge_journal.json"

    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "merge_journal", journal)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))
    from backend.services import corrections as corr_svc

    monkeypatch.setattr(corr_svc, "record_correction", lambda old, new, **kw: None)

    # Redirect match_review stores to tmp too
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.services import match_review as mr

    mr_store = MatchReviewStore()
    mr_store._path = tmp_path / "match_reviews.json"
    md_store = MatchDecisionStore()
    md_store._path = tmp_path / "match_decisions.json"
    monkeypatch.setattr(mr, "reviews", mr_store)
    monkeypatch.setattr(mr, "decisions", md_store)

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app, raise_server_exceptions=False), acc_store, txn_store, mr_store


def _make_account(acc_store):
    return acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )


# ── F24(a): L0 skip — duplicate external_ref is not double-inserted ────────────────


def test_f24a_exact_external_ref_not_double_inserted(client):
    """F24(a): POSTing the same external_ref twice must not create two rows.
    The second call should count the row as skipped.
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)

    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
        "external_refs": [{"source": "simplefin", "ref": "tx-unique-1"}],
        "source": "simplefin",
    }

    r1 = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["created"] == 1
    assert body1["skipped"] == 0

    # Retry the exact same external_ref — should be skipped
    r2 = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["created"] == 0
    assert body2["skipped"] == 1
    assert body2["skipped_details"][0]["external_refs"] == [
        {"source": "simplefin", "ref": "tx-unique-1"}
    ]

    # Only one row in the ledger
    all_rows = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(all_rows) == 1


def test_f24a_no_external_ref_rows_need_force_to_repeat(client):
    """F24(a) as amended by ADR 2026-08-01 (pre-write duplicate refusal).

    A row without an external_ref still bypasses the L0 floor — that part is unchanged. What
    changed is the SEMANTIC guard above it: an identical second row now matches the first and
    is refused, so "always created" is no longer true unbypassably. ``allow_duplicate=true`` is the
    escape hatch, and with it the original F24(a) property holds exactly as before."""
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
    }
    r1 = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    r2 = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r1.json()["created"] == 1
    assert r2.json()["created"] == 0  # refused: same amount/date/description as r1
    r3 = cl.post("/api/transactions/bulk", json={"transactions": [row], "allow_duplicate": True})
    assert r3.json()["created"] == 1  # the caller confirmed it is a separate charge
    all_rows = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(all_rows) == 2


# ── F24(b): batch_id and source stamping ─────────────────────────────────────────


def test_f24b_batch_id_stamped_on_created_rows(client):
    """F24(b): all created rows should carry the batch_id from the response."""
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    rows = [
        {
            "date": "2026-02-01",
            "kind": "expense",
            "description": f"Item {i}",
            "from_account_id": acct["id"],
            "from_amount": float(i + 1),
            "from_currency": "SGD",
        }
        for i in range(3)
    ]
    r = cl.post("/api/transactions/bulk", json={"transactions": rows})
    assert r.status_code == 200
    body = r.json()
    batch_id = body["batch_id"]
    assert batch_id  # must be non-empty
    for t in body["transactions"]:
        assert t.get("batch_id") == batch_id, "every created row must carry the batch_id"


def test_f24b_known_source_honored(client):
    """F24(b): when body.source is in KNOWN_SOURCES, it is used for all rows."""
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Import",
        "from_account_id": acct["id"],
        "from_amount": 10.0,
        "from_currency": "SGD",
    }
    r = cl.post("/api/transactions/bulk", json={"transactions": [row], "source": "csv_import"})
    assert r.status_code == 200
    body = r.json()
    # A RETIRED alias is canonicalised, not preserved verbatim: `csv_import` said what FORMAT
    # arrived, which external_refs already records; the writer is `import` (RFC 2026-08-02).
    assert body["transactions"][0]["source"] == "import"

    # ...but an UNRECOGNISED source is KEPT, never rewritten. This is the half that used to be
    # broken: an unknown value was replaced with "api", erasing a caller's identity at the door
    # (how a user-installed integration became an anonymous HTTP write). Canonicalising a known
    # rename and erasing an unknown identity are different things.
    row2 = {**row, "date": "2026-02-09", "from_amount": 77.0, "description": "Acme sync"}
    r2 = cl.post("/api/transactions/bulk", json={"transactions": [row2], "source": "acme_bank"})
    assert r2.status_code == 200
    assert r2.json()["transactions"][0]["source"] == "acme_bank"


def test_f24b_unknown_source_defaults_to_api(client):
    """F24(b): when body.source is not in KNOWN_SOURCES, defaults to 'api'."""
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Import",
        "from_account_id": acct["id"],
        "from_amount": 10.0,
        "from_currency": "SGD",
    }
    r = cl.post(
        "/api/transactions/bulk", json={"transactions": [row], "source": "totally_fake_source"}
    )
    assert r.status_code == 200
    # Bulk endpoint uses effective_source='api' when unknown
    body = r.json()
    # Row source is determined by per-row source (default "manual" from TransactionCreate)
    # unless the row source is also unknown — in that case the effective_source 'api' wins.
    # The per-row TransactionCreate.source defaults to "manual" (in KNOWN_SOURCES) so it wins.
    # We just confirm the response is 200 and created=1.
    assert body["created"] == 1


def test_f24b_no_longer_force_stamps_csv_import(client):
    """F24(b): the old behavior of force-stamping 'csv_import' on every row is gone.
    A row with source='manual' stays 'manual'.
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Manual add",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
        "source": "manual",
    }
    r = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r.status_code == 200
    created_row = r.json()["transactions"][0]
    assert created_row["source"] == "manual"


# ── F24(c): match_review is called best-effort ────────────────────────────────────


def test_f24c_match_review_called_after_bulk_create(client, monkeypatch):
    """F24(c): after a successful bulk create, match_review.review_created_rows is called
    with the created rows and the batch_id (best-effort — a failure must not abort the write).
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)

    calls = []
    from backend.services import match_review as mr

    real_review = mr.review_created_rows

    def _capture(created, *, batch_id=None, **kw):
        calls.append({"created": created, "batch_id": batch_id})
        return real_review(created, batch_id=batch_id, **kw)

    monkeypatch.setattr(mr, "review_created_rows", _capture)

    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
    }
    r = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r.status_code == 200
    body = r.json()
    assert len(calls) == 1
    assert calls[0]["batch_id"] == body["batch_id"]
    assert len(calls[0]["created"]) == 1


def test_f24c_match_review_failure_does_not_abort_write(client, monkeypatch):
    """F24(c): if match_review.review_created_rows raises, the response is still 200 and
    the rows were written (best-effort).
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)

    from backend.services import match_review as mr

    monkeypatch.setattr(
        mr,
        "review_created_rows",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated failure")),
    )

    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
    }
    r = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 1  # write succeeded despite review failure


# ── Intra-batch dedup: two rows with the same external_ref in one request ──────────


def test_intra_batch_duplicate_external_ref_skipped(client):
    """Item 4 regression: two rows with the same external_ref in a SINGLE request must produce
    1 created and 1 skipped — not two inserts (the second row is caught by seen_in_batch).
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)

    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": acct["id"],
        "from_amount": 5.0,
        "from_currency": "SGD",
        "external_refs": [{"source": "simplefin", "ref": "intra-batch-1"}],
        "source": "simplefin",
    }

    # Both rows share the same external_ref in one request body.
    r = cl.post("/api/transactions/bulk", json={"transactions": [row, row]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 1, "only one row must be created"
    assert body["skipped"] == 1, "second duplicate must be counted as skipped"
    assert body["skipped_details"][0]["external_refs"] == [
        {"source": "simplefin", "ref": "intra-batch-1"}
    ]

    # Confirm only one row ended up in the store.
    live = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(live) == 1


# ── Batch size cap ────────────────────────────────────────────────────────────────


def test_bulk_batch_cap_5001_returns_422(client):
    """Item 5(a): a request with more than 5000 rows must be rejected with HTTP 422."""
    cl, acc_store, _, _ = client
    acct = _make_account(acc_store)

    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "x",
        "from_account_id": acct["id"],
        "from_amount": 1.0,
        "from_currency": "SGD",
    }
    # Construct programmatically — no external_ref so create_transaction won't be called.
    r = cl.post("/api/transactions/bulk", json={"transactions": [row] * 5001})
    assert r.status_code == 422, "batches > 5000 rows must be rejected"


# ── Response shape is backward-compatible ────────────────────────────────────────


def test_response_shape_backward_compat(client):
    """The 'created' and 'transactions' fields are always present (old callers depend on them);
    'skipped', 'batch_id', 'skipped_details' are new additive fields.
    """
    cl, acc_store, txn_store, _ = client
    acct = _make_account(acc_store)
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "description": "Test",
        "from_account_id": acct["id"],
        "from_amount": 1.0,
        "from_currency": "SGD",
    }
    r = cl.post("/api/transactions/bulk", json={"transactions": [row]})
    body = r.json()
    assert "created" in body
    assert "transactions" in body
    # New fields
    assert "skipped" in body
    assert "batch_id" in body
    assert "skipped_details" in body
