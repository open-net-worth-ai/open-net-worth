"""
AI tool-dispatch tests for split transactions (P3-SP, slice 2) — UI↔Lucius parity.

Covers: ``add_transaction`` / ``add_transactions`` / ``update_transaction`` accept an
optional ``splits`` list, validated through the SAME slice-1 create_transaction choke point
(sum == leg ±0.01 REFUSED-not-rescaled, kind gate, valid categories); a split created via the
tool folds per-category in spending/planning (reusing slice-1 behaviour); the schema exposes
``splits`` on those tools; and ``get_transactions`` surfaces ``splits`` in its response shape.

Offline + isolated: stores/FX/profile monkeypatched into tmp_path; execute_tool is async,
driven via asyncio.run. No LLM, no network. Mirrors test_budgets_tools.py + test_split_transactions.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import budgets as budgets_service
from backend.services import ledger as svc
from backend.services import planning as planning

_FX = {"SGD": 1.0, "USD": 1.35}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of the ledger/budget/planning core + seed one account."""
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(budgets_service, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))

    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: dict(_FX))

    # profile is DB-backed now (Wave 5, JSON->DB) — budgets_service/categories_service both
    # read it through the same ProfileStore row, so one seed covers both.
    ProfileStore().set_raw({"base_currency": "SGD"})
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Meridian Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
            }
        ]
    )
    return tmp_path


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── schema parity ─────────────────────────────────────────────────────────────


def test_splits_schema_exposed_on_tools():
    by_name = {t["name"]: t for t in TOOL_SCHEMAS}
    # add_transaction + bulk_add_transactions merged into one plural tool, so the single and
    # bulk paths now share ONE item schema (and therefore one splits description) — the same
    # merge update_transaction + bulk_update_transactions later went through too.
    item = by_name["add_transactions"]["input_schema"]["properties"]["transactions"]["items"][
        "properties"
    ]
    upd = by_name["update_transactions"]["input_schema"]["properties"]["updates"]["items"][
        "properties"
    ]
    assert "splits" in item and "splits" in upd
    # the line shape requires category + amount
    assert set(item["splits"]["items"]["required"]) == {"category", "amount"}


# ── add_transaction: accepts + validates splits ──────────────────────────────────


def test_add_transaction_with_valid_split_stores_lines(env):
    res, proposed = call(
        "add_transactions",
        description="Store run",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 60.0},
            {"category": "cat_6c4bc0170000", "amount": 40.0},
        ],
    )
    assert res["ok"] is True and proposed is None
    t = res["transaction"]
    assert t["category"] == "split"
    assert [(s["category"], s["amount"]) for s in t["splits"]] == [
        ("cat_13095ba70000", 60.0),
        ("cat_6c4bc0170000", 40.0),
    ]


def test_split_line_memo_survives_add_and_update(env):
    """The schema advertises a per-line ``memo``; the add path read ``note`` instead (a leftover
    of the 2026-08-02 rename) and silently dropped every line memo Lucius wrote."""
    lines = [
        {"category": "cat_13095ba70000", "amount": 60.0, "memo": "Weekly shop"},
        {"category": "cat_6c4bc0170000", "amount": 40.0, "memo": "Detergent"},
    ]
    add, _ = call(
        "add_transactions",
        description="Store run",
        amount=100.0,
        currency="SGD",
        account_id="bank",
        splits=lines,
    )
    assert [s["memo"] for s in add["transaction"]["splits"]] == ["Weekly shop", "Detergent"]
    upd, _ = call(
        "update_transactions",
        transaction_id=add["transaction"]["id"],
        splits=[{**lines[0], "memo": "Fortnightly shop"}, lines[1]],
    )
    assert [s["memo"] for s in upd["transaction"]["splits"]] == ["Fortnightly shop", "Detergent"]


def test_split_preview_carries_line_memo():
    from backend.services.ledger_preview import _splits_block

    block = _splits_block(
        {"splits": [{"category": "cat_13095ba70000", "amount": 60.0, "memo": "Weekly shop"}]},
        [{"id": "cat_13095ba70000", "label": "Groceries", "kind": "expense"}],
    )
    assert block[0]["memo"] == "Weekly shop"


def test_add_transaction_split_sum_mismatch_refused(env):
    res, _ = call(
        "add_transactions",
        description="Bad split",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 60.0},
            {"category": "cat_6c4bc0170000", "amount": 30.0},
        ],
    )
    assert res["ok"] is False
    assert "sum" in res["error"].lower()
    # nothing persisted
    assert svc.transactions.all() == []


def test_add_transaction_split_never_rescaled(env):
    res, _ = call(
        "add_transactions",
        description="Over",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 50.0},
            {"category": "cat_6c4bc0170000", "amount": 60.0},
        ],
    )
    assert res["ok"] is False  # refused, not quietly scaled to 100
    assert svc.transactions.all() == []


