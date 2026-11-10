"""
TestClient endpoint tests for backend/api/payees.py.

Covers every route in the payees REST API:
  POST   /api/payees                       create
  GET    /api/payees                       list (first-class entities)
  GET    /api/payees/report                list (derived spend report)
  PATCH  /api/payees/{id}                  update
  DELETE /api/payees/{id}                  delete
  DELETE /api/payees/{id}?reassign_to=X    delete + reassign
  POST   /api/payees/{id}/merge            merge
  POST   /api/payees/{id}/aliases          add alias
  DELETE /api/payees/{id}/aliases/{alias}  remove alias
  POST   /api/payees/{id}/rules            add rule
  DELETE /api/payees/{id}/rules/{idx}      remove rule
  PUT    /api/payees/{id}/rules            set/replace all rules
  POST   /api/payees/{id}/apply-category   apply default category to history
  POST   /api/payees/{id}/apply-rules      apply this payee's rules to existing txns
  POST   /api/payees/apply-rules           apply all rules to history (confirm gate)

Hermetic: all stores redirect to tmp_path; no network, no live data files.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.payees import router
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    """Isolated TestClient: PayeeStore is DB-backed now (plan 4d task 2) — db_isolated
    redirects it (and every other unpinned store, transactions included — plan 3 task 4)
    to a fresh, empty database. payee_registry's module-level ``_store`` singleton resolves
    its engine lazily, so no per-store monkeypatch is needed any more."""
    # Also stub out the derived-payees list_payees (reads txns + FX) so
    # GET /api/payees/report doesn't fall over with missing FX data.
    import backend.services.payees as payees_svc

    monkeypatch.setattr(
        payees_svc,
        "list_payees",
        lambda: {"payees": [], "base_currency": "SGD", "count": 0},
    )
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def seeded(client, tmp_path, monkeypatch):
    """Create two payees and return their IDs."""
    r1 = client.post("/api/payees", json={"name": "Netflix"})
    r2 = client.post("/api/payees", json={"name": "Spotify"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    return {
        "p1": r1.json()["id"],
        "p2": r2.json()["id"],
        "p1_name": r1.json()["name"],
        "p2_name": r2.json()["name"],
    }


# ── POST /api/payees — create ─────────────────────────────────────────────────


def test_create_payee_200_returns_id_and_name(client):
    resp = client.post("/api/payees", json={"name": "Amazon"})
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert body["name"] == "Amazon"
    assert body["id"].startswith("pay_")


def test_create_payee_sets_default_category(client):
    # default_category resolves through the same normalization update_payee uses — a label
    # ("shopping") resolves to its stored category id (built-in "Shopping").
    resp = client.post("/api/payees", json={"name": "Starbucks", "default_category": "shopping"})
    assert resp.status_code == 200
    assert resp.json()["default_categories"] == {"expense": "cat_6c4bc0170000"}


def test_create_payee_empty_name_422(client):
    resp = client.post("/api/payees", json={"name": ""})
    assert resp.status_code == 422


def test_create_payee_conflict_same_normalized_name_409(client):
    client.post("/api/payees", json={"name": "Netflix"})
    resp = client.post("/api/payees", json={"name": "NETFLIX"})
    assert resp.status_code == 409


def test_create_payee_structural_category_422(client):
    """Cannot set a structural/kind-name key as default_category."""
    resp = client.post("/api/payees", json={"name": "SomePayee", "default_category": "income"})
    assert resp.status_code == 422


# ── GET /api/payees/report — derived spend report ────────────────────────────


def test_get_payees_report_200_shape(client):
    resp = client.get("/api/payees/report")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"payees", "base_currency", "count"}
    assert isinstance(body["payees"], list)
    assert isinstance(body["count"], int)


# ── GET /api/payees — first-class entities ───────────────────────────────────


def test_get_payees_200_shape(client):
    resp = client.get("/api/payees")
    assert resp.status_code == 200
    body = resp.json()
    assert "payees" in body and "count" in body
    assert isinstance(body["payees"], list)


def test_get_payees_returns_created_payee(client, seeded):
    resp = client.get("/api/payees")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    ids = {p["id"] for p in body["payees"]}
    assert seeded["p1"] in ids
    assert seeded["p2"] in ids


def test_get_payees_row_has_required_keys(client, seeded):
    """The picker list: everything a picker needs, no usage numbers (Manage Payees asks
    for those with ``usage=true``)."""
    row = client.get("/api/payees").json()["payees"][0]
    assert {
        "id",
        "name",
        "default_categories",
        "autocategorize",
        "hidden",
        "aliases",
        "rules",
    } <= set(row)
    assert not {"txn_count", "last_used"} & set(row)

    with_usage = client.get("/api/payees?usage=true").json()["payees"][0]
    assert {"txn_count", "last_used"} <= set(with_usage)


# ── PATCH /api/payees/{id} — update ──────────────────────────────────────────


def test_update_payee_name_200(client, seeded):
    resp = client.patch(f"/api/payees/{seeded['p1']}", json={"name": "Netflix Inc"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Netflix Inc"


def test_update_payee_rename_onto_existing_name_409(client, seeded):
    """Regression: rename must honour the same uniqueness rule as create (409, not 500)."""
    other = client.post("/api/payees", json={"name": "Hulu"}).json()["id"]
    resp = client.patch(f"/api/payees/{other}", json={"name": "Netflix"})
    assert resp.status_code == 409
    assert client.get("/api/payees").json()["payees"]  # registry intact


def test_update_payee_default_category_200(client, seeded):
    # default_categories resolves a label to its stored category id (built-in "Entertainment").
    resp = client.patch(
        f"/api/payees/{seeded['p1']}", json={"default_categories": {"expense": "entertainment"}}
    )
    assert resp.status_code == 200
    assert resp.json()["default_categories"] == {"expense": "cat_dfcf9f910000"}


def test_update_payee_clear_default_category(client, seeded):
    # First set it
    client.patch(
        f"/api/payees/{seeded['p1']}", json={"default_categories": {"expense": "entertainment"}}
    )
    # Now clear it
    resp = client.patch(f"/api/payees/{seeded['p1']}", json={"default_categories": {}})
    assert resp.status_code == 200
    assert resp.json()["default_categories"] == {}


def test_update_payee_explicit_null_pins_uncategorized(client, seeded):
    # The editor sends null for "Uncategorized on purpose" — it must survive request parsing.
    resp = client.patch(
        f"/api/payees/{seeded['p1']}", json={"default_categories": {"expense": None}}
    )
    assert resp.status_code == 200
    assert resp.json()["default_categories"] == {}
    assert resp.json()["uncategorized_kinds"] == ["expense"]


def test_update_payee_autocategorize_flag(client, seeded):
    resp = client.patch(f"/api/payees/{seeded['p1']}", json={"autocategorize": False})
    assert resp.status_code == 200
    assert resp.json()["autocategorize"] is False


def test_update_payee_hidden_flag(client, seeded):
    resp = client.patch(f"/api/payees/{seeded['p1']}", json={"hidden": True})
    assert resp.status_code == 200
    assert resp.json()["hidden"] is True


def test_update_payee_404_unknown(client):
    resp = client.patch("/api/payees/pay_doesnotexist", json={"name": "Ghost"})
    assert resp.status_code == 404


def test_update_payee_empty_name_422(client, seeded):
    resp = client.patch(f"/api/payees/{seeded['p1']}", json={"name": ""})
    assert resp.status_code == 422


def test_update_payee_structural_category_dropped(client, seeded):
    # default_categories is a bulk replace-map field: normalize_default_categories silently
    # drops a reserved/structural value rather than raising (default_category, the old
    # explicit-raise field, no longer exists).
    resp = client.patch(
        f"/api/payees/{seeded['p1']}", json={"default_categories": {"expense": "transfer"}}
    )
    assert resp.status_code == 200
    assert resp.json()["default_categories"] == {}


# ── DELETE /api/payees/{id} — delete ─────────────────────────────────────────


def test_delete_payee_200_returns_deleted_id(client, seeded):
    resp = client.delete(f"/api/payees/{seeded['p1']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == seeded["p1"]


def test_delete_payee_404_unknown(client):
    resp = client.delete("/api/payees/pay_doesnotexist")
    assert resp.status_code == 404


def test_delete_payee_removed_from_registry(client, seeded):
    client.delete(f"/api/payees/{seeded['p1']}")
    resp = client.get("/api/payees")
    ids = {p["id"] for p in resp.json()["payees"]}
    assert seeded["p1"] not in ids


def test_delete_payee_with_reassign_200(client, seeded, tmp_path):
    """Delete p1 and reassign its transactions to p2 — smoke that the param is accepted."""
    resp = client.delete(f"/api/payees/{seeded['p1']}?reassign_to={seeded['p2']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == seeded["p1"]


def test_delete_payee_reassign_to_invalid_404(client, seeded):
    """Reassign to a non-existent payee raises 404."""
    resp = client.delete(f"/api/payees/{seeded['p1']}?reassign_to=pay_nonexistent")
    assert resp.status_code == 404


# ── POST /api/payees/{id}/merge ───────────────────────────────────────────────


def test_merge_payee_200_source_removed(client, seeded):
    resp = client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": seeded["p2"]})
    assert resp.status_code == 200
    # Source should be gone
    registry = client.get("/api/payees").json()
    ids = {p["id"] for p in registry["payees"]}
    assert seeded["p1"] not in ids
    assert seeded["p2"] in ids


def test_merge_payee_returns_updated_target(client, seeded):
    resp = client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": seeded["p2"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == seeded["p2"]


def test_merge_payee_self_422(client, seeded):
    """Merging a payee into itself is a ValidationError → 422."""
    resp = client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": seeded["p1"]})
    assert resp.status_code == 422


def test_merge_payee_unknown_source_404(client, seeded):
    resp = client.post("/api/payees/pay_nosuchsource/merge", json={"target_id": seeded["p2"]})
    assert resp.status_code == 404


def test_merge_payee_unknown_target_404(client, seeded):
    resp = client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": "pay_nosuchtar"})
    assert resp.status_code == 404


def test_merge_folds_source_aliases_into_target(client, seeded):
    """Source aliases must be copied to target after merge."""
    # Add a distinct alias to p1 first
    client.post(f"/api/payees/{seeded['p1']}/aliases", json={"text": "NF Premium"})
    client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": seeded["p2"]})
    registry = client.get("/api/payees").json()
    target = next(p for p in registry["payees"] if p["id"] == seeded["p2"])
    # The target's aliases should now include the source's alias
    assert "nf premium" in target["aliases"]


def test_merge_folds_source_rules_into_target(client, seeded):
    """Source rules must be copied to target after merge, deduped against overlaps."""
    # Target already has a rule.
    client.put(
        f"/api/payees/{seeded['p2']}/rules",
        json={"rules": [{"match_type": "is", "value": "NETFLIX.COM"}]},
    )
    # Source has that same rule plus a distinct one — via PUT (not POST) to bypass
    # add_payee_rule's cross-payee conflict guard while seeding the overlap.
    client.put(
        f"/api/payees/{seeded['p1']}/rules",
        json={
            "rules": [
                {"match_type": "is", "value": "NETFLIX.COM"},
                {"match_type": "contains", "value": "netflix promo"},
            ]
        },
    )
    client.post(f"/api/payees/{seeded['p1']}/merge", json={"target_id": seeded["p2"]})
    registry = client.get("/api/payees").json()
    target = next(p for p in registry["payees"] if p["id"] == seeded["p2"])
    rules = target["rules"]
    assert {"match_type": "contains", "value": "netflix promo"} in rules
    assert rules.count({"match_type": "is", "value": "NETFLIX.COM"}) == 1


# ── POST /api/payees/{id}/aliases — add alias ─────────────────────────────────


def test_add_alias_200_alias_stored(client, seeded):
    resp = client.post(f"/api/payees/{seeded['p1']}/aliases", json={"text": "NFLX Streaming"})
    assert resp.status_code == 200
    body = resp.json()
    assert "nflx streaming" in body.get("aliases", [])


def test_add_alias_404_unknown_payee(client):
    resp = client.post("/api/payees/pay_ghost/aliases", json={"text": "Something"})
    assert resp.status_code == 404


def test_add_alias_empty_normalizes_to_empty_422(client, seeded):
    """An alias that normalizes to empty string (e.g. only a processor prefix) → 422."""
    resp = client.post(f"/api/payees/{seeded['p1']}/aliases", json={"text": "SQ *"})
    assert resp.status_code == 422


def test_add_alias_conflict_already_claimed_409(client, seeded):
    """Adding an alias already claimed by another payee → 409."""
    # p2 "Spotify" has alias "spotify" auto-created
    # Try to add that to p1
    resp = client.post(f"/api/payees/{seeded['p1']}/aliases", json={"text": "Spotify"})
    assert resp.status_code == 409


# ── DELETE /api/payees/{id}/aliases/{alias} — remove alias ───────────────────


def test_remove_alias_200(client, seeded):
    # Add alias first then remove it
    client.post(f"/api/payees/{seeded['p1']}/aliases", json={"text": "NFLX"})
    resp = client.delete(f"/api/payees/{seeded['p1']}/aliases/nflx")
    assert resp.status_code == 200
    # Verify removal
    registry = client.get("/api/payees").json()
    p1 = next(p for p in registry["payees"] if p["id"] == seeded["p1"])
    assert "nflx" not in p1["aliases"]


def test_remove_alias_404_unknown_payee(client):
    resp = client.delete("/api/payees/pay_ghost/aliases/somealias")
    assert resp.status_code == 404


def test_remove_alias_noop_if_not_present(client, seeded):
    """Removing an alias that doesn't exist is a no-op, not an error."""
    resp = client.delete(f"/api/payees/{seeded['p1']}/aliases/nonexistentalias")
    assert resp.status_code == 200


