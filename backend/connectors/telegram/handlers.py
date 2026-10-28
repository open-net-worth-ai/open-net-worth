"""Telegram bot message handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from backend.connectors import session_map, session_reset, turn_registry
from backend.connectors.base import BaseChannelAdapter
from backend.connectors.chat import confirm
from backend.connectors.chat.base import TRUST_OPERATOR, InboundMessage, QuotedMessage
from backend.connectors.chat.run import handle_inbound
from backend.connectors.msgfmt import render as render_dialect
from backend.connectors.processor import format_permission_card, process_message
from backend.connectors.telegram.auth import is_allowed
from backend.services.model_selection import ModelChoice

log = logging.getLogger("backend.connectors.telegram")

CONNECTOR = "telegram"
VALID_EFFORTS = {"low", "medium", "high", "max"}


def _policy_allows_turn(chat_id: int | str) -> bool:
    """Policy-aware gate for command and non-message handlers (start/effort/new/callback).

    Unlike ``is_allowed``, this reads the current ``access_policy`` and applies strict
    membership under ``pairing`` — so an unknown sender under the pairing policy is silently
    dropped from commands rather than getting the bot's help text.  The standard message path
    (``handle_message``) goes through ``handle_inbound`` which handles the pairing-code reply
    itself; the command handlers do not route through ``handle_inbound``, so they use this
    lighter gate.

    Returns True iff the sender may execute a command RIGHT NOW (same semantics as
    ``_TelegramAdapter.is_allowed``).
    """
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    policy = store.access_policy()
    if policy == "pairing":
        return str(chat_id) in store.allowed_chat_ids()
    # "allowlist" → strict membership (empty = no one, 2026-08-22), "open" → always allow
    # (dropped from the UI as a foot-gun but still a valid stored value), "disabled" → always deny
    if policy == "disabled":
        return False
    if policy == "open":
        return True
    return is_allowed(chat_id)


# Media group buffering: collect all photos in a group before processing
_MEDIA_GROUP_DELAY = 1.5  # seconds to wait for remaining photos in a group
_media_group_buffer: dict[tuple[int, str], list[dict[str, Any]]] = {}
_media_group_tasks: dict[tuple[int, str], asyncio.Task] = {}


def _choice_keyboard(field: str, options: list[str], current: str | None = None):
    """An inline keyboard of tappable command options (e.g. low/medium/high/max). A tap fires a
    ``set:<field>:<value>`` callback that applies the choice — so a bare command answers in one tap
    instead of asking the user to type the argument. The current value is marked with ●."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    ("● " if o == current else "") + o, callback_data=f"set:{field}:{o}"
                )
                for o in options
            ]
        ]
    )


def _reply_keyboard(suggestions: list[str] | None):
    """A native reply keyboard (quick-reply chips under the input) from follow-up suggestions, or
    None. Tapping a chip sends the text as a normal message; one_time_keyboard hides it after."""
    if not suggestions:
        return None
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        [[KeyboardButton(s[:64])] for s in suggestions],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ── Thin connector adapter ────────────────────────────────────────────────────


