"""Parity tests — import PREVIEW advisory flags must agree with the post-commit
review queue produced by ``match_review.review_created_rows``.

F25a regression: the old bespoke detector (1% band) diverged from the engine (5% FX band)
so a 3% FX-drift duplicate showed CLEAN in preview, then appeared in the review queue
post-commit — eroding trust.  The fix delegates the preview's duplicate/transfer detection
to the same engine (``matching.check``, SCAN profile) so both paths are identical in
behavior.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import import_anomalies as anom
from backend.services import ledger as ledger_service
from backend.services import match_review
from backend.services.import_common import RowResult


def _arrange_create(data, **kw):
    """Arrangement helper: build a book that ALREADY contains the duplicate under test.

    These tests exercise the POST-write dedup surfaces (review queue, merge, judge), so their
    setup must be able to write rows that the PRE-write duplicate guard refuses. ``force=True``
    is that sanctioned escape hatch; the guard itself is covered by its own regression file.
    """
    from backend.services import ledger as _l

    return _l.create_transaction(data, allow_duplicate=True, **kw)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
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
    return accounts, txns


@pytest.fixture
def meridian_acct(isolate):
    accounts, _ = isolate
    return accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )


def _ext_fields(external_id):
    """Split a legacy combined ``external_id`` into the (source, ref) pair drafts now carry."""
    if external_id is None:
        return {"external_refs": [{"source": None, "ref": None}]}
    if ":" in external_id:
        source, ref = external_id.split(":", 1)
        return {"external_refs": [{"source": source, "ref": ref}]}
    if "@" in external_id:  # a bare email message-id
        return {"external_refs": [{"source": "email", "ref": external_id}]}
    return {"external_refs": [{"source": None, "ref": external_id}]}


def _ext_pair(d):
    """Read a row/draft's (source, ref) identity pair."""
    return (primary_source(d), primary_ref(d))


def _expense_draft(acct_id, *, date, amount, desc, ext, ccy="SGD"):
    """Build a draft RowResult without persisting it (preview shape)."""
    return RowResult(
        line=1,
        draft={
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": acct_id,
            "from_amount": amount,
            "from_currency": ccy,
            "source": "csv_import",
            **_ext_fields(ext),
        },
    )


def _income_draft(acct_id, *, date, amount, desc, ext, ccy="SGD"):
    return RowResult(
        line=1,
        draft={
            "date": date,
            "kind": "income",
            "description": desc,
            "to_account_id": acct_id,
            "to_amount": amount,
            "to_currency": ccy,
            "source": "csv_import",
            **_ext_fields(ext),
        },
    )


# ── PARITY: preview flags == post-commit review queue ─────────────────────────────────


def test_parity_near_dup_and_clean(meridian_acct):
    """Core parity regression: the set of rows the PREVIEW flags as near_duplicate/transfer_pair
    must equal the set that ``review_created_rows`` enqueues post-commit."""
    acct_id = meridian_acct["id"]

    # Existing ledger row (email receipt, different external_id namespace than csv:).
    _arrange_create(
        {
            "date": "2026-05-10",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": acct_id,
            "from_amount": 25.50,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "grab1"}],
            "source": "email",
        }
    )

    # Batch to import: one near-dup (same charge, different source), one clean row.
    r_dup = _expense_draft(
        acct_id, date="2026-05-11", amount=25.50, desc="GRAB* SG 491", ext="csv:grab1"
    )
    r_clean = _expense_draft(
        acct_id, date="2026-05-15", amount=9.90, desc="Fresh Coffee", ext="csv:fresh1"
    )

    # --- PREVIEW pass ---
    anom.detect_anomalies([r_dup, r_clean])
    preview_flagged_exts = {
        _ext_pair(r.draft)
        for r in [r_dup, r_clean]
        if r.flags and any(f["type"] in ("near_duplicate", "transfer_pair") for f in r.flags)
    }

    # --- COMMIT pass: create rows then run the review pass ---
    created_dup = _arrange_create(r_dup.draft)
    created_clean = _arrange_create(r_clean.draft)
    match_review.review_created_rows([created_dup, created_clean])

    # Which incoming rows were enqueued?
    queued_ids = {item["incoming_id"] for item in match_review.reviews.pending()}
    queued_exts = {_ext_pair(t) for t in [created_dup, created_clean] if t.get("id") in queued_ids}

    # PARITY: same rows flagged in preview and queued in post-commit review.
    assert preview_flagged_exts == queued_exts, (
        f"preview flagged {preview_flagged_exts!r} but post-commit queued {queued_exts!r}"
    )
    assert ("csv", "grab1") in preview_flagged_exts
    assert ("csv", "fresh1") not in preview_flagged_exts


