"""Staged ledger deletions at the decision-commit choke (owner, 2026-08-01).

The "In your ledger, not on this statement" panel used to fire ``DELETE /api/transactions/<id>``
the instant the reviewer clicked Delete — one irreversible round-trip per row, before they had
committed anything, under a footer that promises "nothing is written until you confirm". With
~8 duplicate ride charges to clear that is eight dialogs and eight irreversible writes against a
statement the user might still abandon.

Delete is now STAGED and applied as part of the commit, via ``decision_commit(delete_ids=…)``.
The ids live here rather than in a client-side delete loop because the safe target set is this
statement's own re-derived UNCOVERED set (``_manual_link_ids``) — the same set a manual link is
validated against — and only ``decision_commit`` can re-derive it. The caller's list is a
REQUEST, never an authorization (SECURITY FIX 2, review 2026-07-16).

What is pinned below:

* an id outside the uncovered set REFUSES the whole commit — nothing written, victim unchanged
* an id that is also a merge target in the same commit refuses (cannot link AND delete)
* a confirmed commit deletes EXACTLY the marked rows, and nothing else
* deletions run FIRST, so a merge in the same commit is judged against the world the user
  confirmed — not against rows this commit is about to remove (owner, 2026-08-27). A delete
  failure still never costs the user the import they confirmed: it is REPORTED in
  ``delete_errors`` rather than raised or silently dropped (the ``merge_errors`` contract),
  and an unexpected failure in the row half RESTORES the staged deletions
* a Starting Balance row's atomic refusal surfaces as a reported error, not a 500

Harness mirrors test_decision_commit_guard.py (LLM stubbed via the seam; stores isolated).
"""

from __future__ import annotations

import json

import pytest

from backend.core import llm_seam
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    # accounts/transactions/import_batches/txn_audit/payees are DB-backed now (plan 3 task 4 /
    # plan 4d task 2) — db_isolated redirects the process-wide DB singleton, so
    # ledger_service's module-level store instances AND services.audit's own separate
    # TransactionStore()/TransactionAuditStore() instances all resolve against the same
    # isolated database, with no per-instance path-sync needed.
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
    ledger_service.accounts.insert(
        {"id": "other", "name": "Meridian Bank Savings", "kind": "asset", "currency": "SGD"}
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict | None = None):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta or {})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 60.36,
        "direction": "OUT",
        "payee": "Cinebar",
        "description": "MPOS*CINEBAR 60.36",
        "reference": "74720746142802720528652",
        "confidence": 0.97,
    },
]
_META = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank",
    "opening_balance": 0.0,
    "closing_balance": 60.36,
    "account_last4s": ["1111"],
    "period_start": "2026-05-01",
    "period_end": "2026-05-31",
}


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_FAKE_PDF = _make_pdf(
    "Meridian Bank statement\nMPOS*CINEBAR 60.36\nRef No. : 74720746142802720528652"
)


def _preview(monkeypatch, rows=_ROWS, meta=_META):
    _stub_text_tier(monkeypatch, rows, meta)
    return svc.decision_preview(_FAKE_PDF, "pdf", "acc", filename="june.pdf")


def _live_txns() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def _uncovered_entry(description="Grab ride", date="2026-05-15", amount=13.71, account="acc"):
    """A real ledger entry in the statement's window + account that the statement does not
    account for — exactly what the "In your ledger, not on this statement" panel lists, and the
    owner's live case: duplicate ride charges an agent booked that the statement settled once."""
    return ledger_service.create_transaction(
        {
            "date": date,
            "kind": "expense",
            "description": description,
            "from_account_id": account,
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "agent",
        }
    )


# ── the guard: the marked ids are a request, never an authorization ──────────────────────


