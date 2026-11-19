"""TradingAgents spend: what a run is expected to cost, and whether the owner's caps let it start.

``check_can_start`` is the one entry point the runner and the API call before a run is created. The
per-run cap and the call cap are enforced live by the runner while the run goes (``run_cap_reached``
and ``calls_cap_reached`` are its two tests); the monthly cap is checked here, once, against the
estimate. The app-wide AI budget (``AppConfigStore``) is not enforced — a separate, advisory figure.

Prices are not read here: they live in ``ai.models.pricing``, a layer above, so the caller hands in
``rate_for`` (a bare model id -> ``(input, output)`` USD per million tokens, or ``None`` when the
model has no price).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import timezone
from typing import Callable, Optional, Sequence

from backend.core.errors import BudgetExceededError
from backend.core.user_time import user_now
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.trading_agents import runs
from backend.services.trading_agents.settings import CLI_TRANSPORTS, ResolvedModels, Settings

RateFor = Callable[[str], Optional[tuple[float, float]]]

HISTORY_SIZE = 10
MIN_HISTORY = 3
BASIS_HISTORY = "history"
BASIS_STATIC = "static"
# Assumed tokens of one model call, by tier (spec section 10.4).
_QUICK_TOKENS = (6_000, 800)
_DEEP_TOKENS = (12_000, 1_500)
# The Research Manager and the Portfolio Manager are the two deep-tier nodes of a run.
_DEEP_CALLS = 2
_HISTORY_LOOKBACK = 200


@dataclass(frozen=True)
class Estimate:
    """What a run is expected to cost. ``median_usd`` is the metered part only: calls on a CLI
    subscription are plan-included and add nothing. ``priced`` is False when a metered model has
    no known price, so the figure understates."""

    median_usd: float
    low_usd: float
    high_usd: float
    calls: int
    basis: str
    sample_size: int
    plan_included: bool
    priced: bool

    def as_dict(self) -> dict:
        return {
            "estimate_usd": self.median_usd,
            "low_usd": self.low_usd,
            "high_usd": self.high_usd,
            "calls": self.calls,
            "basis": self.basis,
            "sample_size": self.sample_size,
            "plan_included": self.plan_included,
            "priced": self.priced,
        }


def config_snapshot(cfg: Settings, models: ResolvedModels) -> dict:
    """The ``config_snapshot`` a run is created with: the settings that decide its cost, and
    what past runs are matched on. Models and transports only, never a key."""
    return {
        **models.as_dict(),
        "rounds": {
            "debate": cfg.max_debate_rounds,
            "risk": cfg.max_risk_rounds,
            "tools": cfg.max_tool_rounds,
        },
    }


def static_calls(cfg: Settings, analysts: int) -> int:
    """Model calls of one run: each analyst's tool rounds and wrap-up, the bull/bear debate, the
    research manager, the trader, the three-way risk debate and the portfolio manager."""
    return (
        analysts * (cfg.max_tool_rounds + 1)
        + 2 * cfg.max_debate_rounds
        + 1
        + 1
        + 3 * cfg.max_risk_rounds
        + 1
    )


def _ref_model(ref: str) -> str:
    return ref.partition(":")[2]


def _tier_cost(rates: Optional[tuple[float, float]], calls: int, tokens: tuple[int, int]) -> float:
    if rates is None:
        return 0.0
    return calls * (tokens[0] * rates[0] + tokens[1] * rates[1]) / 1_000_000


def _static(
    cfg: Settings, models: ResolvedModels, analysts: int, rate_for: RateFor
) -> tuple[float, int, bool]:
    """``(usd, calls, priced)`` from the assumed tokens per call, metered tiers only."""
    calls = static_calls(cfg, analysts)
    tiers = (
        (models.deep_transport, models.deep, _DEEP_CALLS, _DEEP_TOKENS),
        (models.quick_transport, models.quick, calls - _DEEP_CALLS, _QUICK_TOKENS),
    )
    usd, priced = 0.0, True
    for transport, ref, tier_calls, tokens in tiers:
        if transport in CLI_TRANSPORTS:
            continue
        rates = rate_for(_ref_model(ref))
        priced = priced and rates is not None
        usd += _tier_cost(rates, tier_calls, tokens)
    return round(usd, 6), calls, priced


def _signature(snapshot: dict, analysts: Sequence[str]) -> tuple:
    rounds = snapshot.get("rounds") or {}
    return (
        snapshot.get("deep"),
        snapshot.get("quick"),
        len(analysts),
        tuple(sorted(rounds.items())),
    )


def _history(snapshot: dict, analysts: Sequence[str]) -> list[dict]:
    """The last ``HISTORY_SIZE`` finished runs that share the models, analyst count and rounds."""
    wanted = _signature(snapshot, analysts)
    rows = TradingAgentsRunStore().cost_rows(statuses=[runs.DONE], limit=_HISTORY_LOOKBACK)
    same = [r for r in rows if _signature(r["config_snapshot"], r["analysts"]) == wanted]
    return same[:HISTORY_SIZE]


def _band(values: list[float]) -> tuple[float, float, float]:
    """``(median, p10, p90)``."""
    cuts = statistics.quantiles(values, n=10, method="inclusive")
    return statistics.median(values), cuts[0], cuts[-1]


def estimate(
    cfg: Settings, models: ResolvedModels, analysts: Sequence[str], rate_for: RateFor
) -> Estimate:
    """The expected cost of a run with these models, analysts and rounds. The median and p10-p90
    of the last ten matching finished runs once there are ``MIN_HISTORY``, else the static
    heuristic. A run on CLI subscriptions only is plan-included: no dollars, a call count."""
    snapshot = config_snapshot(cfg, models)
    past = _history(snapshot, analysts)
    usd, calls, priced = _static(cfg, models, len(analysts), rate_for)
    plan_included = {models.deep_transport, models.quick_transport} <= CLI_TRANSPORTS
    if len(past) >= MIN_HISTORY:
        median, low, high = _band([float(r["cost_usd"]) for r in past])
        usd_band = (median, low, high)
        calls = round(statistics.median(r["llm_calls"] for r in past))
        basis = BASIS_HISTORY
    else:
        usd_band, basis = (usd, usd, usd), BASIS_STATIC
    if plan_included:
        usd_band = (0.0, 0.0, 0.0)
    median, low, high = (round(v, 6) for v in usd_band)
    return Estimate(
        median_usd=median,
        low_usd=low,
        high_usd=high,
        calls=calls,
        basis=basis,
        sample_size=len(past) if basis == BASIS_HISTORY else 0,
        plan_included=plan_included,
        priced=priced or plan_included or basis == BASIS_HISTORY,
    )


def month_start_utc() -> str:
    """The first instant of this month in the owner's zone, as UTC ISO (the form ``created_at``
    is stored in, so it compares as text)."""
    start = user_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def month_spend() -> float:
    """Metered USD of every run created this month, whatever became of it."""
    rows = TradingAgentsRunStore().cost_rows(since=month_start_utc())
    return round(sum(float(r["cost_usd"]) for r in rows), 6)


def check_can_start(
    cfg: Settings, models: ResolvedModels, analysts: Sequence[str], rate_for: RateFor
) -> Estimate:
    """Refuse a run the monthly cap cannot afford; otherwise return its estimate (stored on the
    run as ``estimate_usd``). Raises ``FeatureDisabledError`` with the flag off and
    ``BudgetExceededError`` (the numbers in the message and context) over the cap. A run on
    CLI subscriptions only is never blocked by the dollar cap."""
    trading_agents.require_flag()
    expected = estimate(cfg, models, analysts, rate_for)
    if expected.plan_included:
        return expected
    spent = month_spend()
    cap = cfg.monthly_cap_usd
    if spent + expected.median_usd > cap:
        raise BudgetExceededError(
            f"This run is expected to cost about ${expected.median_usd:.2f}, and ${spent:.2f} of "
            f"the ${cap:.2f} monthly TradingAgents cap is already spent this month.",
            month_spend_usd=spent,
            estimate_usd=expected.median_usd,
            monthly_cap_usd=cap,
        )
    return expected


def run_cap_reached(spent_usd: float, cap_usd: float) -> bool:
    """Whether a run's metered spend has reached its per-run cap (the runner stops it)."""
    return spent_usd >= cap_usd


def calls_cap_reached(calls: int, max_calls: int) -> bool:
    """Whether a run has made its allowed number of model calls (the runner stops it)."""
    return calls >= max_calls
