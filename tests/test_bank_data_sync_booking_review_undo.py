"""Bank data sync — card payments book as transfers, Review teaches the payee, a run can be
undone (ADR 2026-11-13, Amendment 13). Real runner, connector_fetch and ledger chokes; only the
bank is fake (``tests/bank_sync_fakes.py``). Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import runner, settings
from tests.bank_sync_fakes import (
    ACC_MAIN,
    ACC_SAVER,
    FakeBank,
    configure_gocardless,
    days_ago,
    linked_account,
    txn,
)

GC = PROVIDERS["gocardless"]

# Its accounts hold foreign currencies: seed synthetic rates, never a live FX fetch.
pytestmark = [pytest.mark.flags(bank_data_sync=True), pytest.mark.usefixtures("fx_rates")]


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    settings.save("gocardless", mode="data")
    return fake


def _card() -> str:
    return accounts_service.create_account(
        {
            "name": "ACME Card",
            "kind": "liability",
            "type": "credit",
            "currency": "GBP",
            "initial_balance": -500.00,
            "external_ids": [{"source": "gocardless", "ref": ACC_SAVER}],
        }
    )["id"]


def _run() -> dict:
    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(kind: str | None = None) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and t["id"] not in opening and (kind is None or t["kind"] == kind)
    ]


def _typed(kind: str, **fields) -> dict:
    return ledger_service.create_transaction(
        {
            "kind": kind,
            "date": days_ago(1),
            "description": "typed by hand",
            "cleared": True,
            **fields,
        }
    )


# ── 1. card payments are transfers ──────────────────────────────────────────────────────────


def test_a_card_payment_read_from_both_sides_is_one_transfer(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    bank.booked[ACC_MAIN] = [txn(-300.00, days_ago(2), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(300.00, days_ago(1), "Payment Thank You-Mobile", tid="i1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1
    assert _live("income") == [] and _live("expense") == []
    (transfer,) = _live("transfer")
    assert (transfer["from_account_id"], transfer["to_account_id"]) == (main, card)
    assert transfer["from_amount"] == transfer["to_amount"] == 300.00
    assert {e["ref"].split(":")[-1] for e in transfer["external_refs"]} >= {"o1", "i1"}
    assert transfer["needs_review"] is True
    again = _run()  # both refs are known: nothing books twice
    assert again["counts"]["booked"] == 0 and len(_live()) == 1


def test_a_card_payment_meets_a_row_typed_by_hand(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    typed = _typed("expense", from_account_id=main, from_amount=120.0, from_currency="GBP")
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1 and result["counts"]["booked"] == 0
    (transfer,) = _live()
    assert transfer["id"] == typed["id"] and transfer["kind"] == "transfer"
    assert transfer["to_account_id"] == card


def test_a_stray_uncleared_row_typed_by_hand_is_never_rewritten(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    typed = _typed(
        "expense", from_account_id=main, from_amount=120.0, from_currency="GBP", cleared=False
    )
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert ledger_service.transactions.find_by_id(typed["id"])["kind"] == "expense"
    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]


def test_an_uncleared_row_from_another_connector_still_pairs(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    other = _typed(
        "expense",
        from_account_id=main,
        from_amount=120.0,
        from_currency="GBP",
        cleared=False,
        external_refs=[{"source": "plaid", "ref": "plaid-1"}],
    )
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert ledger_service.transactions.find_by_id(other["id"])["kind"] == "transfer"


def test_the_paying_side_arriving_later_pairs_with_the_card_income(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_SAVER] = [txn(75.00, days_ago(2), "ACME PAYMENT THANK YOU", tid="i1")]
    _run()
    assert [t["kind"] for t in _live()] == ["income"]  # no counterpart yet: what the bank says
    bank.booked[ACC_MAIN] = [txn(-75.00, days_ago(2), "ACME CREDIT CARD PAYMENT", tid="o1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1
    (transfer,) = _live()
    assert transfer["kind"] == "transfer" and transfer["from_account_id"] == main


def test_a_merchant_refund_is_not_a_card_payment(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(1), "ACME ELECTRIC BILL PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(40.00, days_ago(1), "ACME SHOP REFUND", tid="i1")]

    _run()

    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]


def test_an_ambiguous_match_pairs_nothing(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [
        txn(-60.00, days_ago(2), "ACME CARD PAYMENT", tid="o1"),
        txn(-60.00, days_ago(1), "ACME CARD PAYMENT", tid="o2"),
    ]
    bank.booked[ACC_SAVER] = [txn(60.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert _live("transfer") == [] and len(_live()) == 3


def test_a_payment_further_than_the_window_is_not_paired(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-60.00, days_ago(9), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(60.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert _live("transfer") == []


# ── 2. Review teaches the payee ─────────────────────────────────────────────────────────────


def _category(name: str) -> str:
    from backend.services import categories as categories_service

    return categories_service.create_category(name, "expense")["id"]


def _booked(text: str) -> dict:
    return next(t for t in _live("expense") if t["description"] == text)


def test_categorising_a_synced_row_in_review_teaches_its_payee(bank):
    linked_account("ACME Current", ACC_MAIN)
    groceries = _category("Groceries")
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(3), "ACME GROCER", tid="t1")]
    _run()
    first = _booked("ACME GROCER")
    assert first["category"] is None and first["needs_review"] is True

    ledger_service.update_transaction(first["id"], {"category": groceries, "needs_review": False})
    bank.booked[ACC_MAIN].append(txn(-22.00, days_ago(1), "ACME GROCER", tid="t2"))
    _run()

    second = next(t for t in _live("expense") if t["id"] != first["id"])
    assert second["category"] == groceries
    assert second["payee_id"] == first["payee_id"]


def test_review_never_overwrites_a_payees_existing_default(bank):
    from backend.services import payee_registry

    linked_account("ACME Current", ACC_MAIN)
    groceries, dining = _category("Groceries"), _category("Dining")
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(3), "ACME GROCER", tid="t1")]
    _run()
    row = _booked("ACME GROCER")
    ledger_service.update_transaction(row["id"], {"category": groceries})

    ledger_service.update_transaction(row["id"], {"category": dining})

    assert payee_registry.default_category_for(row["payee_id"], "expense") == groceries


# ── 3. undo a run ───────────────────────────────────────────────────────────────────────────


def _undo(run: dict) -> dict:
    from backend.services.bank_data_sync import undo

    return undo.undo_run("gocardless", run["run_id"])


def _balances(*ids: str) -> tuple[float, ...]:
    book = ledger_service.balances()
    return tuple(round(book[i], 2) for i in ids)


def test_a_run_records_its_batch_and_undo_removes_what_it_booked(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    before = _balances(main, card)
    bank.booked[ACC_MAIN] = [
        txn(-300.00, days_ago(2), "ACME CARD PAYMENT", tid="o1"),
        txn(-10.00, days_ago(5), "ACME CAFE", tid="o2"),
    ]
    bank.booked[ACC_SAVER] = [txn(300.00, days_ago(1), "Payment Thank You-Mobile", tid="i1")]
    run = _run()
    batch = ledger_service.import_batches.find_by_id(f"bank_sync:{run['run_id']}")
    assert batch["status"] == "active" and batch["transaction_count"] == 2
    assert run["counts"]["undoable"] == 1

    result = _undo(run)

    assert result["removed"] == 2
    assert _live() == []
    assert _balances(main, card) == before  # the first import's moved start is put back
    assert ledger_service.import_batches.find_by_id(batch["id"])["status"] == "rolled_back"


def test_undone_rows_are_held_for_review_not_booked_again(bank):
    from backend.services.bank_data_sync import review_actions, reviews

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o2")]
    run = _run()
    _undo(run)

    items = reviews.open_items("gocardless")
    assert [(i["kind"], len(i["detail"]["rows"]), i["txn_id"]) for i in items] == [
        ("undone", 1, None)
    ]
    again = _run()
    assert again["counts"]["booked"] == 0 and again["counts"]["held"] == 1
    assert _live() == []

    review_actions.book_separately(items[0]["id"])  # "Book again"

    (row,) = _live()
    assert row["description"] == "ACME CAFE" and row["from_amount"] == 10.00


def test_an_undone_run_is_one_review_item_however_many_rows_it_booked(bank):
    from backend.services.bank_data_sync import review_actions, reviews

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-10.00, days_ago(5), "ACME CAFE", tid="o1"),
        txn(-4.00, days_ago(4), "ACME BUS", tid="o2"),
        txn(-6.00, days_ago(3), "ACME KIOSK", tid="o3"),
    ]
    result = _undo(_run())

    (item,) = reviews.open_items("gocardless")
    assert result["held_for_review"] == 3 and len(item["detail"]["rows"]) == 3
    assert _run()["counts"]["held"] == 3 and _live() == []

    bus = next(e for e in item["detail"]["rows"] if e["row"]["description"] == "ACME BUS")
    review_actions.book_separately(item["id"], ref=bus["refs"][0])  # "Book again" on one row

    assert [t["description"] for t in _live()] == ["ACME BUS"]
    (item,) = reviews.open_items("gocardless")  # the other two still wait, still held
    assert len(item["detail"]["rows"]) == 2 and _run()["counts"]["booked"] == 0

    review_actions.book_separately(item["id"], seen=item["updated_at"])  # "Book all again"

    assert {t["description"] for t in _live()} == {"ACME CAFE", "ACME BUS", "ACME KIOSK"}
    assert reviews.open_items("gocardless") == []


def test_a_dismissed_undone_row_stays_out(bank):
    from backend.services.bank_data_sync import review_actions, reviews

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o2")]
    _undo(_run())
    review_actions.dismiss(reviews.open_items("gocardless")[0]["id"])

    _run()

    assert _live() == [] and reviews.open_items("gocardless") == []


def test_undo_brings_back_the_pending_bookings_the_run_removed(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.pending[ACC_MAIN] = [txn(-8.00, days_ago(0), "ACME KIOSK", tid="p1")]
    bank.booked[ACC_MAIN] = [txn(-2.00, days_ago(1), "ACME BUS", tid="b1")]
    _run()
    bank.pending[ACC_MAIN] = []  # the bank drops the hold
    bank.booked[ACC_MAIN].append(txn(-3.00, days_ago(0), "ACME TRAM", tid="b2"))
    dropped = _run()
    assert {t["description"] for t in _live()} == {"ACME BUS", "ACME TRAM"}

    result = _undo(dropped)

    assert result["restored"] == 1
    assert {t["description"] for t in _live()} == {"ACME BUS", "ACME KIOSK"}
    assert main


def test_only_the_latest_run_can_be_undone(bank):
    from backend.core.errors import ConflictError

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o1")]
    first = _run()
    bank.booked[ACC_MAIN].append(txn(-4.00, days_ago(1), "ACME BUS", tid="o2"))
    second = _run()

    with pytest.raises(ConflictError, match="Undo that one first"):
        _undo(first)
    _undo(second)
    _undo(first)

    assert _live() == []


def test_a_run_that_changed_nothing_has_nothing_to_undo(bank):
    from backend.core.errors import ConflictError

    linked_account("ACME Current", ACC_MAIN)
    run = _run()

    assert "undoable" not in run["counts"] or run["counts"]["undoable"] == 0
    with pytest.raises(ConflictError, match="nothing to undo"):
        _undo(run)


def test_undo_folds_a_promoted_row_back_to_what_it_was(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    typed = _typed("expense", from_account_id=main, from_amount=120.0, from_currency="GBP")
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]
    run = _run()
    assert ledger_service.transactions.find_by_id(typed["id"])["kind"] == "transfer"

    _undo(run)

    row = ledger_service.transactions.find_by_id(typed["id"])
    assert row["kind"] == "expense" and not row.get("deleted")


def test_the_undo_route_answers_409_with_the_reason(bank):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.bank_sync_api import router

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o1")]
    run = _run()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    path = f"/api/bank-sync/gocardless/runs/{run['run_id']}/undo"

    ok = client.post(path)
    again = client.post(path)

    assert ok.status_code == 200 and ok.json()["removed"] == 1
    assert again.status_code == 409 and "already undone" in again.json()["detail"]


def test_undo_never_counts_a_pending_row_and_its_settled_successor_twice(bank):
    from backend.services.bank_data_sync import review_actions, reviews

    linked_account("ACME Current", ACC_MAIN)
    bank.pending[ACC_MAIN] = [txn(-10.00, days_ago(0), "ACME HOTEL", tid="h1")]
    _run()
    bank.pending[ACC_MAIN] = []
    bank.booked[ACC_MAIN] = [txn(-12.00, days_ago(0), "ACME HOTEL", tid="h1")]
    settled = _run()  # the settled 12.00 replaces the pending 10.00
    assert [t["from_amount"] for t in _live("expense")] == [12.00]

    _undo(settled)

    assert _live("expense") == []  # the replaced pending row stays gone
    (item,) = reviews.open_items("gocardless")
    review_actions.book_separately(item["id"])
    assert [t["from_amount"] for t in _live("expense")] == [12.00]


def test_a_pending_row_undo_brings_back_is_tracked_again(bank):
    from backend.services.bank_data_sync import pending

    linked_account("ACME Current", ACC_MAIN)
    bank.pending[ACC_MAIN] = [txn(-8.00, days_ago(0), "ACME KIOSK", tid="p1")]
    bank.booked[ACC_MAIN] = [txn(-2.00, days_ago(1), "ACME BUS", tid="b1")]
    _run()
    bank.pending[ACC_MAIN] = []
    dropped = _run()
    assert {t["description"] for t in _live()} == {"ACME BUS"}

    _undo(dropped)

    (entry,) = pending.tracked("gocardless").values()
    assert ledger_service.transactions.find_by_id(entry["txn_id"])["description"] == "ACME KIOSK"


def test_book_again_on_an_undone_card_payment_returns_one_transfer(bank):
    from backend.services.bank_data_sync import review_actions, reviews

    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    bank.booked[ACC_MAIN] = [txn(-300.00, days_ago(2), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(300.00, days_ago(1), "Payment Thank You-Mobile", tid="i1")]
    _undo(_run())

    (item,) = reviews.open_items("gocardless")  # ONE item holding both bank rows
    (entry,) = item["detail"]["rows"]
    assert entry["pair"] and len(entry["refs"]) == 2
    assert _run()["counts"]["held"] == 2  # and both refs stay held
    review_actions.book_separately(item["id"])

    (transfer,) = _live()
    assert transfer["kind"] == "transfer"
    assert (transfer["from_account_id"], transfer["to_account_id"]) == (main, card)


def _promoted_by_a_run(bank, **typed_fields):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    typed = _typed(
        "expense", from_account_id=main, from_amount=120.0, from_currency="GBP", **typed_fields
    )
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]
    return typed, _run()


def test_undoing_a_promotion_keeps_your_later_edits_to_other_fields(bank):
    typed, run = _promoted_by_a_run(bank)
    ledger_service.update_transaction(typed["id"], {"tags": ["mine"]})

    _undo(run)

    row = ledger_service.transactions.find_by_id(typed["id"])
    assert row["kind"] == "expense" and row["tags"] == ["mine"]


def test_undoing_a_promotion_does_not_bring_back_a_row_you_deleted(bank):
    typed, run = _promoted_by_a_run(bank)
    ledger_service.bulk_delete_transactions([typed["id"]])

    _undo(run)

    assert ledger_service.transactions.find_by_id(typed["id"])["deleted"] is True
    assert _live() == []


def test_a_promoted_row_you_changed_in_the_same_fields_is_left_alone_and_said_so(bank):
    # Another connector's uncleared row: the promotion clears it, so clearing is a promoted field.
    typed, run = _promoted_by_a_run(
        bank, cleared=False, external_refs=[{"source": "plaid", "ref": "plaid-1"}]
    )
    ledger_service.update_transaction(typed["id"], {"cleared": False})

    result = _undo(run)

    assert ledger_service.transactions.find_by_id(typed["id"])["kind"] == "transfer"
    assert len(result["not_reverted"]) == 1 and "changed it since" in result["not_reverted"][0]


# ── silent failures are visible ─────────────────────────────────────────────────────────────


def test_a_run_whose_record_could_not_be_saved_says_it_cannot_be_undone(bank, monkeypatch):
    from backend.services import import_runs
    from backend.services.bank_data_sync import undo

    def broken(*args, **kwargs):
        raise RuntimeError("db busy")

    monkeypatch.setattr(undo, "record", broken)
    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o1")]

    run = _run()

    warnings = [str(w) for w in import_runs.get(run["run_id"])["warnings"]]
    assert any("can't be undone" in w for w in warnings)
    assert len(_live()) == 1  # still booked


def test_an_opening_that_could_not_be_put_back_is_said_on_the_run(bank, monkeypatch):
    from backend.services import import_runs

    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-10.00, days_ago(5), "ACME CAFE", tid="o1")]
    run = _run()

    def broken(*args, **kwargs):
        raise RuntimeError("db busy")

    monkeypatch.setattr(ledger_service, "set_account_opening", broken)
    result = _undo(run)

    assert any("couldn't be put back" in s for s in result["not_reverted"])
    warnings = [str(w) for w in import_runs.get(run["run_id"])["warnings"]]
    assert any("couldn't be put back" in w for w in warnings)