# ── POST /api/payees/{id}/rules — add rule ────────────────────────────────────


def test_add_rule_contains_200(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "netflix"},
    )
    assert resp.status_code == 200
    rules = resp.json().get("rules", [])
    assert any(r["match_type"] == "contains" and r["value"] == "netflix" for r in rules)


def test_add_rule_is_match_200(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "is", "value": "Netflix Monthly Charge"},
    )
    assert resp.status_code == 200


def test_add_rule_regex_200(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "regex", "value": r"^netflix.*"},
    )
    assert resp.status_code == 200


def test_add_rule_404_unknown_payee(client):
    resp = client.post(
        "/api/payees/pay_ghost/rules",
        json={"match_type": "contains", "value": "test"},
    )
    assert resp.status_code == 404


def test_add_rule_invalid_match_type_422(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "startswith", "value": "Netflix"},
    )
    assert resp.status_code == 422


def test_add_rule_empty_value_422(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": ""},
    )
    assert resp.status_code == 422


def test_add_rule_invalid_regex_422(client, seeded):
    resp = client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "regex", "value": r"[invalid("},
    )
    assert resp.status_code == 422


def test_add_rule_conflict_same_rule_on_other_payee_409(client, seeded):
    """Same rule on two different payees → 409."""
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "stream charge"},
    )
    resp = client.post(
        f"/api/payees/{seeded['p2']}/rules",
        json={"match_type": "contains", "value": "stream charge"},
    )
    assert resp.status_code == 409


