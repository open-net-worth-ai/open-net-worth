"""LLM-provider seam — the inversion that lets ``services``/``ingest`` run an LLM
completion (statement parsing, email parsing) WITHOUT importing ``ai`` (which would trip
import-linter Contract 1: a service must not import api/ai).

This mirrors the existing inversions (``core.io.register_write_hook``,
``core.channels.register_channel``, ``core.tool_registry_seam``,
``core.agent_runner.register_runner``): ``ai`` registers its provider here at import time,
and a downstream module calls ``complete(...)`` / ``parse_json(...)`` through this seam. So
the edges are ``services → core`` and ``ai → core`` — both downward; no ``services → ai``
import. The only internal import is its stdlib-only sibling ``core.background_ai_health``,
which records each background call's outcome for Settings → AI.

Two capabilities are exposed because the two real consumers (``services.reconcile`` and the
email ingest adapter) both need them:

  * ``complete(prompt, *, system, model, max_tokens, source)`` — a single-shot text
    completion on the configured AI provider (``AIProvider.complete``).
  * ``parse_json(text)`` — the shared fenced/chatty-JSON extractor (``ai.memory._parse_json``).

Batch capability (Feature B — cost optimization):

  * ``submit_batch(task, requests)`` — submit a list of ``BatchRequest`` items to the
    provider batch API; returns an opaque handle string.
  * ``collect_batch(handle)`` — poll for results; returns ``list[BatchResult]`` when done
    or ``BatchPending`` when still processing.
  * ``register_batch(submit, collect)`` — called by ``ai/batch/dispatch`` at startup to wire
    the capability-routed adapter (see ``ai/batch/routing.py`` — routing is by ACTUAL
    endpoint capability, not the transport label alone: an unrecognized ``openai_compatible``
    endpoint degrades to the sync fallback below rather than guessing a wire format).  When no
    batch provider is registered both calls fall back to synchronous per-request ``complete()``
    execution, returning the same shape.
  * ``BatchOutputSchema`` — the neutral forced-structured-output contract a ``BatchRequest``
    carries instead of a provider-specific tool/function-call payload; each adapter translates
    it to its own wire format (see the ``BatchRequest``/``BatchOutputSchema`` docstrings).

Retiring the ``services.reconcile → ai`` import-linter exception is the point: once both
``reconcile`` and the email adapter consume the provider through this seam, NO ``services``
module names ``ai`` and the ``ignore_imports`` entry is removed.

If no provider is registered (e.g. a unit test that imports ``services`` without ``ai``),
``complete`` raises ``LLMUnavailable`` and ``parse_json`` falls back to a plain
``json.loads`` (best-effort), so a caller can degrade gracefully — exactly how the old
direct-import path failed when the provider was misconfigured.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Union

from backend.core import background_ai_health

logger = logging.getLogger(__name__)

# A provider's single-shot completion: async (prompt, system, model, max_tokens, source) -> str.
CompleteFn = Callable[..., Awaitable[str]]
# The chatty-JSON extractor: (text) -> dict | list | None.
ParseJsonFn = Callable[[str], Any]

_complete: Optional[CompleteFn] = None
_parse_json: Optional[ParseJsonFn] = None


class LLMUnavailable(RuntimeError):
    """Raised by ``complete`` when no LLM provider has been registered on the seam."""


def register_llm(complete: Optional[CompleteFn], parse_json: Optional[ParseJsonFn]) -> None:
    """Install (or clear, with ``None``) the LLM provider's completion + JSON-parse hooks.

    Last registration wins. ``ai/tools/__init__`` calls this at import time wiring
    ``ai.assistant.get_provider().complete`` + ``ai.memory._parse_json``.
    """
    global _complete, _parse_json
    _complete = complete
    _parse_json = parse_json


async def complete(
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    max_tokens: int = 2048,
    source: str = "complete",
    images: Optional[List[dict]] = None,
    response_format: Optional[dict] = None,
    effort: str = "",
) -> str:
    """Run a single-shot completion on the registered AI provider.

    ``images`` (optional) is a list of ``{"media_type", "data": <base64>}`` page images for a
    multimodal (vision) completion — the scanned-statement path.

    ``response_format`` (optional, e.g. ``{"type": "json_object"}``) forces structured output
    on providers that support it (OpenAI-standard: OpenRouter/GLM/OpenAI/xAI/Gemini); the
    provider degrades gracefully (retry-without) if an endpoint rejects it, and Anthropic
    ignores it (its forced-JSON path is the batch seam's tool_use). ``effort`` sets the
    reasoning budget for this call (e.g. ``"none"`` to disable reasoning on a structured
    background task so tokens go to the answer, not thinking).

    All three optional args are forwarded ONLY when set, so an older registered hook (or a
    test stub) with a text-only signature keeps working for every plain text call.

    Raises ``LLMUnavailable`` if no provider is registered (so a caller that requires the
    LLM fails loudly rather than silently fabricating data)."""
    fn = _complete
    if fn is None:
        raise LLMUnavailable("no LLM provider registered on core.llm_seam")
    extra: dict = {}
    if images:
        extra["images"] = images
    if response_format is not None:
        extra["response_format"] = response_format
    if effort:
        extra["effort"] = effort
    return await fn(
        prompt, system=system, model=model, max_tokens=max_tokens, source=source, **extra
    )


_JSON_DIRECTIVE = (
    "Respond with ONLY a single valid JSON value — no prose, no explanation, no markdown "
    "code fence, no reasoning preamble."
)

# Absolute ceiling for the reask token-escalation (a truncated reply retries with a bigger
# budget so it can finish; this bounds the worst case so a pathological reply can't run away).
_MAX_REASK_TOKENS = 16000


def strictify_schema(schema: dict) -> dict:
    """Reshape a JSON Schema so it satisfies what ``strict: True`` actually requires.

    Strict structured output (OpenAI Responses, and Codex's ``codex_output_schema``, which
    wraps it) imposes two rules ordinary JSON Schema does not:

      * every object sets ``additionalProperties: false``
      * every object's ``required`` lists ALL of its properties — optionality is expressed by
        a NULLABLE type, never by omission from ``required``

    Our schemas are authored in the Anthropic tool-input dialect, which demands neither, so
    ``_response_format_for`` was stamping ``strict: True`` onto schemas no strict endpoint
    would accept. Every provider paid for it differently, and all of them quietly:

      * Codex app-server — hard ``invalid_json_schema`` on 100% of schema-bearing turns
        (owner repro 2026-08-09), then silently rerouted to ``codex exec``, which takes no
        schema and so always looked fine.
      * OpenAI/xAI — a 400 caught by ``openai_provider``'s degrade ladder, dropping
        ``json_schema`` → ``json_object``. Constrained decoding was never actually on; one
        WARNING was the only trace.

    So this is not a codex workaround: it makes the claim we send true for every transport.
    Returns a NEW schema — these are module-level constants shared across providers, and
    normalising in place would change what every other one sends.
    """
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            out[key] = strictify_schema(value)
        elif isinstance(value, list):
            # Covers anyOf/oneOf/allOf/$defs-as-list AND draft-4 tuple `items`, rather than
            # naming the keywords — an unlisted keyword carrying schemas would be skipped.
            out[key] = [strictify_schema(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value

    declared = out.get("type")
    types = declared if isinstance(declared, list) else [declared]
    if "object" not in types and "properties" not in out:
        return out

    props = out.get("properties")
    if not isinstance(props, dict):
        # An open map (`additionalProperties: {...}` with no `properties`) has NO strict
        # equivalent — strict mode cannot express "any key". Forcing it shut would silently
        # forbid every key the caller wanted. Left alone and reported, so the eventual API
        # rejection is traceable to a schema we knew we could not fix.
        if out.get("additionalProperties") not in (False, None):
            logger.warning(
                "strictify_schema: open map cannot satisfy strict mode; leaving as-is (%s)",
                sorted(out.keys()),
            )
        return out

    already = set(out.get("required") or [])
    out["additionalProperties"] = False
    out["required"] = list(props.keys())
    for name, sub in props.items():
        if name in already or not isinstance(sub, dict):
            continue
        # Newly required, so it can no longer be OMITTED — preserve the author's intent by
        # letting it be null instead. Skipping this would turn "leave it out when it doesn't
        # apply" into "invent a value", which on a learned statement schema means fabricated
        # column data rather than an absent field.
        sub_type = sub.get("type")
        if isinstance(sub_type, str) and sub_type != "null":
            sub["type"] = [sub_type, "null"]
        elif isinstance(sub_type, list) and "null" not in sub_type:
            sub["type"] = [*sub_type, "null"]
    return out


def _response_format_for(schema: Optional[dict], *, pydantic_derived: bool = False) -> dict:
    """OpenAI-shaped ``response_format``: native strict ``json_schema`` when a schema is
    supplied (the provider translates/degrades per its capability — OpenAI/xAI pass it,
    Anthropic → ``output_config``, GLM/older → downgrade to ``json_object``), else plain
    ``json_object``.

    The schema is normalised by ``strictify_schema`` FIRST: ``strict: True`` is set right
    here, so this is the one place responsible for sending a schema that honours it.

    ``pydantic_derived`` (owner report 2026-08-31, code-review-caught regression): a private,
    provider-facing marker, stashed under ``json_schema["_pydantic_derived"]`` and stripped by
    ``claude_cli_provider._json_schema_of`` before use — never a real JSON Schema keyword, never
    sent to any actual API. It tells that ONE provider's own de-strictification step
    (``_destrictify_for_cli``, see its docstring) whether reversing ``strictify_schema``'s
    required+nullable widening is provably safe for THIS schema: safe only when every such
    widened field is KNOWN to have been optional-with-a-default before strictification, which is
    true for a schema `schema_of()` derived from a Pydantic model (this repo's own models never
    author `Optional[X]`/`X | None`), but NOT for the hand-written schema dicts several
    `services/` callers pass to `complete_json` directly — several of which deliberately keep an
    optional field BOTH nullable AND required as their OWN fix for a different prior bug (a
    field merely left out of `required` was a field the model would simply never emit — see
    `services/statements/match_policy.py`'s own incident note). Defaults to False (the CLI keeps
    sending the untouched, fully-strictified schema — its long-standing, unambiguously correct
    behavior for a hand-written schema) so only `complete_model` — the ONE caller that can prove
    the schema is `schema_of()`-derived — opts in."""
    if schema:
        json_schema: dict = {
            "name": "output",
            "strict": True,
            "schema": strictify_schema(schema),
        }
        if pydantic_derived:
            json_schema["_pydantic_derived"] = True
        return {"type": "json_schema", "json_schema": json_schema}
    return {"type": "json_object"}


async def complete_json(
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    max_tokens: int = 2048,
    source: str = "complete_json",
    effort: str = "",
    schema: Optional[dict] = None,
    validate: Optional[Callable[[Any], Optional[str]]] = None,
    max_reasks: int = 1,
    pydantic_derived: bool = False,
) -> Any:
    """Model-agnostic structured-JSON completion — the provider-independent safety net the
    background flows share (mirrors the Instructor/LiteLLM pattern). It:

    1. forces ``response_format: {"type":"json_object"}`` — honored by OpenRouter/GLM/OpenAI/
       xAI/Gemini; the OpenAI provider retries-without on an endpoint that rejects it, and
       Anthropic ignores it (its enforced path is the batch seam's tool_use), so it NEVER
       breaks a flow;
    2. leaves reasoning at the provider default (``effort=""``) — reasoning STAYS ENABLED
       (owner decision); the tolerant parse below extracts ONLY the final JSON, so a
       reasoning model's thinking never reaches the caller regardless. Pass an explicit
       ``effort`` to change the budget;
    3. ALWAYS instructs JSON in the prompt (required by OpenAI json_object; free insurance
       elsewhere);
    4. parses tolerantly (fence-strip + first BALANCED span — survives a reasoning preamble
       AND trailing prose);
    5. optionally ``validate``s the parsed value (a caller predicate returning an error
       string or ``None``) and **reasks** up to ``max_reasks`` times, feeding the error back
       — because even "strict" providers don't enforce every constraint, and GLM enforces
       none.

    Returns the parsed (and, if a validator was given, valid) object/list, or ``None`` when
    it can't get valid JSON within the reask budget — callers MUST handle ``None`` by
    skipping, never by shipping raw/empty output.

    NOTE: this is tier-2 (json_object) + a validate/reask net, which is what GLM (json_object
    only) and every OpenAI-compatible endpoint support. A future tier-1 enhancement can pass
    a native strict ``json_schema`` (OpenAI/xAI/Gemini/Anthropic/OpenRouter-when-supported)
    via ``response_format`` for constrained decoding; the validate/reask net stays regardless.

    ``pydantic_derived``: forwarded to ``_response_format_for`` — see its own docstring. Only
    ``ai.structured.complete_model`` may pass ``True``; every direct ``services/`` caller passing
    its own hand-written ``schema`` dict must leave this at the default ``False``.
    """
    sys = (
        system if "json" in (system + prompt).lower() else f"{system}\n\n{_JSON_DIRECTIVE}".strip()
    )
    response_format = _response_format_for(schema, pydantic_derived=pydantic_derived)
    turn_prompt = prompt
    turn_tokens = max_tokens
    last_err = "no attempts"
    for _ in range(max_reasks + 1):
        try:
            text = await complete(
                turn_prompt,
                system=sys,
                model=model,
                max_tokens=turn_tokens,
                source=source,
                response_format=response_format,
                effort=effort,
            )
        except Exception as exc:
            _note_outcome(source, exc)
            raise
        obj = parse_json(text)
        err = "reply was not valid JSON" if obj is None else (validate(obj) if validate else None)
        if err is None:
            _note_outcome(source, None)
            return obj
        last_err = err
        # A parse failure is often TRUNCATION (the reply hit max_tokens mid-JSON — invalid
        # even in json_object/schema mode): reasking at the SAME budget just re-truncates.
        # So ESCALATE the budget on each retry (×2, capped) — one rule fixes both modes
        # (truncated → room to finish; malformed-but-complete → the error feedback below).
        turn_tokens = min(turn_tokens * 2, _MAX_REASK_TOKENS)
        turn_prompt = (
            f"{prompt}\n\nYour previous reply was invalid ({err}). "
            f"Return ONLY the corrected JSON value — no prose, no code fence."
        )
    # Uniform failure contract: return None (never raw/empty output) and LOG, so every
    # caller can just skip on None while the reason is observable (owner decision:
    # "if it's failing, skip and log the error").
    logger.warning(
        "complete_json: no valid JSON for source=%s after %d attempt(s) (%s)",
        source,
        max_reasks + 1,
        last_err,
    )
    _note_outcome(source, NO_USABLE_ANSWER)
    return None


NO_USABLE_ANSWER = ("no_usable_answer", "")


def _note_outcome(source: str, failure: BaseException | tuple[str, str] | None) -> None:
    """Record one call's outcome for Settings → AI (`core.background_ai_health`). Never lets a
    recording problem change what the call returns or raises."""
    try:
        if failure is None:
            background_ai_health.record_success(source)
        elif isinstance(failure, BaseException):
            background_ai_health.record_failure(source, background_ai_health.classify(failure))
        else:
            background_ai_health.record_failure(source, failure)
    except Exception:
        logger.debug("background_ai_health: could not record an outcome", exc_info=True)


def parse_json(text: str) -> Any:
    """Extract a JSON object/array from a (possibly fenced/chatty) model reply.

    Uses the registered extractor when present; otherwise falls back to a plain
    ``json.loads`` (best-effort, returns None on failure) so a provider-less import path
    still degrades gracefully."""
    fn = _parse_json
    if fn is not None:
        return fn(text)
    try:
        return json.loads(text)
    except Exception:  # pragma: no cover - defensive best-effort fallback
        return None


# ── Batch capability ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BatchOutputSchema:
    """Provider-neutral request for forced structured (JSON) output.

    A caller that needs the model to return JSON matching a schema (instead of free
    text) sets this on ``BatchRequest`` instead of hand-rolling a provider-specific
    tool/function-call payload in ``extra_params``. Each batch adapter translates it
    to its own wire format at submit time — Anthropic forced ``tool_use``
    (``tools``/``tool_choice`` with ``input_schema``), OpenAI/xAI-shape chat
    completions forced function-calling (``tools``/``tool_choice`` with
    ``parameters``) — and unwraps the result back into ``BatchResult.text`` as a
    JSON string, so callers never see the provider-specific shape on either side.
    """

    name: str
    description: str
    schema: dict


@dataclass(frozen=True)
class BatchRequest:
    """A single item to submit in a provider batch call.

    ``output_schema`` is the neutral structured-output contract (see
    ``BatchOutputSchema``) — prefer it over ``extra_params`` for forcing a JSON
    tool/function call, since it works across every adapter. ``extra_params``
    carries call parameters that are genuinely provider-matched (the caller already
    knows which adapter will run and wants to pass that adapter's own wire format
    through verbatim); the provider implementation merges these into the API call
    on top of whatever ``output_schema`` already produced.
    """

    custom_id: str
    prompt: str
    system: str = ""
    model: str = ""
    max_tokens: int = 2048
    source: str = "batch"
    output_schema: Optional["BatchOutputSchema"] = None
    extra_params: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra_params is None:
            object.__setattr__(self, "extra_params", {})


@dataclass(frozen=True)
class BatchResult:
    """Result for one item from a completed batch call."""

    custom_id: str
    text: str | None
    error: str | None = None
    # Provider stop/finish reason (e.g. Anthropic "max_tokens", "end_turn"; OpenAI
    # "length", "stop"). None when the transport doesn't expose one. Lets a caller
    # distinguish a genuinely-empty output from one truncated mid-JSON by the token cap.
    stop_reason: str | None = None


class BatchPending:
    """Sentinel returned by ``collect_batch`` when the provider is still processing."""


# Type aliases for the batch registration hooks.
SubmitBatchFn = Callable[[str, List[BatchRequest]], Awaitable[str]]
CollectBatchFn = Callable[[str], Awaitable[Union[List[BatchResult], BatchPending]]]

_submit_batch: Optional[SubmitBatchFn] = None
_collect_batch: Optional[CollectBatchFn] = None


def register_batch(
    submit: Optional[SubmitBatchFn],
    collect: Optional[CollectBatchFn],
) -> None:
    """Install (or clear) the batch provider hooks.

    Called by ``ai/batch/*`` at startup.  Last registration wins.
    When no provider is registered ``submit_batch``/``collect_batch`` fall back
    to synchronous per-request ``complete()`` execution.
    """
    global _submit_batch, _collect_batch
    _submit_batch = submit
    _collect_batch = collect


async def submit_batch(task: str, requests: list[BatchRequest]) -> str:
    """Submit a batch of requests to the provider.

    Returns an opaque handle string.  When no batch provider is registered,
    executes each request synchronously through ``complete()`` and returns a
    synthetic handle whose ``collect_batch`` yields the results immediately.
    """
    fn = _submit_batch
    if fn is not None:
        return await fn(task, requests)
    return await _sync_fallback_submit(requests)


async def collect_batch(handle: str) -> Union[List[BatchResult], BatchPending]:
    """Poll for batch results.

    Returns a list of ``BatchResult`` when done, or ``BatchPending`` when still
    processing.  Synthetic handles (from the sync fallback path) always resolve
    immediately.
    """
    fn = _collect_batch
    if fn is not None:
        return await fn(handle)
    return await _sync_fallback_collect(handle)


# ── Sync fallback (no batch provider registered) ──────────────────────────────

_SYNC_PREFIX = "sync:"

# Public alias — the prefix is part of the protocol (callers inspect it to route
# sync handles vs real provider handles). Use the public name; the underscore
# variant is kept for backward compat but should not be imported from outside ai/.
SYNC_HANDLE_PREFIX = _SYNC_PREFIX

# Cap the in-flight sync-results dict so a buggy caller that submits without ever
# collecting cannot leak memory indefinitely. When the cap is hit, the oldest entry
# (by insertion order — dict preserves insertion order in Python 3.7+) is evicted.
_SYNC_RESULTS_CAP = 100
_sync_results: dict[str, list[BatchResult]] = {}


async def _sync_fallback_submit(requests: list[BatchRequest]) -> str:
    """Execute each request via ``complete()``/``complete_json()`` and cache the results
    under a synthetic handle.

    A request carrying ``output_schema`` MUST go through ``complete_json`` (forced
    ``response_format`` + validate/reask) rather than plain ``complete()`` — bare
    ``complete()`` has no JSON contract at all, so on a provider with no real batch API
    (this fallback's entire reason to exist — e.g. the live OpenRouter/GLM config, which
    ``ai/batch/routing.py`` always resolves to "sync") the model was previously free to
    reply however it liked. 2026-07-22 incident: ``intel_agent``'s forced ``write_stories``
    schema was silently dropped here, and an unconstrained GLM reply came back as a
    markdown "Lucius Scan" report instead of ``{"stories": [...]}``, failing every scan
    with either an empty result or a JSON-decode error — never a loud one.
    """
    import uuid

    results: list[BatchResult] = []
    for req in requests:
        try:
            if req.output_schema is not None:
                obj = await complete_json(
                    req.prompt,
                    system=req.system,
                    model=req.model,
                    max_tokens=req.max_tokens,
                    source=req.source,
                    schema=req.output_schema.schema,
                )
                # complete_json already logs the reask failure; re-serialize to the JSON
                # string _parse_batch_result expects (None stays None — an honest "empty").
                text = json.dumps(obj) if obj is not None else None
            else:
                text = await complete(
                    req.prompt,
                    system=req.system,
                    model=req.model,
                    max_tokens=req.max_tokens,
                    source=req.source,
                )
            results.append(BatchResult(custom_id=req.custom_id, text=text))
        except Exception as exc:
            results.append(BatchResult(custom_id=req.custom_id, text=None, error=str(exc)))

    handle = f"{_SYNC_PREFIX}{uuid.uuid4().hex}"
    # Evict oldest entry when at capacity to prevent unbounded growth.
    while len(_sync_results) >= _SYNC_RESULTS_CAP:
        _sync_results.pop(next(iter(_sync_results)))
    _sync_results[handle] = results
    return handle


# Public aliases for the sync-fallback helpers — callers outside ai/ (e.g.
# ai/tools/__init__) should use these names so internal refactors don't break them.
sync_fallback_submit = _sync_fallback_submit


async def _sync_fallback_collect(handle: str) -> Union[List[BatchResult], BatchPending]:
    """Return cached sync results for a synthetic handle."""
    if handle.startswith(_SYNC_PREFIX) and handle in _sync_results:
        return _sync_results.pop(handle)
    return BatchPending()


sync_fallback_collect = _sync_fallback_collect