def test_parity_transfer_pair(meridian_acct, isolate):
    """A draft income leg that forms a transfer pair with an existing expense in the ledger
    is flagged in preview (transfer_pair) and queued post-commit — same set, parity holds."""
    accounts, _ = isolate
    quickpay = accounts.create(
        {"name": "QuickPay", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    meridian_id, quickpay_id = meridian_acct["id"], quickpay["id"]

    # Existing debit (money left Meridian Bank — an expense leg).
    _arrange_create(
        {
            "date": "2026-05-20",
            "kind": "expense",
            "description": "To QuickPay",
            "from_account_id": meridian_id,
            "from_amount": 50.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "meridian_debit"}],
            "source": "csv_import",
        }
    )

    # Incoming credit (money arrived in QuickPay — an income leg). Different feed/account.
    r_credit = _income_draft(
        quickpay_id,
        date="2026-05-20",
        amount=50.0,
        desc="Top-up from Meridian Bank",
        ext="csv:quickpay_credit",
    )
    r_clean = _expense_draft(
        meridian_id, date="2026-05-25", amount=12.0, desc="Unrelated", ext="csv:unrelated"
    )

    # --- PREVIEW ---
    anom.detect_anomalies([r_credit, r_clean])
    preview_flagged_exts = {
        _ext_pair(r.draft)
        for r in [r_credit, r_clean]
        if r.flags and any(f["type"] in ("near_duplicate", "transfer_pair") for f in r.flags)
    }

    # --- COMMIT ---
    created_credit = _arrange_create(r_credit.draft)
    created_clean = _arrange_create(r_clean.draft)
    match_review.review_created_rows([created_credit, created_clean])
    queued_ids = {item["incoming_id"] for item in match_review.reviews.pending()}
    queued_exts = {
        _ext_pair(t) for t in [created_credit, created_clean] if t.get("id") in queued_ids
    }

    assert preview_flagged_exts == queued_exts, (
        f"preview flagged {preview_flagged_exts!r} but post-commit queued {queued_exts!r}"
    )
    assert ("csv", "quickpay_credit") in preview_flagged_exts


# ── 3% FX-drift regression (F25a root cause) ──────────────────────────────────────────


def test_fx_drift_dup_same_currency_not_flagged(meridian_acct, isolate, fx_rates):
    """F07 precision: two USD charges at 3% drift are NOT flagged.  Same native currency
    → native comparison at 1% band → 3% exceeds the band → correctly NOT a near-duplicate.

    Pre-F07, both USD rows were treated as 'foreign' (USD ≠ SGD base) and scored against the
    wide 5% FX band, causing false positives.  Post-F07, same-ccy pairs skip FX conversion and
    compare native amounts directly — only genuinely cross-currency pairs get the wider band."""
    accounts, _ = isolate
    usd_acct = accounts.create(
        {"name": "USD Card", "kind": "asset", "type": "credit", "currency": "USD"}
    )
    acct_id = usd_acct["id"]

    # Email receipt at capture rate: USD 100.
    _arrange_create(
        {
            "date": "2026-05-01",
            "kind": "expense",
            "description": "Anthropic",
            "from_account_id": acct_id,
            "from_amount": 100.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": "anthropic1"}],
            "source": "email",
        }
    )

    # Statement line: 3% higher, SAME currency — outside the 1% native band → no flag.
    r = _expense_draft(
        acct_id, date="2026-05-03", amount=103.0, desc="ANTHROPIC", ext="csv:anthropic2", ccy="USD"
    )
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 0, (
        "Same-currency 3% drift must NOT be flagged — post-F07 uses tight 1% native band"
    )
    assert r.flags == []