def test_an_id_outside_the_uncovered_set_refuses_the_whole_commit(monkeypatch):
    """The staged-delete equivalent of the merge guard: a transaction on ANOTHER account is not
    in this statement's uncovered set, so it cannot be deleted through this door — and because
    the check runs before any write, the import does not half-apply either."""
    victim = ledger_service.create_transaction(
        {
            "date": "2026-05-15",
            "kind": "expense",
            "description": "Rent",
            "from_account_id": "other",
            "from_amount": 4000.0,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    before = dict(victim)
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    with pytest.raises(ValidationError):
        svc.decision_commit(res["statement_id"], "acc", decisions=[], delete_ids=[victim["id"]])

    assert ledger_service.transactions.find_by_id(victim["id"]) == before
    # nothing else was written either — the refusal precedes every write
    assert [t["id"] for t in _live_txns()] == [victim["id"]]
    assert ledger_service.import_batches.all() == []


def test_a_fabricated_id_refuses(monkeypatch):
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    with pytest.raises(ValidationError):
        svc.decision_commit(res["statement_id"], "acc", decisions=[], delete_ids=["txn_nope"])

    assert _live_txns() == []


def test_an_entry_cannot_be_both_linked_and_deleted_in_one_commit(monkeypatch):
    """A merge re-dates and re-amounts its target; deleting that same target in the same commit
    is incoherent. The UI cannot produce it (a linked entry leaves the panel), so it refuses
    rather than silently picking an order."""
    entry = _uncovered_entry()
    res = _preview(monkeypatch)
    assert entry["id"] in {r["id"] for r in res["ledger_check"]["rows"]}
    monkeypatch.setattr(llm_seam, "_complete", None)

    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": entry["id"]}],
            delete_ids=[entry["id"]],
        )

    survivor = ledger_service.transactions.find_by_id(entry["id"])
    assert survivor["from_amount"] == 13.71 and not survivor.get("deleted")


# ── staged, not fired: no delete_ids → nothing deleted ───────────────────────────────────


def test_a_commit_with_no_delete_ids_deletes_nothing(monkeypatch):
    """The staged half of the contract, server-side: marking rows in the UI is local state, so a
    commit that carries no ``delete_ids`` must leave every uncovered entry standing — including
    the ones the panel was showing."""
    a = _uncovered_entry("Grab ride A")
    b = _uncovered_entry("Grab ride B", amount=9.40)
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(res["statement_id"], "acc", decisions=[])

    assert out["deleted"] == 0 and out["delete_errors"] == []
    assert {a["id"], b["id"]} <= {t["id"] for t in _live_txns()}


# ── confirm deletes EXACTLY the marked rows ──────────────────────────────────────────────


def test_confirm_deletes_exactly_the_marked_rows_and_nothing_else(monkeypatch):
    """The owner's case: several duplicate ride charges marked, one deliberately left alone.
    The commit deletes the marked ones and only those — and still books the statement row."""
    marked_1 = _uncovered_entry("Grab ride A")
    marked_2 = _uncovered_entry("Grab ride B", amount=9.40)
    spared = _uncovered_entry("Groceries", amount=52.10)
    res = _preview(monkeypatch)
    uncovered = {r["id"] for r in res["ledger_check"]["rows"]}
    assert {marked_1["id"], marked_2["id"], spared["id"]} <= uncovered
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[],
        delete_ids=[marked_1["id"], marked_2["id"]],
    )

    assert out["deleted"] == 2 and out["delete_errors"] == []
    live = {t["id"] for t in _live_txns()}
    assert marked_1["id"] not in live and marked_2["id"] not in live
    assert spared["id"] in live
    # soft-delete, not erasure — the rows are recoverable exactly as any other delete
    assert ledger_service.transactions.find_by_id(marked_1["id"])["deleted"] is True
    # the import itself still happened
    assert out["created"] == 1


def test_a_repeated_id_deletes_once(monkeypatch):
    """A double-marked row (or a caller that repeats itself) must not double-fire — the delete
    is idempotent within one commit."""
    entry = _uncovered_entry()
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(
        res["statement_id"], "acc", decisions=[], delete_ids=[entry["id"], entry["id"]]
    )

    assert out["deleted"] == 1 and out["delete_errors"] == []


# ── a failed delete SURFACES ─────────────────────────────────────────────────────────────


def test_a_row_deleted_between_preview_and_commit_is_reported_not_a_hard_refusal(monkeypatch):
    """It vanished between the preview and the commit. Their intent is already satisfied, so
    this must NOT fail the whole confirmed import the way a foreign id does — but it is still
    reported, so "delete 1" never quietly becomes "deleted 0"."""
    entry = _uncovered_entry()
    res = _preview(monkeypatch)
    ledger_service.bulk_delete_transactions([entry["id"]])
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(res["statement_id"], "acc", decisions=[], delete_ids=[entry["id"]])

    assert out["deleted"] == 0
    assert out["delete_errors"] == [{"id": entry["id"], "error": "already deleted"}]
    assert out["created"] == 1  # the import the user confirmed still stands


def test_a_refused_delete_is_reported_and_the_import_still_stands(monkeypatch):
    """``bulk_delete_transactions`` refuses a Starting Balance row OUTRIGHT (and atomically).
    That refusal must not sink an import the user already confirmed: the rows book, and the
    failure is reported instead of raised."""
    opening = ledger_service.create_transaction(
        {
            "date": "2026-05-10",
            "kind": "income",
            "description": "Starting Balance",
            "to_account_id": "acc",
            "to_amount": 100.0,
            "to_currency": "SGD",
            "source": "manual",
        }
    )
    ledger_service.accounts.update_by_id("acc", {"opening_txn_id": opening["id"]})
    res = _preview(monkeypatch)
    assert opening["id"] in {r["id"] for r in res["ledger_check"]["rows"]}
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(res["statement_id"], "acc", decisions=[], delete_ids=[opening["id"]])

    assert out["deleted"] == 0
    assert len(out["delete_errors"]) == 1
    assert "Starting Balance" in out["delete_errors"][0]["error"]
    # the confirmed import is intact — the delete ran last, precisely so this is true
    assert out["created"] == 1
    assert not ledger_service.transactions.find_by_id(opening["id"]).get("deleted")


