"""A deleted transaction's external-ref identity is remembered, so a re-delivery of the same
source event is refused instead of re-booked (ADR 2026-09-02-a-deleted-identity-is-remembered).

The scar (2026-08-30): the owner deleted a voided hotel charge that an email automation had
booked. ``TransactionStore.by_external_ref`` reads live rows only, so the delete FREED the
ref; the next run re-fetched the same email, found the ref free, and booked the charge again.
Every "is this already in the ledger?" question the app asked was answered by live rows, and
a deleted row is not a live row — the memory had to be a separate fact.

What this pins, in order of what would hurt most if it broke:

  1. The write chokes refuse a remembered identity on EVERY path — the batch tool, the lone
     row, and under ``allow_duplicate``/``force`` — and report it as its own outcome
     (``previously_deleted``), never silently and never as a duplicate of a live row.
  2. The way back is explicit: a ``create`` decision at the tool, ``resurrect`` at the
     service/REST, or forgetting the record in settings. Resurrecting forgets.
  3. The memory is MOVEMENT-scoped: one Wise email books two legs on two routes; deleting one
     must not reject the other.
  4. Rolling back is not rejecting: batch rollback, ``delete_transactions_where(batch_id=)``,
     a trade undo (``reject_identities=False``) and a merge tombstone write nothing.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.ai.tools.ledger import add_transactions
from backend.core.errors import PreviouslyDeletedError
from backend.services import ledger as L
from backend.services import rejected_identities as R

CARD = "Meridian Bank Revolution Card"
WALLET = "Harborview Wallet"
CASH = "Lighthouse Cash"
REF = "aaaa1111cccc"


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_card", "name": CARD, "kind": "liability", "currency": "SGD"})
    L.accounts.insert({"id": "acc_wallet", "name": WALLET, "kind": "asset", "currency": "SGD"})
    L.accounts.insert({"id": "acc_cash", "name": CASH, "kind": "asset", "currency": "SGD"})


def _row(desc="RIDGELINE HOTEL", amount=250.0, ref=REF, date="2026-08-29", account=CARD):
    return {
        "date": date,
        "description": desc,
        "amount": amount,
        "currency": "SGD",
        "direction": "debit",
        "account_id": account,
        "external_refs": [{"source": "email", "ref": ref}],
    }


def _svc_row(ref=REF, frm="acc_card", to=None, amount=250.0, kind="expense"):
    data = {
        "date": "2026-08-29",
        "kind": kind,
        "description": "RIDGELINE HOTEL",
        "from_account_id": frm,
        "from_amount": amount,
        "from_currency": "SGD",
        "source": "ai",
        "external_refs": [{"source": "email", "ref": ref}],
    }
    if to:
        data.update({"to_account_id": to, "to_amount": amount, "to_currency": "SGD"})
    return data


async def _add(transactions, **extra):
    out, _ = await add_transactions({"transactions": transactions, **extra})
    return json.loads(out)


def _live():
    return [t for t in L.transactions.all() if not t.get("deleted")]


def _book_then_delete(**kw) -> str:
    row = L.create_transaction(_svc_row(**kw))
    L.bulk_delete_transactions([row["id"]])
    return row["id"]


# ── 1. Refused on every path, reported as its own outcome ──────────────────────────────


def test_a_delete_writes_the_memory_in_the_same_transaction():
    deleted_id = _book_then_delete()
    [rec] = R.store.all()
    assert rec["txn_id"] == deleted_id
    assert (rec["source"], rec["ref"], rec["from_account_id"]) == ("email", REF, "acc_card")
    assert rec["amount"] == 250.0 and rec["currency"] == "SGD"


@pytest.mark.asyncio
async def test_the_batch_tool_reports_previously_deleted_and_writes_nothing():
    deleted_id = _book_then_delete()

    again = await _add([_row()], import_description="mailbox")

    assert again["ok"] is True and again["created"] == 0
    assert again["summary"] == "1 submitted → 1 previously_deleted"
    assert again["results"][0]["status"] == "previously_deleted"
    assert again["results"][0]["deleted_id"] == deleted_id
    # The row's own entry in `results` carries a readable reason, not just the aggregate
    # `previously_deleted` list — a caller that reads results row-by-row must not need a
    # second lookup to learn WHY (the "status cannot be half-read" rule this file's sibling
    # `add_transactions` code already applies to an ordinary duplicate refusal).
    assert "already deleted" in again["results"][0]["reason"]
    assert again["previously_deleted"][0]["deleted_id"] == deleted_id
    assert "NOT BOOKED" in again["previously_deleted_note"]
    assert "refused_duplicates" not in again, "not a duplicate of a live row"
    assert _live() == []


@pytest.mark.asyncio
async def test_allow_duplicate_does_not_reach_a_remembered_delete():
    _book_then_delete()

    again = await _add([_row(), _row("LUNCH", 12.0, ref="dddd2222eeee")], allow_duplicate=True)

    assert again["created"] == 1, "the unrelated row books; the deleted one does not"
    assert [r["status"] for r in again["results"]] == ["previously_deleted", "created"]
    assert len(_live()) == 1


@pytest.mark.asyncio
async def test_the_lone_row_path_reports_it_too():
    """The single-row path bypasses the batch pre-flight, and a lone ``allow_duplicate`` row
    bypasses even the lone-row matcher — so this MUST come from the choke itself."""
    deleted_id = _book_then_delete()

    for extra in ({}, {"allow_duplicate": True}):
        out = await _add([_row()], **extra)
        assert out["ok"] is True and out["created"] == 0, extra
        assert out["summary"] == "1 submitted → 1 previously_deleted", extra
        assert out["results"] == [{"index": 0, "status": "previously_deleted", "id": None}]
        assert out["previously_deleted"][0]["deleted_id"] == deleted_id
    assert _live() == []


def test_the_service_choke_raises_even_under_force():
    _book_then_delete()
    with pytest.raises(PreviouslyDeletedError) as exc:
        L.create_transaction(_svc_row(), allow_duplicate=True)
    assert exc.value.code == "previously_deleted"
    assert "already deleted" in exc.value.message
    assert exc.value.context["deleted_txn_id"]


def test_bulk_create_skips_it_under_force_and_names_it():
    deleted_id = _book_then_delete()
    created, skipped = L.bulk_create_transactions([_svc_row()], allow_duplicate=True)
    assert created == []
    assert skipped[0]["_previously_deleted"] is True
    assert skipped[0]["_duplicate_of"] == deleted_id
    assert skipped[0]["_batch_index"] == 0


def test_a_live_row_with_the_ref_still_wins_as_already_recorded():
    """L0 runs first: a stale memory next to a live row is "already recorded", never a
    refusal — the answer that returns the existing row is the safe one."""
    deleted_id = _book_then_delete()
    L.transactions.update_by_id(deleted_id, {"deleted": False})

    meta: dict = {}
    got = L.create_transaction(_svc_row(), meta_out=meta)
    assert got["id"] == deleted_id and meta["idempotent_existing_id"] == deleted_id


def test_editing_a_live_row_never_trips_the_memory():
    """update_transaction's inner rebuild re-books the SAME live row; a stale record must not
    turn an edit into a refusal."""
    deleted_id = _book_then_delete()
    L.transactions.update_by_id(deleted_id, {"deleted": False})
    updated = L.update_transaction(deleted_id, {"description": "RIDGELINE HOTEL (voided)"})
    assert updated["description"] == "RIDGELINE HOTEL (voided)"


# ── 2. The way back is explicit, and resurrecting forgets ────────────────────────────


@pytest.mark.asyncio
async def test_a_create_decision_resurrects_and_forgets():
    _book_then_delete()

    back = await _add([_row()], decisions=[{"index": 0, "action": "create"}])

    assert back["created"] == 1 and back["results"][0]["status"] == "created"
    assert back["resurrected"] == [0]
    assert len(_live()) == 1
    assert R.store.all() == [], "resurrecting forgets — the next delete starts clean"


@pytest.mark.asyncio
async def test_deleting_the_resurrected_row_is_remembered_again():
    _book_then_delete()
    back = await _add([_row()], decisions=[{"index": 0, "action": "create"}])
    L.bulk_delete_transactions([back["results"][0]["id"]])

    again = await _add([_row()], import_description="mailbox")

    assert again["created"] == 0 and again["results"][0]["status"] == "previously_deleted"


@pytest.mark.asyncio
async def test_a_create_decision_only_reaches_its_own_index():
    _book_then_delete()
    _book_then_delete(ref="dddd2222eeee")

    out = await _add(
        [_row(), _row("LUNCH", 12.0, ref="dddd2222eeee")],
        decisions=[{"index": 1, "action": "create"}],
    )

    assert [r["status"] for r in out["results"]] == ["previously_deleted", "created"]
    assert [r["ref"] for r in R.store.all()] == [REF]


def test_service_resurrect_books_and_forgets():
    _book_then_delete()
    meta: dict = {}
    L.create_transaction(_svc_row(), resurrect=True, meta_out=meta)
    assert meta["resurrected_from"]
    assert len(_live()) == 1 and R.store.all() == []


def test_forgetting_the_record_lets_the_next_delivery_book():
    _book_then_delete()
    [rec] = R.list_rejected()["rejected"]
    assert R.forget(rec["id"]) is True
    L.create_transaction(_svc_row())
    assert len(_live()) == 1


def test_rest_list_and_forget():
    from fastapi.testclient import TestClient

    from backend.api.matches import router

    deleted_id = _book_then_delete()
    client = TestClient(_app(router))

    listed = client.get("/api/matches/rejected").json()
    assert listed["total"] == 1 and listed["rejected"][0]["txn_id"] == deleted_id

    assert client.delete(f"/api/matches/rejected/{listed['rejected'][0]['id']}").json() == {
        "ok": True
    }
    assert client.delete("/api/matches/rejected/nope").status_code == 404
    assert client.get("/api/matches/rejected").json()["total"] == 0


def test_rest_create_refuses_with_a_typed_409_and_names_the_way_back():
    from backend.api.ledger import router

    deleted_id = _book_then_delete()
    client = _client(router)
    body = {
        "kind": "expense",
        "date": "2026-08-29",
        "description": "RIDGELINE HOTEL",
        "from_account_id": "acc_card",
        "from_amount": 250.0,
        "from_currency": "SGD",
        "external_refs": [{"source": "email", "ref": REF}],
    }

    refused = client.post("/api/transactions", json={**body, "allow_duplicate": True})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error"] == "previously_deleted"
    assert detail["deleted_id"] == deleted_id
    assert detail["retry_with"] == {"resurrect": True}

    booked = client.post("/api/transactions", json={**body, "resurrect": True})
    assert booked.status_code == 200, booked.text
    assert R.store.all() == []


# ── 3. Movement scope ─────────────────────────────────────────────────────────────────


def test_the_other_leg_of_a_deleted_wise_chain_still_books():
    """One confirmation email, two legit legs: card → wallet and wallet → cash. Deleting the
    first must not reject the second — the memory is keyed on the movement, like L0."""
    _book_then_delete(frm="acc_card", to="acc_wallet", kind="transfer")

    other_leg = L.create_transaction(_svc_row(frm="acc_wallet", to="acc_cash", kind="transfer"))
    assert other_leg["id"]
    _, skipped = L.bulk_create_transactions(
        [_svc_row(frm="acc_card", to="acc_wallet", kind="transfer")], allow_duplicate=True
    )
    assert skipped and skipped[0]["_previously_deleted"] is True


# ── 4. Rolling back is not rejecting ──────────────────────────────────────────────────


def test_batch_rollback_and_delete_where_by_batch_write_nothing():
    created, _ = L.bulk_create_transactions([_svc_row()], batch_id="batch-1")
    L.transactions.bulk_soft_delete([created[0]["id"]])
    assert R.store.all() == []

    L.transactions.update_by_id(created[0]["id"], {"deleted": False})
    assert L.delete_transactions_where(batch_id="batch-1") == 1
    assert R.store.all() == []


def test_delete_where_by_anything_else_remembers():
    L.create_transaction(_svc_row())
    assert L.delete_transactions_where(account_id="acc_card") == 1
    assert [r["ref"] for r in R.store.all()] == [REF]
    with pytest.raises(PreviouslyDeletedError):
        L.create_transaction(_svc_row())


def test_reject_identities_false_writes_nothing():
    row = L.create_transaction(_svc_row())
    out = L.bulk_delete_transactions([row["id"]], reject_identities=False)
    assert out["rejected"] == 0 and R.store.all() == []


def test_every_trade_undo_opts_out():
    """The drift guard: a trade undo reverses ITS OWN cash leg. Any composed delete that forgot
    the flag would reject a broker confirmation the user never rejected."""
    root = pathlib.Path(__file__).resolve().parents[1] / "backend" / "services"
    for name in ("equity.py", "lots.py"):
        src = (root / name).read_text()
        calls = [
            line
            for line in src.splitlines()
            if "bulk_delete_transactions(" in line and "conn=conn" in line
        ]
        assert calls, name
        assert all("reject_identities=False" in line for line in calls), (name, calls)


def test_a_merge_tombstone_writes_nothing():
    keep = L.create_transaction(_svc_row(ref="keep0000ref0"))
    lose = L.create_transaction(_svc_row(ref="lose0000ref0"), allow_duplicate=True)
    L.merge_transactions(keep["id"], lose["id"], override=True)
    tomb = L.transactions.find_by_id(lose["id"])
    assert tomb["merged_into"] == keep["id"]
    assert R.store.all() == []


def test_a_failed_statement_commit_forgets_its_staged_deletes():
    from backend.services.statements import decisions

    deleted_id = _book_then_delete()
    decisions._restore_deleted([deleted_id])
    assert L.transactions.find_by_id(deleted_id)["deleted"] is False
    assert R.store.all() == []


def test_the_statement_include_anyway_path_reports_it_not_drops_it(monkeypatch):
    from backend.services.statements import decisions

    deleted_id = _book_then_delete()

    class _Store:
        def add_batch_id(self, *a, **k):
            pass

        def update_by_id(self, *a, **k):
            pass

    monkeypatch.setattr(decisions, "_statement_store", lambda: _Store())
    batch = L.import_batches.create_batch(description="stmt", transaction_count=1)
    out = decisions._apply_rows(
        "stmt_1",
        {},
        [{}],
        "pdf",
        "acc_card",
        [],
        [],
        0,
        0,
        0,
        None,
        [_svc_row()],
        batch,
        [],
        [],
    )

    assert out["created"] == 0
    [refused] = out["refused_duplicates"]
    assert refused["previously_deleted"] is True and refused["duplicate_of"] == deleted_id


# ── helpers ───────────────────────────────────────────────────────────────────────────


def _app(router):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return app


def _client(router):
    from fastapi.testclient import TestClient

    return TestClient(_app(router))
