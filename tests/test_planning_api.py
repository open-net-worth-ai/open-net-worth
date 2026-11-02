"""
API smoke tests for the Planning router (PLAN).

Offline + isolated: a minimal FastAPI app mounts ONLY the planning router; the assignments /
groups / transactions stores, FX, and profile are monkeypatched into tmp_path so requests
never touch live data. Status + shape only — the math is covered by test_planning_service.
A missing month / empty book returns zeros, never 500.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.planning import router as planning_router
from backend.db.profile_store import ProfileStore
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database. No accounts
    seeded: these tests assert zero-cash semantics (RTA 0 / overassigned)."""
    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # The envelope service holds its OWN store instances — isolate them too. Full-split (RFC
    # envelope-category-full-split.md §Backend 3) deleted the pseudo-row fallback: a category
    # with no envelope has no budget row, so "food"/"transport"/etc. need REAL 1:1 envelopes
    # to be assignable — mint them via the same first-boot migration a real app runs.
    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already covers it.

    ProfileStore().set_raw({"base_currency": "SGD"})
    envelopes_service.ensure_envelopes_migrated()
    app = FastAPI()
    app.include_router(planning_router)
    return TestClient(app)


@pytest.fixture
def env_client(client):
    """``client`` already isolates the envelope store into tmp_path — kept as a named alias
    for the envelope merge/split/rename/create REST endpoints (the tests below overwrite the
    fixture-minted envelopes entirely via ``_seed_envelopes``)."""
    return client


