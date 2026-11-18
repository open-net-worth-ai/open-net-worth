"""The ONE inbound→turn→reply path for every chat channel.

``handle_inbound(adapter, inbound)`` is the whole reuse story: allowlist → idempotency →
session lookup → ``processor.process_message`` (which runs ``assistant.run`` and dispatches
text + proposed-change cards back through the adapter's send methods). Telegram, Slack,
Discord (and every future channel) funnel through here — only the transport
(parse/verify/send) differs per adapter.

**Trust model — THE ALLOWLIST IS THE CONTROL.**

* The per-sender ALLOWLIST (default-deny, checked FIRST) decides WHO may talk to Lucius.
  Nobody reaches a turn without being on it, per connector.
* The adapter's ``trust_level`` no longer decides what a turn may DO. It still drives the
  shared-workspace hardening that has nothing to do with tool power:
  - ``untrusted`` (a shared Slack/Discord workspace, the DEFAULT) → the session is keyed
    PER-SENDER (``channel_id:sender_id``) so members never see each other's financial
    answers, plus a per-sender rate limit.
  - ``operator`` (a private Telegram bot, or an Operator-trust opt-in) → channel-keyed
    session, no per-sender rate limit.

**What changed, deliberately (owner-authorised):** an untrusted turn used to be narrowed to
a read+notify+propose ceiling, so a write off shared-channel text was structurally
impossible — the only path was a ``propose_*`` card the user tapped. That ceiling is gone.
An ALLOWLISTED sender in a shared Slack/Discord channel now gets the same tool offer as the
web app and can make Lucius write directly: log a transaction, edit or delete one, move
budget money, change the profile. The allowlist is what stands between a workspace member
and the ledger, so treat adding someone to it as granting them the app.

Unchanged and still load-bearing: a ``propose_*`` card is still bound to its proposer, so
only the sender who caused it may tap Confirm (the IDOR guard in ``chat/confirm.py``), and
the per-tap rate limit still applies.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Optional

from backend.connectors import session_map, session_title, turn_registry
from backend.connectors.chat.base import (
    POLICY_ALLOWLIST,
    POLICY_DISABLED,
    POLICY_OPEN,
    POLICY_PAIRING,
    TRUST_UNTRUSTED,
    ChatChannelAdapter,
    InboundMessage,
)
from backend.connectors.processor import process_message
from backend.core.ratelimit import SlidingWindowLimiter

log = logging.getLogger("backend.connectors.chat")


def _multi_turn_enabled() -> bool:
    """Whether mid-turn injection is on — the SAME profile flag the web path reads.

    One switch for "Lucius accepts a second message while working", not a per-transport
    one: the queue and its drain are shared, and a connector-only toggle could enqueue
    into a drain that is off. Defaults False (the drain's own default), and never raises —
    an unreadable profile means today's behaviour, never a stranded message."""
    try:
        from backend.services.profile import read_profile

        return bool(read_profile().get("chat_multi_turn_enabled", False))
    except Exception:  # noqa: BLE001 — routing must never fail an inbound message
        return False


# Bounded LRU of seen ``(connector, event_id)`` so a provider re-delivery (Slack retries
# aggressively) doesn't double-run a turn. In-memory (resets on restart) — a duplicate that
# survives a restart is acceptable; the real guard is the immediate-200 ack + this dedup.
_SEEN_MAX = 2048
_seen: "OrderedDict[tuple[str, str], None]" = OrderedDict()

# Per-sender inbound turn cap (MEDIUM-1). Each accepted inbound message spawns a full Lucius
# turn (model + tool calls + possible web_search/fetch_page), so an allowlisted sender on an
# untrusted channel firing a high rate of messages is an unbounded spend / resource-exhaustion
# vector. We cap turns per ``(connector, sender_id)`` with the same stdlib sliding-window
# limiter the login route uses (in-memory, resets on restart). Over the cap → one friendly
# reply, then drop (no turn). Operator channels (Telegram) bypass this — the operator is the
# trusted user paying for their own instance.
_TURNS_PER_WINDOW = 10
_TURN_WINDOW_SECONDS = 60.0
_turn_limiter = SlidingWindowLimiter(_TURNS_PER_WINDOW, _TURN_WINDOW_SECONDS)


def reset_rate_limit() -> None:
    """Drop all per-sender turn counters (test isolation / restart)."""
    _turn_limiter.clear()


def session_key_for(channel_id: str, sender_id: str, *, untrusted: bool) -> str:
    """The per-channel session/history key for a chat turn — the SINGLE source of truth.

    Untrusted, shared-workspace channels key PER-SENDER (``channel_id:sender_id``) so members
    never share financial history; an operator-trust channel keys by ``channel_id`` only (a
    private bot the operator owns). Shared with the proactive-context bridge (the notify
    channels) so a recorded alert lands in the EXACT session the user's reply will read —
    otherwise the two would silently drift and the bridge would be a no-op."""
    return f"{channel_id}:{sender_id}" if untrusted else channel_id


def _seen_before(connector: str, event_id: Optional[str]) -> bool:
    """Whether ``(connector, event_id)`` was already processed (idempotency). A falsy
    ``event_id`` (provider gave none) is never deduped — it always runs."""
    if not event_id:
        return False
    key = (connector, event_id)
    if key in _seen:
        return True
    _seen[key] = None
    if len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)  # evict oldest
    return False


