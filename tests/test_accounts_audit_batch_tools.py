"""Batch-acceptance tests for the accounts + audit AI tools — post-rename.

Owner complaint (harness reliability program, 2026-07-19): an earlier lane made these
tools accept batches by widening their SINGULAR id param to `anyOf[string, array]` without
renaming the tool — `delete_account(account_id=[...])` — which is exactly the trap that
made the model stringify a real list instead of passing one. Fixed here: `update_account`
-> `update_accounts`, `delete_account` -> `delete_accounts`, `reconcile_account` ->
`reconcile_accounts`, `set_transaction_cleared` -> `set_transactions_cleared` (all
`ai/tools/accounts.py`) and `trace_transaction` -> `trace_transactions`
(`ai/tools/audit.py`) — both the tool name AND the batch param are now plural. Every old
name still resolves via alias (`registry._LEGACY_TOOL_ALIASES` + `migrate_tool_names.
RENAMES` — the sole rename record; see tests/test_tool_name_migration.py).

Three things every batch tool here must prove:
  1. N entities change in ONE call.
  2. A per-item refusal/not-found/ambiguity is reported per item and never aborts the
     rest of the batch (the "does partial failure stay legible" test).
  3. A single-target call — via the NEW plural param with one item, or the OLD alias name
     with its legacy singular field — gets the exact pre-rename singular response shape.

Offline + isolated: every store the path touches is monkeypatched into tmp_path.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core import audit_context
from backend.services import ledger as svc


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    audit_context.clear_audit_scope()
    yield tmp_path
    audit_context.clear_audit_scope()


def _acct(name, **overrides):
    data = {"name": name, "type": "cash", "currency": "SGD"}
    data.update(overrides)
    return svc.accounts.create(data)


def _expense(account_id, amount=10.0, desc="x", cleared=False):
    return _arrange_create(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": desc,
            "from_account_id": account_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "cleared": cleared,
        }
    )


# ── create_account: seven types only, dead fields refused ────────────────────────
#
# ADR 2026-09-07-seven-account-types-one-axis: an old (pre-collapse) type is a
# ValidationError raised by the store; a "dead" field (kind/asset_class/group/locked/
# on_budget — none of which the seven-type schema declares any more) is refused by
# services.accounts.assert_no_dead_account_fields BEFORE any write. Both surface as the
# tool's standard ``{"ok": False, "error": ...}`` shape, never a raised exception —
# restores the AI-tool coverage a REST-focused test absorbed in the Task 5 retype.


def test_create_account_tool_refuses_dead_fields_and_old_types(env):
    out, _ = call("create_account", name="X", type="checking", currency="SGD")
    assert out["ok"] is False and "error" in out
    assert not any(a["name"] == "X" for a in svc.accounts.all())

    out, _ = call("create_account", name="Y", type="cash", currency="SGD", locked=True)
    assert out["ok"] is False and "error" in out
    assert not any(a["name"] == "Y" for a in svc.accounts.all())


# ── update_accounts: batch ────────────────────────────────────────────────────────


def test_update_accounts_batch_applies_per_item_changes(env):
    a, b, c = _acct("A"), _acct("B"), _acct("C")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": a["id"], "archived": True},
            {"account_id": b["id"], "archived": True},
            {"account_id": c["id"], "archived": True},
        ],
    )
    assert res["ok"] is True
    assert res["count"] == 3
    assert {u["id"] for u in res["updated"]} == {a["id"], b["id"], c["id"]}
    assert all(u["archived"] is True for u in res["updated"])
    assert res["not_found"] == [] and res["ambiguous"] == []


def test_update_accounts_batch_can_vary_the_change_per_item(env):
    a, b = _acct("A"), _acct("B")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": a["id"], "note": "for A"},
            {"account_id": b["id"], "external_ids": [{"source": "plaid", "ref": "p1"}]},
        ],
    )
    assert res["ok"] is True and res["count"] == 2
    by_id = {u["id"]: u for u in res["updated"]}
    assert by_id[a["id"]]["note"] == "for A"
    assert by_id[b["id"]]["connected"] is True


def test_update_accounts_batch_reports_not_found_without_aborting(env):
    a, b = _acct("A"), _acct("B")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": a["id"], "external_ids": [{"source": "plaid", "ref": "pa"}]},
            {"account_id": "ghost-id", "external_ids": [{"source": "plaid", "ref": "pg"}]},
            {"account_id": b["id"], "external_ids": [{"source": "plaid", "ref": "pb"}]},
        ],
    )
    assert res["ok"] is True
    assert res["not_found"] == ["ghost-id"]
    assert {u["id"] for u in res["updated"]} == {a["id"], b["id"]}
    assert all(u["connected"] is True for u in res["updated"])


def test_update_accounts_batch_refuses_type_change_without_aborting(env):
    """A forbidden type patch is reported per item and cannot block a valid batch edit."""
    a, b = _acct("A", note="original"), _acct("B", note="original")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": a["id"], "name": "A renamed"},
            {"account_id": b["id"], "type": "credit"},
        ],
    )
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["updated"][0]["id"] == a["id"] and res["updated"][0]["name"] == "A renamed"
    assert res["errors"] and "cannot be changed" in res["errors"][0]
    assert svc.accounts.find_by_id(b["id"])["type"] == "cash"


def test_update_accounts_refuses_currency_change(env):
    a = _acct("A")
    res, _ = call("update_accounts", account_id=a["id"], currency="USD")
    assert res["ok"] is False and "currency" in res["error"]
    assert svc.accounts.find_by_id(a["id"])["currency"] == "SGD"


def test_update_accounts_batch_by_name_ambiguity_does_not_block_the_rest(env):
    # Two accounts named "Cash" — resolving "Cash" by name is genuinely ambiguous.
    _acct("Cash")
    _acct("Cash")
    unique = _acct("Unique")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": "Cash", "note": "reviewed"},
            {"account_id": unique["id"], "note": "reviewed"},
        ],
    )
    assert res["ok"] is True
    assert len(res["ambiguous"]) == 1 and "Cash" in res["ambiguous"][0]
    assert res["count"] == 1
    assert res["updated"][0]["id"] == unique["id"]
    assert res["updated"][0]["note"] == "reviewed"


def test_update_accounts_batch_routes_initial_balance_through_opening_choke_per_account(env):
    a, b = _acct("A"), _acct("B")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": a["id"], "initial_balance": 500},
            {"account_id": b["id"], "initial_balance": 500},
        ],
    )
    assert res["ok"] is True
    assert {u["initial_balance"] for u in res["updated"]} == {500.0}
    # The scalar itself is never stored on the account row — routed through
    # set_account_opening, which lands it on the account's Starting Balance transaction
    # (opening_txn_id) instead.
    assert "initial_balance" not in svc.accounts.find_by_id(a["id"])
    assert "initial_balance" not in svc.accounts.find_by_id(b["id"])
    a_opening_id = svc.accounts.find_by_id(a["id"])["opening_txn_id"]
    b_opening_id = svc.accounts.find_by_id(b["id"])["opening_txn_id"]
    assert svc.transactions.find_by_id(a_opening_id) is not None
    assert svc.transactions.find_by_id(b_opening_id) is not None


def test_update_accounts_single_item_keeps_the_pre_rename_shape(env):
    a = _acct("A")
    res, _ = call("update_accounts", updates=[{"account_id": a["id"], "note": "hi"}])
    assert res["ok"] is True
    assert res["account"]["id"] == a["id"]
    assert "updated" not in res and "count" not in res


def test_update_accounts_old_alias_bare_shape_still_works(env):
    """A stored `update_account` call (bare top-level fields, no `updates` wrapper) must
    still dispatch and produce the identical single-item response."""
    a = _acct("A")
    res, _ = call("update_accounts", account_id=a["id"], note="hi")
    assert res["ok"] is True
    assert res["account"]["id"] == a["id"] and res["account"]["note"] == "hi"
    assert "updated" not in res and "count" not in res


# ── update_accounts: no-op honesty (harness reliability program, 2026-07-21) ───────
#
# An item carrying only `account_id` — no other changeable field, and the opening-balance
# flag untouched — writes nothing. The pre-fix response was plain `ok: true` echoing the
# unchanged account, byte-indistinguishable from a real edit (the update_transactions
# incident this mirrors). Must now report `no_op: true` instead.


def test_update_accounts_id_only_single_item_is_a_no_op(env):
    a = _acct("A", note="original")
    res, _ = call("update_accounts", updates=[{"account_id": a["id"]}])
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["account"]["id"] == a["id"]
    assert res["account"]["note"] == "original"
    assert "message" in res
    assert svc.accounts.find_by_id(a["id"])["note"] == "original"


def test_update_accounts_id_only_bare_alias_shape_is_also_a_no_op(env):
    """The pre-rename bare-fields shape (no `updates` wrapper) hits the same single-item
    path and must be equally honest."""
    a = _acct("A", note="original")
    res, _ = call("update_accounts", account_id=a["id"])
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["account"]["note"] == "original"


def test_update_accounts_batch_with_one_no_op_item_still_updates_the_rest(env):
    real, empty = _acct("Real", note="original"), _acct("Empty", note="original")
    res, _ = call(
        "update_accounts",
        updates=[
            {"account_id": real["id"], "note": "changed"},
            {"account_id": empty["id"]},
        ],
    )
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["updated"][0]["id"] == real["id"] and res["updated"][0]["note"] == "changed"
    assert res["no_op_count"] == 1
    assert res["no_op"][0]["id"] == empty["id"] and res["no_op"][0]["note"] == "original"
    assert svc.accounts.find_by_id(real["id"])["note"] == "changed"
    assert svc.accounts.find_by_id(empty["id"])["note"] == "original"


def test_update_accounts_batch_all_no_op_items_reports_zero_updated(env):
    a, b = _acct("A", note="x"), _acct("B", note="y")
    res, _ = call(
        "update_accounts",
        updates=[{"account_id": a["id"]}, {"account_id": b["id"]}],
    )
    assert res["ok"] is True
    assert res["count"] == 0 and res["updated"] == []
    assert res["no_op_count"] == 2
    assert {r["id"] for r in res["no_op"]} == {a["id"], b["id"]}
    assert svc.accounts.find_by_id(a["id"])["note"] == "x"
    assert svc.accounts.find_by_id(b["id"])["note"] == "y"


def test_update_accounts_opening_balance_only_change_is_NOT_a_no_op(env):
    """The tool's ONE other effect besides the generic patch: an item with no other
    changeable field but `initial_balance` set is a REAL write (routes through the opening
    choke), not a no-op — this is the exact case rule 1 calls out."""
    a = _acct("A")
    res, _ = call("update_accounts", updates=[{"account_id": a["id"], "initial_balance": 500}])
    assert res["ok"] is True
    assert "no_op" not in res
    assert res["account"]["initial_balance"] == 500.0
    opening_id = svc.accounts.find_by_id(a["id"])["opening_txn_id"]
    assert opening_id is not None
    assert svc.transactions.find_by_id(opening_id) is not None


def test_update_accounts_a_real_single_field_edit_still_writes_normally(env):
    """Regression guard: the no-op guard must never fire for a genuine field change."""
    a = _acct("A", note="original")
    res, _ = call("update_accounts", updates=[{"account_id": a["id"], "note": "hi"}])
    assert res["ok"] is True
    assert "no_op" not in res
    assert res["account"]["note"] == "hi"
    assert svc.accounts.find_by_id(a["id"])["note"] == "hi"


# ── delete_accounts: batch ────────────────────────────────────────────────────────


def test_delete_accounts_batch_reports_refused_and_not_found_without_aborting(env):
    a = _acct("A")  # will be referenced by a transaction -> refused
    b = _acct("B")  # deletable
    _expense(a["id"])

    res, _ = call("delete_accounts", account_ids=[a["id"], b["id"], "ghost-id"])
    assert res["ok"] is True
    assert res["deleted"] == [b["id"]]
    assert res["count"] == 1
    assert len(res["refused"]) == 1
    assert res["refused"][0]["account_id"] == a["id"]
    assert "transaction" in res["refused"][0]["reason"]
    assert res["not_found"] == ["ghost-id"]
    # The refusal must not have deleted account A.
    assert svc.accounts.find_by_id(a["id"]) is not None
    assert svc.accounts.find_by_id(b["id"]) is None


def test_delete_accounts_single_id_keeps_the_pre_rename_shape(env):
    b = _acct("B")
    res, _ = call("delete_accounts", account_ids=[b["id"]])
    assert res["ok"] is True
    assert res["deleted"] == b["id"]  # a bare id, not a list


def test_delete_accounts_old_alias_scalar_shape_still_works(env):
    b = _acct("B")
    res, _ = call("delete_accounts", account_id=b["id"])
    assert res["ok"] is True
    assert res["deleted"] == b["id"]


# ── reconcile_accounts: batch ──────────────────────────────────────────────────────


def test_reconcile_accounts_batch_dry_run_reports_per_account_deltas(env):
    a = _acct("Checking")
    b = _acct("Savings")
    _expense(a["id"], amount=100.0, cleared=True)

    res, _ = call(
        "reconcile_accounts",
        accounts=[
            {"account_id": a["id"], "real_balance": -150.0},
            {"account_id": b["id"], "real_balance": 200.0},
        ],
        dry_run=True,
    )
    assert res["ok"] is True and res["dry_run"] is True
    assert res["count"] == 2
    by_id = {r["account_id"]: r for r in res["results"]}
    assert by_id[a["id"]]["delta"] == -50.0  # -100 (cleared) -> -150 asserted
    assert by_id[b["id"]]["delta"] == 200.0
    # dry_run never writes — still just the one seeded expense, no adjustment minted.
    assert [t["kind"] for t in svc.transactions.all()] == ["expense"]


def test_reconcile_accounts_batch_apply_writes_each_independently(env):
    a = _acct("Checking")
    b = _acct("Savings")

    res, _ = call(
        "reconcile_accounts",
        accounts=[
            {"account_id": a["id"], "real_balance": 300.0},
            {"account_id": b["id"], "real_balance": -20.0},
        ],
    )
    assert res["ok"] is True and res["dry_run"] is False
    assert res["count"] == 2
    a_acc = svc.accounts.find_by_id(a["id"])
    b_acc = svc.accounts.find_by_id(b["id"])
    txns = svc.transactions.all()
    assert svc.account_balance(a_acc, txns, svc.opening_date(a_acc, txns)) == 300.0
    assert svc.account_balance(b_acc, txns, svc.opening_date(b_acc, txns)) == -20.0


def test_reconcile_accounts_batch_reports_unresolved_ref_without_aborting(env):
    a = _acct("Checking")
    res, _ = call(
        "reconcile_accounts",
        accounts=[
            {"account_id": "ghost-id", "real_balance": 10.0},
            {"account_id": a["id"], "real_balance": 42.0},
        ],
        dry_run=True,
    )
    assert res["ok"] is True
    assert res["not_found"] == ["ghost-id"]
    assert res["count"] == 1
    assert res["results"][0]["account_id"] == a["id"]


def test_reconcile_accounts_batch_reports_missing_real_balance_without_aborting(env):
    a = _acct("Checking")
    b = _acct("Savings")
    res, _ = call(
        "reconcile_accounts",
        accounts=[
            {"account_id": a["id"]},  # missing real_balance
            {"account_id": b["id"], "real_balance": 42.0},
        ],
        dry_run=True,
    )
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["errors"] == [{"account_id": a["id"], "reason": "real_balance is required"}]
    assert res["results"][0]["account_id"] == b["id"]


def test_reconcile_accounts_single_form_keeps_the_legacy_shape(env):
    a = _acct("Checking")
    res, _ = call("reconcile_accounts", account_id=a["id"], real_balance=10.0, dry_run=True)
    assert res["ok"] is True and res["dry_run"] is True
    assert res["account_id"] == a["id"]
    assert "results" not in res


def test_reconcile_accounts_old_alias_still_works(env):
    a = _acct("Checking")
    res, _ = call("reconcile_accounts", account_id=a["id"], real_balance=10.0, dry_run=True)
    assert res["ok"] is True and res["dry_run"] is True
    assert res["account_id"] == a["id"]


# ── set_transactions_cleared: batch ───────────────────────────────────────────────


def test_set_transactions_cleared_batch_marks_several(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    t2 = _expense(a["id"])

    res, _ = call("set_transactions_cleared", txn_ids=[t1["id"], t2["id"]], cleared=True)
    assert res["ok"] is True
    assert res["count"] == 2
    assert {u["id"] for u in res["updated"]} == {t1["id"], t2["id"]}
    assert all(u["cleared"] is True for u in res["updated"])


def test_set_transactions_cleared_batch_refuses_starting_balance_without_aborting(env):
    a = _acct("A")
    a = svc.set_account_opening(a["id"], 100.0, "2026-01-01")
    opening_txn = svc.transactions.find_by_id(a["opening_txn_id"])
    t2 = _expense(a["id"])

    res, _ = call("set_transactions_cleared", txn_ids=[opening_txn["id"], t2["id"]], cleared=False)
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["updated"][0]["id"] == t2["id"] and res["updated"][0]["cleared"] is False
    assert len(res["refused"]) == 1
    assert res["refused"][0]["txn_id"] == opening_txn["id"]
    # The opening txn must still be cleared — the refusal actually held.
    assert svc.transactions.find_by_id(opening_txn["id"])["cleared"] is True


def test_set_transactions_cleared_single_id_keeps_the_pre_rename_shape(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    res, _ = call("set_transactions_cleared", txn_ids=[t1["id"]], cleared=True)
    assert res["ok"] is True
    assert res["transaction"]["id"] == t1["id"]
    assert "updated" not in res and "count" not in res


def test_set_transactions_cleared_old_alias_scalar_shape_still_works(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    res, _ = call("set_transactions_cleared", txn_id=t1["id"], cleared=True)
    assert res["ok"] is True
    assert res["transaction"]["id"] == t1["id"]


# ── trace_transactions: batch ─────────────────────────────────────────────────────


def test_trace_transactions_batch_returns_a_result_per_id(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    t2 = _expense(a["id"])

    res, _ = call("trace_transactions", transaction_ids=[t1["id"], t2["id"]])
    assert res["count"] == 2
    by_id = {r["txn_id"]: r for r in res["results"]}
    assert set(by_id) == {t1["id"], t2["id"]}
    assert all(r["events"][0]["op"] == "create" for r in by_id.values())


def test_trace_transactions_batch_over_cap_is_refused(env):
    a = _acct("A")
    ids = [_expense(a["id"])["id"] for _ in range(3)]
    res, _ = call("trace_transactions", transaction_ids=ids * 9)  # 27 > the 25 cap
    assert "error" in res and "25" in res["error"]


def test_trace_transactions_single_id_keeps_the_pre_rename_shape(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    res, _ = call("trace_transactions", transaction_ids=[t1["id"]])
    assert res["txn_id"] == t1["id"]
    assert "results" not in res


def test_trace_transactions_old_alias_scalar_shape_still_works(env):
    a = _acct("A")
    t1 = _expense(a["id"])
    res, _ = call("trace_transactions", transaction_id=t1["id"])
    assert res["txn_id"] == t1["id"]
    assert "results" not in res
