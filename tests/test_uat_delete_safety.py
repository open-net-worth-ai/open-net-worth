"""User acceptance for deleting and correcting linked transactions — synthetic data only.

One test per case in ``docs/qa/acceptance/delete-safety.md``; each name starts with its case ID.
They drive the app the way its screens and Lucius do: through the REST routes and the AI tools,
then read back what the user would see. The [UI] cases (dialog wording) run in the browser
harness instead.
"""

# ruff: noqa: F811  (the shared fixtures are imported, then requested by name)
from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.db.account_store import AccountStore
from backend.db.lot_store import LotStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from tests.test_lot_cash_link import book  # noqa: F401
from tests.test_position_figures import isolated  # noqa: F401

# ── The app, as the screens reach it ─────────────────────────────────────────────────────


@pytest.fixture
def app(book):
    from backend.api import (
        assets_api,
        checkpoints,
        commitments_api,
        income,
        ledger,
        lots,
        matches,
        portability,
    )

    api = FastAPI()
    for module in (ledger, lots, commitments_api, matches, income, assets_api, portability):
        api.include_router(module.router)
    api.include_router(checkpoints.router)
    return TestClient(api, raise_server_exceptions=False)


def ok(response) -> dict:
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


def lucius(tool: str, **args) -> dict:
    text, _ = asyncio.run(execute_tool(tool, args))
    return json.loads(text)


def row(txn_id: str) -> dict:
    return TransactionStore().find_by_id(txn_id) or {}


def live_ids() -> set[str]:
    return {t["id"] for t in TransactionStore().all() if not t.get("deleted")}


def balance(app, account_id: str) -> float:
    accounts = ok(app.get("/api/accounts"))
    return next(a for a in accounts if a["id"] == account_id)["balance"]


def lot(lot_id: str) -> dict:
    return LotStore().find_by_id(lot_id)


def buy(app, book, *, quantity=10, unit_cost=20, on="2026-01-05", account=None) -> dict:
    return ok(
        app.post(
            f"/api/positions/{book['pid']}/lots",
            json={
                "quantity": quantity,
                "unit_cost": unit_cost,
                "cost_currency": "SGD",
                "acquired_at": on,
                "funding_account_id": account or book["cash"],
            },
        )
    )


def email_copy(book, *, amount=200.0, on="2026-01-05", ref="<msg-acme-1@broker.test>") -> str:
    """The same purchase again, as an email automation books it."""
    return ledger_service.create_transaction(
        {
            "date": on,
            "kind": "investment",
            "description": "Bought 10 ACME",
            "from_account_id": book["cash"],
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "email",
            "external_refs": [{"source": "email", "ref": ref}],
        },
        allow_duplicate=True,
    )["id"]


def amend(app, lot_id: str, **fields):
    return app.patch(f"/api/lots/{lot_id}", json=fields)


def sell(app, book, *, account=None, quantity=4, price=25.0) -> dict:
    return ok(
        app.post(
            f"/api/positions/{book['pid']}/disposals",
            json={
                "quantity": quantity,
                "proceeds_per_unit": price,
                "proceeds_currency": "SGD",
                "disposed_at": "2026-02-01",
                "method": "fifo",
                "proceeds_account_id": account if account is not None else book["cash"],
            },
        )
    )


def edit_sale(app, book, event_id: str, *, account="__same__", price=26.0, note=""):
    return app.patch(
        f"/api/disposals/{event_id}",
        json={
            "quantity": 4,
            "proceeds_per_unit": price,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-02-01",
            "method": "fifo",
            "proceeds_account_id": book["cash"] if account == "__same__" else account,
            "note": note,
        },
    )


def commitment(app, *, due="2020-01-01") -> str:
    return ok(
        app.post(
            "/api/commitments",
            json={
                "name": "School fees",
                "installments": [{"id": "i1", "label": "Term 1", "due_date": due, "amount": 200}],
                "funding_plan": [{"id": "f1", "label": "Bonus", "amount": 200}],
            },
        )
    )["commitment"]["id"]


def pay(app, cid: str, txn_id: str) -> None:
    ok(
        app.patch(
            f"/api/commitments/{cid}/installments/i1",
            json={"changes": {"status": "paid", "paid_txn_id": txn_id}},
        )
    )


