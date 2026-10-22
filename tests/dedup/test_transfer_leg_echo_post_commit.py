"""Post-commit reachability of ``TRANSFER_LEG_ECHO`` through ``match_review.review_created_rows``,
and the one-shot-ness of the enqueue that makes a bare ``queued`` count misleading.

A reported incident, 2026-07-31: a single-leg credit into a card account duplicated one leg of a
two-leg ``kind=transfer`` written two days earlier, and reached the ledger twice. The suspicion
was that ``review_created_rows``' candidate pool drops two-leg transfer rows, making
``TRANSFER_LEG_ECHO`` structurally unreachable on the post-commit path.

It does not — ``matching._batch_ledger_pool`` is kind-agnostic, and the first two tests pin that,
so a future narrowing of the pool (an account-side filter, a kind filter, a tighter window) fails
HERE instead of silently costing the owner a duplicate. What the incident actually exposed is the
third test: the engine DID flag the pair on the write, and re-running the pass over the same row
returns ``queued == 0`` — which, before ``already_pending``, was indistinguishable from "no
duplicate found". That ambiguity is what made a net that had already fired look broken.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import match_review


def _arrange_create(data, **kw):
    """Arrangement helper: build a book that ALREADY contains the duplicate under test.

    These tests exercise the POST-write dedup surfaces (review queue, merge, judge), so their
    setup must be able to write rows that the PRE-write duplicate guard refuses. ``force=True``
    is that sanctioned escape hatch; the guard itself is covered by its own regression file.
    """
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture
def book(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    journal = MergeJournalStore()
    journal._path = tmp_path / "merge_journal.json"
    reviews = MatchReviewStore()
    reviews._path = tmp_path / "match_reviews.json"
    decisions = MatchDecisionStore()
    decisions._path = tmp_path / "match_decisions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "merge_journal", journal)
    monkeypatch.setattr(match_review, "reviews", reviews)
    monkeypatch.setattr(match_review, "decisions", decisions)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    accounts.insert({"id": "bank", "name": "Bank Multi", "kind": "asset", "currency": "SGD"})
    accounts.insert({"id": "card", "name": "Rewards Card", "kind": "liability", "currency": "SGD"})
    return txns, reviews


def _booked_transfer() -> dict:
    """The two-leg card payment, booked from the paying bank's own alert."""
    return _arrange_create(
        {
            "date": "2026-07-28",
            "kind": "transfer",
            "description": "Payment to other bank's card ending 1234 - Ref: 11112222333344445555",
            "from_account_id": "bank",
            "from_amount": 1234.56,
            "from_currency": "SGD",
            "to_account_id": "card",
            "to_amount": 1234.56,
            "to_currency": "SGD",
            "source": "agent",
            "cleared": True,
            "batch_id": "imp_first",
            "external_refs": [{"source": "email", "ref": "first-msgid"}],
        }
    )


def _echo_credit() -> dict:
    """The SAME payment seen two days later from the receiving card's own Giro alert: one single
    leg, no ``from_account_id``, its own email message-id, its own import batch."""
    return _arrange_create(
        {
            "date": "2026-07-30",
            "kind": "income",
            "description": "Rewards Card Payment via Giro/Interbank Transfer",
            "to_account_id": "card",
            "to_amount": 1234.56,
            "to_currency": "SGD",
            "source": "agent",
            "cleared": False,
            "batch_id": "imp_second",
            "external_refs": [{"source": "email", "ref": "second-msgid"}],
        }
    )


def test_single_leg_echo_of_a_booked_transfer_is_queued(book):
    """The incident shape: the post-commit pass MUST reach the two-leg transfer as a candidate.

    Pins the pool's kind-agnosticism (a ``kind=transfer`` row is a legal canonical here) and the
    class the engine lands on, so narrowing the pool can never quietly make this unreachable.
    """
    txns, reviews = book
    transfer = _booked_transfer()
    echo = _echo_credit()

    out = match_review.review_created_rows([echo])

    assert out["queued"] == 1
    assert out["auto_merged"] == 0
    item = reviews.all()[0]
    assert item["klass"] == "transfer_leg_echo"
    assert item["canonical_id"] == transfer["id"]
    assert item["incoming_id"] == echo["id"]
    # Created-then-flagged: nothing was dropped, the operator decides.
    assert len([t for t in txns.all() if not t.get("deleted")]) == 2


def test_unrelated_single_leg_near_a_transfer_is_not_queued(book):
    """Control — the pool reaches transfer rows, but ``_classify`` still has to refuse the ones
    that are not an echo. A different amount on the same account two days out queues nothing."""
    _, reviews = book
    _booked_transfer()
    unrelated = _arrange_create(
        {
            "date": "2026-07-30",
            "kind": "expense",
            "description": "SHENG SIONG SUPERMARKET",
            "from_account_id": "card",
            "from_amount": 78.90,
            "from_currency": "SGD",
            "source": "agent",
            "batch_id": "imp_second",
            "external_refs": [{"source": "email", "ref": "third-msgid"}],
        }
    )

    out = match_review.review_created_rows([unrelated])

    assert out == {
        "auto_merged": 0,
        "queued": 0,
        "already_pending": 0,
        "reviews": [],
        "pending_reviews": [],
    }
    assert reviews.all() == []


def test_rerun_reports_already_pending_instead_of_a_bare_zero(book):
    """The diagnostic half of the incident.

    Re-running the pass over a row the engine ALREADY flagged correctly declines to enqueue a
    second copy (F11 pair dedup) — but a bare ``queued: 0`` reads identically to "no duplicate
    found", which is exactly how a net that had fired looked broken. ``already_pending`` carries
    the live item ids so the two outcomes are distinguishable at every caller.
    """
    _, reviews = book
    _booked_transfer()
    echo = _echo_credit()

    first = match_review.review_created_rows([echo])
    assert first["queued"] == 1 and first["already_pending"] == 0
    assert first["pending_reviews"] == []

    second = match_review.review_created_rows([echo])
    assert second["queued"] == 0
    assert second["already_pending"] == 1
    assert second["pending_reviews"] == first["reviews"]
    # Still exactly one review item — the re-run enqueued nothing.
    assert len(reviews.all()) == 1
