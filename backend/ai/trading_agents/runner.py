"""The TradingAgents runner: the queue in front of the one run that may go at a time.

``plan`` checks everything that can refuse a run (availability, the disclaimer, the queue, the
budget) and freezes its inputs — the model references and the exact portfolio context that will be
shared — without writing anything; the tool's approval card and ``submit`` both use it, so there
is one refusal chain. ``submit`` writes the row through ``apply_run_change("create")`` and queues it.
All of that is blocking work (the book, the price table, the database), so it runs in a thread. One run goes at a
time; at most ``MAX_PENDING`` (the running one included) wait, the next is a ``TooManyRunsError``.
The run itself is ``worker_session.RunSession``.

Runs are tracked tasks of the server's event loop, never a bare ``create_task``. ``recover()`` marks
what a stopped app left half-done ``interrupted`` (called once at boot). Under ``ONW_QA_MODE``
nothing runs unless a test injected its own worker (``set_worker_cmd_for_tests``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from backend.ai.trading_agents import billing, worker_session
from backend.ai.trading_agents.llm_bridge import STOP_BOUND_SECONDS
from backend.ai.trading_agents.rates import rate_for_ref
from backend.ai.trading_agents.worker_session import Job, RunSession
from backend.core.config import get_settings
from backend.core.errors import (
    ConflictError,
    FeatureDisabledError,
    OpenNetWorthError,
    TooManyRunsError,
)
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.trading_agents import budget, context, install, runs, settings, ticker

log = logging.getLogger(__name__)

MAX_PENDING = 3
_CANCEL_WAIT_SECONDS = 15.0
# The bridge may take its whole stop bound to give up on a call; the shutdown waits for that.
_SHUTDOWN_WAIT_SECONDS = STOP_BOUND_SECONDS + 5.0

_queue: deque[Job] = deque()
_submitting = 0
_session: Optional[RunSession] = None
_pump: Optional[asyncio.Task] = None
_worker_cmd: Optional[list[str]] = None


def _default_budget_check(
    cfg: settings.Settings, models: settings.ResolvedModels, analysts: list[str]
) -> budget.Estimate:
    """The run's estimate, or ``BudgetExceededError`` when the monthly cap would be passed."""
    return budget.check_can_start(cfg, models, analysts, rate_for_ref)


_budget_check: Callable[..., budget.Estimate] = _default_budget_check


def set_worker_cmd_for_tests(command: Optional[list[str]]) -> None:
    """Run ``command`` instead of the venv's worker (``None`` restores it)."""
    global _worker_cmd
    _worker_cmd = list(command) if command else None


def set_budget_check_for_tests(check: Optional[Callable[..., budget.Estimate]]) -> None:
    global _budget_check
    _budget_check = check or _default_budget_check


# ── submitting ───────────────────────────────────────────────────────────────────────────


def pending_count() -> int:
    """Runs waiting plus the one going (and any being written into the queue)."""
    return len(_queue) + (1 if _session is not None else 0) + _submitting


install.set_busy_probe(pending_count)
settings.set_cli_billing_probe(billing.cli_is_plan_included)


def _require_runnable() -> None:
    if _worker_cmd is None and get_settings().onw_qa_mode:
        raise FeatureDisabledError(
            "TradingAgents does not run in a QA process without a test worker.",
            status="qa_mode",
        )


def _too_many() -> TooManyRunsError:
    return TooManyRunsError(
        f"{MAX_PENDING} TradingAgents runs are already waiting. Try again when one finishes."
    )


@dataclass(frozen=True)
class Plan:
    """A run as it would be started now: what was refused or allowed, and what it would cost
    and share. Nothing is written."""

    symbol: str
    trade_date: str
    analysts: list[str]
    cfg: settings.Settings
    models: settings.ResolvedModels
    estimate: budget.Estimate
    portfolio: Optional[dict]
    disclosure: dict


def plan(
    ticker_symbol: str,
    *,
    trade_date: Optional[str] = None,
    analysts: Optional[list[str]] = None,
    context_level: Optional[str] = None,
    seat_held: bool = False,
) -> Plan:
    """The one chain that can refuse a start, in order, raising the typed error:
    ``FeatureDisabledError`` / ``NotInstalledError`` / ``ModelUnavailableError`` (availability, the
    disclaimer, QA mode, a model with no price), ``TooManyRunsError`` (queue full),
    ``BudgetExceededError`` (monthly cap). ``seat_held``: the caller already took its place in the
    queue (``submit`` does, before it calls this). Blocking: call it in a thread."""
    _require_runnable()
    trading_agents.require_ready()
    saved = settings.load()
    if not saved.disclaimer_accepted_at:
        raise FeatureDisabledError(
            "Accept the TradingAgents research-only notice on its connector page before the "
            "first run.",
            status="disclaimer_required",
        )
    if not seat_held and pending_count() >= MAX_PENDING:
        raise _too_many()
    cfg = settings.with_context_level(saved, context_level)
    chosen = settings.normalize_analysts(analysts if analysts is not None else list(cfg.analysts))
    models = settings.resolve_models(cfg)
    estimate = _budget_check(cfg=cfg, models=models, analysts=chosen)
    symbol = ticker.normalize(ticker_symbol)
    when = runs.normalize_trade_date(trade_date)
    portfolio, disclosure = context.build(symbol, cfg.context_level)
    return Plan(symbol, when, chosen, cfg, models, estimate, portfolio, disclosure)