def installment(app, cid: str) -> dict:
    return ok(app.get(f"/api/commitments/{cid}"))["installments"][0]


def funding(app, cid: str) -> dict:
    return ok(app.get(f"/api/commitments/{cid}"))["funding_plan"][0]


def delete(app, txn_id: str) -> None:
    ok(app.delete(f"/api/transactions/{txn_id}"))


def dressed(txn_id: str) -> dict:
    """What a user adds to a cash row after booking it."""
    ledger_service.update_transaction(
        txn_id, {"memo": "my note", "cleared": True, "description": "My own words"}
    )
    return row(txn_id)


# ── A. Removing a duplicate purchase ─────────────────────────────────────────────────────


def test_DS_DUP_PREVIEW_names_the_lot_and_changes_nothing(app, book):
    bought = buy(app, book)
    email_copy(book)

    links = ok(app.post("/api/transactions/delete-preview", json={"ids": [bought["cash_leg_id"]]}))

    assert [link["kind"] for link in links["links"]] == ["lot"]
    assert "10 shares" in links["links"][0]["label"]
    assert "keeps its shares and cost" in links["links"][0]["effect"]
    assert lot(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    assert bought["cash_leg_id"] in live_ids()


def test_DS_DUP_AFTER_holding_and_cash_add_up_after_removing_the_linked_copy(app, book):
    bought = buy(app, book)
    email_copy(book)
    assert balance(app, book["cash"]) == -400.0

    delete(app, bought["cash_leg_id"])

    assert balance(app, book["cash"]) == -200.0
    after = lot(bought["lot_id"])
    assert (after["remaining_qty"], after["unit_cost"], after["txn_id"]) == (10, 20, None)


def test_DS_DUP_FIX_a_lot_whose_cash_copy_was_deleted_takes_a_fee(app, book):
    bought = buy(app, book)
    twin = email_copy(book)
    delete(app, bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], fees=1.0))

    assert lot(bought["lot_id"])["fees"] == 1.0
    assert live_ids() == {twin}
    assert row(twin)["from_amount"] == 200.0


def test_DS_DUP_REBOOK_choosing_an_account_again_says_why_it_cant(app, book):
    bought = buy(app, book)
    twin = email_copy(book)
    delete(app, bought["cash_leg_id"])

    refused = amend(app, bought["lot_id"], fees=1.0, funding_account_id=book["cash"])

    assert refused.status_code == 409, (refused.status_code, refused.text)
    assert "already in your ledger" in refused.json()["detail"]
    assert twin in refused.json()["detail"]
    assert lot(bought["lot_id"])["fees"] == 0
    assert ok(amend(app, bought["lot_id"], fees=1.0))
    assert lot(bought["lot_id"])["fees"] == 1.0


def test_DS_DUP_LUCIUS_the_card_names_the_lot_and_the_result_says_what_happened(app, book):
    from backend.ai.tools import registry

    bought = buy(app, book)
    email_copy(book)

    card = registry.tool_preview_adapter("delete_transactions").build(
        {"transaction_ids": [bought["cash_leg_id"]]}
    )
    result = lucius("delete_transactions", transaction_ids=[bought["cash_leg_id"]])

    assert any("10 shares" in item["name"] for item in card["data"]["items"])
    assert result["released_links"][0]["id"] == bought["lot_id"]
    assert "Tell the user" in result["note"]


# ── B. Correcting a purchase ─────────────────────────────────────────────────────────────


def test_DS_LOT_FEE_the_same_transaction_takes_the_fee_and_keeps_everything_else(app, book):
    bought = buy(app, book)
    before = dressed(bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], fees=1.0))

    after = row(bought["cash_leg_id"])
    assert lot(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]
    assert after["from_amount"] == 201.0
    for field in ("memo", "cleared", "source"):
        assert after[field] == before[field], field
    history = ok(app.get(f"/api/transactions/{bought['cash_leg_id']}/activity"))["activity"]
    assert history, "the edit shows in the transaction's history"
    assert bought["cash_leg_id"] in live_ids()


