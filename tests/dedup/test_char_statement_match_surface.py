"""Characterization: the match-engine surfaces that MUST NOT MOVE (RFC 2026-07-29, Phase 0).

RFC ``.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`` collapses the
statement twin matcher into ``services/matching`` and threads a ``MatchPolicy`` through the
engine. Its safety argument is that only ONE surface (``statements/decisions._apply_batch_match``,
Phase 2) ever opts into the new weak band, and every OTHER consumer of the engine keeps
byte-identical behavior:

    RFC §D1 — "every knob except ``weak_tier`` is monotone-TIGHTENING … so no policy can widen
    the blast radius of any other surface (``import_anomalies``, ``match_review``,
    ``bulk_add_transactions``, connector sync)"
    RFC "Test strategy" — "A new ``test_char_statement_match_surface.py``: ``check()`` /
    ``match_review.review_created_rows`` / ``bulk_add_transactions`` outputs byte-identical
    before and after Phases 1–2."

This file is that pin. It asserts WHOLE projected outputs, not spot-checks, so a Phase 1/2 diff
that touches these paths fails with the full structure visible. Nothing here is aspirational:
every value was captured from the code as it stands, including one pre-existing defect that is
labelled where it is pinned.

Ids are server-generated, so each pin references the fixture's own ids rather than a literal.
"""

from __future__ import annotations

import json

import pytest

from backend.services import ledger as ledger_service
from backend.services import match_review, matching

_SGD = {"SGD": 1.0}


