"""What the TradingAgents REST API reads and the owner-initiated actions it fronts: the status
card, an estimate, the context preview, the run list and one run.

Everything but ``status`` raises ``FeatureDisabledError`` while the release flag is off, so a router
never branches on it. The runner's queue length and the price table live in ``ai``, a layer above,
so the caller hands in ``queue_length`` and ``rate_for``.
"""

from __future__ import annotations

from typing import Optional, Sequence

from backend.core.errors import ModelUnavailableError, NotFoundError, ValidationError
from backend.db.trading_agents_config_store import SECRET_KEYS, TradingAgentsConfigStore
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import thesis_judgment, trading_agents
from backend.services.trading_agents import budget, context, install, runs, settings, ticker

MAX_PAGE = 200
# What a list row leaves out: the audited context, the progress log and the reports (the full run has them).
_LIST_OMIT = frozenset({"context_sent", "progress", "reports"})


def _models_status(cfg: settings.Settings) -> Optional[dict]:
    try:
        resolved = settings.resolve_models(cfg)
    except ModelUnavailableError:
        return None  # the reason is in availability().message
    return resolved.as_dict()


def status(queue_length: int) -> dict:
    """The one status card: availability, install state, the two models and their transports, the
    caps, this month's metered spend, the queue and the disclaimer. Never raises for a flag that
    is off: ``availability.status`` says so."""
    available = trading_agents.availability()
    saved = TradingAgentsConfigStore().read()
    cfg = settings.load()
    state = saved["state"]
    return {
        "availability": {"status": available.status, "message": available.message},
        "install": {
            "status": state.get("install_status") or install.ABSENT,
            "version": state.get("installed_version"),
            "error": state.get("install_error"),
        },
        "models": _models_status(cfg) if available.status != trading_agents.FLAG_OFF else None,
        "caps": {
            "per_run_cap_usd": cfg.per_run_cap_usd,
            "monthly_cap_usd": cfg.monthly_cap_usd,
            "max_llm_calls": cfg.max_llm_calls,
        },
        "context_level": cfg.context_level,
        "analysts": list(cfg.analysts),
        "month_spend_usd": budget.month_spend() if trading_agents.flag_on() else 0.0,
        "queue_length": queue_length,
        "disclaimer_accepted": bool(cfg.disclaimer_accepted_at),
    }


def estimate(
    ticker_symbol: str, analysts: Optional[Sequence[str]], rate_for: budget.RateFor
) -> dict:
    """The expected cost of a run of ``ticker_symbol`` with the saved settings (no cap check)."""
    trading_agents.require_flag()
    ticker.normalize(ticker_symbol)
    cfg = settings.load()
    chosen = settings.normalize_analysts(list(analysts) if analysts is not None else cfg.analysts)
    return budget.estimate(cfg, settings.resolve_models(cfg), chosen, rate_for).as_dict()


def context_preview(ticker_symbol: str, context_level: Optional[str]) -> dict:
    """Exactly what a run would store as its ``context_sent`` right now: the ticker, the
    portfolio context and the disclosure."""
    trading_agents.require_flag()
    cfg = settings.with_context_level(settings.load(), context_level)
    symbol = ticker.normalize(ticker_symbol)
    portfolio, disclosure = context.build(symbol, cfg.context_level)
    return {"ticker": symbol, "portfolio": portfolio, "disclosure": disclosure}


def accept_disclaimer() -> dict:
    trading_agents.require_flag()
    settings.accept_disclaimer()
    return {"disclaimer_accepted": True}


def start_install() -> None:
    trading_agents.require_flag()
    install.start_install()


def list_runs(
    *, ticker_symbol: Optional[str], status_filter: Optional[str], limit: int, offset: int
) -> dict:
    trading_agents.require_flag()
    if status_filter is not None and status_filter not in runs.STATUSES:
        raise ValidationError(f"status must be one of {', '.join(sorted(runs.STATUSES))}.")
    symbol = ticker.normalize(ticker_symbol) if ticker_symbol else None
    statuses = [status_filter] if status_filter else None
    store = TradingAgentsRunStore()
    rows = store.list(
        ticker=symbol, statuses=statuses, limit=max(1, min(limit, MAX_PAGE)), offset=max(0, offset)
    )
    return {
        "runs": [{k: v for k, v in row.items() if k not in _LIST_OMIT} for row in rows],
        "total": store.count(ticker=symbol, statuses=statuses),
    }


def get_run(run_id: str) -> dict:
    """The full run; ``thesis_judgment`` is present only while the owner has a thesis on its
    ticker (computed from the price on every read, never stored)."""
    trading_agents.require_flag()
    row = TradingAgentsRunStore().get(run_id)
    if row is None:
        raise NotFoundError(f"No TradingAgents run {run_id!r}.", run_id=run_id)
    judgment = thesis_judgment.compact(thesis_judgment.for_ticker(row["ticker"], None))
    return {**row, "thesis_judgment": judgment} if judgment else row


def get_config() -> dict:
    """The connector's settings as the setup form edits them: every key of ``settings.Settings``
    the owner sets (model refs as saved, ``None`` = default) and only the PRESENCE of each
    data-vendor key — a value is revealed through the connector secret-reveal path alone."""
    trading_agents.require_flag()
    cfg = settings.load()
    held = TradingAgentsConfigStore().secrets()
    return {
        "deep_model": cfg.deep_model,
        "quick_model": cfg.quick_model,
        "analysts": list(cfg.analysts),
        "max_debate_rounds": cfg.max_debate_rounds,
        "max_risk_rounds": cfg.max_risk_rounds,
        "max_tool_rounds": cfg.max_tool_rounds,
        "max_llm_calls": cfg.max_llm_calls,
        "bridge_concurrency": cfg.bridge_concurrency,
        "per_run_cap_usd": cfg.per_run_cap_usd,
        "monthly_cap_usd": cfg.monthly_cap_usd,
        "context_level": cfg.context_level,
        "output_language": cfg.output_language,
        "sec_edgar_user_agent": cfg.sec_edgar_user_agent,
        **{f"has_{key}": key in held for key in SECRET_KEYS},
    }


def update_config(changes: dict) -> dict:
    """Apply a PATCH-shaped settings change and return ``get_config()``. A setting that is
    ``None`` resets to its default; a data-vendor key that is blank keeps the saved one and
    ``None`` clears it. Nothing is written when any part is refused."""
    trading_agents.require_flag()
    secrets = {k: changes[k] for k in SECRET_KEYS if k in changes}
    config = {k: v for k, v in changes.items() if k not in secrets}
    settings.check_model_refs(settings.validate_config(config))
    settings.update(config, secrets=secrets)
    return get_config()


def delete_run(run_id: str) -> None:
    trading_agents.require_flag()
    runs.apply_run_change("delete", run_id=run_id)
    install.remove_run(run_id)
