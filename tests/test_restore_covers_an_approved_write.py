"""Restore to before a message undoes a write the person approved on that message's card.

A gated write runs when its card is approved — in the resolve step, after the turn that proposed
it has already paused and finalised its checkpoint. The write ran with no checkpoint open, so it
was recorded nowhere: "Restore to before this message" said it was done and left the new account
in place (owner report, 2026-11-03: an approved create_account survived the restore).
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai import checkpoint as ckpt
from backend.ai.tools import _confirm_gate_v2 as gate
from backend.ai.tools import ledger as ledger_tools
from backend.core.tool_use_context import set_tool_use_id
from backend.db import category_store as cat_mod
from backend.services import ledger as svc

pytestmark = pytest.mark.flags(permissions_v2=True)

SESSION = "sess-restore-approved"
TOOL_USE = "toolu_book"


class _TextOnlyProvider:
    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        yield {"type": "text_delta", "text": "Booked."}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def env(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(gate, "should_gate", lambda name: name == "add_transactions")
    monkeypatch.setattr(assistant, "get_provider", lambda: _TextOnlyProvider())
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    cat_mod.CategoryStore().insert({"id": "cat_food", "label": "Food", "kind": "expense"})
    yield
    set_tool_use_id(None)
    assistant.SESSIONS.pop(SESSION, None)


def _expense() -> dict:
    return {
        "kind": "expense",
        "date": "2026-07-19",
        "description": "ACME lunch",
        "category": "cat_food",
        "account_id": "acc_chk",
        "amount": 13.8,
        "currency": "SGD",
    }


async def _propose_in_a_turn() -> int:
    """The turn that proposes the write: opens its checkpoint on the user message, stages the
    card, and pauses — the history a real turn leaves behind."""
    _, history = assistant.get_or_create_session(SESSION)
    anchor = len(history)
    history.append({"role": "user", "content": "book my ACME lunch"})
    ctx, token = ckpt.begin_turn(SESSION, anchor, "book my ACME lunch")
    try:
        set_tool_use_id(TOOL_USE)
        _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
        assert change["tool_use_id"] == TOOL_USE
    finally:
        set_tool_use_id(None)
        ckpt.end_turn(ctx, token)
    history.append(
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": TOOL_USE, "name": "add_transactions", "input": {}}
            ],
        }
    )
    return anchor


async def _approve() -> None:
    async for _event in assistant.resolve_pending_tool_use(SESSION, TOOL_USE, "approve", "sys"):
        pass


@pytest.mark.asyncio
async def test_restoring_the_message_removes_the_row_its_approved_card_booked(env):
    anchor = await _propose_in_a_turn()
    await _approve()
    assert len(svc.transactions.all()) == 1

    checkpoint_id = ckpt.turn_anchors(SESSION).get(anchor)
    assert checkpoint_id, "the message that proposed the write has no Restore"
    ckpt.restore(checkpoint_id, mode="data")

    assert svc.transactions.all() == []


@pytest.mark.asyncio
async def test_a_denied_card_leaves_nothing_to_restore(env):
    anchor = await _propose_in_a_turn()
    async for _event in assistant.resolve_pending_tool_use(SESSION, TOOL_USE, "deny", "sys"):
        pass
    assert svc.transactions.all() == []
    checkpoint_id = ckpt.turn_anchors(SESSION).get(anchor)
    if checkpoint_id:
        ckpt.restore(checkpoint_id, mode="data")
    assert svc.transactions.all() == []


@pytest.mark.asyncio
async def test_the_restore_the_client_holds_undoes_the_approved_write_and_undo_rebooks_it(env):
    """Live, the client pins the LAST checkpoint_created of the turn on the user message —
    here the approved call's own. Restoring it undoes the write; Undo books it back."""
    await _propose_in_a_turn()
    events = [
        e async for e in assistant.resolve_pending_tool_use(SESSION, TOOL_USE, "approve", "sys")
    ]
    held = [e["checkpoint"]["id"] for e in events if e.get("type") == "checkpoint_created"]
    assert held, "the approved write's checkpoint never reached the client"

    result = ckpt.restore(held[-1], mode="data")
    assert svc.transactions.all() == []

    ckpt.restore(result["undo_checkpoint_id"], mode="data")
    assert [t["description"] for t in svc.transactions.all()] == ["ACME lunch"]
