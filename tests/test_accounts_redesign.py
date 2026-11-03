"""Backend tests for the Accounts-tab redesign (Lane BE).

Covers the 7 deliverables: account-model extension + coercion, transaction needs_review
(set-on-import + approve), source-vocab mapping, the provenance/activity endpoint, the
describe-an-account service + endpoint, and Lucius-tool parity.

Offline + isolated: a minimal FastAPI app mounts only the ledger router and every store is
repointed at tmp_path (the audit stores too) so nothing touches live data.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core import txn_sources
from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.db.account_store import TYPES, _make_account
from backend.db.institution_store import InstitutionStore
from backend.services import account_describe
from backend.services import audit as audit_svc
from backend.services import ledger as svc


@pytest.fixture
def iso(tmp_path, monkeypatch, db_isolated):
    """Repoint the ledger + audit stores at tmp_path and pin SGD / identity FX.

    accounts/transactions/txn_audit are DB-backed now (plan 3 task 4) — db_isolated
    redirects every store instance (fresh or the shared services.ledger/audit
    singletons) to a fresh, empty database."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        svc, "to_base", lambda amt, cur, base=None, rates=None: round(float(amt or 0), 2)
    )
    return tmp_path


@pytest.fixture
def client(iso):
    from backend.main import log_validation_error

    app = FastAPI()
    app.include_router(ledger_router)
    app.add_exception_handler(RequestValidationError, log_validation_error)
    return TestClient(app)


def _acct(**over):
    base = {
        "name": "Meridian Bank",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 100.0,
        "initial_balance_date": "2026-01-01",
    }
    base.update(over)
    return svc.accounts.create(base)


# ── Deliverable 1: account model extension + coercion ────────────────────────────


def test_institution_registry_lists_unused_institutions_for_account_picker(client, iso):
    dbs = InstitutionStore().resolve_or_create("DBS")
    apex = InstitutionStore().resolve_or_create("Apex Securities")

    response = client.get("/api/institutions")

    assert response.status_code == 200
    assert response.json() == {
        "institutions": [
            {"id": apex["id"], "name": "Apex Securities", "account_count": 0, "account_names": []},
            {"id": dbs["id"], "name": "DBS", "account_count": 0, "account_names": []},
        ]
    }


def test_institution_endpoints_rename_and_unlink_on_delete(client, iso):
    institution = InstitutionStore().resolve_or_create("DBS")
    svc.accounts.insert(
        {
            "id": "acc_dbs",
            "name": "DBS account",
            "type": "cash",
            "currency": "SGD",
            "institution_id": institution["id"],
        }
    )

    renamed = client.patch(f"/api/institutions/{institution['id']}", json={"name": "DBS Bank"})
    deleted = client.delete(f"/api/institutions/{institution['id']}")

    assert renamed.status_code == 200
    assert renamed.json() == {
        "id": institution["id"],
        "name": "DBS Bank",
        "account_count": 1,
        "account_names": ["DBS account"],
    }
    assert deleted.status_code == 200
    assert deleted.json() == {
        "id": institution["id"],
        "unlinked_account_count": 1,
        "unlinked_account_ids": ["acc_dbs"],
    }
    assert svc.accounts.find_by_id("acc_dbs")["institution_id"] is None


def test_account_type_set_is_the_seven_type_vocabulary():
    for t in ("cash", "fixed_deposit", "investment", "retirement", "receivable", "credit", "loan"):
        assert t in TYPES
    assert len(TYPES) == 7


def test_make_account_coerces_new_fields(iso):
    a = _make_account(
        {
            "name": "Mortgage",
            "type": "loan",
            "currency": "sgd",
            "connected": "yes",
            "apr": "2.5",
            "principal": 620000,
            "payoff_date": "2046-01-01",
            "min_payment": "3000",
            "apy": "",
        }
    )
    assert a["connected"] is True
    assert a["apr"] == 2.5 and a["principal"] == 620000.0 and a["min_payment"] == 3000.0
    assert a["apy"] is None  # blank coerces to None, never 0.0
    assert a["payoff_date"] == "2046-01-01"
    assert not {"asset_class", "locked", "on_budget", "group"} & set(a)


