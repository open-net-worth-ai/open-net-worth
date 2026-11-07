"""A lot's link to its purchase cash row (``lots.txn_id``) — synthetic data only.

The scar: a double-booked purchase had its lot-linked cash row deleted as the duplicate. The lot
kept pointing at the deleted row, so correcting its fee tried to re-book the cash and the
duplicate guard refused it against the surviving row. ADR
2026-11-06-a-lots-cash-link-is-a-real-foreign-key.
"""

# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.ai.tools import execute_tool, registry
from backend.core.errors import ConflictError, DuplicateTransactionError
from backend.db.account_store import AccountStore
from backend.db.engine import get_engine
from backend.db.lot_store import LotStore
from backend.db.tables import lots as lots_table
from backend.db.tables import metadata
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import positions as positions_service
from tests.test_position_figures import _account, _instrument, isolated  # noqa: F401


@pytest.fixture
def book(isolated, monkeypatch):
    from backend.core import fx as core_fx

    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0})
    monkeypatch.setattr(registry, "should_preview_write", lambda: False)
    inst, acct = _instrument("ACME"), _account("Broker")
    cash = AccountStore().create(
        {"name": "Broker cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    pid = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 0}
    )["position_id"]
    return {"pid": pid, "cash": cash["id"]}


def _buy(book, *, quantity=10, unit_cost=20, on="2026-01-05", source="manual") -> dict:
    return lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": book["pid"],
            "quantity": quantity,
            "unit_cost": unit_cost,
            "cost_currency": "SGD",
            "acquired_at": on,
            "funding_account_id": book["cash"],
            "source": source,
        }
    )


def _twin_of(book, *, amount=200.0, on="2026-01-05") -> str:
    """The same purchase booked a second time, as an automation reading the email did."""
    return ledger_service.create_transaction(
        {
            "date": on,
            "kind": "investment",
            "description": "Bought 10 ACME at 20.00",
            "from_account_id": book["cash"],
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "email",
        },
        allow_duplicate=True,
    )["id"]


def _update_lots(**inp) -> dict:
    text, _ = asyncio.run(execute_tool("update_lots", inp, tool_use_id="toolu_link_test"))
    return json.loads(text)


def _live_ids() -> set[str]:
    return {t["id"] for t in TransactionStore().all() if not t.get("deleted")}


def _column(lot_id: str):
    with get_engine().connect() as conn:
        return conn.execute(select(lots_table.c.txn_id).where(lots_table.c.id == lot_id)).scalar()


# ── Correcting a lot whose cash row is gone ──────────────────────────────────────────────


def _kill_soft(txn_id: str, twin: str) -> None:
    TransactionStore().soft_delete(txn_id)


def _kill_merged(txn_id: str, twin: str) -> None:
    TransactionStore().update_by_id(txn_id, {"deleted": True, "merged_into": twin})


@pytest.mark.parametrize("kill", [_kill_soft, _kill_merged], ids=["deleted", "merged"])
def test_a_fee_on_a_lot_whose_cash_row_is_gone_saves_and_leaves_cash_alone(book, kill):
    bought = _buy(book)
    twin = _twin_of(book)
    kill(bought["cash_leg_id"], twin)  # the state a lot was left in before the delete cleared it

    res = _update_lots(lot_id=bought["lot_id"], fees=1.0)

    assert res["ok"] is True
    lot = LotStore().find_by_id(bought["lot_id"])
    assert lot["fees"] == 1.0
    assert lot["txn_id"] is None, "the dead link goes with the correction"
    assert _live_ids() == {twin}, "no new cash row; the surviving one is untouched"
    assert TransactionStore().find_by_id(twin)["from_amount"] == 200.0
    assert "cash" in res["cash_note"].lower()


def test_a_fee_on_a_sold_lot_whose_cash_row_is_gone_saves_and_leaves_cash_alone(book):
    bought = _buy(book)
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": book["pid"],
            "quantity": 4,
            "proceeds_per_unit": 25.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-02-01",
            "method": "fifo",
        }
    )
    twin = _twin_of(book)
    TransactionStore().soft_delete(bought["cash_leg_id"])

    res = _update_lots(lot_id=bought["lot_id"], fees=1.0)

    assert res["ok"] is True
    lot = LotStore().find_by_id(bought["lot_id"])
    assert (lot["fees"], lot["txn_id"]) == (1.0, None)
    assert _live_ids() == {twin}
    assert "cash" in res["cash_note"].lower()