def _arrange_create(data, **kw):
    """Arrangement helper — see tests/dedup/test_phase0_foundation.py. Builds a book that
    ALREADY contains the duplicate under test; the PRE-write guard would refuse it, and
    ``force=True`` is the sanctioned escape hatch for exactly that."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture
def book(tmp_path, monkeypatch, db_isolated):
    """One isolated ledger for every surface below, so a divergence between two surfaces is
    a real divergence and not a fixture difference."""
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    journal = MergeJournalStore()
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
    accounts.insert({"id": "meridian", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"})
    accounts.insert({"id": "cc", "name": "Card", "kind": "liability", "currency": "SGD"})
    return accounts, txns, reviews


# ══ check() — the single-row entry point every ingest surface calls ════════════════════


def _pin_check(res: matching.MatchResult) -> dict:
    """Every field ``MatchResult`` carries, plus every field of every ranked candidate."""
    return {
        "action": res.action,
        "canonical_id": res.canonical_id,
        "become_transfer": res.become_transfer,
        "reasons": list(res.reasons),
        "candidates": [
            {
                "id": c.id,
                "klass": c.klass.value,
                "score": c.score,
                "reasons": list(c.reasons),
                "become_transfer": c.become_transfer,
                "fidelity": c.fidelity,
                "echo_side": c.echo_side,
            }
            for c in res.candidates
        ],
    }


@pytest.fixture
def check_book(book):
    """Three ledger rows covering the three classes ``check()`` can return, so one fixture
    exercises the L0 floor, the near-duplicate tier, the wordless tier and F06."""
    grab = _arrange_create(
        {
            "date": "2026-04-01",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": "meridian",
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "r1"}],
            "source": "email",
        }
    )
    coffee = _arrange_create(
        {
            "date": "2026-04-02",
            "kind": "expense",
            "description": "Coffee run",
            "from_account_id": "meridian",
            "from_amount": 8.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    card_payment = _arrange_create(
        {
            "date": "2026-04-03",
            "kind": "transfer",
            "description": "Card payment",
            "from_account_id": "meridian",
            "from_amount": 500.00,
            "from_currency": "SGD",
            "to_account_id": "cc",
            "to_amount": 500.00,
            "to_currency": "SGD",
            "source": "manual",
        }
    )
    return grab, coffee, card_payment


def _incoming(date, description, amount, **over):
    row = {
        "date": date,
        "kind": "expense",
        "description": description,
        "from_account_id": "meridian",
        "from_amount": amount,
        "from_currency": "SGD",
        "source": "import",
    }
    row.update(over)
    return row


def test_check_l0_exact_external_ref_is_a_noop(check_book):
    """The unbypassable floor: a row re-presenting an external ref already in the ledger is a
    ``noop`` against that row, with a synthetic EXACT candidate at score 1.0."""
    grab, _, _ = check_book
    row = _incoming(
        "2026-04-01",
        "anything at all",
        25.50,
        external_refs=[{"source": "email", "ref": "r1"}],
        source="email",
    )

    assert _pin_check(matching.check(row, rates=_SGD)) == {
        "action": "noop",
        "canonical_id": grab["id"],
        "become_transfer": False,
        "reasons": ["exact external ref already present (email:r1)"],
        "candidates": [
            {
                "id": grab["id"],
                "klass": "exact",
                "score": 1.0,
                "reasons": ["exact id"],
                "become_transfer": False,
                "fidelity": 0.0,
                "echo_side": None,
            }
        ],
    }


def test_check_near_duplicate_tier(check_book):
    """An email-logged charge and the statement line of the same charge, one day apart."""
    grab, _, _ = check_book
    row = _incoming("2026-04-02", "GRAB* SG RIDE", 25.50)

    assert _pin_check(matching.check(row, rates=_SGD)) == {
        "action": "review",
        "canonical_id": grab["id"],
        "become_transfer": False,
        "reasons": ["same amount, similar description, near date, different source"],
        "candidates": [
            {
                "id": grab["id"],
                "klass": "near_duplicate",
                "score": 0.95,
                "reasons": ["same amount, similar description, near date, different source"],
                "become_transfer": False,
                "fidelity": 0.0,
                "echo_side": None,
            }
        ],
    }


def test_check_wordless_same_day_tier(check_book):
    """F21: same account, same amount, same day, zero shared wording — review-capped."""
    _, coffee, _ = check_book
    row = _incoming("2026-04-02", "SBUX #4471 ORCHARD", 8.00)

    assert _pin_check(matching.check(row, rates=_SGD)) == {
        "action": "review",
        "canonical_id": coffee["id"],
        "become_transfer": False,
        "reasons": ["same account, same amount, same day — wording differs (review)"],
        "candidates": [
            {
                "id": coffee["id"],
                "klass": "near_duplicate",
                "score": 0.75,
                "reasons": ["same account, same amount, same day — wording differs (review)"],
                "become_transfer": False,
                "fidelity": 0.0,
                "echo_side": None,
            }
        ],
    }


def test_check_transfer_leg_echo_tier(check_book):
    """F06: a single-leg debit that echoes the debit leg of an already-booked transfer. The
    TRANSFER is the canonical, and the side is recorded on the candidate."""
    _, _, card_payment = check_book
    row = _incoming("2026-04-03", "PAYMENT TO CARD", 500.00)

    assert _pin_check(matching.check(row, rates=_SGD)) == {
        "action": "review",
        "canonical_id": card_payment["id"],
        "become_transfer": False,
        "reasons": ["This debit entry matches one leg of an existing transfer."],
        "candidates": [
            {
                "id": card_payment["id"],
                "klass": "transfer_leg_echo",
                "score": 0.75,
                "reasons": ["This debit entry matches one leg of an existing transfer."],
                "become_transfer": False,
                "fidelity": 0.0,
                "echo_side": "debit",
            }
        ],
    }


def test_check_unrelated_row_creates(check_book):
    """No tier fires → ``create`` with an empty candidate list. The negative case matters as
    much as the positives: a Phase 2 weak band must NOT start surfacing here."""
    row = _incoming("2026-04-05", "COLD STORAGE", 61.20)

    assert _pin_check(matching.check(row, rates=_SGD)) == {
        "action": "create",
        "canonical_id": None,
        "become_transfer": False,
        "reasons": [],
        "candidates": [],
    }


# ══ match_review.review_created_rows — the post-commit surface ═════════════════════════


def test_review_created_rows_queues_exactly_the_near_duplicate(book):
    """A two-row import batch against a ledger holding one of the two charges already.

    Pins the return contract (``auto_merged``/``queued``/``reviews``) AND the whole enqueued
    review row, because ``review_created_rows`` reads ``rm.assigned``/``rm.alternatives``/
    ``rm.band`` straight off ``match_batch``. ``combine()`` with ``llm_verdict=None`` is the
    Phase 1 safety floor, so ``auto_merged`` is always 0 here.
    """
    _, _, reviews = book
    existing = _arrange_create(
        {
            "date": "2026-05-10",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": "meridian",
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "m1"}],
            "source": "email",
        }
    )
    created = [
        _arrange_create(
            {
                "date": "2026-05-11",
                "kind": "expense",
                "description": "GRAB* SG RIDE",
                "from_account_id": "meridian",
                "from_amount": 25.50,
                "from_currency": "SGD",
                "external_refs": [{"source": "stmt", "ref": "s1"}],
                "source": "csv_import",
                "batch_id": "batch-x",
            }
        ),
        _arrange_create(
            {
                "date": "2026-05-12",
                "kind": "expense",
                "description": "COLD STORAGE",
                "from_account_id": "meridian",
                "from_amount": 61.20,
                "from_currency": "SGD",
                "external_refs": [{"source": "stmt", "ref": "s2"}],
                "source": "csv_import",
                "batch_id": "batch-x",
            }
        ),
    ]

    out = match_review.review_created_rows(created, batch_id="batch-x")

    assert out["auto_merged"] == 0
    assert out["queued"] == 1
    assert out["reviews"] == [r["id"] for r in reviews.all()]

    queued = reviews.all()
    assert len(queued) == 1
    assert {k: v for k, v in queued[0].items() if k not in ("id", "created_at", "signature")} == {
        "incoming_id": created[0]["id"],
        "canonical_id": existing["id"],
        "klass": "near_duplicate",
        "score": 0.95,
        "become_transfer": False,
        "reasons": ["same amount, similar description, near date, different source"],
        "llm_reason": None,
        "batch_id": "batch-x",
        "alternatives": [],
        "status": "pending",
        # PRE-EXISTING DEFECT, pinned deliberately: ``match_review`` passes ``band=rm.band``
        # ("review" here), but ``MatchReviewStore.enqueue`` builds an explicit allowlist row
        # that has no ``band`` key, so the engine's own band never reaches the store. Out of
        # scope for this RFC — flagged so a Phase 2 lane touching bands does not read this
        # absence as something it caused.
    }
    assert "band" not in queued[0]


def test_review_created_rows_is_a_noop_on_an_empty_batch(book):
    # ``already_pending``/``pending_reviews`` are ADDITIVE (a reported incident, 2026-07-31): the pairs
    # the F11 dedup declined to re-enqueue because a live review item already covers them. They
    # were previously invisible, which made a re-run over an already-flagged duplicate return the
    # same ``queued: 0`` as a clean row. No selection, scoring or enqueue behavior moved — every
    # other pin in this file, ``check()``'s included, is byte-identical.
    assert match_review.review_created_rows([], batch_id="batch-empty") == {
        "auto_merged": 0,
        "queued": 0,
        "already_pending": 0,
        "reviews": [],
        "pending_reviews": [],
    }


# ══ bulk_add_transactions (the AI `add_transactions` tool) — the pre-flight surface ═════


@pytest.fixture
def tool_book(book):
    """``add_transactions``' own store surface on top of ``book``."""
    # audit_service._txns/_audit, ledger_service.import_batches, AND payee_registry._store
    # are all DB-backed now (TransactionStore/TransactionAuditStore/ImportBatchStore — plan 3
    # task 4; PayeeStore — plan 4d task 2) — they already resolve against the SAME isolated
    # database `book` set up via db_isolated, no per-instance path-sync needed.
    return book


