"""An incomplete statement parse can never masquerade as a clean reconciliation.

Real incident (2026-09-08): a 5-page scanned statement's page 4 truncated at the vision tier's
output-token cap; the parser kept the complete rows and warned, but nothing downstream honoured
either the warning or the reconcile guard's "off" verdict — `decision_preview` still ran
`preview_ledger_check` and the UI offered to delete 17 real ledger charges as "not on this
statement". This suite pins the server-side gate: `decision_preview` and
`uncovered_ledger_rows` both withhold the ledger-diff panel — never compute it — whenever the
cached parse is incomplete or its own reconcile identity fails. See
docs/adr/2026-09-09-an-incomplete-parse-withholds-the-ledger-diff.md."""

from __future__ import annotations

import json

import pytest

# Collection-time on purpose: backend.ai.memory builds asyncio.Lock() at import (py3.9).
from backend.core import llm_seam
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements.decisions import _parse_withheld_reason, _withheld_ledger_check
from backend.services.statements.ledger_diff import uncovered_ledger_rows
from backend.services.statements.lucius_pass import _link_targets
from tests.conftest import drop_stored_review


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    ledger_service.accounts.insert(
        {
            "id": "acc",
            "name": "Meridian Bank Card",
            "kind": "liability",
            "currency": "SGD",
            "last_four": "1111",
        }
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict) -> None:
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta)
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 60.36,
        "direction": "OUT",
        "payee": "Cinebar",
        "description": "MPOS*CINEBAR 60.36",
        "confidence": 0.97,
    },
    {
        "date": "2026-05-22",
        "amount": 11.04,
        "direction": "OUT",
        "payee": "Foodie",
        "description": "FOODIE 11.04",
        "confidence": 0.95,
    },
]
# opening + Σ(OUT) = 0.00 + 71.40 = closing 71.40 — a clean "ok" reconcile.
_META_OK = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank",
    "opening_balance": 0.0,
    "closing_balance": 71.40,
    "account_last4s": ["1111"],
}
# closing declares 500.00 instead of 71.40 — the identity fails ("off"), nowhere close to
# the reconcile tolerance.
_META_OFF = {**_META_OK, "closing_balance": 500.00}
# opening/closing swapped from _META_OK: the AGGREGATE only closes if the OUT rows are read as
# IN instead — "ok_if_flipped", not "ok".
_META_FLIPPED = {**_META_OK, "opening_balance": 71.40, "closing_balance": 0.0}

_FAKE_PDF = _make_pdf("Meridian Bank statement\nMPOS*CINEBAR 60.36\nFOODIE 11.04")


def _preview(monkeypatch, meta: dict, filename: str = "june.pdf") -> dict:
    _stub_text_tier(monkeypatch, _ROWS, meta)
    return svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename=filename)