def test_DS_LOT_AUTOMATION_a_fee_on_an_automation_booked_lot_saves(app, book):
    from backend.services import lots as lots_service

    bought = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": book["pid"],
            "quantity": 10,
            "unit_cost": 20,
            "cost_currency": "SGD",
            "acquired_at": "2026-01-05",
            "funding_account_id": book["cash"],
            "source": "automation",
        }
    )

    ok(amend(app, bought["lot_id"], fees=1.0))

    assert row(bought["cash_leg_id"])["from_amount"] == 201.0


def test_DS_LOT_DATE_a_new_date_keeps_my_description_and_the_base_value(app, book):
    bought = buy(app, book)
    dressed(bought["cash_leg_id"])
    TransactionStore().update_by_id(
        bought["cash_leg_id"], {"fx_rate": 1.351235, "base_amount": 270.25}
    )

    ok(amend(app, bought["lot_id"], acquired_at="2026-01-07"))

    after = row(bought["cash_leg_id"])
    assert (after["date"], after["description"], after["base_amount"]) == (
        "2026-01-07",
        "My own words",
        270.25,
    )


def test_DS_LOT_QTY_a_new_quantity_moves_the_holding_and_the_cash(app, book):
    bought = buy(app, book)

    ok(amend(app, bought["lot_id"], quantity=12))

    lots = ok(app.get(f"/api/positions/{book['pid']}/lots"))["lots"]
    assert sum(x["remaining_qty"] for x in lots) == 12
    assert row(bought["cash_leg_id"])["from_amount"] == 240.0


def test_DS_LOT_ACCOUNT_moving_the_payment_moves_the_money(app, book):
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    bought = buy(app, book)

    ok(amend(app, bought["lot_id"], funding_account_id=other["id"]))

    assert (balance(app, book["cash"]), balance(app, other["id"])) == (0.0, -200.0)
    assert row(bought["cash_leg_id"])["from_account_id"] == other["id"]


def test_DS_LOT_NODEDUCT_no_account_removes_the_payment_and_frees_the_installment(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], funding_account_id=""))

    assert bought["cash_leg_id"] not in live_ids()
    assert (lot(bought["lot_id"])["remaining_qty"], lot(bought["lot_id"])["unit_cost"]) == (10, 20)
    assert installment(app, cid)["paid_txn_id"] is None
    assert balance(app, book["cash"]) == 0.0


def test_DS_LOT_NOTE_a_note_leaves_the_transaction_untouched(app, book):
    bought = buy(app, book)
    before = row(bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], note="from my bonus"))

    assert row(bought["cash_leg_id"]) == before


def test_DS_LOT_SOLD_a_fee_on_a_sold_lot_lowers_the_sales_gain_by_its_share(app, book):
    bought = buy(app, book)
    sold = sell(app, book)
    gain_before = RealizedEventStore().find_by_id(sold["event_id"])["realized_gain"]

    ok(amend(app, bought["lot_id"], fees=1.0))

    gain_after = RealizedEventStore().find_by_id(sold["event_id"])["realized_gain"]
    assert gain_after == pytest.approx(gain_before - 0.4)
    assert row(bought["cash_leg_id"])["from_amount"] == 201.0


def test_DS_LOT_FX_a_lot_paid_in_another_currency_keeps_its_booked_rate(app, book):
    bought = buy(app, book)
    LotStore().touch(bought["lot_id"], {"cost_currency": "USD"})
    TransactionStore().update_by_id(
        bought["cash_leg_id"], {"from_amount": 270.0, "from_currency": "SGD"}
    )

    ok(amend(app, bought["lot_id"], fees=1.0))

    after = row(bought["cash_leg_id"])
    assert (after["from_amount"], after["from_currency"]) == (271.35, "SGD")


def test_DS_LOT_EMAIL_AGAIN_the_same_email_after_a_correction_books_nothing(app, book):
    bought = buy(app, book)
    ledger_service.update_transaction(
        bought["cash_leg_id"],
        {"external_refs": [{"source": "email", "ref": "<msg-acme-1@broker.test>"}]},
    )
    ok(amend(app, bought["lot_id"], fees=1.0))
    live = live_ids()

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
    assert live_ids() == live


def test_DS_LOT_REMOVE_removing_a_lot_removes_its_payment_and_frees_the_installment(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])

    ok(app.delete(f"/api/lots/{bought['lot_id']}"))

    assert bought["cash_leg_id"] not in live_ids()
    assert installment(app, cid)["paid_txn_id"] is None