def _create_row(prepared: Plan, requested_by: str, session_id: Optional[str]) -> dict:
    return runs.apply_run_change(
        "create",
        ticker=prepared.symbol,
        instrument_id=(ticker.instrument(prepared.symbol) or {}).get("id"),
        trade_date=prepared.trade_date,
        requested_by=requested_by,
        session_id=session_id,
        analysts=prepared.analysts,
        config_snapshot=budget.config_snapshot(prepared.cfg, prepared.models),
        context_level=prepared.cfg.context_level,
        portfolio=prepared.portfolio,
        disclosure=prepared.disclosure,
        estimate_usd=prepared.estimate.median_usd,
        cap_usd=prepared.cfg.per_run_cap_usd,
    )


async def submit(
    ticker_symbol: str,
    *,
    trade_date: Optional[str] = None,
    analysts: Optional[list[str]] = None,
    context_level: Optional[str] = None,
    requested_by: str = "user",
    session_id: Optional[str] = None,
    prepared: Optional[Plan] = None,
) -> str:
    """Queue one analysis and return its run id; raises whatever ``plan`` raises. ``prepared`` is
    a ``Plan`` the caller already made (the tool's, whose inputs then are the plan's, not the
    other arguments). The seat in the queue is taken before any blocking work, so two submits
    cannot both pass the full check."""
    global _submitting
    _submitting += 1
    try:
        if pending_count() > MAX_PENDING:
            raise _too_many()
        if prepared is None:
            prepared = await asyncio.to_thread(
                plan,
                ticker_symbol,
                trade_date=trade_date,
                analysts=analysts,
                context_level=context_level,
                seat_held=True,
            )
        row = await asyncio.to_thread(_create_row, prepared, requested_by, session_id)
        _queue.append(
            Job(
                run_id=row["id"],
                ticker=prepared.symbol,
                trade_date=str(row["trade_date"]),
                analysts=prepared.analysts,
                portfolio=prepared.portfolio,
                cfg=prepared.cfg,
                models=prepared.models,
                cap_usd=prepared.cfg.per_run_cap_usd,
            )
        )
    finally:
        _submitting -= 1
    _ensure_pump()
    return row["id"]


# ── running ──────────────────────────────────────────────────────────────────────────────


def _ensure_pump() -> None:
    global _pump
    if _pump is None or _pump.done():
        _pump = asyncio.get_running_loop().create_task(_drain(), name="trading_agents_runner")


async def _drain() -> None:
    global _session
    while _queue:
        job = _queue.popleft()
        try:
            _session = RunSession(job, _worker_cmd or worker_session.worker_command())
        except Exception:  # noqa: BLE001 — a job that cannot even start fails; the queue goes on
            log.exception("trading_agents_runner_failed run=%s", job.run_id)
            _fail_unstarted(job)
            continue
        try:
            await _session.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one run's bug never stops the queue
            log.exception("trading_agents_runner_failed run=%s", job.run_id)
        finally:
            _session = None


def _fail_unstarted(job: Job) -> None:
    try:
        runs.apply_run_change(
            "fail", run_id=job.run_id, status=runs.FAILED, error="The run could not be started."
        )
    except OpenNetWorthError:
        log.warning("trading_agents_unstarted_not_recorded run=%s", job.run_id)


# ── cancelling, recovering, stopping ─────────────────────────────────────────────────────


async def cancel(run_id: str) -> dict:
    """Cancel a queued or running run and return the run as it then is."""
    trading_agents.require_flag()
    queued = next((j for j in _queue if j.run_id == run_id), None)
    if queued is not None:
        _queue.remove(queued)
        return runs.apply_run_change("cancel", run_id=run_id)
    session = _session
    if session is not None and session.job.run_id == run_id:
        session.stop(worker_session.STOP_CANCEL)
        try:
            await asyncio.wait_for(session.finished.wait(), _CANCEL_WAIT_SECONDS)
        except asyncio.TimeoutError:
            raise ConflictError("The run is still stopping.", run_id=run_id) from None
        return TradingAgentsRunStore().get(run_id) or {}
    return runs.apply_run_change("cancel", run_id=run_id)


def recover() -> int:
    """Boot: a run a stopped app left ``queued`` or ``running`` can never continue — mark it
    ``interrupted`` (and an install that was cut off ``failed``). Returns how many runs."""
    install.recover_interrupted()
    install.remove_all_runs()
    store = TradingAgentsRunStore()
    stale = store.list(statuses=[runs.QUEUED, runs.RUNNING], limit=1000)
    for row in stale:
        try:
            runs.apply_run_change(
                "fail",
                run_id=row["id"],
                status=runs.INTERRUPTED,
                error="The app stopped before the run finished.",
            )
        except OpenNetWorthError:
            log.warning("trading_agents_recover_skipped run=%s", row["id"])
    return len(stale)


async def shutdown() -> None:
    """Stop the queue and the run in flight (it ends ``interrupted``). For the app's shutdown."""
    global _pump
    _queue.clear()
    pump, _pump = _pump, None
    if pump is None or pump.done():
        return
    pump.cancel()
    await asyncio.wait({pump}, timeout=_SHUTDOWN_WAIT_SECONDS)
