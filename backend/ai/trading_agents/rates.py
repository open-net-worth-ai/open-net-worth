"""The price of a TradingAgents model as the price table sees it on its provider's endpoint."""

from __future__ import annotations

from typing import Optional

from backend.ai.models.pricing import approximate_price_rates, list_price_rates
from backend.services.trading_agents import settings
from backend.services.trading_agents.budget import Rate


def rate_for_ref(ref: str) -> Optional[Rate]:
    """The price of ``"<provider_key>:<model>"``, or ``None`` when the table has no entry. The
    provider's endpoint is part of the lookup: OpenRouter's rows are keyed by its own prefix, which
    a bare model id never reaches. A dated snapshot the table lacks is priced as the undated model
    and says so (``Rate.priced_as``)."""
    model, base_url = ref.partition(":")[2], settings.provider_base_url(ref)
    exact = list_price_rates(model, base_url)
    if exact is not None:
        return Rate(*exact)
    approximate = approximate_price_rates(model, base_url)
    return Rate(*approximate[0], priced_as=approximate[1]) if approximate else None