def test_DS_LOT_BATCH_one_correction_that_cant_be_made_doesnt_stop_the_others(app, book):
    first = buy(app, book)
    second = buy(app, book, quantity=3, unit_cost=70, on="2026-03-09")
    third = buy(app, book, quantity=5, unit_cost=30, on="2026-04-02")

    result = lucius(
        "update_lots",
        updates=[
            {"lot_id": first["lot_id"], "fees": 1.0},
            {"lot_id": "lot_does_not_exist", "fees": 1.0},
            {"lot_id": third["lot_id"], "fees": 2.0},
        ],
    )

    assert (result["succeeded"], result["failed"]) == (2, 1)
    failed = next(r for r in result["results"] if r["lot_id"] == "lot_does_not_exist")
    assert failed.get("error")
    assert (lot(first["lot_id"])["fees"], lot(third["lot_id"])["fees"]) == (1.0, 2.0)
    assert lot(second["lot_id"])["fees"] == 0


def test_DS_LOT_RESHAPED_a_transaction_turned_into_a_transfer_never_blocks_a_fee(app, book):
    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    bought = buy(app, book)
    TransactionStore().update_by_id(
        bought["cash_leg_id"],
        {"kind": "transfer", "to_account_id": other["id"], "to_amount": 200.0},
    )
    before = row(bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], fees=1.0))

    after = row(bought["cash_leg_id"])
    assert (after["from_amount"], after["to_amount"], after["kind"]) == (
        before["from_amount"],
        before["to_amount"],
        "transfer",
    )
    assert lot(bought["lot_id"])["fees"] == 1.0


# ── C. Sales ─────────────────────────────────────────────────────────────────────────────


def test_DS_SALE_EDIT_a_new_price_changes_the_same_deposit_and_the_gain(app, book):
    buy(app, book)
    sold = sell(app, book)

    edited = ok(edit_sale(app, book, sold["event_id"]))

    assert edited["cash_leg_id"] == sold["cash_leg_id"]
    assert row(sold["cash_leg_id"])["to_amount"] == 104.0
    assert edited["realized_gain"] == pytest.approx(24.0)
    assert balance(app, book["cash"]) == pytest.approx(-200.0 + 104.0)


def test_DS_SALE_NOTE_a_note_keeps_my_description(app, book):
    buy(app, book)
    sold = sell(app, book)
    dressed(sold["cash_leg_id"])

    ok(edit_sale(app, book, sold["event_id"], price=25.0, note="just a note"))

    assert row(sold["cash_leg_id"])["description"] == "My own words"


