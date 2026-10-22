"""Phase 0 — deterministic floor & data model (RFC transaction-dedup-engine).

Covers the foundation every later phase builds on:
  * the additive model fields default empty / null (back-compat),
  * the idempotency floor consults absorbed ``external_refs`` (a re-import of an absorbed
    source is a no-op on the canonical row),
  * a merge tombstone is excluded from balances / net worth exactly like ``deleted``,
  * the merge primitive: inheritance, tombstoning, ``become_transfer``, and a byte-exact undo,
  * the import occurrence-index disambiguation (the silent-drop bug fix), unit level.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import ledger as ledger_service


def _arrange_create(data, **kw):
    """Arrangement helper: build a book that ALREADY contains the duplicate under test.

    These tests exercise the POST-write dedup surfaces (review queue, merge, judge), so their
    setup must be able to write rows that the PRE-write duplicate guard refuses. ``force=True``
    is that sanctioned escape hatch; the guard itself is covered by its own regression file.
    """
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, db_isolated):
    """Rebind the ledger's account / transaction / merge-journal singletons to fresh
    instances backed by db_isolated's fresh, isolated database."""
    from backend.db.account_store import AccountStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    return accounts, txns, journal


@pytest.fixture
def two_accounts(isolate_stores):
    accounts, _, _ = isolate_stores
    meridian = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    quickpay = accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return meridian, quickpay


def _split_ext(external_id: str | None) -> tuple[str | None, str | None]:
    """Split a legacy combined ``"source:ref"`` string into the (external_source, external_ref)
    pair the store now stores. Bare value (no colon) → source None."""
    if external_id is None:
        return None, None
    if ":" in external_id:
        source, ref = external_id.split(":", 1)
        return source, ref
    return None, external_id


def _expense(account_id, amount, date="2026-02-01", external_id=None, desc="Coffee"):
    source, ref = _split_ext(external_id)
    return _arrange_create(
        {
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": account_id,
            "from_amount": amount,
            "from_currency": "SGD",
            "external_refs": [{"source": source, "ref": ref}],
        }
    )


def _income(account_id, amount, date="2026-02-01", external_id=None, desc="Top-up"):
    source, ref = _split_ext(external_id)
    return _arrange_create(
        {
            "date": date,
            "kind": "income",
            "description": desc,
            "to_account_id": account_id,
            "to_amount": amount,
            "to_currency": "SGD",
            "external_refs": [{"source": source, "ref": ref}],
        }
    )


# ── model defaults ───────────────────────────────────────────────────────────────


def test_new_fields_default_empty(two_accounts):
    meridian, _ = two_accounts
    row = _expense(meridian["id"], 4.50)
    assert row["external_refs"] == []
    assert row["merged_from"] == []
    assert row["merged_into"] is None


# ── idempotency floor consults external_refs ─────────────────────────────────────


def test_by_external_ref_matches_absorbed_list(two_accounts):
    meridian, _ = two_accounts
    canonical = _expense(meridian["id"], 9.00, external_id="email:abc")
    absorbed = _expense(meridian["id"], 9.00, external_id="csv:xyz", date="2026-02-02")

    ledger_service.merge_transactions(canonical["id"], absorbed["id"], reason="same charge")

    # The floor now finds the canonical by the ABSORBED source pair.
    found = ledger_service.transactions.by_external_ref("csv", "xyz")
    assert found is not None and found["id"] == canonical["id"]
    assert ledger_service.transactions.has_external_ref("csv", "xyz")

    # ...so re-creating that absorbed source is a safe no-op returning the canonical.
    again = _expense(meridian["id"], 9.00, external_id="csv:xyz")
    assert again["id"] == canonical["id"]
    live = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(live) == 1


# ── tombstone excluded from balances / net worth ─────────────────────────────────


def test_merge_tombstone_excluded_from_balance(two_accounts):
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 10.00, external_id="email:a")
    b = _expense(meridian["id"], 10.00, external_id="csv:b", date="2026-02-02")
    acct = ledger_service.accounts.find_by_id(meridian["id"])

    txns = ledger_service.transactions.all()
    before = ledger_service.account_balance(acct, txns, ledger_service.opening_date(acct, txns))
    ledger_service.merge_transactions(a["id"], b["id"])
    txns = ledger_service.transactions.all()
    after = ledger_service.account_balance(acct, txns, ledger_service.opening_date(acct, txns))

    # One of the two duplicate $10 debits is now a tombstone → balance recovers $10.
    assert round(after - before, 2) == 10.00
    tomb = ledger_service.transactions.find_by_id(b["id"])
    assert tomb["merged_into"] == a["id"] and tomb["deleted"] is True


# ── merge inheritance + audit ────────────────────────────────────────────────────


