"""OpenAI-compatible provider.

Talks to ANY OpenAI-compatible Chat Completions endpoint via the ``openai`` Python
SDK pointed at a configurable ``base_url`` — which is exactly what makes a LOCAL or
self-hosted model reachable:

  - Ollama        → http://localhost:11434/v1
  - LM Studio     → http://localhost:1234/v1
  - OpenRouter    → https://openrouter.ai/api/v1
  - vLLM / TGI    → http://your-host:8000/v1
  - OpenAI itself → https://api.openai.com/v1 (the default if none given)

``base_url`` + ``api_key`` are passed EXPLICITLY into the client constructor (not
read from the SDK's ambient env), so the runtime config in ``ModelConfigStore``
drives which endpoint we hit. A local server that ignores the key still works
(we send a placeholder when none is configured).

The ``openai`` SDK is imported lazily inside the client builder so this module
imports cleanly even when the dependency isn't installed — the friendly error is
raised only if someone actually selects this provider without ``pip install openai``.

Normalized events mirror the Anthropic provider so ``assistant.run``'s tool loop is
provider-agnostic: ``text_delta`` / ``tool_use_start`` / ``tool_use`` / ``usage`` /
``done``.

Prompt caching on this path is IMPLICIT for almost every endpoint — see
``_cache_body_fields`` for the per-provider contract and why so few of them take an
explicit marker. The practical consequence is that prompt LAYOUT, not a request
field, is what buys the cache hit: an implicit cache keys on an exact prefix match,
so anything volatile placed ahead of stable content destroys it. That invariant is
upheld by the assembly in ``_messages_to_openai`` (system message first, stable half
before semi-stable half, per-turn volatile content in the trailing user message) and
is pinned by tests/test_openai_cache_contract.py.

Cost tracking: ``stream_chat`` requests usage via ``stream_options={"include_usage":
true}`` and picks up the final ``usage`` chunk emitted by the API.  Cached-token
savings are read via ``_extract_cached_tokens`` across provider field conventions —
OpenAI/Grok/GLM's nested ``prompt_tokens_details.cached_tokens`` AND DeepSeek-style
flat ``prompt_cache_hit_tokens`` — and billed at the cache-read discount.  Unknown
models and local endpoints (Ollama/LM Studio) produce ``cost_usd=0.0`` — the former
with a WARNING log so the operator knows the gap exists.

OpenRouter cost: an aggregator fronts hundreds of upstream models with namespaced
ids, so a prefix price table cannot estimate its spend.  Instead, when the endpoint
is OpenRouter we opt into usage accounting (``extra_body={"usage": {"include":
true}}``) and read the REAL per-request cost the API returns in ``usage.cost`` (USD).
A reported cost is trusted over the table estimate for ANY provider that returns one
(``_reported_cost``); only OpenRouter is asked for it, so other endpoints are
unaffected.  OpenRouter attribution headers (``HTTP-Referer`` / ``X-Title``) are set
on the client — recommended by OpenRouter, non-secret, omitted for every other host.

LiteLLM proxy cost: a local LiteLLM proxy (an OpenAI-compatible gateway) reports the
real upstream cost in an ``x-litellm-response-cost`` RESPONSE HEADER — not in
``usage.cost``.  ``_litellm_header_cost`` reads it off the stream so a proxy on
localhost (which the $0 local-endpoint rule would otherwise price free) records true
spend.  Absent on non-LiteLLM endpoints → the normal estimate applies.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from typing import AsyncGenerator, List, Optional

from .base import AIProvider, log_text_cap
from .pricing import cost_usd_openai

log = logging.getLogger(__name__)

# Default endpoint when none is configured — plain OpenAI. A blank api_key is sent
# as a placeholder so a local server (Ollama/LM Studio) that ignores auth still works.
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_PLACEHOLDER_KEY = "not-needed"


class OpenAIProvider(AIProvider):
    """OpenAI-compatible chat provider with a configurable base_url + api_key."""

    def __init__(self, base_url: str = "", api_key: str = ""):
        self.base_url = (base_url or "").strip() or _DEFAULT_BASE_URL
        self.api_key = (api_key or "").strip() or _PLACEHOLDER_KEY
        self._client = None  # lazily constructed (SDK import deferred)
        # OpenRouter reports the ACTUAL per-request cost when asked; detect it so we
        # can request usage accounting and trust the reported figure over a table
        # estimate (an aggregator's namespaced ids never match our prefix table).
        self._is_openrouter = "openrouter.ai" in self.base_url.lower()
        self._is_openai = "api.openai.com" in self.base_url.lower()
        self._is_xai = "api.x.ai" in self.base_url.lower()

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - exercised only without the dep
                raise RuntimeError(
                    "The 'openai' package is required for the openai_compatible provider. "
                    "Install it with: pip3 install openai"
                ) from exc
            client_kwargs: dict = {"base_url": self.base_url, "api_key": self.api_key}
            headers = self._default_headers()
            if headers:
                client_kwargs["default_headers"] = headers
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    def _default_headers(self) -> Optional[dict]:
        """OpenRouter attribution headers (optional, recommended by OpenRouter).

        Identifies this app on OpenRouter's request logs / model rankings. Non-secret
        and omitted for every other endpoint (returns None → SDK default headers).
        """
        if self._is_openrouter:
            return {
                "HTTP-Referer": "https://github.com/open-net-worth",
                "X-Title": "Open Net Worth",
            }
        return None

    def _usage_request_extras(self) -> dict:
        """Extra create() kwargs to opt into provider-reported cost accounting.

        OpenRouter returns ``usage.cost`` only when the request opts in via
        ``usage: {include: true}``.  Sent via ``extra_body`` so it lands in the JSON
        body.  Gated to OpenRouter so a stricter endpoint (e.g. OpenAI) never sees an
        unknown request field.
        """
        if self._is_openrouter:
            return {"extra_body": {"usage": {"include": True}}}
        return {}

    # Effort → reasoning-param mapping, gated by ACTUAL endpoint capability (the
    # batch seam's routing rule, ADR 2026-07-15): only endpoints/models known to
    # accept a reasoning param get one — a strict endpoint 400s on unknown fields,
    # so local/unknown gateways (Ollama, LM Studio, vLLM, LiteLLM) get nothing.
    _OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
    # xAI reasoning_effort support differs by model (verified against docs.x.ai):
    #   grok-3-mini → low/high only
    #   grok-4.5 / grok-4.20 → low/medium/high (grok-4.5 defaults high, can't disable)
    #   grok-4 (non-mini/4.5) → not mentioned by the docs → treated as unsupported
    # _XAI_EFFORT_PREFIXES (the union) drives capability detection in capabilities.py.
    _XAI_LMH_PREFIXES = ("grok-4.5", "grok-4.20")
    _XAI_LOWHIGH_PREFIXES = ("grok-3-mini",)
    _XAI_EFFORT_PREFIXES = _XAI_LMH_PREFIXES + _XAI_LOWHIGH_PREFIXES

    def _reasoning_body_fields(self, model: str, effort: str) -> dict:
        """Request-body fields carrying the turn's reasoning effort, or {}.

        - OpenRouter: the unified ``reasoning: {effort}`` param, any model. Their
          scale takes the FULL app ladder (low/medium/high/xhigh/max) and
          normalizes to the nearest level each model supports, so it passes
          through unchanged (verified against their reasoning-tokens docs).
        - api.openai.com: ``reasoning_effort``, reasoning families only
          (gpt-4o-style models reject the param); xhigh/max clamp to ``high``.
        - api.x.ai: ``reasoning_effort`` — grok-4.5/4.20 take low/medium/high;
          grok-3-mini only low/high (medium folds to low); other grok models get
          nothing.
        """
        # "none"/"off" — DISABLE reasoning (structured background tasks: tokens to the
        # answer, not thinking). OpenRouter exposes a unified enable flag; OpenAI's
        # reasoning families take "minimal"; xAI/others can't disable → nothing.
        if effort in ("none", "off"):
            if self._is_openrouter:
                return {"reasoning": {"enabled": False}}
            if self._is_openai and (model or "").lower().startswith(
                self._OPENAI_REASONING_PREFIXES
            ):
                return {"reasoning_effort": "minimal"}
            return {}
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            effort = "medium"
        if self._is_openrouter:
            return {"reasoning": {"effort": effort}}
        level = {"xhigh": "high", "max": "high"}.get(effort, effort)  # → low/medium/high
        model_lc = (model or "").lower()
        if self._is_openai and model_lc.startswith(self._OPENAI_REASONING_PREFIXES):
            return {"reasoning_effort": level}
        if self._is_xai:
            if model_lc.startswith(self._XAI_LMH_PREFIXES):
                return {"reasoning_effort": level}
            if model_lc.startswith(self._XAI_LOWHIGH_PREFIXES):
                return {"reasoning_effort": "high" if level == "high" else "low"}
        return {}

    def _cache_body_fields(self, model: str) -> dict:
        """OpenRouter automatic prompt caching for Anthropic models, or {}.

        Anthropic caching is OPT-IN (unlike Moonshot/OpenAI/DeepSeek, which cache
        automatically): without this, Claude via OpenRouter re-bills the full
        ~40k-token system prompt at list price EVERY turn (live finding
        2026-07-17). A single top-level ``cache_control`` engages OpenRouter's
        automatic mode — it places and advances breakpoints itself, no
        per-content-block markers. Default 5m TTL deliberately (auto-advancing
        breakpoints re-write as the chat grows; the 1h premium only pays off on
        a pinned stable prefix, which this mode doesn't give us). Gated to
        anthropic/* — other providers need nothing, and this is an
        OpenRouter-normalization field a strict endpoint must never see.

        WHY THIS GATE IS NOT WIDER (surveyed against provider docs 2026-07-19).
        The Anthropic path's four-breakpoint scheme (tools → static → semi-stable
        → last message) has no counterpart to port here, because almost nothing on
        this path accepts a breakpoint at all:

          xAI / api.x.ai   — NO explicit marker exists. docs.x.ai exposes exactly
                             one caching parameter, ``prompt_cache_key``, and it is
                             a sticky-ROUTING hint, not a breakpoint. Caching is
                             automatic and always on. There is nothing to emit.
          OpenAI ≤5.5      — automatic at ≥1024 prompt tokens, no marker.
          DeepSeek, GLM,   — automatic, explicitly "without needing to modify
          Moonshot           their code". No marker.
          Gemini, Qwen     — DO take ``cache_control`` over OpenRouter, but their
          (via OpenRouter)   economics differ (Gemini writes cost full input price
                             on a 5-minute TTL that does NOT refresh on read), so
                             turning it on blind can cost more than it saves. Left
                             off deliberately, pending a measured read/write ratio.

        So: for every provider this instance actually runs on, the cache is either
        already engaged (anthropic/* above) or engaged implicitly with no request
        field to send. What remains under our control is prompt LAYOUT — see the
        module docstring.
        """
        if self._is_openrouter and (model or "").lower().startswith("anthropic/"):
            return {"cache_control": {"type": "ephemeral"}}
        return {}

    def _cache_routing_fields(self, system: str, tools: List[dict]) -> dict:
        """``prompt_cache_key`` — a sticky-routing hint, where it is documented.

        Both docs.x.ai and OpenAI's prompt-caching guide document this field: it
        routes requests that share a prompt prefix back to the same server, so the
        prefix is more likely to still be resident. It is a routing hint only — it
        carries no prompt content and changes nothing the model sees.

        The key is derived from the STABLE PREFIX itself (system text + tool names)
        rather than a session id, which makes it correct by construction: requests
        that share a cacheable prefix share a key, and a request whose prefix has
        genuinely changed gets a different key instead of being pinned to a server
        whose entry no longer matches.

        Gated to the two endpoints that document the field. Deliberately NOT sent
        to OpenRouter: they front upstreams with their own sticky routing and do
        not document accepting this field, and an unrecognized body field is a
        400 risk. That means it does NOT help this instance's current OpenRouter
        configuration — it helps a direct api.x.ai / api.openai.com setup.

        Honest bound on the benefit: this addresses ROUTING misses only. It cannot
        recover a prefix the provider has already EVICTED, which is what the
        measured >1h-gap cold start actually is.
        """
        if not (self._is_xai or self._is_openai):
            return {}
        digest = hashlib.sha256()
        digest.update((system or "").encode("utf-8", "replace"))
        for t in tools or []:
            digest.update(b"\x00")
            digest.update(str(t.get("name", "")).encode("utf-8", "replace"))
        return {"prompt_cache_key": f"onw-{digest.hexdigest()[:32]}"}

    @staticmethod
    def _extract_cached_tokens(usage) -> int:
        """Cache-hit prompt tokens the endpoint reported, across field conventions.

        Providers report the cached subset of the prompt differently:
          - OpenAI / Grok / GLM (standard):  usage.prompt_tokens_details.cached_tokens
          - DeepSeek-style (flat):           usage.prompt_cache_hit_tokens
        In every case ``prompt_tokens`` is the TOTAL, so the caller's
        ``uncached = prompt_tokens - cached`` split holds. These tokens bill at the
        cache-read discount. Returns 0 when no cache field is present (or unparseable).
        """
        if usage is None:
            return 0
        # Standard OpenAI-shaped nested field. isinstance guards against a non-numeric
        # placeholder (e.g. a MagicMock auto-attr) being treated as a real count.
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0)
            if isinstance(cached, (int, float)) and cached > 0:
                return int(cached)
        # Fall back to the DeepSeek-style flat field.
        hit = getattr(usage, "prompt_cache_hit_tokens", 0)
        if isinstance(hit, (int, float)) and hit > 0:
            return int(hit)
        return 0

    @staticmethod
    def _litellm_header_cost(stream) -> Optional[float]:
        """Real cost (USD) from a LiteLLM proxy's ``x-litellm-response-cost`` header.

        A LiteLLM proxy reports the true upstream cost in a RESPONSE HEADER (not in
        ``usage.cost``), so a proxy on localhost — which ``_is_local_base_url`` would
        otherwise price at $0 — still records real spend. Read off the SDK stream's
        raw ``.response`` (available on recent SDKs); absent/unparseable → None so the
        caller falls back to the estimate. The isinstance guard keeps a mocked
        stream's auto-attributes from being mistaken for a real header value.
        """
        resp = getattr(stream, "response", None)
        headers = getattr(resp, "headers", None)
        if headers is None:
            return None
        try:
            raw = headers.get("x-litellm-response-cost")
        except Exception:
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            val = float(raw)
        except ValueError:
            return None
        return val if val >= 0 else None

    @staticmethod
    def _reported_cost(usage) -> Optional[float]:
        """Actual cost (USD) the endpoint reported in its usage block, if any.

        OpenRouter returns ``usage.cost`` (credits == USD) when usage accounting is
        requested. Trusted over the table estimate — it reflects the real upstream
        model + price, which a prefix table cannot for an aggregator. Returns None
        when the field is absent or unparseable so the caller falls back to the
        estimate.
        """
        if usage is None:
            return None
        raw = getattr(usage, "cost", None)
        if raw is None:
            return None
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        return val if val >= 0 else None

    @staticmethod
    def _tools_to_openai(tools: List[dict]) -> list[dict]:
        """Anthropic-shaped tool schemas → OpenAI ``function`` tool schemas."""
        out: list[dict] = []
        for t in tools or []:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return out

    @staticmethod
    def _messages_to_openai(messages: List[dict], system: str) -> list[dict]:
        """Anthropic-shaped history → OpenAI chat messages.

        Anthropic content blocks (text / tool_use / tool_result) are flattened into
        OpenAI's assistant ``tool_calls`` + ``tool`` role messages.

        The system string may contain CACHE_SPLIT_MARKER and/or TURN_SPLIT_MARKER
        (from _split_turn_context). OpenAI has no cache-breakpoint concept, so both
        markers are stripped and the text halves are joined before injecting the
        system message — the model sees one continuous system prompt with no noise.
        """
        from backend.ai.prompts import CACHE_SPLIT_MARKER, TURN_SPLIT_MARKER

        out: list[dict] = []
        if system:
            clean = system.replace(CACHE_SPLIT_MARKER, "\n").replace(TURN_SPLIT_MARKER, "\n")
            out.append({"role": "system", "content": clean})
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if not isinstance(content, list):
                out.append({"role": role, "content": ""})
                continue
            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            }
                        )
                msg: dict = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out.append(msg)
            elif role == "user":
                # A user turn may carry tool_result blocks (from the tool loop), text,
                # and/or image blocks (a photo/screenshot forwarded from a connector).
                text_parts = []
                image_parts: list[dict] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "tool_result":
                        tr = block.get("content", "")
                        if isinstance(tr, list):
                            tr = "".join(b.get("text", "") for b in tr if isinstance(b, dict))
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": tr if isinstance(tr, str) else json.dumps(tr),
                            }
                        )
                    elif btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "image":
                        source = block.get("source", {})
                        image_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{source.get('media_type', 'image/jpeg')};"
                                    f"base64,{source.get('data', '')}"
                                },
                            }
                        )
                if image_parts:
                    user_content: str | list = image_parts + (
                        [{"type": "text", "text": "".join(text_parts)}] if text_parts else []
                    )
                    out.append({"role": "user", "content": user_content})
                elif text_parts:
                    out.append({"role": "user", "content": "".join(text_parts)})
            else:
                out.append({"role": role or "user", "content": ""})
        return out

    def _vision_ref(self, model: str) -> str:
        """This call's model as a ``"<provider_key>:<model_id>"`` capability ref.

        The provider instance knows its own base_url, which is what identifies WHICH
        openai-compatible endpoint this is (GLM vs OpenRouter vs a local runtime) — a
        bare model id would collide across them.
        """
        from backend.db.model_config_store import _infer_provider_key

        key = _infer_provider_key("openai_compatible", self.base_url or "") or ""
        return f"{key}:{model}" if key else (model or "")

    def _known_no_vision(self, model: str) -> bool:
        """True only when we POSITIVELY know this model refuses image input.

        Unknown (``None``) is deliberately not "no" here — see services/vision_capability.py.
        """
        from backend.services.vision_capability import supports_vision

        try:
            return supports_vision(self._vision_ref(model)) is False
        except Exception:
            return False

    @staticmethod
    def _strip_image_parts(oai_messages: List[dict]) -> "tuple[list[dict], bool]":
        """Return ``(messages, had_images)`` with every ``image_url`` content part replaced
        by a short text placeholder. Used to RETRY when the target model has no vision — a
        photo/screenshot lingering in a long history must not 404 every subsequent TEXT turn
        (OpenRouter: "No endpoints found that support image input")."""
        had = False
        out: List[dict] = []
        for m in oai_messages:
            content = m.get("content")
            if isinstance(content, list):
                texts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        had = True
                        texts.append("[image omitted — the selected model has no vision]")
                    elif isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                out.append({**m, "content": "\n".join(t for t in texts if t)})
            else:
                out.append(m)
        return out, had

    async def stream_chat(
        self,
        messages: List[dict],
        tools: List[dict],
        system: str,
        model: str,
        effort: str = "medium",
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        oai_messages = self._messages_to_openai(messages, system)
        # PROACTIVE strip: when we already know this model refuses image input, drop the
        # image parts BEFORE the call instead of paying a round trip (plus the base64
        # upload of every image still in history) to be told so. The reactive retry below
        # remains the path for a model whose capability is genuinely unknown — and is what
        # TEACHES us, so this branch can fire next time.
        if self._known_no_vision(model):
            stripped_first, had_images_first = self._strip_image_parts(oai_messages)
            if had_images_first:
                log.info(
                    "stream_chat: stripping image input up front — %s is known to have no "
                    "vision (see services/vision_capability.py)",
                    model,
                )
                oai_messages = stripped_first
        kwargs: dict = dict(model=model, messages=oai_messages, stream=True)
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)

        log.debug(
            "stream_chat  provider=openai_compatible  model=%s  base_url=%s  effort=%s  "
            "reasoning_params=%s  messages=%d",
            model,
            self.base_url,
            effort,
            self._reasoning_body_fields(model, effort),
            len(messages),
        )

        # Accumulate streamed tool-call deltas by index → emit once complete.
        tool_acc: dict[int, dict] = {}
        finish_reason: Optional[str] = None
        # Usage fields — populated from the trailing usage chunk when
        # stream_options={"include_usage": True} is honoured by the endpoint. SUMMED
        # across attempts (the swallowed-round retry below): a round the host billed and
        # then ate is still paid for, and the usage event must say so.
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0  # prompt_tokens_details.cached_tokens if present
        reported_cost: Optional[float] = None  # usage.cost when the endpoint reports it

        # Request a trailing usage chunk.  Not all OpenAI-compatible endpoints
        # honour this option (Ollama/LM Studio may ignore it silently), but it is
        # harmless to send — endpoints that do support it return the final usage
        # in a chunk whose choices list is empty.
        kwargs["stream_options"] = {"include_usage": True}
        # ONE merged extra_body: OpenRouter cost accounting + the turn's reasoning
        # effort (sent via the body so it works on any SDK version). Both helpers
        # return {} where the endpoint doesn't support the field.
        extra_body: dict = dict(self._usage_request_extras().get("extra_body", {}))
        extra_body.update(self._reasoning_body_fields(model, effort))
        extra_body.update(self._cache_body_fields(model))
        extra_body.update(self._cache_routing_fields(system, tools))
        if extra_body:
            kwargs["extra_body"] = extra_body

        # Wall-clock timing for the routing engine's latency telemetry: ttft = first
        # visible content, duration = full stream. Monotonic, milliseconds.
        import time as _time

        _t0 = _time.monotonic()
        _ttft_ms: Optional[float] = None

        # Two attempts at most: the request, and ONE retry when the first came back
        # SWALLOWED — completion tokens billed, yet no content, no reasoning and no
        # tool_calls delivered, with a clean `stop`. Live 2026-09-22 (run_47f8605fdd98,
        # z-ai/glm-5.3-flash): four turns in a row ended on such a round, 15–17 tokens each
        # — exactly one of this model's tool calls — and replaying the same conversation
        # showed the host OpenRouter had routed to (`provider: "CoreWeave"`) doing it 3/3
        # while any other host returned the proper tool call 2/2. The serving stack in
        # front of the model ate its call and reported a normal end.
        #
        # This is a TRANSPORT retry, not the capability fallback the RFC keeps out of
        # adapters (cross-channel-attachments-vision-and-statement-evidence §4): same
        # model, same messages, same tools — the only change is an OpenRouter routing hint
        # excluding the host that just swallowed the output, and only when the response
        # actually named one (a plain endpoint has no routing to change). A retry that
        # fails, or is swallowed again, falls through to the empty round it was, for the
        # loop's completion gate to correct; it never becomes an error.
        stream = None
        for attempt in range(2):
            try:
                stream = await client.chat.completions.create(**kwargs)
            except Exception as exc:
                if attempt:
                    log.warning(
                        "swallowed_round retry failed  model=%s  err=%s — surfacing the "
                        "empty round",
                        model,
                        str(exc)[:160],
                    )
                    break
                # A non-vision model refuses image content in the request (OpenRouter: "No
                # endpoints found that support image input"). Recognize ONLY a narrow,
                # provider-shaped modality refusal — the error text says image/vision/modal
                # AND the request actually carried an image — and translate it into a typed
                # UnsupportedModalityError. Every other error (auth, rate limit, an
                # unrelated 400) re-raises untouched.
                #
                # The retry/learn decision used to live HERE (strip the image parts, record
                # supports_vision=False, retry once with an "[image omitted]" placeholder —
                # see git history / tests/test_char_openai_provider_reactive_image_strip.py
                # for the pinned pre-Phase-3 shape). It now belongs to shared turn
                # orchestration (ai/assistant.py): an adapter must not itself decide to
                # invoke a different model, learn a capability, or rebuild the request —
                # see RFC cross-channel-attachments-vision-and-statement-evidence section 4.
                msg = str(exc).lower()
                _, had_images = self._strip_image_parts(oai_messages)
                if not (had_images and ("image" in msg or "vision" in msg or "modal" in msg)):
                    raise
                log.warning(
                    "stream_chat: %s rejected image input — raising UnsupportedModalityError "
                    "for shared turn orchestration to recover",
                    model,
                )
                from backend.core.errors import UnsupportedModalityError

                raise UnsupportedModalityError(self._vision_ref(model), "image") from exc

            delivered = False
            served_by: Optional[str] = None
            a_input = a_output = a_cached = 0
            a_cost: Optional[float] = None
            # On a client disconnect, assistant.run aclose()s this generator → GeneratorExit
            # is raised at the yield below. Explicitly CLOSE the upstream stream on any early
            # exit (disconnect / cancel / stream error) so token generation — and billing —
            # stops promptly, instead of abandoning the request to be torn down later by
            # GC/backpressure while the model keeps writing. This is the OpenAI-compatible
            # twin of anthropic_provider's `async with ...messages.stream(...)`. The normal
            # (fully-drained) path is untouched: no except fires, the stream stays open for
            # the LiteLLM header-cost read below, then exhausts and returns to the pool.
            try:
                async for chunk in stream:
                    # OpenRouter names the upstream host that served the request on every
                    # chunk; anything else leaves it unset.
                    served_by = getattr(chunk, "provider", None) or served_by
                    # Usage rides DIFFERENT chunks per provider: OpenAI sends a trailing
                    # chunk with an EMPTY choices list; OpenRouter attaches it to the final
                    # chunk that still CARRIES a choice (live incident 2026-07-17 — reading
                    # only the empty-choices convention booked kimi-k3 turns as 0 tokens /
                    # $0 "unmeasured" and dropped the real usage.cost). Read it wherever it
                    # appears; last chunk wins within an attempt.
                    if getattr(chunk, "usage", None) is not None:
                        a_input = chunk.usage.prompt_tokens or 0
                        a_output = chunk.usage.completion_tokens or 0
                        a_cached = self._extract_cached_tokens(chunk.usage)
                        rc = self._reported_cost(chunk.usage)
                        if rc is not None:
                            a_cost = rc
                    # Gemini and some providers send keepalive / usage-only chunks with no
                    # choices field (or an empty list).
                    choices = chunk.choices or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.delta
                    # OpenRouter reasoning models stream thinking as delta.reasoning —
                    # surface it like the Anthropic provider's thinking_delta.
                    if getattr(delta, "reasoning", None):
                        delivered = True
                        yield {"type": "thinking_delta", "text": delta.reasoning}
                    if getattr(delta, "content", None):
                        delivered = True
                        if _ttft_ms is None:
                            _ttft_ms = (_time.monotonic() - _t0) * 1000.0
                        yield {"type": "text_delta", "text": delta.content}
                    # Accumulate tool-call deltas defensively.  Different providers split
                    # the fields across chunks differently:
                    #   - OpenAI: id + name on the first delta for that index, arguments
                    #     fragmented
                    #   - Gemini: may send id=None on subsequent deltas; function may be None
                    #   - Ollama: generally mirrors OpenAI but may omit function on some deltas
                    for tc in getattr(delta, "tool_calls", None) or []:
                        delivered = True
                        idx = getattr(tc, "index", 0) or 0
                        slot = tool_acc.setdefault(
                            idx, {"id": "", "name": "", "args": "", "_started": False}
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            name = getattr(fn, "name", None) or ""
                            if name and not slot["name"]:
                                slot["name"] = name
                            args_frag = getattr(fn, "arguments", None) or ""
                            if args_frag:
                                slot["args"] += args_frag
                        # Emit tool_use_start exactly once, as soon as we have a name.
                        if slot["name"] and not slot["_started"]:
                            slot["_started"] = True
                            yield {
                                "type": "tool_use_start",
                                "id": slot["id"],
                                "name": slot["name"],
                            }
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
            except BaseException:
                # Disconnect (GeneratorExit) / cancel / mid-stream error: tear down the
                # upstream connection now so the provider stops generating. Best-effort
                # and tolerant of a stream without close(); then re-raise so the turn
                # unwinds exactly as before.
                closer = getattr(stream, "close", None)
                if callable(closer):
                    try:
                        res = closer()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                raise

            input_tokens += a_input
            output_tokens += a_output
            cached_tokens += a_cached
            if a_cost is not None:
                reported_cost = (reported_cost or 0.0) + a_cost

            swallowed = (
                not delivered
                and a_output > 0
                and bool(served_by)
                and finish_reason in (None, "stop")
            )
            if not swallowed or attempt:
                break
            log.warning(
                "swallowed_round  model=%s  host=%s  billed_output=%d  finish=%s — the host "
                "returned nothing for the tokens it billed; retrying once with it excluded",
                model,
                served_by,
                a_output,
                finish_reason,
            )
            routing = dict((kwargs.get("extra_body") or {}).get("provider") or {})
            routing["ignore"] = [*routing.get("ignore", []), served_by]
            kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "provider": routing}

        # Map OpenAI finish_reason → the Anthropic-style stop_reason the loop expects.
        stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
        if finish_reason == "length":
            stop_reason = "max_tokens"
            log.warning("max_tokens hit  model=%s — response was cut off", model)

        # A LiteLLM proxy reports true cost in a response header — trust it over the
        # local-endpoint $0 / table estimate (only when usage.cost didn't already fill it).
        if reported_cost is None:
            reported_cost = self._litellm_header_cost(stream)

        # Trust a provider-reported cost (OpenRouter / LiteLLM proxy) over the estimate.
        cost = (
            reported_cost
            if reported_cost is not None
            else cost_usd_openai(model, input_tokens, output_tokens, cached_tokens, self.base_url)
        )

        log.info(
            "model_turn_done  provider=openai_compatible  model=%s  "
            "input=%d  cached=%d  output=%d  cost_usd=%.5f  cost_src=%s",
            model,
            input_tokens,
            cached_tokens,
            output_tokens,
            cost,
            "reported" if reported_cost is not None else "estimated",
        )

        # NOTE: stream_chat does NOT write a cost record. The single writer for a
        # streaming turn is assistant.py::_record_cost, which accumulates the
        # ``usage`` event below and records once per turn with the real
        # source/session/surface. A self-record here (removed) double-counted
        # every OpenAI-compatible (grok/xAI) turn and mislabeled the copy as
        # source="chat" with an empty session_id — inflating the log and the
        # "Chat" surface. anthropic_provider.stream_chat is single-writer too;
        # the non-streaming complete() path below is a separate llm_seam writer.
        yield {
            "type": "usage",
            "input_tokens": input_tokens,
            "cache_creation_tokens": 0,
            "cache_read_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            # The endpoint priced this call itself — so the price is known even when
            # no table lists the model (an OpenRouter alias like ``~z-ai/glm-flash-latest``).
            "cost_reported": reported_cost is not None,
            "model": model,
            "ttft_ms": _ttft_ms,
            "duration_ms": (_time.monotonic() - _t0) * 1000.0,
        }

        for slot in tool_acc.values():
            try:
                parsed = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                log.warning("tool_use args not valid JSON  tool=%s", slot.get("name"))
                parsed = {}
            yield {
                "type": "tool_use",
                "id": slot["id"],
                "name": slot["name"],
                "input": parsed,
            }

        yield {"type": "done", "stop_reason": stop_reason}

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,
        effort: str = "",
    ) -> str:
        client = self._get_client()
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        user_content: str | list = prompt
        if images:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"},
                }
                for img in images
            ] + [{"type": "text", "text": prompt}]
        oai_messages.append({"role": "user", "content": user_content})
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]
        # Background structured calls (analyst/screen/memory/…) opt into JSON mode and a
        # reasoning setting. Merge the reasoning field into any usage-accounting extra_body.
        extra_body: dict = dict(self._usage_request_extras().get("extra_body", {}))
        if effort:
            extra_body.update(self._reasoning_body_fields(model, effort))
        kwargs: dict = dict(model=model, messages=oai_messages, max_tokens=max_tokens)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if extra_body:
            kwargs["extra_body"] = extra_body

        async def _create(rf: dict | None, xb: dict) -> object:
            kw: dict = dict(model=model, messages=oai_messages, max_tokens=max_tokens)
            if rf is not None:
                kw["response_format"] = rf
            if xb:
                kw["extra_body"] = xb
            return await client.chat.completions.create(**kw)

        try:
            resp = await _create(response_format, extra_body)
        except Exception as exc:
            # DEGRADE LADDER, so a strict endpoint that 400s on an unsupported field NEVER
            # breaks a flow: native json_schema → json_object (GLM etc. support this) →
            # plain (caller still prompts for JSON + parses tolerantly). Reasoning extras
            # ride the json_object step and are dropped for the plain step.
            steps: list[tuple[dict | None, dict]] = []
            if response_format and response_format.get("type") == "json_schema":
                steps.append(({"type": "json_object"}, extra_body))
            steps.append((None, {}))
            resp = None
            for rf, xb in steps:
                try:
                    log.warning(
                        "complete: %s rejected response_format/reasoning (%s) — degrading to %s",
                        model,
                        type(exc).__name__,
                        (rf or {}).get("type", "plain"),
                    )
                    resp = await _create(rf, xb)
                    break
                except Exception as exc2:  # noqa: BLE001 - try the next weaker step
                    exc = exc2
            if resp is None:
                raise exc
        choice = resp.choices[0] if resp.choices else None
        in_tok = getattr(resp.usage, "prompt_tokens", 0) or 0
        out_tok = getattr(resp.usage, "completion_tokens", 0) or 0
        cached_tok = self._extract_cached_tokens(resp.usage if resp.usage else None)

        # Trust a provider-reported cost (OpenRouter) over the table estimate.
        reported = self._reported_cost(resp.usage if resp.usage else None)
        cost = (
            reported
            if reported is not None
            else cost_usd_openai(model, in_tok, out_tok, cached_tok, self.base_url)
        )
        # Owner incident 2026-08-31 (KODK): a 9074-output-token call landed <1200 visible
        # chars of final content — the rest was reasoning, which this method previously threw
        # away entirely (only `choice.message.content` was ever read). With NOTHING logged,
        # there was no way to tell "the model is legitimately still reasoning through a hard
        # case" from "something is silently broken" while a call runs long. `message.reasoning`
        # mirrors the streaming path's own `delta.reasoning` (OpenRouter's unified reasoning-
        # tokens field, same attribute name on both the delta and the final non-streaming
        # message) — `getattr` because it's absent on a non-reasoning response/provider.
        # Logged, not written to `cost_log`: that table's retention design (15-day raw window,
        # rolled into a numeric-only daily aggregate) has no shape for arbitrary response text,
        # and this is diagnostic output, not a cost record.
        message_content = (choice.message.content if choice and choice.message else "") or ""
        message_reasoning = (
            getattr(choice.message, "reasoning", None) if choice and choice.message else None
        ) or ""
        _LOG_TEXT_CAP = log_text_cap(source)
        log.info(
            "complete_done  provider=openai_compatible  source=%s  model=%s  "
            "input=%d  cached=%d  output=%d  cost_usd=%.5f  "
            "content_chars=%d  reasoning_chars=%d\n"
            "--- content (first %d chars) ---\n%s\n"
            "--- reasoning (first %d chars) ---\n%s",
            source,
            model,
            in_tok,
            cached_tok,
            out_tok,
            cost,
            len(message_content),
            len(message_reasoning),
            _LOG_TEXT_CAP,
            message_content[:_LOG_TEXT_CAP],
            _LOG_TEXT_CAP,
            message_reasoning[:_LOG_TEXT_CAP],
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            in_tok,
            out_tok,
            0,
            cached_tok,
            cost_usd=cost,
            provider=provider_label_for("openai_compatible", self.base_url),
        )
        return message_content

    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        effort: str = "",
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.append({"role": "user", "content": prompt})
        if not model:
            from backend.db.model_config_store import ModelConfigStore

            model = ModelConfigStore().resolved()["model"]

        extra_body: dict = dict(self._usage_request_extras().get("extra_body", {}))
        if effort:
            extra_body.update(self._reasoning_body_fields(model, effort))
        kwargs: dict = dict(
            model=model,
            messages=oai_messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if extra_body:
            kwargs["extra_body"] = extra_body

        stream = await client.chat.completions.create(**kwargs)
        in_tok = 0
        out_tok = 0
        cached_tok = 0
        reported_cost: float | None = None
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                in_tok = getattr(usage, "prompt_tokens", 0) or 0
                out_tok = getattr(usage, "completion_tokens", 0) or 0
                cached_tok = self._extract_cached_tokens(usage)
                reported_cost = self._reported_cost(usage)
            for choice in chunk.choices:
                text = getattr(choice.delta, "content", None)
                if text:
                    yield {"type": "text_delta", "text": text}

        cost = (
            reported_cost
            if reported_cost is not None
            else cost_usd_openai(model, in_tok, out_tok, cached_tok, self.base_url)
        )
        log.info(
            "stream_complete_done  provider=openai_compatible  source=%s  model=%s  "
            "input=%d  cached=%d  output=%d  cost_usd=%.5f",
            source,
            model,
            in_tok,
            cached_tok,
            out_tok,
            cost,
        )
        from backend.ai.cost_tracker import record
        from backend.db.model_config_store import provider_label_for

        record(
            source,
            model,
            in_tok,
            out_tok,
            0,
            cached_tok,
            cost_usd=cost,
            provider=provider_label_for("openai_compatible", self.base_url),
        )