def test_DS_SALE_DEL_CASH_editing_after_deleting_the_deposit_adds_none_unless_asked(app, book):
    buy(app, book)
    sold = sell(app, book)
    delete(app, sold["cash_leg_id"])
    event = RealizedEventStore().find_by_id(sold["event_id"])
    assert (event["txn_id"], event["proceeds_account_id"]) == (None, None)
    live = live_ids()

    edited = ok(edit_sale(app, book, sold["event_id"], account=None))
    assert edited["cash_leg_id"] is None and live_ids() == live

    other = AccountStore().create(
        {"name": "Other cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    again = ok(edit_sale(app, book, edited["event_id"], account=other["id"]))
    assert again["cash_leg_id"] in live_ids()


def test_DS_SALE_UNDO_undoing_a_sale_returns_shares_and_frees_its_funding(app, book):
    bought = buy(app, book)
    sold = sell(app, book)
    cid = commitment(app)
    ok(
        app.patch(
            f"/api/commitments/{cid}/funding/f1",
            json={"changes": {"status": "done", "realized_txn_id": sold["cash_leg_id"]}},
        )
    )

    result = lucius("undo_lot_disposals", event_id=sold["event_id"])

    assert result.get("ok") is True, result
    assert lot(bought["lot_id"])["remaining_qty"] == 10
    assert sold["cash_leg_id"] not in live_ids()
    assert (funding(app, cid)["realized_txn_id"], funding(app, cid)["status"]) == (None, "planned")


def test_DS_SALE_UNDO_DEAD_undoing_a_sale_whose_deposit_is_gone_works(app, book):
    bought = buy(app, book)
    sold = sell(app, book)
    delete(app, sold["cash_leg_id"])

    result = lucius("undo_lot_disposals", event_id=sold["event_id"])

    assert result.get("ok") is True, result
    assert lot(bought["lot_id"])["remaining_qty"] == 10


def test_DS_SALE_NOACCT_no_deposit_account_removes_the_deposit(app, book):
    buy(app, book)
    sold = sell(app, book)

    edited = ok(edit_sale(app, book, sold["event_id"], account=None))

    assert sold["cash_leg_id"] not in live_ids()
    assert RealizedEventStore().find_by_id(edited["event_id"])["quantity"] == 4


# ── D. Commitments ───────────────────────────────────────────────────────────────────────


def test_DS_COM_PAID_DEL_deleting_the_payment_makes_it_owed_again(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])
    owed_before = ok(app.get("/api/commitments/summary"))["overdue_count"]

    delete(app, bought["cash_leg_id"])

    assert installment(app, cid)["status"] == "due"
    assert ok(app.get("/api/commitments/summary"))["overdue_count"] == owed_before + 1


def test_DS_COM_RECOVER_an_unpaid_installment_can_be_relinked_marked_or_removed(app, book):
    bought = buy(app, book)
    other = buy(app, book, quantity=1, unit_cost=50, on="2026-01-09")
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])
    delete(app, bought["cash_leg_id"])

    pay(app, cid, other["cash_leg_id"])
    assert installment(app, cid)["paid_txn_id"] == other["cash_leg_id"]

    ok(
        app.patch(
            f"/api/commitments/{cid}/installments/i1",
            json={"changes": {"status": "paid", "paid_txn_id": None}},
        )
    )
    assert installment(app, cid)["status"] == "paid"

    from backend.db.commitments_store import CommitmentsStore

    _, missing, refused = CommitmentsStore().remove_installments(cid, ["i1"])
    assert (missing, refused) == ([], [])


def test_DS_COM_FUTURE_a_future_installment_goes_back_to_upcoming(app, book):
    bought = buy(app, book)
    cid = commitment(app, due="2099-01-01")
    pay(app, cid, bought["cash_leg_id"])

    delete(app, bought["cash_leg_id"])

    assert installment(app, cid)["status"] == "upcoming"


