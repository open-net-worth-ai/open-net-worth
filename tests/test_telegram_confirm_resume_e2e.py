"""E2E: a Telegram confirm tap must resume the turn like a web tap does.

Drives the REAL path end to end — ``handlers.handle_message`` → ``handle_inbound`` →
``processor.process_message`` → a real ``assistant.run`` tool loop → the real
``registry._permission_gate`` → the real Telegram adapter's inline keyboard → a real
``handlers.handle_callback`` tap → ``confirm.handle_confirm_action`` →
``assistant.resolve_pending_tool_use``. Only the LLM (a scripted provider) and the
Telegram HTTP transport (a recording fake bot) are stubbed; every gate, store and seam
in between is the production one.

Two live defects this pins (owner report, 2026-08-22):

  * **The resumed turn ran with NO cost surface bound.** The tap arrives on a fresh
    python-telegram-bot task and ``confirm._resolve_via_pending_tool_use`` called
    ``assistant.resolve_pending_tool_use`` DIRECTLY — never through ``ai/turns.py``,
    whose ``_drive`` binds the surface as its first statement precisely so this cannot
    happen (CLAUDE.md: "NEVER a bare ``asyncio.create_task`` around ``assistant.run``:
    the confirm gate fails OPEN without it"). With ``get_cost_surface()`` returning
    None, ``_confirm_gate.can_render_card(None)`` is False → ``should_gate`` returns
    False → **every remaining gated tool in the resumed turn executed unapproved.**
    Approving one permission really did approve the rest.

  * **Nothing the resumed turn produced ever reached the chat.** That same call site
    captured only the first ``tool_done`` event and dropped every ``text_delta`` /
    ``proposed_change`` on the floor, so after tapping ✓ the user got an ack and
    silence, and had to ask again.

Both are one root cause — the confirm tap bypassed the turn lifecycle — so both are
fixed by routing the resumed turn through the same processor event pump the inbound
turn uses, under ``turns.run_inline(cost_surface="connector:telegram")``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.ai.assistant as assistant
from backend.connectors.telegram import handlers
from backend.db.payee_store import PayeeStore

_GATED_TOOL = "create_payees"


class _ScriptedProvider:
    """One scripted model round per call; plain text once the script is exhausted.

    Round 0 is the inbound turn. Round 1 is the RESUMED turn the confirm tap drives —
    it asks for a SECOND gated write, which is the whole point: on a correctly-armed
    surface that must raise its own card, not execute silently.
    """

    def __init__(self, rounds):
        self._rounds = rounds
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium", **kwargs):
        idx = self.calls
        self.calls += 1
        if idx < len(self._rounds):
            for j, (name, inp) in enumerate(self._rounds[idx]):
                yield {"type": "tool_use", "id": f"toolu_r{idx}_{j}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "Both payees are in. Anything else?"}
            yield {"type": "done", "stop_reason": "end_turn"}


class _Bot:
    """Records what actually went out to Telegram."""

    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=len(self.sent))

    def cards(self) -> list[dict]:
        """Only the messages carrying inline buttons — i.e. confirm/permission cards."""
        return [m for m in self.sent if m.get("reply_markup") is not None]

    def prose(self) -> str:
        return "\n".join(m["text"] for m in self.sent if m.get("reply_markup") is None)


def _callback_data(card: dict) -> list[str]:
    markup = card["reply_markup"]
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _update(chat_id: int, text: str):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="owner", first_name="Owner"),
        message=SimpleNamespace(text=text, message_id=11, reply_to_message=None),
    )


def _tap(chat_id: int, data: str, bot: _Bot):
    """A real inline-button tap: the Update shape ``handle_callback`` consumes."""
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        from_user=SimpleNamespace(id=chat_id),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=chat_id, username="owner", first_name="Owner"),
    )
    return update, SimpleNamespace(bot=bot), query


@pytest.fixture
def tg(db_isolated, monkeypatch):
    """A Telegram connector that is enabled + allowlisted, with no real network."""
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "streaming": False, "suggestions": False})
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: True)
    monkeypatch.setattr(handlers, "_policy_allows_turn", lambda cid: True)

    yield


def _payee_names() -> set[str]:
    return {str(p.get("name")) for p in PayeeStore().all()}


@pytest.mark.asyncio
async def test_second_gated_write_in_a_resumed_turn_still_asks(tg, monkeypatch):
    """Approving ONE permission must not silently approve the next one.

    Round 0 asks to create "Kopitiam". The user taps ✓ Once. The resumed round asks to
    create "Toast Box" — a SECOND gated write. That must produce its own card and must
    NOT be written until it is itself approved.
    """
    provider = _ScriptedProvider(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
            [(_GATED_TOOL, {"payees": [{"name": "Toast Box"}]})],  # the RESUMED turn's 2nd write
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    bot = _Bot()
    await handlers.handle_message(
        _update(4242, "add my two coffee payees"), SimpleNamespace(bot=bot)
    )

    first_cards = bot.cards()
    assert len(first_cards) == 1, f"expected one permission card, got {bot.sent}"
    data = _callback_data(first_cards[0])
    once = next(d for d in data if d.startswith("allow_once:"))
    assert _payee_names() == set(), "nothing may be written before the tap"

    # ── the tap ────────────────────────────────────────────────────────────────
    update, ctx, _query = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    assert "Kopitiam" in _payee_names(), "the approved write must land"

    assert "Toast Box" not in _payee_names(), (
        "SECURITY: a second gated write executed unapproved just because an earlier "
        "one was approved — the confirm gate was off for the resumed turn"
    )
    second_cards = bot.cards()[1:]
    assert len(second_cards) == 1, (
        "the SECOND gated write in the resumed turn must raise its own card; "
        f"instead the chat got: {[m['text'][:80] for m in bot.sent]}"
    )


@pytest.mark.asyncio
async def test_resumed_turn_delivers_its_answer_to_the_chat(tg, monkeypatch):
    """After ✓, the model's follow-up must reach Telegram — not be dropped."""
    provider = _ScriptedProvider(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    bot = _Bot()
    await handlers.handle_message(
        _update(4242, "add Kopitiam as a payee"), SimpleNamespace(bot=bot)
    )
    once = next(d for d in _callback_data(bot.cards()[0]) if d.startswith("allow_once:"))

    update, ctx, _query = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    assert "Both payees are in" in bot.prose(), (
        "the resumed turn's reply must be delivered to the channel; "
        f"chat only got: {[m['text'][:80] for m in bot.sent]}"
    )


@pytest.mark.asyncio
async def test_confirm_tap_binds_the_connector_cost_surface(tg, monkeypatch):
    """The mechanism behind both defects, pinned directly.

    Whatever else changes, the resumed turn must run with a card-capable surface bound —
    that single fact is what keeps the confirm gate armed for the rest of the turn.
    """
    from backend.ai import _confirm_gate as policy
    from backend.ai.tools import _confirm_gate as tool_gate
    from backend.core.cost_context import get_cost_surface

    seen: list = []

    provider = _ScriptedProvider(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
            [(_GATED_TOOL, {"payees": [{"name": "Toast Box"}]})],  # the RESUMED turn's 2nd write
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    real_should_gate = tool_gate.should_gate

    def spy_should_gate(tool_name, level=None):
        seen.append((tool_name, get_cost_surface()))
        return real_should_gate(tool_name, level=level)

    monkeypatch.setattr(tool_gate, "should_gate", spy_should_gate)

    bot = _Bot()
    await handlers.handle_message(_update(4242, "add Kopitiam"), SimpleNamespace(bot=bot))
    once = next(d for d in _callback_data(bot.cards()[0]) if d.startswith("allow_once:"))

    seen.clear()
    update, ctx, _query = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    assert seen, "the resumed turn must consult the gate at least once"
    for name, surface in seen:
        assert policy.can_render_card(surface), (
            f"the gate was consulted for {name} on surface {surface!r}, where "
            "can_render_card() is False — so the gate answered 'do not gate' and the "
            "tool ran unapproved"
        )


@pytest.mark.asyncio
async def test_a_failing_resumed_turn_says_so_instead_of_going_quiet(tg, monkeypatch):
    """A turn can FAIL without raising — ``assistant.run`` maps a provider/tool failure
    into an ``error`` event and completes normally. Nothing rendered that event, so the
    channel simply went silent, which on a phone is indistinguishable from "still
    thinking" and reproduces the same "it doesn't send anything else" symptom the confirm
    fix is about. The user must be told."""

    class _FailsAfterApproval(_ScriptedProvider):
        async def stream_chat(self, messages, tools, system, model, effort="medium", **kwargs):
            idx = self.calls
            if idx >= len(self._rounds):
                raise RuntimeError("upstream model exploded")
            async for ev in super().stream_chat(messages, tools, system, model, effort, **kwargs):
                yield ev

    provider = _FailsAfterApproval(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    bot = _Bot()
    await handlers.handle_message(_update(4242, "add Kopitiam"), SimpleNamespace(bot=bot))
    once = next(d for d in _callback_data(bot.cards()[0]) if d.startswith("allow_once:"))

    update, ctx, query = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    assert "Kopitiam" in _payee_names(), "the approved write still landed"
    assert "⚠️" in bot.prose(), (
        f"a failed resumed turn must report itself; chat got: {[m['text'][:60] for m in bot.sent]}"
    )
    # ...and the acknowledgement must still tell the truth about the WRITE. Reporting a
    # committed write as "Failed to apply" is the one thing a confirm ack must not do.
    ack = " ".join(str(c.args[0]) for c in query.edit_message_text.call_args_list if c.args)
    assert "Failed to apply" not in ack, (
        f"the write committed but the acknowledgement claimed it failed: {ack!r}"
    )


@pytest.mark.asyncio
async def test_tap_clears_the_card_keyboard_before_the_turn_resumes(tg, monkeypatch):
    """The Once/Always/Deny keyboard must clear the instant the tap lands — not only once
    the whole resumed turn (which can run more tool calls, hit the model again, etc.)
    finishes streaming. Otherwise the card sits with live Once/Always/Deny buttons for the
    entire resumed turn, inviting a confused second tap (owner report, 2026-09-01)."""
    provider = _ScriptedProvider(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    bot = _Bot()
    await handlers.handle_message(_update(4242, "add Kopitiam"), SimpleNamespace(bot=bot))
    once = next(d for d in _callback_data(bot.cards()[0]) if d.startswith("allow_once:"))

    update, ctx, query = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    calls = [c.args[0] for c in query.edit_message_text.call_args_list if c.args]
    assert len(calls) >= 2, (
        f"expected an immediate provisional ack AND a later edit with the real outcome, "
        f"got: {calls}"
    )
    assert calls[0] == handlers._PROVISIONAL_ACK["allow_once"], (
        "the FIRST edit on the tapped message must clear its keyboard immediately with a "
        f"provisional ack, got: {calls[0]!r}"
    )
    assert calls[0] != calls[-1], "the provisional ack must be overwritten with the real outcome"


@pytest.mark.asyncio
async def test_an_approved_call_leaves_no_dangling_note_for_the_next_turn(tg, monkeypatch):
    """A confirm must not leave the transcript ending on an unanswered "[System: …]" note.

    Live failure: they tapped ✓ on an `attach_receipt` card, and the
    NEXT message — a bare "Hello" — was answered with *"This confirms the SGD 12.50 Riverside Cafe payment already logged…"*. The transcript showed why: entry [16] was a
    `[System: User confirmed and applied via chat: …attach_receipt…]` note with no assistant
    reply after it, because `_handle_grant_action` injects the note AFTER `apply_change`.
    The model treated that dangling note as the outstanding thing to respond to.

    The note existed to tell the model what happened back when a connector confirm did NOT
    resume the turn. It does now — the model sees the real `tool_result` and answers it in
    the same turn — so for a tool_use-resolving card the note is redundant AND harmful.
    """
    provider = _ScriptedProvider(
        [
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # bounces: unloaded write
            [(_GATED_TOOL, {"payees": [{"name": "Kopitiam"}]})],  # retry -> gated -> card
        ]
    )
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: provider)

    bot = _Bot()
    await handlers.handle_message(_update(4242, "add Kopitiam"), SimpleNamespace(bot=bot))
    once = next(d for d in _callback_data(bot.cards()[0]) if d.startswith("allow_once:"))

    update, ctx, _q = _tap(4242, once, bot)
    await handlers.handle_callback(update, ctx)

    from backend.connectors import session_map

    sid = session_map.get_or_create_session("telegram", "4242")
    history = assistant._load_session(sid) or []
    assert history, "the session must have a transcript"

    last = history[-1]
    body = last.get("content")
    text = body if isinstance(body, str) else ""
    assert not (last.get("role") == "user" and "[System: User confirmed" in text), (
        "the transcript ends on an unanswered confirm note — the next message will be "
        f"answered as if it were a reply to this: {text[:120]!r}"
    )
