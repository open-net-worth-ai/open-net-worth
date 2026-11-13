"""Deleting a transaction never leaves something the user can't fix — synthetic data only.

Every record that points at a transaction is either released in the delete's own database
transaction or named in ``NOT_RELEASED`` with the reason. For each link: delete the row, then every
edit and undo on the record that pointed at it must still work. ADR
2026-11-06-deleting-a-transaction-leaves-nothing-unfixable.
"""

# ruff: noqa: F811  (the shared fixtures are imported, then requested by name)
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from backend.ai.tools import execute_tool, registry
from backend.db.account_store import AccountStore
from backend.db.commitments_store import CommitmentsStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.lot_store import LotStore
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services import commitments as commitments_service
from backend.services import income as income_service
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import owned_assets as owned_assets_service
from backend.services import transaction_links
from tests.test_lot_cash_link import _buy, _live_ids, _twin_of, book  # noqa: F401
from tests.test_position_figures import isolated  # noqa: F401


def _delete(*ids: str) -> dict:
    return ledger_service.bulk_delete_transactions(list(ids))


def _kinds(result: dict) -> set[str]:
    return {link["kind"] for link in result.get("released_links") or []}


# ── Sale proceeds ────────────────────────────────────────────────────────────────────────


def _sell(book, *, source="manual") -> dict:
    return lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": book["pid"],
            "quantity": 4,
            "proceeds_per_unit": 25.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-02-01",
            "method": "fifo",
            "proceeds_account_id": book["cash"],
            "source": source,
        }
    )


def _edit_sale(book, event_id: str, **fields) -> dict:
    return lots_service.apply_lot_change(
        {
            "type": "dispose_amend",
            "event_id": event_id,
            "quantity": 4,
            "proceeds_per_unit": 26.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-02-01",
            "method": "fifo",
            "proceeds_account_id": book["cash"],
            **fields,
        }
    )


def test_deleting_a_sales_cash_row_keeps_the_sale_and_clears_its_deposit(book):
    _buy(book)
    sold = _sell(book)

    result = _delete(sold["cash_leg_id"])

    event = RealizedEventStore().find_by_id(sold["event_id"])
    assert "sale" in _kinds(result)
    assert (event["txn_id"], event["proceeds_account_id"]) == (None, None)
    assert event["quantity"] == 4


def test_editing_a_sale_after_its_cash_row_is_gone_does_not_book_the_cash_again(book):
    bought = _buy(book)
    sold = _sell(book)
    _delete(sold["cash_leg_id"])
    live = _live_ids()

    edited = _edit_sale(book, sold["event_id"], proceeds_account_id=None)

    assert edited["ok"] is True
    assert _live_ids() == live, f"only the purchase {bought['cash_leg_id']} stays"


def test_editing_a_sale_with_a_dead_link_from_old_data_is_not_refused(book):
    _buy(book)
    sold = _sell(book)
    twin = ledger_service.create_transaction(
        {
            "date": "2026-02-01",
            "kind": "investment",
            "description": "Sold 4 ACME at 25.00",
            "to_account_id": book["cash"],
            "to_amount": 100.0,
            "to_currency": "SGD",
            "source": "email",
        },
        allow_duplicate=True,
    )["id"]
    TransactionStore().soft_delete(sold["cash_leg_id"])  # how a delete used to leave it

    edited = _edit_sale(book, sold["event_id"])  # the form resends the account it showed

    assert edited["ok"] is True
    assert edited["cash_leg_id"] is None
    assert twin in _live_ids()
    assert "cash" in edited["cash_note"].lower()


def test_choosing_a_different_account_on_a_sale_books_cash_again(book):
    _buy(book)
    sold = _sell(book)
    _delete(sold["cash_leg_id"])
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )

    edited = _edit_sale(book, sold["event_id"], proceeds_account_id=other["id"])

    assert edited["cash_leg_id"] in _live_ids()


def test_editing_a_sale_booked_elsewhere_edits_its_own_cash_row(book):
    _buy(book)
    sold = _sell(book, source="automation")

    edited = _edit_sale(book, sold["event_id"])

    assert edited["ok"] is True
    assert edited["cash_leg_id"] == sold["cash_leg_id"]
    assert TransactionStore().find_by_id(edited["cash_leg_id"])["to_amount"] == 104.0


