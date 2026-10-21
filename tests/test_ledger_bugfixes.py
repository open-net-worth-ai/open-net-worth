"""
Regression tests for confirmed bugs in the ledger/positions core:

  BUG-1  update_transaction silently drops all edits for txns with an external_ref.
         The idempotency guard in create_transaction matched the existing external_ref
         and returned the unchanged row, so every bank-imported / recurring /
         email-logged transaction was un-editable.

  BUG-4  update_transaction silently drops reconciled_at (and only reconciled_at)
         because make_transaction does not emit that field.  Editing any reconciled
         transaction lost its reconciled stamp.  Fix: pop + re-attach, mirroring the
         existing external_ref pattern.

  BUG-2  POST /api/transactions/bulk returned a malformed response because
         bulk_create_transactions() returns a (list, []) tuple but the API route
         assigned the whole tuple to `created`, giving len(created)==2 and a
         [[...],[]] transactions field.

  BUG-3  row_base_value used row["currency"] (= cost_currency) to FX-convert the
         market value of a holding, but qty×price×qpu is denominated in
         quote_currency.  When cost_currency != quote_currency (e.g. a HKD-quoted ETF
         held in a USD account), the wrong rate was applied, inflating net worth ~8×.

  BUG-5  The update_transaction AI tool silently dropped `amount`/`currency` fields
         (the add_transaction-style shorthand).  The tool's field extractor only
         whitelisted the explicit leg fields (to_amount/from_amount), so a model call
         like {transaction_id, amount: 2548.7, currency: "USD"} looked successful
         (ok:true) but left the stored amount unchanged — a silent data-integrity
         failure.  Fix: `"additionalProperties": False` on the tool schema so the API
         rejects the call before it reaches the handler; the model is forced to retry
         with the correct fields (to_amount/from_amount + to_currency/from_currency).

All tests are offline (monkeypatched stores; no network, no LLM, no live data files).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.errors import ValidationError
from backend.core.txn_sources import primary_ref, primary_source
from backend.db.account_store import AccountStore
from backend.db.category_store import CategoryStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import positions as positions_service  # noqa: E402

# ── FX helpers ───────────────────────────────────────────────────────────────────
# A simple VND-pivoted rate table: 1 USD = 23,000 VND; 1 HKD ≈ 2,948 VND.
# (real approximate cross-rate, chosen to be clearly distinct from USD)
_FX = {"VND": 1.0, "USD": 23_000.0, "HKD": 2_948.0, "SGD": 17_000.0}


# ── BUG-1: update_transaction must not discard edits for txns with external_ref ───


class _TxnStore:
    """Minimal in-memory transaction store that supports the methods update_transaction
    needs: find_by_id, by_external_ref, insert (via bulk_append backing for create),
    delete, upsert."""

    def __init__(self):
        self._rows: dict[str, dict] = {}

    def find_by_id(self, txn_id: str) -> dict | None:
        return self._rows.get(txn_id)

    def by_external_ref(self, source: str | None, ref: str | None) -> dict | None:
        from backend.core.txn_sources import ext_ref_pairs

        for r in self._rows.values():
            if (source, ref) in ext_ref_pairs(r):
                return r
        return None

    def has_external_ref(self, source: str | None, ref: str | None) -> bool:
        return self.by_external_ref(source, ref) is not None

    def insert(self, row: dict, *, conn=None) -> dict:
        return self.append(row)

    def append(self, row: dict, *, conn=None) -> dict:
        import uuid

        row = dict(row)
        if not row.get("id"):
            row["id"] = str(uuid.uuid4())
        self._rows[row["id"]] = row
        return row

    def delete(self, txn_id: str, *, conn=None) -> bool:
        return self._rows.pop(txn_id, None) is not None

    def upsert(self, row: dict, *, conn=None) -> dict:
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
    """Patch the module-level store instances in services.ledger so tests run offline.

    ``db_isolated`` + ``seed_stub_accounts`` (PLAN 3 TASK 5, INCLUSION B ripple): every
    fixture/seed helper in this file books against the synthetic ``"acc-bank"`` id — never a
    real ``AccountStore``-created row. That was harmless before this task (``_validate_legs``
    never existence-checked a single (non-transfer) leg), but the whole point of INCLUSION B
    is that it now does, the same way the transfer branch always has — so `update_transaction`
    (which re-validates through `create_transaction` on every edit) started refusing every
    test here with a clean `ValidationError` ("'acc-bank' does not exist") where it previously
    proceeded. `svc.transactions` stays the in-memory fake (unaffected — these tests are
    about update_transaction's field-preservation behavior, not storage); `svc.accounts` is
    intentionally left as the REAL (now isolated, real-row-backed) store, matching how
    `_validate_legs` actually reads it in production."""
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
    # Rules service: no-op (no disk read — we're testing update, not categorization).
    # Corrections service: no-op (no disk write)
    import backend.services.corrections as corr

    monkeypatch.setattr(corr, "record_correction", lambda old, new: None)
    return store


def _seed_txn(store: _TxnStore, external_id: str) -> dict:
    """Insert a minimal bank-imported expense into the in-memory store."""
    src, _, ref = external_id.partition(":")
    row = {
        "id": "txn-imported-1",
        "kind": "expense",
        "date": "2026-03-15",
        "description": "Grocery store",
        "category": "cat_13095ba70000",
        "from_account_id": "acc-bank",
        "from_amount": 50.0,
        "from_currency": "VND",
        "base_amount": 50.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "external_refs": [{"source": src or None, "ref": ref}],
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row
    return row


def test_update_transaction_edit_persists_with_external_ref(patched_svc):
    """Editing the category of a bank-imported txn (which has an external_ref) must
    PERSIST: the change must be visible on re-fetch.  Before the fix, create_transaction's
    idempotency guard returned the unmodified row → the edit was silently discarded."""
    store = patched_svc
    original = _seed_txn(store, "plaid:x:y")
    assert original["category"] == "cat_13095ba70000"

    result = svc.update_transaction("txn-imported-1", {"category": "cat_db5642ab0000"})

    assert result is not None, "update_transaction must return the updated row"
    assert result["category"] == "cat_db5642ab0000", (
        "category edit must persist for txns with external_ref"
    )
    # Verify the change is also durably reflected in the store.
    stored = store.find_by_id("txn-imported-1")
    assert stored is not None
    assert stored["category"] == "cat_db5642ab0000", "stored row must reflect the edit"


def test_update_transaction_external_ref_retained_after_edit(patched_svc):
    """After editing a bank-imported txn its external_ref must still be present so the
    dedup key is not lost (a future re-import must not create a duplicate row)."""
    store = patched_svc
    _seed_txn(store, "plaid:x:y")

    result = svc.update_transaction("txn-imported-1", {"category": "cat_db5642ab0000"})

    assert result is not None
    assert primary_source(result) == "plaid" and primary_ref(result) == "x:y", (
        "external_source/external_ref must be preserved on the rebuilt row after an edit"
    )
    stored = store.find_by_id("txn-imported-1")
    assert stored is not None
    assert primary_source(stored) == "plaid" and primary_ref(stored) == "x:y", (
        "stored row must retain external_source/external_ref"
    )


def test_update_transaction_refuses_kind_change_without_rewriting_its_leg(patched_svc):
    """A kind-only edit cannot turn an expense's debit leg into malformed income.

    Direction is represented by the leg itself, not merely ``kind``.  Before this
    guard, a tool could update just ``kind='income'`` and leave ``from_*`` in
    place, so the register and detail view disagreed about the transaction.
    """
    store = patched_svc
    original = _seed_txn(store, "email:incoming-paynow")

    with pytest.raises(ValidationError, match="income requires a destination leg"):
        svc.update_transaction(original["id"], {"kind": "income"})

    assert store.find_by_id(original["id"])["kind"] == "expense"
    assert store.find_by_id(original["id"])["from_amount"] == 50.0


def _seed_inverted_income(store: _TxnStore) -> dict:
    """The one live shape the 2026-09-13 leg guard did not migrate: ``kind=income`` with the
    money still on the FROM leg (an email-logged row created as an expense, then flipped to
    income by a kind-only update the day before that guard landed)."""
    row = {
        "id": "txn-inverted-income",
        "kind": "income",
        "date": "2026-09-12",
        "description": "DBS PayNow received SGD 20.00 from LEE SHU WEI",
        "category": None,
        "from_account_id": "acc-bank",
        "from_amount": 20.0,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "base_amount": 20.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "external_refs": [{"source": "email", "ref": "ddf6d306ccd3"}],
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row
    return row


def test_update_moving_income_onto_its_to_leg_keeps_an_orphaned_from_amount_unless_cleared(
    patched_svc,
):
    """Premise for the edit form's explicit clear: an omitted ``from_amount`` is "unchanged",
    and ``_normalise_leg`` keeps a bare amount beside a null account id — so moving the
    account alone leaves ``from_amount`` orphaned on a leg that no longer exists."""
    store = patched_svc
    row = _seed_inverted_income(store)

    svc.update_transaction(
        row["id"],
        {
            "kind": "income",
            "to_account_id": "acc-bank",
            "to_amount": 20.0,
            "to_currency": "SGD",
            "from_account_id": None,
        },
    )

    stored = store.find_by_id(row["id"])
    assert stored["to_account_id"] == "acc-bank" and stored["to_amount"] == 20.0
    assert stored["from_account_id"] is None
    assert stored["from_amount"] == 20.0, "the orphan the edit form must clear explicitly"


def test_update_moving_income_onto_its_to_leg_with_explicit_nulls_repairs_the_row(patched_svc):
    """The body the accounts tab's ``detailPatchToUpdate`` now sends for an income edit
    (owner report 2026-09-20): the emptied from-leg's amount/currency arrive as explicit
    ``None`` — the PUT's clearable list forwards them — and the stored row comes out as a
    clean to-leg-only income, no stale leg left behind."""
    store = patched_svc
    row = _seed_inverted_income(store)

    result = svc.update_transaction(
        row["id"],
        {
            "kind": "income",
            "to_account_id": "acc-bank",
            "to_amount": 20.0,
            "to_currency": "SGD",
            "from_account_id": None,
            "from_amount": None,
            "from_currency": None,
        },
    )

    assert result["kind"] == "income"
    stored = store.find_by_id(row["id"])
    assert stored["to_account_id"] == "acc-bank"
    assert stored["to_amount"] == 20.0
    assert stored["to_currency"] == "SGD"
    assert stored["from_account_id"] is None
    assert stored["from_amount"] is None
    assert stored["from_currency"] is None
    assert stored["external_refs"] == [{"source": "email", "ref": "ddf6d306ccd3"}]


def test_create_transaction_refuses_negative_leg_amount(patched_svc):
    """Leg amounts are unsigned magnitudes; the leg/kind carries direction."""
    with pytest.raises(ValidationError, match="from_amount must be zero or greater"):
        svc.create_transaction(
            {
                "date": "2026-03-15",
                "kind": "expense",
                "description": "Negative input must not be normalised silently",
                "from_account_id": "acc-bank",
                "from_amount": -20,
                "from_currency": "VND",
            }
        )


def test_update_transaction_external_refs_are_appended_idempotently(patched_svc, monkeypatch):
    """An update may attach newly-discovered provenance without replacing the origin pair."""
    store = patched_svc
    _seed_txn(store, "email:meridian-alert")
    promoted: list[set] = []
    monkeypatch.setattr(svc, "_promote_pairs", lambda pairs: promoted.append(pairs))

    patch = {
        "external_refs": [
            {"source": "email", "ref": "meridian-alert"},
            {"source": "email", "ref": "grab-receipt"},
            {"source": "email", "ref": "grab-receipt"},
        ]
    }
    result = svc.update_transaction("txn-imported-1", patch)

    assert result is not None
    assert result["external_refs"] == [
        {"source": "email", "ref": "meridian-alert"},
        {"source": "email", "ref": "grab-receipt"},
    ]
    assert store.find_by_id("txn-imported-1")["external_refs"] == result["external_refs"]
    # ONLY the newly appended pair promotes — the pre-existing "meridian-alert" pair was
    # already promoted at its own creation time (RFC connectors-as-data-sources §C). The
    # inner rebuild's own create_transaction call fires the SAME hook first, but with an
    # empty set (external_refs is stripped before that inner call) — a harmless no-op.
    assert promoted[-1] == {("email", "grab-receipt")}
    assert all(pairs <= {("email", "grab-receipt")} for pairs in promoted)


def test_update_transaction_without_external_ref_still_works(patched_svc):
    """Manually-created txns (no external_ref) must also update correctly — the fix
    must not regress the common case."""
    store = patched_svc
    row = {
        "id": "txn-manual-1",
        "kind": "expense",
        "date": "2026-04-01",
        "description": "Coffee",
        "category": "cat_13095ba70000",
        "from_account_id": "acc-bank",
        "from_amount": 5.0,
        "from_currency": "VND",
        "base_amount": 5.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row

    result = svc.update_transaction("txn-manual-1", {"description": "Latte"})

    assert result is not None
    assert result["description"] == "Latte"
    assert primary_source(result) is None  # no external_ref on manual txns
    assert primary_ref(result) is None


# ── MATH-5: moving a txn to a different-currency account must re-stamp fx_rate ────
#
# `_leg_changed` compared the PRE-resolution merged dict, so an edit that only swaps
# `from_account_id` read as "leg unchanged" even though `_normalise_leg` re-expresses
# the leg's amount in the new account's currency. `base_amount` must stay locked
# (historical-rate lock); `fx_rate` must stay consistent with the row's own numbers.

_FX_SGD_EUR = {**_FX, "SGD": 17_000.0, "EUR": 25_000.0}  # 1 EUR ≈ 1.47 SGD (~0.68 SGD→EUR)


def _seed_sgd_to_eur_move_txn(store: _TxnStore) -> dict:
    row = {
        "id": "txn-move-1",
        "kind": "expense",
        "date": "2026-03-15",
        "description": "Dinner",
        "category": "cat_13095ba70000",
        "from_account_id": "acc-bank",  # seeded as currency="SGD" by seed_stub_accounts
        "from_amount": 135.0,
        "from_currency": "SGD",
        "base_amount": 2_295_000.0,  # 135 * 17,000 (VND base)
        "base_currency": "VND",
        "fx_rate": 17_000.0,
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row
    return row


def test_update_transaction_currency_account_move_restamps_fx_rate(patched_svc, monkeypatch):
    """Probe: a 135 SGD expense moved to a EUR account. `base_amount` must not move
    (the historical-rate lock); `fx_rate` must become consistent with the row's own
    (freshly resolved) from_amount, not the stale pre-move rate."""
    import backend.services.fx_cache as fx_mod
    from backend.db.account_store import AccountStore

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX_SGD_EUR))
    AccountStore().create(
        {"id": "acc-eur", "name": "acc-eur", "kind": "asset", "type": "cash", "currency": "EUR"}
    )
    store = patched_svc
    _seed_sgd_to_eur_move_txn(store)

    result = svc.update_transaction("txn-move-1", {"from_account_id": "acc-eur"})

    assert result["from_currency"] == "EUR"
    # 135 SGD -> EUR at rate 17,000/25,000
    assert result["from_amount"] == pytest.approx(135.0 * 17_000.0 / 25_000.0, abs=0.01)
    assert result["base_amount"] == pytest.approx(2_295_000.0, abs=0.01), (
        "base_amount is locked — an account re-label must not move it"
    )
    assert result["fx_rate"] != pytest.approx(17_000.0), "the stale pre-move rate must not survive"
    assert result["fx_rate"] == pytest.approx(
        result["base_amount"] / result["from_amount"], rel=1e-4
    ), "fx_rate must stay consistent with the row's own base_amount/from_amount"


def test_update_transaction_amount_edit_still_recomputes_base(patched_svc, monkeypatch):
    """Companion: an edit that DOES change the native amount (same account/currency)
    must still recompute base_amount/fx_rate at the current rate — MATH-5's fix must
    not disable that existing behavior."""
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX_SGD_EUR))
    store = patched_svc
    _seed_sgd_to_eur_move_txn(store)

    result = svc.update_transaction("txn-move-1", {"from_amount": 200.0})

    assert result["from_currency"] == "SGD"
    assert result["from_amount"] == pytest.approx(200.0)
    assert result["base_amount"] == pytest.approx(200.0 * 17_000.0, abs=0.01)
    assert result["fx_rate"] == pytest.approx(17_000.0, rel=1e-4)


def test_update_transaction_memo_edit_preserves_leg_and_base(patched_svc, monkeypatch):
    """Companion: an edit that touches neither the leg nor the account must preserve
    from_amount/from_currency/base_amount/fx_rate byte-identical."""
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX_SGD_EUR))
    store = patched_svc
    _seed_sgd_to_eur_move_txn(store)

    result = svc.update_transaction("txn-move-1", {"description": "Dinner (updated)"})

    assert result["description"] == "Dinner (updated)"
    assert result["from_currency"] == "SGD"
    assert result["from_amount"] == pytest.approx(135.0)
    assert result["base_amount"] == pytest.approx(2_295_000.0, abs=0.01)
    assert result["fx_rate"] == pytest.approx(17_000.0, rel=1e-4)


# ── BUG-2: POST /api/transactions/bulk must return a FLAT list, not a nested tuple ─


@pytest.fixture
def bulk_client(monkeypatch, db_isolated):
    """Minimal app + client for the bulk-add endpoint, with ledger stores isolated to a
    fresh, empty database (db_isolated — accounts/transactions/import_batches/statements
    are DB-backed now, plan 3 task 4)."""
    import backend.services.fx_cache as fx_mod
    from backend.api.ledger import router as ledger_router
    from tests.conftest import seed_stub_accounts

    # Every payload below books against this synthetic account id.
    seed_stub_accounts([{"from_account_id": "acc-1"}])
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def test_bulk_add_returns_correct_count(bulk_client):
    """POST /api/transactions/bulk must return created == len(transactions), not 2."""
    payload = {
        "transactions": [
            {
                "date": "2026-05-01",
                "kind": "expense",
                "description": "Lunch",
                "category": "cat_13095ba70000",
                "from_account_id": "acc-1",
                "from_amount": 10.0,
                "from_currency": "VND",
            },
            {
                "date": "2026-05-02",
                "kind": "expense",
                "description": "Bus",
                "category": "cat_db5642ab0000",
                "from_account_id": "acc-1",
                "from_amount": 3.0,
                "from_currency": "VND",
            },
        ]
    }
    r = bulk_client.post("/api/transactions/bulk", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    # Before the fix len(created)==2 because it was a 2-tuple, not the transaction list.
    assert body["created"] == 2, f"expected created==2 (one per row), got {body['created']!r}"
    # transactions must be a flat list of dicts, not [[...], []].
    assert isinstance(body["transactions"], list), "transactions must be a list"
    assert len(body["transactions"]) == 2, "transactions list must contain both rows"
    assert isinstance(body["transactions"][0], dict), (
        "each element of transactions must be a dict (row), not a nested list"
    )


def test_bulk_add_single_txn_returns_flat_list(bulk_client):
    """Even for a 1-item batch the response shape must be a flat list."""
    payload = {
        "transactions": [
            {
                "date": "2026-06-01",
                "kind": "income",
                "description": "Salary",
                "category": None,
                "to_account_id": "acc-1",
                "to_amount": 5000.0,
                "to_currency": "VND",
            }
        ]
    }
    r = bulk_client.post("/api/transactions/bulk", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert isinstance(body["transactions"], list)
    assert len(body["transactions"]) == 1
    assert isinstance(body["transactions"][0], dict)


def test_api_rejects_income_with_a_source_leg(bulk_client):
    """The REST boundary returns a useful 422 instead of persisting a mismatched row."""
    response = bulk_client.post(
        "/api/transactions",
        json={
            "date": "2026-06-01",
            "kind": "income",
            "description": "Malformed incoming payment",
            "from_account_id": "acc-1",
            "from_amount": 20,
            "from_currency": "VND",
        },
    )

    assert response.status_code == 422
    assert "income requires a destination leg" in response.json()["detail"]


def test_bulk_add_refused_rows_are_folded_into_skipped_details(bulk_client):
    """Security-review fix: POST /api/transactions/bulk ignored `refused` (the strong-
    duplicate guard's own skip list) entirely — a row it refused came back as
    created=0/skipped=0/skipped_details=[], so the attach-refs hint on `_duplicate_reason`
    never reached a REST caller. A hand-typed row plus a colliding bulk row (same account/
    amount/description) carrying a connector ref must be reported, naming both the existing
    row and add_transaction_refs; `force=true` then books it."""
    hand_typed = bulk_client.post(
        "/api/transactions",
        json={
            "date": "2026-05-01",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": "acc-1",
            "from_amount": 100_000.0,
            "from_currency": "VND",
        },
    ).json()

    colliding_payload = {
        "transactions": [
            {
                "date": "2026-05-01",
                "kind": "expense",
                "description": "Coffee",
                "from_account_id": "acc-1",
                "from_amount": 100_000.0,
                "from_currency": "VND",
                "external_refs": [{"source": "plaid", "ref": "t1"}],
            }
        ]
    }
    r = bulk_client.post("/api/transactions/bulk", json=colliding_payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 0
    assert body["skipped"] == 1
    assert len(body["skipped_details"]) == 1
    detail = body["skipped_details"][0]
    assert "add_transaction_refs" in detail["reason"]
    assert hand_typed["id"] in detail["reason"]
    assert detail.get("duplicate_of") == hand_typed["id"]
    assert detail["external_refs"] == [{"source": "plaid", "ref": "t1"}]

    forced_payload = {**colliding_payload, "force": True}
    r2 = bulk_client.post("/api/transactions/bulk", json=forced_payload)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["created"] == 1


# ── BUG-3: row_base_value must use quote_currency for the FX conversion ────────────


@pytest.fixture
def patched_positions(monkeypatch):
    """Patch positions_service.hydrated with a foreign-quoted holding."""
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))

    def _hydrated():
        return [
            {
                "asset_class": "equity",
                "quantity": 100.0,
                "price": 10.0,  # price in HKD
                "quote_per_unit": 1.0,
                "quote_currency": "HKD",  # quoted in HKD
                "currency": "USD",  # cost_currency: bought with USD
            }
        ]

    monkeypatch.setattr(positions_service, "hydrated", _hydrated)
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")


@pytest.fixture
def fx_patched(monkeypatch):
    """Patch the FX loader so row_base_value / to_base(rates=None) uses _FX.

    Two-step: monkeypatch fx_cache.load_fx_cache (picked up by the module-level
    lambda) AND directly re-register the loader via set_fx_rates_loader so the
    injected function definitely resolves to our table.  The conftest autouse
    fixture saves/restores the loader so no cross-test pollution."""
    import backend.services.fx_cache as fx_mod
    from backend.core.fx import set_fx_rates_loader

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    set_fx_rates_loader(lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")


def test_row_base_value_uses_quote_currency_not_cost_currency(fx_patched):
    """qty × price × qpu is in quote_currency.  When quote_currency != cost_currency
    the FX conversion must use quote_currency, not cost_currency (the "currency" field).

    100 shares × 10 HKD/share = 1000 HKD.
    Expected base: 1000 HKD × 2948 VND/HKD = 2,948,000 VND.
    With the bug (USD rate applied): 1000 × 23,000 = 23,000,000 VND — ~8× wrong."""
    from backend.core.fx import to_base

    row = {
        "asset_class": "equity",
        "quantity": 100.0,
        "price": 10.0,
        "quote_per_unit": 1.0,
        "quote_currency": "HKD",
        "currency": "USD",  # cost_currency — must NOT be used for price FX
    }

    hkd_value = 100.0 * 10.0 * 1.0  # = 1000 HKD
    expected_vnd = to_base(hkd_value, "HKD", "VND", rates=_FX)  # correct: ~2,948,000
    buggy_vnd = to_base(hkd_value, "USD", "VND", rates=_FX)  # old wrong: ~23,000,000

    # Sanity: the two paths differ enough to make the regression detectable.
    assert expected_vnd == pytest.approx(2_948_000.0, rel=1e-3)
    assert buggy_vnd == pytest.approx(23_000_000.0, rel=1e-3)
    assert buggy_vnd / expected_vnd > 4.0

    result = svc.row_base_value(row, "VND")

    assert result == pytest.approx(expected_vnd, rel=1e-4), (
        f"row_base_value={result:.2f} should be ~{expected_vnd:.2f} (HKD→VND), "
        f"not ~{buggy_vnd:.2f} (USD→VND)"
    )
    assert result != pytest.approx(buggy_vnd, rel=0.01), (
        "row_base_value must NOT use cost_currency (USD) for the FX conversion"
    )


def test_row_base_value_fallback_when_no_quote_currency(fx_patched):
    """When quote_currency is absent (e.g. synthetic net-worth-history rows) the
    function must fall back to the legacy 'currency' field — no regression for
    callers that never set quote_currency."""
    from backend.core.fx import to_base

    row = {
        "asset_class": "equity",
        "quantity": 10.0,
        "price": 1000.0,
        "quote_per_unit": 1.0,
        "currency": "USD",  # no quote_currency key
    }
    result = svc.row_base_value(row, "VND")

    expected_vnd = to_base(10.0 * 1000.0 * 1.0, "USD", "VND", rates=_FX)
    assert result == pytest.approx(expected_vnd, rel=1e-4)


def test_quote_currency_present_in_hydrated_rows():
    """positions_service.hydrated() must include a 'quote_currency' key on every
    row so row_base_value can use the correct currency for FX conversion."""
    from unittest.mock import patch

    instruments = [
        {
            "id": "inst-hk",
            "symbol": "0700.HK",
            "name": "Tencent",
            "asset_class": "equity",
            "quote_currency": "HKD",
            "price_source": "yahoo",
            "price_source_id": "0700.HK",
            "quote_per_unit": 1.0,
        }
    ]
    positions_data = [
        {
            "id": "pos-hk",
            "instrument_id": "inst-hk",
            "account_id": "acc-usd",
            "quantity": 50.0,
            "cost_basis": 1000.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    ]
    accounts = [{"id": "acc-usd", "name": "USD Brokerage", "currency": "USD"}]

    with (
        patch.object(PositionStore, "all", return_value=positions_data),
        patch.object(InstrumentStore, "all", return_value=instruments),
        patch.object(AccountStore, "all", return_value=accounts),
        patch.object(PriceHistoryStore, "latest_map", return_value={"0700.HK": 380.0}),
        patch.object(PriceHistoryStore, "prior_to_map", return_value={}),
    ):
        rows = positions_service.hydrated()

    assert len(rows) == 1
    row = rows[0]
    assert "quote_currency" in row, "hydrated rows must include quote_currency"
    assert row["quote_currency"] == "HKD"
    # currency is cost_currency — must differ from quote_currency for this holding.
    assert row["currency"] == "USD"
    assert row["currency"] != row["quote_currency"]


# ── BUG-4: update_transaction must preserve reconciled_at on reconciled txns ────────


def _seed_reconciled_txn(store: _TxnStore) -> dict:
    """Insert a minimal reconciled expense into the in-memory store."""
    row = {
        "id": "txn-reconciled-1",
        "kind": "expense",
        "date": "2026-01-10",
        "description": "Supermarket",
        "category": "cat_13095ba70000",
        "from_account_id": "acc-bank",
        "from_amount": 100.0,
        "from_currency": "VND",
        "base_amount": 100.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "cleared": True,
        "reconciled_at": "2026-01-31",
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row
    return row


def test_update_reconciled_txn_preserves_reconciled_at(patched_svc):
    """Editing a reconciled transaction (cleared=True, reconciled_at set) must not drop
    reconciled_at.  Before the fix, make_transaction omitted reconciled_at so the stamp
    was silently erased on every edit of a reconciled row."""
    store = patched_svc
    original = _seed_reconciled_txn(store)
    assert original["reconciled_at"] == "2026-01-31"

    result = svc.update_transaction("txn-reconciled-1", {"category": "cat_db5642ab0000"})

    assert result is not None, "update_transaction must return the updated row"
    assert result["category"] == "cat_db5642ab0000", "category edit must persist"
    assert result.get("reconciled_at") == "2026-01-31", (
        "reconciled_at must be preserved after editing a reconciled transaction"
    )
    stored = store.find_by_id("txn-reconciled-1")
    assert stored is not None
    assert stored.get("reconciled_at") == "2026-01-31", (
        "stored row must retain reconciled_at after edit"
    )


def test_update_reconciled_txn_clears_reconciled_at_when_explicitly_set(patched_svc):
    """If the caller explicitly passes reconciled_at=None in changes (e.g. un-reconciling),
    the value must be cleared — the preserve logic must not override an explicit caller intent."""
    store = patched_svc
    _seed_reconciled_txn(store)

    result = svc.update_transaction("txn-reconciled-1", {"reconciled_at": None})

    assert result is not None
    # The caller explicitly cleared reconciled_at — that intent must be honoured.
    assert result.get("reconciled_at") is None, (
        "explicit reconciled_at=None in changes must be respected (not overridden by preserve)"
    )


def test_update_cleared_txn_preserves_cleared_flag(patched_svc):
    """cleared=True must round-trip through an edit (make_transaction emits cleared, so
    this is a guard against regression, not a new fix)."""
    store = patched_svc
    _seed_reconciled_txn(store)  # cleared=True in seed

    result = svc.update_transaction("txn-reconciled-1", {"description": "Updated desc"})

    assert result is not None
    assert result.get("cleared") is True, "cleared flag must be preserved through an edit"


def test_update_txn_with_no_reconciled_at_is_unaffected(patched_svc):
    """A plain unreconciled transaction (no reconciled_at) must be unaffected by the
    preserve logic — it must not gain a spurious reconciled_at key after an edit."""
    store = patched_svc
    row = {
        "id": "txn-plain-1",
        "kind": "expense",
        "date": "2026-02-01",
        "description": "Coffee",
        "category": "cat_13095ba70000",
        "from_account_id": "acc-bank",
        "from_amount": 5.0,
        "from_currency": "VND",
        "base_amount": 5.0,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "cleared": False,
        "tags": [],
        "splits": None,
    }
    store._rows[row["id"]] = row

    result = svc.update_transaction("txn-plain-1", {"description": "Latte"})

    assert result is not None
    assert result["description"] == "Latte"
    assert result.get("reconciled_at") is None, (
        "a plain txn with no reconciled_at must not gain one after an edit"
    )


# ── BUG-5: update_transaction schema must declare additionalProperties: false ────


def test_update_transaction_schema_rejects_extra_fields():
    """BUG-5 regression: the update_transaction tool schema must carry
    additionalProperties:false so the Anthropic API rejects any call that includes
    undeclared fields (e.g. the add_transaction-style `amount`/`currency` shorthand).
    This is a schema-contract test — it does not invoke the live API."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "update_transactions")
    assert schema["input_schema"].get("additionalProperties") is False, (
        "update_transactions input_schema must have additionalProperties:false so the API "
        "rejects undeclared fields like `amount`/`currency` before they reach the handler"
    )


def test_update_transaction_schema_exposes_external_refs():
    """Lucius must be able to repair provenance without deleting and recreating a row."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "update_transactions")
    item = schema["input_schema"]["properties"]["updates"]["items"]
    assert "external_refs" in item["properties"]
    assert item["properties"]["external_refs"]["items"]["required"] == ["source", "ref"]


def test_update_reports_ANY_field_the_write_declined_not_just_known_ones(patched_svc):
    """A write that did not do what it was asked must not report success — checked GENERICALLY.

    Regression, re-raised once after an incomplete first fix. Repairing a mis-attached
    provenance ref, a caller sent the list they wanted the row to END UP with.
    ``resolve_external_refs_patch`` is append-only — it seeds from the stored refs and only
    adds — so a SHORTER list was a no-op that still returned a normally-updated row. It
    reported "written", changed nothing on disk, and only a re-read caught it. The
    unresolved consequence: ``email:aa11bb22cc33`` can sit on both the Compass payout it
    describes and a Meridian Bank funding row it does not, so ``by_external_ref`` is
    ambiguous for that identity.

    The first fix was an ``external_refs``-shaped guard. That was the wrong shape: it does not
    scale, and the next append-only or read-only field ships the same bug unnoticed. So the
    question is asked of EVERY patched field instead — *you asked me to set this, did it move?*
    — which is why this test drives `external_refs` but asserts on the generic report."""
    _seed_txn(patched_svc, "email:meridian-alert")
    patched_svc.find_by_id("txn-imported-1")["external_refs"].append(
        {"source": "email", "ref": "stray-compass"}
    )

    meta: dict = {}
    svc.update_transaction(
        "txn-imported-1",
        {"external_refs": [{"source": "email", "ref": "meridian-alert"}]},
        meta_out=meta,
    )

    [declined] = meta["declined_fields"]
    assert declined["field"] == "external_refs"
    assert [r["ref"] for r in declined["stored"]] == ["meridian-alert", "stray-compass"]
    stored = patched_svc.find_by_id("txn-imported-1")
    assert [r["ref"] for r in stored["external_refs"]] == ["meridian-alert", "stray-compass"]


def test_a_field_that_DID_apply_is_not_reported_as_declined(patched_svc):
    """The report must stay quiet on a normal edit, or it is noise nobody reads.

    Note what makes this work without per-field normalisation: the comparison is against the
    STORED before/after, so a value the write TRANSFORMS (a category label landing as
    ``cat_<hex>``) still reads as applied — it moved."""
    _seed_txn(patched_svc, "email:meridian-alert")

    meta: dict = {}
    svc.update_transaction("txn-imported-1", {"description": "Corrected"}, meta_out=meta)

    assert meta["declined_fields"] == []
    assert patched_svc.find_by_id("txn-imported-1")["description"] == "Corrected"


def test_update_transaction_tool_appends_external_refs(patched_svc, monkeypatch):
    """The advertised Lucius field reaches the ledger write choke instead of being dropped."""
    from backend.ai.tools import execute_tool

    _seed_txn(patched_svc, "email:meridian-alert")
    monkeypatch.setattr(svc, "_promote_pairs", lambda _pairs: None)

    text, proposed = asyncio.run(
        execute_tool(
            "update_transactions",
            {
                "updates": [
                    {
                        "transaction_id": "txn-imported-1",
                        "external_refs": [{"source": "email", "ref": "grab-receipt"}],
                    }
                ]
            },
        )
    )

    assert proposed is None
    assert json.loads(text)["ok"] is True
    assert patched_svc.find_by_id("txn-imported-1")["external_refs"] == [
        {"source": "email", "ref": "meridian-alert"},
        {"source": "email", "ref": "grab-receipt"},
    ]


def test_update_transaction_api_accepts_external_refs(bulk_client, monkeypatch):
    """The REST update contract exposes the same additive provenance patch as Lucius."""
    existing = {
        "id": "txn-api-provenance",
        "date": "2026-07-23",
        "kind": "expense",
        "description": "GRAB",
        "from_account_id": "acc-1",
        "from_amount": 12.5,
        "from_currency": "VND",
        "base_amount": 12.5,
        "base_currency": "VND",
        "fx_rate": 1.0,
        "external_refs": [{"source": "email", "ref": "meridian-alert"}],
        "tags": [],
        "splits": None,
    }
    svc.transactions.append(existing)
    monkeypatch.setattr(svc, "_promote_pairs", lambda _pairs: None)

    response = bulk_client.put(
        "/api/transactions/txn-api-provenance",
        json={"external_refs": [{"source": "email", "ref": "grab-receipt"}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["external_refs"] == [
        {"source": "email", "ref": "meridian-alert"},
        {"source": "email", "ref": "grab-receipt"},
    ]


# ── MATH-7: GET /api/ledger/summary must delegate to services.ledger.get_ledger_summary ─
# instead of re-implementing the fold inline (backend/api/ledger.py duplicated the service's
# own kind-bucketing/txn_base_amount loop byte-for-byte).


@pytest.fixture
def summary_client(monkeypatch, db_isolated):
    """App + client for the ledger-summary endpoint, isolated DB, VND base with known FX
    (mirrors bulk_client's setup). Patches ``load_fx_cache`` both where fx_cache defines it
    and where api/ledger imports it by name, so pre-fix (module-level import) and post-fix
    (service's per-call import) code paths both resolve to the same synthetic rates."""
    import backend.api.ledger as ledger_api
    import backend.services.fx_cache as fx_mod
    from backend.api.ledger import router as ledger_router
    from backend.db.account_store import AccountStore

    # Accounts denominated in the base currency (VND) so only the USD expense leg below
    # exercises an actual FX conversion — seed_stub_accounts' SGD default would muddy that.
    acc_store = AccountStore()
    for acc_id in ("acc-checking", "acc-savings"):
        acc_store.create(
            {"id": acc_id, "name": acc_id, "kind": "asset", "type": "cash", "currency": "VND"}
        )
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(ledger_api, "load_fx_cache", lambda: dict(_FX))
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _seed_summary_month(client):
    """Income + expense + transfer + foreign-currency expense, all dated 2026-06."""
    payload = {
        "transactions": [
            {
                "date": "2026-06-03",
                "kind": "income",
                "description": "Paycheck",
                "category": "cat_13095ba70000",
                "to_account_id": "acc-checking",
                "to_amount": 5_000_000.0,
                "to_currency": "VND",
            },
            {
                "date": "2026-06-05",
                "kind": "expense",
                "description": "Groceries",
                "category": "cat_13095ba70000",
                "from_account_id": "acc-checking",
                "from_amount": 400_000.0,
                "from_currency": "VND",
            },
            {
                "date": "2026-06-10",
                "kind": "transfer",
                "description": "To savings",
                "from_account_id": "acc-checking",
                "to_account_id": "acc-savings",
                "from_amount": 1_000_000.0,
                "from_currency": "VND",
                "to_amount": 1_000_000.0,
                "to_currency": "VND",
            },
            {
                "date": "2026-06-15",
                "kind": "expense",
                "description": "Software subscription",
                "category": "cat_13095ba70000",
                "from_account_id": "acc-checking",
                "from_amount": 20.0,
                "from_currency": "USD",
            },
        ]
    }
    r = client.post("/api/transactions/bulk", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_ledger_summary_route_matches_service_output(summary_client):
    """The route's response must be exactly the service's own dict for the same params —
    not a coincidentally-matching hand-rolled duplicate of the fold."""
    _seed_summary_month(summary_client)

    route_body = summary_client.get("/api/ledger/summary?month=6&year=2026").json()
    service_body = svc.get_ledger_summary(month=6, year=2026)

    assert route_body == service_body


def test_ledger_summary_route_delegates_to_service(summary_client, monkeypatch):
    """Proves delegation, not coincidence: with services.ledger.get_ledger_summary swapped
    for a sentinel, the route's response must equal the sentinel."""
    _seed_summary_month(summary_client)

    sentinel = {"sentinel": True, "by_kind": {}, "total_in": 0, "total_out": 0, "net": 0}
    monkeypatch.setattr(svc, "get_ledger_summary", lambda **kwargs: sentinel)

    body = summary_client.get("/api/ledger/summary?month=6&year=2026").json()
    assert body == sentinel
