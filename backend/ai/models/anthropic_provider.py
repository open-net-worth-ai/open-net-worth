from __future__ import annotations

import logging
from typing import AsyncGenerator, List

import anthropic

from backend.core.cost_context import get_cost_surface

from .base import AIProvider, log_text_cap
from .pricing import cost_usd_anthropic

log = logging.getLogger(__name__)

# Models with the modern thinking surface: adaptive thinking + the GA effort param.
# budget_tokens is REMOVED on Opus 4.7+/Fable 5/Sonnet 5 (the API returns 400) and
# deprecated on Opus 4.6/Sonnet 4.6 — these all take {"type": "adaptive"}. Older models
# (Haiku 4.5, Sonnet 4.5, Opus 4.5 and earlier) keep the legacy budget_tokens path.
# xhigh (Anthropic's "Extra" tier) arrived with Opus 4.7 — the full low/medium/high/
# xhigh/max ladder is a NATIVE effort value on Fable 5 / Mythos 5 / Opus 4.8 / 4.7 /
# Sonnet 5; Opus 4.6 / Sonnet 4.6 take low/medium/high/max (no xhigh). Verified against
# the Claude API reference (claude-api skill, 2026-07-18).
_XHIGH_CAPABLE_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
)
_ADAPTIVE_NO_XHIGH_PREFIXES = ("claude-opus-4-6", "claude-sonnet-4-6")
_ADAPTIVE_THINKING_PREFIXES = _XHIGH_CAPABLE_PREFIXES + _ADAPTIVE_NO_XHIGH_PREFIXES
_LEGACY_THINKING_BUDGET = {
    "low": 0,
    "medium": 0,
    "high": 4096,
    "xhigh": 8192,
    "max": 16000,
}


def _thinking_kwargs(model: str, effort: str) -> dict:
    """Per-model thinking/effort request params.

    Modern models: output_config.effort natively — the full low/medium/high/xhigh/max
    ladder on xhigh-capable models, low/medium/high/max on Opus 4.6 / Sonnet 4.6 (xhigh
    folds to high there). Adaptive thinking on high/xhigh/max. Never send
    {"type": "disabled"} — Fable 5 rejects it; omitting the thinking param is the
    supported off-state. Legacy models: unchanged budget_tokens behavior, no effort param.
    """
    if model.startswith(_ADAPTIVE_THINKING_PREFIXES):
        xhigh_ok = model.startswith(_XHIGH_CAPABLE_PREFIXES)
        valid = (
            ("low", "medium", "high", "xhigh", "max")
            if xhigh_ok
            else ("low", "medium", "high", "max")
        )
        if effort == "xhigh" and not xhigh_ok:
            effort = "high"  # Opus 4.6 / Sonnet 4.6 have no xhigh tier
        level = effort if effort in valid else "medium"
        kwargs: dict = {"output_config": {"effort": level}}
        if level in ("high", "xhigh", "max"):
            kwargs["thinking"] = {"type": "adaptive"}
        return kwargs
    budget = _LEGACY_THINKING_BUDGET.get(effort, 0)
    if budget > 0:
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    return {}


# 1-hour cache TTL for the STABLE prefix (static system body + tools block).
# These bytes are byte-identical across an entire session AND across sessions, so a
# longer-lived entry survives cold-start / >5min-gap turns — exactly the turns the
# live cost log shows paying the full cache-WRITE premium (39% fresh / 43% write in
# the aggregate). 1h TTL is GA (no beta header). The volatile snapshot + the last
# message stay on the default 5-min TTL: they change every turn, so a longer TTL
# would just pay the 2x write premium for nothing.
_STABLE_CACHE = {"type": "ephemeral", "ttl": "1h"}
_VOLATILE_CACHE = {"type": "ephemeral"}


def _stable_cache_for_surface(surface: str | None) -> dict:
    """Return the cache_control dict for the stable prefix based on the calling surface.

    Scheduled/agent runs fire every 1-4+ hours, so a 1h-TTL write is read at most once
    before expiry — pure 2x write waste.  The 5-min default (1.25x write) still covers
    within-run reads (multi-round tool loops complete in minutes).  Interactive surfaces
    (web, connector:*) are session-local: the gap between turns is seconds to minutes, so
    1h entries are read on every subsequent turn and the 2x write cost is amortised.
    """
    if surface is not None and (surface == "scheduled" or surface.startswith("scheduled")):
        return dict(_VOLATILE_CACHE)  # 5-min default TTL: 1.25x write
    return dict(_STABLE_CACHE)  # 1-hour TTL: 2x write (paid back by reads)


