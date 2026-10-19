"""The connector confirm round trip for a LEDGER change (txn_create / txn_update / txn_bulk).

Before this change, ``connectors.chat.confirm.apply_change`` had no branch for these types —
it fell through to ``positions_service.apply_position_change``, which understands none of
``ledger_preview``'s shape and would either raise or silently no-op. That is exactly the gap
CLAUDE.md's ledger-confirm-gate section calls out: "connectors render via
``format_change_summary`` ... and apply via ``chat/confirm.apply_change``, which has no branch
for these types". These tests drive the REAL staging path (``ledger_tools.add_transactions`` /
``update_transactions``, the actual ``ai.tools._confirm_gate`` stage/redeem machinery) through
the connector confirm seam (``pending_changes.store`` → ``confirm.handle_confirm_action``) —
never mocking ``execute_tool`` — so a green run here proves the SAME code path a Telegram/
Slack/Discord button tap drives end to end: stage → bind to a proposer → tap → redeem the
SERVER-STORED args → write.

RFC 2026-07-22 (approval-as-pending-tool_use) replaced the confirm_token re-call with a
tool_use_id-keyed pending record: a connector "yes" now resolves through
``assistant.resolve_pending_tool_use`` — the SAME mechanism the web resolve endpoint uses —
which appends the real result to the paused tool_use in SESSION HISTORY and then resumes the
turn (one more model round trip). Since these tests call the tool HANDLER directly (bypassing
the real turn loop that would have appended the preceding assistant tool_use), ``_stage_create``
also seeds that session's history with the plausible tool_use block a real turn would have
left — resolve depends on it being there. The resume's one model round trip is driven by a
stubbed provider (no LLM, no network) so these tests stay fast and offline.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import ledger as ledger_tools
from backend.connectors import pending_changes
from backend.connectors.chat import confirm
from backend.core.tool_use_context import set_tool_use_id
from backend.db import category_store as cat_mod
from backend.services import ledger as svc


class _TextOnlyProvider:
    """Stubs the resume's one model round trip — plain text, no tools, no network."""

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        yield {"type": "text_delta", "text": "Noted."}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # PendingPlanStore is DB-backed now (WAVE 5D2b, JSON->DB) — db_isolated already covers
    # its isolation, no class-level __init__ override needed.
    # Force the gate ON regardless of surface — these tests exercise the STAGE/REDEEM
    # machinery directly (mirrors test_ledger_confirm_gate.py's own fixture), independent of
    # whichever surface predicate ai.tools._confirm_gate.should_gate currently enforces.
    #
    _gated = frozenset({"add_transactions", "update_transactions"})
    monkeypatch.setattr(gate, "should_gate", lambda name: name in _gated)
    # The resolve step's resume round-trips the model once — stub it (no LLM, no network).
    monkeypatch.setattr(assistant, "get_provider", lambda: _TextOnlyProvider())
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    cats = cat_mod.CategoryStore()
    cats.insert({"id": "cat_food", "label": "Food", "kind": "expense"})
    yield tmp_path
    set_tool_use_id(None)
    for sid in ("sess-1", "sess-2"):
        assistant.SESSIONS.pop(sid, None)


def _expense(amount=13.8, description="Grab ride home"):
    return {
        "kind": "expense",
        "date": "2026-07-19",
        "description": description,
        "category": "cat_food",
        "account_id": "acc_chk",
        "amount": amount,
        "currency": "SGD",
    }


def _seed_pending_history(session_id: str, tool_use_id: str, tool_name: str) -> None:
    """Seed ``session_id``'s history with the assistant tool_use block a REAL turn loop
    would have appended before staging (these tests call the tool handler directly,
    bypassing that turn loop entirely) — ``resolve_pending_tool_use`` appends the real
    result to this SAME id, so it must already exist as a result-less tool_use."""
    _, history = assistant.get_or_create_session(session_id)
    history.append({"role": "user", "content": "book it"})
    history.append(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": {}}],
        }
    )