def test_make_account_new_fields_default_empty(iso):
    a = _make_account({"name": "Cash", "type": "cash"})
    assert a["connected"] is False
    assert a["apr"] is None and a["apy"] is None
    assert not {"asset_class", "locked", "on_budget", "group"} & set(a)


def test_post_account_accepts_and_echoes_new_fields(client):
    body = {
        "name": "Meridian Bank Home Loan",
        "type": "loan",
        "currency": "SGD",
        "initial_balance": -612000,
        "apr": 2.4,
        "principal": 620000,
        "payoff_date": "2046-01-01",
        "min_payment": 3000,
    }
    r = client.post("/api/accounts", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["type"] == "loan"
    # `connected` is DERIVED from external_ids (RFC connectors-as-data-sources §D) — never
    # caller-writable, so a fresh account with no links is always False.
    assert out["connected"] is False
    assert out["apr"] == 2.4 and out["principal"] == 620000.0
    assert not {"asset_class", "locked", "on_budget", "group"} & set(out)
    # Regression: the create response must carry HYDRATED balances — a missing field renders
    # as "NaN" in the client's balance card / CC due alert after an add.
    for k in ("balance", "balance_base", "cleared_balance", "cleared_balance_base"):
        assert isinstance(out.get(k), (int, float)), f"create response missing numeric {k}"


def test_put_credit_cycle_does_not_seed_a_reminder_automation(client):
    """Cycle dates describe the card; reminder creation belongs to Automations/Lucius."""
    from backend.db.automation_store import AutomationStore

    created = client.post(
        "/api/accounts",
        json={
            "name": "Rewards card",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 0,
        },
    )
    assert created.status_code == 200
    before = len(AutomationStore().all_persisted())

    updated = client.put(
        f"/api/accounts/{created.json()['id']}",
        json={
            "statement_close_day": 25,
            "payment_due_day": 15,
            "reminder_days_before": 3,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["statement_close_day"] == 25
    assert updated.json()["payment_due_day"] == 15
    assert len(AutomationStore().all_persisted()) == before


def test_legacy_reminder_days_is_readable_but_not_writable(client):
    from backend.api.ledger import AccountUpdate

    created = client.post(
        "/api/accounts",
        json={
            "name": "Legacy reminder card",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 0,
        },
    )
    assert created.status_code == 200
    account_id = created.json()["id"]
    svc.accounts.update_by_id(account_id, {"reminder_days_before": 4})

    fetched = client.get(f"/api/accounts/{account_id}")

    assert fetched.status_code == 200
    assert fetched.json()["reminder_days_before"] == 4
    assert "reminder_days_before" not in AccountUpdate.model_json_schema()["properties"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_post_account_rejects_non_finite_opening_balance(client, value):
    r = client.post(
        "/api/accounts",
        content=f'{{"name":"Unsafe","type":"cash","currency":"SGD","initial_balance":{value}}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert "finite number" in r.text


def test_post_account_ignores_dead_fields_and_derives_kind(client):
    r = client.post(
        "/api/accounts",
        json={
            "name": "HDB",
            "type": "loan",
            "currency": "SGD",
            "initial_balance": 0,
            "kind": "asset",
            "locked": True,
            "on_budget": True,
            "group": "cash",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "liability"
    assert not {"asset_class", "locked", "on_budget", "group"} & set(body)


def test_post_account_refuses_old_type(client):
    r = client.post("/api/accounts", json={"name": "X", "type": "checking", "currency": "SGD"})
    assert r.status_code in (400, 422)


def test_get_single_account_returns_hydrated_balance(client):
    # The single-account refresh the client calls after a txn write — was missing (404), so
    # sidebar balances went stale after an edit. Must exist + carry hydrated balances.
    a = _acct(type="cash")
    r = client.get(f"/api/accounts/{a['id']}")
    assert r.status_code == 200
    out = r.json()
    assert out["id"] == a["id"]
    for k in ("balance", "balance_base", "cleared_balance", "cleared_balance_base"):
        assert isinstance(out.get(k), (int, float)), f"missing numeric {k}"
    assert client.get("/api/accounts/does-not-exist").status_code == 404


def test_put_account_updates_new_fields(client):
    a = _acct(type="cash")
    r = client.put(f"/api/accounts/{a['id']}", json={"apy": 3.1})
    assert r.status_code == 200
    out = r.json()
    # `connected` is DERIVED, not caller-writable — no link, so it stays False.
    assert out["apy"] == 3.1 and out["connected"] is False
    assert not {"asset_class", "locked", "on_budget", "group"} & set(out)
    # Regression: the UPDATE response must also carry hydrated balances (this is the path
    # that produced the Meridian Bank card "S$NaN" after an edit replaced the row with a balance-less one).
    for k in ("balance", "balance_base", "cleared_balance", "cleared_balance_base"):
        assert isinstance(out.get(k), (int, float)), f"update response missing numeric {k}"


def test_put_account_refuses_a_type_change_before_any_payment_envelope_side_effect(client):
    card = _acct(name="Citi Rewards", type="credit", initial_balance=0.0)
    before = svc.accounts.find_by_id(card["id"])["payment_envelope_id"]

    r = client.put(f"/api/accounts/{card['id']}", json={"type": "cash"})

    assert r.status_code == 422
    assert "cannot be changed" in r.text
    assert svc.accounts.find_by_id(card["id"])["payment_envelope_id"] == before


def test_credit_create_reports_success_when_post_commit_envelope_sync_fails(client, monkeypatch):
    """The account already committed; a sync outage must never invite a duplicate retry."""
    from backend.services import planning

    def fail_sync():
        raise RuntimeError("synthetic envelope sync outage")

    monkeypatch.setattr(planning, "sync_card_payment_envelopes", fail_sync)
    r = client.post(
        "/api/accounts",
        json={
            "name": "Atomic Card",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 0,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_envelope_sync_warning"]["retryable"] is True
    stored = svc.accounts.find_by_id(body["id"])
    assert stored is not None and stored["opening_txn_id"]


def test_create_account_discloses_an_incidentally_retired_UNRELATED_envelope(client):
    """Review 2026-09-10, bug 1. `sync_card_payment_envelopes()` is a GLOBAL reconciliation —
    minting a brand-new card's own envelope can, in the SAME pass, retire a DIFFERENT card's
    already-stale one (e.g. one archived earlier: archiving alone never triggers a sync, only a
    `type` flip does, so the stale FK just sits there until some later call sweeps it up).
    Before this fix `create_account` never threaded `side_effects` at all — that retirement,
    real assigned money zeroed, was discarded with NO disclosure anywhere. It must now surface,
    and — since the brand-new account did not cause it — under `unrelated_retired_payment_envelopes`,
    never the `retired_payment_envelopes` key reserved for a write's OWN destructive effect."""
    from backend.services import envelopes as envelopes_service

    # Card A: created for real (through the POST choke, so its own sync mints an envelope),
    # funded, then archived — archiving alone does NOT re-run the sync, so its FK survives.
    r = client.post(
        "/api/accounts", json={"name": "Card A", "type": "credit", "initial_balance": 0.0}
    )
    assert r.status_code == 200
    card_a_id = r.json()["id"]
    env_a_id = svc.accounts.find_by_id(card_a_id)["payment_envelope_id"]
    assert env_a_id
    envelopes_service.assignments_store.set_assigned("2026-06", env_a_id, 50.0)
    r = client.put(f"/api/accounts/{card_a_id}", json={"archived": True})
    assert r.status_code == 200
    assert "retired_payment_envelopes" not in r.json()  # archiving alone syncs nothing
    assert not envelopes_service.envelope_store.find_by_id(env_a_id)["archived"]

    # Card C: a brand-new credit account. Its OWN create-time sync sweeps the whole book —
    # Card A no longer qualifies (archived), so its stale envelope is retired in this same pass.
    r = client.post(
        "/api/accounts", json={"name": "Card C", "type": "credit", "initial_balance": 0.0}
    )
    assert r.status_code == 200
    body = r.json()
    assert "retired_payment_envelopes" not in body  # nothing of Card C's OWN was retired
    unrelated = body["unrelated_retired_payment_envelopes"]
    assert unrelated == [
        {
            "account_id": card_a_id,
            "envelope_id": env_a_id,
            "name": "Card A",
            "zeroed_assignments": {"2026-06": 50.0},
            "zeroed_total": 50.0,
        }
    ]
    assert envelopes_service.envelope_store.find_by_id(env_a_id) is None


def test_put_account_external_ids_links_and_derives_connected(client):
    """RFC connectors-as-data-sources §D: linking a provider account sets `connected`
    (derived, never independently writable) and fills blank last_four/institution from the
    entry's mask/institution."""
    a = _acct(type="cash")
    r = client.put(
        f"/api/accounts/{a['id']}",
        json={
            "external_ids": [
                {"source": "plaid", "ref": "acc_123", "mask": "4242", "institution": "Chase"}
            ]
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["connected"] is True
    assert out["last_four"] == "4242"
    assert out["institution"] == "Chase"
    assert out["external_ids"][0]["source"] == "plaid"
    assert out["external_ids"][0]["provider_label"]  # server-side label, never blank

    # Unlinking (an explicit empty list) flips `connected` back to False.
    r = client.put(f"/api/accounts/{a['id']}", json={"external_ids": []})
    assert r.status_code == 200
    assert r.json()["connected"] is False


def test_accounts_resolve_one_shared_institution_and_reject_platform(client):
    first = client.post(
        "/api/accounts",
        json={"name": "DBS SGD", "type": "cash", "institution": "DBS", "initial_balance": 0},
    )
    second = client.post(
        "/api/accounts",
        json={"name": "DBS USD", "type": "cash", "institution": " dbs ", "initial_balance": 0},
    )

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["institution"] == second.json()["institution"] == "DBS"
    assert len(InstitutionStore().all()) == 1
    rejected = client.post(
        "/api/accounts",
        json={"name": "Old field", "type": "cash", "platform": "DBS", "initial_balance": 0},
    )
    assert rejected.status_code == 422


def test_put_account_external_ids_collision_names_the_holder(client):
    a = _acct(type="cash")
    b = _acct(type="cash")
    client.put(
        f"/api/accounts/{a['id']}",
        json={"external_ids": [{"source": "plaid", "ref": "acc_dup"}]},
    )
    r = client.put(
        f"/api/accounts/{b['id']}",
        json={"external_ids": [{"source": "plaid", "ref": "acc_dup"}]},
    )
    assert r.status_code >= 400
    assert a["id"] in r.text


# ── Deliverable 2: transaction needs_review ──────────────────────────────────────


def test_needs_review_set_on_import_and_ai(iso):
    a = _acct()
    imported, _ = svc.bulk_create_transactions(
        [
            {
                "date": "2026-06-10",
                "kind": "expense",
                "from_account_id": a["id"],
                "from_amount": 20,
                "from_currency": "SGD",
                "source": "csv_import",
            }
        ]
    )
    assert imported[0]["needs_review"] is True
    ai_row = svc.create_transaction(
        {
            "date": "2026-06-11",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 9,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    assert ai_row["needs_review"] is True


def test_needs_review_set_for_unattended_agent_and_email(iso):
    # Owner decision 2026-06-29: unattended automated entries (agent runs, email-parsed
    # receipts) land in the review queue, same as interactive AI / imports.
    a = _acct()
    agent_row = svc.create_transaction(
        {
            "date": "2026-06-14",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 7,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    assert agent_row["needs_review"] is True
    email_row = svc.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 8,
            "from_currency": "SGD",
            "source": "email",
        }
    )
    assert email_row["needs_review"] is True


def test_unattended_agent_run_remaps_source_and_flags_review(iso):
    # The real agent flow: it logs through the AI tools (source defaults to "ai") under an
    # agent audit scope, which the choke remaps ai→agent. With "agent" now review-eligible,
    # the remapped row must land in the queue (it previously slipped through).
    from backend.core import audit_context

    a = _acct()
    audit_context.set_audit_scope(actor="agent", surface="agent:ttl")
    try:
        row = svc.create_transaction(
            {
                "date": "2026-06-16",
                "kind": "expense",
                "from_account_id": a["id"],
                "from_amount": 4,
                "from_currency": "SGD",
                "source": "ai",
            }
        )
    finally:
        audit_context.clear_audit_scope()
    assert row["source"] == "agent"  # ai→agent remap held
    assert row["needs_review"] is True  # and it's flagged for review


def test_unattended_agent_bulk_run_remaps_ai_source_but_preserves_explicit_source(iso):
    from backend.core import audit_context

    a = _acct()
    audit_context.set_audit_scope(actor="agent", surface="agent:transaction-logger")
    try:
        rows, _ = svc.bulk_create_transactions(
            [
                {
                    "date": "2026-06-16",
                    "kind": "expense",
                    "from_account_id": a["id"],
                    "from_amount": 4,
                    "from_currency": "SGD",
                    "source": "ai",
                },
                {
                    "date": "2026-06-17",
                    "kind": "expense",
                    "from_account_id": a["id"],
                    "from_amount": 5,
                    "from_currency": "SGD",
                    "source": "email",
                },
                {
                    "date": "2026-06-18",
                    "kind": "expense",
                    "from_account_id": a["id"],
                    "from_amount": 6,
                    "from_currency": "SGD",
                },
            ]
        )
        svc.update_transaction(rows[0]["id"], {"date": "2026-06-19"})
    finally:
        audit_context.clear_audit_scope()

    update = audit_svc.activity(rows[0]["id"])["activity"][-1]
    assert {"field": "date", "from": "2026-06-16", "to": "2026-06-19"} in update["changes"]
    assert "source" not in {change["field"] for change in update["changes"]}
    assert [row["source"] for row in rows] == ["agent", "email", "agent"]


def test_needs_review_false_for_manual_and_bank(iso):
    a = _acct()
    manual = svc.create_transaction(
        {
            "date": "2026-06-12",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 5,
            "from_currency": "SGD",
            "source": "manual",
        }
    )
    assert manual["needs_review"] is False
    synced, _ = svc.bulk_create_transactions(
        [
            {
                "date": "2026-06-13",
                "kind": "expense",
                "from_account_id": a["id"],
                "from_amount": 6,
                "from_currency": "SGD",
                "source": "simplefin",
            }
        ]
    )
    assert synced[0]["needs_review"] is False


def test_explicit_needs_review_wins_over_source_default(iso):
    a = _acct()
    row = svc.create_transaction(
        {
            "date": "2026-06-14",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 7,
            "from_currency": "SGD",
            "source": "csv_import",
            "needs_review": False,
        }
    )
    assert row["needs_review"] is False


def test_bulk_update_approve_clears_review(client, iso):
    a = _acct()
    row = svc.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 8,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    assert row["needs_review"] is True
    r = client.post(
        "/api/transactions/bulk-update", json={"ids": [row["id"]], "needs_review": False}
    )
    assert r.status_code == 200 and r.json()["count"] == 1
    assert svc.transactions.find_by_id(row["id"])["needs_review"] is False


def test_bulk_update_move_repoints_leg(client, iso):
    a = _acct(name="A")
    b = _acct(name="B")
    row = svc.create_transaction(
        {
            "date": "2026-06-16",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 12,
            "from_currency": "SGD",
            "source": "manual",
        }
    )
    r = client.post(
        "/api/transactions/bulk-update", json={"ids": [row["id"]], "to_account_id": b["id"]}
    )
    assert r.status_code == 200 and r.json()["count"] == 1
    assert svc.transactions.find_by_id(row["id"])["from_account_id"] == b["id"]


def test_bulk_update_move_unknown_target_404(client, iso):
    a = _acct()
    row = svc.create_transaction(
        {
            "date": "2026-06-17",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 3,
            "from_currency": "SGD",
        }
    )
    r = client.post(
        "/api/transactions/bulk-update", json={"ids": [row["id"]], "to_account_id": "nope"}
    )
    assert r.status_code == 404


def test_bulk_update_move_skips_transfers(iso):
    a = _acct(name="A")
    b = _acct(name="B")
    c = _acct(name="C")
    tr = svc.create_transaction(
        {
            "date": "2026-06-18",
            "kind": "transfer",
            "from_account_id": a["id"],
            "from_amount": 50,
            "from_currency": "SGD",
            "to_account_id": b["id"],
            "to_amount": 50,
            "to_currency": "SGD",
        }
    )
    result = svc.bulk_update_transactions([tr["id"]], {"to_account_id": c["id"]})
    assert result["count"] == 0
    assert result["skipped"][0]["reason"] == "move not supported on transfers"


# ── Deliverable 3: source vocab mapping ──────────────────────────────────────────


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("manual", "manual"),
        ("ai", "ai"),
        ("csv_import", "import"),
        ("qif_import", "import"),
        ("email", "agent"),
        ("telegram", "manual"),
        ("simplefin", "bank"),
        ("ibkr", "bank"),
        ("recurring", "automation"),
        ("reconcile", "reconcile"),
        # A PRESENT but unrecognised writer must never render as "You" — that made a
        # plugin's rows claim the owner hand-entered them (RFC 2026-08-02). An ABSENT
        # source still defaults to "manual"; the two are different facts.
        ("totally-unknown", "import"),
        ("", "manual"),
    ],
)
def test_display_source_mapping(stored, expected):
    assert txn_sources.display_source(stored) == expected


def test_display_source_adjustment_reads_reconcile():
    assert txn_sources.display_source("manual", kind="adjustment") == "reconcile"
    # a richer recorded source on an adjustment is preserved.
    assert txn_sources.display_source("ai", kind="adjustment") == "ai"


def test_get_transactions_maps_source_and_surfaces_review(client, iso):
    a = _acct()
    svc.transactions.append(
        {
            "date": "2026-06-19",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 4,
            "from_currency": "SGD",
            "source": "csv_import",
            "needs_review": True,
        }
    )
    rows = client.get(f"/api/transactions?account_id={a['id']}").json()
    assert rows[0]["source"] == "import"  # csv_import → import
    assert rows[0]["needs_review"] is True


# ── Deliverable 4: provenance / activity endpoint ────────────────────────────────


def test_activity_endpoint_shape(client, iso):
    a = _acct()
    created = client.post(
        "/api/transactions",
        json={
            "date": "2026-06-20",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 11,
            "from_currency": "SGD",
        },
    ).json()
    r = client.get(f"/api/transactions/{created['id']}/activity")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"source", "addedBy", "createdAt", "updatedAt", "activity"}
    assert body["source"] == "manual" and body["addedBy"] == "You"
    assert body["activity"][0]["action"] == "created"
    assert body["activity"][0]["actor"] == "you"


def test_activity_endpoint_404_for_missing(client, iso):
    assert client.get("/api/transactions/does-not-exist/activity").status_code == 404


# ── Deliverable 5: describe-an-account service + endpoint ─────────────────────────


def _fake_complete(payload: dict):
    # complete_json calls llm_seam.complete with extra kwargs (response_format, effort) —
    # absorb them with **kw so the stub stands in for the real signature.
    async def _c(prompt, *, system="", model="", max_tokens=2048, source="", **kw):
        return json.dumps(payload)

    return _c


@pytest.mark.asyncio
async def test_describe_account_service_coerces_draft(monkeypatch):
    monkeypatch.setattr(
        account_describe.llm_seam,
        "complete",
        _fake_complete(
            {
                "type": "cash",
                "name": "Meridian Bank Multiplier",
                "currency": "sgd",
                "initial_balance": 12000,
                "institution": "Meridian Bank",
                "last_four": "card 8821",
            }
        ),
    )
    draft = await account_describe.describe_account("my meridian bank savings, around 12k")
    assert draft["type"] == "cash"
    assert draft["currency"] == "SGD" and draft["initial_balance"] == 12000.0
    assert draft["institution"] == "Meridian Bank"
    assert draft["last_four"] == "8821"  # digits-only, last 4
    # `kind` is DERIVED from type — a draft that carried it invited the form to send it
    # back, and the create choke refuses a client-supplied `kind`.
    assert not {"kind", "group", "band", "asset_class"} & set(draft)


@pytest.mark.asyncio
async def test_describe_account_invalid_type_is_dropped(monkeypatch):
    monkeypatch.setattr(
        account_describe.llm_seam,
        "complete",
        _fake_complete({"type": "credit", "name": "Amex", "credit_limit": 20000}),
    )
    draft = await account_describe.describe_account("amex platinum, 20k limit")
    assert draft["type"] == "credit"
    assert draft["credit_limit"] == 20000.0
    # and an out-of-set type is dropped
    monkeypatch.setattr(
        account_describe.llm_seam, "complete", _fake_complete({"type": "spaceship", "name": "X"})
    )
    draft2 = await account_describe.describe_account("a weird account")
    assert draft2["type"] is None


@pytest.mark.asyncio
async def test_describe_account_empty_text_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        await account_describe.describe_account("   ")


@pytest.mark.asyncio
async def test_describe_account_unavailable_llm_raises(monkeypatch):
    async def _boom(*a, **k):
        raise account_describe.llm_seam.LLMUnavailable("no provider")

    monkeypatch.setattr(account_describe.llm_seam, "complete", _boom)
    with pytest.raises(ExternalServiceError):
        await account_describe.describe_account("anything")


def test_describe_account_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        account_describe.llm_seam,
        "complete",
        _fake_complete({"type": "cash", "name": "Lighthouse Bank", "currency": "sgd"}),
    )
    r = client.post("/api/accounts/describe", json={"text": "my lighthouse bank checking"})
    assert r.status_code == 200
    draft = r.json()
    assert draft["type"] == "cash" and draft["currency"] == "SGD"


# ── Deliverable 6: Lucius tool parity ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_accounts_tool_writes_new_fields(iso):
    from backend.ai.tools import accounts as acct_tools

    a = _acct(type="cash")
    out, _ = await acct_tools.update_accounts({"account_id": a["id"], "apy": 3.25})
    body = json.loads(out)
    assert body["ok"] is True
    # `connected` is DERIVED, not a tool-writable field (RFC connectors-as-data-sources §D).
    assert body["account"]["apy"] == 3.25 and body["account"]["connected"] is False


@pytest.mark.asyncio
async def test_describe_account_tool(iso, monkeypatch):
    from backend.ai.tools import accounts as acct_tools

    monkeypatch.setattr(
        account_describe.llm_seam,
        "complete",
        _fake_complete({"type": "cash", "name": "Compass", "currency": "usd"}),
    )
    out, _ = await acct_tools.describe_account({"text": "my compass usd wallet"})
    body = json.loads(out)
    assert body["ok"] is True
    assert body["draft"]["type"] == "cash" and body["draft"]["currency"] == "USD"


@pytest.mark.asyncio
async def test_bulk_update_tool_moves_but_no_longer_approves(iso):
    """Approving left ``update_transactions`` (ADR 2026-10-04): the legacy bulk shape that used to
    approve and move in one call is refused whole — nothing moves — and points at
    ``approve_transactions``. The move itself, without the flag, still works."""
    from backend.ai.tools import ledger as ledger_tools

    a = _acct(name="A")
    b = _acct(name="B")
    row = svc.create_transaction(
        {
            "date": "2026-06-21",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 14,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    out, _ = await ledger_tools.update_transactions(
        {"transaction_ids": [row["id"]], "needs_review": False, "to_account_id": b["id"]}
    )
    refused = json.loads(out)
    assert refused["ok"] is False and "approve_transactions" in refused["error"]
    untouched = svc.transactions.find_by_id(row["id"])
    assert untouched["needs_review"] is True and untouched["from_account_id"] == a["id"]

    out, _ = await ledger_tools.update_transactions(
        {"transaction_ids": [row["id"]], "to_account_id": b["id"]}
    )
    assert json.loads(out)["ok"] is True
    moved = svc.transactions.find_by_id(row["id"])
    assert moved["from_account_id"] == b["id"] and moved["needs_review"] is True


def test_bulk_update_service_rejects_unknown_field(iso):
    a = _acct()
    row = svc.transactions.append(
        {
            "date": "2026-06-22",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 1,
            "from_currency": "SGD",
        }
    )
    with pytest.raises(ValidationError):
        svc.bulk_update_transactions([row["id"]], {"bogus": 1})


def test_bulk_update_move_unknown_target_raises_not_found(iso):
    a = _acct()
    row = svc.transactions.append(
        {
            "date": "2026-06-23",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 2,
            "from_currency": "SGD",
        }
    )
    with pytest.raises(NotFoundError):
        svc.bulk_update_transactions([row["id"]], {"to_account_id": "ghost"})