def _access_policy(adapter: ChatChannelAdapter) -> str:
    """The adapter's access policy, default ``allowlist`` (back-compat for an adapter that
    doesn't implement ``access_policy`` — it keeps today's manual-allowlist behavior)."""
    getter = getattr(adapter, "access_policy", None)
    if not callable(getter):
        return POLICY_ALLOWLIST
    try:
        return getter()
    except Exception:  # a config read should never break the inbound path
        log.exception("[%s] access_policy() failed — defaulting to allowlist", adapter.connector)
        return POLICY_ALLOWLIST


async def _handle_allowlist_reject(adapter: ChatChannelAdapter, inbound: InboundMessage) -> None:
    """Tell a sender rejected under the ``allowlist`` policy their own chat id, so they can
    hand it to the operator — the same information ``/id`` already gives anyone unconditionally
    (owner review, 2026-08-22: staying silent here added no real protection once `/id` existed,
    just friction for a first-time sender who didn't know to run it). No Lucius turn runs;
    best-effort reply, same shape as ``_handle_pairing``."""
    try:
        await adapter.send_text(
            inbound.channel_id,
            "Hi! You'll need to be added before I can help. Send this number to whoever set up "
            f"this bot: **{inbound.sender_id}**",
        )
    except Exception:
        log.exception("[%s] failed to send allowlist-reject reply", adapter.connector)


async def _handle_pairing(adapter: ChatChannelAdapter, inbound: InboundMessage) -> None:
    """Record/refresh a pending pairing request for an UNKNOWN sender + reply the pairing code.

    The ONLY thing an unapproved sender's message ever produces under the ``pairing`` policy —
    NO Lucius turn runs. The operator approves the code in-app (``POST /api/chat-pairing/approve``)
    → the sender id is appended to the allowlist + the row cleared, so their NEXT message proceeds
    normally. The reply is best-effort (a send failure is logged, never propagated)."""
    from backend.db.pairing_store import PairingStore

    try:
        row = PairingStore().record(
            adapter.connector,
            inbound.sender_id,
            sender_label=inbound.raw_meta.get("sender_label", ""),
        )
    except Exception:
        log.exception("[%s] failed to record pairing request", adapter.connector)
        return
    code = row.get("code", "")
    log.info(
        "[%s] pairing request from sender=%s code=%s", adapter.connector, inbound.sender_id, code
    )
    try:
        await adapter.send_text(
            inbound.channel_id,
            "Hi! You're not approved to chat with me yet.\n\n"
            f"Pairing code: **{code}**\n\n"
            "Approve it in Open Net Worth, under Connectors → "
            f"{adapter.connector.title()}. If someone else set this bot up, send them the code. "
            "I'll tell you here when you're in.",
        )
    except Exception:
        log.exception("[%s] failed to send pairing-code reply", adapter.connector)


