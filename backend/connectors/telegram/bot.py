"""Telegram bot lifecycle — start/stop as an asyncio task within FastAPI."""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("backend.connectors.telegram.bot")  # under "backend" → app.log

_app = None  # telegram.ext.Application instance
_task: asyncio.Task | None = None  # the connect-with-retry supervisor task

# Serializes start()/stop()/reload() ENTRY — the generic connector enable/disable/remove
# routes, PUT /api/telegram/config, and (indirectly) the AI's set_connector_enabled tool
# can all now trigger one of these concurrently. Without mutual exclusion here, two
# interleaved calls mutating _app/_task across multiple `await` points can
# orphan a live process while the module believes it is stopped (security review finding).
# Scoped to these three entry points only — NOT held for the lifetime of a connection, so
# the long-running retry-supervisor task (_run_with_retry, spawned by _start_locked and
# left running detached) is unaffected; its own interaction with a concurrent stop() is
# already race-free via plain task cancellation (see stop()'s own comment).
_lock = asyncio.Lock()

# Reconnect backoff bounds (seconds) — mirror the discord/slack connectors. An unreachable
# Telegram (transient network error, a regional block) must never block or fail the boot, and
# must self-heal once connectivity returns.
_RECONNECT_MIN = 2.0
_RECONNECT_MAX = 60.0


def _telegram_disabled() -> bool:
    """A second app instance (a test boot / QA agent / a parallel dev server) must NOT
    start a Telegram poller: Telegram allows ONE getUpdates poller per bot token, and a
    second one issues `Conflict: terminated by other getUpdates request` that takes the
    FIRST (e.g. your running dev server) down. So any non-primary boot sets
    ONW_DISABLE_TELEGRAM=1 to skip the connector entirely (the API still serves)."""
    return os.getenv("ONW_DISABLE_TELEGRAM", "").strip().lower() in ("1", "true", "yes", "on")


async def start() -> None:
    """Validate config, then connect in a supervised BACKGROUND task that retries with
    exponential backoff. An unreachable Telegram NEVER blocks or fails the boot — it
    self-heals once connectivity returns. Mirrors the discord/slack connectors, which
    already run their connection as a background task (telegram was the lone connector that
    connected synchronously inline and so could crash startup on a network timeout).

    Serialized against a concurrent stop()/reload() via ``_lock`` — see the module-level
    comment on ``_lock`` for why."""
    async with _lock:
        await _start_locked()


async def _start_locked() -> bool:
    """``start()``'s actual body — assumes ``_lock`` is ALREADY held by the caller. Never
    call this directly except from ``start()``/``reload()``, both of which hold the lock
    for their whole operation (``reload()`` needs stop-then-start to be ATOMIC, which two
    separate ``async with _lock`` blocks would not guarantee — another entry point could
    run in the gap between them).

    Returns whether start completed with no error — mirrors ``_stop_locked``'s own
    best-effort/non-raising contract (security review 2026-08-17, finding A) so ``reload()``
    can report a real result instead of assuming "no exception" means "succeeded". NOTE this
    does NOT mean the bot is actually connected yet: spawning the retry-supervisor task is
    fire-and-forget by design (an unreachable Telegram must never fail the caller) — ``True``
    here means only that nothing in the enable/token/task-creation path itself failed."""
    global _task
    if _telegram_disabled():
        log.info("ONW_DISABLE_TELEGRAM set — Telegram connector skipped (non-primary boot)")
        return True
    if _task is not None and not _task.done():
        return True  # already running
    try:
        # Config comes from the store, so the connector is UI-configurable.
        from backend.db.telegram_config_store import TelegramConfigStore

        cfg = TelegramConfigStore().get()
        if not cfg.get("enabled"):
            log.info("Telegram connector disabled (the connector is paused)")
            return True
        token = (cfg.get("bot_token") or "").strip()
        if not token:
            log.info("Telegram bot token not set — Telegram connector disabled")
            return True

        _task = asyncio.create_task(_run_with_retry(token))
        return True
    except Exception:
        log.exception("Telegram start failed")
        return False


