"""
APScheduler wrapper — starts background jobs on FastAPI startup.

Jobs:
  - price_check      : every N minutes (from watchlist config)
  - intel_scan       : every N hours (from watchlist config)
  - watchlist_sync   : daily + shortly after boot — reconciles the auto-managed
                       "My holdings" watchlist against owned positions

Call reschedule(config) to update intervals at runtime (e.g. when Lucius changes config).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# IntervalTrigger doesn't fire until one full interval has elapsed. The price check is cheap
# (just price fetches) and has no manual trigger, so without a kick-start its freshness chip
# reads "awaiting first scan" until the first interval lands — and in dev, `--reload` resets
# that timer on every code change, so it can stay unfired indefinitely. Fire it almost
# immediately on startup so freshness is stamped right away (a stable server then re-stamps it
# every interval, so the "Prices Xm ago" chip never sits hours-stale just because the server
# restarted). A few seconds of settle keeps it off the boot critical path. The intel/news scan
# is intentionally NOT kick-started: it does web searches + AI calls (cost), and already has a
# manual "Scan now" (its first fire is instead anchored to last_scan_at — see _intel_start_date).
_PRICE_CHECK_INITIAL_DELAY_SECONDS = 3
# Notable Price Movement sweep (monitoring.price_movements): 5-min cadence, after price_check.
_PRICE_MOVEMENTS_INTERVAL_SECONDS = 300
_PRICE_MOVEMENTS_INITIAL_DELAY_SECONDS = 90
# Watch signals (services.watch_signals): deterministic feed stories for watched-not-held tickers.
# One batched quote per run (3-min cache), no LLM/search — cheap enough for a 10-min cadence.
_WATCH_SIGNALS_INTERVAL_SECONDS = 600
_WATCH_SIGNALS_INITIAL_DELAY_SECONDS = 45
_WATCH_HISTORY_INTERVAL_SECONDS = 12 * 3600
_WATCH_HISTORY_INITIAL_DELAY_SECONDS = 120

# Same reasoning as the price check's kick-start, for the same reason: an IntervalTrigger
# does not fire until a full interval has elapsed, and 24h of that would leave the auto
# watchlist stale for a day after every restart. A few seconds keeps it off the boot path.
_WATCHLIST_SYNC_INITIAL_DELAY_SECONDS = 8

# The intel scan's next-fire lives only in the in-memory scheduler, so a plain IntervalTrigger
# re-anchors to now+interval on every restart (incl. dev `--reload`). If the server restarts more
# often than the interval, the auto-scan is perpetually deferred and never fires on its own. We
# instead anchor its first fire to the last scan that actually ran (persisted as
# config.last_scan_at by intel_agent). When a restart left it overdue, fire after a short settle
# delay rather than instantly on boot, so a flapping/reloading server doesn't scan on every restart.
_INTEL_OVERDUE_DELAY_SECONDS = 60


def _intel_start_date(news_interval: int, config: dict) -> datetime:
    """First-fire time for the intel scan, anchored to config.last_scan_at so it survives restarts.

    Falls back to now+interval (the plain-trigger default) when no scan has ever run — preserving
    the existing 'no cold-start kick-start' behaviour for fresh installs (intel does paid web+AI
    calls and already has a manual 'Scan now')."""
    now = datetime.now()
    last_raw = config.get("last_scan_at")
    if not last_raw:
        return now + timedelta(seconds=news_interval)
    try:
        due = datetime.fromisoformat(last_raw) + timedelta(seconds=news_interval)
    except (ValueError, TypeError):
        return now + timedelta(seconds=news_interval)
    return max(due, now + timedelta(seconds=_INTEL_OVERDUE_DELAY_SECONDS))


async def _run_price_check() -> None:
    logger.info("scheduler: price_check starting")
    try:
        from backend.monitoring.price_monitor import run_price_check

        fired = await run_price_check()
        logger.info("scheduler: price_check done — %d alert(s) fired", fired)
    except Exception as exc:
        logger.exception("scheduler: price_check error: %s", exc)


async def _run_intel_scan() -> None:
    logger.info("scheduler: intel_scan starting")
    try:
        from backend.monitoring.intel_agent import run_intel_scan

        created = await run_intel_scan()
        logger.info("scheduler: intel_scan done — %d signal(s) created", created)
    except Exception as exc:
        logger.exception("scheduler: intel_scan error: %s", exc)


async def _run_reap_batches() -> None:
    """Sweep the batch ledger and apply results for any pending rows.

    Each tick: for every ``pending`` row, poll ``collect_batch``.  Resolved → apply
    results to the owning subsystem (intel only for now) + mark ``done``.  Still pending
    → update ``poll_count`` / ``last_polled_at`` and leave as pending.  Expired → fall
    back to a synchronous retry once, then mark ``done``/``failed``.
    """
    try:
        from backend.core.llm_seam import BatchPending, collect_batch
        from backend.db.batch_ledger_store import BatchLedgerStore

        store = BatchLedgerStore()
        pending = store.pending()
        if not pending:
            return

        logger.info("scheduler: reap_batches — %d pending row(s)", len(pending))

        for row in pending:
            handle = row["handle"]
            task = row.get("task", "")

            if store.is_expired(row):
                logger.warning(
                    "scheduler: reap_batches — batch %s expired (task=%s); running sync fallback",
                    handle,
                    task,
                )
                await _reap_expired_row(row, store)
                continue

            store.mark_polled(handle)
            try:
                result = await collect_batch(handle)
            except Exception as exc:
                logger.error("scheduler: reap_batches — collect failed for %s: %s", handle, exc)
                continue

            if isinstance(result, BatchPending):
                logger.debug("scheduler: reap_batches — %s still pending", handle)
                continue

            if not result:
                logger.warning("scheduler: reap_batches — batch %s empty/failed result", handle)
                store.mark_failed(handle)
                continue

            applied = await _apply_batch_results(task, handle, row, result)
            if applied >= 0:
                store.mark_done(handle)

    except Exception as exc:
        logger.exception("scheduler: reap_batches error: %s", exc)


async def _apply_batch_results(task: str, handle: str, row: dict, result: list) -> int:
    """Route completed batch results to the owning subsystem.  Returns item count or -1 on error.

    ``result`` is the already-collected ``list[BatchResult]`` — do NOT call collect_batch
    again; that would double-record cost and fail on one-shot result streams.
    """
    if task == "intel":
        try:
            from backend.monitoring.intel_agent import apply_intel_batch_results

            count = apply_intel_batch_results(result, handle, row)
            return count
        except Exception as exc:
            logger.error("scheduler: _apply_batch_results intel failed: %s", exc)
            return -1
    logger.warning("scheduler: unknown batch task %r — discarding results", task)
    return 0


async def _reap_expired_row(row: dict, store) -> None:
    """For an expired batch row, attempt a one-time sync fallback via the seam."""
    from backend.core.llm_seam import BatchPending, BatchRequest, collect_batch, submit_batch

    handle = row["handle"]
    task = row.get("task", "")
    metadata = row.get("metadata") or {}

    custom_ids = row.get("custom_ids") or []
    requests = [
        BatchRequest(
            custom_id=cid,
            prompt=metadata.get("prompt", ""),
            system=metadata.get("system", ""),
            model=row.get("model", ""),
            max_tokens=2048,
            source=row.get("source", "batch"),
        )
        for cid in custom_ids
        if metadata.get("prompt")
    ]
    if not requests:
        # Rows that don't carry a prompt (intel rows store only url_to_date —
        # the 10-50KB prompt is deliberately not persisted) cannot be retried;
        # the next scheduled scan regenerates the work. Mark expired honestly
        # instead of submitting an empty batch that pretends to retry.
        logger.info("scheduler: expired batch %s has no retryable prompt — dropping", handle)
        store.mark_expired(handle)
        return

    try:
        fallback_handle = await submit_batch(task, requests)
        result = await collect_batch(fallback_handle)
        if result and not isinstance(result, BatchPending):
            await _apply_batch_results(task, handle, row, result)
            store.mark_done(handle)
        else:
            store.mark_expired(handle)
    except Exception as exc:
        logger.error("scheduler: expired batch sync fallback failed for %s: %s", handle, exc)
        store.mark_failed(handle)


async def _run_price_movements() -> None:
    """Notable Price Movement: observe today's prints for held/watched instruments and narrate a
    just-completed session (monitoring.price_movements). Cheap when nothing closed."""
    try:
        from backend.monitoring.price_movements import run_price_movement_sweep

        landed = await run_price_movement_sweep()
        if landed:
            logger.info("scheduler: price_movements done — %d paragraph(s)", landed)
    except Exception as exc:
        logger.exception("scheduler: price_movements error: %s", exc)


def _schedule_price_movements_job() -> None:
    _scheduler.add_job(
        _run_price_movements,
        trigger=IntervalTrigger(
            seconds=_PRICE_MOVEMENTS_INTERVAL_SECONDS,
            start_date=datetime.now() + timedelta(seconds=_PRICE_MOVEMENTS_INITIAL_DELAY_SECONDS),
        ),
        id="price_movements",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info(
        "scheduler: price_movements scheduled — every %ds", _PRICE_MOVEMENTS_INTERVAL_SECONDS
    )


async def _run_watch_signals() -> None:
    try:
        from backend.monitoring.watch_analyst import explain_material
        from backend.monitoring.watch_reader import read_stories
        from backend.services.watch_signals import emit_watch_signals, gather_context

        # Sync + DB/network bound — off the shared loop (core.loop_watch names blockers). ONE
        # context (one quote batch) feeds both the stories and the explained alerts.
        ctx = await asyncio.to_thread(gather_context)
        writes = await asyncio.to_thread(lambda: emit_watch_signals(ctx=ctx))
        if writes:
            logger.info("scheduler: watch_signals done — %d story write(s)", writes)
        explained = await explain_material(ctx)
        if explained:
            logger.info("scheduler: watch_signals — %d explained alert(s)", explained)
        read = await read_stories(ctx)
        if read:
            logger.info("scheduler: watch_signals — %d Lucius read(s)", read)
    except Exception as exc:
        logger.exception("scheduler: watch_signals error: %s", exc)


async def _run_watch_history() -> None:
    try:
        from backend.services.prices import backfill_watched_instruments

        results = await asyncio.to_thread(backfill_watched_instruments)
        if results:
            logger.info("scheduler: watch_history — %d instrument(s) topped up", len(results))
    except Exception as exc:
        logger.exception("scheduler: watch_history error: %s", exc)


def _schedule_watch_signals_job() -> None:
    _scheduler.add_job(
        _run_watch_signals,
        trigger=IntervalTrigger(
            seconds=_WATCH_SIGNALS_INTERVAL_SECONDS,
            start_date=datetime.now() + timedelta(seconds=_WATCH_SIGNALS_INITIAL_DELAY_SECONDS),
        ),
        id="watch_signals",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("scheduler: watch_signals scheduled — every %ds", _WATCH_SIGNALS_INTERVAL_SECONDS)
    # History for watched names (the watch rules' "usual move" / beta / streaks read it).
    _scheduler.add_job(
        _run_watch_history,
        trigger=IntervalTrigger(
            seconds=_WATCH_HISTORY_INTERVAL_SECONDS,
            start_date=datetime.now() + timedelta(seconds=_WATCH_HISTORY_INITIAL_DELAY_SECONDS),
        ),
        id="watch_history",
        replace_existing=True,
        misfire_grace_time=600,
    )


async def _run_analysis_sweep() -> None:
    try:
        from backend.monitoring.analyst import run_analysis_sweep

        count = await run_analysis_sweep()
        if count:
            logger.info("scheduler: analysis_sweep done — %d analyzed", count)
    except Exception as exc:
        logger.exception("scheduler: analysis_sweep error: %s", exc)


async def _run_refresh_dividend_meta() -> None:
    """Daily refresh of cached dividend calendar metadata (ADR 0020). enrich_dividend_meta is
    sync + network (its own thread pool), so it runs off the event loop. Best-effort."""
    try:
        import asyncio

        from backend.services.prices import enrich_dividend_meta

        result = await asyncio.to_thread(enrich_dividend_meta)
        logger.info("scheduler: refresh_dividend_meta done — %s", result)
    except Exception as exc:
        logger.exception("scheduler: refresh_dividend_meta error: %s", exc)


async def _run_daily_backup() -> None:
    """Daily full-data backup (services/backup.py) — a disaster-recovery snapshot
    independent of the AI-turn checkpoint system. shutil.copytree is sync + disk I/O,
    so it runs off the event loop. No-ops when the backup_enabled instance flag is off."""
    try:
        import asyncio

        from backend.services.backup import run_backup

        result = await asyncio.to_thread(run_backup)
        if result.get("ok"):
            logger.info("scheduler: daily_backup done — %s", result)
        elif result.get("skipped"):
            logger.debug("scheduler: daily_backup skipped — %s", result["skipped"])
        else:
            logger.error("scheduler: daily_backup failed — %s", result.get("error"))
    except Exception as exc:
        logger.exception("scheduler: daily_backup error: %s", exc)


async def _run_rss_poll_fast() -> None:
    try:
        from backend.monitoring.rss_poller import run_rss_poll

        count = await run_rss_poll("fast")
        logger.debug("scheduler: rss_poll_fast done — %d feed target(s) fetched", count)
    except Exception as exc:
        logger.exception("scheduler: rss_poll_fast error: %s", exc)


async def _run_rss_poll_slow() -> None:
    try:
        from backend.monitoring.rss_poller import run_rss_poll

        count = await run_rss_poll("slow")
        logger.debug("scheduler: rss_poll_slow done — %d feed target(s) fetched", count)
    except Exception as exc:
        logger.exception("scheduler: rss_poll_slow error: %s", exc)


async def _run_rss_poll_hot() -> None:
    try:
        from backend.monitoring.rss_poller import run_rss_poll

        count = await run_rss_poll("hot")
        logger.debug("scheduler: rss_poll_hot done — %d source(s) fetched", count)
    except Exception as exc:
        logger.exception("scheduler: rss_poll_hot error: %s", exc)


async def _run_automations() -> None:
    logger.info("scheduler: automations tick starting")
    try:
        from backend.services.automations.runs import run_due_automations

        result = await run_due_automations()
        if result.get("ran"):
            logger.info("scheduler: automations done — %s", result)
    except Exception as exc:
        logger.exception("scheduler: due automations error: %s", exc)

    from backend.services.automations import events as automation_events

    try:
        replayed = automation_events.replay_automation_event_deliveries()
        if replayed:
            logger.info("scheduler: replayed %d Automation event deliveries", replayed)
    except Exception as exc:
        logger.exception("scheduler: Automation event replay error: %s", exc)

    for name, observe in (
        ("balance", automation_events.observe_balance_events),
        ("price", automation_events.observe_price_events),
    ):
        try:
            observe()
        except Exception as exc:
            logger.exception("scheduler: automation %s observation error: %s", name, exc)
    try:
        drained = await automation_events.drain_automation_events()
        if drained:
            logger.info("scheduler: automation events drained — %s", drained)
    except Exception as exc:
        logger.exception("scheduler: automation event drain error: %s", exc)


def _schedule_automations_job() -> None:
    """Register the single 60-second Automation scheduler tick."""
    if not _scheduler:
        return
    _scheduler.add_job(
        _run_automations,
        trigger=IntervalTrigger(seconds=60),
        id="automations",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("scheduler: automations scheduled — every 60s")


def _get_config() -> dict:
    try:
        from backend.api.watchlist import read_watchlist

        return read_watchlist().get("config", {})
    except Exception:
        return {}


# news_freshness → (interval_seconds | None). None = don't register rss_poll_fast.
_FRESHNESS_SECONDS: dict[str, int | None] = {"fast": 300, "balanced": 900, "off": None}


def _schedule_rss_fast_job(config: dict) -> None:
    """Register or remove rss_poll_fast based on news_freshness config key.

    'fast' (default) → 300s; 'balanced' → 900s; 'off' → remove the job entirely.
    The slow poll (rss_poll_slow) is always registered when monitoring is enabled.
    """
    if not _scheduler:
        return
    freshness = config.get("news_freshness", "fast")
    interval = _FRESHNESS_SECONDS.get(freshness, 300)
    if interval is None:
        job = _scheduler.get_job("rss_poll_fast")
        if job:
            job.remove()
            logger.info("scheduler: rss_poll_fast removed (news_freshness=off)")
        return
    _scheduler.add_job(
        _run_rss_poll_fast,
        trigger=IntervalTrigger(seconds=interval),
        id="rss_poll_fast",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info(
        "scheduler: rss_poll_fast scheduled — every %ds (freshness=%s)", interval, freshness
    )


_HOT_INTERVAL_SECONDS = 90  # within the RFC's 60-120s hot-cadence band


def _schedule_rss_hot_job(config: dict) -> None:
    """Register or remove rss_poll_hot (INT5) — gated on the SAME news_freshness=='off' switch
    as rss_poll_fast, but at a FIXED cadence: the hot tier's value is being near-realtime for
    disclosures/scoops, not a user-tunable dial like fast/balanced/off."""
    if not _scheduler:
        return
    if config.get("news_freshness", "fast") == "off":
        job = _scheduler.get_job("rss_poll_hot")
        if job:
            job.remove()
            logger.info("scheduler: rss_poll_hot removed (news_freshness=off)")
        return
    _scheduler.add_job(
        _run_rss_poll_hot,
        trigger=IntervalTrigger(seconds=_HOT_INTERVAL_SECONDS),
        id="rss_poll_hot",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("scheduler: rss_poll_hot scheduled — every %ds", _HOT_INTERVAL_SECONDS)


async def _run_watchlist_sync() -> None:
    """Reconcile the auto-managed "My holdings" watchlist against owned positions.

    This job exists so that RECONCILING is not something a READ does. Until 2026-08-23 the
    only things that called ``sync_from_holdings`` were two REST endpoints and the
    ``get_watchlist`` AI tool — so the read WAS the reconciler, and a tool named `get_` wrote
    to a data domain every time the model asked what was on the list. The endpoints still
    sync (a screen you open should be right the moment it paints).

    Later the same day, ``core.holdings_change`` started notifying
    ``services.watchlist.reconcile_from_holdings_change`` immediately after any real
    `positions` row write (a trade, an import, an equity vest, a chat-driven add/remove) —
    see that module and ``db.position_store``'s ``_notify_holdings_changed``. This job is now
    the BACKSTOP for everything that trigger doesn't reach: a server-down window (a restore
    or an import run elsewhere while nothing was listening), or any future write path that
    doesn't funnel through ``PositionStore``. No reader has to write either way.

    Cheap and idempotent: it compares owned tickers against current membership and touches
    the store only when they differ.
    """
    logger.info("scheduler: watchlist_sync starting")
    try:
        from backend.db.watchlist_store import WatchlistStore
        from backend.services.positions import hydrated as read_rows

        added = await asyncio.to_thread(lambda: WatchlistStore().sync_from_holdings(read_rows()))
        logger.info("scheduler: watchlist_sync done — %d ticker(s) added", len(added or []))
    except Exception as exc:
        logger.exception("scheduler: watchlist_sync error: %s", exc)


async def _run_ynab_sync() -> None:
    """Tick of the two-way YNAB sync: runs it when its schedule says it is due (and none is
    running). Cheap when it is not — one small state read."""
    try:
        from backend.services import ynab_sync

        if await asyncio.to_thread(ynab_sync.run_if_due):
            logger.info("scheduler: ynab_sync ran")
    except Exception as exc:
        logger.exception("scheduler: ynab_sync error: %s", exc)


def _outward_jobs_allowed() -> bool:
    """False on a QA/isolated run (ONW_QA_MODE) unless it opts in with ONW_QA_RUN_SCHEDULES=1.

    The jobs this gates call models, market/news APIs or notification channels on a timer:
    the owner's Automations, price checks and alerts, AI news/analysis scans, RSS and the
    dividend refresh. A QA run is often a clone of the owner's REAL book
    (clone_data_for_qa) with a model configured — found 2026-10-09: a clone booted with the
    owner's 13 active Automations and monitoring on, fired a price alert within seconds, and
    would have spent the owner's model budget on AI scans. Same rule as the boot-time price
    backfill and the CLI probe ("QA runs must not hit external APIs / spend a subscription").
    The data is untouched — an Automation still shows active with its next run."""
    from backend.core.config import get_settings

    if not get_settings().onw_qa_mode:
        return True
    return os.environ.get("ONW_QA_RUN_SCHEDULES") == "1"


def start() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    config = _get_config()
    price_interval = max(10, int(config.get("price_check_interval_seconds", 60)))
    news_interval = max(30, int(config.get("news_scan_interval_seconds", 14400)))
    outward = _outward_jobs_allowed()
    enabled = config.get("enabled", True) and outward
    if not outward:
        logger.info(
            "scheduler: QA mode — automations, monitoring and other jobs that call "
            "models or outside services are not scheduled (ONW_QA_RUN_SCHEDULES=1 opts in)"
        )

    _scheduler = AsyncIOScheduler()

    if enabled:
        _scheduler.add_job(
            _run_price_check,
            trigger=IntervalTrigger(
                seconds=price_interval,
                start_date=datetime.now() + timedelta(seconds=_PRICE_CHECK_INITIAL_DELAY_SECONDS),
            ),
            id="price_check",
            replace_existing=True,
            misfire_grace_time=60,
        )
        intel_start = _intel_start_date(news_interval, config)
        _scheduler.add_job(
            _run_intel_scan,
            trigger=IntervalTrigger(seconds=news_interval, start_date=intel_start),
            id="intel_scan",
            replace_existing=True,
            misfire_grace_time=300,
        )
        _schedule_price_movements_job()
        _schedule_watch_signals_job()
        logger.info(
            "scheduler: started — price every %ds (first in %ds), intel every %ds (first at %s)",
            price_interval,
            _PRICE_CHECK_INITIAL_DELAY_SECONDS,
            news_interval,
            intel_start.isoformat(timespec="seconds"),
        )
    else:
        logger.info("scheduler: monitoring disabled — jobs not scheduled")

    # Scheduled tasks (reminders + recurring reviews) tick independently of the monitoring
    # `enabled` flag (P2-SK2) — but not on a QA run (see _outward_jobs_allowed).
    if outward:
        _schedule_automations_job()

    # Two-way YNAB sync — one tick a minute; the schedule itself (15 min … daily, or manual
    # only) lives in the sync's state, so changing it needs no re-registration here.
    if outward:
        _scheduler.add_job(
            _run_ynab_sync,
            trigger=IntervalTrigger(seconds=60),
            id="two_way_budget_sync",  # not a provider ingest poll (see test_scheduler_no_ingest_job)
            replace_existing=True,
            misfire_grace_time=120,
            max_instances=1,
        )
        logger.info("scheduler: ynab_sync scheduled — checked every 60s")

    # Batch reap sweep — polls pending batch ledger rows and applies results.
    # Runs every 5 minutes; no-op when the ledger is empty.
    _scheduler.add_job(
        _run_reap_batches,
        trigger=IntervalTrigger(seconds=300),
        id="reap_batches",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("scheduler: reap_batches scheduled — every 300s")

    # Dividend calendar metadata refresh (ADR 0020) — daily; dividends move quarterly so this
    # is generous. Independent of the monitoring `enabled` flag; cheap no-op without yahoo
    # instruments. Feeds the Income tab's Upcoming-payments calendar from cache (never page-load).
    if outward:
        _scheduler.add_job(
            _run_refresh_dividend_meta,
            trigger=CronTrigger(hour=9, minute=5),
            id="refresh_dividend_meta",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduler: refresh_dividend_meta scheduled — daily at 09:05 local")

    # NO pending-approval TTL sweep. There WAS one here (RFC 2026-07-22 §4.3/§9, every 300s
    # against a 15-minute TTL) and it was the bug: it deleted the approval record under a card
    # still on screen, so the ✓ came back "this approval is no longer pending" while the UI had
    # already painted it approved. An approval is a question put to a human and waits until it is
    # answered or superseded — ADR 2026-07-30-approvals-never-expire. Do not re-add a clock here.

    # Full-data disaster-recovery backup — daily at a quiet off-peak hour. Scheduled
    # unconditionally (like refresh_dividend_meta above); the job itself no-ops when the
    # backup_enabled instance flag is off, so toggling it in Settings takes effect on the
    # next fire without a scheduler restart.
    _scheduler.add_job(
        _run_daily_backup,
        trigger=CronTrigger(hour=3, minute=30),
        id="daily_backup",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("scheduler: daily_backup scheduled — daily at 03:30 local")

    # Auto-managed "My holdings" watchlist reconciliation (see _run_watchlist_sync). Daily,
    # plus a kick-start a few seconds after boot: holdings can change while the server is
    # down (a restore, an import run elsewhere), and the model reads this list without
    # necessarily any screen having been opened first. Unconditional — it is not monitoring,
    # it is data hygiene, and it is a no-op when nothing differs.
    _scheduler.add_job(
        _run_watchlist_sync,
        trigger=IntervalTrigger(
            hours=24,
            start_date=datetime.now() + timedelta(seconds=_WATCHLIST_SYNC_INITIAL_DELAY_SECONDS),
        ),
        id="watchlist_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "scheduler: watchlist_sync scheduled — every 24h (first in %ds)",
        _WATCHLIST_SYNC_INITIAL_DELAY_SECONDS,
    )

    if enabled:
        _scheduler.add_job(
            _run_analysis_sweep,
            trigger=IntervalTrigger(seconds=60),
            id="analysis_sweep",
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("scheduler: analysis_sweep scheduled — every 60s")
        _schedule_rss_fast_job(config)
        _scheduler.add_job(
            _run_rss_poll_slow,
            trigger=IntervalTrigger(seconds=1800),
            id="rss_poll_slow",
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("scheduler: rss_poll_slow scheduled — every 1800s")
        _schedule_rss_hot_job(config)

    _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler: stopped")


def reschedule(config: dict) -> None:
    """Hot-reload job intervals from updated config."""
    global _scheduler
    if not _scheduler:
        return

    price_interval = max(10, int(config.get("price_check_interval_seconds", 60)))
    news_interval = max(30, int(config.get("news_scan_interval_seconds", 14400)))
    enabled = config.get("enabled", True) and _outward_jobs_allowed()

    if enabled:
        _scheduler.add_job(
            _run_price_check,
            trigger=IntervalTrigger(
                seconds=price_interval,
                start_date=datetime.now() + timedelta(seconds=_PRICE_CHECK_INITIAL_DELAY_SECONDS),
            ),
            id="price_check",
            replace_existing=True,
            misfire_grace_time=60,
        )
        intel_start = _intel_start_date(news_interval, config)
        _scheduler.add_job(
            _run_intel_scan,
            trigger=IntervalTrigger(seconds=news_interval, start_date=intel_start),
            id="intel_scan",
            replace_existing=True,
            misfire_grace_time=300,
        )
        _schedule_price_movements_job()
        _schedule_watch_signals_job()
        logger.info(
            "scheduler: rescheduled — price every %ds (first in %ds), intel every %ds (first at %s)",
            price_interval,
            _PRICE_CHECK_INITIAL_DELAY_SECONDS,
            news_interval,
            intel_start.isoformat(timespec="seconds"),
        )
    else:
        for job_id in (
            "price_check",
            "price_movements",
            "watch_signals",
            "watch_history",
            "intel_scan",
            "analysis_sweep",
            "rss_poll_fast",
            "rss_poll_slow",
            "rss_poll_hot",
        ):
            job = _scheduler.get_job(job_id)
            if job:
                job.remove()
        logger.info("scheduler: monitoring disabled — jobs removed")

    if enabled:
        _scheduler.add_job(
            _run_analysis_sweep,
            trigger=IntervalTrigger(seconds=60),
            id="analysis_sweep",
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("scheduler: analysis_sweep rescheduled — every 60s")
        _schedule_rss_fast_job(config)
        _scheduler.add_job(
            _run_rss_poll_slow,
            trigger=IntervalTrigger(seconds=1800),
            id="rss_poll_slow",
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("scheduler: rss_poll_slow rescheduled — every 1800s")
        _schedule_rss_hot_job(config)
