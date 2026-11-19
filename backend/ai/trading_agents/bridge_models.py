"""What the LLM bridge is made of: the two aliases' models, the limits it enforces, and the record
it hands the runner for each call. No server and no I/O — ``llm_bridge`` is the server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.ai.models.base import AIProvider
from backend.ai.models.pricing import list_price_rates
from backend.ai.trading_agents import openai_shape as shape
from backend.core.local_endpoint import is_local_base_url

SOURCE = "trading_agents"
ALIAS_DEEP = "onw-deep"
ALIAS_QUICK = "onw-quick"
ALIASES = (ALIAS_DEEP, ALIAS_QUICK)
PLAN_INCLUDED = "plan_included"
KIND_BUDGET, KIND_CALLS = "budget", "calls"
# What a call may cost at most, before it is made: the request's bytes read as 3 per token, and
# the reply as long as the request allows (else this many tokens). A model with no known price is
# reserved at the dearest rates there are.
DEFAULT_MAX_OUTPUT_TOKENS = 8192
_BYTES_PER_TOKEN = 3
_UNPRICED_RATES = (15.0, 75.0)


@dataclass(frozen=True)
class BridgeModel:
    """One alias's resolved model: the provider object and how its calls are labelled."""

    alias: str
    ref: str
    model: str
    provider_label: str
    transport: str
    provider: AIProvider
    base_url: str = ""
    plan_included: bool = False

    @property
    def metered(self) -> bool:
        """Whether a call on it can cost money: not on a subscription, not on this machine."""
        return not (self.plan_included or is_local_base_url(self.base_url))

    def worst_case_usd(self, body_bytes: int, max_output_tokens: int) -> float:
        if not self.metered:
            return 0.0
        rates = list_price_rates(self.model, self.base_url) or _UNPRICED_RATES
        input_tokens = body_bytes / _BYTES_PER_TOKEN
        return (input_tokens * rates[0] + max_output_tokens * rates[1]) / 1_000_000

    def is_unpriced(self, usage: shape.Usage) -> bool:
        """A call that cost money nobody can name: no subscription, no local endpoint, no price in
        the table and none reported by the endpoint. (A provider emits ``0.0``, never nothing, for
        such a call — so a missing figure is not the test.)"""
        if usage.cost_basis == PLAN_INCLUDED or not self.metered or usage.cost_reported:
            return False
        return list_price_rates(self.model, self.base_url) is None


@dataclass(frozen=True)
class RunLimits:
    """Hard stops the bridge enforces on its own, so a cap cannot be overshot by the calls that
    were already in flight when the runner noticed it. ``None`` = no limit.
    ``month_remaining_usd`` is what the monthly cap had left when the run started."""

    max_calls: Optional[int] = None
    max_metered_usd: Optional[float] = None
    month_remaining_usd: Optional[float] = None

    def usd_limit(self) -> Optional[tuple[float, str]]:
        """The tighter dollar limit and the sentence that names it."""
        found = []
        if self.max_metered_usd is not None:
            found.append(
                (
                    self.max_metered_usd,
                    f"The run reached its spend cap of ${self.max_metered_usd:.2f}.",
                )
            )
        if self.month_remaining_usd is not None:
            found.append(
                (self.month_remaining_usd, "The run reached what is left of the monthly cap.")
            )
        return min(found, key=lambda item: item[0]) if found else None


@dataclass(frozen=True)
class CallRecord:
    """What one call cost, handed to the runner in-process (never via the worker). A call that
    failed or was cut off is a record with no tokens and ``failed`` set: it still counts as a call."""

    call_id: str
    alias: str
    model_ref: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: Optional[float]
    cost_basis: Optional[str]
    latency_ms: float
    tool_calls: int
    unpriced: bool = False
    failed: bool = False

    @property
    def metered_usd(self) -> float:
        """Spend that is real money: a plan-included call is quota use, not an invoice."""
        return 0.0 if self.cost_basis == PLAN_INCLUDED else float(self.cost_usd or 0.0)