# ── DELETE /api/payees/{id}/rules/{index} — remove rule ──────────────────────


def test_remove_rule_200(client, seeded):
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "netflix"},
    )
    resp = client.delete(f"/api/payees/{seeded['p1']}/rules/0")
    assert resp.status_code == 200
    rules = resp.json().get("rules", [])
    assert rules == []


def test_remove_rule_404_unknown_payee(client):
    resp = client.delete("/api/payees/pay_ghost/rules/0")
    assert resp.status_code == 404


def test_remove_rule_out_of_range_422(client, seeded):
    """No rules exist; index 0 is out of range."""
    resp = client.delete(f"/api/payees/{seeded['p1']}/rules/0")
    assert resp.status_code == 422


def test_remove_rule_negative_index_422(client, seeded):
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "netflix"},
    )
    resp = client.delete(f"/api/payees/{seeded['p1']}/rules/-1")
    assert resp.status_code == 422


# ── PUT /api/payees/{id}/rules — set/replace rules ───────────────────────────


def test_set_rules_200_replaces_existing(client, seeded):
    # Add initial rule
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "old-value"},
    )
    resp = client.put(
        f"/api/payees/{seeded['p1']}/rules",
        json={"rules": [{"match_type": "is", "value": "New Exact Match"}]},
    )
    assert resp.status_code == 200
    rules = resp.json().get("rules", [])
    assert len(rules) == 1
    assert rules[0]["match_type"] == "is"
    assert rules[0]["value"] == "New Exact Match"