def test_DS_COM_HAND_an_installment_marked_paid_by_hand_stays_paid(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    ok(app.patch(f"/api/commitments/{cid}/installments/i1", json={"changes": {"status": "paid"}}))

    delete(app, bought["cash_leg_id"])

    assert installment(app, cid)["status"] == "paid"


def test_DS_COM_FUND_deleting_the_receipt_makes_funding_planned_again(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    ok(
        app.patch(
            f"/api/commitments/{cid}/funding/f1",
            json={"changes": {"status": "done", "realized_txn_id": bought["cash_leg_id"]}},
        )
    )

    delete(app, bought["cash_leg_id"])

    assert (funding(app, cid)["status"], funding(app, cid)["realized_txn_id"]) == ("planned", None)


def test_DS_COM_MERGE_merging_the_payment_keeps_it_paid_and_undo_puts_it_back(app, book):
    bought = buy(app, book)
    twin = email_copy(book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])

    merged = app.post(
        "/api/matches/merge", json={"canonical_id": twin, "absorbed_id": bought["cash_leg_id"]}
    )

    assert merged.status_code == 200, merged.text
    assert (installment(app, cid)["paid_txn_id"], installment(app, cid)["status"]) == (
        twin,
        "paid",
    )
    assert lot(bought["lot_id"])["txn_id"] == twin

    ok(app.post("/api/matches/undo", json={"absorbed_id": bought["cash_leg_id"]}))
    assert installment(app, cid)["paid_txn_id"] == bought["cash_leg_id"]
    assert lot(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_DS_COM_CORRECT_correcting_the_lot_keeps_the_installment_paid(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])

    ok(amend(app, bought["lot_id"], fees=1.0))

    assert (installment(app, cid)["paid_txn_id"], installment(app, cid)["status"]) == (
        bought["cash_leg_id"],
        "paid",
    )


# ── E. Assets and income ─────────────────────────────────────────────────────────────────


def _sold_asset(app) -> tuple[str, str, str]:
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.services import owned_assets

    savings = AccountStore().create(
        {"name": "Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    asset = owned_assets.apply_owned_asset_change(
        {"op": "create", "name": "Old bike", "category": "other", "value": 900}
    )
    ok(
        app.post(
            f"/api/assets/{asset['id']}/dispose",
            json={"proceeds": 500, "account_id": savings["id"]},
        )
    )
    return asset["id"], savings["id"], OwnedAssetsStore().find_by_id(asset["id"])["disposal_txn_id"]


def test_DS_ASSET_DEL_a_sold_asset_stays_sold_and_lucius_can_rebook_its_proceeds(app, book):
    from backend.db.owned_assets_store import OwnedAssetsStore

    asset_id, savings, txn_id = _sold_asset(app)

    delete(app, txn_id)
    assert OwnedAssetsStore().find_by_id(asset_id)["archived"] is True
    assert balance(app, savings) == 0.0

    result = lucius("dispose_asset", asset_id=asset_id, proceeds=500, account_id=savings)

    assert result["ok"] is True, result
    assert balance(app, savings) == 500.0


def test_DS_ASSET_TWICE_an_asset_with_its_proceeds_cant_be_sold_again(app, book):
    asset_id, savings, _ = _sold_asset(app)

    refused = app.post(
        f"/api/assets/{asset_id}/dispose", json={"proceeds": 450, "account_id": savings}
    )

    assert refused.status_code in (400, 409, 422), refused.text
    assert "already sold" in refused.text
    assert balance(app, savings) == 500.0


def test_DS_INC_DEL_deleting_a_dividends_cash_keeps_the_dividend(app, book, monkeypatch):
    from backend.services import income as income_service

    monkeypatch.setattr(income_service, "base_currency", lambda: "SGD")
    recorded = ok(
        app.post(
            "/api/income",
            json={
                "position_id": book["pid"],
                "gross_amount": 50.0,
                "tax_withheld": 0.0,
                "currency": "SGD",
                "account_id": book["cash"],
                "paid_at": "2026-05-01",
            },
        )
    )

    delete(app, recorded["txn_id"])

    events = ok(app.get("/api/income"))
    listed = events if isinstance(events, list) else events.get("events", events.get("items", []))
    assert any(e["id"] == recorded["event_id"] for e in listed)
    ok(app.delete(f"/api/income/{recorded['event_id']}"))


# ── F. Undo, restore and moving data ─────────────────────────────────────────────────────


def _in_a_turn(name: str, act):
    from backend.ai import checkpoint as ckpt

    ctx, token = ckpt.begin_turn(name, anchor_history_len=0, message_preview=name)
    try:
        act()
    finally:
        entry = ckpt.end_turn(ctx, token)
    return entry["id"]


def test_DS_UNDO_CHAT_restoring_brings_back_the_payment_and_the_paid_installment(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])

    checkpoint = _in_a_turn(
        "sess-uat-delete",
        lambda: lucius("delete_transactions", transaction_ids=[bought["cash_leg_id"]]),
    )
    ok(app.post(f"/api/checkpoints/{checkpoint}/restore", json={"mode": "data"}))

    assert bought["cash_leg_id"] in live_ids()
    assert (installment(app, cid)["paid_txn_id"], installment(app, cid)["status"]) == (
        bought["cash_leg_id"],
        "paid",
    )


def test_DS_UNDO_CORRECT_restoring_puts_the_fee_and_the_amount_back(app, book):
    bought = buy(app, book)

    checkpoint = _in_a_turn(
        "sess-uat-correct", lambda: lucius("update_lots", lot_id=bought["lot_id"], fees=1.0)
    )
    ok(app.post(f"/api/checkpoints/{checkpoint}/restore", json={"mode": "data"}))

    assert lot(bought["lot_id"])["fees"] == 0
    assert row(bought["cash_leg_id"])["from_amount"] == 200.0


def test_DS_UNDO_IMPORT_rolling_an_import_back_and_forward_keeps_the_installment_link(app, book):
    bought = buy(app, book)
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])
    TransactionStore().update_by_id(bought["cash_leg_id"], {"batch_id": "batch_uat"})

    ledger_service.delete_transactions_where(batch_id="batch_uat")
    assert installment(app, cid)["paid_txn_id"] == bought["cash_leg_id"]
    TransactionStore().bulk_set_deleted([bought["cash_leg_id"]], deleted=False)

    assert bought["cash_leg_id"] in live_ids()
    assert (installment(app, cid)["paid_txn_id"], installment(app, cid)["status"]) == (
        bought["cash_leg_id"],
        "paid",
    )