def _split_system_blocks(system: str, stable_cache: dict | None = None) -> list[dict]:
    """Build the system param as cache-segmented text blocks.

    _split_turn_context() passes ``static + CACHE_SPLIT_MARKER + semi_stable`` as
    the system string (the per-turn tail has already been extracted). We split on
    the marker and give each half its own cache breakpoint:

      • static (instruction body, ~33K chars) — stable TTL (1h for interactive,
        5m for scheduled): byte-identical for the life of an instance, so a
        cold or >5-min-gapped turn READs it at 0.1x; scheduled runs use 5m to
        avoid paying the 2x write for an entry that expires before the next run.
      • semi_stable (memory, skills, profile, FIRE, ~30K chars) — 5-min TTL: changes
        only on memory extraction or profile/skill edits (~every few turns), so a
        5-min TTL gives reasonable cache hits without paying 2x writes for nothing.

    The per-turn block (date, portfolio snapshot, FX, ~1-2K chars) is NOT in this
    string — it was extracted by _split_turn_context() and lives in the user message,
    giving a 5-min last-message breakpoint. Total cache breakpoints in play:
    tools@stable → static@stable → semi_stable@5m → last_message@5m = 4 (the API max).
    """
    from backend.ai.prompts import CACHE_SPLIT_MARKER

    _sc = stable_cache if stable_cache is not None else dict(_STABLE_CACHE)
    if CACHE_SPLIT_MARKER in system:
        static, semi_stable = system.split(CACHE_SPLIT_MARKER, 1)
        return [
            {"type": "text", "text": static, "cache_control": _sc},
            {"type": "text", "text": semi_stable, "cache_control": dict(_VOLATILE_CACHE)},
        ]
    # Fallback (no marker): cache the whole string on the stable TTL — it's dominated
    # by the stable instruction body, so the surface-chosen TTL applies even here.
    return [{"type": "text", "text": system, "cache_control": _sc}]


def _cache_last_message(messages: List[dict]) -> List[dict]:
    """
    Mark the final message block with cache_control so the whole conversation
    prefix is cached. In a multi-tool-call turn each model round-trip appends a
    tool_use + tool_result and re-sends everything; without this the entire
    growing history is reprocessed as fresh input every round-trip. With a
    trailing breakpoint, the prior prefix is a cache READ (0.1x) and only the
    new ~2 messages are written — incremental caching across the tool loop and
    across turns. Inputs are not mutated (shallow-copied).
    """
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        if not content:
            return messages
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = new_content
    else:
        return messages
    out[-1] = last
    return out


