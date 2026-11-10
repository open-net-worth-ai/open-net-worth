"""``check_duplicates`` — the PRE-write duplicate check (a reported incident, 2026-07).

The incident: the same S$2,000.00 card payment was booked TWICE by the email agent — once as a
two-leg transfer from the bank alert, once as a single-leg credit from the Giro alert two days
later — and caught by eye, twice. Every dedup surface was post-hoc (``review_created_rows`` runs
after the write; ``find_duplicates`` scans the ledger by date range), so an agent's only
protection ran after it had already written.

These tests pin the four properties that make the new tool trustworthy:
  1. a proposed row that duplicates an existing one comes back with the engine's OWN verdict
     (klass / score / band / reasons / the matched transaction) — never a second dialect;
  2. a clean row comes back with no candidates and "create";
  3. the input cap errors clearly, naming the cap, having checked nothing;
  4. NOTHING is written — the transaction store, review queue and decision cache are byte-for-byte
     unchanged across a call.

Every figure here is synthesised. No real financial data enters the repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.ai.tools.matching as matching_tools
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services import match_preflight, match_review

PAYMENT = 2000.00  # synthesised; shaped like the incident (a round-ish card payment)


def _arrange_create(data, **kw):
    """Arrangement helper — see tests/dedup/test_phase0_foundation.py. Builds a book that
    ALREADY contains the duplicate under test; the PRE-write guard would refuse it, and
    ``force=True`` is the sanctioned escape hatch for exactly that."""
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    reviews_store = MatchReviewStore()
    reviews_store._path = tmp_path / "match_reviews.json"
    decisions_store = MatchDecisionStore()
    decisions_store._path = tmp_path / "match_decisions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(match_review, "reviews", reviews_store)
    monkeypatch.setattr(match_review, "decisions", decisions_store)
    bank = accounts.create({"name": "Bank SGD", "kind": "asset", "type": "cash", "currency": "SGD"})
    card = accounts.create(
        {"name": "Card SGD", "kind": "liability", "type": "credit", "currency": "SGD"}
    )
    return bank, card, tmp_path


def _book_card_payment(bank_id: str, card_id: str, date: str = "2026-06-10") -> dict:
    """The bank alert, already in the ledger: one two-leg transfer bank -> card."""
    return _arrange_create(
        {
            "date": date,
            "kind": "transfer",
            "description": "PAYMENT TO CARD",
            "from_account_id": bank_id,
            "from_amount": PAYMENT,
            "from_currency": "SGD",
            "to_account_id": card_id,
            "to_amount": PAYMENT,
            "to_currency": "SGD",
            "source": "email",
        }
    )


async def _check(items, **extra):
    raw, proposed = await matching_tools.check_duplicates({"transactions": items, **extra})
    assert proposed is None, "check_duplicates must never return a proposed_change"
    return json.loads(raw)


def _store_bytes(tmp_path: Path) -> dict[str, bytes | None]:
    """Every file this call could conceivably touch, as raw bytes (None = still absent)."""
    return {
        name: (tmp_path / name).read_bytes() if (tmp_path / name).exists() else None
        for name in ("transactions.json", "match_reviews.json", "match_decisions.json")
    }


# ── 1. the incident ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_giro_credit_echoing_an_existing_transfer_is_flagged_before_the_write(isolate):
    """The incident, replayed as a PRE-write check: the bank alert is already booked as a
    two-leg transfer; the Giro alert arrives two days later as a single-leg credit onto the
    card. The engine calls that TRANSFER_LEG_ECHO — and now the agent hears about it BEFORE it
    writes, not from a post-hoc hook it may never read."""
    bank, card, _ = isolate
    booked = _book_card_payment(bank["id"], card["id"])

    body = await _check(
        [
            {
                "ref": "giro-alert",
                "date": "2026-06-12",
                "description": "GIRO PAYMENT RECEIVED",
                "account_id": card["id"],
                "direction": "credit",
                "amount": PAYMENT,
                "currency": "SGD",
            }
        ]
    )

    (row,) = body["results"]
    assert row["ref"] == "giro-alert"
    assert row["index"] == 0
    assert body["flagged"] == 1
    assert row["band"] == "review"
    assert row["recommended_action"]["action"] == "confirm"
    assert row["duplicate_of_index"] is None  # the match is a LEDGER row, not a sibling

    (cand,) = row["candidates"]
    assert cand["klass"] == "transfer_leg_echo"
    assert cand["score"] == pytest.approx(0.75)
    assert cand["existing_id"] == booked["id"]
    assert cand["existing"]["kind"] == "transfer"
    assert cand["reasons"], "the engine's own reasons must reach the caller"
    assert cand["signature"], "a ledger-side match carries the pair signature"


@pytest.mark.asyncio
async def test_clean_row_returns_no_candidates(isolate):
    bank, card, _ = isolate
    _book_card_payment(bank["id"], card["id"])

    body = await _check(
        [
            {
                "date": "2026-06-12",
                "description": "KOPITIAM BREAKFAST",
                "account_id": card["id"],
                "amount": 4.80,
                "currency": "SGD",
            }
        ]
    )
    (row,) = body["results"]
    assert row["candidates"] == []
    assert row["candidates_total"] == 0
    assert row["band"] == "none"
    assert row["recommended_action"]["action"] == "create"
    assert body["flagged"] == 0


# ── 2. rows are checked against EACH OTHER too ───────────────────────────────────────


@pytest.mark.asyncio
async def test_two_proposed_legs_of_one_movement_point_at_each_other(isolate):
    """A list that describes ONE movement twice — a debit off the bank and a credit onto the
    card, same amount, same day — the shape a per-row ledger scan structurally cannot see.
    ``duplicate_of_index`` names the other submitted row, and the sibling candidate carries no
    existing_id/signature because there is no persisted row to point at."""
    bank, card, _ = isolate

    body = await _check(
        [
            {
                "date": "2026-06-10",
                "description": "PAYMENT TO CARD",
                "account_id": bank["id"],
                "direction": "debit",
                "amount": PAYMENT,
                "currency": "SGD",
            },
            {
                "date": "2026-06-10",
                "description": "GIRO PAYMENT RECEIVED",
                "account_id": card["id"],
                "direction": "credit",
                "amount": PAYMENT,
                "currency": "SGD",
            },
        ]
    )
    flagged = [r for r in body["results"] if r["duplicate_of_index"] is not None]
    assert flagged, f"an in-list pair must be reported: {body}"
    row = flagged[0]
    assert row["duplicate_of_index"] in (0, 1) and row["duplicate_of_index"] != row["index"]
    assert "THIS SAME list" in row["recommended_action"]["note"]
    cand = row["candidates"][0]
    assert cand["klass"] == "transfer_pair"
    assert cand["proposed_index"] == row["duplicate_of_index"]
    assert cand["existing_id"] is None and cand["existing"] is None
    assert cand["signature"] is None


@pytest.mark.asyncio
async def test_two_identical_same_direction_rows_are_NOT_flagged_against_each_other(isolate):
    """Parity with the write, deliberately. ``match_batch`` stamps one synthetic feed id across
    the batch, so the same-feed invariant ("the bank already de-duplicated its own statement")
    vetoes two same-direction rows in ONE list — exactly as it does inside ``add_transactions``'
    own pre-flight. A check that flagged what the write then books happily would be worse than
    no check: the caller would learn to ignore it. Cross-source repeats — the actual incident —
    are unaffected, because they arrive in different calls."""
    _bank, card, _ = isolate

    body = await _check(
        [
            {
                "date": "2026-06-12",
                "description": "ANNUAL FEE",
                "account_id": card["id"],
                "amount": 192.60,
                "currency": "SGD",
            },
            {
                "date": "2026-06-12",
                "description": "ANNUAL FEE",
                "account_id": card["id"],
                "amount": 192.60,
                "currency": "SGD",
            },
        ]
    )
    assert body["flagged"] == 0
    assert all(r["duplicate_of_index"] is None for r in body["results"])


# ── 3. L0 — the exact-external-ref floor ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exact_external_ref_reports_skip(isolate):
    """L0 is skipped by ``match_batch`` (its callers already ran it at the create choke). For a
    row that has NOT been written it has not run, and it is the most decisive answer there is:
    ``create_transaction`` would return the existing row rather than create a second one."""
    _bank, card, _ = isolate
    booked = _arrange_create(
        {
            "date": "2026-06-12",
            "kind": "expense",
            "description": "NETFLIX",
            "from_account_id": card["id"],
            "from_amount": 22.98,
            "from_currency": "SGD",
            "source": "email",
            "external_refs": [{"source": "email", "ref": "msg-771"}],
        }
    )

    body = await _check(
        [
            {
                "date": "2026-06-12",
                "description": "NETFLIX",
                "account_id": card["id"],
                "amount": 22.98,
                "currency": "SGD",
                "external_refs": [{"source": "email", "ref": "msg-771"}],
            }
        ]
    )
    (row,) = body["results"]
    assert row["band"] == "strong"
    assert row["recommended_action"]["action"] == "skip"
    (cand,) = row["candidates"]
    assert cand["klass"] == "exact"
    assert cand["score"] == 1.0
    assert cand["existing_id"] == booked["id"]


# ── 4. bounding ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_over_the_input_cap_errors_and_names_it(isolate, monkeypatch):
    _bank, card, _ = isolate
    monkeypatch.setattr(match_preflight, "MAX_PROPOSED_ROWS", 2)
    items = [
        {
            "date": "2026-06-12",
            "description": f"ROW {i}",
            "account_id": card["id"],
            "amount": 1.0 + i,
            "currency": "SGD",
        }
        for i in range(3)
    ]
    body = await _check(items)
    assert "2-row cap" in body["error"]
    assert "Nothing was checked" in body["error"]
    assert "results" not in body


def test_service_raises_over_the_cap(isolate, monkeypatch):
    """The cap is enforced in the SERVICE too, so a non-tool caller cannot bypass it."""
    monkeypatch.setattr(match_preflight, "MAX_PROPOSED_ROWS", 1)
    with pytest.raises(ValidationError, match="1-row check cap"):
        match_preflight.check_proposed_rows([{}, {}])


def test_input_cap_equals_the_write_cap():
    """A pre-write check that refuses rows the write itself accepts is useless at the only
    boundary that matters. Pinned so the two cannot drift apart silently."""
    from backend.ai.tools.ledger import _MAX_BULK_ADD_ROWS

    assert match_preflight.MAX_PROPOSED_ROWS == _MAX_BULK_ADD_ROWS


@pytest.mark.asyncio
async def test_candidates_are_capped_per_row_and_the_true_total_is_reported(isolate):
    """A bounded read must never look like a complete one — the cap reports the pre-cap total
    and the payload says how to reach the rest."""
    _bank, card, _ = isolate
    for i in range(4):
        _arrange_create(
            {
                "date": f"2026-07-0{i + 1}",
                "kind": "expense",
                "description": "STARBUCKS SG",
                "from_account_id": card["id"],
                "from_amount": 6.50,
                "from_currency": "SGD",
                "source": "csv_import",
                "external_refs": [{"source": "csv", "ref": f"sbux-{i}"}],
            }
        )

    body = await _check(
        [
            {
                "date": "2026-07-02",
                "description": "STARBUCKS SG",
                "account_id": card["id"],
                "amount": 6.50,
                "currency": "SGD",
            }
        ],
        max_candidates=1,
    )
    (row,) = body["results"]
    assert row["candidates_showing"] == 1
    assert row["candidates_total"] >= 2, f"expected several candidates, got {row}"
    assert "max_candidates" in body["note"]


# ── 5. it writes NOTHING ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_flagged_check_writes_nothing(isolate):
    """The load-bearing safety property: a duplicate verdict must not create a transaction, a
    review item or a remembered decision. Byte-compared, not merely counted — a rewritten file
    with the same row count would slip a count assertion."""
    bank, card, tmp_path = isolate
    _book_card_payment(bank["id"], card["id"])

    before = _store_bytes(tmp_path)
    pending_before = match_review.reviews.pending()

    body = await _check(
        [
            {
                "date": "2026-06-12",
                "description": "GIRO PAYMENT RECEIVED",
                "account_id": card["id"],
                "direction": "credit",
                "amount": PAYMENT,
                "currency": "SGD",
            },
            {
                "date": "2026-06-12",
                "description": "KOPITIAM BREAKFAST",
                "account_id": card["id"],
                "amount": 4.80,
                "currency": "SGD",
            },
        ]
    )
    assert body["flagged"] == 1, body

    assert _store_bytes(tmp_path) == before, "check_duplicates wrote to a store"
    assert match_review.reviews.pending() == pending_before
    assert len(ledger_service.transactions.all()) == 1


# ── 6. per-row keying and error isolation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bad_row_reports_its_own_error_without_losing_the_others(isolate):
    """Unlike ``add_transactions`` (where batch atomicity of the WRITE is the point), one
    malformed row must not cost the caller every other answer — this call writes nothing, so
    there is no partial-batch hazard to protect against."""
    bank, card, _ = isolate
    booked = _book_card_payment(bank["id"], card["id"])

    body = await _check(
        [
            {"ref": "broken", "description": "NO ACCOUNT", "amount": 5.0, "currency": "SGD"},
            {
                "ref": "giro-alert",
                "date": "2026-06-12",
                "description": "GIRO PAYMENT RECEIVED",
                "account_id": card["id"],
                "direction": "credit",
                "amount": PAYMENT,
                "currency": "SGD",
            },
        ]
    )
    bad, good = body["results"]
    assert body["errors"] == 1
    assert bad["ref"] == "broken" and bad["index"] == 0 and "account_id is required" in bad["error"]
    assert good["ref"] == "giro-alert" and good["index"] == 1
    assert good["candidates"][0]["existing_id"] == booked["id"]
    assert body["total"] == 2 and body["showing"] == 2


@pytest.mark.asyncio
async def test_empty_input_is_refused_clearly(isolate):
    body = await _check([])
    assert "non-empty array" in body["error"]