class _TelegramAdapter(BaseChannelAdapter):
    """Adapts the generic processor interface to python-telegram-bot calls.

    The reference ``ChatChannelAdapter`` (RFC chat-channel-seam §2): ``connector``/
    ``trust_level``/``is_allowed`` + the duck-typed ``send_*`` the processor already used.
    Telegram is ``"operator"`` trust — a private bot the operator controls is the trusted
    operator, like the web UI — so it runs the FULL tool set (behavior byte-identical to
    before the seam). A SHARED-workspace channel (Slack/Discord) is ``"untrusted"`` by
    default, so ITS inbound turn runs headless-scoped (propose + in-channel confirm) unless
    the operator opts into Operator trust; Telegram is unaffected by that default.
    """

    connector = CONNECTOR
    trust_level = TRUST_OPERATOR
    platform = "telegram"
    max_length = 4096
    uses_utf16_len = True  # Telegram measures length in UTF-16 code units

    def __init__(self, bot):
        self._bot = bot
        self._rich_send_disabled = False  # latched off after a rich-send capability failure
        self._flood_until = 0.0  # monotonic time until which we skip edits (honoring retry_after)

    def is_allowed(self, sender_id: str) -> bool:
        # Under the default ``allowlist`` policy keep Telegram's legacy operator-bot contract
        # (empty allowed_chat_ids = OPEN). Under ``pairing`` an empty allowlist must mean
        # "nobody approved yet" so unknown senders actually get paired (the empty=open quirk
        # would otherwise let everyone in and pairing would never trigger) — so do a STRICT
        # membership check there.
        from backend.db.telegram_config_store import TelegramConfigStore

        store = TelegramConfigStore()
        if store.access_policy() == "pairing":
            return str(sender_id) in store.allowed_chat_ids()
        return is_allowed(sender_id)

    def access_policy(self) -> str:
        from backend.db.telegram_config_store import TelegramConfigStore

        return TelegramConfigStore().access_policy()

    def add_allowed_sender(self, sender_id: str) -> bool:
        from backend.db.telegram_config_store import TelegramConfigStore

        return TelegramConfigStore().add_allowed_sender(sender_id)

    # Rich Messages are opt-in (Hermes's convention). We default ON because tables/task-lists are
    # exactly what the owner asked to render natively; the construct gate below means ordinary
    # replies never go rich, so the "hard to copy as plain text" concern only touches the messages
    # that genuinely benefit. Latched OFF for the session after a capability failure.
    _rich_enabled = True

    async def send_text(
        self, channel_id: str, text: str, suggestions: list[str] | None = None
    ) -> None:
        """Copy Hermes's handling (minus streaming): send via Telegram Rich Messages (Bot API 10.2,
        raw markdown) ONLY for constructs the legacy path degrades — pipe tables, GFM task lists,
        <details>, block math (see ``needs_rich_rendering``). Ordinary replies (prose, bold/italic,
        simple lists) stay on the legacy HTML path (``super().send_text``) for consistent typography.
        A rich failure falls back to the legacy path AND latches rich off for the session.

        ``suggestions`` (follow-up quick-replies) ride the answer as a native REPLY keyboard — no
        separate carrier bubble. sendRichMessage supports ``reply_markup``, so a TABLE reply still
        renders rich AND carries the keyboard; only the legacy fallback attaches it per-chunk."""
        kb = _reply_keyboard(suggestions)
        reply_markup = kb.to_dict() if kb else None
        if self._rich_eligible(text) and await self._send_rich(
            channel_id, text, reply_markup=reply_markup
        ):
            return
        if kb is None:
            await super().send_text(channel_id, text)
            return
        parts = self.prepare(text)
        for i, part in enumerate(parts):
            await self._bot.send_message(
                chat_id=int(channel_id),
                text=part,
                parse_mode=ParseMode.HTML,
                reply_markup=(kb if i == len(parts) - 1 else None),
            )

    def _rich_eligible(self, text: str) -> bool:
        from backend.connectors.telegram.rich import needs_rich_rendering, rich_unsafe

        return bool(
            self._rich_enabled
            and not self._rich_send_disabled
            and text
            and text.strip()
            and needs_rich_rendering(text)
            and not rich_unsafe(text)  # skip rich for client-crash/garble/over-cap content
        )

    async def _send_rich(
        self, channel_id: str, text: str, reply_markup: dict | None = None
    ) -> bool:
        from backend.connectors.telegram.formatter import markdown_to_rich_html
        from backend.connectors.telegram.rich import send_rich_html

        token = getattr(self._bot, "token", "")
        if not isinstance(token, str) or not token:
            return False  # no usable token (e.g. a test double) → fall back, never raise
        ok = await send_rich_html(
            token, channel_id, markdown_to_rich_html(text), reply_markup=reply_markup
        )
        if not ok:
            self._rich_send_disabled = True  # latch off so we don't retry a doomed rich send
        return ok

    async def _deliver(self, channel_id: str, part: str) -> None:
        await self._bot.send_message(
            chat_id=int(channel_id),
            text=part,
            parse_mode=ParseMode.HTML,
        )

    # ── Live streaming (edit-in-place, Hermes's model on our HTML legacy path) ──
    # Streaming edits render markdown → Telegram HTML, which is balance-safe on PARTIAL markdown
    # (incomplete `**bol` stays literal, an unclosed ``` auto-closes) so a mid-stream edit can never
    # 400 on an unbalanced tag. The final answer finalizes to the rich path for tables/math.
    @property
    def supports_streaming(self) -> bool:
        """User-toggleable via /streaming (default on). Read fresh each turn so the command applies
        immediately."""
        from backend.db.telegram_config_store import TelegramConfigStore

        return TelegramConfigStore().streaming_enabled()

    async def stream_send(self, channel_id: str, text: str) -> str | None:
        from backend.connectors.telegram.formatter import markdown_to_telegram_html

        try:
            msg = await self._bot.send_message(
                chat_id=int(channel_id),
                text=markdown_to_telegram_html(text),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return str(msg.message_id)
        except Exception as exc:  # noqa: BLE001 — give up streaming this turn, caller buffer-sends
            log.warning("telegram stream_send failed: %s", type(exc).__name__)
            return None

    async def stream_update(self, channel_id: str, message_id: str, text: str) -> None:
        import time

        from backend.connectors.telegram.formatter import markdown_to_telegram_html

        if time.monotonic() < self._flood_until:
            return  # honoring a prior 429 retry_after — skip this edit
        try:
            await self._bot.edit_message_text(
                chat_id=int(channel_id),
                message_id=int(message_id),
                text=markdown_to_telegram_html(text),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001 — flood / not-modified: skip this edit, keep going
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:  # Telegram flood control — back off for the server-requested window
                self._flood_until = time.monotonic() + float(retry_after)
            log.debug("telegram stream_update skipped: %s", type(exc).__name__)

    async def stream_finalize(
        self, channel_id: str, message_id: str, text: str, suggestions: list[str] | None = None
    ) -> None:
        from backend.connectors.base import utf16_len

        # Follow-up suggestions must ride the answer as a reply keyboard, and a streamed (edited)
        # message can't gain one — so replace the preview with a fresh send that carries it. One
        # message (the answer + chips), no separate carrier bubble; a small one-time flicker only
        # when the user has opted into suggestions.
        if suggestions:
            await self.delete_message(channel_id, message_id)
            await self.send_text(channel_id, text, suggestions=suggestions)
            return

        # Rich (table/math, and SAFE per rich_unsafe) that fits one message → edit the SAME preview
        # in place into a rich message (Bot API 10.2 editMessageText rich_message) — no delete, no
        # flicker. Only if that in-place edit fails, or the reply overflows one message, do we drop
        # the preview and re-send fresh. Everything else is a final in-place HTML edit.
        rich = self._rich_eligible(text)
        if rich and utf16_len(text) <= 4000:
            if await self._edit_rich(channel_id, message_id, text):
                return
        if rich or utf16_len(text) > 4000:
            await self.delete_message(channel_id, message_id)
            await self.send_text(channel_id, text)
        else:
            await self.stream_update(channel_id, message_id, text)

    async def _edit_rich(self, channel_id: str, message_id: str, text: str) -> bool:
        from backend.connectors.telegram.formatter import markdown_to_rich_html
        from backend.connectors.telegram.rich import edit_rich_html

        token = getattr(self._bot, "token", "")
        if not isinstance(token, str) or not token:
            return False
        return await edit_rich_html(token, channel_id, message_id, markdown_to_rich_html(text))

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        try:
            await self._bot.delete_message(chat_id=int(channel_id), message_id=int(message_id))
        except Exception as exc:  # noqa: BLE001
            log.debug("telegram delete_message skipped: %s", type(exc).__name__)

    supports_documents = True

    async def send_document(
        self, channel_id: str, content: bytes, filename: str, caption: str = ""
    ) -> None:
        """Send a file attachment via Telegram's native sendDocument (Hermes's model) — the way an
        artifact (HTML/SVG) reaches a chat user: a downloadable file, not a rendered iframe."""
        from io import BytesIO

        await self._bot.send_document(
            chat_id=int(channel_id),
            document=BytesIO(content),
            filename=filename,
            caption=(caption[:1024] or None),
        )

    # Continuous typing indicator (Hermes's keep-typing) — refreshed by the processor during a long
    # turn so "typing…" doesn't vanish after Telegram's ~5s window.
    supports_typing = True

    async def send_typing(self, channel_id: str) -> None:
        try:
            await self._bot.send_chat_action(chat_id=int(channel_id), action="typing")
        except Exception as exc:  # noqa: BLE001 — a typing blip must never affect the turn
            log.debug("telegram send_typing skipped: %s", type(exc).__name__)

    # Native image reply (parity with Hermes's send_photo). No caller today — Lucius delivers
    # artifacts as FILES (owner choice) — but the capability exists for a future image tool.
    supports_media = True

    async def send_photo(self, channel_id: str, content: bytes, caption: str = "") -> None:
        from io import BytesIO

        await self._bot.send_photo(
            chat_id=int(channel_id), photo=BytesIO(content), caption=(caption[:1024] or None)
        )

    # Follow-up suggestions (opt-in via /suggestions; default off) ride the answer message as a
    # native REPLY keyboard (chips UNDER the input) — see `send_text`/`stream_finalize`. Tapping a
    # chip sends the text as a normal message → a normal turn, so no callback/menu is needed.
    supports_suggestions = True

    def suggestions_enabled(self) -> bool:
        from backend.db.telegram_config_store import TelegramConfigStore

        return TelegramConfigStore().suggestions_enabled()

    def tool_stream_enabled(self) -> bool:
        """Whether to show live tool activity while a turn works (default on, /toolstream).

        Read fresh each turn, like ``streaming_enabled``, so a toggle applies immediately."""
        from backend.db.telegram_config_store import TelegramConfigStore

        return TelegramConfigStore().tool_stream_enabled()

    async def send_proposed_change(
        self, channel_id: str, change: dict, change_id: str, summary: str
    ) -> None:
        # A PERMISSION card (one carrying `grant_tool`; RFC 2026-07-20 §13.1) offers the same
        # three choices web does — a channel must not be quietly weaker (ADR
        # 2026-07-19-confirm-gate-surface-matrix). The per-session tier was removed on
        # 2026-08-02, so three buttons fit one phone-width row without truncating.
        #
        # `callback_data` is capped at 64 BYTES by Telegram. The longest here is
        # `allow_always:` (13) + an 8-char change id (`pending_changes.store`) = 21, so there
        # is ample headroom — but the id length is the reason to keep these prefixes short
        # rather than spelling the scope out further.
        if change.get("grant_tool"):
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✓ Once", callback_data=f"allow_once:{change_id}"),
                        InlineKeyboardButton("✓ Always", callback_data=f"allow_always:{change_id}"),
                        InlineKeyboardButton("✗ Deny", callback_data=f"deny:{change_id}"),
                    ],
                ]
            )
            # The card body is CANONICAL MARKDOWN built once for every channel
            # (processor.format_permission_card) and rendered here to Telegram's HTML —
            # the fenced summary becomes a <pre>, which both preserves aligned columns on a
            # phone AND html-escapes the untrusted names inside it (see that function).
            await self._bot.send_message(
                chat_id=int(channel_id),
                text=render_dialect(format_permission_card(change, summary), "telegram"),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✓ Apply", callback_data=f"confirm:{change_id}"),
                    InlineKeyboardButton("✗ Discard", callback_data=f"discard:{change_id}"),
                ]
            ]
        )
        await self._bot.send_message(
            chat_id=int(channel_id),
            text=f"Proposed change:\n\n{summary}",
            reply_markup=keyboard,
        )