@pytest.mark.asyncio
async def test_bulk_add_transactions_preflight_aborts_on_a_strong_match(tool_book):
    """RFC §D1 names ``bulk_add_transactions`` as a surface a policy must never widen. Its
    pre-flight is ``matching.match_batch(rows, context=BatchContext(complete=False))``: a
    STRONG-band row aborts the WHOLE call before anything is booked, and reports the engine's
    verdict verbatim. Pinned whole — reason string included, since that text is assembled from
    ``klass``/``score``/``reasons`` and would shift if any of the three moved.
    """
    from backend.ai.tools.ledger import add_transactions

    existing = _arrange_create(
        {
            "date": "2026-06-06",
            "kind": "expense",
            "description": "Grab Ride",
            "from_account_id": "meridian",
            "from_amount": 15.90,
            "from_currency": "SGD",
            "source": "ai",
        }
    )

    raw, proposed = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "meridian",
                },
                {
                    "date": "2026-06-07",
                    "description": "Cold Storage groceries",
                    "amount": 61.20,
                    "currency": "SGD",
                    "account_id": "meridian",
                },
            ],
            "import_description": "characterization pin",
        }
    )
    body = json.loads(raw)

    assert proposed is None
    assert body["ok"] is False
    assert body["created"] == 0
    assert body["total_submitted"] == 2
    assert [
        {k: v for k, v in rec.items() if k != "existing"} for rec in body["already_recorded"]
    ] == [
        {
            "index": 0,
            "description": "Grab Ride",
            "existing_id": existing["id"],
            "score": 1.0,
            "reason": (
                "near_duplicate (score 1.0); same amount, similar description, "
                "near date, different source"
            ),
        }
    ]
    assert body["already_recorded"][0]["existing"]["id"] == existing["id"]
    # Nothing written — the abort precedes every write.
    assert [t["id"] for t in ledger_service.transactions.all() if not t.get("deleted")] == [
        existing["id"]
    ]


@pytest.mark.asyncio
async def test_bulk_add_transactions_books_a_clean_batch_untouched(tool_book):
    """The negative half: with no ledger collision the pre-flight is silent and every row
    books. A Phase 2 weak band leaking into this surface would show up right here."""
    from backend.ai.tools.ledger import add_transactions

    raw, proposed = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Cold Storage groceries",
                    "amount": 61.20,
                    "currency": "SGD",
                    "account_id": "meridian",
                },
                {
                    "date": "2026-06-07",
                    "description": "Kopitiam lunch",
                    "amount": 5.40,
                    "currency": "SGD",
                    "account_id": "meridian",
                },
            ],
            "import_description": "characterization pin",
        }
    )
    body = json.loads(raw)

    assert proposed is None
    assert body["ok"] is True
    assert body["created"] == 2
    assert "already_recorded" not in body
    live = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert sorted(t["description"] for t in live) == [
        "Cold Storage groceries",
        "Kopitiam lunch",
    ]
