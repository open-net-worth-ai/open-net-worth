"""
Regression: unsplitting a split transaction must clear the top-level SPLIT sentinel.

The transaction detail drawer now edits split allocations (P3-SP UI). "Unsplit" sends
``splits: []`` plus a null ``category``. But the update PUT serialises with
``model_dump(exclude_none=True)``, which DROPS the null category — so ``update_transaction``
sees only ``{"splits": []}`` and ``merged`` keeps the old ``category == "split"`` sentinel.
Before the fix, ``_apply_splits`` returned early on empty ``splits`` and left that stale
sentinel in place, persisting a phantom "Split" with no lines behind it (the UI's
``isSplit`` check is ``category == 'split' AND splits.length > 0``, so it would render the
row as a broken bare "Split" category).

``_apply_splits`` now clears a bare SPLIT sentinel when there are no lines, so the row
falls back to Uncategorized. These tests pin both the unsplit-via-update path (the real
exclude_none scenario) and the direct create path.

Offline: in-memory transaction store; rules/corrections no-op'd; no disk, network, or LLM.
"""

from __future__ import annotations

import uuid

import pytest

from backend.models.transaction import SPLIT
from backend.services import ledger as svc

_FX = {"VND": 1.0, "USD": 1.0, "SGD": 1.0}


class _TxnStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def find_by_id(self, txn_id):
        return self._rows.get(txn_id)

    def by_external_ref(self, source, ref):
        from backend.core.txn_sources import ext_ref_pairs

        return next((r for r in self._rows.values() if (source, ref) in ext_ref_pairs(r)), None)

    def has_external_ref(self, source, ref):
        return self.by_external_ref(source, ref) is not None

    def insert(self, row, *, conn=None):
        return self.append(row, conn=conn)

    def append(self, row, *, conn=None):
        row = dict(row)
        if not row.get("id"):
            row["id"] = str(uuid.uuid4())
        self._rows[row["id"]] = row
        return row

    def delete(self, txn_id, *, conn=None):
        return self._rows.pop(txn_id, None) is not None

    def upsert(self, row, *, conn=None):
        self._rows[row["id"]] = row
        return row

    def transfer_routes(self):
        return frozenset(
            frozenset((t["from_account_id"], t["to_account_id"]))
            for t in self.all()
            if t.get("kind") == "transfer"
            and not t.get("deleted")
            and not t.get("merged_into")
            and t.get("from_account_id")
            and t.get("to_account_id")
        )

    def all(self):
        return list(self._rows.values())

    def query_txns(self, **_):
        return list(self._rows.values())

    def enabled_rules(self):
        return []


@pytest.fixture
def patched_svc(monkeypatch, db_isolated):
    """``svc.transactions`` is swapped for the in-memory fake below, but
    ``update_transaction`` (plan 3 task 4) still opens a REAL ``locked_unit_of_work``
    transaction (for the txn_audit write, and for the lock/transaction machinery
    itself) — db_isolated gives it a real, isolated database to do that against.

    ``seed_stub_accounts`` (PLAN 3 TASK 5, INCLUSION B ripple): every fixture here books
    against the synthetic ``"acc-bank"`` id, never a real ``AccountStore``-created row.
    ``update_transaction`` re-validates through ``create_transaction`` on every edit, and
    ``_validate_legs`` now existence-checks a single (non-transfer) leg too — the same
    check the transfer branch always had — so this stub keeps `svc.accounts` (the REAL,
    now-isolated store `_validate_legs` actually reads) able to resolve it, matching
    production wiring rather than papering over the check."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc-bank"}])
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    store = _TxnStore()
    monkeypatch.setattr(svc, "transactions", store)
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    import backend.services.corrections as corr

    monkeypatch.setattr(corr, "record_correction", lambda old, new: None)
    return store


def _seed_split(store: _TxnStore) -> dict:
    row = {
        "id": "txn-split-1",
        "kind": "expense",
        "date": "2026-07-10",
        "description": "Test split",
        "category": SPLIT,
        "from_account_id": "acc-bank",
        "from_amount": 50.0,
        "from_currency": "VND",
        "base_amount": 50.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "tags": [],
        "splits": [
            {"category": "cat_13095ba70000", "amount": 30.0, "note": ""},
            {"category": "cat_db5642ab0000", "amount": 20.0, "note": ""},
        ],
    }
    store._rows[row["id"]] = row
    return row


def test_unsplit_clears_stale_split_sentinel(patched_svc):
    """The exact update the route produces after exclude_none drops the null category:
    only ``{"splits": []}`` reaches the service. The row must NOT stay ``category=='split'``."""
    store = patched_svc
    _seed_split(store)

    result = svc.update_transaction("txn-split-1", {"splits": []})

    assert result is not None
    assert result["category"] != SPLIT, "a bare SPLIT sentinel with no lines must be cleared"
    assert result["category"] is None, (
        "unsplitting with no explicit category falls back to Uncategorized"
    )
    assert not result.get("splits"), "no split lines remain"
    stored = store.find_by_id("txn-split-1")
    assert stored["category"] is None


def test_unsplit_with_explicit_category_keeps_it(patched_svc):
    """If the user picks a real category while unsplitting, that category survives (it is not
    dropped by exclude_none), and the sentinel-clear must not stomp it."""
    store = patched_svc
    _seed_split(store)

    result = svc.update_transaction("txn-split-1", {"splits": [], "category": "cat_db5642ab0000"})

    assert result["category"] == "cat_db5642ab0000"
    assert not result.get("splits")


def test_create_bare_split_sentinel_without_lines_is_cleared(patched_svc):
    """Defensive: creating a row with the SPLIT sentinel but no lines must never persist the
    bare sentinel — it collapses to Uncategorized."""
    result = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-10",
            "category": SPLIT,
            "from_account_id": "acc-bank",
            "from_amount": 40.0,
            "from_currency": "VND",
            "splits": None,
        }
    )
    assert result["category"] is None
    assert not result.get("splits")


def test_real_split_still_stamps_sentinel(patched_svc, monkeypatch):
    """Guard against over-reach: a genuine ≥2-line split still stamps the SPLIT sentinel."""
    import backend.services.categories as cats

    # resolve_category_ref is offline-hostile (reads the category store); stub it to identity
    # for the two known ids so _validate_splits accepts the lines without disk.
    monkeypatch.setattr(cats, "resolve_category_ref", lambda ref, rows=None: ref or None)

    result = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-10",
            "from_account_id": "acc-bank",
            "from_amount": 50.0,
            "from_currency": "VND",
            "splits": [
                {"category": "cat_13095ba70000", "amount": 30.0, "note": ""},
                {"category": "cat_db5642ab0000", "amount": 20.0, "note": ""},
            ],
        }
    )
    assert result["category"] == SPLIT
    assert len(result["splits"]) == 2