def test_get_month_empty_book_zeros(client):
    r = client.get("/api/planning/month", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["ready_to_assign"] == 0.0
    assert body["totals"]["assigned"] == 0.0
    assert isinstance(body["groups"], list)


def test_get_month_missing_param_defaults_current(client):
    r = client.get("/api/planning/month")
    assert r.status_code == 200
    assert len(r.json()["month"]) == 7  # YYYY-MM


def test_get_month_carries_the_identitys_terms(client):
    """The wire contract after ADR 2026-09-09: the four terms Ready to Assign is made of are ON
    the payload, the YNAB decomposition that used to explain it is OFF, and so are the two
    2026-09-08-only keys (`set_aside_base`/`unfunded_card_debt_base`) the restored identity has
    no term for."""
    r = client.get("/api/planning/month", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    for key in (
        "on_budget_cash",
        "pre_budget_baseline",
        "held_in_envelopes",
        "card_spend_not_covered",
    ):
        assert key in body, key
    for retired in (
        "inflows_to_assign",
        "rta_movements",
        "rta_carried_in",
        "prev_month_overspend_base",
        "set_aside_base",
        "unfunded_card_debt_base",
    ):
        assert retired not in body, retired
    assert body["ready_to_assign"] == pytest.approx(
        body["on_budget_cash"]
        - body["pre_budget_baseline"]
        - body["held_in_envelopes"]
        - body["card_spend_not_covered"]
        - body["assigned_in_future_months"],
        abs=0.011,
    )
    # Flow-framed terms (2026-09-13): present on every response, `flow` null at the floor —
    # an empty book with no transactions has no data month, so the requested month IS the floor.
    assert "flow" in body
    assert "cash_overspending" in body
    assert body["flow"] is None


def test_get_activity_transactions_shape(client, tmp_path):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    AccountStore().insert(
        {
            "id": "bank",
            "name": "Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        }
    )
    TransactionStore().insert(
        {
            "id": "t1",
            "kind": "expense",
            "date": "2026-06-15",
            "category": "cat_13095ba70000",
            "from_account_id": "bank",
            "from_amount": 42.5,
            "from_currency": "SGD",
            "base_amount": 42.5,
        }
    )
    r = client.get(
        "/api/planning/activity-transactions",
        params=[("category", "cat_13095ba70000"), ("ym", "2026-06")],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["excluded_count"] == 0
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["activity_amount"] == -42.5


def test_get_activity_transactions_missing_ym_defaults_current(client):
    r = client.get("/api/planning/activity-transactions", params={"category": "cat_13095ba70000"})
    assert r.status_code == 200
    assert r.json() == {"transactions": [], "excluded_count": 0}


def _mk_group(client, name, cats=()):
    """Create an (empty) envelope group over the API, then move the 1:1 envelopes of ``cats``
    into it in order. Returns the group id."""
    r = client.post("/api/planning/groups", json={"name": name})
    assert r.status_code == 200
    gid = r.json()["group"]["id"]
    for cat in cats:
        moved = client.post(
            "/api/planning/groups/move-category", json={"category": _env(cat), "group_id": gid}
        )
        assert moved.status_code == 200
    return gid


def _env(key):
    """Resolve a category key to its (fixture-minted, 1:1) envelope id — the ref every money
    write now takes (docs/adr/2026-07-05-envelope-id-native.md)."""
    return envelopes_service.env_of()[key]


def test_assign_roundtrip(client):
    env_id = _env("cat_13095ba70000")
    r = client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": env_id, "amount": 800},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "month": "2026-06", "envelope_id": env_id, "assigned": 800.0}
    # reflected in the table
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    food = next(c for g in table["groups"] for c in g["categories"] if c["key"] == env_id)
    assert food["assigned"] == 800.0


def test_assign_bad_category_422(client):
    r = client.post(
        "/api/planning/assign", json={"month": "2026-06", "envelope_id": "nope", "amount": 1}
    )
    assert r.status_code == 422


def test_get_fresh_start_preview_names_assigned_in_future_when_the_clamp_is_live(client):
    """`GET /api/planning/fresh-start-preview` is an eight-key wire shape normally; a NINTH,
    `assigned_in_future`, appears only when a LATER month's own assignment clamps this month's
    displayed figure (`_display_rta`) — needs positive cash for the clamp to bite (an empty book
    has nothing to deduct FROM)."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    AccountStore().insert(
        {
            "id": "bank",
            "name": "Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        }
    )
    TransactionStore().insert(
        {
            "id": "t-income",
            "kind": "income",
            "date": "2026-06-01",
            "category": "",
            "to_account_id": "bank",
            "to_amount": 1000.0,
            "to_currency": "SGD",
        }
    )
    client.post(
        "/api/planning/assign",
        json={"month": "2026-07", "envelope_id": _env("cat_13095ba70000"), "amount": 100},
    )

    r = client.get("/api/planning/fresh-start-preview", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "month",
        "ready_to_assign_after",
        "cash_now",
        "card_credit",
        "held_in_envelopes",
        "held_outside_envelopes",
        "card_spend_not_covered",
        "baseline",
        "assigned_in_future",
    }
    # Derivation at the hypothetical floor (June, June's assignments zeroed): cash 1000, no
    # envelope touched by the blank-category income so held_in_envelopes 0, nothing spent
    # outside an envelope so held_outside_envelopes 0, no card so card_spend_not_covered 0,
    # baseline 0 → identity 1000; July's own 100 then clamps the displayed figure by
    # min(100, max(0, 1000)) = 100.
    assert body["cash_now"] == 1000.0
    assert body["held_in_envelopes"] == 0.0
    assert body["held_outside_envelopes"] == 0.0
    assert body["card_spend_not_covered"] == 0.0
    assert body["assigned_in_future"] == 100.0
    assert body["ready_to_assign_after"] == 900.0  # 1000 − 0 − 0 − 0 − 100


def test_fresh_start_preview_carries_card_credit_and_closes_on_the_wire(client):
    """A card in credit is budget money (ADR 2026-11-02-a-card-in-credit-is-budget-money), so the
    wire preview must carry `card_credit` — without it the dialog's ledger is short by the credit
    and renders an "Unexplained" line. June: 1,000 in the bank, 150 paid to a card that owes
    nothing → cash 850, credit 150, the payment envelope holding −150 in-month."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    for acct in (
        {"id": "bank", "name": "Bank", "kind": "asset", "type": "cash", "currency": "SGD"},
        {"id": "visa", "name": "Visa", "kind": "liability", "type": "credit", "currency": "SGD"},
    ):
        AccountStore().insert({**acct, "initial_balance": 0.0})
    planning.sync_card_payment_envelopes()
    TransactionStore().insert(
        {
            "id": "t-income",
            "kind": "income",
            "date": "2026-06-01",
            "category": "",
            "to_account_id": "bank",
            "to_amount": 1000.0,
            "to_currency": "SGD",
        }
    )
    TransactionStore().insert(
        {
            "id": "t-pay",
            "kind": "transfer",
            "date": "2026-06-20",
            "from_account_id": "bank",
            "from_amount": 150.0,
            "from_currency": "SGD",
            "to_account_id": "visa",
            "to_amount": 150.0,
            "to_currency": "SGD",
        }
    )

    body = client.get("/api/planning/fresh-start-preview", params={"ym": "2026-06"}).json()
    assert body["cash_now"] == 850.0
    assert body["card_credit"] == 150.0
    closes = (
        body["cash_now"]
        + body["card_credit"]
        - body["baseline"]
        - body["held_in_envelopes"]
        - body["held_outside_envelopes"]
        - body["card_spend_not_covered"]
        - body.get("assigned_in_future", 0.0)
    )
    assert round(body["ready_to_assign_after"] - closes, 2) == 0


def test_fresh_start_preview_refuses_a_future_month_exactly_like_the_write(client):
    """LOW, security review 2026-09-08: the preview ran ``_valid_month`` but skipped
    ``budgets._validate_anchor_month``, which the WRITE applies — so `9999-12` rendered a full
    dialog (``ready_to_assign_after``, a figure and all) that `POST /fresh-start` then refused
    with "rollover_start cannot be in the future". A dialog must never promise a number its own
    confirm will reject; both endpoints now 422 on the same input, and a malformed month (which
    ``_valid_month`` already caught on both paths) still does too."""
    for ym in ("9999-12", "2026-13"):
        preview = client.get("/api/planning/fresh-start-preview", params={"ym": ym})
        write = client.post("/api/planning/fresh-start", json={"ym": ym})
        assert (preview.status_code, write.status_code) == (422, 422), ym


def test_move_money(client):
    client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": _env("cat_13095ba70000"), "amount": 800},
    )
    r = client.post(
        "/api/planning/move",
        json={
            "month": "2026-06",
            "from_envelope_id": _env("cat_13095ba70000"),
            "to_envelope_id": _env("cat_db5642ab0000"),
            "amount": 200,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["from_assigned"] == 600.0 and body["to_assigned"] == 200.0


def test_set_and_clear_target(client):
    env_id = _env("cat_13095ba70000")
    r = client.put(f"/api/planning/targets/{env_id}", json={"type": "monthly", "amount": 800})
    assert r.status_code == 200
    assert r.json()["target"] == {"type": "monthly", "amount": 800.0}
    # clear
    r2 = client.put(f"/api/planning/targets/{env_id}", json={"type": "", "amount": 0})
    assert r2.status_code == 200
    assert r2.json()["target"] is None


def test_set_weekly_target(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}", json={"type": "weekly", "amount": 100}
    )
    assert r.status_code == 200
    assert r.json()["target"] == {"type": "weekly", "amount": 100.0}


def test_set_yearly_target(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}", json={"type": "yearly", "amount": 1200}
    )
    assert r.status_code == 200
    assert r.json()["target"] == {"type": "yearly", "amount": 1200.0}


def test_set_target_unknown_type_422(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}",
        json={"type": "biweekly", "amount": 100},
    )
    assert r.status_code == 422


def test_snooze_and_unsnooze_target(client):
    env_id = _env("cat_13095ba70000")
    client.put(f"/api/planning/targets/{env_id}", json={"type": "monthly", "amount": 800})
    # Snooze the existing target (no type sent).
    r = client.put(f"/api/planning/targets/{env_id}", json={"snooze_until": "2026-06"})
    assert r.status_code == 200
    assert r.json()["target"]["snooze_until"] == "2026-06"
    assert r.json()["target"]["type"] == "monthly"
    # Un-snooze (blank snooze, no type).
    r2 = client.put(f"/api/planning/targets/{env_id}", json={"snooze_until": ""})
    assert r2.status_code == 200
    assert "snooze_until" not in r2.json()["target"]


def test_group_crud(client):
    # create
    r = client.post("/api/planning/groups", json={"name": "Essentials"})
    assert r.status_code == 200
    gid = r.json()["group"]["id"]
    assert "categories" not in r.json()["group"]
    # list
    listed = client.get("/api/planning/groups").json()
    assert "Essentials" in [g["name"] for g in listed]
    assert all("income" not in g and "categories" not in g for g in listed)
    # rename
    r2 = client.patch(f"/api/planning/groups/{gid}", json={"name": "Core"})
    assert r2.json()["group"]["name"] == "Core"
    # move envelopes in
    for cat in ("cat_13095ba70000", "cat_db5642ab0000"):
        r3 = client.post(
            "/api/planning/groups/move-category",
            json={"category": _env(cat), "group_id": gid},
        )
        assert r3.status_code == 200
    # the group now contains the moved category in the table
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    core = next(g for g in table["groups"] if g["id"] == gid)
    assert {c["key"] for c in core["categories"]} >= {
        _env("cat_13095ba70000"),
        _env("cat_db5642ab0000"),
    }
    # delete — envelopes not lost (they move to Others)
    r4 = client.delete(f"/api/planning/groups/{gid}")
    assert r4.status_code == 200
    table2 = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    keys = {c["key"] for g in table2["groups"] for c in g["categories"]}
    assert _env("cat_13095ba70000") in keys and _env("cat_db5642ab0000") in keys


def test_group_rename_missing_404(client):
    r = client.patch("/api/planning/groups/grp-nope", json={"name": "X"})
    assert r.status_code == 404


def test_group_reorder(client):
    # "Others" already exists (the fixture's first-boot migration mints it for the builtin
    # expense categories) — filter it out, this test only cares about A/B's relative order.
    a = client.post("/api/planning/groups", json={"name": "A"}).json()["group"]["id"]
    b = client.post("/api/planning/groups", json={"name": "B"}).json()["group"]["id"]
    r = client.post("/api/planning/groups/reorder", json={"order": [b, a]})
    assert r.status_code == 200
    names = [g["name"] for g in r.json()["groups"] if g["name"] in ("A", "B")]
    assert names == ["B", "A"]


def test_reorder_categories_within_group(client):
    gid = _mk_group(
        client, "Essentials", ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]
    )
    ordered = [_env("cat_6ea706940000"), _env("cat_13095ba70000"), _env("cat_db5642ab0000")]
    r = client.post(
        f"/api/planning/groups/{gid}/reorder-categories",
        json={"categories": ordered},
    )
    assert r.status_code == 200
    assert r.json()["categories"] == ordered
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    grp = next(g for g in table["groups"] if g["id"] == gid)
    assert [c["key"] for c in grp["categories"]] == ordered


