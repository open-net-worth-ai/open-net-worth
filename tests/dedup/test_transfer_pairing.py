"""Phase 4 — transfer-pairing precision via learned account routes (RFC transaction-dedup-engine).

A debit+credit pair on a route the user has confirmed before keeps full score (auto-eligible with
LLM agreement); an unconfirmed route is capped into the review band so a refund / split-bill
Paynow that merely looks like a transfer is surfaced for review, never silently auto-merged.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, db_isolated):
    """AccountStore/TransactionStore are DB-backed (plan 3 task 4) — db_isolated
    redirects the process-wide DB engine, no per-instance path wiring needed.

    from/to_account_id are now real foreign keys into accounts; several tests here
    seed a confirmed route via txns.append(...) onto synthetic ids ("meridian", "quickpay",
    "wallet", ...). txns.append is wrapped to auto-seed a stub account for any id
    not already present (tests.conftest.seed_stub_accounts) BEFORE each write.
    matching.check/match_batch's own `candidates=`/`rows=` args are never persisted
    (in-memory only), so they need no seeding."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    accounts = AccountStore()
    txns = TransactionStore()

    real_append = txns.append

    def _append_seeding_accounts(data, *args, **kwargs):
        seed_stub_accounts([data])
        return real_append(data, *args, **kwargs)

    monkeypatch.setattr(txns, "append", _append_seeding_accounts)
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


def _ext_fields(external_id):
    """Split a legacy combined ``external_id`` into the (source, ref) pair rows now carry."""
    if external_id is None:
        return {"external_refs": [{"source": None, "ref": None}]}
    if ":" in external_id:
        source, ref = external_id.split(":", 1)
        return {"external_refs": [{"source": source, "ref": ref}]}
    if "@" in external_id:  # a bare email message-id
        return {"external_refs": [{"source": "email", "ref": external_id}]}
    return {"external_refs": [{"source": None, "ref": external_id}]}


def _debit(acct, amt, date="2026-06-03", desc="To QuickPay", ext="csv:d"):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "from_account_id": acct,
        "from_amount": amt,
        "from_currency": "SGD",
        "to_account_id": None,
        "source": "csv_import",
        "id": ext,
        **_ext_fields(ext),
    }


def _credit(acct, amt, date="2026-06-01", desc="Top-up", ext="csv:c"):
    return {
        "kind": "income",
        "date": date,
        "description": desc,
        "to_account_id": acct,
        "to_amount": amt,
        "to_currency": "SGD",
        "from_account_id": None,
        "source": "csv_import",
        "id": ext,
        **_ext_fields(ext),
    }


def test_unknown_route_is_review_band_not_strong(isolate_stores):
    # No confirmed transfers exist → the route is unconfirmed → capped below STRONG.
    res = matching.check(
        _debit("meridian", 50.0), candidates=[_credit("quickpay", 50.0)], profile=matching.FULL
    )
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score < matching.SCORE_STRONG


def test_known_route_keeps_full_score(isolate_stores):
    accounts, txns = isolate_stores
    # Seed a confirmed Meridian Bank→QuickPay transfer so the route is learned.
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "Top-up",
            "from_account_id": "meridian",
            "from_amount": 30.0,
            "from_currency": "SGD",
            "to_account_id": "quickpay",
            "to_amount": 30.0,
            "to_currency": "SGD",
        }
    )
    res = matching.check(
        _debit("meridian", 50.0), candidates=[_credit("quickpay", 50.0)], profile=matching.FULL
    )
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    # Known route → not penalised → reaches the strong band (auto-eligible with LLM agreement).
    assert res.best.score >= matching.SCORE_STRONG


def test_refund_on_unknown_route_not_strong(isolate_stores):
    # A merchant refund (credit to bank) vs the original card charge (debit) on a route never used
    # for transfers — must stay in the review band, never auto-merge.
    debit = _debit("card", 42.0, desc="UNIQLO", ext="csv:charge")
    credit = _credit("bank", 42.0, desc="Refund UNIQLO", ext="csv:refund")
    res = matching.check(debit, candidates=[credit], profile=matching.FULL)
    if res.action == "review" and res.best.klass == matching.MatchClass.TRANSFER_PAIR:
        assert res.best.score < matching.SCORE_STRONG


