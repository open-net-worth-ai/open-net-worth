"""What the LLM bridge is made of: the two aliases' models, the limits it enforces, and the record
it hands the runner for each call. No server — ``llm_bridge`` is the server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.ai.models.base import AIProvider
from backend.ai.models.pricing import approximate_price_rates, list_price_rates
from backend.ai.trading_agents import openai_shape as shape
from backend.core.errors import ConfigurationError, ModelUnavailableError
from backend.core.local_endpoint import is_local_base_url
from backend.services.trading_agents.settings import ResolvedModels

SOURCE = "trading_agents"
ALIAS_DEEP = "onw-deep"
ALIAS_QUICK = "onw-quick"
ALIASES = (ALIAS_DEEP, ALIAS_QUICK)
PLAN_INCLUDED = "plan_included"
KIND_BUDGET, KIND_CALLS = "budget", "calls"
# What a call may cost at most, before it is made: the request's bytes read as 3 per token, and
# the reply as long as the provider could ever write — never the length the worker asks for, which
# is untrusted. A model with no known price is reserved at the dearest rates there are, except on
# this machine's own endpoint, where it is reserved at nothing.
OUTPUT_CEILING_TOKENS = {"anthropic": 32_000}  # what ``AnthropicProvider.stream_chat`` allows
_DEFAULT_OUTPUT_CEILING_TOKENS = (
    64_000  # a CLI cannot cap its reply; a vendor's own limit may be higher
)
_BYTES_PER_TOKEN = 3
_UNPRICED_RATES = (15.0, 75.0)
# A dated snapshot priced as its undated model may cost more: its reservation is doubled.
_APPROXIMATE_MARGIN = 2.0
_FREE_RATES = (0.0, 0.0)


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
        """Whether a call on it is held to the dollar limits: everything but a subscription. A
        local endpoint is metered too — it is not free to the caps just for being on loopback (a
        proxy there may forward to a paid API and report a cost) — but an unknown price on it is
        allowed rather than refused (``unpriced_allowed``)."""
        return not self.plan_included

    @property
    def unpriced_allowed(self) -> bool:
        """A model with no known price may run only on a truly loopback endpoint."""
        return is_local_base_url(self.base_url)

    @property
    def output_ceiling_tokens(self) -> int:
        return OUTPUT_CEILING_TOKENS.get(self.transport, _DEFAULT_OUTPUT_CEILING_TOKENS)

    def worst_case_usd(self, body_bytes: int) -> float:
        if not self.metered:
            return 0.0
        rates, margin = list_price_rates(self.model, self.base_url), 1.0
        if rates is None:
            approximate = approximate_price_rates(self.model, self.base_url)
            if approximate is not None:
                rates, margin = approximate[0], _APPROXIMATE_MARGIN
        if rates is None:
            rates = _FREE_RATES if self.unpriced_allowed else _UNPRICED_RATES
        input_tokens = body_bytes / _BYTES_PER_TOKEN
        return (
            margin * (input_tokens * rates[0] + self.output_ceiling_tokens * rates[1]) / 1_000_000
        )

    def is_unpriced(self, usage: shape.Usage) -> bool:
        """A call that cost money nobody can name: no subscription, no local endpoint, no price in
        the table and none reported by the endpoint. (A provider emits ``0.0``, never nothing, for
        such a call — so a missing figure is not the test.)"""
        if usage.cost_basis == PLAN_INCLUDED or not self.metered or usage.cost_reported:
            return False
        if self.unpriced_allowed:
            return False
        return (
            list_price_rates(self.model, self.base_url) is None
            and approximate_price_rates(self.model, self.base_url) is None
        )


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


def resolve_bridge_models(models: ResolvedModels) -> dict[str, BridgeModel]:
    """The two aliases' providers, built once for the run (a config change mid-run is not
    picked up). A provider the owner turned off raises ``ModelUnavailableError`` — never a
    reroute to the instance default."""
    from backend.ai.assistant import resolve_model_context
    from backend.db.model_config_store import provider_label_for

    out: dict[str, BridgeModel] = {}
    tiers = (
        (ALIAS_DEEP, models.deep, models.deep_plan_included),
        (ALIAS_QUICK, models.quick, models.quick_plan_included),
    )
    for alias, ref, plan_included in tiers:
        try:
            context = resolve_model_context(ref)
        except ConfigurationError as exc:
            raise ModelUnavailableError(exc.message or str(exc), model_ref=ref) from exc
        if context.provider_key != ref.partition(":")[0]:
            raise ModelUnavailableError(f"{ref!r} is not a usable AI model.", model_ref=ref)
        label = provider_label_for(context.transport, context.base_url, context.provider_key)
        out[alias] = BridgeModel(
            alias,
            ref,
            context.model,
            label,
            context.transport,
            context.provider,
            base_url=context.base_url,
            plan_included=plan_included,
        )
    return out