def test_undoing_a_sale_after_its_cash_row_is_gone_works(book):
    bought = _buy(book)
    sold = _sell(book)
    TransactionStore().soft_delete(sold["cash_leg_id"])
    audit_before = _audit_count(sold["cash_leg_id"])

    undone = lots_service.apply_lot_change({"type": "dispose_undo", "event_id": sold["event_id"]})

    assert undone["ok"] is True
    assert LotStore().find_by_id(bought["lot_id"])["remaining_qty"] == 10
    assert _audit_count(sold["cash_leg_id"]) == audit_before, "the dead row isn't deleted twice"


def test_removing_a_lot_after_its_cash_row_is_gone_works(book):
    bought = _buy(book)
    TransactionStore().soft_delete(bought["cash_leg_id"])
    audit_before = _audit_count(bought["cash_leg_id"])

    removed = lots_service.apply_lot_change({"type": "delete", "lot_id": bought["lot_id"]})

    assert removed["ok"] is True
    assert _audit_count(bought["cash_leg_id"]) == audit_before


def _audit_count(txn_id: str) -> int:
    from backend.db.transaction_audit_store import TransactionAuditStore

    return len(TransactionAuditStore().query(txn_id=txn_id))


# ── Commitments ──────────────────────────────────────────────────────────────────────────


def _commitment(book, txn_id: str, *, due="2020-01-01") -> tuple[str, str, str]:
    commitment = commitments_service.create_commitment(
        {
            "name": "School fees",
            "installments": [{"id": "inst-1", "label": "Term 1", "due_date": due, "amount": 200}],
            "funding_plan": [{"id": "fund-1", "label": "Bonus", "amount": 200}],
        }
    )
    cid = commitment["id"]
    commitments_service.update_installment(cid, "inst-1", {"status": "paid", "paid_txn_id": txn_id})
    commitments_service.update_funding_source(
        cid, "fund-1", {"status": "done", "realized_txn_id": txn_id}
    )
    return cid, "inst-1", "fund-1"


def _row(cid: str, key: str, row_id: str) -> dict:
    rows = CommitmentsStore().find_by_id(cid)[key]
    return next(row for row in rows if row["id"] == row_id)