def test_known_routes_helper_reads_confirmed_transfers(isolate_stores):
    _, txns = isolate_stores
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "x",
            "from_account_id": "a",
            "from_amount": 1.0,
            "from_currency": "SGD",
            "to_account_id": "b",
            "to_amount": 1.0,
            "to_currency": "SGD",
        }
    )
    routes = matching.known_transfer_routes()
    assert frozenset(("a", "b")) in routes


def test_known_routes_skip_deleted_and_merged_transfers(isolate_stores):
    """The routes come from indexed columns, not the JSON payload (decoding every row made a
    large bulk import O(ledger) per call) — the deleted/merged exclusions must still hold."""
    _, txns = isolate_stores
    base = {
        "kind": "transfer",
        "date": "2026-01-01",
        "description": "x",
        "from_amount": 1.0,
        "from_currency": "SGD",
        "to_amount": 1.0,
        "to_currency": "SGD",
    }
    txns.append({**base, "from_account_id": "a", "to_account_id": "b"})
    txns.append({**base, "from_account_id": "c", "to_account_id": "d", "deleted": True})
    txns.append({**base, "from_account_id": "e", "to_account_id": "f", "merged_into": "x"})
    txns.append({**base, "kind": "expense", "from_account_id": "g", "to_account_id": None})
    assert matching.known_transfer_routes() == frozenset({frozenset(("a", "b"))})


# ── #1 (owner decision 2026-07-16): pair FORMATION max gap ≤ TRANSFER_PAIR_MAX_GAP_DAYS ──
#
# A reported case (2026-07-16, a false-pair incident): a PayNow income and
# an unrelated expense a week apart on different accounts were proposed as a transfer pair —
# they are not. The gate applies to pair FORMATION (two single-leg rows proposed as a NEW pair)
# AND — since 2026-07-17 — to transfer_leg_echo with the SAME constant: both make the same
# physical claim ("these two records are one movement"), so both obey the same settlement
# window. The original echo exemption ("a leg legitimately settles days after booking") was
# reversed by a second reported case: a family member's round +2,000,000 VND gift (06-21)
# echo-linked to an unrelated Compass→Riverside Bank 2,000,000 VND transfer dated 06-28 — a
# 7-day reach on a round recurring amount. Settlement lag is real at 1–3 days, not 7.


def test_formation_seven_day_gap_never_pairs_fail_first(isolate_stores):
    """Fail-first repro (a): income +1,500.00 dated 2026-06-27 (note 'PayNow transfer from
    Nguyen Van A', categorized) vs expense -1,500.00 dated 2026-07-04 on another account — a
    7-day gap. Must NOT classify as transfer_pair at all (fails on pre-fix code, which forms the
    pair capped into review; passes once TRANSFER_PAIR_MAX_GAP_DAYS gates formation)."""
    income_row = _credit(
        "meridian",
        1500.00,
        date="2026-06-27",
        desc="PayNow transfer from Nguyen Van A",
        ext="csv:in",
    )
    income_row["category"] = "cat_income"
    expense_row = _debit("cash", 1500.00, date="2026-07-04", desc="ATM withdrawal", ext="csv:out")
    res = matching.check(expense_row, candidates=[income_row], profile=matching.FULL)
    assert res.action == "create", (
        f"a 7-day-apart pair must never classify as transfer_pair, got action={res.action!r} "
        f"best={res.best!r}"
    )


