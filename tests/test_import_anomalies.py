"""Unit tests for the advisory import anomaly/near-duplicate detector (L2).

Covers: a near-duplicate against an existing ledger txn with a DIFFERENT external_id
(the hard dedup wouldn't catch it), an intra-batch duplicate, an amount anomaly vs a
payee's own history, a clean/young book yielding no flags, base-change safety (a base
switch must not change which rows flag), and the end-to-end preview/commit contract:
the preview surfaces flags but the COMMIT still imports EVERYTHING (advisory only)."""

from __future__ import annotations

import pytest

from backend.services import import_anomalies as anom
from backend.services.import_common import RowResult


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, fx_rates):
    """Fresh tmp-backed account/transaction stores (the detector reads
    ledger_service.transactions.query_txns + ledger_service.base_currency)."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture
def checking(isolate_stores):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


def _split_ext(external_id):
    """Split a legacy ``source:ref`` string into the (external_source, external_ref) pair."""
    source, _, ref = external_id.partition(":")
    return source, ref


def _existing_expense(
    checking, *, date, amount, desc, external_id, original_amount=None, original_currency=None
):
    """Persist an existing expense leg through the ledger choke point.

    ``force=True``: these helpers ARRANGE a history — e.g. five identical monthly S$12
    subscription charges — which is exactly what the pre-write duplicate guard refuses. The
    subject here is anomaly DETECTION over that history, not dedup. (Before the fail-closed
    allowlist, invariant 1 exempted them for the wrong reason: their invented ``manual:N``
    namespace read as authoritative, so distinct refs vetoed the match.)"""
    from backend.services import ledger as ledger_service

    source, ref = _split_ext(external_id)
    return ledger_service.create_transaction(
        allow_duplicate=True,
        data={
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": checking["id"],
            "from_amount": amount,
            "from_currency": "USD",
            "external_refs": [{"source": source, "ref": ref}],
            "original_amount": original_amount,
            "original_currency": original_currency,
        },
    )


def _draft(checking, *, date, amount, desc, external_id):
    """A two-leg expense draft RowResult (the importer's shape)."""
    source, ref = _split_ext(external_id)
    return RowResult(
        line=1,
        draft={
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": checking["id"],
            "from_amount": amount,
            "from_currency": "USD",
            "external_refs": [{"source": source, "ref": ref}],
        },
    )


# ── near_duplicate vs the existing ledger ───────────────────────────────────────────


def test_near_duplicate_flagged_when_external_id_differs(checking):
    _existing_expense(
        checking, date="2026-02-10", amount=42.0, desc="Acme Store #42", external_id="manual:1"
    )
    # Same amount, similar description, 1 day apart, DIFFERENT external_id → hard dedup misses it.
    r = _draft(checking, date="2026-02-11", amount=42.0, desc="ACME STORE", external_id="csv:abc")
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 1
    assert summary["flagged_rows"] == 1
    types = [f["type"] for f in r.flags]
    assert "near_duplicate" in types
    assert r.flags[0]["existing_id"]  # points at the matched existing txn
    # SI-50: the match-line sub-line's fields — source is DISPLAY-projected (no explicit
    # source on this txn → raw None → display_source's "manual" default), account_id picks
    # the same (else/from-leg) branch amount/currency already use, and the original-charge
    # pair rides through even when absent (None, not a missing key).
    existing = r.flags[0]["existing"]
    assert existing["source"] == "manual"
    assert existing["account_id"] == checking["id"]
    assert existing["original_amount"] is None
    assert existing["original_currency"] is None


def test_existing_summary_carries_original_charge_when_present(checking):
    """The foreign-charge gap-fill fragment (owner ask, SI-50) needs to know whether the
    EXISTING row already has an original amount — computed once in `_existing_summary` so
    every caller (this match line, the ledger-diff panel) agrees."""
    _existing_expense(
        checking,
        date="2026-02-10",
        amount=42.0,
        desc="Anthropic",
        external_id="manual:2",
        original_amount=31.09,
        original_currency="EUR",
    )
    r = _draft(checking, date="2026-02-11", amount=42.0, desc="Anthropic", external_id="csv:xyz")
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 1
    existing = r.flags[0]["existing"]
    assert existing["original_amount"] == 31.09
    assert existing["original_currency"] == "EUR"


def test_existing_summary_transfer_carries_both_legs():
    """A transfer summary ships BOTH legs (additive, 2026-07-18) so the FE can compare/display
    a linked single-leg statement row against the leg on ITS OWN account — regression repro: a
    +1,450,000 VND credit linked to a Compass SGD→RIV transfer false-drifted ₫1,449,900 because
    only the from-leg (S$76.40) shipped as the summary `amount`."""
    txn = {
        "id": "t1",
        "kind": "transfer",
        "date": "2026-06-01",
        "from_account_id": "compass_sgd",
        "from_amount": 76.40,
        "from_currency": "SGD",
        "to_account_id": "riv",
        "to_amount": 1450000.0,
        "to_currency": "VND",
    }
    s = anom._existing_summary(txn)
    # The legacy `amount`/`currency`/`account_id` still project the from-leg (unchanged).
    assert s["amount"] == 76.40 and s["currency"] == "SGD" and s["account_id"] == "compass_sgd"
    # ...but both legs now ride the wire for the anchor-perspective resolver.
    assert s["to_account_id"] == "riv"
    assert s["from_amount"] == 76.40 and s["from_currency"] == "SGD"
    assert s["to_amount"] == 1450000.0 and s["to_currency"] == "VND"


def test_existing_summary_non_transfer_has_no_leg_keys():
    """Legs are transfer-only — an expense summary is byte-identical to before."""
    s = anom._existing_summary(
        {
            "id": "e1",
            "kind": "expense",
            "date": "2026-06-01",
            "from_amount": 42.0,
            "from_currency": "SGD",
        }
    )
    assert "to_account_id" not in s and "to_amount" not in s and "from_amount" not in s


def test_no_near_duplicate_when_amount_differs(checking):
    _existing_expense(
        checking, date="2026-02-10", amount=42.0, desc="Acme Store", external_id="manual:1"
    )
    r = _draft(checking, date="2026-02-11", amount=999.0, desc="Acme Store", external_id="csv:abc")
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 0
    assert r.flags == []


def test_no_near_duplicate_when_dates_far_apart(checking):
    _existing_expense(
        checking, date="2026-02-01", amount=42.0, desc="Acme Store", external_id="manual:1"
    )
    r = _draft(checking, date="2026-02-20", amount=42.0, desc="Acme Store", external_id="csv:abc")
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 0


def test_same_external_id_not_near_duplicate(checking):
    # The exact same external_id is the hard dedup's job — the advisory layer ignores it.
    _existing_expense(
        checking, date="2026-02-10", amount=42.0, desc="Acme Store", external_id="csv:same"
    )
    r = _draft(checking, date="2026-02-10", amount=42.0, desc="Acme Store", external_id="csv:same")
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 0


# ── intra_batch_duplicate ───────────────────────────────────────────────────────────


def test_intra_batch_duplicate(checking):
    # Engine invariant 3: two rows from the SAME import feed (same batch_id) are two
    # real charges — the bank already de-duplicated its own statement. The preview stamps all
    # drafts with a shared batch_id, so the engine correctly does NOT flag intra-batch pairs.
    r1 = _draft(checking, date="2026-03-01", amount=20.0, desc="Lunch Diner", external_id="csv:1")
    r2 = _draft(checking, date="2026-03-01", amount=20.0, desc="LUNCH DINER", external_id="csv:2")
    r2.line = 2
    summary = anom.detect_anomalies([r1, r2])
    assert summary["intra_batch_duplicate"] == 0
    assert r1.flags == []
    assert r2.flags == []


# ── amount_anomaly vs payee history ─────────────────────────────────────────────────


def test_amount_anomaly_vs_history(checking):
    # A recurring ~12/mo subscription...
    for i, day in enumerate(("05", "06", "07", "08", "09"), start=1):
        _existing_expense(
            checking,
            date=f"2026-01-{day}",
            amount=12.0,
            desc="Streamflix Monthly",
            external_id=f"manual:{i}",
        )
    # ...then a 1200 charge from the same payee → far outside its range.
    r = _draft(
        checking, date="2026-02-05", amount=1200.0, desc="Streamflix Monthly", external_id="csv:x"
    )
    summary = anom.detect_anomalies([r])
    assert summary["amount_anomaly"] == 1
    assert any(f["type"] == "amount_anomaly" for f in r.flags)


def test_no_amount_anomaly_without_enough_history(checking):
    # Only 2 prior observations → below the evidence floor, no judgement made.
    _existing_expense(
        checking, date="2026-01-05", amount=12.0, desc="Streamflix", external_id="manual:1"
    )
    _existing_expense(
        checking, date="2026-01-12", amount=12.0, desc="Streamflix", external_id="manual:2"
    )
    r = _draft(checking, date="2026-02-05", amount=1200.0, desc="Streamflix", external_id="csv:x")
    summary = anom.detect_anomalies([r])
    assert summary["amount_anomaly"] == 0


# ── clean / young book ──────────────────────────────────────────────────────────────


def test_clean_book_no_flags(checking):
    # Empty ledger → nothing to match against → no flags, never fabricated.
    r = _draft(checking, date="2026-04-01", amount=33.0, desc="Brand New Shop", external_id="csv:1")
    summary = anom.detect_anomalies([r])
    assert summary == {
        "near_duplicate": 0,
        "intra_batch_duplicate": 0,
        "transfer_pair": 0,
        "transfer_leg_echo": 0,
        "amount_anomaly": 0,
        "flagged_rows": 0,
    }
    assert r.flags == []


def test_no_candidates_returns_empty_summary(checking):
    # Only a skip row (no draft) → no candidates → all-zero summary.
    skip = RowResult(line=1, skip_reason="malformed")
    summary = anom.detect_anomalies([skip])
    assert summary["flagged_rows"] == 0


# ── base-change safety ──────────────────────────────────────────────────────────────


def test_base_change_safety(checking, monkeypatch):
    """A near-dup is detected on the NATIVE leg via txn_base_amount, so switching the
    reporting base must not change which rows flag (both existing + draft are USD)."""
    _existing_expense(
        checking, date="2026-05-10", amount=88.0, desc="Hardware Depot", external_id="manual:1"
    )
    r = _draft(checking, date="2026-05-11", amount=88.0, desc="Hardware Depot", external_id="csv:a")

    from backend.core import fx as core_fx

    monkeypatch.setattr(core_fx, "base_currency", lambda: "SGD")
    monkeypatch.setattr(anom.ledger_service, "base_currency", lambda: "SGD", raising=False)
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 1  # same USD legs → still a match under SGD base