def _is_stop_command(text: str) -> bool:
    """The explicit ``/stop`` command only (optionally ``/stop@bot`` / ``/stop <args>`` in a
    group). Bare "stop"/"abort" text is deliberately NOT a trigger (owner: /stop only) — it's
    too easy to hit mid-conversation and would cancel a turn the user didn't mean to stop."""
    t = (text or "").strip().lower()
    return t == "/stop" or t.startswith("/stop@") or t.startswith("/stop ")


async def handle_inbound(adapter: ChatChannelAdapter, inbound: InboundMessage) -> None:
    """Run one inbound message through the seam → a Lucius turn → a reply via ``adapter``.

    **Access policy (chat-pairing) — the WHO gate, applied FIRST.** Per the adapter's
    ``access_policy`` (``pairing``|``allowlist``|``open``|``disabled``):

      * ``disabled`` → drop every inbound (no reply).
      * ``open``     → allow anyone (dev-only opt-in).
      * ``allowlist``→ proceed iff ``is_allowed``; else rejected with a reply naming their own
                       chat id, so they can hand it to the operator — no Lucius turn.
      * ``pairing``  → proceed iff ``is_allowed``; else an UNKNOWN sender gets a pairing-CODE
                       reply + a pending request the operator approves in-app — NO Lucius turn.

    Then, for an allowed sender: drop a duplicate event; an ``untrusted`` channel (the default
    for a shared Slack/Discord workspace) runs the turn under the headless propose-only ceiling
    (a money change can only be PROPOSED + confirmed in-channel by a button tap); an
    ``operator`` channel (Telegram, or a connector with the Operator-trust opt-in) runs the
    full tool set. Exceptions are caught + logged so a failed turn never crashes the transport /
    webhook handler.
    """
    # Idempotency FIRST — a provider re-delivery (Slack retries) must not re-run the gate (it
    # would re-record/re-reply a pairing code, or re-run a turn). A falsy event_id is never
    # deduped (always runs); the per-sender rate limit below still bounds those.
    if _seen_before(adapter.connector, inbound.raw_meta.get("event_id")):
        log.info("[%s] duplicate event %s — ignored", adapter.connector, inbound.raw_meta)
        return

    policy = _access_policy(adapter)
    if policy == POLICY_DISABLED:
        log.info(
            "[%s] dropped message from sender=%s — policy=disabled",
            adapter.connector,
            inbound.sender_id,
        )
        return

    allowed = policy == POLICY_OPEN or adapter.is_allowed(inbound.sender_id)
    if not allowed:
        if policy == POLICY_PAIRING:
            # An unknown sender under pairing: rate-limit the code reply per sender (so an
            # unknown-sender flood can't be used to spam pairing-code sends), then record +
            # reply the code. NO turn runs — pairing is purely "register + tell them the code".
            rl_key = f"{adapter.connector}:pairing:{inbound.sender_id}"
            ok, retry_after = _turn_limiter.check(rl_key)
            if not ok:
                log.warning(
                    "[%s] pairing reply rate-limited for sender=%s",
                    adapter.connector,
                    inbound.sender_id,
                )
                return
            _turn_limiter.record(rl_key)
            await _handle_pairing(adapter, inbound)
            return
        # allowlist (default) → rejected, but told their own chat id (2026-08-22 — was a
        # silent drop; same per-sender rate limit as pairing so a flood can't spam replies).
        rl_key = f"{adapter.connector}:allowlist-reject:{inbound.sender_id}"
        ok, retry_after = _turn_limiter.check(rl_key)
        if not ok:
            log.warning(
                "[%s] allowlist-reject reply rate-limited for sender=%s",
                adapter.connector,
                inbound.sender_id,
            )
            return
        _turn_limiter.record(rl_key)
        log.warning("rejected %s message from sender=%s", adapter.connector, inbound.sender_id)
        await _handle_allowlist_reject(adapter, inbound)
        return

    # ``/stop`` control message (any allowed sender): cancel this channel's in-flight turn +
    # pending debounce, and DON'T start a new turn. Handled here — before the rate limit and
    # session setup — so a stop never costs a turn token, and the cancel tears down the LLM
    # stream (turn_registry.stop → task.cancel → assistant.run aclose → provider close). The
    # webhook connectors (Slack/Discord/WhatsApp) each deliver this as a separate concurrent
    # request, so it reaches a running turn; Telegram routes /stop via its own CommandHandler.
    if _is_stop_command(inbound.text):
        # `turn_registry` is imported at module scope now (the injection router below needs
        # it too). Re-importing it HERE would make the name function-local for the WHOLE
        # function body, so the router's earlier-executing reference would raise
        # UnboundLocalError on every message that is not `/stop`.
        stopped = turn_registry.stop(adapter.connector, inbound.channel_id)
        log.info(
            "[%s] /stop from sender=%s — stopped=%s",
            adapter.connector,
            inbound.sender_id,
            stopped,
        )
        try:
            await adapter.send_text(
                inbound.channel_id,
                "⏹ Stopped." if stopped else "Nothing is running to stop.",
            )
        except Exception:
            log.exception("[%s] failed to send /stop acknowledgement", adapter.connector)
        return

    # ``untrusted`` (the default for a shared workspace) now drives ONLY the shared-workspace
    # hardening: per-sender session isolation + a per-sender rate limit. It no longer narrows
    # the tool offer — see the trust-model note in the module docstring for exactly what an
    # allowlisted sender on a shared channel can now do.
    untrusted = adapter.trust_level == TRUST_UNTRUSTED

    # Per-sender rate limit (MEDIUM-1) — only on untrusted channels (the cost/DoS surface).
    if untrusted:
        rl_key = f"{adapter.connector}:{inbound.sender_id}"
        allowed, retry_after = _turn_limiter.check(rl_key)
        if not allowed:
            log.warning(
                "[%s] sender=%s rate-limited (%s turns / %ss)",
                adapter.connector,
                inbound.sender_id,
                _TURNS_PER_WINDOW,
                int(_TURN_WINDOW_SECONDS),
            )
            try:
                await adapter.send_text(
                    inbound.channel_id,
                    f"You're sending messages a bit too fast — I can handle up to "
                    f"{_TURNS_PER_WINDOW} per minute. Please try again in about "
                    f"{retry_after}s.",
                )
            except Exception:
                log.exception("[%s] failed to send rate-limit reply", adapter.connector)
            return
        _turn_limiter.record(rl_key)

    # Session/history key (HIGH-2). For an UNTRUSTED, shared-workspace channel the session is
    # keyed PER-SENDER (``channel_id:sender_id``) — the SAME sender identity the allowlist
    # checks above — so each member gets an ISOLATED Lucius session and one user can never see
    # the financial answers generated for another (cross-user data bleed). For an ``operator``
    # channel (a private Telegram bot the operator owns) the key stays ``channel_id``, so
    # Telegram's per-chat session is byte-identical to before the seam.
    session_key = session_key_for(inbound.channel_id, inbound.sender_id, untrusted=untrusted)

    session_id = session_map.get_or_create_session(adapter.connector, session_key)
    # Stamp the TRANSPORT on the session, here — the one path every chat connector's inbound
    # turn goes through (`handle_inbound`), so Slack/Discord/WhatsApp are covered the day
    # they are added rather than the day someone remembers.
    #
    # `channel` is deliberately NOT `source`: that field is an ownership claim a security
    # gate reads (`automation_cleanup` fail-closes on `source == "automation"`), and a
    # transport is a different axis from a runner. IN-APP is the ABSENCE of a channel — it
    # needs no value of its own, so the app's own conversations need no migration and cannot
    # be dropped from their own list by a missed stamp. The risk runs the other way: an
    # UNSTAMPED connector leaks into the in-app list, which is exactly what happened to the
    # owner's Telegram thread (2026-08-21). That is why this lives on the shared path.
    #
    # Not in `session_map.get_or_create_session`, which is deliberately AI-FREE:
    # `services.connectors` imports it, and `services` must never reach `backend.ai`, even
    # transitively (security review 2026-08-17, finding 10/1).
    #
    # The same call NAMES the conversation, once (owner decision, 2026-08-22). A connector
    # thread is not topic-scoped the way a web chat is, so the frontend's
    # name-it-after-the-first-message rule is wrong for it — and moot anyway, since the
    # frontend filters `channel`-carrying rows out of the list it titles. Left alone, every
    # Telegram conversation stayed "New conversation" forever. See `session_title` for why
    # the chat id is deliberately not part of the name.
    session_title.ensure_session_titled(adapter.connector, session_id)

    # Mid-turn injection — the connector twin of `api/chat.py`'s own routing. A message
    # arriving while THIS session already has a turn executing is spliced into that turn
    # instead of starting a second one, so a correction ("no, just June") can still reach
    # the turn it is correcting. Without it the second message queued behind
    # `assistant.run`'s per-session lock and ran afterwards, by which point the first turn
    # had already done the thing (owner, 2026-08-23).
    #
    # Three rules copied from the web path deliberately, because both feed the SAME queue
    # and the SAME drain (`assistant._drain_injected_messages`, keyed by session id):
    #   * gated on `chat_multi_turn_enabled` — the DRAIN is gated on it too, so enqueuing
    #     with it off would strand the message with nothing to consume it;
    #   * routed on the turn TASK (`turn_registry.is_running_for_session`), keyed by
    #     SESSION not channel — see that function for why the channel key is wrong here;
    #   * re-checked after enqueueing, with NO await in between (single event loop, so
    #     both spans are atomic): if the turn ended in the gap, fall through and start a
    #     normal turn, which drains the queue at its own first checkpoint.
    if _multi_turn_enabled() and inbound.text.strip():
        if turn_registry.is_running_for_session(session_id):
            # Imported HERE, not at module scope: `backend.ai` pulls in the assistant, which
            # transitively reaches back into `connectors`, and a top-level import deadlocks
            # the whole package (verified: `import backend.connectors.chat.run` hangs).
            # `session_title` defers its own `ai` import for exactly the same reason.
            from backend.ai import pending_injection

            pending_injection.enqueue(session_id, f"conn_{uuid.uuid4().hex[:12]}", inbound.text)
            if turn_registry.is_running_for_session(session_id):
                log.info(
                    "[%s] injected into the running turn for %s", adapter.connector, session_id[:8]
                )
                return

    effort = session_map.get_effort(adapter.connector, session_key)

    # No per-turn tool narrowing on either trust level: an inbound chat turn has a real human
    # waiting on the other end of the channel, so it gets the same default offer the web app
    # gets (CORE + find_tools discovery) and is NOT marked unattended.
    try:
        await process_message(
            adapter,
            inbound.channel_id,
            session_id,
            inbound.text,
            effort,
            attachment_refs=inbound.attachment_refs,
            # Bind any proposed change to this proposer so only the same chat user may confirm
            # it in-channel (IDOR defense). Only needed on untrusted shared channels; harmless
            # (just an extra binding) on operator channels.
            sender_id=inbound.sender_id,
            # The earlier message this one replies to, when the channel has a native reply
            # affordance — the model needs it to know WHICH message "why?" is about.
            reply_to=inbound.reply_to,
        )
    except Exception:
        log.exception("[%s] error processing inbound message", adapter.connector)
    finally:
        # Belt-and-braces: reset any per-turn scope a tool call inside the turn may have set
        # (a skill run) so nothing leaks into the next inbound message on this connector.
        # ``assistant.run`` already restores at turn end; this covers an exception path that
        # never reached one.
        from backend.ai.tools import _skill_scope

        _skill_scope.clear_scope()