def test_set_rules_empty_clears_all(client, seeded):
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "netflix"},
    )
    resp = client.put(f"/api/payees/{seeded['p1']}/rules", json={"rules": []})
    assert resp.status_code == 200
    assert resp.json().get("rules", []) == []


def test_set_rules_404_unknown_payee(client):
    resp = client.put(
        "/api/payees/pay_ghost/rules",
        json={"rules": [{"match_type": "contains", "value": "x"}]},
    )
    assert resp.status_code == 404


def test_set_rules_invalid_rule_422(client, seeded):
    resp = client.put(
        f"/api/payees/{seeded['p1']}/rules",
        json={"rules": [{"match_type": "bad_type", "value": "x"}]},
    )
    assert resp.status_code == 422


# ── POST /api/payees/{id}/apply-category ─────────────────────────────────────


def test_apply_category_200_returns_updated_count(client, seeded, tmp_path):
    """Payee with a default_category + matching transactions → returns {updated: N}."""
    # Set default category on the payee
    client.patch(
        f"/api/payees/{seeded['p1']}", json={"default_categories": {"expense": "entertainment"}}
    )

    # Seed a transaction for that payee
    txn = {
        "id": "t1",
        "kind": "expense",
        "description": "Netflix Monthly",
        "payee_id": seeded["p1"],
        "from_account_id": "acct1",
        "from_amount": 15.0,
        "from_currency": "SGD",
        "category": "uncategorized",
        "date": "2026-06-01",
        "deleted": False,
    }
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([txn])
    TransactionStore().insert(txn)

    resp = client.post(f"/api/payees/{seeded['p1']}/apply-category")
    assert resp.status_code == 200
    body = resp.json()
    assert "updated" in body
    assert body["updated"] == 1