def test_reorder_categories_non_permutation_422(client):
    gid = _mk_group(client, "Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    r = client.post(
        f"/api/planning/groups/{gid}/reorder-categories",
        json={"categories": [_env("cat_13095ba70000")]},
    )
    assert r.status_code == 422


def test_move_category_with_index(client):
    gid = _mk_group(client, "Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    # Seed Others so 'health' has a home, then move it into Essentials at index 1.
    client.get("/api/planning/month", params={"ym": "2026-06"})
    r = client.post(
        "/api/planning/groups/move-category",
        json={"category": _env("cat_6ea706940000"), "group_id": gid, "index": 1},
    )
    assert r.status_code == 200
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    grp = next(g for g in table["groups"] if g["id"] == gid)
    assert [c["key"] for c in grp["categories"]] == [
        _env("cat_13095ba70000"),
        _env("cat_6ea706940000"),
        _env("cat_db5642ab0000"),
    ]


def test_get_health_empty_book_zeros(client):
    r = client.get("/api/planning/health", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["rta"] == 0.0
    assert body["rta_status"] == "all_assigned"
    assert body["underfunded_count"] == 0
    assert body["overspent_count"] == 0
    # Empty book → Age of Money is honest, never a fabricated number.
    assert body["age_of_money"]["forecastable"] is False
    assert body["age_of_money"]["age_days"] is None


def test_get_health_missing_param_defaults_current(client):
    r = client.get("/api/planning/health")
    assert r.status_code == 200
    assert len(r.json()["month"]) == 7  # YYYY-MM


def test_get_health_reflects_assignment(client):
    client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": _env("cat_13095ba70000"), "amount": 800},
    )
    body = client.get("/api/planning/health", params={"ym": "2026-06"}).json()
    assert body["total_assigned"] == 800.0
    assert body["rta_status"] == "overassigned"  # nothing to assign from, so RTA negative


def test_category_note_round_trip(client):
    env_id = _env("cat_13095ba70000")
    # Set a note.
    r = client.put(
        f"/api/planning/categories/{env_id}/note",
        json={"note": "This is the food budget."},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "envelope_id": env_id}

    # GET /api/planning/notes reflects it.
    r2 = client.get("/api/planning/notes")
    assert r2.status_code == 200
    assert r2.json()["notes"][env_id] == "This is the food budget."

    # Clear by sending empty note.
    r3 = client.put(f"/api/planning/categories/{env_id}/note", json={"note": ""})
    assert r3.status_code == 200

    # Verify cleared.
    r4 = client.get("/api/planning/notes")
    assert r4.status_code == 200
    assert env_id not in r4.json()["notes"]


# ── GET /api/planning/income ──────────────────────────────────────────────────


def test_get_income_empty_book_200(client):
    """Empty book → 200 with correct shape and zero totals."""
    r = client.get("/api/planning/income", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["categories"] == [] or isinstance(body["categories"], list)
    assert "groups" not in body
    assert body["total_received"] == 0.0
    assert body["total_ytd"] == 0.0
    assert "taxable_received" not in body
    assert "taxable_ytd" not in body


def test_get_income_no_ym_defaults_current_month(client):
    """Omitting ym defaults to the current YYYY-MM."""
    r = client.get("/api/planning/income")
    assert r.status_code == 200
    assert len(r.json()["month"]) == 7  # YYYY-MM


def test_get_income_with_income_category_and_txn(client, tmp_path):
    """A custom income-kind category with a matching txn surfaces in the response.
    The client fixture has already isolated the DB-backed stores, so we seed there."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert(
        {
            "id": "salary",
            "label": "Salary",
            "kind": "income",
            "color": "#34b08a",
        }
    )
    ProfileStore().set_raw({"base_currency": "SGD"})

    from backend.db.transaction_store import TransactionStore

    TransactionStore().insert(
        {
            "id": "t_salary",
            "kind": "income",
            "date": "2026-06-01",
            "category": "salary",
            "to_amount": 6800.0,
            "to_currency": "SGD",
            "base_amount": 6800.0,
        }
    )

    r = client.get("/api/planning/income", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    salary = next((c for c in body["categories"] if c["key"] == "salary"), None)
    assert salary is not None
    assert salary["received"] == pytest.approx(6800.0)
    assert salary["ytd"] == pytest.approx(6800.0)
    assert "taxable" not in salary
    assert body["total_received"] == pytest.approx(6800.0)


def test_move_category_refuses_an_income_key(client):
    """Income categories have no envelope and belong to no group — moving one is refused."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "salary", "label": "Salary", "kind": "income"})
    gid = _mk_group(client, "Job Income")
    r = client.post(
        "/api/planning/groups/move-category",
        json={"category": "salary", "group_id": gid},
    )
    assert r.status_code == 422
    assert "income categories have no envelope" in r.text


def test_a_sentinel_looking_group_name_is_refused_over_the_api(client):
    before = {g["id"] for g in client.get("/api/planning/groups").json()}
    for name in ("__income__", "__unsorted__"):
        assert client.post("/api/planning/groups", json={"name": name}).status_code == 422
    assert {g["id"] for g in client.get("/api/planning/groups").json()} == before


def test_move_category_unknown_key_422(client):
    """move-category with a nonexistent key raises an error."""
    r = client.post("/api/planning/groups", json={"name": "G"})
    gid = r.json()["group"]["id"]
    r2 = client.post(
        "/api/planning/groups/move-category",
        json={"category": "ghost_income_cat", "group_id": gid},
    )
    assert r2.status_code in (404, 422)


def test_month_flags_commitments_group_synced(client):
    """The auto-managed 'Commitments' group is flagged synced=True in the month view so the
    client can pin+lock it without guessing a magic id (regression: the port keyed off a
    prototype-only id 'commit' that never exists in real data)."""
    # Built the way the app builds it (sync_commitment_envelopes writes the store directly): a
    # user can't create a group with a synced name, nor move a row into one.
    commitments = planning.groups_store.create({"name": "Commitments"})
    envelopes_service.envelope_store.update_envelope(
        _env("cat_13095ba70000"), {"group_id": commitments["id"]}
    )
    _mk_group(client, "Fun", ["cat_dfcf9f910000"])
    client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": _env("cat_13095ba70000"), "amount": 100},
    )
    client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": _env("cat_dfcf9f910000"), "amount": 50},
    )

    groups = {
        g["name"]: g
        for g in client.get("/api/planning/month", params={"ym": "2026-06"}).json()["groups"]
    }
    assert groups["Commitments"]["synced"] is True
    assert groups["Fun"]["synced"] is False