# ── Command handlers ──────────────────────────────────────────────────────────


async def handle_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your chat ID: {update.effective_chat.id}")


# Single source of truth for the command list — shown by /start and /help so they never
# drift apart. Each line is "  /cmd — description".
_COMMAND_HELP = (
    "Commands:\n"
    "  /model [name] — show or switch the chat model\n"
    "  /status — model, effort, streaming + today's cost\n"
    "  /cost — today's and 30-day AI spend\n"
    "  /effort [low|medium|high|max] — set response depth (default: low)\n"
    "  /streaming [on|off] — live edit-in-place replies (default: on)\n"
    "  /suggestions [on|off] — tappable follow-up suggestions (default: off)\n"
    "  /new — start a fresh conversation (saves bandwidth)\n"
    "  /stop — stop the reply that's currently generating\n"
    "  /id — show this chat's ID\n"
    "  /help — show this list"
)

# The command list Telegram shows in the "/" autocomplete panel (via setMyCommands). Same source
# of truth as _COMMAND_HELP — (command, description) pairs, no leading slash.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("model", "Show or switch the chat model"),
    ("status", "Model, effort, streaming + today's cost"),
    ("cost", "Today's and 30-day AI spend"),
    ("effort", "Set response depth: low|medium|high|max"),
    ("streaming", "Live edit-in-place replies: on|off"),
    ("suggestions", "Tappable follow-up suggestions: on|off"),
    ("toolstream", "Show what I'm doing while I work: on|off"),
    ("new", "Start a fresh conversation"),
    ("stop", "Stop the reply that's currently generating"),
    ("id", "Show this chat's ID"),
    ("help", "Show the command list"),
]