def test_a_chosen_funding_account_still_books_cash_after_a_dead_link(book):
    bought = _buy(book)
    TransactionStore().soft_delete(bought["cash_leg_id"])

    lots_service.apply_lot_change(
        {
            "type": "amend",
            "lot_id": bought["lot_id"],
            "fees": 1.0,
            "funding_account_id": book["cash"],
        }
    )

    lot = LotStore().find_by_id(bought["lot_id"])
    assert lot["txn_id"] in _live_ids()
    assert TransactionStore().find_by_id(lot["txn_id"])["from_amount"] == 201.0


# ── Deleting the cash row clears the link ────────────────────────────────────────────────


def test_deleting_a_lots_cash_row_clears_the_link_and_says_which_lot(book):
    bought = _buy(book)

    result = ledger_service.bulk_delete_transactions([bought["cash_leg_id"]])

    assert [link["id"] for link in result["released_links"]] == [bought["lot_id"]]
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None
    assert _column(bought["lot_id"]) is None


def test_deleting_an_unrelated_row_reports_no_lot(book):
    bought = _buy(book)
    twin = _twin_of(book)

    result = ledger_service.bulk_delete_transactions([twin])

    assert "released_links" not in result
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_the_delete_tool_tells_the_model_a_lot_lost_its_cash_row(book):
    bought = _buy(book)

    text, _ = asyncio.run(
        execute_tool("delete_transactions", {"transaction_ids": [bought["cash_leg_id"]]})
    )

    assert [link["id"] for link in json.loads(text)["released_links"]] == [bought["lot_id"]]


def test_a_filtered_delete_clears_the_link(book):
    bought = _buy(book)

    assert ledger_service.delete_transactions_where(account_id=book["cash"]) == 1

    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None


def test_an_import_rollback_is_not_a_decision_and_keeps_the_link(book):
    bought = _buy(book)
    TransactionStore().update_by_id(bought["cash_leg_id"], {"batch_id": "batch_1"})

    assert ledger_service.delete_transactions_where(batch_id="batch_1") == 1

    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_correcting_a_live_link_edits_the_cash_row(book):
    bought = _buy(book)

    assert _update_lots(lot_id=bought["lot_id"], fees=1.0)["ok"] is True

    lot = LotStore().find_by_id(bought["lot_id"])
    assert lot["txn_id"] == bought["cash_leg_id"]
    assert _live_ids() == {lot["txn_id"]}
    assert TransactionStore().find_by_id(lot["txn_id"])["from_amount"] == 201.0
    assert _column(bought["lot_id"]) == lot["txn_id"]


# Rewriting by delete-and-book used to be refused here: the duplicate scan couldn't see the
# delete in the open transaction, so it matched the row being replaced.
def test_correcting_a_lot_booked_elsewhere_edits_its_own_cash_row(book):
    bought = _buy(book, source="automation")

    assert _update_lots(lot_id=bought["lot_id"], fees=1.0)["ok"] is True

    lot = LotStore().find_by_id(bought["lot_id"])
    assert lot["fees"] == 1.0
    assert _live_ids() == {lot["txn_id"]}, "never doubled"
    assert TransactionStore().find_by_id(lot["txn_id"])["from_amount"] == 201.0


def test_correcting_a_sold_lot_booked_elsewhere_edits_its_own_cash_row(book):
    bought = _buy(book, source="automation")
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": book["pid"],
            "quantity": 4,
            "proceeds_per_unit": 25.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-02-01",
            "method": "fifo",
        }
    )

    assert _update_lots(lot_id=bought["lot_id"], fees=1.0)["ok"] is True

    lot = LotStore().find_by_id(bought["lot_id"])
    assert lot["fees"] == 1.0
    assert lot["txn_id"] == bought["cash_leg_id"]
    assert TransactionStore().find_by_id(lot["txn_id"])["from_amount"] == 201.0


def test_a_truly_new_cash_row_beside_a_twin_is_still_refused_and_rolls_back(book):
    bought = _buy(book)
    twin = _twin_of(book)
    TransactionStore().soft_delete(bought["cash_leg_id"])

    with pytest.raises(DuplicateTransactionError):
        lots_service.apply_lot_change(
            {
                "type": "amend",
                "lot_id": bought["lot_id"],
                "fees": 1.0,
                "funding_account_id": book["cash"],
            }
        )

    lot = LotStore().find_by_id(bought["lot_id"])
    assert (lot["fees"], lot["txn_id"]) == (0, bought["cash_leg_id"])
    assert _live_ids() == {twin}


# ── A refusal stays on its own item ──────────────────────────────────────────────────────