def test_apply_category_404_unknown_payee(client):
    resp = client.post("/api/payees/pay_ghost/apply-category")
    assert resp.status_code == 404


def test_apply_category_422_no_default_category(client, seeded):
    """Payee without a default_category set → 422."""
    resp = client.post(f"/api/payees/{seeded['p1']}/apply-category")
    assert resp.status_code == 422


# ── POST /api/payees/{id}/apply-rules ────────────────────────────────────────


def test_apply_payee_rules_to_existing_200(client, seeded, tmp_path):
    """Payee with a rule should re-point matching transactions."""
    # Add a rule
    client.post(
        f"/api/payees/{seeded['p1']}/rules",
        json={"match_type": "contains", "value": "NFLX"},
    )
    # Seed a transaction that matches but isn't yet assigned to p1
    txn = {
        "id": "t2",
        "kind": "expense",
        "description": "NFLX.COM",
        "payee_id": None,
        "from_account_id": "acct1",
        "from_amount": 15.0,
        "from_currency": "SGD",
        "category": "entertainment",
        "date": "2026-06-01",
        "deleted": False,
    }
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([txn])
    TransactionStore().insert(txn)

    resp = client.post(f"/api/payees/{seeded['p1']}/apply-rules")
    assert resp.status_code == 200
    body = resp.json()
    assert "updated" in body
    assert body["updated"] == 1


def test_apply_payee_rules_to_existing_no_rules_zero_updated(client, seeded):
    """Payee with no rules → {updated: 0}."""
    resp = client.post(f"/api/payees/{seeded['p1']}/apply-rules")
    assert resp.status_code == 200
    assert resp.json()["updated"] == 0


def test_apply_payee_rules_to_existing_404_unknown(client):
    resp = client.post("/api/payees/pay_ghost/apply-rules")
    assert resp.status_code == 404


# ── POST /api/payees/apply-rules (global) ─────────────────────────────────────


def test_apply_rules_requires_confirm_true(client):
    resp = client.post("/api/payees/apply-rules", json={"confirm": False})
    assert resp.status_code == 400


def test_apply_rules_missing_confirm_defaults_false_400(client):
    resp = client.post("/api/payees/apply-rules", json={})
    assert resp.status_code == 400


def test_apply_rules_confirm_true_200(client, seeded):
    resp = client.post("/api/payees/apply-rules", json={"confirm": True})
    assert resp.status_code == 200
    body = resp.json()
    assert "updated" in body
    assert isinstance(body["updated"], int)


# ── Behavior: end-to-end create → update → rename → delete ──────────────────


def test_full_payee_lifecycle(client):
    """Create → set category → rename → add alias → delete."""
    # Create
    r = client.post("/api/payees", json={"name": "Amazon"})
    assert r.status_code == 200
    pid = r.json()["id"]

    # Set default category (label resolves to the built-in "Shopping" category id)
    r = client.patch(
        pid and f"/api/payees/{pid}", json={"default_categories": {"expense": "shopping"}}
    )
    assert r.status_code == 200
    assert r.json()["default_categories"] == {"expense": "cat_6c4bc0170000"}

    # Rename
    r = client.patch(f"/api/payees/{pid}", json={"name": "Amazon.com"})
    assert r.status_code == 200
    assert r.json()["name"] == "Amazon.com"

    # Add alias
    r = client.post(f"/api/payees/{pid}/aliases", json={"text": "AMZN MKTP"})
    assert r.status_code == 200

    # Appears in registry
    reg = client.get("/api/payees").json()
    p = next((x for x in reg["payees"] if x["id"] == pid), None)
    assert p is not None
    assert p["default_categories"] == {"expense": "cat_6c4bc0170000"}

    # Delete
    r = client.delete(f"/api/payees/{pid}")
    assert r.status_code == 200

    # Gone from registry
    reg = client.get("/api/payees").json()
    assert all(x["id"] != pid for x in reg["payees"])