async def _not_approved(update: Update, *, discovery: bool) -> None:
    """Reply to a not-yet-approved sender instead of the old silent drop (owner review,
    2026-08-22). Silent under ``disabled`` on purpose — that policy's whole contract is "ignore
    everything", so replying there would contradict its own definition.

    ``discovery=True`` (``/start``/``/help`` — the two commands a stranger actually taps first,
    Telegram shows /start as the big first button) gives the same "here's your chat id" message
    as the plain-text path (``connectors.chat.run._handle_allowlist_reject``). Every other
    gated command gets a short generic nudge toward those two instead of repeating the full
    explanation on every single one."""
    from backend.db.telegram_config_store import TelegramConfigStore

    if TelegramConfigStore().access_policy() == "disabled":
        return
    if discovery:
        await update.message.reply_text(
            "Hi! You'll need to be added before I can help. Send this number to whoever set up "
            f"this bot: {update.effective_chat.id}"
        )
    else:
        await update.message.reply_text("This bot isn't set up for you yet — try /start or /help.")


async def handle_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _policy_allows_turn(update.effective_chat.id):
        await _not_approved(update, discovery=True)
        return
    await update.message.reply_text(
        "Hi! I'm Lucius, your personal wealth assistant — I have access to your accounts, "
        "transactions, budgets, investments, and taxes.\n\n"
        "What I can help with:\n"
        '• Answer questions — "What\'s my net worth?", "How much did I spend on dining this '
        'month?", "How\'s my portfolio doing?"\n'
        "• Log things — send me a receipt photo or forward a bank statement and I'll record it\n"
        "• Take action — ask me to log a transaction, adjust a budget, or update a holding; "
        "some actions may ask you to confirm first, depending on your permission settings\n"
        "• Watch for you — I'll message you proactively with price alerts and things worth "
        "flagging; just reply to keep the conversation going\n\n" + _COMMAND_HELP
    )


async def handle_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _policy_allows_turn(update.effective_chat.id):
        await _not_approved(update, discovery=True)
        return
    await update.message.reply_text(
        "I'm Lucius — your personal wealth assistant, with access to your full financial "
        "picture: accounts, transactions, budgets, investments, and taxes.\n\n"
        'Ask me anything — "What\'s my net worth?", "Show me my top spending categories", '
        '"Am I on track with my budget?" — or send a statement file or a photo of a receipt '
        "and I'll log it. I can also make changes when you ask: log a transaction, adjust a "
        "budget, update a holding.\n\n" + _COMMAND_HELP
    )


async def handle_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    args = ctx.args or []
    if not args or args[0] not in VALID_EFFORTS:
        current = session_map.get_effort(CONNECTOR, str(chat_id))
        await update.message.reply_text(
            f"Response depth (current: {current}):",
            reply_markup=_choice_keyboard("effort", ["low", "medium", "high", "max"], current),
        )
        return
    session_map.set_effort(CONNECTOR, str(chat_id), args[0])
    await update.message.reply_text(f"Effort set to: {args[0]}")


async def handle_streaming(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle live edit-in-place streaming (default on). ``/streaming`` shows state; ``on``/``off``
    sets it. Persisted on the channel config, so it applies to the very next turn."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    arg = (ctx.args[0].lower() if ctx.args else "").strip()
    if arg in ("on", "off"):
        store.set_streaming(arg == "on")
        await update.message.reply_text(f"Live streaming turned {arg}.")
    else:
        cur = "on" if store.streaming_enabled() else "off"
        await update.message.reply_text(
            f"Live edit-in-place replies (current: {cur}):",
            reply_markup=_choice_keyboard("streaming", ["on", "off"], cur),
        )


async def handle_toolstream(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the live tool-activity line (default on). ``/toolstream`` shows state;
    ``on``/``off`` sets it.

    Telegram otherwise shows only "typing…" for a whole turn, so a long multi-tool answer is
    indistinguishable from a stall. Behind a toggle because it costs one extra message per
    turn (edited in place, deleted when the answer lands) and quiet is a fair preference."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    arg = (ctx.args[0].lower() if ctx.args else "").strip()
    if arg in ("on", "off"):
        store.set_tool_stream(arg == "on")
        await update.message.reply_text(f"Tool activity turned {arg}.")
    else:
        cur = "on" if store.tool_stream_enabled() else "off"
        await update.message.reply_text(
            f"Show what I'm doing while I work (current: {cur}):",
            reply_markup=_choice_keyboard("toolstream", ["on", "off"], cur),
        )


# Per-chat cache of the canonical models last shown in a /model picker. Callback data carries
# only the index to stay under Telegram's 64-byte limit; the cached row retains provider identity.
_model_menu: dict[int, tuple[str, list[ModelChoice]]] = {}
# Per-chat cache of the follow-up suggestions last shown (index → text, for callback data).


async def handle_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or switch this Telegram conversation's provider-pinned model."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.ai import conversation
    from backend.db.model_config_store import ModelConfigStore
    from backend.services.model_selection import configured_model_choices, describe_model_ref

    store = ModelConfigStore()
    session_id = session_map.get_or_create_session(CONNECTOR, str(chat_id))
    arg = " ".join(ctx.args).strip() if ctx.args else ""
    if arg:
        if ":" not in arg:
            current_ref = conversation.turn_model_ref(session_id, store=store) or ""
            current = describe_model_ref(current_ref, store=store)
            await update.message.reply_text(
                "Model names must include provider:model so the selection cannot silently "
                "switch providers.\n\n"
                f"Current model: {current.display}\n"
                f"Try: /model {current.provider_key}:{arg}"
            )
            return
        try:
            choice = describe_model_ref(arg, store=store)
            conversation.select_session_model(session_id, choice.ref, store=store)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Can't switch model: {exc}")
            return
        await update.message.reply_text(f"✅ Model set to: {choice.display}")
        return

    current_ref = conversation.turn_model_ref(session_id, store=store) or ""
    current = describe_model_ref(current_ref, store=store)
    choices = configured_model_choices(store=store)
    _model_menu[chat_id] = (session_id, choices)
    keyboard = (
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        ("● " if c.ref == current.ref else "") + c.display,
                        callback_data=f"model:{i}",
                    )
                ]
                for i, c in enumerate(choices)
            ]
        )
        or None
    )
    await update.message.reply_text(
        f"Current model: {current.display}\n\n"
        "Tap to switch, or send /model <provider:model> for any model:",
        reply_markup=keyboard,
    )