def test_echo_three_day_gap_still_matches(isolate_stores):
    """Repro (b): an ECHO case at a 3-day gap (existing kind=transfer dated 06-26, a statement
    credit row dated 06-29, same amount) must STILL match transfer_leg_echo — 3 days is inside
    the shared settlement window (the cap is inclusive; the exemption itself was reversed
    2026-07-17, see the fourteenth ADR addendum)."""
    transfer_txn = {
        "id": "t-echo-1",
        "kind": "transfer",
        "date": "2026-06-26",
        "description": "Meridian Bank to QuickPay",
        "from_account_id": "meridian",
        "from_amount": 500.0,
        "from_currency": "SGD",
        "to_account_id": "quickpay",
        "to_amount": 500.0,
        "to_currency": "SGD",
        "external_refs": [{"source": "manual", "ref": "t-echo-1"}],
        "source": "manual",
    }
    statement_credit = _credit(
        "quickpay", 500.0, date="2026-06-29", desc="Top-up received", ext="csv:echo-credit"
    )
    res = matching.check(statement_credit, candidates=[transfer_txn], profile=matching.FULL)
    assert res.action == "review"
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_LEG_ECHO


def test_echo_seven_day_gap_never_matches(isolate_stores):
    """Fail-first reported case (2026-07-17): statement income +2,000,000 VND into Riverside Bank
    dated 06-21 (a relative's gift, 'NGUYEN THI B chuyen') vs an existing transfer whose to-leg is
    also 2,000,000 VND, dated 06-28 — 7 days apart. Same account, same round amount, existing is
    a transfer: pre-fix this echo-flagged with no date gate (score 0.737, review band live).
    Must NOT flag at all. NOTE: the reported case's candidate was cross-currency (Compass SGD→VND);
    this fixture is same-currency because the isolated test env has no FX rates, so a
    cross-currency candidate is dropped by the initial amount gate BEFORE echo classification —
    it would pass pre-fix for the wrong reason. The date gate under test is currency-agnostic."""
    vnd_transfer = {
        "id": "t-vnd-1",
        "kind": "transfer",
        "date": "2026-06-28",
        "description": "Transfer to Riverside Bank",
        "from_account_id": "pocketpay",
        "from_amount": 2000000.0,
        "from_currency": "VND",
        "to_account_id": "riverside",
        "to_amount": 2000000.0,
        "to_currency": "VND",
        "external_refs": [{"source": "agent", "ref": "vnd-1"}],
        "source": "ai",
    }
    statement_credit = _credit(
        "riverside",
        2000000.0,
        date="2026-06-21",
        desc="6172IBT1kCYE2DIP.NGUYEN THI B chuyen",
        ext="xlsx:relative",
    )
    statement_credit["to_currency"] = "VND"
    res = matching.check(statement_credit, candidates=[vnd_transfer], profile=matching.FULL)
    assert res.action == "create", (
        f"a 7-day-apart leg echo must never flag, got action={res.action!r} best={res.best!r}"
    )
    assert res.best is None


def test_echo_gap_boundary_inclusive(isolate_stores):
    """Exactly TRANSFER_PAIR_MAX_GAP_DAYS (3) still echo-matches — the cap is inclusive,
    mirroring the formation boundary (Friday send → Monday statement posting)."""
    transfer_txn = {
        "id": "t-echo-b",
        "kind": "transfer",
        "date": "2026-06-05",
        "description": "Meridian Bank to QuickPay",
        "from_account_id": "meridian",
        "from_amount": 500.0,
        "from_currency": "SGD",
        "to_account_id": "quickpay",
        "to_amount": 500.0,
        "to_currency": "SGD",
        "external_refs": [{"source": "manual", "ref": "t-echo-b"}],
        "source": "manual",
    }
    statement_credit = _credit(
        "quickpay", 500.0, date="2026-06-08", desc="Top-up received", ext="csv:eb"
    )
    res = matching.check(statement_credit, candidates=[transfer_txn], profile=matching.FULL)
    assert res.action == "review"
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_LEG_ECHO