def test_creating_a_group_with_a_synced_name_is_refused_before_anything_is_written(client):
    before = {g["id"] for g in client.get("/api/planning/groups").json()}
    r = client.post("/api/planning/groups", json={"name": "Commitments"})
    assert r.status_code == 422
    assert {g["id"] for g in client.get("/api/planning/groups").json()} == before


# ── Envelope lifecycle REST (P4 — merge/split/rename) ─────────────────────────────


def _seed_envelopes(env_client, envelopes: list[dict], assignments: dict | None = None):
    envelopes_service.envelope_store.replace_all(envelopes)
    # BudgetAssignmentsStore (plan 4a task 4) is DB-backed with no bulk replace_all — set
    # each (month, category) directly; the fixture's fresh per-test database starts empty,
    # so there is nothing pre-existing to clear first (unlike a true whole-file overwrite).
    for month, cats in (assignments or {}).items():
        for category, amount in cats.items():
            envelopes_service.assignments_store.set_assigned(month, category, amount)


_ENV_A = {
    "id": "env_a0001",
    "name": "Food",
    "category_ids": ["food"],
    "group_id": "others",
    "order": 0,
    "target": None,
    "note": "",
    "archived": False,
}
_ENV_B = {
    "id": "env_b0001",
    "name": "Transport",
    "category_ids": ["transport"],
    "group_id": "others",
    "order": 1,
    "target": None,
    "note": "",
    "archived": False,
}