def test_one_refused_lot_in_a_batch_does_not_stop_the_rest(book, monkeypatch):
    refused = _buy(book)
    fine = _buy(book, quantity=3, unit_cost=70, on="2026-03-09")
    real_apply = lots_service.apply_lot_change

    def refuse_one(change):
        if change.get("lot_id") == refused["lot_id"]:
            raise ConflictError("refused for this lot")
        return real_apply(change)

    monkeypatch.setattr(lots_service, "apply_lot_change", refuse_one)

    res = _update_lots(
        updates=[
            {"lot_id": refused["lot_id"], "fees": 1.0},
            {"lot_id": fine["lot_id"], "fees": 2.0},
        ]
    )

    by_lot = {r["lot_id"]: r for r in res["results"]}
    assert (res["succeeded"], res["failed"]) == (1, 1)
    assert "error" in by_lot[refused["lot_id"]]
    assert by_lot[fine["lot_id"]]["ok"] is True
    assert LotStore().find_by_id(fine["lot_id"])["fees"] == 2.0
    assert LotStore().find_by_id(refused["lot_id"])["fees"] == 0


def test_a_single_refusal_comes_back_as_a_reason_not_a_failed_turn(book, monkeypatch):
    bought = _buy(book)

    def refuse(change):
        raise ConflictError("refused for this lot")

    monkeypatch.setattr(lots_service, "apply_lot_change", refuse)

    assert _update_lots(lot_id=bought["lot_id"], fees=1.0)["error"] == "refused for this lot"


# ── The foreign key ──────────────────────────────────────────────────────────────────────


def test_the_column_follows_every_write_of_the_link(book):
    bought = _buy(book)
    assert _column(bought["lot_id"]) == bought["cash_leg_id"]

    LotStore().touch(bought["lot_id"], {"txn_id": None})

    assert _column(bought["lot_id"]) is None
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None


def test_a_link_to_a_row_that_does_not_exist_is_refused_by_the_database(book):
    bought = _buy(book)

    with pytest.raises(IntegrityError):
        LotStore().touch(bought["lot_id"], {"txn_id": "txn_never_existed"})

    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_really_removing_the_row_nulls_the_link_for_every_reader(book):
    bought = _buy(book)

    TransactionStore().delete(bought["cash_leg_id"])

    assert _column(bought["lot_id"]) is None
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None
    assert next(iter(LotStore().all()))["txn_id"] is None
    assert LotStore().lots_for_position(book["pid"])[0]["txn_id"] is None
    assert _update_lots(lot_id=bought["lot_id"], fees=1.0)["ok"] is True


def test_editing_the_cash_row_keeps_the_link(book):
    bought = _buy(book)

    ledger_service.update_transaction(bought["cash_leg_id"], {"memo": "edited"})

    assert _column(bought["lot_id"]) == bought["cash_leg_id"]
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == bought["cash_leg_id"]


def test_force_deleting_the_funding_account_clears_the_link(book):
    bought = _buy(book)

    assert ledger_service.delete_account(book["cash"], force=True) is True

    assert _column(bought["lot_id"]) is None
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] is None


def test_a_rewind_restores_transactions_before_the_lots_that_point_at_them():
    order = [t.name for t in metadata.sorted_tables]
    assert order.index("transactions") < order.index("lots")


# ── Backup bundles ───────────────────────────────────────────────────────────────────────


def test_a_bundle_lot_pointing_at_a_missing_row_imports_without_the_link(book):
    from backend.db import investments_io
    from backend.db.engine import unit_of_work

    bought = _buy(book)
    good = LotStore().find_by_id(bought["lot_id"])
    dangling = {**good, "id": "lot_dangling", "txn_id": "txn_not_in_this_bundle"}

    with unit_of_work() as conn:
        conn.execute(lots_table.delete())
        assert investments_io.import_lots(conn, [good, dangling]) == 2

    assert LotStore().find_by_id(good["id"])["txn_id"] == bought["cash_leg_id"]
    assert LotStore().find_by_id("lot_dangling")["txn_id"] is None
    assert _column("lot_dangling") is None


def test_a_bundle_exports_the_column_not_a_stale_payload(book):
    from backend.db import investments_io
    from backend.db.engine import unit_of_work

    bought = _buy(book)
    TransactionStore().delete(bought["cash_leg_id"])  # SET NULL leaves the payload copy stale

    with unit_of_work() as conn:
        exported = investments_io.export_lots(conn)

    assert exported[0]["txn_id"] is None