# ── Echo ranking + same-side exclusivity (reported cases, 2026-07-17 evening) ──────────
# Two +2,000,000 VND statement credits (06-01, 06-04) each had an exact-legged same-day
# Compass→Riverside Bank transfer, yet BOTH displayed the 06-01 transfer: (a) echo fidelity was computed
# from the base-converted PRIMARY legs (FX drift on the SGD from-leg outranked a same-day
# exact VND leg in the (−score, fidelity, date_gap) key — recorded pre-fix: fid 0.013868 vs
# 0.016241, both rows assigned f7fa8efd); (b) echo bypassed the 1:1 claim assignment
# entirely, so two same-side rows could share one target. Fixed: fidelity from the ECHOED
# leg's native amounts; echo competes in claims on SIDE-qualified targets (debit+credit of
# one internal move still legitimately share).


def _vnd_credit(date, amt, ext):
    return {
        "kind": "income",
        "date": date,
        "description": f"VIETINBANK CK NGUOI GUI {ext}",
        "to_account_id": "riverside",
        "to_amount": amt,
        "to_currency": "VND",
        "from_account_id": None,
        "source": "csv_import",
        "id": ext,
        **_ext_fields(ext),
    }


def _compass_transfer(tid, date, sgd, vnd):
    return {
        "id": tid,
        "kind": "transfer",
        "date": date,
        "description": None,
        "from_account_id": "compass_sgd",
        "from_amount": sgd,
        "from_currency": "SGD",
        "to_account_id": "riverside",
        "to_amount": vnd,
        "to_currency": "VND",
        "external_refs": [{"source": None, "ref": None}],
        "source": "ai",
    }


_RATES = {"SGD": 1.0, "VND": 1.0 / 20100.0}


def test_echo_fidelity_uses_echoed_leg_not_fx_primary(isolate_stores):
    """Cross-currency echo: the same-day transfer (exact echoed VND leg) must outrank a
    3-day-away one, even when FX drift makes the wrong one's SGD from-leg look 'closer'."""
    t_0601 = _compass_transfer("t-0601", "2026-06-01", 99.51, 2000000.0)
    t_0604 = _compass_transfer("t-0604", "2026-06-04", 99.75, 2000000.0)
    row = _vnd_credit("2026-06-04", 2000000.0, "MS1917")
    res = matching.check(row, candidates=[t_0601, t_0604], profile=matching.FULL, rates=_RATES)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_LEG_ECHO
    assert res.best.txn["id"] == "t-0604", (
        f"same-day exact-leg transfer must rank first, got {res.best.txn['id']}"
    )


def test_echo_same_side_rows_never_share_one_target(isolate_stores):
    """Batch door: two credits, two transfers → each row assigns its own-date transfer;
    no shared target (pre-fix: both took the fidelity-favored one via the bypass)."""
    t_0601 = _compass_transfer("t-0601", "2026-06-01", 99.51, 2000000.0)
    t_0604 = _compass_transfer("t-0604", "2026-06-04", 99.75, 2000000.0)
    rows = [
        _vnd_credit("2026-06-01", 2000000.0, "MS1911"),
        _vnd_credit("2026-06-04", 2000000.0, "MS1917"),
    ]
    ctx = matching.BatchContext(complete=False, window=None, account_ids=frozenset({"riverside"}))
    rms = matching.match_batch(rows, context=ctx, candidates=[t_0601, t_0604], rates=_RATES)
    assigned = [rm.assigned.txn["id"] if rm.assigned else None for rm in rms]
    assert assigned == ["t-0601", "t-0604"], (
        f"each row must take its own-date transfer, got {assigned}"
    )


def test_echo_one_transfer_two_same_side_rows_one_wins(isolate_stores):
    """One booked transfer, two same-side credits → exactly ONE row links it; the loser
    gets no echo target (imports as its own row) instead of double-merging."""
    t = _compass_transfer("t-only", "2026-06-02", 99.51, 2000000.0)
    rows = [_vnd_credit("2026-06-02", 2000000.0, "a"), _vnd_credit("2026-06-04", 2000000.0, "b")]
    ctx = matching.BatchContext(complete=False, window=None, account_ids=frozenset({"riverside"}))
    rms = matching.match_batch(rows, context=ctx, candidates=[t], rates=_RATES)
    assigned = [rm.assigned.txn["id"] if rm.assigned else None for rm in rms]
    assert assigned.count("t-only") == 1, f"one leg, one claimant — got {assigned}"
    assert assigned[0] == "t-only", "the same-day row must win the contest"


