"""The Claude model ids the app offers come from ONE list, and every one of them has a price.

Scar (2026-11-11): the ids lived in six hand-kept lists. Haiku stayed on 4.5 in all of them
after 5.5 shipped, the Claude Code setup hints sat a generation behind (opus-4-8, sonnet-5,
fable-5), and no current model had a price row, so each was costed at the $3/$15 default —
Haiku 5.5 at 30x its price.
"""

from __future__ import annotations

import pytest

from backend.ai.models import pricing
from backend.db.model_config_store import (
    CLAUDE_CLI_ALIASES,
    CURRENT_CLAUDE_MODELS,
    PROVIDER_CATALOG,
    PROVIDER_HINTS,
)


def _catalog_hints(key: str) -> list[str]:
    return next(e["model_hints"] for e in PROVIDER_CATALOG if e["key"] == key)


@pytest.mark.parametrize("model", CURRENT_CLAUDE_MODELS)
def test_every_offered_claude_model_has_a_pinned_price(model):
    matched = [p for p, _i, _o in pricing._ANTHROPIC_PRICING if model.startswith(p)]
    assert matched, f"{model} has no row in _ANTHROPIC_PRICING and would cost the default"


def test_current_generation_prices():
    assert pricing.list_price_rates("claude-opus-5-5") == (4.00, 20.00)
    assert pricing.list_price_rates("claude-sonnet-5-5") == (2.00, 10.00)
    assert pricing.list_price_rates("claude-haiku-5-5") == (0.10, 0.50)
    assert pricing.list_price_rates("claude-fable-5-1") == (10.00, 50.00)
    # The 5-5 rows must win over the bare 5 rows (ordering rule).
    assert pricing.list_price_rates("claude-opus-5") == (5.00, 25.00)


def test_every_claude_list_is_the_one_list():
    from backend.api import models as api

    assert api._ANTHROPIC_FALLBACK == list(CURRENT_CLAUDE_MODELS)
    assert api._CLAUDE_CLI_MODELS == [*CLAUDE_CLI_ALIASES, *CURRENT_CLAUDE_MODELS]
    assert PROVIDER_HINTS["anthropic"]["model_hints"] == list(CURRENT_CLAUDE_MODELS)
    assert _catalog_hints("anthropic") == list(CURRENT_CLAUDE_MODELS)
    assert _catalog_hints("claude_cli") == [*CLAUDE_CLI_ALIASES, *CURRENT_CLAUDE_MODELS]