def test_add_transaction_split_unknown_category_refused(env):
    res, _ = call(
        "add_transactions",
        description="Bad cat",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 60.0},
            {"category": "nope", "amount": 40.0},
        ],
    )
    assert res["ok"] is False
    assert "category" in res["error"].lower()


def test_add_transaction_one_line_split_normalised_to_plain(env):
    res, _ = call(
        "add_transactions",
        description="Single",
        amount=80.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[{"category": "cat_13095ba70000", "amount": 80.0}],
    )
    assert res["ok"] is True
    t = res["transaction"]
    assert t["category"] == "cat_13095ba70000" and t["splits"] is None


def test_transfer_with_splits_is_not_split(env):
    # An internal transfer (counterparty account) → kind=transfer; _txn_data_from_ai only attaches
    # splits to a final expense/income, and the choke-point kind gate rejects splits on a transfer
    # — so a transfer can never carry a split allocation.
    svc.accounts.insert(
        {
            "id": "cash",
            "name": "Savings",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
        }
    )
    res, _ = call(
        "add_transactions",
        description="move to savings",
        amount=100.0,
        currency="SGD",
        category="transfer",
        account_id="bank",
        to_account_id="cash",
        splits=[{"category": "cat_13095ba70000", "amount": 100.0}],
        force=True,
    )
    assert res["ok"] is True
    assert res["transaction"]["kind"] == "transfer"
    assert not res["transaction"].get("splits")  # never a split transfer


# ── the fold: a tool-created split attributes per category ────────────────────────


def test_tool_split_folds_per_category_in_spending_and_planning(env):
    res, _ = call(
        "add_transactions",
        date="2026-06-10",
        description="Store run",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 60.0},
            {"category": "cat_6c4bc0170000", "amount": 40.0},
        ],
    )
    assert res["ok"] is True
    txns = svc.transactions.all()
    spent = budgets_service._spent_by_category(txns, 2026, 6, "SGD", _FX)
    assert spent["cat_13095ba70000"] == pytest.approx(60.0)
    assert spent["cat_6c4bc0170000"] == pytest.approx(40.0)
    assert "split" not in spent  # sentinel never a category row
    # month total unchanged vs one $100 category
    assert sum(spent.values()) == pytest.approx(100.0)

    activity = planning._activity_by_category(txns, "2026-06", "SGD", _FX, frozenset({"bank"}))
    assert activity["cat_13095ba70000"] == pytest.approx(-60.0)
    assert activity["cat_6c4bc0170000"] == pytest.approx(-40.0)


# ── update_transaction: set + clear splits ───────────────────────────────────────


def test_update_transaction_sets_split(env):
    add, _ = call(
        "add_transactions",
        description="Groceries",
        amount=100.0,
        currency="SGD",
        category="cat_13095ba70000",
        account_id="bank",
    )
    tid = add["transaction"]["id"]
    res, _ = call(
        "update_transactions",
        transaction_id=tid,
        splits=[
            {"category": "cat_13095ba70000", "amount": 70.0},
            {"category": "cat_6c4bc0170000", "amount": 30.0},
        ],
    )
    assert res["ok"] is True
    t = res["transaction"]
    assert t["category"] == "split"
    assert [(s["category"], s["amount"]) for s in t["splits"]] == [
        ("cat_13095ba70000", 70.0),
        ("cat_6c4bc0170000", 30.0),
    ]


def test_update_transaction_clears_split_with_empty_list(env):
    add, _ = call(
        "add_transactions",
        description="Store run",
        amount=100.0,
        currency="SGD",
        category="cat_a6b4e3bc0000",
        account_id="bank",
        splits=[
            {"category": "cat_13095ba70000", "amount": 60.0},
            {"category": "cat_6c4bc0170000", "amount": 40.0},
        ],
    )
    tid = add["transaction"]["id"]
    assert add["transaction"]["category"] == "split"
    # Un-split: send a single category + an empty splits list to clear.
    res, _ = call(
        "update_transactions",
        transaction_id=tid,
        category="cat_13095ba70000",
        splits=[],
    )
    assert res["ok"] is True
    t = res["transaction"]
    assert t["category"] == "cat_13095ba70000"
    assert not t.get("splits")  # None or []


def test_update_transaction_bad_split_sum_refused(env):
    add, _ = call(
        "add_transactions",
        description="Groceries",
        amount=100.0,
        currency="SGD",
        category="cat_13095ba70000",
        account_id="bank",
    )
    tid = add["transaction"]["id"]
    res, _ = call(
        "update_transactions",
        transaction_id=tid,
        splits=[
            {"category": "cat_13095ba70000", "amount": 70.0},
            {"category": "cat_6c4bc0170000", "amount": 40.0},
        ],
    )
    assert res["ok"] is False  # 110 ≠ 100, refused
    # the txn is untouched (still a plain food txn)
    assert svc.transactions.find_by_id(tid)["category"] == "cat_13095ba70000"