def test_echo_debit_and_credit_legs_still_share_one_transfer(isolate_stores):
    """A statement listing BOTH legs of one internal move (debit out of meridian, credit into
    quickpay) legitimately references the SAME booked transfer — side-qualified targets keep
    the both-legs sharing legal."""
    t = {
        "id": "t-int",
        "kind": "transfer",
        "date": "2026-06-10",
        "description": "Meridian Bank to QuickPay",
        "from_account_id": "meridian",
        "from_amount": 500.0,
        "from_currency": "SGD",
        "to_account_id": "quickpay",
        "to_amount": 500.0,
        "to_currency": "SGD",
        "external_refs": [{"source": None, "ref": None}],
        "source": "manual",
    }
    debit_row = _debit(
        "meridian", 500.0, date="2026-06-10", desc="Fund transfer out", ext="csv:d10"
    )
    credit_row = _credit(
        "quickpay", 500.0, date="2026-06-10", desc="Top-up received", ext="csv:c10"
    )
    ctx = matching.BatchContext(
        complete=False, window=None, account_ids=frozenset({"meridian", "quickpay"})
    )
    rms = matching.match_batch([debit_row, credit_row], context=ctx, candidates=[t])
    assigned = [rm.assigned.txn["id"] if rm.assigned else None for rm in rms]
    assert assigned == ["t-int", "t-int"], f"both legs may share the one transfer, got {assigned}"


def test_formation_three_day_gap_boundary_still_pairs(isolate_stores):
    """Boundary: exactly TRANSFER_PAIR_MAX_GAP_DAYS (3 days — the Friday→Monday shape, raised
    from 2 in the 2026-07-17 generality audit) still forms a transfer_pair candidate — the cap
    is inclusive. Dates are a real Fri→Mon: money leaves Friday 06-05, lands Monday 06-08."""
    debit = _debit("meridian", 200.0, date="2026-06-05", ext="csv:bd")
    credit = _credit("quickpay", 200.0, date="2026-06-08", ext="csv:bc")
    res = matching.check(credit, candidates=[debit], profile=matching.FULL)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR


def test_formation_four_day_gap_does_not_pair(isolate_stores):
    """One day past the cap: no transfer_pair candidate forms (and nothing else classifies this
    pair either, since the two legs are opposite kinds)."""
    debit = _debit("meridian", 200.0, date="2026-06-07", ext="csv:td")
    credit = _credit("quickpay", 200.0, date="2026-06-03", ext="csv:tc")
    res = matching.check(debit, candidates=[credit], profile=matching.FULL)
    assert res.action == "create"


# ── #2 (owner decision 2026-07-16): a categorized single-leg row never scores STRONG ──
#
# Rationale (owner): a categorized income is a deliberate classification of external money; a
# mis-booked internal leg is typically raw/uncategorized. Re-kinding a categorized row must
# always get human eyes — so it may still ASK (review), never auto-link.


def test_categorized_leg_caps_review_even_on_known_route(isolate_stores):
    _, txns = isolate_stores
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "seed route",
            "from_account_id": "meridian",
            "from_amount": 30.0,
            "from_currency": "SGD",
            "to_account_id": "quickpay",
            "to_amount": 30.0,
            "to_currency": "SGD",
        }
    )
    debit = _debit("meridian", 50.0, date="2026-06-02", ext="csv:catd")
    credit = _credit("quickpay", 50.0, date="2026-06-01", ext="csv:catc")
    credit["category"] = "cat_salary"
    res = matching.check(debit, candidates=[credit], profile=matching.FULL)
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score < matching.SCORE_STRONG
    assert "categorized leg" in " ".join(res.best.reasons).lower()