async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """One-line runtime state: model, provider, effort, streaming + today's AI spend."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.ai import conversation
    from backend.db.model_config_store import ModelConfigStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.services.model_selection import describe_model_ref

    store = ModelConfigStore()
    session_id = session_map.get_or_create_session(CONNECTOR, str(chat_id))
    selected_ref = conversation.turn_model_ref(session_id, store=store) or ""
    selected = describe_model_ref(selected_ref, store=store) if selected_ref else None
    effort = session_map.get_effort(CONNECTOR, str(chat_id))
    streaming = "on" if TelegramConfigStore().streaming_enabled() else "off"
    lines = [
        "Status",
        f"• Model: {selected.model_id if selected else 'Not configured'}",
        f"• Provider: {selected.provider_label if selected else 'Not configured'}",
        f"• Effort: {effort}",
        f"• Streaming: {streaming}",
    ]
    try:
        from backend.services.cost import cost_summary

        today = cost_summary(days=1)["total"]
        lines.append(f"• Today: ${today.get('cost_usd', 0):.3f} ({today.get('turns', 0)} turns)")
    except Exception as exc:  # noqa: BLE001
        log.debug("status cost lookup failed: %s", type(exc).__name__)
    await update.message.reply_text("\n".join(lines))


async def handle_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Today's and 30-day AI spend (tokens + USD)."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.services.cost import cost_summary

    try:
        today = cost_summary(days=1)["total"]
        month = cost_summary(days=30)["total"]
    except Exception as exc:  # noqa: BLE001
        log.debug("cost lookup failed: %s", type(exc).__name__)
        await update.message.reply_text("Cost data is unavailable right now.")
        return
    await update.message.reply_text(
        "AI cost\n"
        f"• Today: ${today.get('cost_usd', 0):.3f} • {today.get('turns', 0)} turns • "
        f"{today.get('output_tokens', 0):,} out tokens\n"
        f"• Last 30d: ${month.get('cost_usd', 0):.2f} • {month.get('turns', 0)} turns"
    )


async def handle_suggestions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle tappable follow-up suggestions after each reply (default off — costs one small model
    call per turn). ``/suggestions`` shows state; ``on``/``off`` sets it."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    arg = (ctx.args[0].lower() if ctx.args else "").strip()
    if arg in ("on", "off"):
        store.set_suggestions(arg == "on")
        await update.message.reply_text(f"Follow-up suggestions turned {arg}.")
    else:
        cur = "on" if store.suggestions_enabled() else "off"
        await update.message.reply_text(
            f"Follow-up suggestions (current: {cur}):",
            reply_markup=_choice_keyboard("suggestions", ["on", "off"], cur),
        )


async def handle_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a fresh conversation. ``reset_session`` deletes the old session (transcript + fold
    summary + any staged proactive messages), so the next turn sends a minimal prompt — the
    bandwidth win. (Was also reachable as /clear, dropped 2026-07-20 — one command, not two.)"""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    session_reset.reset_session(CONNECTOR, str(chat_id))
    _model_menu.pop(chat_id, None)
    await update.message.reply_text("Done — starting a fresh conversation.")


async def handle_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the turn currently generating for THIS chat — cancels the in-flight LLM call (and
    any pending debounce flush), tearing down the upstream stream so generation AND billing
    stop, not just the visible output. Only affects this chat; nothing running → a no-op reply.

    Works while a turn is mid-generation because the bot runs with ``concurrent_updates`` (see
    ``bot.py``): this command is dispatched in its own task concurrently with the running turn,
    so it isn't queued behind it (the failure mode OpenClaw #20600 / Hermes #26813 hit)."""
    chat_id = update.effective_chat.id
    if not _policy_allows_turn(chat_id):
        await _not_approved(update, discovery=False)
        return
    stopped = turn_registry.stop(CONNECTOR, str(chat_id))
    log.info("/stop for chat_id=%s — stopped=%s", chat_id, stopped)
    await update.message.reply_text("⏹ Stopped." if stopped else "Nothing is running to stop.")


# ── Message handlers ──────────────────────────────────────────────────────────


# Inbound aggregation: Telegram's client splits a >4096-char paste into consecutive messages.
# A message at/over this boundary is likely such a fragment — buffer it briefly to rejoin the rest
# into ONE turn. NORMAL (shorter) messages are dispatched immediately — no added latency, and the
# synchronous-turn contract callers/tests rely on is preserved.
_SPLIT_THRESHOLD = 4000
_TEXT_BATCH_DELAY_SEC = 0.5
_text_batches: dict[int, dict[str, Any]] = {}