async def _run_with_retry(token: str) -> None:
    """Connect with exponential backoff until it succeeds (or stop() cancels the task). Once
    _connect() returns, python-telegram-bot's updater owns the polling loop and rides out
    steady-state network blips itself — this loop only covers the INITIAL connect (the
    get_me() handshake that fails when Telegram is unreachable at boot)."""
    delay = _RECONNECT_MIN
    attempt = 0
    while True:
        attempt += 1
        try:
            await _connect(token)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — any connect error is retryable, never fatal
            await _teardown_partial()
            log.log(
                logging.WARNING if attempt == 1 else logging.DEBUG,
                "Telegram connect failed (attempt %d): %s — retrying in %.0fs",
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)


async def _connect(token: str) -> None:
    """Build the Application, register handlers, initialize the bot (the network step that
    can time out), then start polling. Sets the module-global _app."""
    global _app

    from backend.connectors.telegram.handlers import BOT_COMMANDS

    _app = _build_app(token)
    await _app.initialize()
    await _app.start()

    # Register the command list so Telegram shows the "/" autocomplete panel + menu button.
    # Best-effort — a failure here must never block the connector from starting.
    try:
        from telegram import BotCommand

        await _app.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram set_my_commands failed: %s", type(exc).__name__)

    # Steal the long-poll session from any previous run by issuing a
    # zero-timeout getUpdates, which forces Telegram to close it.
    await _app.bot.get_updates(timeout=0, offset=-1)
    await _app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram connector started (polling mode)")


