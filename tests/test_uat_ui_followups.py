"""Follow-ups from hand UAT of the delete-safety change — synthetic data only (ACME, made-up sums).

- A record that stops moving cash deletes only a row that is still its own: a transfer the user
  made of it stays, and a row another record shares shrinks by this record's part.
- Lucius's update_lots can set or move a lot's paying account.
- A payment is offered to one installment, the closest.
- A dividend can be corrected and deleted from its holding's Dividends table, its cash row with it.
"""

# ruff: noqa: F811  (the shared fixtures are imported, then requested by name)
from __future__ import annotations

import pytest

from backend.ai.tools.lot_updates import _build_lot_update_change
from backend.db.account_store import AccountStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.lot_store import LotStore
from backend.db.transaction_store import TransactionStore
from backend.services import commitments as commitments_service
from backend.services import ledger as ledger_service
from tests.test_lot_cash_link import book  # noqa: F401
from tests.test_position_figures import isolated  # noqa: F401
from tests.test_uat_delete_safety import (  # noqa: F401
    amend,
    app,
    buy,
    edit_sale,
    live_ids,
    lot,
    lucius,
    ok,
    row,
    sell,
)


def _other_cash() -> str:
    return AccountStore().create(
        {"name": "Spare cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )["id"]


def _make_transfer(txn_id: str, to_account: str) -> None:
    TransactionStore().update_by_id(
        txn_id, {"kind": "transfer", "to_account_id": to_account, "to_amount": 200.0}
    )


def _share_row(book, app) -> tuple[dict, dict]:
    """Two lots on one purchase row, the way a merge of two purchase copies leaves them."""
    first = buy(app, book)
    second = buy(app, book, quantity=5, on="2026-01-06")
    TransactionStore().soft_delete(second["cash_leg_id"])
    LotStore().touch(second["lot_id"], {"txn_id": first["cash_leg_id"]})
    TransactionStore().update_by_id(first["cash_leg_id"], {"from_amount": 300.0})
    return first, second


# ── A record that lets go of its cash ────────────────────────────────────────────────────


def test_no_account_on_a_lot_whose_row_is_a_transfer_keeps_the_transfer(app, book):
    bought = buy(app, book)
    _make_transfer(bought["cash_leg_id"], _other_cash())

    ok(amend(app, bought["lot_id"], funding_account_id=""))

    assert bought["cash_leg_id"] in live_ids()
    assert row(bought["cash_leg_id"])["kind"] == "transfer"
    assert lot(bought["lot_id"])["txn_id"] is None


def test_removing_a_lot_whose_row_is_a_transfer_keeps_the_transfer(app, book):
    bought = buy(app, book)
    _make_transfer(bought["cash_leg_id"], _other_cash())

    ok(app.delete(f"/api/lots/{bought['lot_id']}"))

    assert bought["cash_leg_id"] in live_ids()
    assert row(bought["cash_leg_id"])["kind"] == "transfer"


def test_no_account_on_one_of_two_lots_sharing_a_row_shrinks_it_by_that_lots_part(app, book):
    first, second = _share_row(book, app)

    ok(amend(app, second["lot_id"], funding_account_id=""))

    assert first["cash_leg_id"] in live_ids()
    assert row(first["cash_leg_id"])["from_amount"] == 200.0
    assert lot(first["lot_id"])["txn_id"] == first["cash_leg_id"]
    assert lot(second["lot_id"])["txn_id"] is None


def test_removing_one_of_two_lots_sharing_a_row_leaves_the_others_cash(app, book):
    first, second = _share_row(book, app)

    ok(app.delete(f"/api/lots/{second['lot_id']}"))

    assert row(first["cash_leg_id"])["from_amount"] == 200.0
    assert lot(first["lot_id"])["txn_id"] == first["cash_leg_id"]


def test_removing_a_position_whose_lots_share_a_row_removes_the_row_once(app, book):
    from backend.services import positions as positions_service

    first, _second = _share_row(book, app)

    assert positions_service.remove_position(book["pid"]) is not False

    assert first["cash_leg_id"] not in live_ids()


def test_no_account_on_a_sale_whose_deposit_is_a_transfer_keeps_the_transfer(app, book):
    buy(app, book)
    sold = sell(app, book)
    TransactionStore().update_by_id(
        sold["cash_leg_id"],
        {"kind": "transfer", "from_account_id": _other_cash(), "from_amount": 100.0},
    )

    ok(edit_sale(app, book, sold["event_id"], account=""))

    assert sold["cash_leg_id"] in live_ids()
    assert row(sold["cash_leg_id"])["kind"] == "transfer"


# ── update_lots: the paying account ──────────────────────────────────────────────────────


def test_update_lots_moves_a_lots_purchase_cash_to_another_account(app, book):
    other = _other_cash()
    bought = buy(app, book)

    res = lucius("update_lots", lot_id=bought["lot_id"], funding_account_id="Spare cash")

    assert res.get("ok") is True, res
    assert row(bought["cash_leg_id"])["from_account_id"] == other
    assert lot(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_update_lots_card_names_the_accounts(app, book):
    _other_cash()
    bought = buy(app, book)

    change, error = _build_lot_update_change(
        {"lot_id": bought["lot_id"], "funding_account_id": "Spare cash"}
    )

    assert error is None
    assert change["display"]["paid_from"] == "Broker cash → Spare cash"


def test_update_lots_refuses_an_unknown_account_before_any_card(app, book):
    bought = buy(app, book)

    change, error = _build_lot_update_change(
        {"lot_id": bought["lot_id"], "funding_account_id": "No such account"}
    )

    assert change is None and error


def test_update_lots_cant_take_the_cash_off_a_lot(app, book):
    bought = buy(app, book)

    change, error = _build_lot_update_change(
        {"lot_id": bought["lot_id"], "funding_account_id": None}
    )

    assert change is None and error.startswith("nothing to change")


# ── Commitments: one payment, one installment ────────────────────────────────────────────


def _two_installments(app) -> str:
    return ok(
        app.post(
            "/api/commitments",
            json={
                "name": "Course fees",
                "kind": "purchase",
                "currency": "SGD",
                "installments": [
                    {"id": "p1", "label": "Part 1", "due_date": "2026-03-03", "amount": 300},
                    {"id": "p2", "label": "Part 2", "due_date": "2026-03-06", "amount": 300},
                ],
            },
        )
    )["commitment"]["id"]


def _payment(book, on: str) -> str:
    return ledger_service.create_transaction(
        {
            "date": on,
            "kind": "expense",
            "description": "Course payment",
            "from_account_id": book["cash"],
            "from_amount": 300.0,
            "from_currency": "SGD",
        },
        allow_duplicate=True,
    )["id"]


def test_one_payment_is_offered_to_one_installment_the_closest(app, book):
    _two_installments(app)
    paid = _payment(book, "2026-03-05")

    suggestions = commitments_service.detect_unmarked_payments()

    assert [(s["installment_id"], s["txn_id"]) for s in suggestions] == [("p2", paid)]


def test_two_payments_each_go_to_their_own_installment(app, book):
    _two_installments(app)
    early, late = _payment(book, "2026-03-04"), _payment(book, "2026-03-07")

    suggestions = commitments_service.detect_unmarked_payments()

    assert {(s["installment_id"], s["txn_id"]) for s in suggestions} == {
        ("p1", early),
        ("p2", late),
    }


# ── Dividends: correct and delete from the table ─────────────────────────────────────────


def _dividend(app, book, *, gross=12.0, tax=1.8, currency="SGD") -> dict:
    return ok(
        app.post(
            "/api/income",
            json={
                "position_id": book["pid"],
                "paid_at": "2026-06-15",
                "income_kind": "dividend",
                "gross_amount": gross,
                "tax_withheld": tax,
                "currency": currency,
                "account_id": book["cash"],
            },
        )
    )


def test_correcting_a_dividends_tax_edits_its_cash_row_in_place(app, book):
    paid = _dividend(app, book)
    ledger_service.update_transaction(paid["txn_id"], {"cleared": True})

    ok(app.patch(f"/api/income/{paid['event_id']}", json={"tax_withheld": 1.2}))

    after = row(paid["txn_id"])
    assert (after["to_amount"], after["cleared"], after["deleted"]) == (10.8, True, False)
    event = IncomeEventStore().find_by_id(paid["event_id"])
    assert (event["tax_withheld"], event["txn_id"]) == (1.2, paid["txn_id"])


def test_a_dividend_in_another_currency_keeps_its_rows_rate(app, book, monkeypatch):
    from backend.services import lots as lots_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    paid = _dividend(app, book, gross=12.0, tax=1.8)
    IncomeEventStore().update_by_id(paid["event_id"], {"currency": "USD"})
    TransactionStore().update_by_id(paid["txn_id"], {"to_amount": 13.77, "to_currency": "SGD"})

    ok(app.patch(f"/api/income/{paid['event_id']}", json={"tax_withheld": 1.2}))

    after = row(paid["txn_id"])
    assert (after["to_amount"], after["to_currency"]) == (14.58, "SGD")


def test_moving_a_dividend_to_another_account_moves_the_same_row(app, book):
    other = _other_cash()
    paid = _dividend(app, book)

    ok(app.patch(f"/api/income/{paid['event_id']}", json={"account_id": other}))

    assert row(paid["txn_id"])["to_account_id"] == other
    assert paid["txn_id"] in live_ids()


def test_no_account_on_a_dividend_removes_its_cash_and_keeps_its_account(app, book):
    paid = _dividend(app, book)

    ok(app.patch(f"/api/income/{paid['event_id']}", json={"account_id": None}))

    assert paid["txn_id"] not in live_ids()
    event = IncomeEventStore().find_by_id(paid["event_id"])
    assert (event["txn_id"], event["account_id"]) == (None, book["cash"])


def test_a_note_on_a_dividend_whose_cash_was_deleted_books_none(app, book):
    paid = _dividend(app, book)
    ok(app.delete(f"/api/transactions/{paid['txn_id']}"))
    live = live_ids()

    ok(app.patch(f"/api/income/{paid['event_id']}", json={"note": "special dividend"}))

    assert live_ids() == live
    assert IncomeEventStore().find_by_id(paid["event_id"])["note"] == "special dividend"


def test_a_reinvested_dividend_cant_be_given_an_account(app, book):
    drp = ok(
        app.post(
            "/api/income",
            json={
                "position_id": book["pid"],
                "paid_at": "2026-09-15",
                "gross_amount": 10,
                "currency": "SGD",
                "is_drp": True,
                "drp_quantity": 0.5,
                "drp_unit_cost": 20,
            },
        )
    )

    res = app.patch(f"/api/income/{drp['event_id']}", json={"account_id": book["cash"]})

    assert res.status_code == 422
    assert "lot" in res.json()["detail"]


@pytest.mark.parametrize(
    "patch, message",
    [({"tax_withheld": 50}, "more than the gross"), ({"paid_at": "not a date"}, "date")],
)
def test_a_dividend_correction_that_cant_be_made_says_why(app, book, patch, message):
    paid = _dividend(app, book)

    res = app.patch(f"/api/income/{paid['event_id']}", json=patch)

    assert res.status_code == 422
    assert message in res.json()["detail"]


def test_deleting_a_dividend_from_the_table_removes_its_cash(app, book):
    paid = _dividend(app, book)

    ok(app.delete(f"/api/income/{paid['event_id']}?remove_cash=true"))

    assert paid["txn_id"] not in live_ids()
    assert IncomeEventStore().find_by_id(paid["event_id"]) is None


def test_deleting_a_dividend_without_remove_cash_keeps_it_as_before(app, book):
    paid = _dividend(app, book)

    ok(app.delete(f"/api/income/{paid['event_id']}"))

    assert paid["txn_id"] in live_ids()


def test_deleting_a_dividend_whose_cash_is_a_transfer_keeps_the_transfer(app, book):
    paid = _dividend(app, book)
    TransactionStore().update_by_id(
        paid["txn_id"], {"kind": "transfer", "from_account_id": _other_cash(), "from_amount": 10.2}
    )

    ok(app.delete(f"/api/income/{paid['event_id']}?remove_cash=true"))

    assert paid["txn_id"] in live_ids()


# ── Review findings (2026-11-11) ─────────────────────────────────────────────────────────


def test_removing_a_partly_sold_lot_is_refused_and_keeps_its_cash(app, book):
    bought = buy(app, book)
    sell(app, book)
    before = row(bought["cash_leg_id"])

    res = app.delete(f"/api/lots/{bought['lot_id']}")

    assert res.status_code != 200
    assert bought["cash_leg_id"] in live_ids()
    assert row(bought["cash_leg_id"])["from_amount"] == before["from_amount"]
    assert lot(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_update_lots_refuses_a_blank_account(app, book):
    bought = buy(app, book)

    change, error = _build_lot_update_change(
        {"lot_id": bought["lot_id"], "funding_account_id": " "}
    )

    assert change is None and "account id or name" in error
    assert bought["cash_leg_id"] in live_ids()


@pytest.mark.parametrize("field", ["gross_amount", "tax_withheld", "paid_at", "currency"])
def test_a_blank_money_field_on_a_dividend_is_refused_not_read_as_zero(app, book, field):
    paid = _dividend(app, book)

    res = app.patch(f"/api/income/{paid['event_id']}", json={field: None})

    assert res.status_code == 422
    assert paid["txn_id"] in live_ids()
    assert row(paid["txn_id"])["to_amount"] == 10.2


def test_a_dividend_given_back_an_account_books_its_cash_with_a_payee(app, book):
    paid = _dividend(app, book)
    ok(app.patch(f"/api/income/{paid['event_id']}", json={"account_id": None}))

    out = ok(app.patch(f"/api/income/{paid['event_id']}", json={"account_id": book["cash"]}))

    booked = row(out["txn_id"])
    assert (booked["to_amount"], booked["to_account_id"]) == (10.2, book["cash"])
    assert booked.get("payee_id")
