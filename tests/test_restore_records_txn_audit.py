"""A chat restore leaves its own row in a transaction's Activity.

The audit trail is deliberately NOT rewound by a restore (``txn_audit`` is checkpoint-
excluded — it is the forensic record of how the data got here). Before this, the replay
wrote nothing to it either, so a transaction Lucius edited and the user then restored
still showed "Edited Amount 22.21 → 22.35" as its last word while the row read 22.21.
Every transaction the replay changes now gets one "revert" event with the field diff,
written in the same DB transaction as the replay.
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as cp
from backend.core import audit_context
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import audit
from backend.services import ledger as svc


@pytest.fixture
def env(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc_card", "name": "Card", "type": "cash", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield
    audit_context.clear_audit_scope()


def _create(amount: float = 22.21) -> str:
    return svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-10-29",
            "description": "OpenRouter",
            "from_account_id": "acc_card",
            "from_amount": amount,
            "from_currency": "SGD",
        }
    )["id"]


def _turn(write) -> str:
    ctx, token = cp.begin_turn("s-audit", 0, "change it")
    try:
        write()
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    return entry["id"]


def _ops(txn_id: str) -> list[str]:
    return [e["op"] for e in audit.trace(txn_id)["events"]]


def test_a_restore_clicked_in_the_ui_is_attributed_to_the_user(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.checkpoints import router

    app = FastAPI()
    app.include_router(router)
    txn_id = _create()
    ckpt = _turn(lambda: svc.update_transaction(txn_id, {"from_amount": 22.35}))

    assert TestClient(app).post(f"/api/checkpoints/{ckpt}/restore", json={}).status_code == 200

    last = audit.trace(txn_id)["events"][-1]
    assert (last["op"], last["actor"], last["surface"]) == ("revert", "user", "ui")
    assert last["note"] == "Restored to before a change"


def test_restoring_an_edit_records_a_revert_with_the_diff(env):
    txn_id = _create()
    ckpt = _turn(lambda: svc.update_transaction(txn_id, {"from_amount": 22.35}))

    audit_context.set_audit_scope("user", "ui")
    cp.restore(ckpt, mode="data")

    assert svc.transactions.find_by_id(txn_id)["from_amount"] == 22.21
    assert _ops(txn_id) == ["create", "update", "revert"]
    item = audit.activity(txn_id)["activity"][-1]
    assert item["action"] == "reverted"
    assert item["actor"] == "you"
    assert {"field": "from_amount", "from": 22.35, "to": 22.21} in item["changes"]


def test_undoing_the_restore_records_a_second_revert(env):
    txn_id = _create()
    ckpt = _turn(lambda: svc.update_transaction(txn_id, {"from_amount": 22.35}))
    undo_id = cp.restore(ckpt, mode="data")["undo_checkpoint_id"]

    cp.restore(undo_id, mode="data")

    assert svc.transactions.find_by_id(txn_id)["from_amount"] == 22.35
    assert _ops(txn_id) == ["create", "update", "revert", "revert"]
    last = audit.activity(txn_id)["activity"][-1]
    assert {"field": "from_amount", "from": 22.21, "to": 22.35} in last["changes"]


def test_restoring_a_create_records_the_removal(env):
    holder: dict = {}
    ckpt = _turn(lambda: holder.update(id=_create()))

    cp.restore(ckpt, mode="data")

    assert svc.transactions.find_by_id(holder["id"]) is None
    events = audit.trace(holder["id"])["events"]
    assert [e["op"] for e in events] == ["create", "revert"]
    assert events[-1]["before"] is not None and events[-1]["after"] is None


def test_a_restore_that_touches_no_transaction_writes_no_audit(env):
    txn_id = _create()
    ckpt = _turn(
        lambda: svc.accounts.insert(
            {"id": "acc_new", "name": "New", "type": "cash", "currency": "SGD"}
        )
    )

    cp.restore(ckpt, mode="data")

    assert _ops(txn_id) == ["create"]
