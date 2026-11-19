"""Provider-agnostic token-cost calculation.

Each provider module calls the appropriate cost function here; ``cost_tracker``
imports ``cost_usd_for_model`` to stay provider-neutral.

Pricing tables: USD per million tokens, (input_rate, output_rate).
  - These hand tables are PINNED overrides — our core/default models plus any
    bleeding-edge ones upstream data lags on — and win over the vendored map.
    Models not in a table fall back to ``price_map.vendored_rates`` (a filtered copy
    of LiteLLM's maintained price data, refreshed via ``make refresh-prices``), so the
    long tail is priced without hand-typing every model. Only when BOTH miss is the
    cost logged as unmeasured (0.0).
  - Longer model-id prefix MUST come before shorter so startswith matching is
    unambiguous (e.g. ``gpt-4o-mini`` before ``gpt-4o``).
  - Rates are *list prices* as published by each provider; actual billed amounts
    may differ with negotiated contracts or promotional credits.
  - As-of date: 2026-06-07.  Refresh when providers announce price changes.

Cache-write billing:
  Anthropic charges different rates depending on the cache TTL of the written block:
    - ephemeral (5 min, default)  = 1.25× input rate
    - 1-hour TTL                  = 2.00× input rate
  The tools+static-system blocks use ttl='1h'; volatile blocks use the 5-min default.
  When the SDK exposes a per-TTL breakdown (usage.cache_creation.ephemeral_5m_input_tokens
  / ephemeral_1h_input_tokens) callers should pass those counts via
  ``cache_creation_5m_tokens`` and ``cache_creation_1h_tokens``.  When the breakdown is
  unavailable the legacy flat ``cache_creation_tokens`` arg is used at 1.25× (safe
  underestimate; cold turns may be charged at 2×).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anthropic pricing
# ---------------------------------------------------------------------------

# (prefix, input_$/Mtok, output_$/Mtok)
# ORDERING RULE: longer / more-specific prefixes MUST come before shorter ones.
# Example: "claude-opus-4-6" before "claude-opus-4" so the cheap 4-6 row is
# matched first and the legacy-priced "claude-opus-4" row only catches genuinely
# unversioned ids (claude-opus-4-0, claude-opus-4-1, ...).
_ANTHROPIC_PRICING: list[tuple[str, float, float]] = [
    # ── Fable 5 / 5.1, Mythos 5 / 5.1 ────────────────────────────────────────
    ("claude-fable-5", 10.00, 50.00),
    ("claude-mythos-5", 10.00, 50.00),
    # ── Opus 5 / Sonnet 5 / Haiku 5 — 5-5 before the bare 5 (ordering rule) ────
    # Without these rows every current model fell through to _ANTHROPIC_DEFAULT ($3/$15):
    # Haiku 5.5 was shown at 30x its price (2026-11-11).
    ("claude-opus-5-5", 4.00, 20.00),
    ("claude-opus-5", 5.00, 25.00),
    ("claude-sonnet-5-5", 2.00, 10.00),
    ("claude-sonnet-5", 2.00, 10.00),
    # Haiku 5.5 doubles past 100K input tokens ($0.50/$2.50); this table is flat, so a
    # long prompt is under-priced — the rate for the common case.
    ("claude-haiku-5-5", 0.10, 0.50),
    # ── Opus 4 — versioned (cheaper) rows BEFORE the legacy catch-all ────────
    ("claude-opus-4-8", 5.00, 25.00),
    ("claude-opus-4-7", 5.00, 25.00),
    ("claude-opus-4-6", 5.00, 25.00),
    ("claude-opus-4", 15.00, 75.00),  # legacy: 4-0 / 4-1 / unversioned
    # ── Sonnet 4 ─────────────────────────────────────────────────────────────
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-sonnet-4", 3.00, 15.00),
    # ── Haiku 4 ──────────────────────────────────────────────────────────────
    ("claude-haiku-4-5", 1.00, 5.00),
    ("claude-haiku-4", 1.00, 5.00),
    # ── Claude 3.x ───────────────────────────────────────────────────────────
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-5-haiku", 0.80, 4.00),
    ("claude-3-opus", 15.00, 75.00),
    ("claude-3-sonnet", 3.00, 15.00),
    ("claude-3-haiku", 0.25, 1.25),
]

# Fallback when no prefix matches — claude-sonnet-4 list price.
_ANTHROPIC_DEFAULT = (3.00, 15.00)

# Cache-write multipliers per TTL tier.
_CACHE_WRITE_5M = 1.25  # ephemeral / default TTL
_CACHE_WRITE_1H = 2.00  # 1-hour TTL (tools block + static system)


def _anthropic_rates(model: str) -> tuple[float, float]:
    for prefix, in_rate, out_rate in _ANTHROPIC_PRICING:
        if model.startswith(prefix):
            return in_rate, out_rate
    return _ANTHROPIC_DEFAULT


def list_price_rates(model: str, base_url: str = "") -> "tuple[float, float] | None":
    """(input_$/Mtok, output_$/Mtok) for ANY known model, or None when unpriceable.

    The routing engine's cost oracle: hand-pinned tables first (claude prefix table,
    then the OpenAI-compatible table), then the vendored LiteLLM map for the long
    tail. Deliberately does NOT apply the local-endpoint $0 rule — locality is the
    caller's concern (the engine prices local candidates as free itself).
    """
    model = (model or "").strip()
    if not model:
        return None
    if model.startswith("claude-"):
        return _anthropic_rates(model)
    rates = _openai_rates(model)
    if rates is not None:
        return rates
    from backend.ai.models.price_map import vendored_rates

    return vendored_rates(model, base_url)


def cost_usd_anthropic(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_5m_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
) -> float:
    """Anthropic cost including prompt-cache write and read.

    Cache write pricing:
      - When ``cache_creation_5m_tokens`` / ``cache_creation_1h_tokens`` are provided
        they are billed at their respective TTL rates (1.25× and 2.00×); the legacy
        ``cache_creation_tokens`` arg is ignored so callers that already extracted the
        breakdown do not double-count.
      - When only the legacy ``cache_creation_tokens`` is non-zero the flat 1.25×
        rate is used (backward-compatible; underestimates cost on cold turns with 1h
        blocks, but never crashes on older SDKs that lack the breakdown).
    Cache read: always 0.10× input rate (TTL does not affect read pricing).
    """
    in_rate, out_rate = _anthropic_rates(model)
    has_breakdown = cache_creation_5m_tokens or cache_creation_1h_tokens
    if has_breakdown:
        write_cost = (
            cache_creation_5m_tokens * in_rate * _CACHE_WRITE_5M
            + cache_creation_1h_tokens * in_rate * _CACHE_WRITE_1H
        )
    else:
        write_cost = cache_creation_tokens * in_rate * _CACHE_WRITE_5M
    return (
        input_tokens * in_rate
        + write_cost
        + cache_read_tokens * in_rate * 0.10
        + output_tokens * out_rate
    ) / 1_000_000


# ---------------------------------------------------------------------------
# OpenAI-compatible pricing
# ---------------------------------------------------------------------------

# (prefix, input_$/Mtok, output_$/Mtok)
# Prices are standard list prices; the provider may apply volume discounts.
# Longer prefixes MUST come before shorter ones — e.g. "gpt-4o-mini" before "gpt-4o".
_OPENAI_PRICING: list[tuple[str, float, float]] = [
    # ── OpenAI ────────────────────────────────────────────────────────────
    # o-series reasoning models
    ("o3-mini", 1.10, 4.40),  # o3-mini (2025)
    ("o3", 10.00, 40.00),  # o3 (2025)
    ("o1-mini", 3.00, 12.00),  # o1-mini
    ("o1-preview", 15.00, 60.00),  # o1-preview
    ("o1", 15.00, 60.00),  # o1
    # GPT-4o family (mini before base so it matches first)
    ("gpt-4o-mini", 0.15, 0.60),  # gpt-4o-mini
    ("gpt-4o", 2.50, 10.00),  # gpt-4o
    # GPT-4.1 family
    ("gpt-4.1-mini", 0.40, 1.60),  # gpt-4.1-mini
    ("gpt-4.1-nano", 0.10, 0.40),  # gpt-4.1-nano
    ("gpt-4.1", 2.00, 8.00),  # gpt-4.1
    # GPT-4-turbo / GPT-4 legacy
    ("gpt-4-turbo", 10.00, 30.00),  # gpt-4-turbo-* variants
    ("gpt-4", 30.00, 60.00),  # gpt-4 (8k/32k)
    # GPT-3.5
    ("gpt-3.5-turbo", 0.50, 1.50),  # gpt-3.5-turbo
    # ── Google Gemini via OpenAI-compatible endpoint ───────────────────────
    # "gemini-2.5-flash" must come before "gemini-2.5" (more specific)
    ("gemini-2.5-flash", 0.15, 0.60),  # Gemini 2.5 Flash
    ("gemini-2.5-pro", 1.25, 10.00),  # Gemini 2.5 Pro
    ("gemini-2.5", 1.25, 10.00),  # Gemini 2.5 (generic)
    ("gemini-2.0-flash", 0.10, 0.40),  # Gemini 2.0 Flash
    ("gemini-1.5-flash", 0.075, 0.30),  # Gemini 1.5 Flash
    ("gemini-1.5-pro", 1.25, 5.00),  # Gemini 1.5 Pro
    # ── xAI Grok via its OpenAI-compatible endpoint (api.x.ai) ──────────────
    # Verified against https://docs.x.ai/developers/models (2026-07); refresh on change.
    # Order: more-specific "grok-4.X" ids before any shorter "grok-4" prefix.
    ("grok-4.5", 2.00, 6.00),  # Grok 4.5 (flagship)
    ("grok-4.3", 1.25, 2.50),  # Grok 4.3
    ("grok-4.20", 1.25, 2.50),  # Grok 4.20 (reasoning / non-reasoning / multi-agent)
    ("grok-build", 1.00, 2.00),  # Grok Build (code)
    # ── Zhipu GLM via its OpenAI-compatible endpoint (api.z.ai) ─────────────
    # Verified against https://docs.z.ai/guides/overview/pricing (2026-07); refresh on change.
    # Order: -flashx before -flash; -airx/-x/-flash/-air before bare glm-4.5;
    # glm-5.2/5.1/5-turbo before bare glm-5.
    ("glm-5.2", 1.40, 4.40),  # GLM-5.2 (flagship)
    ("glm-5.1", 1.40, 4.40),  # GLM-5.1
    ("glm-5-turbo", 1.20, 4.00),  # GLM-5-Turbo
    ("glm-5", 1.00, 3.20),  # GLM-5
    ("glm-4.7-flashx", 0.07, 0.40),  # GLM-4.7-FlashX
    ("glm-4.7-flash", 0.0, 0.0),  # GLM-4.7-Flash (free)
    ("glm-4.7", 0.60, 2.20),  # GLM-4.7
    ("glm-4.6", 0.60, 2.20),  # GLM-4.6
    ("glm-4.5-airx", 1.10, 4.50),  # GLM-4.5-AirX
    ("glm-4.5-air", 0.20, 1.10),  # GLM-4.5-Air
    ("glm-4.5-x", 2.20, 8.90),  # GLM-4.5-X
    ("glm-4.5-flash", 0.0, 0.0),  # GLM-4.5-Flash (free)
    ("glm-4.5", 0.60, 2.20),  # GLM-4.5
    ("glm-4-32b", 0.10, 0.10),  # GLM-4-32B-0414-128K
]

# ---------------------------------------------------------------------------
# Cached-input discount — PER MODEL FAMILY, not global
# ---------------------------------------------------------------------------
#
# A single global 0.50 was wrong: the cached-input multiple is set by each
# provider independently and ranges from 0.10x to 0.50x.  Calibrated against
# OpenRouter records carrying a PROVIDER-REPORTED cost (usage.cost), where the
# base (input, output) rates are pinned exactly and the only free variable is the
# cache-read multiple:
#
#   x-ai/grok-4.5        7 records → 0.25000 on every one   (matches the published
#                                    $0.50 cached vs $2.00 input on docs.x.ai)
#   moonshotai/kimi-k3  15 records → 0.10000 on every one
#
# Two providers, two different multiples, both exact to 5 decimals — which is the
# proof that this cannot be one constant.  See tests/test_cache_read_discount.py,
# which re-derives both from the recorded figures so the calibration cannot drift.
#
# ORDERING RULE: as with the rate tables, longer/more-specific prefixes first.
_OAI_CACHE_READ_DISCOUNTS: list[tuple[str, float]] = [
    # ── xAI Grok (docs.x.ai/developers/models, 2026-07) ──────────────────────
    # grok-4.5 is MEASURED at 0.25 against provider-reported cost; the cheaper
    # 4.3/4.20 line publishes $0.20 cached vs $1.25 input, and grok-build
    # $0.20 vs $1.00.  xAI charges nothing for cache writes.
    ("grok-4.5", 0.25),
    ("grok-4.3", 0.16),
    ("grok-4.20", 0.16),
    ("grok-build", 0.20),
    # ── Anthropic reached over an OpenAI-compatible proxy (OpenRouter) ───────
    # Same 0.10x cache-read as the native path (see cost_usd_anthropic).
    ("claude-", 0.10),
    # ── Moonshot Kimi (MEASURED, 15 provider-reported records) ──────────────
    ("kimi-", 0.10),
    # ── Google Gemini — 75% off implicit cache hits (Google's own launch post;
    # the current docs page states only "cost savings" without the figure).
    ("gemini-", 0.25),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    # NOTE: these families are DELISTED from OpenAI's current pricing page, so
    # the ratios below are their last published list prices and are no longer
    # re-verifiable at source.  OpenRouter's live prompt-caching page still
    # describes OpenAI reads as "0.25x or 0.50x", which corroborates the split.
    # Refresh on the next price sweep; the current gpt-5.x line is 0.10x but is
    # absent from _OPENAI_PRICING entirely, so it is deliberately not listed here.
    ("gpt-4o", 0.50),
    ("gpt-4.1", 0.25),
    ("o1", 0.25),
    ("o3", 0.25),
    ("o4", 0.25),
]

# Fallback for a model we have no cache-read figure for.  Deliberately the
# LEAST generous multiple in play (0.50): an unknown model should OVER-state
# spend rather than under-state it, so a gap surfaces as a suspiciously high
# bill rather than a silently flattering one.  Notably GLM lands here on
# purpose — docs.z.ai's prose says "usually 50% of standard price" while the
# GLM-5.2 rate card implies ~0.19x, and we do not guess between the two.
_OAI_CACHE_READ_DEFAULT = 0.50


def _normalize_model_id(model: str) -> str:
    """Strip aggregator decoration so a namespaced id matches the same prefixes.

    OpenRouter namespaces every id by vendor (``x-ai/grok-4.5``,
    ``anthropic/claude-sonnet-5``) and marks variants with a leading ``~``
    (``~anthropic/claude-haiku-latest``).  The cache-read table is keyed on the
    bare model family, so both forms resolve to one entry.

    Scoped to the discount lookup ONLY — the rate tables keep matching raw ids so
    an aggregator's models still fall through to ``vendored_rates`` exactly as
    before (and OpenRouter reports its real cost anyway, so the estimate is
    rarely the figure that lands).
    """
    m = (model or "").strip().lstrip("~").lower()
    return m.rsplit("/", 1)[-1] if "/" in m else m


def cache_read_discount(model: str) -> float:
    """Cached input tokens cost this multiple of the normal input rate."""
    normalized = _normalize_model_id(model)
    for prefix, discount in _OAI_CACHE_READ_DISCOUNTS:
        if normalized.startswith(prefix):
            return discount
    return _OAI_CACHE_READ_DEFAULT


def _is_local_base_url(base_url: str) -> bool:
    """Return True if base_url points at a local process (Ollama/LM Studio/vLLM)."""
    url = base_url.lower().strip()
    return (
        url.startswith("http://localhost")
        or url.startswith("https://localhost")
        or url.startswith("http://127.")
        or url.startswith("https://127.")
        or url.startswith("http://[::1]")
        or url.startswith("http://0.0.0.0")
    )


def _openai_rates(model: str) -> tuple[float, float] | None:
    """Return (input_rate, output_rate) or None if the model is unknown."""
    for prefix, in_rate, out_rate in _OPENAI_PRICING:
        if model.startswith(prefix):
            return in_rate, out_rate
    return None


def cost_usd_openai(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    base_url: str = "",
) -> float:
    """Compute cost for an OpenAI-compatible call.

    Local endpoints (localhost/127.x — Ollama, LM Studio, vLLM) always return
    0.0 because they are self-hosted and cost nothing per token.

    Unknown model IDs return 0.0 with a WARNING log so the operator knows
    the cost is unmeasured rather than genuinely zero.

    ``cached_tokens`` comes from ``response.usage.prompt_tokens_details.cached_tokens``
    when the endpoint returns it; those tokens are charged at that model's own
    cache-read multiple (``cache_read_discount`` — 0.10x-0.50x depending on the
    provider, NOT a single global figure).
    """
    if _is_local_base_url(base_url):
        return 0.0

    # Hand-pinned tables win (tested + bleeding-edge). Fall back to the vendored
    # LiteLLM price map for the long tail before giving up as unmeasured.
    rates = _openai_rates(model)
    if rates is None:
        from backend.ai.models.price_map import vendored_rates

        rates = vendored_rates(model, base_url)
    if rates is None:
        log.warning(
            "cost_usd_openai: unknown model %r — cost_usd=0.0 (unmeasured, not free)",
            model,
        )
        return 0.0

    in_rate, out_rate = rates
    # ``input_tokens`` from the OpenAI API is the TOTAL prompt token count;
    # ``cached_tokens`` is the subset that was served from cache (cheaper).
    # Uncached portion = input_tokens - cached_tokens.
    uncached_input = max(0, input_tokens - cached_tokens)
    return (
        uncached_input * in_rate
        + cached_tokens * in_rate * cache_read_discount(model)
        + output_tokens * out_rate
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Provider-agnostic entry point (used by cost_tracker)
# ---------------------------------------------------------------------------


def cost_usd_for_model(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    base_url: str = "",
    cache_creation_5m_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
) -> float:
    """Best-effort cost estimate regardless of which provider emitted the usage.

    For Anthropic models the prompt-cache write/read multipliers apply; for all
    other models the OpenAI-compatible formula is used (cached_tokens =
    cache_read_tokens if > 0, ignoring creation tokens which OpenAI doesn't have).
    """
    if model.startswith("claude-"):
        return cost_usd_anthropic(
            model,
            input_tokens,
            output_tokens,
            cache_creation_tokens,
            cache_read_tokens,
            cache_creation_5m_tokens,
            cache_creation_1h_tokens,
        )
    # For non-Anthropic models map the generic cached field → cached_tokens.
    cached = cache_read_tokens  # OpenAI reports cache hits in usage.prompt_tokens_details
    return cost_usd_openai(model, input_tokens, output_tokens, cached, base_url)