def test_list_envelopes_excludes_archived_by_default(env_client):
    archived = dict(_ENV_B, archived=True)
    _seed_envelopes(env_client, [_ENV_A, archived])
    r = env_client.get("/api/planning/envelopes")
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()]
    assert ids == ["env_a0001"]
    r = env_client.get("/api/planning/envelopes", params={"include_archived": "true"})
    assert {e["id"] for e in r.json()} == {"env_a0001", "env_b0001"}


def test_merge_envelopes_conserves_money(env_client):
    _seed_envelopes(
        env_client, [_ENV_A, _ENV_B], {"2026-06": {"env_a0001": 300.0, "env_b0001": 100.0}}
    )
    r = env_client.post(
        "/api/planning/envelopes/merge",
        json={"source_envelope_id": "env_b0001", "target_envelope_id": "env_a0001"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["target_envelope_id"] == "env_a0001"
    assert body["categories_moved"] == 1
    raw = envelopes_service.assignments_store.month_map("2026-06")
    assert raw["env_a0001"] == 400.0  # 300 + 100, conserved
    assert not raw.get("env_b0001")


def test_merge_envelopes_unknown_target_404(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.post(
        "/api/planning/envelopes/merge",
        json={"source_envelope_id": "env_a0001", "target_envelope_id": "env_nope"},
    )
    assert r.status_code == 404


def test_merge_envelopes_self_merge_422(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.post(
        "/api/planning/envelopes/merge",
        json={"source_envelope_id": "env_a0001", "target_envelope_id": "env_a0001"},
    )
    assert r.status_code == 422


def test_split_envelope_unlinks_no_new_envelope_minted(env_client):
    """Handoff delta #1 — unlink semantics flip: /split no longer mints a new envelope; the
    category becomes envelope-less and the source keeps its FULL pool untouched."""
    merged = dict(_ENV_A, category_ids=["food", "entertainment"])
    _seed_envelopes(env_client, [merged], {"2026-06": {"env_a0001": 400.0}})
    r = env_client.post(
        "/api/planning/envelopes/env_a0001/split", json={"category_id": "entertainment"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "category_id": "entertainment", "envelope_id": "env_a0001"}
    raw = envelopes_service.assignments_store.month_map("2026-06")
    assert raw.get("env_a0001") == 400.0  # source's pool untouched
    envs = env_client.get("/api/planning/envelopes").json()
    assert len(envs) == 1  # NO new envelope
    assert envs[0]["category_ids"] == ["food"]


def test_split_envelope_sole_member_now_legal(env_client):
    """Handoff delta #1: unlinking a 1:1 row's only category is now legal — the envelope
    survives zero-category, keeping its pool."""
    _seed_envelopes(env_client, [_ENV_A], {"2026-06": {"env_a0001": 300.0}})
    r = env_client.post("/api/planning/envelopes/env_a0001/split", json={"category_id": "food"})
    assert r.status_code == 200
    envs = env_client.get("/api/planning/envelopes").json()
    assert len(envs) == 1
    assert envs[0]["category_ids"] == []
    assert envs[0]["archived"] is False


def test_split_envelope_not_a_member_422(env_client):
    _seed_envelopes(env_client, [_ENV_A, _ENV_B])
    r = env_client.post(
        "/api/planning/envelopes/env_a0001/split", json={"category_id": "transport"}
    )
    assert r.status_code == 422


def test_rename_envelope(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.patch("/api/planning/envelopes/env_a0001", json={"name": "Groceries"})
    assert r.status_code == 200
    assert r.json()["envelope"]["name"] == "Groceries"
    envs = {e["id"]: e for e in env_client.get("/api/planning/envelopes").json()}
    assert envs["env_a0001"]["name"] == "Groceries"


def test_rename_envelope_blank_422(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.patch("/api/planning/envelopes/env_a0001", json={"name": "  "})
    assert r.status_code == 422


# ── Envelope create + add-category REST (RFC envelope-category-full-split.md) ─────


def test_create_envelope_returns_201_with_no_categories(env_client):
    _seed_envelopes(env_client, [])
    r = env_client.post("/api/planning/envelopes", json={"name": "Emergency fund"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Emergency fund"
    assert body["category_ids"] == []
    assert body["id"].startswith("env_")
    # No category was ever minted — GET /api/planning/envelopes shows the new envelope,
    # linked to nothing.
    envs = env_client.get("/api/planning/envelopes").json()
    assert any(e["id"] == body["id"] and e["category_ids"] == [] for e in envs)


def test_create_envelope_blank_name_422(env_client):
    _seed_envelopes(env_client, [])
    r = env_client.post("/api/planning/envelopes", json={"name": "  "})
    assert r.status_code == 422


def test_create_envelope_placed_in_named_group(env_client):
    _seed_envelopes(env_client, [])
    gid = env_client.post("/api/planning/groups", json={"name": "Sinking funds"}).json()["group"][
        "id"
    ]
    r = env_client.post("/api/planning/envelopes", json={"name": "Car repairs", "group_id": gid})
    assert r.status_code == 201
    assert r.json()["group_id"] == gid


def test_zero_category_envelope_is_assignable_via_rest(env_client):
    """Row identity = envelope id: assign/set_target accept the envelope's own id as the
    "category" key for a zero-category row (contract addendum)."""
    _seed_envelopes(env_client, [])
    envelope = env_client.post("/api/planning/envelopes", json={"name": "Emergency fund"}).json()

    r = env_client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": envelope["id"], "amount": 250.0},
    )
    assert r.status_code == 200
    assert r.json()["assigned"] == 250.0

    r2 = env_client.put(
        f"/api/planning/targets/{envelope['id']}", json={"type": "monthly", "amount": 250.0}
    )
    assert r2.status_code == 200

    table = env_client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == envelope["id"])
    assert row["assigned"] == 250.0
    assert row["target"]["amount"] == 250.0


def test_add_category_to_envelope(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    other = env_client.post("/api/planning/envelopes", json={"name": "Coffee"}).json()
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "coffee_shops", "label": "Coffee Shops", "kind": "expense"})

    r = env_client.post(
        f"/api/planning/envelopes/{other['id']}/add-category", json={"category_id": "coffee_shops"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    envs = {e["id"]: e for e in env_client.get("/api/planning/envelopes").json()}
    assert envs[other["id"]]["category_ids"] == ["coffee_shops"]


def test_add_category_already_linked_409(env_client):
    """Attempting to link a category that already belongs to another envelope is refused —
    use merge/split instead."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "food", "label": "Food", "kind": "expense"})
    _seed_envelopes(env_client, [_ENV_A])
    other = env_client.post("/api/planning/envelopes", json={"name": "Coffee"}).json()
    r = env_client.post(
        f"/api/planning/envelopes/{other['id']}/add-category", json={"category_id": "food"}
    )
    assert r.status_code == 409


def test_add_category_unknown_category_404(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.post(
        "/api/planning/envelopes/env_a0001/add-category", json={"category_id": "ghost_cat"}
    )
    assert r.status_code == 404


# ── Envelope color (handoff delta #5) ──────────────────────────────────────────────


def test_create_envelope_with_color(env_client):
    _seed_envelopes(env_client, [])
    r = env_client.post(
        "/api/planning/envelopes", json={"name": "Emergency fund", "color": "#3B86DD"}
    )
    assert r.status_code == 201
    assert r.json()["color"] == "#3b86dd"


def test_patch_envelope_color_only(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.patch("/api/planning/envelopes/env_a0001", json={"color": "#00ff00"})
    assert r.status_code == 200
    assert r.json()["envelope"]["color"] == "#00ff00"
    assert r.json()["envelope"]["name"] == "Food"  # name untouched


def test_patch_envelope_invalid_color_422(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.patch("/api/planning/envelopes/env_a0001", json={"color": "red"})
    assert r.status_code == 422


def test_patch_envelope_neither_field_422(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.patch("/api/planning/envelopes/env_a0001", json={})
    assert r.status_code == 422


def test_budget_month_row_emits_color(client):
    _mk_group(client, "Essentials", ["cat_13095ba70000"])
    envs = client.get("/api/planning/envelopes").json()
    env_id = next(e["id"] for e in envs if "cat_13095ba70000" in e.get("category_ids", []))
    client.patch(f"/api/planning/envelopes/{env_id}", json={"color": "#e0a032"})
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == env_id)
    assert row["color"] == "#e0a032"


# ── Envelope delete / undo / archived list (ADR 2026-09-26-deleting-an-envelope-deletes-it,
#    superseding the handoff delta #2/#3 archive-and-restore shape) ───────────────────────


def test_delete_envelope_detaches_zeroes_and_hard_deletes(env_client):
    _seed_envelopes(env_client, [_ENV_A], {"2026-06": {"env_a0001": 300.0}})
    r = env_client.delete("/api/planning/envelopes/env_a0001", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["undo_token"]
    assert body["former_category_ids"] == ["food"]
    assert body["zeroed_assignments"] == {"2026-06": 300.0}
    # The realized Ready-to-Assign figure for the requested month, echoed straight from
    # `_op_envelope_delete`'s own pre-write read (no cash seeded, so `available` alone — the
    # whole 300 assigned, no activity to offset it — is both the OLD and the TRUE figure here).
    assert body["month"] == "2026-06"
    assert body["available"] == 300.0
    assert body["delta_rta"] == 300.0
    envs = {
        e["id"]: e
        for e in env_client.get(
            "/api/planning/envelopes", params={"include_archived": "true"}
        ).json()
    }
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — gone entirely,
    # not even visible via include_archived.
    assert "env_a0001" not in envs


def test_delete_envelope_without_ym_still_carries_a_figure(env_client):
    """`ym` is optional on the route (defaults to this month, mirroring fresh-start-preview) —
    an older caller that never sends it still gets a well-shaped response, just for today."""
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.delete("/api/planning/envelopes/env_a0001")
    assert r.status_code == 200
    body = r.json()
    assert "month" in body and "available" in body and "delta_rta" in body


def test_delete_envelope_unknown_404(env_client):
    _seed_envelopes(env_client, [])
    r = env_client.delete("/api/planning/envelopes/env_nope")
    assert r.status_code == 404


def test_delete_envelope_already_archived_422(env_client):
    archived = dict(_ENV_A, archived=True)
    _seed_envelopes(env_client, [archived])
    r = env_client.delete("/api/planning/envelopes/env_a0001")
    assert r.status_code == 422


# ── GET .../delete-preview (the confirm dialog's own number, writes nothing) ──────────────


def test_delete_preview_shape(env_client):
    _seed_envelopes(env_client, [_ENV_A], {"2026-06": {"env_a0001": 300.0}})
    r = env_client.get("/api/planning/envelopes/env_a0001/delete-preview", params={"ym": "2026-06"})
    assert r.status_code == 200
    assert r.json() == {
        "month": "2026-06",
        "envelope_id": "env_a0001",
        "available": 300.0,
        "delta_rta": 300.0,
    }
    # Read-only — the envelope and its assignment are both still there afterward.
    assert env_client.get("/api/planning/envelopes").json()
    still = env_client.get(
        "/api/planning/envelopes/env_a0001/delete-preview", params={"ym": "2026-06"}
    )
    assert still.status_code == 200


def test_delete_preview_defaults_ym_to_this_month(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.get("/api/planning/envelopes/env_a0001/delete-preview")
    assert r.status_code == 200
    assert r.json()["envelope_id"] == "env_a0001"


def test_delete_preview_unknown_404(env_client):
    _seed_envelopes(env_client, [])
    r = env_client.get("/api/planning/envelopes/env_nope/delete-preview", params={"ym": "2026-06"})
    assert r.status_code == 404


def test_delete_preview_already_archived_422(env_client):
    """Never offers a figure for a row `delete_envelope` itself would refuse — the SAME guard
    (`envelopes_service.assert_deletable`), so the confirm dialog and the write always agree."""
    archived = dict(_ENV_A, archived=True)
    _seed_envelopes(env_client, [archived])
    r = env_client.get("/api/planning/envelopes/env_a0001/delete-preview", params={"ym": "2026-06"})
    assert r.status_code == 422


def test_delete_preview_bad_month_422(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.get(
        "/api/planning/envelopes/env_a0001/delete-preview", params={"ym": "not-a-month"}
    )
    assert r.status_code == 422


def test_undo_delete_envelope_round_trip(env_client):
    """POST .../undo-delete replaces the retired POST .../restore (ADR
    2026-09-26-deleting-an-envelope-deletes-it) — it replays the server-held before-image
    named by the delete's own returned token."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "food", "label": "Food & Dining", "kind": "expense"})
    _seed_envelopes(env_client, [_ENV_A])
    delete_body = env_client.delete("/api/planning/envelopes/env_a0001").json()

    r = env_client.post(
        "/api/planning/envelopes/undo-delete", json={"token": delete_body["undo_token"]}
    )
    assert r.status_code == 204
    envs = {e["id"]: e for e in env_client.get("/api/planning/envelopes").json()}
    assert envs["env_a0001"]["category_ids"] == ["food"]
    assert envs["env_a0001"]["archived"] is False


def test_undo_delete_envelope_stale_token_409(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    env_client.delete("/api/planning/envelopes/env_a0001")
    r = env_client.post("/api/planning/envelopes/undo-delete", json={"token": "edu_nope"})
    assert r.status_code == 409


def test_restore_envelope_route_is_retired(env_client):
    """ADR 2026-09-26-deleting-an-envelope-deletes-it: POST .../restore no longer exists —
    FastAPI 404s the unmatched route, same as any other retired path."""
    _seed_envelopes(env_client, [_ENV_A])
    env_client.delete("/api/planning/envelopes/env_a0001")
    r = env_client.post("/api/planning/envelopes/env_a0001/restore")
    assert r.status_code == 404


def test_list_archived_envelopes(env_client):
    """A permanently-historical read (ADR 2026-09-26-deleting-an-envelope-deletes-it) —
    delete hard-deletes for real now, so this seeds a grandfathered pre-hard-delete archived
    row directly, exactly as a live book's leftover archived rows are shaped."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "food", "label": "Food & Dining", "kind": "expense"})
    _seed_envelopes(
        env_client,
        [
            dict(
                _ENV_A,
                archived=True,
                archive_reason="deleted",
                former_category_ids=["food"],
                category_ids=[],
                archived_at="2026-01-01T00:00:00",
            )
        ],
    )
    r = env_client.get("/api/planning/envelopes/archived")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["id"] == "env_a0001"
    assert row["archive_reason"] == "deleted"
    assert row["former_category_ids"] == ["food"]
    assert row["former_members"] == [{"key": "food", "label": "Food & Dining"}]
    assert row["merged_into"] == ""
    assert row["merged_into_name"] == ""


def test_list_archived_envelopes_empty(env_client):
    _seed_envelopes(env_client, [_ENV_A])
    r = env_client.get("/api/planning/envelopes/archived")
    assert r.status_code == 200
    assert r.json() == []


# ── RTA as a move endpoint (handoff delta #9) ──────────────────────────────────────


def test_move_from_rta_endpoint(client):
    r = client.post(
        "/api/planning/move",
        json={
            "month": "2026-06",
            "from_envelope_id": "__rta",
            "to_envelope_id": _env("cat_13095ba70000"),
            "amount": 200,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["to_assigned"] == 200.0
    assert body["from_envelope_id"] == "__rta"


def test_move_to_rta_endpoint(client):
    client.post(
        "/api/planning/assign",
        json={"month": "2026-06", "envelope_id": _env("cat_13095ba70000"), "amount": 200},
    )
    r = client.post(
        "/api/planning/move",
        json={
            "month": "2026-06",
            "from_envelope_id": _env("cat_13095ba70000"),
            "to_envelope_id": "__rta",
            "amount": 50,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["from_assigned"] == 150.0
    assert body["to_envelope_id"] == "__rta"


def test_move_both_rta_422(client):
    r = client.post(
        "/api/planning/move",
        json={
            "month": "2026-06",
            "from_envelope_id": "__rta",
            "to_envelope_id": "__rta",
            "amount": 10,
        },
    )
    assert r.status_code == 422


# ── Target deadline fields (handoff delta #13) ─────────────────────────────────────


def test_put_target_with_due_weekday(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}",
        json={"type": "weekly", "amount": 100, "due_weekday": 5},
    )
    assert r.status_code == 200
    assert r.json()["target"]["due_weekday"] == 5


def test_put_target_with_due_day(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}",
        json={"type": "monthly", "amount": 800, "due_day": -1},
    )
    assert r.status_code == 200
    assert r.json()["target"]["due_day"] == -1


def test_put_target_due_weekday_on_monthly_422(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_13095ba70000')}",
        json={"type": "monthly", "amount": 800, "due_weekday": 2},
    )
    assert r.status_code == 422


def test_put_target_yearly_with_date(client):
    r = client.put(
        f"/api/planning/targets/{_env('cat_c19c4c0f0000')}",
        json={"type": "yearly", "amount": 1200, "date": "2026-03-15"},
    )
    assert r.status_code == 200
    assert r.json()["target"]["date"] == "2026-03-15"