def test_DS_UNDO_MERGE_KEEP_undoing_a_merge_keeps_a_link_i_made_since(app, book):
    bought = buy(app, book)
    twin = email_copy(book)
    later = buy(app, book, quantity=1, unit_cost=50, on="2026-06-01")
    cid = commitment(app)
    pay(app, cid, bought["cash_leg_id"])
    ok(
        app.post(
            "/api/matches/merge", json={"canonical_id": twin, "absorbed_id": bought["cash_leg_id"]}
        )
    )
    pay(app, cid, later["cash_leg_id"])

    ok(app.post("/api/matches/undo", json={"absorbed_id": bought["cash_leg_id"]}))

    assert installment(app, cid)["paid_txn_id"] == later["cash_leg_id"]


def _export(app) -> bytes:
    response = app.get("/api/data/export", headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 200, response.text
    return response.content


def _import(app, bundle: bytes):
    return app.post(
        "/api/data/import",
        files={"file": ("bundle.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )


def test_DS_BACKUP_lots_keep_their_purchases_through_export_and_import(app, book):
    first = buy(app, book)
    second = buy(app, book, quantity=3, unit_cost=70, on="2026-03-09")
    bundle = _export(app)

    ok(_import(app, bundle))

    assert lot(first["lot_id"])["txn_id"] == first["cash_leg_id"]
    assert lot(second["lot_id"])["txn_id"] == second["cash_leg_id"]
    assert {first["cash_leg_id"], second["cash_leg_id"]} <= live_ids()


def test_DS_BACKUP_PARTIAL_a_backup_missing_the_purchases_still_imports(app, book):
    import hashlib

    bought = buy(app, book)
    original = zipfile.ZipFile(io.BytesIO(_export(app)))
    manifest = json.loads(original.read("manifest.json"))
    rows = json.loads(original.read("transactions.json"))
    kept = json.dumps([r for r in rows if r.get("id") != bought["cash_leg_id"]]).encode()
    manifest["entries"]["transactions.json"].update(
        sha256=hashlib.sha256(kept).hexdigest(), row_count=len(rows) - 1, bytes=len(kept)
    )
    partial = io.BytesIO()
    with zipfile.ZipFile(partial, "w") as out:
        for name in original.namelist():
            data = original.read(name)
            if name == "transactions.json":
                data = kept
            elif name == "manifest.json":
                data = json.dumps(manifest).encode()
            out.writestr(name, data)

    result = _import(app, partial.getvalue())

    assert result.status_code == 200, result.text
    assert lot(bought["lot_id"])["txn_id"] is None
    assert lot(bought["lot_id"])["remaining_qty"] == 10


def test_DS_ACCT_FORCE_deleting_the_paying_account_keeps_the_lots(app, book):
    bought = buy(app, book)

    ok(app.delete(f"/api/accounts/{book['cash']}", params={"force": "true"}))

    after = lot(bought["lot_id"])
    assert (after["remaining_qty"], after["unit_cost"], after["txn_id"]) == (10, 20, None)


# ── Every case in the document is checked somewhere ──────────────────────────────────────


def test_every_case_in_the_acceptance_document_has_a_test_or_a_browser_spec():
    import re
    from pathlib import Path

    document = Path("docs/qa/acceptance/delete-safety.md").read_text()
    cases = set(re.findall(r"^### (DS-[A-Z-]+)", document, re.M))
    tested = {
        case
        for case in cases
        if any(name.startswith(f"test_{case.replace('-', '_')}_") for name in globals())
    }
    specs = "".join(p.read_text() for p in Path("qa/browser-use/specs").glob("delete_safety*.yaml"))
    in_browser = set(re.findall(r"^\s+- (DS-[A-Z-]+)$", specs, re.M))

    assert len(cases) > 40
    assert cases - tested - in_browser == set()
