"""Regression tests for the ingestion L0 dedup floor (FAIL-C3).

``bulk_create_transactions`` documents "all rows always inserted" and every existing
caller (CSV/QIF import, the AI bulk_add_transactions tool, ...) must keep that default
behavior unconditionally. The ingestion orchestrator opts into an EXTRA belt-and-
suspenders floor via ``dedup_by_external_ref=True`` — a row whose external_ref already
exists in the ledger is skipped instead of inserted, a backstop for the case where the
per-source sync mutex (CONC-C1) has a gap (a stale pre-fetch known-refs snapshot).

Offline: an isolated tmp_path TransactionStore/AccountStore; no network, no LLM.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref
from backend.services import ledger as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return {"account_id": acct["id"]}


def _draft(isolated, *, external_ref: str, amount: float = 10.0):
    return {
        "date": "2026-01-01",
        "kind": "expense",
        "from_account_id": isolated["account_id"],
        "from_amount": amount,
        "from_currency": "SGD",
        # A CONNECTOR namespace, not a made-up one. `dedup_by_external_ref` is the ingestion
        # orchestrator's floor, and an orchestrator's refs are native per-event ids — which is
        # exactly the case invariant 1 (two distinct refs from one authoritative source ARE two
        # real events) exists to protect. Under the fail-CLOSED allowlist (RFC 2026-08-01 §9) an
        # invented namespace like "fake" is non-authoritative, so these otherwise-identical rows
        # match each other and the semantic guard refuses the second before L0 can skip it.
        "external_refs": [{"source": "simplefin", "ref": external_ref}],
        "description": "coffee",
        "source": "feed",
    }


def test_dedup_by_external_ref_skips_an_existing_ref(isolated):
    # Seed the ledger with a row already carrying this external_ref (as if a prior sync
    # — or a competing concurrent run — already committed it).
    svc.bulk_create_transactions([_draft(isolated, external_ref="1")])
    assert len(svc.transactions.all()) == 1

    created, skipped = svc.bulk_create_transactions(
        [_draft(isolated, external_ref="1"), _draft(isolated, external_ref="2")],
        dedup_by_external_ref=True,
    )

    assert len(created) == 1
    assert primary_ref(created[0]) == "2"
    assert len(skipped) == 1
    assert primary_ref(skipped[0]) == "1"
    all_rows = svc.transactions.all()
    assert len(all_rows) == 2  # the pre-seeded row + the one genuinely new row


def test_default_now_REFUSES_a_semantic_duplicate_and_reports_why(isolated):
    """The default is no longer "insert everything".

    This test previously pinned the opposite ("everything passed in gets inserted, even a row
    that repeats an already-stored external_ref") — that WAS the contract, and it is the one the
    owner asked to change: created-then-flagged only protects a book whose review queue is read,
    and rows reach this choke from scheduled agents, connectors and statement imports that have
    no reader. A refused row is REPORTED in ``skipped`` with the reason, never dropped silently,
    so one duplicate in a 300-row statement cannot cost the caller the other 299."""
    svc.bulk_create_transactions([_draft(isolated, external_ref="1")])
    assert len(svc.transactions.all()) == 1

    created, skipped = svc.bulk_create_transactions([_draft(isolated, external_ref="1")])

    assert created == []
    assert len(skipped) == 1
    assert skipped[0]["_duplicate_of"]  # names the row it collided with
    # Tells the caller how to proceed.
    assert "allow_duplicate=true" in skipped[0]["_duplicate_reason"]
    assert len(svc.transactions.all()) == 1  # the duplicate was NOT written


def test_force_restores_the_old_insert_everything_behaviour(isolated):
    """``force`` is the sanctioned escape hatch — the caller has confirmed the rows are
    intentional, so a genuine second identical charge stays bookable."""
    svc.bulk_create_transactions([_draft(isolated, external_ref="1")])

    created, skipped = svc.bulk_create_transactions(
        [_draft(isolated, external_ref="1")], allow_duplicate=True
    )

    assert len(created) == 1
    assert skipped == []
    assert len(svc.transactions.all()) == 2


def test_dedup_by_external_ref_skips_in_batch_repeats_too(isolated):
    created, skipped = svc.bulk_create_transactions(
        [_draft(isolated, external_ref="dup"), _draft(isolated, external_ref="dup")],
        dedup_by_external_ref=True,
    )
    assert len(created) == 1
    assert len(skipped) == 1
    assert len(svc.transactions.all()) == 1