def _quoted_message(message) -> "QuotedMessage | None":
    """Normalize Telegram's ``reply_to_message`` into the channel-neutral quote shape.

    A photo/document reply carries its text in ``caption``; an empty quote (a reply to a
    bare sticker, say) is dropped rather than sent as an empty block.
    """
    quoted = getattr(message, "reply_to_message", None)
    if quoted is None:
        return None
    body = (getattr(quoted, "text", None) or getattr(quoted, "caption", None) or "").strip()
    if not body:
        return None
    author = getattr(quoted, "from_user", None)
    return QuotedMessage(
        text=body,
        from_bot=bool(getattr(author, "is_bot", False)),
        message_id=getattr(quoted, "message_id", None),
    )


async def _dispatch_text(
    bot, chat_id: int, message_id: int, text: str, *, react: bool = True, reply_to=None
) -> None:
    """React/type (policy-aware, once) then route the text through the inbound seam."""
    adapter = _TelegramAdapter(bot)
    inbound = InboundMessage(
        channel_id=str(chat_id), sender_id=str(chat_id), text=text, reply_to=reply_to
    )
    if react and adapter.is_allowed(str(chat_id)):
        try:
            await bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id, reaction=[ReactionTypeEmoji("👀")]
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("set_message_reaction skipped for %s: %s", chat_id, exc)
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(0)  # let the reaction flush before the turn blocks the loop
    try:
        await handle_inbound(adapter, inbound)
    except Exception:  # pragma: no cover — handle_inbound already swallows turn errors
        log.exception("Error processing message from %s", chat_id)
        await bot.send_message(chat_id=chat_id, text="Something went wrong.")


