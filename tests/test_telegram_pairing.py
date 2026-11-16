"""Telegram connector under the ``pairing`` access policy (LOW-1 regression + full flow).

Regression: an UNKNOWN sender under ``pairing`` must NOT receive a ``👀`` reaction or a
``typing`` chat action before the strict gate in ``handle_inbound`` fires.  Only APPROVED
senders should see those signals.

Full pairing flow (hermetic, no real Telegram bot):
  * unknown sender → pairing code sent + pending row recorded + NO reaction/typing + NO turn
  * after operator approves → next message from same sender runs a real turn (+ reaction/typing)
  * under ``allowlist`` with empty list → legacy open-access (reaction + turn) — unchanged

All tests use ``_TelegramAdapter`` directly and stub ``handle_inbound`` so no real model
or network I/O runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.connectors.telegram import handlers
from backend.db.pairing_store import PairingStore
from backend.db.telegram_config_store import TelegramConfigStore

# ── helpers ───────────────────────────────────────────────────────────────────


def _update(chat_id: int, text: str = "hello"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="bob", first_name="Bob"),
        message=SimpleNamespace(text=text, message_id=42),
    )


class _Bot:
    """Minimal stub that records every send_chat_action / set_message_reaction call."""

    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.send_message = AsyncMock()

    @property
    def reaction_calls(self):
        return self.set_message_reaction.call_count

    @property
    def typing_calls(self):
        return self.send_chat_action.call_count


@pytest.fixture
def pairing_env(tmp_path, monkeypatch):
    """Isolate config + pairing store; configure Telegram in pairing mode, empty allowlist.

    TelegramConfigStore, PairingStore, and connector_sessions (session_map) are all
    DB-backed now (Wave 5D1, JSON->DB) — no more per-store file path to monkeypatch;
    tests/conftest.py's autouse per-test table clear already isolates each of them on the
    shared default engine.
    """
    from backend.connectors.chat import run as chat_run

    store = TelegramConfigStore()
    store.update({"bot_token": "tok:en", "enabled": True, "access_policy": "pairing"})

    # Isolate the seam's in-process LRU and rate limiter between tests.
    chat_run.reset_rate_limit()
    chat_run._seen.clear()

    return store


@pytest.fixture
def allowlist_env(tmp_path, monkeypatch):
    """Allowlist policy, EMPTY list (legacy open-access)."""
    from backend.connectors.chat import run as chat_run

    store = TelegramConfigStore()
    store.update({"bot_token": "tok:en", "enabled": True, "access_policy": "allowlist"})

    chat_run.reset_rate_limit()
    chat_run._seen.clear()

    return store


# ── LOW-1: no reaction/typing for unapproved sender under pairing ─────────────


@pytest.mark.asyncio
async def test_pairing_unknown_sender_no_reaction_no_typing_no_turn(
    pairing_env, monkeypatch, tmp_path
):
    """LOW-1 regression: an unapproved sender under pairing MUST NOT get 👀 or typing."""

    turns = {"n": 0}

    async def _fake_handle_inbound(adapter, inbound):
        # We stub handle_inbound to avoid the seam's full logic but still replicate the
        # pairing reply path (send_text with the code), so we can verify the pre-seam
        # reaction gate.  In the real path handle_inbound calls _handle_pairing which calls
        # adapter.send_text — here we call it directly to represent the code reply.
        if not adapter.is_allowed(inbound.sender_id):
            # Simulate the pairing-code reply (what handle_inbound/_handle_pairing does)
            await adapter.send_text(inbound.channel_id, "pairing code: ABC123")
        else:
            turns["n"] += 1

    monkeypatch.setattr(handlers, "handle_inbound", _fake_handle_inbound)

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(99999), ctx)

    # No reaction and no typing for the unapproved sender (LOW-1 fix).
    assert bot.reaction_calls == 0, "👀 reaction must NOT be sent to an unapproved sender"
    assert bot.typing_calls == 0, "typing action must NOT be sent to an unapproved sender"
    assert turns["n"] == 0, "No real Lucius turn should run"


@pytest.mark.asyncio
async def test_pairing_unknown_sender_gets_code_reply(pairing_env, monkeypatch, tmp_path):
    """The pairing code IS sent to an unknown sender — the reaction/typing gate doesn't block it."""

    async def _fake_handle_inbound(adapter, inbound):
        # Replicate real handle_inbound: unknown sender → send pairing code
        if not adapter.is_allowed(inbound.sender_id):
            row = PairingStore().record(adapter.connector, inbound.sender_id, sender_label="bob")
            await adapter.send_text(
                inbound.channel_id,
                f"Not approved yet. Code: {row['code']}",
            )

    monkeypatch.setattr(handlers, "handle_inbound", _fake_handle_inbound)
    bot = _Bot()

    # Capture send_message to verify the code reply comes through
    actual_replies: list[str] = []
    bot.send_message = AsyncMock(side_effect=lambda **kw: actual_replies.append(kw.get("text", "")))

    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(99999), ctx)

    # The pairing store got the request
    pending = PairingStore().list_pending("telegram")
    assert len(pending) == 1
    assert pending[0]["sender_id"] == "99999"


