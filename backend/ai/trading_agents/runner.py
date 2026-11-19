"""The TradingAgents runner: the queue in front of the one run that may go at a time.

``submit`` checks everything that can refuse a run (availability, the disclaimer, the queue, the
budget), freezes its inputs — the model references and the exact portfolio context that will be
shared — writes the row through ``apply_run_change("create")`` and queues it. One run goes at a
time; at most ``MAX_PENDING`` (the running one included) wait, the next is a ``TooManyRunsError``.
The run itself is ``worker_session.RunSession``.

Runs are tracked tasks of the server's event loop, never a bare ``create_task``. ``recover()`` marks
what a stopped app left half-done ``interrupted`` (called once at boot). Under ``ONW_QA_MODE``
nothing runs unless a test injected its own worker (``set_worker_cmd_for_tests``).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections import deque
from typing import Callable, Optional

from backend.ai.trading_agents import worker_session
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
from backend.services.trading_agents import context, install, runs, settings, ticker

log = logging.getLogger(__name__)

MAX_PENDING = 3
_CANCEL_WAIT_SECONDS = 15.0
_SHUTDOWN_WAIT_SECONDS = 10.0

_queue: deque[Job] = deque()
_session: Optional[RunSession] = None
_pump: Optional[asyncio.Task] = None
_worker_cmd: Optional[list[str]] = None


def _default_budget_check(
    cfg: settings.Settings, models: settings.ResolvedModels, analysts: list[str]
) -> Optional[float]:
    """The estimate for the run, or ``BudgetExceededError`` when the monthly cap would be passed
    (``services.trading_agents.budget.check_can_start``)."""
    budget = importlib.import_module("backend.services.trading_agents.budget")
    return budget.check_can_start(cfg=cfg, models=models, analysts=analysts)


_budget_check: Callable[..., Optional[float]] = _default_budget_check


def set_worker_cmd_for_tests(command: Optional[list[str]]) -> None:
    """Run ``command`` instead of the venv's worker (``None`` restores it)."""
    global _worker_cmd
    _worker_cmd = list(command) if command else None


def set_budget_check_for_tests(check: Optional[Callable[..., Optional[float]]]) -> None:
    global _budget_check
    _budget_check = check or _default_budget_check


# ── submitting ───────────────────────────────────────────────────────────────────────────


def pending_count() -> int:
    """Runs waiting plus the one going."""
    return len(_queue) + (1 if _session is not None else 0)


def _require_runnable() -> None:
    if _worker_cmd is None and get_settings().onw_qa_mode:
        raise FeatureDisabledError(
            "TradingAgents does not run in a QA process without a test worker.",
            status="qa_mode",
        )


def _snapshot(cfg: settings.Settings, models: settings.ResolvedModels, analysts: list[str]) -> dict:
    return {
        "models": models.as_dict(),
        "analysts": analysts,
        "max_debate_rounds": cfg.max_debate_rounds,
        "max_risk_rounds": cfg.max_risk_rounds,
        "max_tool_rounds": cfg.max_tool_rounds,
        "max_llm_calls": cfg.max_llm_calls,
        "bridge_concurrency": settings.bridge_concurrency(cfg, models),
        "output_language": cfg.output_language,
        "ta_commit": install.TA_COMMIT,
    }


async def submit(
    ticker_symbol: str,
    *,
    trade_date: Optional[str] = None,
    analysts: Optional[list[str]] = None,
    context_level: Optional[str] = None,
    requested_by: str = "user",
    session_id: Optional[str] = None,
) -> str:
    """Queue one analysis and return its run id. Raises the typed error for whatever refuses it:
    ``FeatureDisabledError`` / ``NotInstalledError`` / ``ModelUnavailableError`` (availability, the
    disclaimer, QA mode), ``TooManyRunsError`` (queue full), ``BudgetExceededError`` (monthly cap)."""
    _require_runnable()
    trading_agents.require_ready()
    saved = settings.load()
    if not saved.disclaimer_accepted_at:
        raise FeatureDisabledError(
            "Accept the TradingAgents research-only notice before the first run.",
            status="disclaimer_required",
        )
    if pending_count() >= MAX_PENDING:
        raise TooManyRunsError(
            f"{MAX_PENDING} TradingAgents runs are already waiting. Try again when one finishes."
        )
    cfg = settings.with_context_level(saved, context_level)
    chosen = settings.normalize_analysts(analysts if analysts is not None else list(cfg.analysts))
    models = settings.resolve_models(cfg)
    estimate = _budget_check(cfg=cfg, models=models, analysts=chosen)
    symbol = ticker.normalize(ticker_symbol)
    portfolio, disclosure = context.build(symbol, cfg.context_level)
    row = runs.apply_run_change(
        "create",
        ticker=symbol,
        instrument_id=(ticker.instrument(symbol) or {}).get("id"),
        trade_date=trade_date,
        requested_by=requested_by,
        session_id=session_id,
        analysts=chosen,
        config_snapshot=_snapshot(cfg, models, chosen),
        context_level=cfg.context_level,
        portfolio=portfolio,
        disclosure=disclosure,
        estimate_usd=estimate,
        cap_usd=cfg.per_run_cap_usd,
    )
    _queue.append(
        Job(
            run_id=row["id"],
            ticker=symbol,
            trade_date=str(row["trade_date"]),
            analysts=chosen,
            portfolio=portfolio,
            cfg=cfg,
            models=models,
            cap_usd=cfg.per_run_cap_usd,
        )
    )
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
        _session = RunSession(job, _worker_cmd or worker_session.worker_command())
        try:
            await _session.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one run's bug never stops the queue
            log.exception("trading_agents_runner_failed run=%s", job.run_id)
        finally:
            _session = None


# ── cancelling, recovering, stopping ─────────────────────────────────────────────────────


async def cancel(run_id: str) -> dict:
    """Cancel a queued or running run and return the run as it then is."""
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