def test_deleting_a_payment_puts_a_past_installment_back_to_due(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    result = _delete(bought["cash_leg_id"])

    inst = _row(cid, "installments", inst_id)
    assert "installment" in _kinds(result)
    assert (inst["paid_txn_id"], inst["status"]) == (None, "due")
    assert commitments_service.is_paid(inst) is False


def test_deleting_a_payment_puts_a_future_installment_back_to_upcoming(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"], due="2099-01-01")

    _delete(bought["cash_leg_id"])

    assert _row(cid, "installments", inst_id)["status"] == "upcoming"


def test_an_installment_can_be_removed_after_its_payment_is_deleted(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    _delete(bought["cash_leg_id"])

    updated, missing, refused = CommitmentsStore().remove_installments(cid, [inst_id])

    assert (missing, refused) == ([], [])
    assert updated["installments"] == []


def test_an_installment_marked_paid_by_hand_is_left_alone(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    commitments_service.update_installment(cid, inst_id, {"paid_txn_id": None})

    _delete(bought["cash_leg_id"])

    assert _row(cid, "installments", inst_id)["status"] == "paid"


def test_deleting_a_receipt_puts_funding_back_to_planned(book):
    bought = _buy(book)
    cid, _, fund_id = _commitment(book, bought["cash_leg_id"])

    result = _delete(bought["cash_leg_id"])

    source = _row(cid, "funding_plan", fund_id)
    assert "funding" in _kinds(result)
    assert (source["realized_txn_id"], source["status"]) == (None, "planned")
    assert commitments_service.funding_arrived(source) is False


# ── Asset sales ──────────────────────────────────────────────────────────────────────────


def _sold_asset(book) -> tuple[str, str, str]:
    cash = AccountStore().create(
        {"name": "Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    asset = owned_assets_service.apply_owned_asset_change(
        {"op": "create", "name": "Old bike", "category": "other", "value": 900}
    )
    owned_assets_service.apply_owned_asset_change(
        {"op": "dispose", "id": asset["id"], "proceeds": 500, "account_id": cash["id"]}
    )
    stored = OwnedAssetsStore().find_by_id(asset["id"])
    return asset["id"], cash["id"], stored["disposal_txn_id"]


def test_deleting_an_asset_sales_cash_keeps_it_sold_and_clears_the_link(book):
    asset_id, _, txn_id = _sold_asset(book)

    result = _delete(txn_id)

    asset = OwnedAssetsStore().find_by_id(asset_id)
    assert "asset_sale" in _kinds(result)
    assert asset["archived"] is True
    assert asset["disposal_txn_id"] is None


def test_an_asset_sale_can_book_its_cash_again_after_the_row_is_deleted(book):
    asset_id, cash_id, txn_id = _sold_asset(book)
    _delete(txn_id)

    owned_assets_service.apply_owned_asset_change(
        {"op": "dispose", "id": asset_id, "proceeds": 450, "account_id": cash_id}
    )

    asset = OwnedAssetsStore().find_by_id(asset_id)
    assert asset["disposal_proceeds"] == 450
    assert asset["disposal_txn_id"] in _live_ids()
    assert TransactionStore().find_by_id(asset["disposal_txn_id"])["to_amount"] == 450


def test_lucius_can_book_an_asset_sales_cash_again(book):
    asset_id, cash_id, txn_id = _sold_asset(book)
    _delete(txn_id)

    text, _ = asyncio.run(
        execute_tool(
            "dispose_asset", {"asset_id": asset_id, "proceeds": 500, "account_id": cash_id}
        )
    )

    assert json.loads(text)["ok"] is True
    assert OwnedAssetsStore().find_by_id(asset_id)["disposal_txn_id"] in _live_ids()


def test_an_asset_sale_with_a_dead_link_from_old_data_can_book_again(book):
    asset_id, cash_id, txn_id = _sold_asset(book)
    TransactionStore().soft_delete(txn_id)

    owned_assets_service.apply_owned_asset_change(
        {"op": "dispose", "id": asset_id, "proceeds": 500, "account_id": cash_id}
    )

    assert OwnedAssetsStore().find_by_id(asset_id)["disposal_txn_id"] in _live_ids()


def test_a_sale_with_live_cash_still_cant_be_recorded_twice(book):
    from backend.core.errors import ValidationError

    asset_id, cash_id, _ = _sold_asset(book)

    with pytest.raises(ValidationError, match="recorded twice"):
        owned_assets_service.apply_owned_asset_change(
            {"op": "dispose", "id": asset_id, "proceeds": 450, "account_id": cash_id}
        )


# ── Income ───────────────────────────────────────────────────────────────────────────────


def test_deleting_a_dividends_cash_keeps_the_income_record(book, monkeypatch):
    monkeypatch.setattr(income_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(income_service, "events", IncomeEventStore())
    monkeypatch.setattr(income_service, "positions", PositionStore())
    recorded = income_service.apply_income_change(
        {
            "type": "record",
            "position_id": book["pid"],
            "gross_amount": 50.0,
            "tax_withheld": 0.0,
            "currency": "SGD",
            "account_id": book["cash"],
            "paid_at": "2026-05-01",
        }
    )

    result = _delete(recorded["txn_id"])

    assert "income" in _kinds(result)
    assert IncomeEventStore().find_by_id(recorded["event_id"])["txn_id"] is None
    assert income_service.apply_income_change({"type": "delete", "event_id": recorded["event_id"]})


# ── Merges keep the money, so links move instead of clearing ─────────────────────────────


def test_merging_a_linked_row_moves_every_link_to_the_surviving_row(book):
    bought = _buy(book)
    twin = _twin_of(book)
    cid, inst_id, fund_id = _commitment(book, bought["cash_leg_id"])

    ledger_service.merge_transactions(twin, bought["cash_leg_id"], override=True)

    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == twin
    inst = _row(cid, "installments", inst_id)
    assert (inst["paid_txn_id"], inst["status"]) == (twin, "paid")
    assert _row(cid, "funding_plan", fund_id)["realized_txn_id"] == twin


def test_undoing_a_merge_moves_the_links_back(book):
    bought = _buy(book)
    twin = _twin_of(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    ledger_service.merge_transactions(twin, bought["cash_leg_id"], override=True)

    ledger_service.unmerge_transactions(bought["cash_leg_id"])

    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    assert _row(cid, "installments", inst_id)["paid_txn_id"] == bought["cash_leg_id"]


# ── The app reversing its own cash row follows the same rule ─────────────────────────────


def test_correcting_a_lot_keeps_a_payment_linked(book):
    bought = _buy(book)
    cid, inst_id, fund_id = _commitment(book, bought["cash_leg_id"])

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    new_row = LotStore().find_by_id(bought["lot_id"])["txn_id"]
    inst = _row(cid, "installments", inst_id)
    assert (inst["paid_txn_id"], inst["status"]) == (new_row, "paid")
    assert _row(cid, "funding_plan", fund_id)["realized_txn_id"] == new_row


def test_correcting_a_sold_lot_keeps_a_payment_linked(book):
    bought = _buy(book)
    _sell(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    assert (
        _row(cid, "installments", inst_id)["paid_txn_id"]
        == LotStore().find_by_id(bought["lot_id"])["txn_id"]
    )


def test_editing_a_sale_keeps_a_funding_source_linked(book):
    _buy(book)
    sold = _sell(book)
    cid, _, fund_id = _commitment(book, sold["cash_leg_id"])

    edited = _edit_sale(book, sold["event_id"])

    source = _row(cid, "funding_plan", fund_id)
    assert (source["realized_txn_id"], source["status"]) == (edited["cash_leg_id"], "done")


def test_removing_a_sales_deposit_releases_a_funding_link(book):
    _buy(book)
    sold = _sell(book)
    cid, _, fund_id = _commitment(book, sold["cash_leg_id"])

    edited = _edit_sale(book, sold["event_id"], proceeds_account_id=None)

    assert edited["cash_leg_id"] is None
    source = _row(cid, "funding_plan", fund_id)
    assert (source["realized_txn_id"], source["status"]) == (None, "planned")


def test_undoing_a_sale_releases_a_funding_link_to_its_cash(book):
    _buy(book)
    sold = _sell(book)
    cid, _, fund_id = _commitment(book, sold["cash_leg_id"])

    lots_service.apply_lot_change({"type": "dispose_undo", "event_id": sold["event_id"]})

    source = _row(cid, "funding_plan", fund_id)
    assert (source["realized_txn_id"], source["status"]) == (None, "planned")


def test_removing_a_lot_releases_a_payment_link_to_its_cash(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    lots_service.apply_lot_change({"type": "delete", "lot_id": bought["lot_id"]})

    assert _row(cid, "installments", inst_id)["paid_txn_id"] is None


def test_removing_a_position_releases_a_payment_link_to_its_cash(book):
    from backend.services import positions as positions_service

    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    positions_service.apply_position_change({"type": "remove", "position_id": book["pid"]})

    assert bought["cash_leg_id"] not in _live_ids()
    assert _row(cid, "installments", inst_id)["paid_txn_id"] is None


def test_undoing_a_merge_leaves_a_link_made_since_alone(book):
    bought = _buy(book)
    twin = _twin_of(book)
    other = _twin_of(book, amount=999.0, on="2026-06-01")
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    ledger_service.merge_transactions(twin, bought["cash_leg_id"], override=True)
    commitments_service.update_installment(cid, inst_id, {"paid_txn_id": other})

    ledger_service.unmerge_transactions(bought["cash_leg_id"])

    assert _row(cid, "installments", inst_id)["paid_txn_id"] == other


def test_a_lot_linked_to_a_row_that_doesnt_exist_is_refused_plainly(book):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="not a transaction"):
        lots_service.apply_lot_change(
            {
                "type": "acquire",
                "position_id": book["pid"],
                "quantity": 1,
                "unit_cost": 5,
                "cost_currency": "SGD",
                "acquired_at": "2026-04-01",
                "txn_id": "txn_never_existed",
            }
        )


# ── A correction edits the cash row it already has ───────────────────────────────────────


_KEPT = {
    "category": None,  # filled by _dressed
    "memo": "my note",
    "cleared": True,
    "reconciled_at": "2026-02-02T00:00:00+00:00",
}


def _dressed(row_id: str) -> dict:
    """Give a cash row everything a user or a feed adds to it after booking."""
    from backend.services import categories

    category = categories.create_category("Investing", "expense")
    changes = {**_KEPT, "category": category["id"]}
    ledger_service.update_transaction(row_id, changes)
    ledger_service.update_transaction(
        row_id, {"external_refs": [{"source": "email", "ref": "<msg-acme-1@broker.test>"}]}
    )
    return TransactionStore().find_by_id(row_id)


def _assert_details_kept(before: dict, after: dict) -> None:
    for field in ("category", "memo", "cleared", "reconciled_at", "source", "external_refs"):
        assert after.get(field) == before.get(field), field


def test_correcting_a_lot_keeps_its_cash_row_and_everything_on_it(book):
    bought = _buy(book, source="automation")
    before = _dressed(bought["cash_leg_id"])

    assert lots_service.apply_lot_change(
        {"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0, "acquired_at": "2026-01-06"}
    )["ok"]

    lot = LotStore().find_by_id(bought["lot_id"])
    after = TransactionStore().find_by_id(bought["cash_leg_id"])
    assert lot["txn_id"] == bought["cash_leg_id"]
    assert after["deleted"] is False
    assert (after["from_amount"], after["date"]) == (201.0, "2026-01-06")
    assert "fee" in after["description"].lower()
    _assert_details_kept(before, after)


def test_correcting_a_sold_lot_keeps_its_cash_row(book):
    bought = _buy(book, source="automation")
    _sell(book)
    before = _dressed(bought["cash_leg_id"])

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    after = TransactionStore().find_by_id(bought["cash_leg_id"])
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    assert after["from_amount"] == 201.0
    _assert_details_kept(before, after)


def test_editing_a_sale_keeps_its_cash_row(book):
    _buy(book)
    sold = _sell(book, source="automation")
    before = _dressed(sold["cash_leg_id"])

    edited = _edit_sale(book, sold["event_id"])

    after = TransactionStore().find_by_id(sold["cash_leg_id"])
    assert edited["cash_leg_id"] == sold["cash_leg_id"]
    assert RealizedEventStore().find_by_id(edited["event_id"])["txn_id"] == sold["cash_leg_id"]
    assert after["to_amount"] == 104.0
    _assert_details_kept(before, after)


def test_a_re_delivered_email_is_still_recognised_after_a_correction(book):
    bought = _buy(book, source="automation")
    _dressed(bought["cash_leg_id"])
    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})
    live = _live_ids()

    again = ledger_service.create_transaction(
        {
            "date": "2026-01-05",
            "kind": "investment",
            "description": "Bought 10 ACME",
            "from_account_id": book["cash"],
            "from_amount": 200.0,
            "from_currency": "SGD",
            "source": "email",
            "external_refs": [{"source": "email", "ref": "<msg-acme-1@broker.test>"}],
        }
    )

    assert again["id"] == bought["cash_leg_id"]
    assert _live_ids() == live


def test_a_correction_shows_as_an_edit_in_the_rows_history(book):
    from backend.db.transaction_audit_store import TransactionAuditStore

    bought = _buy(book)

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    ops = [e["op"] for e in TransactionAuditStore().query(txn_id=bought["cash_leg_id"])]
    assert "update" in ops
    assert "delete" not in ops


def test_a_correction_keeps_the_rows_original_exchange_rate(book):
    bought = _buy(book)
    row_id = bought["cash_leg_id"]
    TransactionStore().update_by_id(row_id, {"fx_rate": 1.35, "base_amount": 270.0})

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    after = TransactionStore().find_by_id(row_id)
    assert after["base_amount"] == pytest.approx(201.0 * 1.35)


def test_a_correction_to_one_lot_on_a_shared_cash_row_keeps_the_other_lots_money(book):
    first = _buy(book, quantity=10, unit_cost=20)
    second = LotStore().create(
        {
            "position_id": book["pid"],
            "instrument_id": LotStore().find_by_id(first["lot_id"])["instrument_id"],
            "account_id": LotStore().find_by_id(first["lot_id"])["account_id"],
            "acquired_at": "2026-01-05",
            "original_qty": 5,
            "remaining_qty": 5,
            "unit_cost": 20,
            "cost_currency": "SGD",
            "kind": "buy",
            "txn_id": first["cash_leg_id"],
        }
    )
    TransactionStore().update_by_id(first["cash_leg_id"], {"from_amount": 300.0})

    lots_service.apply_lot_change({"type": "amend", "lot_id": first["lot_id"], "fees": 1.0})

    assert TransactionStore().find_by_id(first["cash_leg_id"])["from_amount"] == 301.0
    assert LotStore().find_by_id(second["id"])["txn_id"] == first["cash_leg_id"]


def test_a_date_only_correction_leaves_the_rows_base_value_exactly_alone(book):
    bought = _buy(book)
    row_id = bought["cash_leg_id"]
    TransactionStore().update_by_id(row_id, {"fx_rate": 1.351235, "base_amount": 270.25})

    lots_service.apply_lot_change(
        {"type": "amend", "lot_id": bought["lot_id"], "acquired_at": "2026-01-07"}
    )

    after = TransactionStore().find_by_id(row_id)
    assert after["date"] == "2026-01-07"
    assert after["base_amount"] == 270.25


def test_a_lot_paid_from_another_currency_keeps_the_rows_own_conversion(book):
    bought = _buy(book)
    row_id = bought["cash_leg_id"]
    LotStore().touch(bought["lot_id"], {"cost_currency": "USD"})
    TransactionStore().update_by_id(row_id, {"from_amount": 270.0, "from_currency": "SGD"})

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    after = TransactionStore().find_by_id(row_id)
    assert (after["from_amount"], after["from_currency"]) == (271.35, "SGD")


def test_a_shared_row_in_another_currency_moves_by_the_difference_in_its_own_units(
    book, monkeypatch
):
    first = _buy(book)
    row_id = first["cash_leg_id"]
    lot = LotStore().find_by_id(first["lot_id"])
    LotStore().create({**lot, "id": "lot_other", "idempotency_key": None})
    LotStore().touch(first["lot_id"], {"cost_currency": "USD"})
    LotStore().touch("lot_other", {"cost_currency": "USD"})
    TransactionStore().update_by_id(row_id, {"from_amount": 500.0, "from_currency": "SGD"})
    monkeypatch.setattr(lots_service, "_rate_between", lambda frm, to, rates: 1.3)

    lots_service.apply_lot_change({"type": "amend", "lot_id": first["lot_id"], "fees": 1.0})

    assert TransactionStore().find_by_id(row_id)["from_amount"] == 501.3


@pytest.mark.parametrize(
    "reshaped",
    [
        {"kind": "transfer", "to_account_id": "__other__", "to_amount": 200.0},
        {
            "kind": "income",
            "from_account_id": None,
            "from_amount": None,
            "to_account_id": "__cash__",
            "to_amount": 200.0,
        },
    ],
    ids=["merged_into_a_transfer", "rekinded_with_no_such_leg"],
)
def test_a_row_that_no_longer_looks_like_this_cash_never_blocks_a_correction(book, reshaped):
    bought = _buy(book)
    row_id = bought["cash_leg_id"]
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    swap = {"__other__": other["id"], "__cash__": book["cash"]}
    TransactionStore().update_by_id(row_id, {k: swap.get(v, v) for k, v in reshaped.items()})
    before = TransactionStore().find_by_id(row_id)

    done = lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    assert done["ok"] is True
    assert LotStore().find_by_id(bought["lot_id"])["fees"] == 1.0
    after = TransactionStore().find_by_id(row_id)
    assert (after["from_amount"], after["to_amount"]) == (
        before["from_amount"],
        before["to_amount"],
    )


def test_moving_a_lot_to_another_account_takes_the_new_accounts_payee(book, monkeypatch):
    from backend.services import payee_registry

    bought = _buy(book)
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    payee = payee_registry.create_payee("Other Broker")["id"]
    monkeypatch.setattr(lots_service, "_resolve_deposit_payee_if_needed", lambda change: payee)

    lots_service.apply_lot_change(
        {"type": "amend", "lot_id": bought["lot_id"], "funding_account_id": other["id"]}
    )

    assert TransactionStore().find_by_id(bought["cash_leg_id"])["payee_id"] == payee


def test_a_fee_correction_keeps_the_rows_payee(book, monkeypatch):
    from backend.services import payee_registry

    bought = _buy(book)
    kept = payee_registry.create_payee("My Broker")["id"]
    TransactionStore().update_by_id(bought["cash_leg_id"], {"payee_id": kept})
    other = payee_registry.create_payee("Other Broker")["id"]
    monkeypatch.setattr(lots_service, "_resolve_deposit_payee_if_needed", lambda change: other)

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    assert TransactionStore().find_by_id(bought["cash_leg_id"])["payee_id"] == kept


def test_a_note_only_sale_edit_keeps_a_description_you_wrote(book):
    _buy(book)
    sold = _sell(book)
    TransactionStore().update_by_id(sold["cash_leg_id"], {"description": "My own words"})

    _edit_sale(book, sold["event_id"], proceeds_per_unit=25.0, note="just a note")

    assert TransactionStore().find_by_id(sold["cash_leg_id"])["description"] == "My own words"


def test_changing_a_sales_figures_updates_the_apps_own_description(book):
    _buy(book)
    sold = _sell(book)

    _edit_sale(book, sold["event_id"])

    assert "26" in TransactionStore().find_by_id(sold["cash_leg_id"])["description"]


def test_changing_a_sales_figures_keeps_a_description_you_wrote(book):
    # Only the app's own trade text follows the figures (hand UAT DS-LOT-FEE, 2026-11-10;
    # ADR 2026-11-06-deleting-a-transaction-leaves-nothing-unfixable).
    _buy(book)
    sold = _sell(book)
    TransactionStore().update_by_id(sold["cash_leg_id"], {"description": "My own words"})

    _edit_sale(book, sold["event_id"])

    assert TransactionStore().find_by_id(sold["cash_leg_id"])["description"] == "My own words"


def test_choosing_no_account_removes_the_cash_row(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    lots_service.apply_lot_change(
        {"type": "amend", "lot_id": bought["lot_id"], "funding_account_id": None}
    )

    assert bought["cash_leg_id"] not in _live_ids()
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None
    assert _row(cid, "installments", inst_id)["paid_txn_id"] is None


def test_moving_a_lot_to_another_account_edits_the_row(book):
    bought = _buy(book)
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )

    lots_service.apply_lot_change(
        {"type": "amend", "lot_id": bought["lot_id"], "funding_account_id": other["id"]}
    )

    after = TransactionStore().find_by_id(bought["cash_leg_id"])
    assert after["from_account_id"] == other["id"]
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_a_correction_that_fails_leaves_the_row_and_the_lot_untouched(book, monkeypatch):
    from backend.services import position_figures

    bought = _buy(book)

    def boom(*args, **kwargs):
        raise RuntimeError("failed after the row was edited")

    monkeypatch.setattr(position_figures, "rederive", boom)

    with pytest.raises(RuntimeError):
        lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    assert TransactionStore().find_by_id(bought["cash_leg_id"])["from_amount"] == 200.0
    assert LotStore().find_by_id(bought["lot_id"])["fees"] == 0


def test_rewinding_a_correction_restores_the_row(book):
    from backend.ai import checkpoint as ckpt

    bought = _buy(book)

    ctx, token = ckpt.begin_turn("sess-correct-undo", anchor_history_len=0, message_preview="fee")
    try:
        lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})
    finally:
        entry = ckpt.end_turn(ctx, token)
    ckpt.restore(entry["id"], mode="data")

    assert TransactionStore().find_by_id(bought["cash_leg_id"])["from_amount"] == 200.0
    assert LotStore().find_by_id(bought["lot_id"])["fees"] == 0


def test_a_composed_edit_tells_the_app_only_after_the_commit(book, monkeypatch):
    bought = _buy(book)
    seen: list[float] = []
    real = ledger_service._publish_committed_transaction_events

    def spy(operation, rows, **kwargs):
        seen.append(TransactionStore().find_by_id(rows[0]["id"])["from_amount"])
        return real(operation, rows, **kwargs)

    monkeypatch.setattr(ledger_service, "_publish_committed_transaction_events", spy)

    lots_service.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "fees": 1.0})

    assert seen == [201.0], "fired once, and it could already read the committed row"


def test_an_ordinary_edit_still_works_on_its_own(book):
    bought = _buy(book)

    edited = ledger_service.update_transaction(bought["cash_leg_id"], {"memo": "just a note"})

    assert edited["memo"] == "just a note"
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


# ── The rule holds across the delete ─────────────────────────────────────────────────────


def test_a_failed_cleanup_rolls_the_delete_back(book, monkeypatch):
    bought = _buy(book)

    def boom(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(transaction_links, "release", boom)

    with pytest.raises(RuntimeError):
        _delete(bought["cash_leg_id"])

    assert bought["cash_leg_id"] in _live_ids()
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_rewinding_a_delete_restores_the_row_and_every_link(book):
    from backend.ai import checkpoint as ckpt

    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    ctx, token = ckpt.begin_turn("sess-link-undo", anchor_history_len=0, message_preview="delete")
    try:
        _delete(bought["cash_leg_id"])
    finally:
        entry = ckpt.end_turn(ctx, token)
    ckpt.restore(entry["id"], mode="data")

    assert bought["cash_leg_id"] in _live_ids()
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    inst = _row(cid, "installments", inst_id)
    assert (inst["paid_txn_id"], inst["status"]) == (bought["cash_leg_id"], "paid")


def test_an_import_rollback_releases_nothing(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    TransactionStore().update_by_id(bought["cash_leg_id"], {"batch_id": "batch_1"})

    ledger_service.delete_transactions_where(batch_id="batch_1")

    assert _row(cid, "installments", inst_id)["paid_txn_id"] == bought["cash_leg_id"]


def test_a_filtered_delete_releases_every_link(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])
    out: dict = {}

    ledger_service.delete_transactions_where(account_id=book["cash"], meta_out=out)

    assert _row(cid, "installments", inst_id)["paid_txn_id"] is None
    assert {"lot", "installment", "funding"} <= {link["kind"] for link in out["released_links"]}


# ── You see the effect before you confirm ────────────────────────────────────────────────


def test_the_preview_names_what_would_change_and_writes_nothing(book):
    bought = _buy(book)
    cid, inst_id, _ = _commitment(book, bought["cash_leg_id"])

    links = transaction_links.linked_to([bought["cash_leg_id"]])

    assert {link["kind"] for link in links} == {"lot", "installment", "funding"}
    assert all(link["label"] and link["effect"] for link in links)
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    assert _row(cid, "installments", inst_id)["paid_txn_id"] == bought["cash_leg_id"]


def test_the_delete_card_warns_about_a_linked_row(book):
    bought = _buy(book)

    adapter = registry.tool_preview_adapter("delete_transactions")
    preview = adapter.build({"transaction_ids": [bought["cash_leg_id"]]})

    assert preview["kind"] == "field_diff"
    assert adapter.validate(preview) is None
    text = json.dumps(preview["data"]["items"])
    assert bought["cash_leg_id"] in text
    assert "linked purchase" in text


def test_the_delete_card_stays_plain_for_a_row_nothing_points_at(book):
    twin = _twin_of(book)

    adapter = registry.tool_preview_adapter("delete_transactions")

    assert adapter.build({"transaction_ids": [twin]}) is None


def test_the_register_can_ask_what_a_delete_would_change(book):
    from backend.api.ledger import TxnDeletePreview, preview_transaction_delete

    bought = _buy(book)

    out = preview_transaction_delete(TxnDeletePreview(ids=[bought["cash_leg_id"]]))

    assert [link["kind"] for link in out["links"]] == ["lot"]


# ── A new link can't skip the rule ───────────────────────────────────────────────────────


# A store's field appears quoted in its code or, for a flat store, only in its schema docstring.
_FIELD = re.compile(r"""\b([a-z_]*txn_id|transaction_id)\b["']?\s*:""")
_NOT_STORES = {
    "investments_io.py",  # lots' import path, covered by the lot link
    "money_io.py",  # the ledger's own rows
    "portable_bridge.py",
    "tables.py",
    "transaction_store.py",
}


def _stored_links() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in Path("backend/db").glob("*.py"):
        if path.name in _NOT_STORES:
            continue
        found |= {(path.name, field) for field in _FIELD.findall(path.read_text())}
    return found


def test_every_stored_link_to_a_transaction_is_released_or_explained():
    known = set(transaction_links.RELEASED_FIELDS) | set(transaction_links.NOT_RELEASED)

    assert _stored_links() - known == set(), (
        "A store now points at a transaction. Add it to transaction_links so deleting that "
        "row can't leave it stuck, or to NOT_RELEASED with the reason."
    )


def test_the_registry_names_no_field_that_no_longer_exists():
    known = set(transaction_links.RELEASED_FIELDS) | set(transaction_links.NOT_RELEASED)

    assert known - _stored_links() == set()