@pytest.mark.asyncio
async def test_pairing_approved_sender_gets_reaction_and_turn(pairing_env, monkeypatch):
    """An approved sender under pairing gets the 👀 reaction + typing + a real turn."""

    turns = {"n": 0}

    async def _fake_handle_inbound(adapter, inbound):
        if adapter.is_allowed(inbound.sender_id):
            turns["n"] += 1

    monkeypatch.setattr(handlers, "handle_inbound", _fake_handle_inbound)

    # Add the sender to the allowlist (simulates operator approval)
    pairing_env.add_allowed_sender("77777")

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(77777), ctx)

    assert bot.reaction_calls == 1, "Approved sender should get 👀 reaction"
    assert bot.typing_calls == 1, "Approved sender should get typing indicator"
    assert turns["n"] == 1, "Approved sender should run a real turn"


# ── allowlist: fail-closed on an empty list ────────────────────────────────────


@pytest.mark.asyncio
async def test_allowlist_empty_list_is_closed_no_reaction_no_turn(allowlist_env, monkeypatch):
    """Under ``allowlist`` with empty allowed_chat_ids, NO sender is allowed (fail-closed,
    2026-08-22 — the legacy empty=open contract was the same danger the dropped ``open``
    policy named explicitly, just silent)."""

    turns = {"n": 0}

    async def _fake_handle_inbound(adapter, inbound):
        # Replicate the real handle_inbound allowlist gate (the mock replaces the function
        # that owns the gate, so it must reproduce it — _dispatch_text calls handle_inbound
        # unconditionally; this is the ONLY place a not-allowed sender gets dropped).
        if adapter.is_allowed(inbound.sender_id):
            turns["n"] += 1

    monkeypatch.setattr(handlers, "handle_inbound", _fake_handle_inbound)

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(12345), ctx)

    assert bot.reaction_calls == 0, "Empty allowlist: sender should get NO 👀 reaction"
    assert bot.typing_calls == 0, "Empty allowlist: sender should get NO typing indicator"
    assert turns["n"] == 0, "Empty allowlist: sender should get NO turn"


@pytest.mark.asyncio
async def test_allowlist_non_listed_sender_no_reaction_no_typing(allowlist_env, monkeypatch):
    """Under ``allowlist`` with a populated list, a non-listed sender gets no reaction/typing.
    The final gate (turn drop) is inside ``handle_inbound``; this test covers the PRE-seam
    reaction/typing gate only (the LOW-1 fix pattern: no telltale signals before the gate)."""

    allowlist_env.update({"allowed_chat_ids": ["111"]})
    process_calls = {"n": 0}

    async def _fake_handle_inbound(adapter, inbound):
        # Replicate handle_inbound allowlist logic: non-listed → no turn
        if adapter.is_allowed(inbound.sender_id):
            process_calls["n"] += 1

    monkeypatch.setattr(handlers, "handle_inbound", _fake_handle_inbound)

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(99999), ctx)

    assert bot.reaction_calls == 0, "Unlisted allowlist sender must NOT get 👀 reaction"
    assert bot.typing_calls == 0, "Unlisted allowlist sender must NOT get typing indicator"
    assert process_calls["n"] == 0, "No real process call for unlisted sender"


# ── _policy_allows_turn helper: command handlers ──────────────────────────────


