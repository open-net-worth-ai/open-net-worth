"""Balance-chain completeness for tabular statements (batch-aware-dedup follow-up).

Tabular exports (csv/xlsx) never declare the opening/closing pair the reconcile identity
needs, so their reconcile sat at ``unknown`` forever → ``BatchContext(complete=False)`` →
books-close promotion never fired → every wordless unique exact same-day pair landed in
"Needs you" (a live record ``stmt_ab4a65dc0000``: 06-22 −650,000 VND "Example Diner",
unique, exact, same-day, different sources — sat at review). The fix proves the statement
against its OWN per-row running-balance column: an unbroken chain is per-link proof that no
row is missing between first and last — deterministic, label-free, zero LLM.

Pure-function matrix + the pipeline acceptance in miniature: a chain-proven tabular preview
promotes its unique exact pair to STRONG; the same file with a mid-file row deleted (chain
broken) keeps it at review; a pre-fix CACHED record self-heals on its next preview with zero
LLM and zero re-parse.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import guards
from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta
from tests.conftest import drop_stored_review

# ── the sample-shape fixture: 45 rows, opening 1,040,000 → closing 456,000 ────────


def _row(date: str, amount: float, direction: str, balance, **kw) -> CanonicalRow:
    return CanonicalRow(date=date, direction=direction, amount=amount, balance=balance, **kw)


def _owner_shape_rows() -> list[CanonicalRow]:
    """A 45-row replica of a real Riverside Bank book shape: opening 1,040,000 →
    closing 456,000 VND, mixed IN/OUT (including the −650,000 "Example Diner" probe row),
    ascending dates 06-01 → 06-29 with same-day interleaves, every row carrying the running
    balance. Deterministic (fixed seed)."""
    rng = random.Random(20260622)
    opening, closing = 1_040_600.0, 455_900.0
    amounts = [-600_700.0]
    for _ in range(43):
        mag = float(rng.randrange(5_000, 900_000, 1_000))
        amounts.append(mag if rng.random() < 0.55 else -mag)
    amounts.append(closing - opening - sum(amounts))
    assert all(a != 0 for a in amounts)
    rng.shuffle(amounts)
    dates = sorted(f"2026-06-{rng.randint(1, 29):02d}" for _ in range(45))
    dates[0], dates[-1] = "2026-06-01", "2026-06-29"
    rows, bal = [], opening
    for date, amt in zip(dates, amounts):
        bal = round(bal + amt, 2)
        rows.append(
            _row(
                date,
                abs(amt),
                "IN" if amt > 0 else "OUT",
                bal,
                description=f"GD {rng.randrange(10**9)}",
            )
        )
    return rows


def test_owner_shape_chain_reconciles_ok():
    verdict = guards.reconcile_from_balance_chain(_owner_shape_rows())
    assert verdict is not None
    assert verdict["status"] == "ok"
    assert verdict["method"] == "balance_chain"
    assert verdict["derived"]["opening_balance"] == 1_040_600.0
    assert verdict["derived"]["closing_balance"] == 455_900.0
    assert verdict["derived"]["period_start"] == "2026-06-01"
    assert verdict["derived"]["period_end"] == "2026-06-29"
    assert verdict["expected"] == verdict["actual"] == -584_700.0


def test_missing_row_mid_file_breaks_the_chain():
    rows = _owner_shape_rows()
    del rows[20]  # a real movement vanished — the claim "nothing is missing" must die
    assert guards.reconcile_from_balance_chain(rows) is None


def test_altered_amount_breaks_the_chain():
    rows = _owner_shape_rows()
    rows[10] = replace(rows[10], amount=rows[10].amount + 1_000.0)
    assert guards.reconcile_from_balance_chain(rows) is None


def test_sign_flipped_file_reconciles_ok_if_flipped():
    flipped = [
        replace(r, direction="OUT" if r.direction == "IN" else "IN") for r in _owner_shape_rows()
    ]
    verdict = guards.reconcile_from_balance_chain(flipped)
    assert verdict is not None and verdict["status"] == "ok_if_flipped"
    assert verdict["derived"]["opening_balance"] == 1_040_600.0
    assert verdict["derived"]["closing_balance"] == 455_900.0


def test_newest_first_file_reconciles_ok():
    verdict = guards.reconcile_from_balance_chain(list(reversed(_owner_shape_rows())))
    assert verdict is not None and verdict["status"] == "ok"
    assert verdict["derived"]["opening_balance"] == 1_040_600.0
    assert verdict["derived"]["closing_balance"] == 455_900.0
    assert verdict["derived"]["period_start"] == "2026-06-01"
    assert verdict["derived"]["period_end"] == "2026-06-29"


def test_partial_balance_column_proves_nothing():
    rows = _owner_shape_rows()
    rows[7] = replace(rows[7], balance=None)
    assert guards.reconcile_from_balance_chain(rows) is None


def test_single_row_file_proves_nothing():
    # One row has no link — an unbroken 0-link chain says nothing about missing rows.
    assert guards.reconcile_from_balance_chain([_row("2026-06-05", 100.0, "IN", 1_100.0)]) is None


def test_skip_rows_are_excluded_from_the_chain():
    rows = _owner_shape_rows()
    rows.insert(5, _row("2026-06-03", 50_000.0, "OUT", None, skip=True, skip_reason="failed"))
    verdict = guards.reconcile_from_balance_chain(rows)
    assert verdict is not None and verdict["status"] == "ok"


def test_ambiguous_orientations_yield_none():
    # Two same-day rows of the SAME amount: forward (opening 5) and reversed-flipped
    # (opening 20) are both arithmetically consistent — genuinely undecidable, claim nothing.
    rows = [_row("2026-06-05", 5.0, "IN", 10.0), _row("2026-06-05", 5.0, "IN", 15.0)]
    assert guards.reconcile_from_balance_chain(rows) is None


def test_all_zero_amounts_prove_nothing():
    rows = [_row("2026-06-05", 0.0, "IN", 10.0), _row("2026-06-06", 0.0, "IN", 10.0)]
    assert guards.reconcile_from_balance_chain(rows) is None


def test_mixed_currencies_prove_nothing():
    rows = [
        _row("2026-06-05", 5.0, "IN", 10.0, currency="SGD"),
        _row("2026-06-06", 7.0, "IN", 17.0, currency="USD"),
        _row("2026-06-07", 3.0, "OUT", 14.0, currency="SGD"),
    ]
    assert guards.reconcile_from_balance_chain(rows) is None


def test_printed_opening_contradiction_stays_unknown_with_warning():
    # An extracted opening that CONTRADICTS the derived one means the chain and the header
    # disagree — one of them is misread; claiming completeness on either would be a lie.
    result = ParseResult(
        meta=StatementMeta(opening_balance=1_500_000.0),
        rows=_owner_shape_rows(),
        tier="tabular-profile",
        reconcile={"status": "unknown", "currency": None, "groups": []},
    )
    assert guards.apply_chain_reconcile(result) is False
    assert result.reconcile["status"] == "unknown"
    assert any("running-balance chain" in w for w in result.meta.warnings)
    assert result.meta.period_start is None and result.meta.closing_balance is None
    guards.apply_chain_reconcile(result)  # cached re-previews never stack duplicate warnings
    assert len(result.meta.warnings) == 1


def test_printed_opening_that_agrees_is_kept_and_closing_filled():
    result = ParseResult(
        meta=StatementMeta(opening_balance=1_040_600.0),
        rows=_owner_shape_rows(),
        tier="tabular-profile",
        reconcile={"status": "unknown", "currency": None, "groups": []},
    )
    assert guards.apply_chain_reconcile(result) is True
    assert result.reconcile["status"] == "ok"
    assert result.meta.opening_balance == 1_040_600.0  # printed value never overwritten
    assert result.meta.closing_balance == 455_900.0
    assert result.meta.period_start == "2026-06-01" and result.meta.period_end == "2026-06-29"


# ── pipeline acceptance in miniature (the owner's complaint, end to end) ─────────────


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_complete", None)  # any LLM call on this path = loud failure
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Checking", "kind": "asset", "currency": "SGD"}
    )


# Chain-consistent: opening 1000 → 1250 → 1220 → 1074.50 → closing 1072.50.
_CSV_OK = b"""Date,Description,Amount,Balance
2026-06-01,SALARY TOPUP COMPANY,250.00,1250.00
2026-06-10,AUTOGIRO UTILITY BILL,-30.00,1220.00
2026-06-22,MPOS 9999 ABC,-145.50,1074.50
2026-06-29,MONTHLY SERVICE FEE,-2.00,1072.50
"""

# The SAME statement with the 06-10 row deleted: 1074.50 != 1250 - 145.50 → chain broken.
_CSV_BROKEN = b"""Date,Description,Amount,Balance
2026-06-01,SALARY TOPUP COMPANY,250.00,1250.00
2026-06-22,MPOS 9999 ABC,-145.50,1074.50
2026-06-29,MONTHLY SERVICE FEE,-2.00,1072.50
"""

_SCHEMA = {
    "header_row": 0,
    "date_col": 0,
    "desc_col": 1,
    "payee_col": None,
    "id_col": None,
    "debit_col": 2,
    "credit_col": 2,
    "signed": True,
    "balance_col": 3,
    "status_col": None,
    "category_col": None,
    "success_values": [],
    "currency": None,
    "date_order": "YMD",
}


def _seed_profile():
    """Format memory hit → the preview parses with ZERO LLM (a real-world shape: a
    Riverside Bank-style layout was inferred once and profiled; every later parse is
    deterministic)."""
    from backend.services.statements import profiles
    from backend.services.statements.tiers import parse_grid

    assert profiles.learn(parse_grid(_CSV_OK.decode()), _SCHEMA) is not None


def _book_telegram_twin() -> dict:
    """The other source's copy of the 06-22 charge: same account, same amount, same day,
    ZERO shared wording ("Example Diner dinner" vs the statement's "MPOS 9999 ABC") — the
    wordless exact tier lands it in the review band; only books-close can lift it."""
    return ledger_service.transactions.append(
        {
            "id": "tg-row-1",
            "kind": "expense",
            "date": "2026-06-22",
            "description": "Example Diner dinner",
            "category": None,
            "from_account_id": "acc",
            "from_amount": 145.5,
            "from_currency": "SGD",
            "to_account_id": None,
            "to_amount": None,
            "to_currency": None,
            "external_refs": [{"source": None, "ref": None}],
            "source": "telegram",
        }
    )


def _pair_flag(prev: dict) -> dict:
    row = next(r for r in prev["rows"] if r["date"] == "2026-06-22")
    return next(f for f in row["flags"] if f["type"] == "near_duplicate")


def test_chain_proven_tabular_preview_promotes_unique_exact_pair_to_strong(env):
    booked = _book_telegram_twin()
    _seed_profile()
    prev = svc.decision_preview(_CSV_OK, "csv", "acc", filename="riverside.csv")
    assert prev["llm_cost_usd"] == 0
    assert prev["reconcile"]["status"] == "ok"
    assert prev["reconcile"]["method"] == "balance_chain"
    assert prev["meta"]["opening_balance"] == 1000.0
    assert prev["meta"]["closing_balance"] == 1072.5
    assert prev["meta"]["period_start"] == "2026-06-01"
    assert prev["meta"]["period_end"] == "2026-06-29"
    flag = _pair_flag(prev)
    assert flag["existing_id"] == booked["id"]
    assert flag["band"] == "strong" and flag["promoted"] is True


def test_broken_chain_keeps_the_same_pair_at_review(env):
    _book_telegram_twin()
    _seed_profile()
    prev = svc.decision_preview(_CSV_BROKEN, "csv", "acc", filename="riverside.csv")
    assert prev["reconcile"]["status"] == "unknown"  # no false completeness
    assert prev["meta"]["period_start"] is None
    flag = _pair_flag(prev)
    assert flag["band"] == "review" and flag["promoted"] is False


def test_cached_record_self_heals_reconcile_on_next_preview(env):
    import backend.db.statement_store as stmt_mod

    booked = _book_telegram_twin()
    _seed_profile()
    prev = svc.decision_preview(_CSV_OK, "csv", "acc", filename="riverside.csv")
    sid = prev["statement_id"]
    # Simulate a pre-existing cached record: cached BEFORE the balance-chain reconcile existed —
    # rows (with balances) cached, reconcile stuck at unknown, no derived meta.
    store = stmt_mod.StatementStore()
    rec = store.find_by_id(sid)
    store.update_by_id(
        sid,
        {
            "reconcile": {"status": "unknown", "currency": None, "groups": []},
            "import_meta": {
                **rec["import_meta"],
                "opening_balance": None,
                "closing_balance": None,
                "period_start": None,
                "period_end": None,
            },
        },
    )
    drop_stored_review(sid)  # the heal runs on the recompute path
    prev2 = svc.decision_preview(_CSV_OK, "csv", "acc", filename="riverside.csv")
    assert prev2["statement_id"] == sid  # cached reuse — zero re-parse
    assert prev2["llm_cost_usd"] == 0  # and zero LLM (the seam would raise loudly anyway)
    assert prev2["reconcile"]["status"] == "ok"
    flag = _pair_flag(prev2)
    assert flag["existing_id"] == booked["id"]
    assert flag["band"] == "strong" and flag["promoted"] is True
    # Healed verdict + derived meta PERSISTED — decision_commit's gate reads the record.
    rec2 = store.find_by_id(sid)
    assert rec2["reconcile"]["status"] == "ok"
    assert rec2["import_meta"]["opening_balance"] == 1000.0
    assert rec2["import_meta"]["period_start"] == "2026-06-01"
    assert rec2["import_meta"]["period_end"] == "2026-06-29"
