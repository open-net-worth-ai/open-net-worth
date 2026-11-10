"""REST tests for the 50/30/20 screen's own contract (2026-09-26 GROUP-based redesign):
``GET /api/planning/proportional/month`` and the new ``proportional`` verbs reached via
``POST /api/planning/verb``. Mirrors ``tests/test_planning_engines_api.py``'s isolation
strategy — a minimal FastAPI app mounting only the planning router, DB-backed stores
redirected via ``db_isolated``, and a fixed FX cache/base currency (``services/
budget_engines/service.py`` holds its OWN local ``load_fx_cache``/``base_currency``
bindings, so both must be monkeypatched for a folded figure to be deterministic).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.planning import router as planning_router
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.db.transaction_store import TransactionStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service
from backend.services.budget_engines import service as budget_engines_service

_FOOD = "cat_13095ba70000"  # Food & Dining (built-in, stable id)


def _env(category_id: str) -> str:
    return envelopes_service.env_of()[category_id]


def _group(name: str) -> str:
    """A real Zero-Based group — 50/30/20's groups are the book's own now, so any test
    exercising ``set_group_targets``/``set_group_excluded`` needs a real one to reference."""
    result = planning_service.apply_budget_change({"type": "group_create", "name": name})
    return (result.get("group") or result)["id"]


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore

    fx = {"SGD": 1.0}
    monkeypatch.setattr(budget_engines_service, "load_fx_cache", lambda: fx)
    monkeypatch.setattr(budget_engines_service, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    envelopes_service.ensure_envelopes_migrated()
    AccountStore().create({"id": "a", "name": "Checking", "type": "cash", "currency": "SGD"})
    app = FastAPI()
    app.include_router(planning_router)
    return TestClient(app)


_txn_counter = [0]


def _txn(**overrides) -> dict:
    _txn_counter[0] += 1
    base = {
        "id": f"t{_txn_counter[0]}",
        "date": "2026-06-05",
        "from_currency": "SGD",
        "to_currency": "SGD",
    }
    base.update(overrides)
    return TransactionStore().insert(base)


# ── GET /api/planning/proportional/month ─────────────────────────────────────────────


def test_get_month_shape(client):
    r = client.get("/api/planning/proportional/month", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "month",
        "status",
        "day",
        "days_in_month",
        "configured",
        "groups",
        "savings",
        "income",
        "income_categories",
        "unassigned",
        "envelopes",
    }
    assert body["month"] == "2026-06"
    assert body["configured"] is False  # nothing saved yet
    assert isinstance(body["envelopes"], list) and body["envelopes"]
    row = body["envelopes"][0]
    assert set(row) == {
        "id",
        "label",
        "color",
        "group_id",
        "group",
        "category_ids",
        "cap",
        "activity",
        "excluded",
        "history",
    }
    assert set(row["history"]) == {
        "last",
        "avg3",
        "last_by_day",
        "avg3_by_day",
        "median3",
        "rest_median3",
    }
    assert set(body["savings"]) == {"amount", "target"}
    assert isinstance(body["groups"], list) and body["groups"]
    group = body["groups"][0]
    assert set(group) == {"id", "name", "target", "excluded", "synced", "activity"}


def test_get_month_defaults_to_current_month(client):
    r = client.get("/api/planning/proportional/month")
    assert r.status_code == 200
    assert len(r.json()["month"]) == 7


def test_get_month_configured_true_once_configure_has_run(client):
    client.post(
        "/api/planning/verb",
        json={"engine_id": "proportional", "verb_id": "configure", "params": {}},
    )
    r = client.get("/api/planning/proportional/month")
    assert r.json()["configured"] is True


def test_get_month_activity_reflects_transactions(client):
    """An envelope's Activity is unconditional now — no verb has to first place it in a
    bucket for its spend to show; the group comes straight from Zero-Based's own
    ``group_id``."""
    food_env = _env(_FOOD)
    _txn(
        kind="income",
        date="2026-06-01",
        category="",
        to_amount=5000,
        from_amount=None,
        from_account_id=None,
        to_account_id="a",
    )
    _txn(
        kind="expense",
        date="2026-06-05",
        category=_FOOD,
        from_amount=300,
        to_amount=None,
        from_account_id="a",
        to_account_id=None,
    )
    body = client.get("/api/planning/proportional/month", params={"ym": "2026-06"}).json()
    food_row = next(e for e in body["envelopes"] if e["id"] == food_env)
    assert food_row["activity"] == 300.0
    assert food_row["group_id"]  # a real Zero-Based group, whatever it resolved to


def test_get_month_cap_applies_from_its_month_onward(client):
    # A cap can only ever be set from the current calendar month onward — pick months
    # around that instead of a fixed past date so this test stays valid over time.
    food_env = _env(_FOOD)
    from backend.services.budget_engines.draft_signals import current_period

    effective_month = current_period()
    before_month = f"{int(effective_month[:4]) - 1}-12"
    after_month = f"{int(effective_month[:4]) + 1}-{effective_month[5:7]}"
    client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_cap",
            "params": {"envelope_id": food_env, "month": effective_month, "amount": 500},
        },
    )
    before = client.get("/api/planning/proportional/month", params={"ym": before_month}).json()
    at = client.get("/api/planning/proportional/month", params={"ym": effective_month}).json()
    after = client.get("/api/planning/proportional/month", params={"ym": after_month}).json()
    assert next(e for e in before["envelopes"] if e["id"] == food_env)["cap"] is None
    assert next(e for e in at["envelopes"] if e["id"] == food_env)["cap"] == 500.0
    assert next(e for e in after["envelopes"] if e["id"] == food_env)["cap"] == 500.0


def test_get_month_envelopes_ordered_group_then_envelope_order(client):
    """Matches the Zero-Based table's own order — group order, then envelope order
    within the group (proportional_state.row_envelopes)."""
    body = client.get("/api/planning/proportional/month", params={"ym": "2026-06"}).json()
    groups_seen = [e["group"] for e in body["envelopes"]]
    # Every distinct group forms a contiguous run — never interleaved with another group.
    seen_so_far: list[str] = []
    for g in groups_seen:
        if seen_so_far and seen_so_far[-1] != g and g in seen_so_far:
            pytest.fail(f"group {g!r} is not contiguous in envelope order: {groups_seen}")
        if not seen_so_far or seen_so_far[-1] != g:
            seen_so_far.append(g)


def test_get_month_unassigned_and_income_categories(client):
    income_cat = CategoryStore().insert(
        {"id": "cat_salary_x", "label": "Salary X", "kind": "income", "color": ""}
    )
    _txn(
        kind="income",
        date="2026-06-01",
        category=income_cat["id"],
        to_amount=4000,
        from_amount=None,
        from_account_id=None,
        to_account_id="a",
    )
    _txn(
        kind="expense",
        date="2026-06-06",
        category=None,
        from_amount=45,
        to_amount=None,
        from_account_id="a",
        to_account_id=None,
    )
    body = client.get("/api/planning/proportional/month", params={"ym": "2026-06"}).json()
    assert body["unassigned"]["uncategorized"] == 45.0
    salary_row = next(c for c in body["income_categories"] if c["id"] == income_cat["id"])
    assert salary_row["counted"] is True
    assert salary_row["label"] == "Salary X"


def test_get_month_income_source_is_profile_for_current_month(client, monkeypatch):
    from backend.services import profile as profile_service

    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {**profile_service.DEFAULT_PROFILE, "monthly_net_income": 6500.0},
    )
    body = client.get("/api/planning/proportional/month").json()  # defaults to current month
    assert body["income"]["source"] == "profile"
    assert body["income"]["amount"] == 6500.0
    assert body["income"]["profile"] == 6500.0


# ── fold()/get_budget_view consistency with the REST endpoint ───────────────────────


def test_view_and_month_endpoint_report_the_same_activity_and_group(client):
    food_env = _env(_FOOD)
    _txn(
        kind="income",
        date="2026-06-01",
        category="",
        to_amount=5000,
        from_amount=None,
        from_account_id=None,
        to_account_id="a",
    )
    _txn(
        kind="expense",
        date="2026-06-05",
        category=_FOOD,
        from_amount=300,
        to_amount=None,
        from_account_id="a",
        to_account_id=None,
    )

    month = client.get("/api/planning/proportional/month", params={"ym": "2026-06"}).json()
    view = client.get(
        "/api/planning/view", params={"engine": "proportional", "ym": "2026-06"}
    ).json()

    food_month_row = next(e for e in month["envelopes"] if e["id"] == food_env)
    group = next(g for g in view["groups"] if g["id"] == food_month_row["group_id"])
    food_view_row = next(r for r in group["rows"] if r["id"] == food_env)
    view_activity = next(m for m in food_view_row["measures"] if m["key"] == "activity")[
        "amount_base"
    ]

    assert food_month_row["activity"] == view_activity == 300.0

    savings_income = next(
        m
        for m in next(g for g in view["groups"] if g["id"] == "savings")["subtotal"]
        if m["key"] == "income"
    )["amount_base"]
    assert savings_income == month["income"]["amount"]


# ── Verbs via POST /api/planning/verb ────────────────────────────────────────────────


def test_post_verb_configure_saves_group_targets(client):
    bills = _group("Bills")
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "configure",
            "params": {"group_targets": {bills: 50}},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"group_targets": {bills: 50.0}}


def test_post_verb_set_caps_bulk(client):
    food_env = _env(_FOOD)
    transport_env = _env("cat_db5642ab0000")  # Transport
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_caps",
            "params": {"month": "2099-09", "caps": {food_env: 400, transport_env: 150}},
        },
    )
    assert r.status_code == 200
    assert r.json() == {
        "caps": {food_env: [["2099-09", 400.0]], transport_env: [["2099-09", 150.0]]}
    }


def test_post_verb_copy_targets_to_caps(client):
    food_env = _env(_FOOD)
    client.put(f"/api/planning/targets/{food_env}", json={"type": "monthly", "amount": 350})
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "copy_targets_to_caps",
            "params": {"month": "2099-09"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 1
    assert body["caps"][food_env] == [["2099-09", 350.0]]


def test_post_verb_set_income_categories(client):
    income_cat = CategoryStore().insert(
        {"id": "cat_bonus_x", "label": "Bonus X", "kind": "income", "color": ""}
    )
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_income_categories",
            "params": {"category_ids": [income_cat["id"]]},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"income_category_ids": [income_cat["id"]]}


def test_post_verb_set_cap_422s_a_past_month(client):
    food_env = _env(_FOOD)
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_cap",
            "params": {"envelope_id": food_env, "month": "2020-01", "amount": 100},
        },
    )
    assert r.status_code == 422
    assert "past months are history" in r.json()["detail"]


def test_post_verb_set_group_targets_422s_unknown_group(client):
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_group_targets",
            "params": {"targets": {"grp_bogus": 50}},
        },
    )
    assert r.status_code == 422


def test_post_verb_set_group_excluded_422s_unknown_group(client):
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_group_excluded",
            "params": {"group_id": "grp_bogus", "excluded": True},
        },
    )
    assert r.status_code == 422


# ── An end-to-end round trip: set a group's target, see it (and savings') on the screen ──


def test_post_verb_set_group_targets_then_get_month_shows_target_and_savings_target(client):
    bills = _group("Bills")
    food_env = _env(_FOOD)
    planning_service.apply_budget_change(
        {"type": "move_category", "category": food_env, "group_id": bills}
    )

    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "proportional",
            "verb_id": "set_group_targets",
            "params": {"targets": {bills: 40}},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"group_targets": {bills: 40.0}}

    body = client.get("/api/planning/proportional/month", params={"ym": "2026-06"}).json()
    bills_group = next(g for g in body["groups"] if g["id"] == bills)
    assert bills_group["target"] == 40.0
    assert body["savings"]["target"] == pytest.approx(60.0)  # 100 - 40