def _build_app(token: str):
    """The Application with every handler registered — no network I/O."""
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    from backend.connectors.telegram.handlers import (
        handle_callback,
        handle_cost,
        handle_document,
        handle_effort,
        handle_error,
        handle_help,
        handle_id,
        handle_message,
        handle_model,
        handle_new,
        handle_photo,
        handle_start,
        handle_status,
        handle_stop,
        handle_streaming,
        handle_suggestions,
        handle_toolstream,
    )

    # concurrent_updates(True): dispatch each update in its own task. REQUIRED for /stop —
    # otherwise a long turn hogs the (default) sequential dispatcher and a /stop sent mid-turn
    # queues behind it, firing only after the turn already finished (the exact failure mode in
    # OpenClaw #20600 / Hermes #26813). The turn-batching critical section in handle_message
    # has no await between its _text_batches read and write, so it stays race-free under
    # cooperative scheduling; same-session turns are still serialized by assistant.run's lock.
    app = Application.builder().token(token).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("id", handle_id))
    app.add_handler(CommandHandler("model", handle_model))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("cost", handle_cost))
    app.add_handler(CommandHandler("effort", handle_effort))
    app.add_handler(CommandHandler("streaming", handle_streaming))
    app.add_handler(CommandHandler("suggestions", handle_suggestions))
    app.add_handler(CommandHandler("toolstream", handle_toolstream))
    app.add_handler(CommandHandler("new", handle_new))
    app.add_handler(CommandHandler("stop", handle_stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(handle_error)
    return app


async def _teardown_partial() -> None:
    """Best-effort cleanup of a partially-built _app after a failed connect, so the next
    retry starts clean and a stray _app can't make notify()/is_running() think the bot
    is live."""
    global _app
    if _app is None:
        return
    try:
        await _app.shutdown()
    except Exception:  # noqa: BLE001 — tearing down a half-initialized app is best-effort
        pass
    _app = None


def _notify_recipients() -> list[int]:
    """Chat IDs to push proactive notifications (price alerts, etc.) to — the allowlist, and
    ONLY the allowlist (owner review, 2026-08-22).

    Used to fall back to every chat that has ever talked to the bot (``session_map``) when the
    allowlist was empty — a leftover from the "empty allowlist = open" era. Once inbound
    messages started failing CLOSED on an empty allowlist (``connectors.telegram.auth.
    is_allowed``), that fallback became a side channel of its own: a stranger's message
    recorded before the allowlist was ever configured could still receive financial alerts
    even though they can no longer talk to the bot at all. An empty allowlist now means no
    recipients, full stop — same "no one" as the inbound gate."""
    from backend.db.telegram_config_store import TelegramConfigStore

    ids = TelegramConfigStore().allowed_chat_ids()
    if not ids:
        log.warning("telegram notify: no recipients — allowed_chat_ids is empty")

    out = []
    for x in ids:
        try:
            out.append(int(x))
        except ValueError:
            continue
    return out


async def notify(text: str) -> int:
    """Broadcast an HTML message to all notification recipients. Returns count sent.

    Best-effort: never raises, so a failed send can't break the caller (e.g. the
    price monitor). Safe no-op when the bot isn't running.
    """
    if _app is None:
        return 0

    from telegram.constants import ParseMode

    from backend.connectors.base import chunk_message, utf16_len
    from backend.connectors.msgfmt import render
    from backend.connectors.telegram.formatter import markdown_to_rich_html
    from backend.connectors.telegram.rich import needs_rich_rendering, send_rich_html

    # Rich Messages ONLY for constructs the legacy path degrades (tables/task-lists/details/math),
    # exactly like the chat path; an ordinary alert stays on the HTML path. Rendered lazily.
    try_rich = needs_rich_rendering(text)
    rich_html = markdown_to_rich_html(text) if try_rich else ""
    html_parts: list[str] | None = None
    sent = 0
    for chat_id in _notify_recipients():
        try:
            if try_rich and await send_rich_html(_app.bot.token, chat_id, rich_html):
                sent += 1
                continue
            if html_parts is None:
                html_parts = chunk_message(render(text, "telegram"), 4096, utf16_len, html=True)
            for part in html_parts:
                await _app.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            log.warning("notify: failed to send to %s: %s", chat_id, exc)
    return sent


async def notify_document(content: bytes, filename: str, caption: str = "") -> int:
    """Send one file (a report artifact) to every notification recipient. Returns count sent.

    Same best-effort contract as ``notify``: never raises, a safe no-op without the bot."""
    if _app is None:
        return 0

    from io import BytesIO

    sent = 0
    for chat_id in _notify_recipients():
        try:
            await _app.bot.send_document(
                chat_id=chat_id,
                document=BytesIO(content),
                filename=filename,
                caption=(caption[:1024] or None),
            )
            sent += 1
        except Exception as exc:
            log.warning("notify_document: failed to send to %s: %s", chat_id, exc)
    return sent


async def stop() -> bool:
    """Stop the connector. Serialized against a concurrent start()/reload() via ``_lock``.

    Returns ``True`` iff teardown completed with no error — a caller that needs a HARD
    guarantee the process is actually down (e.g. before erasing its credentials, see
    ``api.connectors_api``'s ``DELETE`` route) must check this rather than assume "no
    exception propagated" means "definitely stopped": every teardown step below is
    deliberately best-effort/non-raising (a half-up app must still get a best-effort
    teardown attempt), so the only way to observe a failure is this return value. Every
    failure is LOGGED (``log.exception``), never silently swallowed — the pre-fix version
    of this function caught every teardown exception and threw it away."""
    async with _lock:
        return await _stop_locked()


async def _stop_locked() -> bool:
    """``stop()``'s actual body — assumes ``_lock`` is ALREADY held by the caller (see
    ``_start_locked``'s docstring for why ``reload()`` needs this split)."""
    global _app, _task
    ok = True
    # Cancel the connect-with-retry supervisor first — it may be mid-retry/backoff.
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Telegram supervisor task raised during cancellation")
            ok = False
        _task = None
    current_app = _app
    if current_app is None:
        return ok
    # Each step is best-effort so a half-up app (e.g. cancelled mid-connect) still tears down
    # cleanly — but a failure here is now LOGGED and reflected in the return value, never
    # swallowed.
    try:
        if current_app.updater is not None:
            await current_app.updater.stop()
        await current_app.stop()
        await current_app.shutdown()
    except Exception:
        log.exception("Telegram teardown failed — the process may still be partially live")
        ok = False
    # Compare-and-clear, not a blind assign: if a CONCURRENT start() (impossible while we
    # hold _lock, but this guards the same invariant defensively — e.g. a future caller
    # that mutates _app without going through the lock) already replaced _app with a NEWER
    # instance while we were tearing down THIS one, blindly nulling it here would orphan
    # the new instance's bookkeeping (is_running()/notify() would wrongly read "stopped").
    #
    # ONLY clear on SUCCESS (security review 2026-08-17, finding C): clearing it
    # unconditionally — even when the teardown above just logged a failure — made the
    # module believe a still-possibly-live process was stopped. A caller that then retries
    # ``stop()`` (exactly what the DELETE route's own 502 message tells the operator to do)
    # would hit ``current_app is None`` above and short-circuit to ``True`` on the SECOND
    # call without ever re-attempting real teardown — defeating the whole point of gating
    # the credential wipe on a CONFIRMED stop. Keeping ``_app`` set on failure means a retry
    # genuinely re-attempts teardown against the same object, and ``is_running()`` stays
    # honest (still reports live, since we don't actually know otherwise) in the meantime.
    if ok and _app is current_app:
        _app = None
    log.info(
        "Telegram connector stopped" if ok else "Telegram connector stop completed with errors"
    )
    return ok


def is_running() -> bool:
    """Whether the connector is live — connected (``_app`` up) OR actively trying to connect
    in the background (the retry supervisor task). Used by the hot-reload restart check."""
    return _app is not None or (_task is not None and not _task.done())


async def reload() -> bool:
    """Stop then re-start the connector so a config change (token/allowed/enabled)
    takes effect without a process restart. Honors ONW_DISABLE_TELEGRAM + the store's
    enabled flag via start(), so disabling simply stops the bot.

    Holds ``_lock`` for the WHOLE stop-then-start, not two separate acquisitions — two
    separate ``async with _lock`` blocks (one for stop, one for start) would let another
    entry point's start()/stop()/reload() run in the gap between them, exactly the
    interleaving ``_lock`` exists to rule out.

    Returns ``stopped and started`` (security review 2026-08-17, finding A): the PREVIOUS
    version discarded both ``_stop_locked()``/``_start_locked()``'s results and always
    returned ``None``, so ``reload_best_effort()`` reported success whenever ``reload()``
    didn't raise — which was almost always, since a teardown failure here is reported via
    ``_stop_locked``'s return value, not an exception. That meant a PATCH-triggered reload
    (enable/disable, the AI tool path) could report ``reloaded: true`` even when the live
    bot's teardown genuinely failed; only the DELETE route, which awaits ``stop()``
    directly rather than going through ``reload()``, got the real signal."""
    async with _lock:
        if _telegram_disabled():
            # A non-primary boot never runs the poller; nothing to reload.
            return True
        stopped = await _stop_locked()
        started = await _start_locked()
        return stopped and started


async def reload_best_effort() -> bool:
    """``reload()``, but never raises — the shared shape every caller that must not fail
    its own request over a live-bot hiccup uses (a bad token, a network blip): the config
    write has already landed by the time this runs, only the process's in-memory bot needs
    to catch up, and a full restart would pick up the change regardless. Used by both
    ``PUT /api/telegram/config`` and the generic connector enable/disable/remove routes.
    Returns whether the reload actually completed — ``reload()``'s own result, propagated
    directly (not just "no exception raised"; see ``reload()``'s docstring)."""
    try:
        return await reload()
    except Exception:
        log.exception("Telegram hot-reload failed (non-fatal)")
        return False
