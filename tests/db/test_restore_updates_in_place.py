"""A restore UPDATES a row that still exists in place — it never deletes and re-inserts it.

The replay used to delete every touched pk and re-insert its before-image. Deleting a row
that the turn only EDITED broke on every foreign key pointing at it from a row the turn
never touched: a RESTRICT child (a transaction on an edited account) failed the whole
restore, and a CASCADE child (the external refs of an edited transaction) was silently
deleted with it and never came back.
"""

from __future__ import annotations

from sqlalchemy import insert, select

from backend.ai import checkpoint as cp
from backend.core.io import fire_db_write_hook
from backend.db import engine as eng
from backend.db import tables as t
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore


def _account(acc_id: str, name: str = "Card") -> dict:
    return {"id": acc_id, "name": name, "type": "cash", "currency": "SGD"}


def _txn(**kw) -> dict:
    base = {
        "date": "2026-10-29",
        "kind": "expense",
        "description": "OpenRouter",
        "source": "ai",
        "from_account_id": "acc_card",
        "from_amount": 22.21,
        "from_currency": "SGD",
    }
    return {**base, **kw}


def _ref_rows(txn_id: str) -> list[str]:
    """The child TABLE (what dedup queries), not the payload copy re-inserted with the parent."""
    refs = t.transaction_external_refs
    with eng.get_engine().connect() as c:
        return [r for (r,) in c.execute(select(refs.c.ref).where(refs.c.txn_id == txn_id))]


def _turn(write) -> str:
    ctx, token = cp.begin_turn("s-in-place", 0, "t")
    try:
        write()
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    return entry["id"]


def test_restoring_an_account_edit_keeps_its_transactions(db_isolated):
    accounts, txns = AccountStore(), TransactionStore()
    accounts.insert(_account("acc_card"))
    row = txns.append(_txn())
    ckpt = _turn(lambda: accounts.update_by_id("acc_card", {"name": "Renamed"}))

    cp.restore(ckpt, mode="data")

    assert accounts.find_by_id("acc_card")["name"] == "Card"
    assert txns.find_by_id(row["id"]) is not None


def test_restoring_an_edited_transaction_keeps_its_untouched_refs(db_isolated):
    AccountStore().insert(_account("acc_card"))
    txns = TransactionStore()
    refs = [
        {"source": "email", "ref": "msg-receipt"},
        {"source": "email", "ref": "msg-alert"},
    ]
    row = txns.append(_txn(external_refs=refs))
    ckpt = _turn(lambda: txns.update_by_id(row["id"], {"from_amount": 22.35}))

    cp.restore(ckpt, mode="data")

    assert txns.find_by_id(row["id"])["from_amount"] == 22.21
    assert sorted(_ref_rows(row["id"])) == ["msg-alert", "msg-receipt"]


def test_moving_a_transaction_onto_an_account_made_in_the_same_turn_restores(db_isolated):
    accounts, txns = AccountStore(), TransactionStore()
    accounts.insert(_account("acc_card"))
    row = txns.append(_txn())

    def write():
        accounts.insert(_account("acc_new", "New"))
        txns.update_by_id(row["id"], {"from_account_id": "acc_new"})

    ckpt = _turn(write)

    cp.restore(ckpt, mode="data")

    assert txns.find_by_id(row["id"])["from_account_id"] == "acc_card"
    assert accounts.find_by_id("acc_new") is None


def test_a_permuted_seq_order_restores_without_a_unique_collision(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="bk", name="bk", base_currency="", created_at=""))
        for seq, cid in ((1, "a"), (2, "b")):
            c.execute(
                insert(t.categories).values(id=cid, book_id="bk", seq=seq, payload={"id": cid})
            )

    def swap():
        with eng.get_engine().begin() as c:
            rows = {
                r.id: dict(r._mapping)
                for r in c.execute(select(t.categories).where(t.categories.c.book_id == "bk"))
            }
            fire_db_write_hook("categories", [(k, v) for k, v in rows.items()], conn=c)
            c.execute(t.categories.update().where(t.categories.c.id == "a").values(seq=3))
            c.execute(t.categories.update().where(t.categories.c.id == "b").values(seq=1))
            c.execute(t.categories.update().where(t.categories.c.id == "a").values(seq=2))

    ckpt = _turn(swap)

    cp.restore(ckpt, mode="data")

    with eng.get_engine().connect() as c:
        seqs = dict(
            c.execute(
                select(t.categories.c.id, t.categories.c.seq).where(t.categories.c.book_id == "bk")
            ).all()
        )
    assert seqs == {"a": 1, "b": 2}


def test_a_parent_only_edit_never_cascades_onto_its_untouched_children(db_isolated):
    AccountStore().insert(_account("acc_card"))
    txns = TransactionStore()
    row = txns.append(_txn(external_refs=[{"source": "email", "ref": "msg-receipt"}]))

    def edit_parent_only():
        with eng.get_engine().begin() as c:
            before = dict(
                c.execute(select(t.transactions).where(t.transactions.c.id == row["id"]))
                .one()
                ._mapping
            )
            fire_db_write_hook("transactions", [(row["id"], before)], conn=c)
            c.execute(
                t.transactions.update()
                .where(t.transactions.c.id == row["id"])
                .values(description="Edited")
            )

    ckpt = _turn(edit_parent_only)

    cp.restore(ckpt, mode="data")

    assert txns.find_by_id(row["id"])["description"] == "OpenRouter"
    assert _ref_rows(row["id"]) == ["msg-receipt"]