def test_uncategorized_pair_one_day_gap_known_route_reaches_strong(isolate_stores):
    """Existing behavior preserved: an uncategorized pair on a confirmed route still reaches
    strong (auto-eligible with LLM agreement) at a 1-day gap."""
    _, txns = isolate_stores
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "seed route",
            "from_account_id": "meridian",
            "from_amount": 30.0,
            "from_currency": "SGD",
            "to_account_id": "quickpay",
            "to_amount": 30.0,
            "to_currency": "SGD",
        }
    )
    debit = _debit("meridian", 50.0, date="2026-06-02", ext="csv:okd")
    credit = _credit("quickpay", 50.0, date="2026-06-01", ext="csv:okc")
    res = matching.check(debit, candidates=[credit], profile=matching.FULL)
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score >= matching.SCORE_STRONG


# ── #2b (owner decision 2026-07-29): a BOOKED THIRD-PARTY ledger row never pairs at all ──
#
# The review cap above was too weak. Two reported cases (2026-07-29):
#   (1) a genuine wallet cash-out landing in the bank was offered as the counterparty of a
#       ride-hailing expense — a completed taxi payment, categorized and payee'd;
#   (2) a card LOYALTY CASHBACK credit — which has no second leg anywhere in existence — was
#       offered as the counterparty of another such ride payment.
# Both stayed in review (the cap worked), but the engine should not even ASK: a ledger row
# carrying BOTH a resolved payee entity AND a category is a completed purchase from a third
# party, and cannot be a leg of the owner moving money between their OWN accounts. Absorbing
# one into a transfer understates spend AND drops it off the "In your ledger, not on this
# statement" panel — the surface where the owner would have noticed.
#
# The veto is DEFINITIONAL and CANDIDATE-SIDE only: payee_id ∧ category, on the ledger row.
# Every weaker shape (one signal, or the signal only on the incoming statement row) keeps the
# review cap — see the three weak-shape pins below. Statement drafts never carry a payee_id
# (``statements/importer`` passes a payee NAME, resolved into an id at the write choke), so a
# batch sibling row can never be vetoed by this rule.


def _booked_third_party_expense(acct, amt, date, ext):
    """A ledger row in the shape the veto targets: a completed single-leg purchase carrying
    BOTH a resolved payee entity and a category."""
    row = _debit(acct, amt, date=date, desc="Ride payment", ext=ext)
    row |= {"category": "cat_transport", "payee_id": "pay_ride_co"}
    return row


def _seed_route(txns, src, dst):
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "seed route",
            "from_account_id": src,
            "from_amount": 30.0,
            "from_currency": "SGD",
            "to_account_id": dst,
            "to_amount": 30.0,
            "to_currency": "SGD",
        }
    )


def test_booked_third_party_candidate_never_forms_a_transfer_pair(isolate_stores):
    """Repro (1): a wallet cash-out credit lands in the bank on 06-02; a categorized, payee'd
    ride expense of the same amount sits on another account on 06-01. Pre-fix this formed a
    review-capped TRANSFER_PAIR; it must form NO candidate at all, so the statement row books
    as its own transaction and the ride expense stays visible as uncovered."""
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:r5")
    expense = _booked_third_party_expense("wallet", 99.0, "2026-06-01", "csv:ride-1")
    res = matching.check(credit, candidates=[expense], profile=matching.FULL)
    assert res.best is None, (
        "a payee'd AND categorized ledger row is a completed third-party purchase — it must "
        f"never be offered as a transfer leg, got {res.best!r}"
    )
    assert res.action == "create"
    # The degrade direction, pinned explicitly (2026-07-29 security review: a tightening must
    # not manufacture a duplicate or redirect a merge). Even the most permissive policy WITH an
    # agreeing LLM cannot resurrect a vetoed pair — the row books as its own transaction.
    assert matching.combine(res, "auto_high", llm_verdict="pair") == "create"