def _forbid_preview_ledger_check(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("preview_ledger_check must not run on a withheld parse")

    monkeypatch.setattr("backend.services.statements.ledger_diff.preview_ledger_check", _boom)


# ── unit coverage of the gate predicate itself ──────────────────────────────────────────


def test_withheld_reason_prefers_parse_incomplete_over_reconcile_status():
    meta = {"extra": {"parse_complete": False, "incomplete_pages": [2, 4]}}
    assert _parse_withheld_reason(meta, {"status": "ok"}) == "parse_incomplete"


def test_withheld_reason_flags_a_failing_reconcile():
    meta = {"extra": {"parse_complete": True}}
    assert _parse_withheld_reason(meta, {"status": "off", "delta": 12.5}) == "reconcile_off"


@pytest.mark.parametrize("status", ["ok", "unknown", None])
def test_withheld_reason_is_none_for_a_trusted_reconcile(status):
    meta = {"extra": {"parse_complete": True}}
    assert _parse_withheld_reason(meta, {"status": status} if status else None) is None


@pytest.mark.parametrize("status", ["off", "ok_if_flipped"])
def test_withheld_reason_flags_off_and_flipped_reconciles(status):
    """`ok_if_flipped` is deliberately NOT trusted here even though `_completeness_ok`
    treats it as a passing books-close for a different purpose: it proves the AGGREGATE
    nets to the right total, but only because every row's direction was read backwards —
    a per-row ledger-diff comparison built on that is unproven even though the sum closes."""
    meta = {"extra": {"parse_complete": True}}
    assert _parse_withheld_reason(meta, {"status": status}) == "reconcile_off"


@pytest.mark.parametrize("meta", [{"extra": {}}, {}])
def test_withheld_reason_is_unverified_for_a_legacy_record_with_no_parse_complete_key(meta):
    """A record cached BEFORE completeness tracking existed (no `parse_complete` key at
    all, including the one that caused the incident this gate exists for) must never be
    silently trusted as complete."""
    assert _parse_withheld_reason(meta, {"status": "ok"}, tier="vision") == "parse_unverified"
    assert _parse_withheld_reason(meta, {"status": "ok"}, tier=None) == "parse_unverified"


@pytest.mark.parametrize("tier", ["tabular-schema", "tabular-profile"])
def test_withheld_reason_stays_open_for_a_legacy_tabular_record(tier):
    """Tabular is zero-LLM and parses every grid row deterministically — complete by
    construction regardless of when it was cached."""
    assert _parse_withheld_reason({"extra": {}}, {"status": "ok"}, tier=tier) is None


def test_withheld_ledger_check_shape():
    meta = {"extra": {"parse_complete": False, "incomplete_pages": [3]}}
    reconcile = {"status": "off", "delta": -302.17, "currency": "SGD"}
    assert _withheld_ledger_check(meta, reconcile) == {
        "withheld": "parse_incomplete",
        "pages_incomplete": 1,
        "delta": -302.17,
        "currency": "SGD",
    }


# ── decision_preview: fresh + cached branches ───────────────────────────────────────────


def test_regression_a_complete_parse_stamps_parse_complete_and_leaves_the_gate_open(monkeypatch):
    res = _preview(monkeypatch, _META_OK)
    assert res["meta"]["extra"].get("parse_complete") is True
    assert res["reconcile"]["status"] == "ok"
    assert res["ledger_check"]["status"] == "ok"
    assert "withheld" not in res["ledger_check"]


def test_decision_preview_withholds_when_the_cached_parse_is_incomplete(monkeypatch):
    res = _preview(monkeypatch, _META_OK)
    assert "withheld" not in res["ledger_check"]  # baseline: the gate is open before the poke

    import backend.db.statement_store as stmt_mod

    store = stmt_mod.StatementStore()
    record = store.find_by_id(res["statement_id"])
    meta = dict(record["import_meta"])
    meta["extra"] = {**meta.get("extra", {}), "parse_complete": False, "incomplete_pages": [4]}
    store.update_by_id(res["statement_id"], {"import_meta": meta})

    _forbid_preview_ledger_check(monkeypatch)
    drop_stored_review(res["statement_id"])  # the gate is read on the recompute path
    # Same content/format/account, no force flags — hits the CACHED reuse branch, so this
    # exercises the gate reading the flags off the record, not a fresh parse.
    res2 = _preview(monkeypatch, _META_OK, filename="june-again.pdf")
    assert res2["statement_id"] == res["statement_id"]
    assert res2["ledger_check"] == {
        "withheld": "parse_incomplete",
        "pages_incomplete": 1,
        "delta": None,  # the cached reconcile is "ok" — no delta on a passing verdict
        "currency": "SGD",
    }


def test_decision_preview_withholds_when_reconcile_is_off(monkeypatch):
    _forbid_preview_ledger_check(monkeypatch)
    res = _preview(monkeypatch, _META_OFF)
    assert res["reconcile"]["status"] == "off"
    assert res["ledger_check"]["withheld"] == "reconcile_off"
    assert res["ledger_check"]["delta"] == res["reconcile"]["delta"]
    assert res["ledger_check"]["currency"] == "SGD"
    assert res["ledger_check"]["pages_incomplete"] == 0


def test_decision_preview_withholds_when_reconcile_is_ok_if_flipped(monkeypatch):
    """A flipped-convention reconcile proves the AGGREGATE closes, but every row's own
    direction was read backwards to get there — a per-row ledger comparison on top of that
    is unproven, so it withholds exactly like an outright "off" verdict."""
    _forbid_preview_ledger_check(monkeypatch)
    res = _preview(monkeypatch, _META_FLIPPED)
    assert res["reconcile"]["status"] == "ok_if_flipped"
    assert res["ledger_check"]["withheld"] == "reconcile_off"


# ── uncovered_ledger_rows: the post-commit half of the same gate ───────────────────────


def test_uncovered_ledger_rows_withholds_on_an_incomplete_cached_parse(monkeypatch):
    res = _preview(monkeypatch, _META_OK)
    monkeypatch.setattr(llm_seam, "_complete", None)  # commit must not call the model
    svc.decision_commit(res["statement_id"], "acc", default_action="skip")
    assert uncovered_ledger_rows(res["statement_id"])["status"] == "ok"

    import backend.db.statement_store as stmt_mod

    store = stmt_mod.StatementStore()
    record = store.find_by_id(res["statement_id"])
    meta = dict(record["import_meta"])
    meta["extra"] = {**meta.get("extra", {}), "parse_complete": False, "incomplete_pages": [2]}
    store.update_by_id(res["statement_id"], {"import_meta": meta})

    out = uncovered_ledger_rows(res["statement_id"])
    assert out == {"status": "parse_incomplete", "rows": []}


def test_uncovered_ledger_rows_withholds_a_legacy_record_missing_the_parse_complete_key(
    monkeypatch,
):
    res = _preview(monkeypatch, _META_OK)
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "acc", default_action="skip")

    import backend.db.statement_store as stmt_mod

    store = stmt_mod.StatementStore()
    record = store.find_by_id(res["statement_id"])
    meta = dict(record["import_meta"])
    meta["extra"] = {}  # no parse_complete key at all — pre-existing, cached before B16
    store.update_by_id(res["statement_id"], {"import_meta": meta, "tier": "vision"})
    assert uncovered_ledger_rows(res["statement_id"]) == {
        "status": "parse_unverified",
        "rows": [],
    }

    store.update_by_id(res["statement_id"], {"tier": "tabular-profile"})
    assert uncovered_ledger_rows(res["statement_id"])["status"] == "ok"


