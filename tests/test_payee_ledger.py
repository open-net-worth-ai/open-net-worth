"""Choke-point integration for payees (P4-PAYEE phase 1).

Pins the precedence the whole feature rests on — explicit > rule > payee-default >
uncategorized — plus payee resolution, silent learning (on create AND on edit), and
the "transfers carry no payee" invariant. All stores point at tmp_path; base = SGD.
"""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import payee_registry


def _arrange_create(data, **kw):
    """Arrangement helper: this file's fixtures build books containing similar rows, and its
    subject is NOT duplicate detection. ``force=True`` is the sanctioned escape hatch for the
    pre-write duplicate guard (ADR 2026-08-01) so those fixtures still build."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store, e.g. categories — plan 4a task 2, payees —
    plan 4d task 2) to a fresh, empty database; reseed BUILTIN_CATEGORIES here
    (tests/conftest.py's shared autouse seed ran BEFORE db_isolated activated, so it landed
    in the old, now-orphaned default engine). payee_registry._store is a PayeeStore module
    singleton that resolves its engine lazily (payload_store.py's `_eng()`), so db_isolated's
    ONW_DATABASE_URL redirect already isolates it — no per-store monkeypatch needed any more."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    # Two SGD accounts for from/to legs.
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    svc.accounts.insert({"id": "acc_sav", "name": "Savings", "kind": "asset", "currency": "SGD"})
    return tmp_path


def _expense(desc="Anthropic, PBC", category=None, payee_id=None):
    return {
        "kind": "expense",
        "date": "2026-05-31",
        "description": desc,
        "category": category,
        "payee_id": payee_id,
        "from_account_id": "acc_chk",
        "from_amount": 11.5,
        "from_currency": "SGD",
    }


def test_create_resolves_and_stamps_payee_id(env):
    row = _arrange_create(_expense())
    assert row["payee_id"] is not None
    assert payee_registry._store.find_by_id(row["payee_id"]) is not None


def test_same_merchant_resolves_to_one_payee(env):
    a = _arrange_create(_expense("Anthropic, PBC #2321"))
    b = _arrange_create(_expense("ANTHROPIC PBC #9999"))
    assert a["payee_id"] == b["payee_id"]
    assert len(payee_registry._store.all()) == 1


def test_silent_learn_then_autofill(env):
    # First txn is explicitly categorized → payee learns it.
    first = _arrange_create(_expense(category="cat_f2ff09940000"))
    pid = first["payee_id"]
    assert (
        payee_registry._store.find_by_id(pid)["default_categories"]["expense"] == "cat_f2ff09940000"
    )
    # Second txn for the same merchant with NO category → auto-filled from the default.
    second = _arrange_create(_expense())
    assert second["category"] == "cat_f2ff09940000"


def test_explicit_category_wins_over_payee_default(env):
    _arrange_create(_expense(category="cat_f2ff09940000"))  # learn default
    row = _arrange_create(_expense(category="cat_13095ba70000"))  # explicit overrides
    assert row["category"] == "cat_13095ba70000"


def test_blank_falls_through_to_none(env):
    # New merchant, no rule, no default, no explicit category → None (unsorted).
    row = _arrange_create(_expense("Some New Shop"))
    assert row["category"] is None
    # …and None is NEVER learned as the payee default.
    assert not payee_registry._store.find_by_id(row["payee_id"]).get("default_categories")


def test_edit_category_teaches_payee(env):
    row = _arrange_create(_expense("Brand New Cafe"))  # → None (unsorted)
    pid = row["payee_id"]
    assert not payee_registry._store.find_by_id(pid).get("default_categories")
    # Editing the category re-runs the choke point → the payee learns it.
    svc.update_transaction(row["id"], {"category": "cat_13095ba70000"})
    assert (
        payee_registry._store.find_by_id(pid)["default_categories"]["expense"] == "cat_13095ba70000"
    )


def test_create_honors_explicit_payee_id(env):
    # Caller-supplied payee_id wins over description resolution — no second entity minted.
    p = payee_registry.create_payee("Green Smoothie Bar")
    row = _arrange_create(_expense("some unrelated bank gibberish", payee_id=p["id"]))
    assert row["payee_id"] == p["id"]
    assert len(payee_registry._store.all()) == 1


def test_create_rejects_unknown_payee_id(env):
    with pytest.raises(NotFoundError):
        _arrange_create(_expense(payee_id="pay_does_not_exist"))


def test_bulk_honors_explicit_payee_id(env):
    p = payee_registry.create_payee("Green Smoothie Bar")
    created, _ = svc.bulk_create_transactions([_expense("GRN SMTH 8821", payee_id=p["id"])])
    assert created[0]["payee_id"] == p["id"]
    assert len(payee_registry._store.all()) == 1


def test_bulk_rejects_unknown_payee_id(env):
    with pytest.raises(NotFoundError):
        svc.bulk_create_transactions([_expense(payee_id="pay_does_not_exist")])


def test_update_sets_explicit_payee_id(env):
    row = _arrange_create(_expense("Anthropic, PBC"))
    p = payee_registry.create_payee("Green Smoothie Bar")
    updated = svc.update_transaction(row["id"], {"payee_id": p["id"]})
    assert updated["payee_id"] == p["id"]


def test_update_rejects_unknown_payee_id(env):
    row = _arrange_create(_expense("Anthropic, PBC"))
    with pytest.raises(NotFoundError):
        svc.update_transaction(row["id"], {"payee_id": "pay_does_not_exist"})


def test_bulk_update_sets_payee_id(env):
    a = _arrange_create(_expense("GRN SMTH 8821"))
    b = _arrange_create(_expense("GREEN SMOOTHIE BAR PTE"))
    p = payee_registry.create_payee("Green Smoothie Bar")
    result = svc.bulk_update_transactions([a["id"], b["id"]], {"payee_id": p["id"]})
    assert result["count"] == 2
    assert svc.transactions.find_by_id(a["id"])["payee_id"] == p["id"]
    assert svc.transactions.find_by_id(b["id"])["payee_id"] == p["id"]


def test_bulk_update_payee_skips_transfer(env):
    t = _arrange_create(
        {
            "kind": "transfer",
            "date": "2026-05-31",
            "description": "Move to savings",
            "from_account_id": "acc_chk",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc_sav",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    p = payee_registry.create_payee("Green Smoothie Bar")
    result = svc.bulk_update_transactions([t["id"]], {"payee_id": p["id"]})
    assert result["count"] == 0
    assert result["skipped"] == [{"id": t["id"], "reason": "payee not allowed on this kind"}]
    assert svc.transactions.find_by_id(t["id"]).get("payee_id") is None


def _investment_buy():
    return _arrange_create(
        {
            "kind": "investment",
            "date": "2026-05-31",
            "description": "Share purchase",
            "from_account_id": "acc_chk",
            "from_amount": 500.0,
            "from_currency": "SGD",
        }
    )


def test_bulk_update_payee_sets_it_on_an_investment_row(env):
    # An investment row's payee is the broker (ADR 2026-08-25); a row booked without one must
    # be correctable — investment used to sit in _NO_PAYEE_KINDS beside transfer/adjustment.
    t = _investment_buy()
    p = payee_registry.create_payee("Vantage Brokerage")
    result = svc.bulk_update_transactions([t["id"]], {"payee_id": p["id"]})
    assert result["count"] == 1
    assert result["skipped"] == []
    assert svc.transactions.find_by_id(t["id"])["payee_id"] == p["id"]


def test_update_transaction_sets_the_payee_on_an_investment_row(env):
    t = _investment_buy()
    p = payee_registry.create_payee("Vantage Brokerage")
    svc.update_transaction(t["id"], {"payee_id": p["id"]})
    assert svc.transactions.find_by_id(t["id"])["payee_id"] == p["id"]


def test_bulk_update_rejects_unknown_payee_id_before_any_write(env):
    row = _arrange_create(_expense("Anthropic, PBC"))
    original_pid = row["payee_id"]
    with pytest.raises(NotFoundError):
        svc.bulk_update_transactions([row["id"]], {"payee_id": "pay_does_not_exist"})
    assert svc.transactions.find_by_id(row["id"])["payee_id"] == original_pid


def test_bulk_import_resolves_and_stamps_payee_id(env):
    # Regression: the bulk path (connector syncs, file/OFX imports, import_transactions)
    # must mint/attach payees exactly like create_transaction — it used to skip the
    # payee step entirely, stamping payee_id=null on every imported expense.
    created, _ = svc.bulk_create_transactions(
        [
            _expense("Payment for green smoothie"),
            _expense("PAYMENT FOR GREEN SMOOTHIE #88"),
            _expense("Anthropic, PBC"),
        ]
    )
    assert all(t["payee_id"] for t in created)
    assert created[0]["payee_id"] == created[1]["payee_id"]  # one entity per merchant
    assert created[0]["payee_id"] != created[2]["payee_id"]
    assert len(payee_registry._store.all()) == 2


def test_bulk_import_fills_category_from_payee_default(env):
    # Teach the payee a default via the single-create path, then bulk-import the same
    # merchant uncategorized → payee default fills it (rule < explicit precedence pinned
    # by the single-create tests; here we pin payee-default > uncategorized for bulk).
    _arrange_create(_expense("Green Smoothie Bar", category="cat_13095ba70000"))
    # force=True for the same reason _arrange_create uses it: the bulk row is DELIBERATELY the
    # same merchant as the row above (that repetition IS the test), so the pre-write duplicate
    # guard would refuse it. The subject here is payee-default category fill, not dedup.
    created, _ = svc.bulk_create_transactions(
        [_expense("Green Smoothie Bar")], allow_duplicate=True
    )
    assert created[0]["category"] == "cat_13095ba70000"


def test_bulk_import_transfer_has_no_payee(env):
    created, _ = svc.bulk_create_transactions(
        [
            {
                "kind": "transfer",
                "date": "2026-05-31",
                "description": "Transfer to savings",
                "from_account_id": "acc_chk",
                "from_amount": 100.0,
                "from_currency": "SGD",
                "to_account_id": "acc_sav",
                "to_amount": 100.0,
                "to_currency": "SGD",
            }
        ]
    )
    assert created[0].get("payee_id") is None
    assert payee_registry._store.all() == []


def test_transfer_has_no_payee(env):
    row = _arrange_create(
        {
            "kind": "transfer",
            "date": "2026-05-31",
            "description": "Transfer to savings",
            "from_account_id": "acc_chk",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc_sav",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    assert row.get("payee_id") is None
    assert payee_registry._store.all() == []  # no entity minted for a transfer


def test_reserved_payee_default_never_set_nor_filled(env):
    """Kind names can't become payee defaults — ``update_payee`` silently drops a reserved
    value from ``default_categories`` (the write-time guard), and even a directly-injected
    reserved value in the store is never auto-filled: the fill runs after the choke point's
    reserved-category guard, so leaking one would bypass it."""
    p = payee_registry.create_payee("Employer Pte Ltd")
    result = payee_registry.update_payee(p["id"], {"default_categories": {"expense": "income"}})
    assert result["default_categories"] == {}
    result = payee_registry.update_payee(p["id"], {"default_categories": {"expense": "transfer"}})
    assert result["default_categories"] == {}
    # Simulate corrupt/pre-guard data: a reserved default injected directly into the store,
    # bypassing update_payee's validation.
    payee_registry._store.set_default_category_for_kind(p["id"], "expense", "income")
    row = _arrange_create(_expense("Employer Pte Ltd", payee_id=p["id"]))
    assert row["category"] is None  # reserved key never used; no forced sink


# ── regression: a payee default naming a DELETED category must never fail a create ────
# Confirmed by a data audit on the owner's real book: 12 payees / 13 dead defaults. Before
# the fix, default_category_for trusted the stored value blindly — resolve_transaction_row
# then filled the blank category with that dangling id and raised "Unknown category",
# failing the whole create (and, in bulk_create_transactions, the whole batch).


def test_create_transaction_dead_payee_default_books_unsorted(env):
    from backend.services import categories as categories_service

    doomed = categories_service.create_category("Doomed Cat", "expense")
    pid = payee_registry.create_payee("Doomed Merchant")["id"]
    payee_registry._store.set_default_category_for_kind(pid, "expense", doomed["id"])
    categories_service.delete_category(doomed["id"])  # nothing references it — succeeds

    row = _arrange_create(_expense("Doomed Merchant", payee_id=pid))
    assert row["category"] is None  # Uncategorized, not a raised ValidationError


def test_bulk_create_transactions_dead_payee_default_does_not_fail_the_batch(env):
    doomed = payee_registry.create_payee("Doomed Merchant 2")
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Doomed Cat 2", "expense")
    payee_registry._store.set_default_category_for_kind(doomed["id"], "expense", cat["id"])
    categories_service.delete_category(cat["id"])

    created, skipped = svc.bulk_create_transactions(
        [
            _expense("Doomed Merchant 2", payee_id=doomed["id"]),
            _expense("Some New Shop 2"),
        ],
        allow_duplicate=True,
    )
    assert skipped == []
    assert len(created) == 2
    by_desc = {t["description"]: t for t in created}
    assert by_desc["Doomed Merchant 2"]["category"] is None
    assert by_desc["Doomed Merchant 2"]["payee_id"] == doomed["id"]