class AnthropicProvider(AIProvider):
    def __init__(self, api_key: str | None = None):
        # Explicit key > Settings-stored key > env var. The store is the source of
        # truth post write-only-keys; the env fallback keeps env-configured
        # deployments working. Never construct a keyless client silently — that
        # surfaces as an opaque SDK auth error on the first request.
        if not api_key:
            from backend.db.provider_keys import effective_api_key

            api_key = effective_api_key("anthropic")
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def stream_chat(
        self,
        messages: List[dict],
        tools: List[dict],
        system: str,
        model: str,
        effort: str = "medium",
    ) -> AsyncGenerator[dict, None]:
        thinking_kwargs = _thinking_kwargs(model, effort)

        # Choose the stable-prefix TTL based on the calling surface.  Scheduled/agent
        # runs fire every 1-4+ hours and would never read a 1h-TTL entry before expiry,
        # so the 5-min default (1.25x write) is the right choice there.  Interactive
        # turns (web, connector:*) stay on 1h so cold/gapped turns read instead of write.
        surface = get_cost_surface()
        stable_cache = _stable_cache_for_surface(surface)

        # System prompt: cache the stable instruction body at surface-chosen TTL, leave
        # the volatile snapshot (date/portfolio/FX/memory) uncached so it can't bust the prefix.
        system_blocks = _split_system_blocks(system, stable_cache=stable_cache)

        # Mark the last tool definition for caching — the tool set never changes
        # within a session (or across sessions), so it gets the SAME surface-chosen TTL
        # as the static system body: a cold/gapped turn reads it instead of paying the
        # full re-write of the whole schema block.
        cached_tools: list[dict] | None = None
        if tools:
            cached_tools = list(tools)
            cached_tools[-1] = {**cached_tools[-1], "cache_control": stable_cache}

        # Cache the conversation prefix (trailing breakpoint on the last message) so
        # a multi-tool-call turn reads prior messages from cache instead of
        # reprocessing the whole history on every round-trip. The four breakpoints
        # in play: tools@1h (1) + static-system@1h (1) + semi-stable-system@5m (1)
        # + last-message@5m (1) = 4, exactly the API maximum. The two system
        # breakpoints are created in _split_system_blocks; this one is the prefix.
        kwargs: dict = dict(
            model=model,
            max_tokens=32000,  # safety ceiling only — not an effort cap
            system=system_blocks,
            messages=_cache_last_message(messages),
        )
        kwargs.update(thinking_kwargs)
        if cached_tools:
            kwargs["tools"] = cached_tools

        stable_ttl = stable_cache.get("ttl", "5m")
        log.debug(
            "stream_chat  model=%s  effort=%s  surface=%s  stable_ttl=%s  thinking_params=%s  messages=%d",
            model,
            effort,
            surface,
            stable_ttl,
            thinking_kwargs,
            len(messages),
        )

        # Wall-clock timing for the routing engine's latency telemetry: ttft = first
        # visible content, duration = full stream. Monotonic, milliseconds.
        import time as _time

        _t0 = _time.monotonic()
        _ttft_ms: "float | None" = None

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if _ttft_ms is None:
                        _ttft_ms = (_time.monotonic() - _t0) * 1000.0
                    if hasattr(event.delta, "thinking") and event.delta.thinking:
                        yield {"type": "thinking_delta", "text": event.delta.thinking}
                    elif hasattr(event.delta, "text"):
                        yield {"type": "text_delta", "text": event.delta.text}
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        log.debug(
                            "tool_use_start  tool=%s  id=%s",
                            event.content_block.name,
                            event.content_block.id,
                        )
                        yield {
                            "type": "tool_use_start",
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                        }
                elif event.type == "content_block_stop":
                    pass

            final = await stream.get_final_message()
            usage = final.usage
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            # Per-TTL cache-write breakdown (SDK ≥0.43).  Older SDKs expose only the
            # flat ``cache_creation_input_tokens`` total; getattr guards keep them safe.
            _cc = getattr(usage, "cache_creation", None)
            cc_5m = getattr(_cc, "ephemeral_5m_input_tokens", 0) or 0
            cc_1h = getattr(_cc, "ephemeral_1h_input_tokens", 0) or 0
            cost = cost_usd_anthropic(
                model,
                usage.input_tokens,
                usage.output_tokens,
                cache_creation,
                cache_read,
                cc_5m,
                cc_1h,
            )

            log.info(
                "model_turn_done  stop_reason=%s  model=%s  effort=%s  "
                "input=%d  cache_write=%d  cache_read=%d  output=%d  cost_usd=%.5f",
                final.stop_reason,
                model,
                effort,
                usage.input_tokens,
                cache_creation,
                cache_read,
                usage.output_tokens,
                cost,
            )
            if final.stop_reason == "max_tokens":
                log.warning(
                    "max_tokens hit  output_tokens=%d  effort=%s  model=%s — response was cut off",
                    usage.output_tokens,
                    effort,
                    model,
                )

            yield {
                "type": "usage",
                "input_tokens": usage.input_tokens,
                "cache_creation_tokens": cache_creation,
                "cache_creation_5m_tokens": cc_5m,
                "cache_creation_1h_tokens": cc_1h,
                "cache_read_tokens": cache_read,
                "output_tokens": usage.output_tokens,
                "cost_usd": cost,
                "model": model,
                "ttft_ms": _ttft_ms,
                "duration_ms": (_time.monotonic() - _t0) * 1000.0,
            }

            tool_uses = [b for b in final.content if b.type == "tool_use"]
            for tu in tool_uses:
                if not tu.input:
                    log.warning(
                        "tool_use has empty input — likely truncated  "
                        "tool=%s  id=%s  stop_reason=%s",
                        tu.name,
                        tu.id,
                        final.stop_reason,
                    )
                else:
                    log.debug(
                        "tool_use_complete  tool=%s  id=%s  input_keys=%s",
                        tu.name,
                        tu.id,
                        list(tu.input.keys()),
                    )
                yield {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}

            yield {"type": "done", "stop_reason": final.stop_reason}

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,
        effort: str = "",  # noqa: ARG002 - Anthropic reasoning ("thinking") not wired on the sync path yet
    ) -> str:
        # Native structured outputs (GA late-2025): translate the OpenAI-shaped
        # ``response_format`` to Anthropic's ``output_config.format`` (json_schema —
        # Anthropic has NO schemaless json_object mode, so a bare json_object maps to a
        # permissive object schema). Sent below with a graceful fallback (an older SDK
        # without the kwarg, or a model that rejects it, degrades to prompt+parse — the
        # caller still instructs JSON and parses tolerantly).
        if model:
            _model = model
        else:
            from backend.db.model_config_store import ModelConfigStore

            _model = ModelConfigStore().resolved()["model"]
        content: str | list = prompt
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                }
                for img in images
            ] + [{"type": "text", "text": prompt}]
        msg = await self.client.messages.create(
            model=_model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": content}],
        )
        cache_creation = getattr(msg.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(msg.usage, "cache_read_input_tokens", 0) or 0
        _cc = getattr(msg.usage, "cache_creation", None)
        cc_5m = getattr(_cc, "ephemeral_5m_input_tokens", 0) or 0
        cc_1h = getattr(_cc, "ephemeral_1h_input_tokens", 0) or 0
        cost = cost_usd_anthropic(
            _model,
            msg.usage.input_tokens,
            msg.usage.output_tokens,
            cache_creation,
            cache_read,
            cc_5m,
            cc_1h,
        )
        # Owner incident 2026-08-31 (KODK, openai_provider's own equivalent fix): log the
        # actual response text, not just token counts — a call that runs unusually long (or
        # produces an unusually large output_tokens figure) had NOTHING to show what it was
        # actually doing. `thinking` blocks only appear when extended thinking is requested
        # (not currently wired on this sync path — see `effort`'s own noqa above — so normally
        # empty), captured anyway for when that changes. Bounded, logged only — see
        # openai_provider.complete's matching comment for why this isn't in cost_log.
        response_text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        response_thinking = "".join(
            getattr(b, "thinking", "") or ""
            for b in msg.content
            if getattr(b, "type", "") == "thinking"
        )
        _LOG_TEXT_CAP = log_text_cap(source)
        log.info(
            "complete_done  source=%s  model=%s  input=%d  cache_write=%d  cache_read=%d  output=%d  "
            "cost_usd=%.5f  content_chars=%d  thinking_chars=%d\n"
            "--- content (first %d chars) ---\n%s\n"
            "--- thinking (first %d chars) ---\n%s",
            source,
            _model,
            msg.usage.input_tokens,
            cache_creation,
            cache_read,
            msg.usage.output_tokens,
            cost,
            len(response_text),
            len(response_thinking),
            _LOG_TEXT_CAP,
            response_text[:_LOG_TEXT_CAP],
            _LOG_TEXT_CAP,
            response_thinking[:_LOG_TEXT_CAP],
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            _model,
            msg.usage.input_tokens,
            msg.usage.output_tokens,
            cache_creation,
            cache_read,
            cost_usd=cost,
            provider=provider_label_for("anthropic", ""),
        )
        return response_text

    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        effort: str = "",  # noqa: ARG002 - reasoning not wired on this path, matches complete()
    ) -> AsyncGenerator[dict, None]:
        if model:
            _model = model
        else:
            from backend.db.model_config_store import ModelConfigStore

            _model = ModelConfigStore().resolved()["model"]

        async with self.client.messages.stream(
            model=_model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and hasattr(event.delta, "text"):
                    yield {"type": "text_delta", "text": event.delta.text}
            final = await stream.get_final_message()

        usage = final.usage
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        _cc = getattr(usage, "cache_creation", None)
        cc_5m = getattr(_cc, "ephemeral_5m_input_tokens", 0) or 0
        cc_1h = getattr(_cc, "ephemeral_1h_input_tokens", 0) or 0
        cost = cost_usd_anthropic(
            _model,
            usage.input_tokens,
            usage.output_tokens,
            cache_creation,
            cache_read,
            cc_5m,
            cc_1h,
        )
        log.info(
            "stream_complete_done  source=%s  model=%s  input=%d  cache_write=%d  "
            "cache_read=%d  output=%d  cost_usd=%.5f",
            source,
            _model,
            usage.input_tokens,
            cache_creation,
            cache_read,
            usage.output_tokens,
            cost,
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            _model,
            usage.input_tokens,
            usage.output_tokens,
            cache_creation,
            cache_read,
            cost_usd=cost,
            provider=provider_label_for("anthropic", ""),
        )