# ── decision_commit: delete_ids refused on a withheld statement ────────────────────────


def _mark_incomplete(statement_id: str) -> None:
    import backend.db.statement_store as stmt_mod

    store = stmt_mod.StatementStore()
    record = store.find_by_id(statement_id)
    meta = dict(record["import_meta"])
    meta["extra"] = {**meta.get("extra", {}), "parse_complete": False, "incomplete_pages": [3]}
    store.update_by_id(statement_id, {"import_meta": meta})


def test_decision_commit_refuses_delete_ids_on_a_withheld_statement_but_still_books_creates(
    monkeypatch,
):
    res = _preview(monkeypatch, _META_OK)
    # A real ledger entry, same day as row 0, unrelated wording/amount — the "not on this
    # statement" panel would legitimately list it as uncovered on an OPEN gate.
    entry = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "ExampleSub subscription renewal",
            "from_account_id": "acc",
            "from_amount": 25.90,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    _mark_incomplete(res["statement_id"])
    monkeypatch.setattr(llm_seam, "_complete", None)  # commit must not call the model

    with pytest.raises(ValidationError, match="parse_incomplete"):
        svc.decision_commit(res["statement_id"], "acc", delete_ids=[entry["id"]])
    assert not ledger_service.transactions.find_by_id(entry["id"]).get("deleted")

    # The SAME (still withheld) statement's ordinary create decisions are unaffected — only
    # the delete_ids path is gated.
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "create"}],
        default_action="skip",
    )
    assert out["created"] == 1


# ── lucius_pass._link_targets: fails closed on a withheld statement ────────────────────


def test_link_targets_fails_closed_when_withheld(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("preview_ledger_check must not run when withheld")

    monkeypatch.setattr("backend.services.statements.ledger_diff.preview_ledger_check", _boom)
    record = {"reconcile": {"status": "off", "delta": 12.0}}
    meta = {"extra": {"parse_complete": True}}
    result = _link_targets(record, [], [], frozenset({"acc"}), meta)
    assert result == frozenset()