def test_booked_third_party_veto_leaves_the_ledger_entry_uncovered(isolate_stores):
    """The batch door — the path a statement import actually takes. No assignment means the
    row carries no ``existing_id`` flag, which is exactly what ``ledger_diff._covered_ids``
    subtracts: the ride expense stays under "In your ledger, not on this statement"."""
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:b5")
    expense = _booked_third_party_expense("wallet", 99.0, "2026-06-01", "csv:ride-b")
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"bank"})
    )
    rms = matching.match_batch([credit], context=ctx, candidates=[expense])
    assert [rm.assigned for rm in rms] == [None]
    assert [rm.candidates for rm in rms] == [[]]


def test_booked_third_party_veto_beats_a_confirmed_route(isolate_stores):
    """Repro (2), the cashback shape — a credit with no second leg in existence. Stacked with
    the STRONGEST positive evidence the engine has (a confirmed route + exact amount + same
    day) to pin that the veto is definitional, not another score cap."""
    _, txns = isolate_stores
    _seed_route(txns, "wallet", "bank")
    cashback = _credit("bank", 10.0, date="2026-06-20", desc="Card cashback", ext="xlsx:r20")
    expense = _booked_third_party_expense("wallet", 10.0, "2026-06-20", "csv:ride-2")
    res = matching.check(cashback, candidates=[expense], profile=matching.FULL)
    assert res.best is None, f"the veto must outrank a confirmed route, got {res.best!r}"
    assert res.action == "create"


def test_raw_opposite_leg_still_forms_a_transfer_pair(isolate_stores):
    """CONTROL — genuine transfer matching is untouched: the same geometry with a raw,
    uncategorized, payee-less ledger leg still pairs."""
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:ctl-c")
    raw_leg = _debit("wallet", 99.0, date="2026-06-01", desc="Cash out", ext="csv:ctl-d")
    res = matching.check(credit, candidates=[raw_leg], profile=matching.FULL)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR


def test_categorized_candidate_without_a_payee_still_only_review_caps(isolate_stores):
    """Weak shape — category but no resolved payee: still ASKS (the 2026-07-16 cap), never
    vetoed. Only the conjunction is definitional."""
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:w1-c")
    leg = _debit("wallet", 99.0, date="2026-06-01", desc="Cash out", ext="csv:w1-d")
    leg["category"] = "cat_transport"
    res = matching.check(credit, candidates=[leg], profile=matching.FULL)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score < matching.SCORE_STRONG
    assert "categorized leg" in " ".join(res.best.reasons).lower()


def test_payee_without_a_category_is_neither_a_veto_nor_a_cap(isolate_stores):
    """Weak shape — a resolved payee but NO category. ``payee_id`` alone proves nothing: the
    write choke MINTS a payee from the raw bank descriptor for every expense/income row, so an
    untouched imported leg carries one. On a confirmed route this still reaches STRONG."""
    _, txns = isolate_stores
    _seed_route(txns, "wallet", "bank")
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:w2-c")
    leg = _debit("wallet", 99.0, date="2026-06-01", desc="Cash out", ext="csv:w2-d")
    leg["payee_id"] = "pay_wallet"
    res = matching.check(credit, candidates=[leg], profile=matching.FULL)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score >= matching.SCORE_STRONG


def test_signal_only_on_the_incoming_row_still_only_review_caps(isolate_stores):
    """Weak shape — both signals, but on the INCOMING row. The veto reads the LEDGER side only:
    an incoming row is a statement line whose payee/category are a dry-run preview, not a
    deliberate classification the owner made."""
    credit = _credit("bank", 99.0, date="2026-06-02", desc="Wallet cash-out", ext="xlsx:w3-c")
    credit |= {"category": "cat_transport", "payee_id": "pay_ride_co"}
    leg = _debit("wallet", 99.0, date="2026-06-01", desc="Cash out", ext="csv:w3-d")
    res = matching.check(credit, candidates=[leg], profile=matching.FULL)
    assert res.best is not None
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.best.score < matching.SCORE_STRONG
    assert "categorized leg" in " ".join(res.best.reasons).lower()