def test_policy_allows_turn_pairing_unknown_returns_false(pairing_env):
    """_policy_allows_turn returns False for an unknown sender under pairing."""
    assert handlers._policy_allows_turn(88888) is False


def test_policy_allows_turn_pairing_known_returns_true(pairing_env):
    """_policy_allows_turn returns True for an approved sender under pairing."""
    pairing_env.add_allowed_sender("88888")
    assert handlers._policy_allows_turn(88888) is True


def test_policy_allows_turn_allowlist_empty_is_closed(allowlist_env):
    """_policy_allows_turn with allowlist + empty list → False (fail-closed, 2026-08-22)."""
    assert handlers._policy_allows_turn(12345) is False


def test_policy_allows_turn_allowlist_non_listed_false(allowlist_env):
    """_policy_allows_turn with allowlist + non-empty list → False for unknown sender."""
    allowlist_env.update({"allowed_chat_ids": ["111"]})
    assert handlers._policy_allows_turn(99999) is False


def test_policy_allows_turn_disabled_always_false(monkeypatch):
    """_policy_allows_turn returns False under disabled policy for any sender."""
    store = TelegramConfigStore()
    store.update({"bot_token": "tok:en", "access_policy": "disabled"})
    assert handlers._policy_allows_turn(12345) is False


def test_policy_allows_turn_open_always_true(monkeypatch):
    """_policy_allows_turn returns True under open policy for any sender — requires
    ``enabled: True`` too, since ``access_policy()`` now fail-closes to ``disabled``
    whenever the connector itself is paused/off (security review finding B)."""
    store = TelegramConfigStore()
    store.update({"bot_token": "tok:en", "enabled": True, "access_policy": "open"})
    assert handlers._policy_allows_turn(12345) is True


def test_policy_allows_turn_false_when_disabled_even_under_open_policy(monkeypatch):
    """Regression guard (security review 2026-08-17, finding B): a PAUSED connector
    (``enabled=False``) refuses every sender even if ``access_policy`` is the permissive
    ``open`` — enabled is now a fail-closed short-circuit, not a separate, unenforced axis."""
    store = TelegramConfigStore()
    store.update({"bot_token": "tok:en", "enabled": False, "access_policy": "open"})
    assert handlers._policy_allows_turn(12345) is False


# ── full seam integration: handle_inbound is the real gate ───────────────────


@pytest.mark.asyncio
async def test_full_seam_pairing_unknown_no_reaction_no_turn(pairing_env, monkeypatch):
    """Integration: run through the REAL handle_inbound seam. Under pairing, an unknown
    sender gets a code reply, no reaction/typing, and no real Lucius turn."""
    from backend.connectors.chat import run as chat_run

    turns = {"n": 0}

    async def _fake_process(*a, **kw):
        turns["n"] += 1

    monkeypatch.setattr(chat_run, "process_message", _fake_process)

    bot = _Bot()
    # Capture send_message calls (the pairing code is sent via adapter.send_text →
    # TelegramAdapter.send_text → bot.send_message)
    code_replies: list[str] = []

    async def _capture_send(**kw):
        code_replies.append(kw.get("text", ""))

    bot.send_message = AsyncMock(side_effect=lambda **kw: code_replies.append(kw.get("text", "")))
    ctx = SimpleNamespace(bot=bot)

    await handlers.handle_message(_update(55555), ctx)

    assert bot.reaction_calls == 0, "LOW-1: no 👀 for unapproved sender"
    assert bot.typing_calls == 0, "LOW-1: no typing for unapproved sender"
    assert turns["n"] == 0, "No real Lucius turn ran"
    # The pairing code reply IS sent (as a send_message call)
    assert len(code_replies) == 1
    assert "not approved" in code_replies[0].lower() or "55555" in str(code_replies)


def test_removing_the_connector_clears_its_pending_requests():
    from backend.db.pairing_store import PairingStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.services.connectors import remove_connector

    TelegramConfigStore().update({"bot_token": "1:t", "enabled": True})
    store = PairingStore()
    store.record("telegram", "111", sender_label="Ada Lovelace (@ada)")
    store.record("discord", "222")

    remove_connector("telegram")

    assert store.list_pending("telegram") == []
    assert len(store.list_pending("discord")) == 1