async def _flush_text_batch(chat_id: int, bot) -> None:
    """Fire after a quiet window; rejoin the buffered split fragments (verbatim, no separator — a
    client split adds none) into one turn. A newer fragment cancels + supersedes this flush."""
    try:
        await asyncio.sleep(_TEXT_BATCH_DELAY_SEC)
    except asyncio.CancelledError:
        return
    batch = _text_batches.pop(chat_id, None)
    if not batch:
        return
    await _dispatch_text(
        bot,
        chat_id,
        batch["message_id"],
        "".join(batch["parts"]),
        react=False,
        reply_to=batch.get("reply_to"),
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or "unknown"
    text = update.message.text or ""
    log.info("message from @%s (chat_id=%s): %s", username, chat_id, text[:80])

    batch = _text_batches.get(chat_id)
    # Common case: a normal-length message, no batch in flight → dispatch immediately (unchanged).
    if batch is None and len(text) < _SPLIT_THRESHOLD:
        await _dispatch_text(
            ctx.bot,
            chat_id,
            update.message.message_id,
            text,
            reply_to=_quoted_message(update.message),
        )
        return

    # Likely a client-split fragment (or a continuation of one) — buffer + debounce to rejoin.
    if batch is None:
        batch = _text_batches[chat_id] = {
            "parts": [],
            "task": None,
            "message_id": update.message.message_id,
            "reply_to": _quoted_message(update.message),
        }
        adapter = _TelegramAdapter(ctx.bot)
        if adapter.is_allowed(str(chat_id)):  # react once, on the first fragment
            try:
                await ctx.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=update.message.message_id,
                    reaction=[ReactionTypeEmoji("👀")],
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("set_message_reaction skipped for %s: %s", chat_id, exc)
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
    batch["parts"].append(text)
    if batch["task"] is not None:
        batch["task"].cancel()
    batch["task"] = asyncio.create_task(_flush_text_batch(chat_id, ctx.bot))
    # Track the pending flush so /stop can cancel the not-yet-launched turn (OpenClaw #51046).
    turn_registry.register_debounce(CONNECTOR, str(chat_id), batch["task"])


_DOWNLOAD_RETRY_DELAY = 1.0


async def _download_file(bot, file_id: str) -> bytes:
    """Fetch an inbound file, retrying ONCE on a network error — a lone ``get_file`` timeout
    once dropped a receipt photo with nothing but the 👀 reaction to show for it."""
    try:
        tg_file = await bot.get_file(file_id)
        return bytes(await tg_file.download_as_bytearray())
    except NetworkError as exc:
        if isinstance(exc, BadRequest):  # permanent (e.g. file too big) — a retry can't help
            raise
        log.info("telegram file download failed, retrying once: %r", exc)
    await asyncio.sleep(_DOWNLOAD_RETRY_DELAY)
    tg_file = await bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def handle_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The Application's error handler: without one, PTB prints a handler's exception to
    stderr only, so a dropped message left no trace in app.log."""
    chat = getattr(update, "effective_chat", None)
    log.error(
        "unhandled error in Telegram handler (chat_id=%s)",
        getattr(chat, "id", None),
        exc_info=ctx.error,
    )


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or "unknown"
    if not _policy_allows_turn(chat_id):
        log.warning("rejected photo from @%s (chat_id=%s)", username, chat_id)
        await _not_approved(update, discovery=False)
        return

    photo = update.message.photo[-1]  # largest available size
    caption = update.message.caption or ""
    media_group_id = update.message.media_group_id
    message_id = update.message.message_id
    session_id = session_map.get_or_create_session(CONNECTOR, str(chat_id))

    log.info(
        "photo from @%s (chat_id=%s, group=%s) caption: %s",
        username,
        chat_id,
        media_group_id,
        caption[:80],
    )

    try:
        await ctx.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji("👀")],
        )
    except Exception:
        pass

    # Download the photo and persist it into AttachmentStore before any turn construction
    # (cross-channel-attachments RFC items 3/11/12) — a Telegram photo is now a durable
    # attachment ref exactly like a web upload, never raw bytes riding straight into
    # build_user_content as ``images=`` (removed).
    try:
        raw_bytes = await _download_file(ctx.bot, photo.file_id)
    except TelegramError as exc:
        log.warning("photo download from %s failed after retry: %r", chat_id, exc)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="I couldn't download your photo from Telegram. Please send it again.",
        )
        return
    from backend.services.attachment_ingress import persist_inbound_file

    try:
        result = persist_inbound_file(
            f"telegram_photo_{message_id}.jpg",
            "image/jpeg",
            raw_bytes,
            source_channel="connector:telegram",
            source_conversation_id=session_id,
            source_message_id=str(message_id),
        )
    except ValueError as exc:
        log.exception("Error persisting photo from %s", chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"Failed to process the photo: {exc}")
        return
    attachment_id = result["attachment_id"]

    if not media_group_id:
        # Single photo — process immediately
        await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        effort = session_map.get_effort(CONNECTOR, str(chat_id))
        connector = _TelegramAdapter(ctx.bot)
        try:
            await process_message(
                connector,
                str(chat_id),
                session_id,
                caption,
                effort,
                attachment_refs=[attachment_id],
                reply_to=_quoted_message(update.message),
            )
        except Exception as exc:
            log.exception("Error processing photo from %s", chat_id)
            await ctx.bot.send_message(chat_id=chat_id, text=f"Something went wrong:\n{exc}")
        return

    # Media group — buffer the already-persisted attachment id until all photos arrive,
    # then process together.
    key = (chat_id, media_group_id)
    if key not in _media_group_buffer:
        _media_group_buffer[key] = []

    _media_group_buffer[key].append(
        {
            "attachment_id": attachment_id,
            "caption": caption,
            "bot": ctx.bot,
            "session_id": session_id,
            # An album is several Updates; the person replied from ONE of them, so keep
            # the first quote seen rather than letting a later frame clear it.
            "reply_to": _quoted_message(update.message),
        }
    )

    # Cancel existing flush task (if any) and reschedule — keeps extending until quiet
    existing = _media_group_tasks.get(key)
    if existing and not existing.done():
        existing.cancel()

    async def _flush_media_group(k: tuple[int, str]) -> None:
        await asyncio.sleep(_MEDIA_GROUP_DELAY)
        items = _media_group_buffer.pop(k, [])
        _media_group_tasks.pop(k, None)
        if not items:
            return
        gid_chat_id, _ = k
        bot = items[0]["bot"]
        gid_session_id = items[0]["session_id"]
        caption = next((i["caption"] for i in items if i["caption"]), "")
        reply_to = next((i["reply_to"] for i in items if i.get("reply_to")), None)
        attachment_ids = [i["attachment_id"] for i in items]
        log.info(
            "flushing media group %s: %d photo(s) for chat %s",
            k[1],
            len(attachment_ids),
            gid_chat_id,
        )
        await bot.send_chat_action(chat_id=gid_chat_id, action="typing")
        effort = session_map.get_effort(CONNECTOR, str(gid_chat_id))
        connector = _TelegramAdapter(bot)
        try:
            await process_message(
                connector,
                str(gid_chat_id),
                gid_session_id,
                caption,
                effort,
                attachment_refs=attachment_ids,
                reply_to=reply_to,
            )
        except Exception as exc:
            log.exception("Error processing media group from %s", gid_chat_id)
            await bot.send_message(chat_id=gid_chat_id, text=f"Something went wrong:\n{exc}")

    _media_group_tasks[key] = asyncio.create_task(_flush_media_group(key))


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or "unknown"
    if not _policy_allows_turn(chat_id):
        log.warning("rejected document from @%s (chat_id=%s)", username, chat_id)
        await _not_approved(update, discovery=False)
        return

    doc = update.message.document
    caption = update.message.caption or ""
    log.info(
        "document from @%s (chat_id=%s) name=%s mime=%s",
        username,
        chat_id,
        doc.file_name,
        doc.mime_type,
    )

    try:
        await ctx.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji("👀")],
        )
    except Exception:
        pass

    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")

    from backend.services.attachment_ingress import persist_inbound_file

    session_id = session_map.get_or_create_session(CONNECTOR, str(chat_id))
    password = caption.strip() or None
    try:
        raw_bytes = await _download_file(ctx.bot, doc.file_id)
        result = persist_inbound_file(
            doc.file_name or "",
            doc.mime_type or "",
            raw_bytes,
            source_channel="connector:telegram",
            source_conversation_id=session_id,
            source_message_id=str(update.message.message_id),
            password=password,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("password_required:"):
            await ctx.bot.send_message(
                chat_id=chat_id,
                text="🔒 This file is password-protected. Resend it with the password as the caption.",
            )
        else:
            await ctx.bot.send_message(
                chat_id=chat_id, text=f"Sorry, I can't read that file: {msg}"
            )
        return
    except Exception as exc:
        log.exception("Error downloading/parsing document from %s", chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"Failed to process the file: {exc}")
        return

    ref_id = result["attachment_id"]
    # If the caption was consumed as a decryption password, don't forward it to the AI
    ai_text = "" if result["was_encrypted"] else caption
    effort = session_map.get_effort(CONNECTOR, str(chat_id))
    connector = _TelegramAdapter(ctx.bot)
    try:
        await process_message(
            connector,
            str(chat_id),
            session_id,
            ai_text,
            effort,
            attachment_refs=[ref_id],
            reply_to=_quoted_message(update.message),
        )
    except Exception as exc:
        log.exception("Error processing document from %s", chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"Something went wrong:\n{exc}")


# ── Callback query handler (confirm / discard) ────────────────────────────────

# Optimistic keyboard-clearing text shown the instant a change-id-bound button is tapped,
# before ``confirm.handle_confirm_action`` (which may drain a whole resumed turn) resolves
# the REAL outcome. Overwritten unconditionally once that outcome is known.
_PROVISIONAL_ACK = {
    "confirm": "⏳ Applying…",
    "discard": "✗ Discarded.",
    "allow_once": "✓ Allowed once — applying…",
    "allow_always": "✓ Always allowed — applying…",
    "deny": "✗ Denied.",
}


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not _policy_allows_turn(query.from_user.id):
        return

    data = query.data or ""
    if ":" not in data:
        return

    action, change_id = data.split(":", 1)

    if action == "model":  # a /model picker tap → switch the chat model
        from backend.ai import conversation

        menu = _model_menu.get(update.effective_chat.id)
        session_id = session_map.get_or_create_session(CONNECTOR, str(update.effective_chat.id))
        if menu is None or menu[0] != session_id:
            await query.edit_message_text("That model menu expired — send /model again.")
            return
        choices = menu[1]
        try:
            choice = choices[int(change_id)]
        except (ValueError, IndexError):
            await query.edit_message_text("That model menu expired — send /model again.")
            return
        try:
            conversation.select_session_model(session_id, choice.ref)
        except Exception as exc:  # noqa: BLE001
            await query.edit_message_text(f"Can't switch model: {exc}")
            return
        await query.edit_message_text(f"✅ Model set to: {choice.display}")
        return

    if action == "set":  # a bare-command quick-answer tap → apply the choice
        field, _, value = change_id.partition(":")
        from backend.db.telegram_config_store import TelegramConfigStore

        if field == "effort" and value in VALID_EFFORTS:
            session_map.set_effort(CONNECTOR, str(update.effective_chat.id), value)
            await query.edit_message_text(f"✅ Effort set to: {value}")
        elif field == "streaming" and value in ("on", "off"):
            TelegramConfigStore().set_streaming(value == "on")
            await query.edit_message_text(f"✅ Live streaming turned {value}.")
        elif field == "suggestions" and value in ("on", "off"):
            TelegramConfigStore().set_suggestions(value == "on")
            await query.edit_message_text(f"✅ Follow-up suggestions turned {value}.")
        elif field == "toolstream" and value in ("on", "off"):
            TelegramConfigStore().set_tool_stream(value == "on")
            await query.edit_message_text(f"✅ Tool activity turned {value}.")
        else:
            await query.edit_message_text("Unknown option.")
        return

    # Route confirm/discard through the SHARED confirm seam (chat-channel-seam) — the same
    # path Slack's Block Kit buttons use → ``positions_service.apply_position_change``. The
    # allowlist was re-checked above (a tap is a fresh inbound request). Pass the tapper's
    # identity so the confirm seam can enforce tapper==proposer (IDOR): Telegram binds the
    # proposed change on (telegram, str(chat_id), str(chat_id)) — for the private-bot DM the
    # tap's chat id IS that proposer key, so the operator's own confirm always matches.
    chat_id = str(update.effective_chat.id)
    log.info("handle_callback: action=%s change_id=%s chat_id=%s", action, change_id, chat_id)

    # Clear the card's keyboard THE INSTANT the tap is processed — before awaiting
    # ``handle_confirm_action`` below, which (for a tool_use_id card) drains the resumed
    # turn to completion via ``pump_channel_turn`` and can take many seconds. Without this,
    # the button-tap is honoured immediately server-side but the visible card sits with its
    # Once/Always/Deny buttons live until the whole rest of the turn finishes streaming —
    # inviting a confused second tap. This is optimistic (the action may still turn out
    # "stale"/"ignored"/"failed"), so the final edit below always overwrites it with the
    # real outcome once known.
    provisional = _PROVISIONAL_ACK.get(action)
    if provisional is not None:
        try:
            await query.edit_message_text(provisional)
        except Exception:
            log.warning(
                "handle_callback: could not clear keyboard immediately for change %s", change_id
            )

    try:
        outcome = await confirm.handle_confirm_action(
            action,
            change_id,
            connector=CONNECTOR,
            channel_id=chat_id,
            sender_id=chat_id,
            # The renderer for the turn this tap RESUMES. Without it the resumed turn was
            # invisible (its prose and any further permission card were dropped) and ran
            # with no cost surface bound, which disarmed the confirm gate for the rest of
            # the turn — so approving one permission approved every later one. See
            # ``processor.pump_channel_turn``.
            adapter=_TelegramAdapter(ctx.bot),
        )
    except Exception:
        log.exception(
            "handle_callback: unexpected error in handle_confirm_action for change %s", change_id
        )
        try:
            await ctx.bot.send_message(
                chat_id=int(chat_id), text="Something went wrong processing your request."
            )
        except Exception:
            pass
        return
    log.info("handle_callback: outcome=%s for change %s", outcome.status, change_id)
    if outcome.status == "ignored":
        # "ignored" means IDOR mismatch or unrecognised action — tell the user rather than
        # silently dropping so they have a clue if something is misconfigured.
        try:
            await ctx.bot.send_message(
                chat_id=int(chat_id),
                text="This action could not be applied (already processed or not yours to confirm).",
            )
        except Exception:
            pass
        return
    reply = outcome.message
    try:
        await query.edit_message_text(reply)
    except Exception:
        log.warning(
            "handle_callback: edit_message_text failed for change %s, falling back to send_message",
            change_id,
        )
        try:
            await ctx.bot.send_message(chat_id=int(chat_id), text=reply)
        except Exception:
            log.exception(
                "handle_callback: fallback send_message also failed for change %s", change_id
            )