def test_fx_drift_cross_currency_flagged_in_preview(meridian_acct, isolate, monkeypatch):
    """F07 + F25a parity: a genuinely cross-currency pair (USD email receipt vs SGD statement
    line) within the 5% FX band IS flagged in preview.  Rates are injected so F15 (no real
    rate → no classification) does not suppress the match.

    This is the F25a root cause in its correct form: the preview delegates to the same engine
    so cross-currency duplicates surface before commit, not only in the post-commit queue."""
    from backend.services import fx_cache

    accounts, _ = isolate
    usd_acct = accounts.create(
        {"name": "USD Card", "kind": "asset", "type": "credit", "currency": "USD"}
    )
    acct_id = usd_acct["id"]

    # Inject a real USD/SGD rate so F15 allows the cross-currency classification.
    # USD 100 × 1.345 = SGD 134.5; statement settled at SGD 138 ≈ 2.6% drift — within 5%.
    # Patch BOTH seams: matching reads services.fx_cache.load_fx_cache directly, while
    # create_transaction's base-stamping resolves via core.fx._load_rates (a distinct seam —
    # see CLAUDE.md). An email-booked row in a prior test promotes an email snapshot whose
    # import chain can leave the core.fx loader resolving base-only, so inject there too.
    monkeypatch.setattr(fx_cache, "load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.345})
    import backend.core.fx as _core_fx

    monkeypatch.setattr(_core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "USD": 1.345})

    # Email receipt in USD.
    _arrange_create(
        {
            "date": "2026-05-01",
            "kind": "expense",
            "description": "Anthropic",
            "from_account_id": acct_id,
            "from_amount": 100.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": "anthropic1"}],
            "source": "email",
        }
    )

    # Statement line in SGD — genuinely cross-currency, 2.6% drift inside the 5% FX band.
    r = _expense_draft(
        acct_id, date="2026-05-03", amount=138.0, desc="ANTHROPIC", ext="csv:anthropic2", ccy="SGD"
    )
    summary = anom.detect_anomalies([r])
    assert summary["near_duplicate"] == 1, (
        "Cross-currency pair within 5% FX band must be flagged in preview (F07 + F25a)"
    )
    assert any(f["type"] == "near_duplicate" for f in r.flags)


def test_fx_drift_dup_old_band_would_miss(meridian_acct):
    """Document the amounts_match band behavior: 1% band misses 3% drift, 5% catches it.
    Post-F07, same-ccy pairs use native comparison (1% band), so this 3% case is correctly
    not flagged as a near-duplicate between two USD rows."""
    from backend.services import matching

    # Amounts 100 and 103: drift = 3%.
    assert not matching.amounts_match(100.0, 103.0, rel_tol=0.01), "1% band misses 3% drift"
    assert matching.amounts_match(100.0, 103.0, rel_tol=0.05), "5% band catches 3% drift"


# ── Clean batch produces zero advisory flags ──────────────────────────────────────────


def test_clean_batch_no_flags(meridian_acct):
    """A batch against an empty ledger produces zero advisory flags — never fabricated."""
    acct_id = meridian_acct["id"]
    r1 = _expense_draft(acct_id, date="2026-06-01", amount=10.0, desc="New Cafe", ext="csv:n1")
    r2 = _expense_draft(acct_id, date="2026-06-02", amount=20.0, desc="New Gym", ext="csv:n2")
    summary = anom.detect_anomalies([r1, r2])
    assert summary["near_duplicate"] == 0
    assert summary["transfer_pair"] == 0
    assert summary["amount_anomaly"] == 0
    assert summary["flagged_rows"] == 0
    assert r1.flags == [] and r2.flags == []


# ── Non-duplicate anomaly type (amount_anomaly) still works ───────────────────────────


def test_amount_anomaly_still_works(meridian_acct):
    """The amount_anomaly detector (non-duplicate type) is unchanged and still fires."""
    acct_id = meridian_acct["id"]
    # Four historical ~$12 charges from the same payee.
    for i, day in enumerate(("01", "02", "03", "04"), start=1):
        _arrange_create(
            {
                "date": f"2026-03-{day}",
                "kind": "expense",
                "description": "Streamflix",
                "from_account_id": acct_id,
                "from_amount": 12.0,
                "from_currency": "SGD",
                "external_refs": [{"source": "manual", "ref": f"sf{i}"}],
            }
        )
    # A $1,200 charge — 100× the median, far outside normal range.
    r = _expense_draft(
        acct_id, date="2026-04-01", amount=1200.0, desc="Streamflix", ext="csv:sfbig"
    )
    summary = anom.detect_anomalies([r])
    assert summary["amount_anomaly"] == 1
    assert any(f["type"] == "amount_anomaly" for f in r.flags)
