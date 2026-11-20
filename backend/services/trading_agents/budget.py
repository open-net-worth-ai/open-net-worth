"""TradingAgents spend: what a run is expected to cost, and whether the owner's caps let it start.

``check_can_start`` is the one entry point the runner and the API call before a run is created. The
per-run cap and the call cap are enforced live by the bridge while the run goes, and the monthly
cap's remainder at the run's start becomes one more live limit (``run_limit_usd``); here the
monthly cap is checked once, against the estimates of every run still queued or going plus this
one. What is metered is decided by the provider's own billing, never by a transport's name: a CLI
on a stored API key is billed, and a model with no known price is refused (it cannot be capped),
unless its endpoint is a loopback one. The app-wide AI budget (``AppConfigStore``) is not enforced — a
separate, advisory figure.

Prices are not read here: they live in ``ai.models.pricing``, a layer above, so the caller hands in
``rate_for`` (a ``"<provider_key>:<model>"`` ref -> a ``Rate`` of USD per million tokens, or ``None``
when the price table has no entry for the model on that provider's endpoint).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import timezone
from typing import Callable, NamedTuple, Optional, Sequence

from backend.core.errors import BudgetExceededError, ModelUnavailableError
from backend.core.user_time import user_now
from backend.db.trading_agents_run_store import TradingAgentsRunStore
from backend.services import trading_agents
from backend.services.trading_agents import runs
from backend.services.trading_agents.settings import ResolvedModels, Settings


class Rate(NamedTuple):
    """USD per million tokens. ``priced_as`` names the model a dated snapshot was priced as when the
    table has no row of its own for it: an approximation, disclosed and reserved with a margin."""

    input: float
    output: float
    priced_as: Optional[str] = None


RateFor = Callable[[str], Optional[Rate]]

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
    unpriced_models: tuple[str, ...] = ()
    approximate_prices: tuple[tuple[str, str], ...] = ()

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
            "unpriced_models": list(self.unpriced_models),
            "approximate_prices": [
                {"model": ref, "priced_as": priced_as} for ref, priced_as in self.approximate_prices
            ],
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


def _tier_cost(rates: Optional[Rate], calls: int, tokens: tuple[int, int]) -> float:
    if rates is None:
        return 0.0
    return calls * (tokens[0] * rates[0] + tokens[1] * rates[1]) / 1_000_000


def _static(
    cfg: Settings, models: ResolvedModels, analysts: int, rate_for: RateFor
) -> tuple[float, int, tuple[str, ...], tuple[tuple[str, str], ...]]:
    """``(usd, calls, unpriced models, approximate prices)`` from the assumed tokens per call, metered tiers only: a
    plan-included tier costs nothing. A local tier with no known price costs nothing and is never
    unpriced (an endpoint on loopback is allowed to be); one whose model has a price — a proxy
    serving a paid model — is estimated like any other."""
    calls = static_calls(cfg, analysts)
    tiers = (
        (models.deep, models.deep_plan_included, models.deep_local, _DEEP_CALLS, _DEEP_TOKENS),
        (
            models.quick,
            models.quick_plan_included,
            models.quick_local,
            calls - _DEEP_CALLS,
            _QUICK_TOKENS,
        ),
    )
    usd, unpriced, approximate = 0.0, [], []
    for ref, plan, local, tier_calls, tokens in tiers:
        if plan:
            continue
        rates = rate_for(ref)
        if rates is None and not local and ref not in unpriced:
            unpriced.append(ref)
        if rates is not None and rates.priced_as and (ref, rates.priced_as) not in approximate:
            approximate.append((ref, rates.priced_as))
        usd += _tier_cost(rates, tier_calls, tokens)
    return round(usd, 6), calls, tuple(unpriced), tuple(approximate)


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
    usd, calls, unpriced, approximate = _static(cfg, models, len(analysts), rate_for)
    plan_included = models.plan_included
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
        priced=not unpriced or basis == BASIS_HISTORY,
        unpriced_models=() if basis == BASIS_HISTORY else unpriced,
        approximate_prices=() if basis == BASIS_HISTORY else approximate,
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


def month_committed() -> float:
    """What this month's runs have cost or are expected to: a finished run its actual cost, a
    queued or running one the larger of its cost so far and its estimate."""
    total = 0.0
    for row in TradingAgentsRunStore().cost_rows(since=month_start_utc()):
        spent = float(row["cost_usd"])
        pending = row["status"] not in runs.TERMINAL
        total += max(spent, float(row["estimate_usd"] or 0.0)) if pending else spent
    return round(total, 6)


def run_limit_usd(cfg: Settings, committed_elsewhere: float) -> float:
    """The most one run's metered spend may reach: its per-run cap, or what is left of the monthly
    cap after ``committed_elsewhere`` when that is less."""
    return max(0.0, min(cfg.per_run_cap_usd, cfg.monthly_cap_usd - committed_elsewhere))


def refuse_unpriced(expected: Estimate) -> None:
    """A metered model with no known price cannot be capped, so no run starts on it."""
    if expected.unpriced_models:
        names = ", ".join(expected.unpriced_models)
        raise ModelUnavailableError(
            f"{names} has no known price (the price table has no entry for it), so a run on it "
            "could not be held to a spend cap. Choose a model the price table knows (for an "
            "OpenRouter model, one of its listed models), refresh the table with "
            "`make refresh-prices`, or use a local model.",
            model_ref=expected.unpriced_models[0],
        )


def check_can_start(
    cfg: Settings, models: ResolvedModels, analysts: Sequence[str], rate_for: RateFor
) -> Estimate:
    """Refuse a run the caps cannot afford; otherwise return its estimate (stored on the run as
    ``estimate_usd``). Raises ``FeatureDisabledError`` with the flag off, ``ModelUnavailableError``
    for a metered model with no price and ``BudgetExceededError`` (the numbers in the message and
    context) when this month's committed spend plus the estimate passes the monthly cap — a cap
    of 0 forbids every run that is not entirely on CLI subscriptions."""
    trading_agents.require_flag()
    expected = estimate(cfg, models, analysts, rate_for)
    if expected.plan_included:
        return expected
    refuse_unpriced(expected)
    committed = month_committed()
    cap = cfg.monthly_cap_usd
    if cap <= 0 or committed + expected.median_usd > cap:
        raise BudgetExceededError(
            f"This run is expected to cost about ${expected.median_usd:.2f}, and ${committed:.2f} of "
            f"the ${cap:.2f} monthly TradingAgents cap is already spent or promised to other "
            "runs this month.",
            month_spend_usd=committed,
            estimate_usd=expected.median_usd,
            monthly_cap_usd=cap,
        )
    return expected