def test_merge_inherits_external_refs_and_records(two_accounts):
    meridian, _ = two_accounts
    a = _expense(meridian["id"], 5.00, external_id="email:a")
    b = _expense(meridian["id"], 5.00, external_id="csv:b", date="2026-02-02")

    canonical = ledger_service.merge_transactions(a["id"], b["id"], reason="dup")
    assert {"source": "csv", "ref": "b"} in canonical["external_refs"]
    assert b["id"] in canonical["merged_from"]


# ── become_transfer (Meridian Bank → QuickPay) ───────────────────────────────────────────────


def test_become_transfer_builds_two_leg(two_accounts):
    meridian, quickpay = two_accounts
    debit = _expense(
        meridian["id"],
        50.00,
        date="2026-02-03",
        external_id="csv:meridian",
        desc="Transfer to QuickPay",
    )
    credit = _income(
        quickpay["id"], 50.00, date="2026-02-01", external_id="csv:quickpay", desc="Top-up"
    )

    transfer = ledger_service.merge_transactions(
        debit["id"], credit["id"], reason="top-up pair", become_transfer=True
    )
    assert transfer["kind"] == "transfer"
    assert transfer["from_account_id"] == meridian["id"]
    assert transfer["to_account_id"] == quickpay["id"]
    assert transfer["from_amount"] == 50.00 and transfer["to_amount"] == 50.00
    # Keeps the EARLIER of the two dates (settlement-timing gap).
    assert transfer["date"] == "2026-02-01"
    assert transfer["category"] is None


# ── merge → undo restores byte-identical state ───────────────────────────────────


def test_merge_then_undo_round_trip(two_accounts):
    meridian, quickpay = two_accounts
    a = _expense(meridian["id"], 7.00, external_id="email:a")
    b = _income(quickpay["id"], 7.00, external_id="csv:b", date="2026-02-02")
    before_a = ledger_service.transactions.find_by_id(a["id"])
    before_b = ledger_service.transactions.find_by_id(b["id"])

    ledger_service.merge_transactions(a["id"], b["id"], reason="x", become_transfer=True)
    restored = ledger_service.unmerge_transactions(b["id"])
    assert restored is not None

    after_a = ledger_service.transactions.find_by_id(a["id"])
    after_b = ledger_service.transactions.find_by_id(b["id"])
    assert after_a == before_a
    assert after_b == before_b
    # journal entry consumed
    assert ledger_service.merge_journal.find_by_id(b["id"]) is None


# ── merge guards ─────────────────────────────────────────────────────────────────


def test_merge_rejects_self_and_already_merged(two_accounts):
    from backend.core.errors import ValidationError

    meridian, _ = two_accounts
    a = _expense(meridian["id"], 3.00, external_id="email:a")
    b = _expense(meridian["id"], 3.00, external_id="csv:b", date="2026-02-02")

    with pytest.raises(ValidationError):
        ledger_service.merge_transactions(a["id"], a["id"])

    ledger_service.merge_transactions(a["id"], b["id"])
    with pytest.raises(ValidationError):
        ledger_service.merge_transactions(a["id"], b["id"])  # b already merged


def test_become_transfer_rejects_canonical_already_transfer(two_accounts):
    """Review CRITICAL-1: a become_transfer merge where the canonical is ALREADY a two-leg
    transfer must refuse (it satisfies both debit+credit, which would silently rebuild it from
    its own legs and ignore the absorbed row)."""
    from backend.core.errors import ValidationError

    meridian, quickpay = two_accounts
    transfer = _arrange_create(
        {
            "date": "2026-02-01",
            "kind": "transfer",
            "description": "Top-up",
            "from_account_id": meridian["id"],
            "from_amount": 50.0,
            "from_currency": "SGD",
            "to_account_id": quickpay["id"],
            "to_amount": 50.0,
            "to_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "t1"}],
        }
    )
    income = _income(quickpay["id"], 50.0, external_id="csv:i1", date="2026-02-02")
    with pytest.raises(ValidationError):
        ledger_service.merge_transactions(transfer["id"], income["id"], become_transfer=True)


# ── occurrence-index disambiguation (unit) ───────────────────────────────────────


def test_disambiguate_identical_rows_unit():
    from backend.services.import_common import RowResult, disambiguate_identical_rows

    results = [
        RowResult(line=1, draft={"external_refs": [{"source": "csv", "ref": "hABC"}]}),
        RowResult(line=2, draft={"external_refs": [{"source": "csv", "ref": "hABC"}]}),
        RowResult(line=3, draft={"external_refs": [{"source": "csv", "ref": "hABC"}]}),
        RowResult(line=4, draft={"external_refs": [{"source": "csv", "ref": "hDIFF"}]}),
    ]
    disambiguate_identical_rows(results)
    refs = [primary_ref(r.draft) for r in results]
    # First occurrence keeps the bare ref (back-compat); repeats get a stable suffix.
    assert refs == ["hABC", "hABC:1", "hABC:2", "hDIFF"]
    # external_source is never touched.
    assert all(primary_source(r.draft) == "csv" for r in results)