async def _stage_create(env, session_id="sess-1", tool_use_id="toolu_create"):
    """Stage a real txn_create card exactly as the tool does, returning the raw change dict
    (``tool_use_id`` included) the assistant would emit as ``proposed_change``."""
    set_tool_use_id(tool_use_id)
    _result, change = await ledger_tools.add_transactions({"transactions": [_expense()]})
    assert change["type"] == "txn_create"
    assert change["tool_use_id"] == tool_use_id
    _seed_pending_history(session_id, tool_use_id, "add_transactions")
    return change


@pytest.mark.asyncio
async def test_connector_confirm_tap_books_the_previewed_row(env):
    """The core fix: a button tap on a txn_create card must actually write the row, through
    the SAME tool_use_id resolve path the web path uses — not fall through to the positions
    choke point."""
    change = await _stage_create(env)
    change_id = pending_changes.store(
        "sess-1", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "applied"
    assert "booked" in outcome.message.lower()
    stored = svc.transactions.all()
    assert len(stored) == 1
    assert stored[0]["from_amount"] == 13.8
    assert stored[0]["category"] == "cat_food"


@pytest.mark.asyncio
async def test_connector_discard_never_writes(env):
    change = await _stage_create(env)
    change_id = pending_changes.store(
        "sess-1", change, connector="slack", channel_id="C1", sender_id="U-alice"
    )

    outcome = await confirm.handle_confirm_action(
        "discard", change_id, connector="slack", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "discarded"
    assert svc.transactions.all() == []


@pytest.mark.asyncio
async def test_connector_confirm_updates_the_previewed_edit(env):
    booked = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-19",
            "description": "Lunch",
            "category": "cat_food",
            "from_account_id": "acc_chk",
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    set_tool_use_id("toolu_update")
    _result, change = await ledger_tools.update_transactions(
        {"transaction_id": booked["id"], "memo": "team lunch"}
    )
    assert change["type"] == "txn_update"
    _seed_pending_history("sess-1", "toolu_update", "update_transactions")

    change_id = pending_changes.store(
        "sess-1", change, connector="discord", channel_id="C1", sender_id="U-alice"
    )
    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="discord", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "applied"
    assert svc.transactions.find_by_id(booked["id"])["memo"] == "team lunch"


@pytest.mark.asyncio
async def test_connector_confirm_bulk_books_every_row(env):
    set_tool_use_id("toolu_bulk")
    _result, change = await ledger_tools.add_transactions(
        {"transactions": [_expense(amount=1.0), _expense(amount=2.0)]}
    )
    assert change["type"] == "txn_bulk"
    assert change["count"] == 2
    _seed_pending_history("sess-1", "toolu_bulk", "add_transactions")

    change_id = pending_changes.store(
        "sess-1", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )
    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "applied"
    assert "2 transactions" in outcome.message
    assert len(svc.transactions.all()) == 2


@pytest.mark.asyncio
async def test_connector_confirm_reports_a_real_write_failure_not_applied(env):
    """A single-use pending call redeemed twice must surface as FAILED on the second tap —
    never silently reported as applied when nothing was written the second time."""
    change = await _stage_create(env, session_id="sess-2", tool_use_id="toolu_replay")
    # Redeem it once directly (simulating an earlier successful tap) so the SAME pending
    # entry is already consumed by the time the connector path tries it.
    await ledger_tools.add_transactions({"confirm_token": change["tool_use_id"]})
    assert len(svc.transactions.all()) == 1

    change_id = pending_changes.store(
        "sess-2", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )
    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "failed"
    assert len(svc.transactions.all()) == 1, "a stale replay must never double-book"


# ── IDOR: the SAME proposer/tapper check applies to ledger confirm cards ────────────────


@pytest.mark.asyncio
async def test_idor_a_different_sender_cannot_confirm_someone_elses_ledger_card(env):
    change = await _stage_create(env)
    change_id = pending_changes.store(
        "sess-1", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    mismatched = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-mallory"
    )
    assert mismatched.status == "ignored"
    assert svc.transactions.all() == [], "IDOR breach — a non-proposer booked someone else's row"

    # The real proposer can still confirm it afterward — a mismatched tap must not consume it.
    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )
    assert outcome.status == "applied"
    assert len(svc.transactions.all()) == 1