def test_one_bad_id_does_not_take_the_good_ones_with_it(monkeypatch):
    """A protected row among ordinary ones: ``bulk_delete_transactions`` pre-scans and refuses
    the BATCH atomically, so nothing is half-deleted — and the whole refusal is reported."""
    opening = ledger_service.create_transaction(
        {
            "date": "2026-05-10",
            "kind": "income",
            "description": "Starting Balance",
            "to_account_id": "acc",
            "to_amount": 100.0,
            "to_currency": "SGD",
            "source": "manual",
        }
    )
    ledger_service.accounts.update_by_id("acc", {"opening_txn_id": opening["id"]})
    ordinary = _uncovered_entry()
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(
        res["statement_id"], "acc", decisions=[], delete_ids=[ordinary["id"], opening["id"]]
    )

    assert out["deleted"] == 0 and len(out["delete_errors"]) == 1
    live = {t["id"] for t in _live_txns()}
    assert ordinary["id"] in live and opening["id"] in live


# ── deletes run first: one decision must not be judged against the other's leftovers ──────


def _row_sharing_ref(description, amount, ref, date="2026-05-15"):
    """A ledger row carrying ``ref``, created through the BULK path.

    Deliberately not ``create_transaction``: its L0 floor would return the first row instead of
    writing a second, and the point here is the state a real book actually reaches.
    ``bulk_create_transactions`` does not enforce the external-ref floor unless a caller opts in
    via ``dedup_by_external_ref``, and the automation path does not — which is how three live
    rows came to share one Apple message-id on the owner's book.

    ``force=True`` only switches off the SEMANTIC (resemblance) guard, so the fixture isolates
    the question under test — shared IDENTITY — from two rows merely looking alike. On the real
    book the siblings were booked days apart by separate automation runs, which that guard never
    saw as a pair either.
    """
    created, _ = ledger_service.bulk_create_transactions(
        [
            {
                "date": date,
                "kind": "expense",
                "description": description,
                "from_account_id": "acc",
                "from_amount": amount,
                "from_currency": "SGD",
                "source": "agent",
                "external_refs": [{"source": "email", "ref": ref}],
            }
        ],
        allow_duplicate=True,
    )
    return created[0]


def test_linking_one_row_and_deleting_its_ref_siblings_succeeds(monkeypatch):
    """Root-caused from a repro, 2026-08-27.

    Three rows shared ``email:aaaa1111bbbb``. The reviewer linked the statement row to one and
    marked the other two for deletion — a complete, consistent decision. While deletes ran LAST,
    the merge asked who owned that ref, one of the not-yet-deleted siblings answered, and
    ``resolve_external_refs_patch`` refused. The failure was swallowed into ``skipped`` and shown
    as "1 left out"; the deletes then freed the ref seconds later, so the statement row stayed
    unlinked and offered to link again on every re-import.
    """
    ref = "aaaa1111bbbb"
    # Siblings FIRST, so the merge target is not the first live owner of its own ref —
    # `by_external_ref` answers with the first, and that is what made the merge refuse.
    sibling_a = _row_sharing_ref("Apple Invoice 2-1234500001", 31.96, ref)
    sibling_b = _row_sharing_ref("Apple - Goodnotes", 31.96, ref)
    target = _row_sharing_ref("MPOS*CINEBAR 60.36", 60.36, ref, date="2026-05-21")
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": target["id"]}],
        delete_ids=[sibling_a["id"], sibling_b["id"]],
    )

    assert out["merge_errors"] == [], out["merge_errors"]
    assert out["merged"] == 1
    assert out["deleted"] == 2
    # The linked row really carries the statement now — the whole point of the link.
    after = ledger_service.transactions.find_by_id(target["id"])
    assert after["cleared"] is True
    live = {t["id"] for t in _live_txns()}
    assert sibling_a["id"] not in live and sibling_b["id"] not in live


def test_a_failure_in_the_row_half_puts_the_staged_deletions_back(monkeypatch):
    """The guarantee the old deletes-last ordering bought, kept by compensation instead.

    A confirmed import that blows up must not also cost the user rows it never got to justify
    removing.
    """
    doomed = _uncovered_entry("Grab ride A")
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)

    boom = RuntimeError("row half exploded")
    monkeypatch.setattr(svc, "_apply_rows", lambda *a, **k: (_ for _ in ()).throw(boom))

    with pytest.raises(RuntimeError):
        svc.decision_commit(res["statement_id"], "acc", decisions=[], delete_ids=[doomed["id"]])

    assert ledger_service.transactions.find_by_id(doomed["id"])["deleted"] is False
