"""Rules — fixed transaction rules with no AI (docs/design/transaction-rules.md).

Pure units (conditions, split maths, ordering), the ledger's create pass under both flag states,
the save choke, apply/run through the update choke, and the routes. Synthetic data only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.automation_store import AutomationStore
from backend.main import app
from backend.services import accounts as accounts_service
from backend.services import audit, payee_registry
from backend.services import categories as categories_service
from backend.services import ledger as svc
from backend.services.automations import rule_apply
from backend.services.automations.rule_actions import compute_split
from backend.services.automations.rule_engine import apply_rules
from backend.services.automations.service import apply_automation_change
from backend.services.automations.transaction_events import (
    match_transaction_automation,
    ordered_transforms,
)
from backend.services.automations.txn_conditions import conditions_match, validate_conditions

rules_on = pytest.mark.flags(transaction_rules=True)
rules_off = pytest.mark.flags(transaction_rules=False)


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    account = accounts_service.create_account(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1000,
        }
    )
    cats = {
        label: categories_service.create_category(label)["id"]
        for label in ("Gym", "Fun", "Health", "Other", "Food")
    }
    payee = payee_registry.create_payee("Acme Gym", default_category=cats["Food"])
    return {"account": account["id"], "cats": cats, "payee": payee["id"]}


def _txn(book, amount, description="ACME GYM", day="2026-11-01", **extra):
    return svc.create_transaction(
        {
            "kind": "expense",
            "from_account_id": book["account"],
            "from_amount": amount,
            "from_currency": "SGD",
            "description": description,
            "date": day,
            **extra,
        },
        allow_duplicate=True,
    )


def _rule(book, conditions, set_=None, *, name="Rule", splits=None, match="all", **execution):
    body = {"kind": "transaction_transform", "set": set_ or {}, **execution}
    if splits:
        body["splits"] = splits
    return apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": name,
                "trigger": {
                    "kind": "event",
                    "event": "txn",
                    "when": {"conditions": conditions, "match": match},
                },
                "execution": body,
            },
        }
    )


def _payee_is(book):
    return {"field": "payee", "op": "is", "value": book["payee"]}


# ── conditions ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("op", "value", "amount", "expected"),
    [
        ("is", 20, 20.0, True),
        ("is", 20, 20.01, False),
        ("between", [10, 30], 10, True),
        ("between", [10, 30], 30, True),
        ("between", [10, 30], 30.01, False),
        ("approx", 100, 107.5, True),
        ("approx", 100, 92.5, True),
        ("approx", 100, 107.6, False),
        ("over", 10, 10, False),
    ],
)
def test_amount_operators(op, value, amount, expected):
    conditions = validate_conditions([{"field": "amount", "op": op, "value": value}])
    assert conditions_match(conditions, {"base_amount": amount}) is expected


def test_between_needs_an_ordered_pair():
    with pytest.raises(ValueError, match="low <= high"):
        validate_conditions([{"field": "amount", "op": "between", "value": [30, 10]}])


def test_match_any_needs_one_condition_and_skips_the_conflict_check():
    conditions = [
        {"field": "payee", "op": "is", "value": "a"},
        {"field": "payee", "op": "is", "value": "b"},
    ]
    with pytest.raises(ValueError, match="one payee"):
        validate_conditions(conditions)
    assert validate_conditions(conditions, "any")
    assert conditions_match(conditions, {"payee_id": "b"}, match="any")
    assert not conditions_match(conditions, {"payee_id": "c"}, match="any")


# ── split maths ─────────────────────────────────────────────────────────────────────────


def _line(kind, value=None, category="cat_a"):
    amount = {"type": kind} if value is None else {"type": kind, "value": value}
    return {"amount": amount, "category": category}


def test_split_fixed_percent_and_remainder():
    lines = [_line("fixed", 30), _line("percent", 50, "cat_b"), _line("remainder", None, "cat_c")]
    got = compute_split(100, lines)
    assert [line["amount"] for line in got] == [30.0, 35.0, 35.0]


def test_split_rounding_lands_on_the_remainder_line():
    got = compute_split(10, [_line("percent", 33.333), _line("remainder", None, "cat_b")])
    assert sum(line["amount"] for line in got) == 10.0
    assert got[0]["amount"] == 3.33 and got[1]["amount"] == 6.67


def test_split_rounding_lands_on_the_last_percent_line_without_a_remainder():
    got = compute_split(10, [_line("percent", 33.333), _line("percent", 66.667, "cat_b")])
    assert sum(line["amount"] for line in got) == 10.0


@pytest.mark.parametrize(
    "lines",
    [
        [_line("fixed", 30)],  # short of the amount, nothing takes the rest
        [_line("fixed", 120), _line("remainder", None, "cat_b")],  # remainder would be negative
        [_line("percent", 60), _line("percent", 60, "cat_b")],  # over 100%
    ],
)
def test_split_that_does_not_add_up_is_none(lines):
    assert compute_split(100, lines) is None


# ── ordering and the pass ───────────────────────────────────────────────────────────────


def _row(id_, *, category="cat_x", position=None, stop=False, priority=100, **when):
    execution = {"kind": "transaction_transform", "set": {"category": category}, "stop": stop}
    if position is not None:
        execution["position"] = position
    else:
        execution["priority"] = priority
    return {
        "id": id_,
        "name": id_,
        "status": "active",
        "created_at": "2026-11-01",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_contains": "acme", **when},
        },
        "execution": {**execution, "override_existing": False},
    }


def test_position_orders_rules_and_priority_orders_legacy_rows():
    rows = [_row("c", position=2), _row("a", position=0), _row("legacy", priority=1)]
    assert [r["id"] for r in ordered_transforms(rows)] == ["a", "legacy", "c"]


def test_later_rule_wins_where_two_set_the_same_field():
    rules = [
        _row("first", category="cat_1", position=0),
        _row("second", category="cat_2", position=1),
    ]
    row, matches, _ = apply_rules(
        {"kind": "expense", "description": "ACME"}, rules, finalize=lambda r: r
    )
    assert row["category"] == "cat_2"
    assert [m.automation_id for m in matches] == ["first", "second"]


def test_a_category_already_on_the_row_is_not_replaced():
    rules = [_row("r", category="cat_1", position=0)]
    row, _, skipped = apply_rules(
        {"kind": "expense", "description": "ACME", "category": "cat_keep"},
        rules,
        finalize=lambda r: r,
    )
    assert row["category"] == "cat_keep"
    assert skipped[0]["reason"] == "category already set"


def test_stop_ends_the_pass_and_a_stored_row_without_stop_defaults_to_stopping():
    stopper = _row("first", category="cat_1", position=0, stop=True)
    rules = [stopper, _row("second", category="cat_2", position=1)]
    row, matches, _ = apply_rules(
        {"kind": "expense", "description": "ACME"}, rules, finalize=lambda r: r
    )
    assert row["category"] == "cat_1" and len(matches) == 1
    legacy = _row("legacy", category="cat_1", priority=1)
    del legacy["execution"]["stop"]
    row, matches, _ = apply_rules(
        {"kind": "expense", "description": "ACME"},
        [legacy, _row("late", category="cat_2", priority=2)],
        finalize=lambda r: r,
    )
    assert row["category"] == "cat_1" and len(matches) == 1


def test_legacy_flat_keys_still_match():
    rule = _row("r", position=0)
    assert match_transaction_automation(rule, {"description": "Acme Gym"})
    assert not match_transaction_automation(rule, {"description": "Other"})


# ── the save choke ──────────────────────────────────────────────────────────────────────


@rules_off
def test_creating_a_rule_is_refused_while_the_flag_is_off(book):
    with pytest.raises(ValidationError, match="Lucius"):
        _rule(book, [_payee_is(book)], {"category": book["cats"]["Gym"]})
    assert AutomationStore().all() == []


@rules_on
def test_a_rule_is_saved_with_a_sentence_a_position_and_ids(book):
    first = _rule(book, [_payee_is(book)], {"category": "Gym", "tags": ["fitness"]}, name="One")
    second = _rule(book, [_payee_is(book)], {"category": book["cats"]["Fun"]}, name="Two")
    assert first["execution"]["set"]["category"] == book["cats"]["Gym"]
    assert first["execution"]["stop"] is False
    assert [first["execution"]["position"], second["execution"]["position"]] == [0, 1]
    assert first["trigger"]["limit"] == "each"
    assert (
        first["instructions"]
        == "When payee is Acme Gym, set category to Gym, then add tags fitness."
    )


@rules_on
def test_the_sentence_reads_any_amounts_and_splits(book):
    rule = _rule(
        book,
        [
            {"field": "amount", "op": "between", "value": [10, 30]},
            {"field": "description", "op": "contains", "value": "gym"},
        ],
        splits=[_line("fixed", 30, "Gym"), _line("remainder", None, "Fun")],
        match="any",
    )
    assert rule["instructions"] == (
        "When amount is between 10 and 30 or description contains any of gym, "
        "split 30 to Gym, the rest to Fun."
    )


@rules_on
def test_an_unknown_category_or_a_bad_split_is_refused(book):
    with pytest.raises(ValidationError, match="existing category"):
        _rule(book, [], {"category": "Nope"})
    with pytest.raises(ValidationError, match="at most one remainder"):
        _rule(book, [], splits=[_line("remainder", None, "Gym"), _line("remainder", None, "Fun")])
    with pytest.raises(ValidationError, match="at most 10"):
        _rule(book, [], splits=[_line("fixed", 1, "Gym")] * 11)


@rules_on
def test_a_rule_is_never_queued_as_a_lucius_run(book):
    from backend.services.automations.events import _active_event_automations

    _rule(book, [_payee_is(book)], {"category": "Gym"})
    assert _active_event_automations("txn") == []


# ── the create pass ─────────────────────────────────────────────────────────────────────


@rules_on
def test_payee_is_matches_after_payee_resolution_and_a_rule_beats_the_payee_default(book):
    _rule(book, [_payee_is(book)], {"category": "Gym"})
    stored = _txn(book, 20)
    assert stored["payee_id"] == book["payee"]
    assert stored["category"] == book["cats"]["Gym"]  # rule > the payee's Food default


@rules_on
def test_an_explicit_category_beats_a_rule_and_the_payee_default_fills_the_rest(book):
    _rule(book, [_payee_is(book)], {"category": "Gym"})
    assert _txn(book, 21, category=book["cats"]["Fun"])["category"] == book["cats"]["Fun"]
    _rule(book, [{"field": "amount", "op": "over", "value": 1000}], {"category": "Gym"})
    assert _txn(book, 22, description="ACME GYM x")["category"] == book["cats"]["Gym"]


@rules_on
def test_a_non_matching_rule_leaves_the_payee_default(book):
    _rule(book, [{"field": "amount", "op": "between", "value": [10, 30]}], {"category": "Gym"})
    assert _txn(book, 50)["category"] == book["cats"]["Food"]


@rules_on
def test_a_split_rule_splits_the_row_on_create(book):
    cats = book["cats"]
    _rule(
        book,
        [_payee_is(book), {"field": "amount", "op": "approx", "value": 100}],
        {"tags": ["bundle"]},
        splits=[_line("fixed", 30, "Gym"), _line("remainder", None, "Other")],
    )
    stored = _txn(book, 104)
    assert stored["category"] == "split"
    assert [(s["category"], s["amount"]) for s in stored["splits"]] == [
        (cats["Gym"], 30.0),
        (cats["Other"], 74.0),
    ]
    assert stored["tags"] == ["bundle"]


@rules_on
def test_a_split_that_does_not_add_up_is_skipped_never_refused(book):
    _rule(book, [_payee_is(book)], splits=[_line("fixed", 30, "Gym")])
    stored = _txn(book, 100)
    assert not stored.get("splits")
    assert stored["category"] == book["cats"]["Food"]  # the payee default, as if no rule


@rules_on
def test_an_edit_never_re_runs_rules(book):
    stored = _txn(book, 20)
    rule = _rule(book, [_payee_is(book)], {"category": "Gym", "tags": ["late"]})
    updated = svc.update_transaction(stored["id"], {"memo": "edited"})
    assert "late" not in (updated.get("tags") or [])
    assert rule


@rules_on
def test_rules_run_in_list_order_and_the_later_one_wins(book):
    _rule(book, [_payee_is(book)], {"category": "Gym"}, name="A")
    _rule(book, [_payee_is(book)], {"category": "Fun", "tags": ["t"]}, name="B")
    stored = _txn(book, 20)
    assert stored["category"] == book["cats"]["Fun"] and stored["tags"] == ["t"]


@rules_on
def test_set_payee_action_links_the_row_to_the_payee(book):
    other = payee_registry.create_payee("Zed Fitness")
    _rule(
        book, [{"field": "description", "op": "contains", "value": "zed"}], {"payee": "Zed Fitness"}
    )
    assert _txn(book, 25, description="ZED 123")["payee_id"] == other["id"]


def _legacy(book, when):
    return AutomationStore().create(
        {
            "name": "legacy",
            "trigger": {"kind": "event", "event": "txn", "limit": "each", "when": when},
            "execution": {
                "kind": "transaction_transform",
                "set": {"category": book["cats"]["Gym"]},
                "priority": 1,
            },
        }
    )


def test_flag_off_the_legacy_pass_runs_before_payee_resolution(book):
    _legacy(book, {"description_contains": "acme"})
    assert _txn(book, 20)["category"] == book["cats"]["Gym"]


@rules_off
def test_flag_off_a_payee_condition_cannot_match_yet(book):
    _legacy(book, {"conditions": [_payee_is(book)]})
    assert _txn(book, 20)["category"] == book["cats"]["Food"]  # the payee default, not the rule


@rules_on
def test_flag_on_the_same_legacy_row_sees_the_resolved_payee(book):
    _legacy(book, {"conditions": [_payee_is(book)]})
    assert _txn(book, 20)["category"] == book["cats"]["Gym"]


# ── apply / run on history ──────────────────────────────────────────────────────────────


@rules_on
def test_preview_lists_matches_with_before_and_after(book):
    stored = _txn(book, 20)
    _txn(book, 400, day="2026-11-02")
    body = {
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {
                "conditions": [
                    _payee_is(book),
                    {"field": "amount", "op": "between", "value": [10, 30]},
                ]
            },
        },
        "execution": {
            "kind": "transaction_transform",
            "set": {"category": "Gym"},
            "override_existing": True,
        },
    }
    result = rule_apply.preview_rule_matches(body, 50)
    assert result["total"] == 1
    match = result["matches"][0]
    assert match["transaction_id"] == stored["id"] and match["payee_name"] == "Acme Gym"
    assert match["category"] == book["cats"]["Food"]
    assert match["after"]["category"] == book["cats"]["Gym"]


@rules_on
def test_preview_reads_the_starting_balances_once_not_per_transaction(book, monkeypatch):
    # Reading them per row re-loaded every account for each transaction: 25s on a 3,000-row book.
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        _txn(book, 20, day=day)
    calls = []
    real = svc.opening_txn_ids
    monkeypatch.setattr(svc, "opening_txn_ids", lambda *a, **k: calls.append(1) or real(*a, **k))
    body = {
        "trigger": {"kind": "event", "event": "txn", "when": {"conditions": [_payee_is(book)]}},
        "execution": {"kind": "transaction_transform", "set": {"tags": ["x"]}},
    }

    assert rule_apply.preview_rule_matches(body, 50)["total"] == 3
    assert len(calls) == 1


@rules_on
def test_apply_writes_through_the_update_choke_and_respects_the_category_guard(book):
    keep = _txn(book, 20)
    other = _txn(book, 30, day="2026-11-02", category=book["cats"]["Fun"])
    body = {
        "trigger": {"kind": "event", "event": "txn", "when": {"conditions": [_payee_is(book)]}},
        "execution": {"kind": "transaction_transform", "set": {"category": "Gym", "tags": ["x"]}},
    }
    result = rule_apply.apply_rule(body, [keep["id"], other["id"], "missing"])
    assert result["applied"] == 2 and result["unchanged"] == 0
    assert {s["transaction_id"] for s in result["skipped"]} == {"missing"}
    assert svc.transactions.find_by_id(keep["id"])["category"] == book["cats"]["Food"]
    assert svc.transactions.find_by_id(keep["id"])["tags"] == ["x"]
    assert svc.transactions.find_by_id(other["id"])["category"] == book["cats"]["Fun"]
    body["execution"]["override_existing"] = True
    again = rule_apply.apply_rule(body, [keep["id"]])
    assert again["applied"] == 1
    row = svc.transactions.find_by_id(keep["id"])
    assert row["category"] == book["cats"]["Gym"]
    assert len(audit.trace(keep["id"])["events"]) >= 3  # the create and both applies


@rules_on
def test_apply_splits_a_history_row_and_never_resplits(book):
    row = _txn(book, 100)
    body = {
        "trigger": {"kind": "event", "event": "txn", "when": {}},
        "execution": {
            "kind": "transaction_transform",
            "set": {},
            "override_existing": True,
            "splits": [_line("percent", 40, "Gym"), _line("remainder", None, "Other")],
        },
    }
    assert rule_apply.apply_rule(body, [row["id"]])["applied"] == 1
    stored = svc.transactions.find_by_id(row["id"])
    assert [s["amount"] for s in stored["splits"]] == [40.0, 60.0]
    again = rule_apply.apply_rule(body, [row["id"]])
    assert again["applied"] == 0 and again["skipped"][0]["reason"] == "already split"


@rules_on
def test_a_starting_balance_is_never_touched(book):
    opening = next(iter(svc.opening_txn_ids()))
    body = {
        "trigger": {"kind": "event", "event": "txn", "when": {}},
        "execution": {"kind": "transaction_transform", "set": {"tags": ["x"]}},
    }
    result = rule_apply.apply_rule(body, [opening])
    assert result["applied"] == 0 and "Starting Balance" in result["skipped"][0]["reason"]


@rules_on
def test_run_rules_runs_every_active_rule_in_order(book):
    row = _txn(book, 20, category=None)
    svc.update_transaction(row["id"], {"category": None}, labels_as_given=True)
    _rule(book, [_payee_is(book)], {"category": "Gym"}, name="A")
    _rule(book, [_payee_is(book)], {"tags": ["b"]}, name="B")
    result = rule_apply.run_rules([row["id"]])
    assert result["applied"] == 1
    stored = svc.transactions.find_by_id(row["id"])
    assert stored["category"] == book["cats"]["Gym"] and stored["tags"] == ["b"]
    stats = AutomationStore().all()[0]["stats"]
    assert stats["match_count"] == 1


@rules_on
def test_reorder_sets_positions(book):
    a = _rule(book, [], {"tags": ["a"]}, name="A")
    b = _rule(book, [], {"tags": ["b"]}, name="B")
    ordered = rule_apply.reorder_rules([b["id"], a["id"]])
    assert [r["id"] for r in ordered] == [b["id"], a["id"]]
    with pytest.raises(ValidationError, match="every Rule"):
        rule_apply.reorder_rules([a["id"]])


@rules_off
def test_rules_features_are_not_found_while_the_flag_is_off():
    with pytest.raises(NotFoundError):
        rule_apply.require_rules_enabled()


# ── routes ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(app)


@rules_off
def test_routes_are_404_while_the_flag_is_off(client, book):
    assert client.post("/api/automations/rules/reorder", json={"ids": []}).status_code == 404
    body = {"rule": {"trigger": {}, "execution": {}}}
    assert client.post("/api/automations/rules/preview", json=body).status_code == 404
    assert (
        client.post(
            "/api/automations/rules/apply", json={**body, "transaction_ids": ["a"]}
        ).status_code
        == 404
    )
    assert (
        client.post("/api/automations/rules/run", json={"transaction_ids": ["a"]}).status_code
        == 404
    )


@rules_on
def test_routes_create_preview_apply_run_and_undo(client, book):
    trigger = {"kind": "event", "event": "txn", "when": {"conditions": [_payee_is(book)]}}
    execution = {
        "kind": "transaction_transform",
        "set": {"category": "Gym"},
        "override_existing": True,
    }
    created = client.post(
        "/api/automations", json={"name": "Gym", "trigger": trigger, "execution": execution}
    )
    assert created.status_code == 201, created.text
    assert created.json()["execution"]["position"] == 0
    row = _txn(book, 20)
    assert row["category"] == book["cats"]["Gym"]
    svc.update_transaction(row["id"], {"category": book["cats"]["Fun"]}, labels_as_given=True)

    rule = {"trigger": trigger, "execution": execution}
    preview = client.post("/api/automations/rules/preview", json={"rule": rule, "limit": 10})
    assert preview.status_code == 200 and preview.json()["total"] == 1
    applied = client.post(
        "/api/automations/rules/apply", json={"rule": rule, "transaction_ids": [row["id"]]}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 1 and applied.json()["checkpoint_id"]
    assert svc.transactions.find_by_id(row["id"])["category"] == book["cats"]["Gym"]

    undone = client.post(
        f"/api/checkpoints/{applied.json()['checkpoint_id']}/restore", json={"mode": "data"}
    )
    assert undone.status_code == 200, undone.text
    assert svc.transactions.find_by_id(row["id"])["category"] == book["cats"]["Fun"]

    ran = client.post("/api/automations/rules/run", json={"transaction_ids": [row["id"]]})
    assert ran.status_code == 200 and "checkpoint_id" in ran.json()
    reordered = client.post("/api/automations/rules/reorder", json={"ids": [created.json()["id"]]})
    assert reordered.status_code == 200 and len(reordered.json()["automations"]) == 1
    too_many = client.post("/api/automations/rules/run", json={"transaction_ids": ["x"] * 501})
    assert too_many.status_code == 422


# ── category delete / merge follows a Rule's split lines ───────────────────────────────


def _split_rule(book):
    cats = book["cats"]
    return _rule(
        book,
        [_payee_is(book)],
        splits=[
            {"amount": {"type": "fixed", "value": 30}, "category": cats["Gym"]},
            {"amount": {"type": "remainder"}, "category": cats["Fun"]},
        ],
    )


@rules_on
def test_deleting_a_category_reassigned_moves_a_split_line_to_the_replacement(book):
    cats = book["cats"]
    rule = _split_rule(book)

    categories_service.delete_category(cats["Gym"], reassign=True, reassign_to=cats["Health"])

    stored = AutomationStore().find_by_id(rule["id"])
    lines = stored["execution"]["splits"]
    assert [line["category"] for line in lines] == [cats["Health"], cats["Fun"]]
    assert stored["status"] == "active"


@rules_on
def test_deleting_a_category_a_rule_uses_is_refused_without_reassign(book):
    cats = book["cats"]
    _rule(book, [_payee_is(book)], {"category": cats["Health"]}, name="Gym rule")

    with pytest.raises(ConflictError, match="Gym rule"):
        categories_service.delete_category(cats["Health"])


@rules_on
def test_deleting_a_split_lines_category_pauses_the_rule_instead_of_deleting_it(book):
    cats = book["cats"]
    rule = _split_rule(book)

    categories_service.delete_category(cats["Gym"], reassign=True)

    stored = AutomationStore().find_by_id(rule["id"])
    assert stored is not None
    assert stored["status"] == "paused"
    # The dead id stays on its line: a line with no category would fail every later save.
    assert [line["category"] for line in stored["execution"]["splits"]] == [
        cats["Gym"],
        cats["Fun"],
    ]


@rules_on
def test_after_a_split_lines_category_is_deleted_every_rule_still_reorders_and_resumes(book):
    cats = book["cats"]
    split = _split_rule(book)
    other = _rule(book, [_payee_is(book)], {"tags": ["x"]}, name="Other")
    categories_service.delete_category(cats["Gym"], reassign=True)

    ordered = rule_apply.reorder_rules([other["id"], split["id"]])
    resumed = apply_automation_change(
        {"op": "update", "id": split["id"], "changes": {"status": "active"}}
    )

    assert [r["id"] for r in ordered] == [other["id"], split["id"]]
    assert resumed["status"] == "active"
    row = _txn(book, 50, payee_id=book["payee"])  # the split names a gone category: skipped
    assert not row.get("splits") and "x" in row["tags"]


@rules_on
def test_a_saved_rule_naming_a_payee_deleted_since_still_applies_its_other_actions(book):
    rule = _rule(book, [_payee_is(book)], {"tags": ["x"], "payee": book["payee"]})
    row = _txn(book, 20, description="something else")
    store = AutomationStore()
    execution = {**rule["execution"], "set": {**rule["execution"]["set"], "payee": "pay_gone"}}
    store.update_by_id(rule["id"], {"execution": execution})
    saved = {"id": rule["id"], "trigger": rule["trigger"], "execution": execution}

    result = rule_apply.apply_rule(saved, [row["id"]])

    assert result["applied"] == 1
    assert "x" in svc.transactions.find_by_id(row["id"])["tags"]


def test_with_the_flag_off_an_existing_rule_can_still_be_paused(book):
    from backend.core.feature_flags import override

    with override(transaction_rules=True):
        rule = _rule(book, [_payee_is(book)], {"tags": ["x"]})
    with override(transaction_rules=False):
        paused = apply_automation_change(
            {"op": "update", "id": rule["id"], "changes": {"status": "paused"}}
        )
        with pytest.raises(ValidationError, match="Lucius"):
            _rule(book, [_payee_is(book)], {"tags": ["y"]}, name="New")
    assert paused["status"] == "paused"


@rules_on
def test_run_rules_does_not_count_a_tag_reorder_as_a_change(book):
    _rule(book, [{"field": "amount", "op": "over", "value": 1000}], {"tags": ["x"]})
    row = _txn(book, 20)
    svc.transactions.update_by_id(row["id"], {"tags": ["b", "a"]})

    result = rule_apply.run_rules([row["id"]])

    assert result["applied"] == 0 and result["unchanged"] == 1


def test_a_rule_sets_a_payee_only_on_expense_and_income():
    from backend.services.automations.rule_actions import apply_actions

    rule = {"id": "r", "execution": {"kind": "transaction_transform", "set": {"payee": "pay_x"}}}
    for kind, expected in (("expense", "pay_x"), ("income", "pay_x"), ("investment", None)):
        out = apply_actions({"kind": kind, "payee_id": None}, rule, finalize=lambda r: r)
        assert out.row.get("payee_id") == expected, kind


@rules_on
def test_merging_a_category_moves_a_rules_category_to_the_target(book):
    cats = book["cats"]
    rule = _rule(book, [_payee_is(book)], {"category": cats["Gym"]})

    categories_service.rename_category(cats["Gym"], merge_into=cats["Health"])

    stored = AutomationStore().find_by_id(rule["id"])
    assert stored["execution"]["set"]["category"] == cats["Health"]
    assert stored["status"] == "active"


@rules_on
def test_a_rule_naming_a_category_gone_since_never_refuses_the_transaction(book):
    cats = book["cats"]
    rule = _rule(book, [_payee_is(book)], {"category": cats["Gym"], "tags": ["gym"]})
    store = AutomationStore()
    execution = {
        **rule["execution"],
        "set": {**rule["execution"]["set"], "category": "cat_0000dead0000"},
    }
    store.update_by_id(rule["id"], {"execution": execution})

    row = _txn(book, 20, payee_id=book["payee"])

    assert row["category"] == cats["Food"]  # the payee default, not the dead id
    assert "gym" in row["tags"]


# ── Audit trail: what a Rule (or a payee default) did is recorded, not only who saved ──────


def _events(txn_id, op):
    return [e for e in audit.trace(txn_id)["events"] if e["op"] == op]


@rules_on
def test_a_create_records_every_rule_that_changed_it_and_which_fields(book):
    cat = _rule(book, [_payee_is(book)], {"category": "Gym"}, name="Gym it")
    tag = _rule(book, [_payee_is(book)], {"tags": ["fitness"], "memo_append": "club"}, name="Tag")
    stored = _txn(book, 20)
    (create,) = _events(stored["id"], "create")
    assert create["provenance"]["rules"] == [
        {"automation_id": cat["id"], "automation_name": "Gym it", "fields": ["category"]},
        {"automation_id": tag["id"], "automation_name": "Tag", "fields": ["tags", "memo"]},
    ]
    assert create["automation_applied"]["automation_id"] == cat["id"]  # unchanged shape
    assert "payee_default" not in create["provenance"]


@rules_on
def test_a_rule_that_matched_but_could_not_act_is_recorded_with_why(book):
    rule = _rule(book, [_payee_is(book)], splits=[_line("fixed", 30, "Gym")], name="Bad split")
    stored = _txn(book, 100)
    (create,) = _events(stored["id"], "create")
    (skipped,) = create["provenance"]["rules_skipped"]
    assert skipped["automation_id"] == rule["id"] and skipped["reason"]
    # The payee default then filled the category — recorded as such, not as a Rule's doing.
    assert create["provenance"]["payee_default"] == {
        "payee_id": book["payee"],
        "category": book["cats"]["Food"],
    }


@rules_on
def test_a_payee_default_category_is_recorded(book):
    stored = _txn(book, 20)
    (create,) = _events(stored["id"], "create")
    assert create["provenance"] == {
        "payee_default": {"payee_id": book["payee"], "category": book["cats"]["Food"]}
    }


@rules_on
def test_a_category_the_caller_chose_carries_no_provenance(book):
    stored = _txn(book, 20, category=book["cats"]["Fun"])
    (create,) = _events(stored["id"], "create")
    assert "provenance" not in create


@rules_on
def test_running_rules_on_history_names_the_rules_on_each_edit(book):
    row = _txn(book, 20, category=None)
    svc.update_transaction(row["id"], {"category": None}, labels_as_given=True)
    gym = _rule(book, [_payee_is(book)], {"category": "Gym"}, name="A")
    rule_apply.run_rules([row["id"]])
    last = _events(row["id"], "update")[-1]
    assert last["provenance"]["rules"] == [
        {"automation_id": gym["id"], "automation_name": "A", "fields": ["category"]}
    ]


@rules_on
def test_applying_one_rule_to_history_names_it_and_ordinary_edits_name_none(book):
    row = _txn(book, 20)
    rule = _rule(book, [_payee_is(book)], {"tags": ["late"]}, name="Late")
    body = {
        "id": rule["id"],
        "name": "Late",
        "trigger": rule["trigger"],
        "execution": rule["execution"],
    }
    rule_apply.apply_rule(body, [row["id"]])
    applied = _events(row["id"], "update")[-1]
    assert applied["provenance"]["rules"][0]["fields"] == ["tags"]
    svc.update_transaction(row["id"], {"memo": "by hand"})
    assert "provenance" not in _events(row["id"], "update")[-1]


# ── Lucius's approval card for applying a Rule to history: names, from → to, skips ─────────


def _apply_card(args):
    from backend.ai.tools.automations import _APPLY_RULE_PREVIEW

    return _APPLY_RULE_PREVIEW.build(args)


@rules_on
def test_the_apply_card_names_the_rule_and_each_change_and_skip(book):
    cats = book["cats"]
    keep = _txn(book, 20, category=None)
    svc.update_transaction(keep["id"], {"category": None}, labels_as_given=True)
    already = _txn(book, 25, category=cats["Fun"])
    rule = _rule(book, [_payee_is(book)], {"category": "Gym", "tags": ["T1"]}, name="Gym it")
    card = _apply_card({"rule_id": rule["id"], "transaction_ids": [keep["id"], already["id"]]})

    assert card["kind"] == "field_diff"
    head, *rows = card["data"]["items"]
    assert head["name"] == "Rule “Gym it”"
    assert head["changes"]["Transactions"]["new"] == "2 will change"
    first = next(r for r in rows if r["id"] == keep["id"])
    assert "Acme Gym" in first["name"] and "20.00" in first["name"]
    assert first["changes"]["Category"] == {"old": "Uncategorized", "new": "Gym"}
    assert first["changes"]["Tags"] == {"old": "None", "new": "T1"}
    # A category already on a row is kept (no override) — only the tag changes there.
    second = next(r for r in rows if r["id"] == already["id"])
    assert "Category" not in second["changes"]
    from backend.ai.tools.automations import _APPLY_RULE_PREVIEW

    assert _APPLY_RULE_PREVIEW.validate(card) is None


@rules_on
def test_the_apply_card_lists_a_starting_balance_as_skipped_and_writes_nothing(book):
    opening = svc.opening_txn_ids()
    opening_id = next(iter(opening))
    rule = _rule(book, [_payee_is(book)], {"tags": ["T1"]}, name="Tag")
    before = svc.transactions.find_by_id(opening_id)
    card = _apply_card({"rule_id": rule["id"], "transaction_ids": [opening_id]})
    head, row = card["data"]["items"]
    assert head["changes"]["Transactions"]["new"] == "0 will change, 1 skipped"
    assert "Starting Balance" in row["changes"]["Skipped"]["new"]
    assert svc.transactions.find_by_id(opening_id) == before


@rules_on
def test_the_apply_card_for_every_rule_and_a_deleted_rule(book):
    row = _txn(book, 20)
    _rule(book, [_payee_is(book)], {"tags": ["T1"]}, name="Tag")
    card = _apply_card({"transaction_ids": [row["id"]]})
    assert card["data"]["items"][0]["name"] == "Every active rule, top to bottom"
    assert _apply_card({"rule_id": "automation_gone", "transaction_ids": [row["id"]]}) is None
