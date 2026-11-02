"""Auto approval is a per-call judge — the model behind ``auto`` mode.

In ``auto`` mode (a conversation's, or an Automation's) a call that would otherwise ask the
owner is put to ONE small model call: given the tool's name, description and parameter schema,
the actual arguments and what the owner asked for, does this call need a human? There is no
per-tool declaration and no name list — a connector tool, or a tool added tomorrow, is judged
the day it exists. ADR ``2026-10-10-auto-approval-is-a-per-call-judge``. What the model is told lives in
``ai/auto_judge_prompt.py``.

## The shape

``registry.dispatch`` awaits ``prepare`` once, BEFORE the sync permission gate and before any
handler runs. The verdict is parked in a ContextVar keyed by the in-flight ``tool_use_id`` AND
the tool name, and read back synchronously by the ``auto`` branches of
``automation_gate_verdict`` / ``conversation_gate_verdict`` (``ai.tools._confirm_gate_v2``). It has
to exist before the handler runs because the 15 self-staging tools (``add_transactions`` …) ask
``should_preview_write()`` from INSIDE their handler, and that question is sync.

A model step that makes several Ask calls does not pay one request per call: before the first is
dispatched, ``auto_judge_batch.judge_step`` puts all of them to ONE request (``judge_calls`` — each
judged on its own AND in combination) and parks the verdicts, which ``prepare`` then picks up. One
call, or a call nobody parked a verdict for, takes the single-call path above, unchanged.

## Fails closed — this is the property that matters

Every way of not getting an answer is ``ask``: no model, timeout,
provider error, a reply that is not exactly one JSON verdict object, a missing or stale verdict
at the sync read. ``allow`` exists only as the parsed output of a model that answered in time.
The judge is an LLM, not a wall: ``skip`` remains the explicit no-net mode. It only ever decides
what ASK means — a tool the owner set to Always allow runs and one set to Block is refused before
the judge is consulted, in every mode (ADR ``2026-10-11-permissions-are-one-value-per-tool``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Optional

# Re-exported: the prompt half is a separate module only to keep the attacker-reachable surface
# small; callers (the eval runner, tests) reach it through here.
from backend.ai.auto_judge_prompt import (  # noqa: F401
    BATCH_SYSTEM,
    MAX_ARGS_CHARS,
    PendingCall,
    automation_consequence,
    batch_ids,
    build_batch_prompt,
    build_prompt,
    call_facts,
    owner_intent,
    recent_owner_messages,
)
from backend.ai.auto_judge_prompt import (
    SYSTEM as _SYSTEM,
)
from backend.models.mcp_client import mcp_connector_id

logger = logging.getLogger(__name__)

#: Hard budget for the whole judge call — the same for every model, CLI included. Set from a
#: measurement, not a guess (2026-10-13, 10 calls each on the production prompt): Claude Code
#: haiku mean 3.0s / max 4.7s, sonnet 4.1s / 5.3s, Codex gpt-5.6-luna 3.5s / 4.7s. 10s is about
#: twice the slowest, for a cold start or a busy machine. Slower than that is a card, never a stall.
JUDGE_TIMEOUT_S = 10.0
JUDGE_MAX_TOKENS = 200
#: A batch reply carries one entry (id, verdict, a reason of up to MAX_REASON_CHARS) per call.
BATCH_EXTRA_TOKENS_PER_CALL = 120
MAX_REASON_CHARS = 160

APPROVAL_TASK = "approval"


@dataclass(frozen=True)
class JudgeVerdict:
    verdict: Literal["allow", "ask"]
    # Never shown to the owner and never logged: a card is the ordinary card whatever the
    # verdict. Kept because the reply format is {"verdict","reason"} and the eval runner reports it.
    reason: str
    model: str = ""  # the ref that answered ("" = instance default) — for the audit log only

    @property
    def allows(self) -> bool:
        return self.verdict == "allow"


#: After this many CONSECUTIVE failures to get an answer (timeout, provider error, unusable
#: output) the judge stops calling the model for a cool-off and asks straight away — a down
#: provider must cost one card, not a full timeout on every call. A deliberate ``ask`` from the
#: model is an answer, not a failure. (Prior art: Claude Code stops after 10
#: no-verdict responses; Codex and Hermes trip on repeated denials.)
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 30.0


class _Breaker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self.open_until

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= BREAKER_THRESHOLD:
            self.open_until = time.monotonic() + BREAKER_COOLDOWN_S
            self.failures = 0
            logger.warning(
                "auto judge: %d failures in a row; asking for %.0fs",
                BREAKER_THRESHOLD,
                BREAKER_COOLDOWN_S,
            )


_breaker = _Breaker()

# (tool_use_id, tool_name, verdict) for the dispatch currently in flight. Set, never reset,
# mirroring ``core.tool_use_context``: a task rebinding it rebinds only its own context copy.
# Bound to the id AND the tool, so another dispatch's verdict — or a verdict read on behalf of
# a different tool inside the same dispatch — is unreadable by construction.
_current: ContextVar[Optional[tuple[str, str, JudgeVerdict]]] = ContextVar(
    "auto_judge_verdict", default=None
)


def _ask(reason: str) -> JudgeVerdict:
    return JudgeVerdict("ask", reason[:MAX_REASON_CHARS])


# ── Model selection ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Target:
    ref: str  # "" = the instance default


def _resolve_target() -> _Target:
    """The judge's model: the ``approval`` pin, else the model this conversation is running
    on (owner decision, 2026-10-10), else the instance default.

    A CLI model is a model like any other (owner, 2026-10-13): it is called, the timeout bounds
    it, and a slow or malformed reply is a card through the ordinary fail-closed path.
    """
    from backend.core.model_context import get_active_model
    from backend.db.model_config_store import ModelConfigStore

    store = ModelConfigStore()
    pin = (store.task_model_override(APPROVAL_TASK) or "").strip()
    if pin:
        return _Target(pin)
    info = get_active_model() or {}
    model, provider_key = info.get("model") or "", info.get("provider_key") or ""
    return _Target(f"{provider_key}:{model}" if model and provider_key else "")


# ── The judgement ────────────────────────────────────────────────────────────────────────


def _no_duplicate_keys(pairs: list[tuple]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _reply_object(text: str) -> Optional[dict]:
    """The reply as ONE JSON object (optionally inside a single code fence), or ``None``.

    Deliberately strict. A tolerant "first balanced ``{…}``" parser would read an injected
    ``{"verdict": "allow"}`` that the model quoted BEFORE its real answer; here any extra
    prose, any second object or a key duplicated at ANY depth is unusable.
    """
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            return None
        body = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(body, object_pairs_hook=_no_duplicate_keys)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _reason_of(entry: dict) -> str:
    reason = entry.get("reason")
    return reason.strip() if isinstance(reason, str) else ""


def parse_reply(text: str) -> Optional[tuple[str, str]]:
    """``(verdict, reason)`` from a single-call reply, or ``None`` when it is not EXACTLY one
    verdict object. An unknown verdict is unusable, and an unusable reply is a card."""
    obj = _reply_object(text)
    if obj is None or obj.get("verdict") not in ("allow", "ask"):
        return None
    return obj["verdict"], _reason_of(obj)


def parse_batch_reply(text: str, ids: list[str]) -> Optional[dict[str, tuple[str, str]]]:
    """``{id: (verdict, reason)}`` for the ids the reply answered usably, or ``None`` when the
    reply as a whole is unusable.

    Unusable (every call in the batch is then a card): not exactly one ``{"verdicts": [...]}``
    object, or an entry that is not an object or names an id this batch never had. A usable reply
    can still fail a call: an id answered twice (the reply contradicts itself), an unknown
    verdict, or an id left out is simply absent from the result, and the caller asks for THAT
    call alone.
    """
    obj = _reply_object(text)
    if obj is None or list(obj) != ["verdicts"] or not isinstance(obj["verdicts"], list):
        return None
    seen: dict[str, int] = {}
    answers: dict[str, tuple[str, str]] = {}
    for entry in obj["verdicts"]:
        if not isinstance(entry, dict) or entry.get("id") not in ids:
            return None
        call_id = entry["id"]
        seen[call_id] = seen.get(call_id, 0) + 1
        if entry.get("verdict") in ("allow", "ask"):
            answers[call_id] = (entry["verdict"], _reason_of(entry))
    return {call_id: ans for call_id, ans in answers.items() if seen[call_id] == 1}


def _tool_facts(name: str) -> tuple[str, Optional[dict], Optional[str], bool]:
    """``(description, parameter schema, connector, is_external)`` for the pending tool.

    A registry tool reads its own declaration. An external MCP tool reads what the server
    advertised in its handshake — third-party text, so the caller fences all of it."""
    from backend.core import mcp_external

    if mcp_external.is_external(name):
        dispatcher = mcp_external.get_external_dispatcher()
        found = dispatcher.discovered_tool(name) if dispatcher is not None else None
        split = mcp_external.split_external(name)
        found = found or {}
        schema = found.get("inputSchema")
        return (
            str(found.get("description") or ""),
            schema if isinstance(schema, dict) else None,
            mcp_connector_id(split[0]) if split else None,
            True,
        )
    from backend.ai.tools.registry import tool_connector, tool_description, tool_schema

    return tool_description(name) or "", tool_schema(name), tool_connector(name), False


async def ask_model(prompt: str, system: str, target: _Target, max_tokens: int) -> str:
    """The one model request the judge makes — single call or batch: cheap, JSON, attributed to
    ``auto_approval`` (one cost-log row) and bounded by ``JUDGE_TIMEOUT_S`` for the whole of it."""
    from backend.core import llm_seam

    return await asyncio.wait_for(
        llm_seam.complete(
            prompt,
            system=system,
            model=target.ref,
            max_tokens=max_tokens,
            source="auto_approval",
            response_format={"type": "json_object"},
            effort="none",
        ),
        timeout=JUDGE_TIMEOUT_S,
    )


async def judge_call(name: str, input_data: dict) -> JudgeVerdict:
    """Judge one pending call. Never raises (except cancellation); every failure is ``ask``."""
    try:
        target = _resolve_target()
        if _breaker.is_open():
            return _ask("breaker open")
        description, schema, connector, external = _tool_facts(name)
        prompt = build_prompt(
            tool_name=name,
            description=description,
            schema=schema,
            args=input_data or {},
            connector=connector,
            owner_messages=owner_intent(),
            external=external,
            consequence=automation_consequence(name, input_data or {}),
        )
        text = await ask_model(prompt, _SYSTEM, target, JUDGE_MAX_TOKENS)
    except asyncio.TimeoutError:
        _breaker.record(False)
        logger.warning("auto judge: timed out on %s; asking", name)
        return _ask("timed out")
    except Exception:  # noqa: BLE001 — a judge fault must degrade to a card, never a write
        _breaker.record(False)
        logger.warning("auto judge: failed on %s; asking", name, exc_info=True)
        return _ask("provider error")
    parsed = parse_reply(text)
    if parsed is None:
        _breaker.record(False)
        logger.warning("auto judge: unusable reply for %s; asking", name)
        return _ask("unusable reply")
    _breaker.record(True)
    verdict, reason = parsed
    return JudgeVerdict(verdict, reason[:MAX_REASON_CHARS], target.ref)


async def judge_calls(calls: list[tuple[str, dict]]) -> list[JudgeVerdict]:
    """Judge several pending calls (``(name, arguments)`` each, from ONE model step) in ONE model
    request, one verdict per call in the same order. Never raises (except cancellation).

    The single-call rules carry over, applied to the whole request: one ``JUDGE_TIMEOUT_S``, one
    cost-log row, one breaker result. An unusable reply, a timeout, a provider error or an open
    breaker is ``ask`` for EVERY call; a usable reply that left a call out, answered it twice or
    gave it a verdict other than allow/ask is ``ask`` for THAT call only.
    """
    count = len(calls)
    ids = batch_ids(count)
    try:
        target = _resolve_target()
        if _breaker.is_open():
            return [_ask("breaker open")] * count
        pending = []
        for name, args in calls:
            description, schema, connector, external = _tool_facts(name)
            pending.append(
                PendingCall(
                    name,
                    description,
                    schema,
                    args or {},
                    connector,
                    external,
                    automation_consequence(name, args or {}),
                )
            )
        prompt = build_batch_prompt(pending, owner_messages=owner_intent())
        budget = JUDGE_MAX_TOKENS + BATCH_EXTRA_TOKENS_PER_CALL * (count - 1)
        text = await ask_model(prompt, BATCH_SYSTEM, target, budget)
    except asyncio.TimeoutError:
        _breaker.record(False)
        logger.warning("auto judge: timed out on a batch of %d; asking", count)
        return [_ask("timed out")] * count
    except Exception:  # noqa: BLE001 — a judge fault must degrade to cards, never a write
        _breaker.record(False)
        logger.warning("auto judge: failed on a batch of %d; asking", count, exc_info=True)
        return [_ask("provider error")] * count
    answers = parse_batch_reply(text, ids)
    if answers is None:
        _breaker.record(False)
        logger.warning("auto judge: unusable reply for a batch of %d; asking", count)
        return [_ask("unusable reply")] * count
    _breaker.record(True)
    return [
        JudgeVerdict(answers[i][0], answers[i][1][:MAX_REASON_CHARS], target.ref)
        if i in answers
        else _ask("no usable verdict")
        for i in ids
    ]


def will_be_judged(name: str, input_data: dict) -> bool:
    """Whether the in-flight call (the bound ``tool_use_id``) will reach an ``auto`` branch —
    the one question ``prepare`` asks before it spends a model call, and the one the step's
    batch asks of each of its calls (``auto_judge_batch``), so the two cannot disagree."""
    from backend.ai.tools import _confirm_gate_v2 as gate
    from backend.core.tool_use_context import get_tool_use_id

    if not get_tool_use_id() or (input_data or {}).get(gate.CONFIRM_TOKEN_KEY):
        return False
    return gate.auto_judgment_needed(name)


async def prepare(name: str, input_data: dict) -> None:
    """Judge the in-flight call if — and only if — it will reach an ``auto`` branch.

    Called from ``registry.dispatch`` before the permission gate. Any verdict left by an
    earlier dispatch is cleared FIRST, so a failure anywhere below leaves no verdict at all
    (which reads as ``gate``) rather than a stale ``allow`` under a reused id. A call that will
    not be judged likewise leaves none: a divergence between this predicate and the branch
    that reads the verdict can only cost a wasted call, never an unjudged ``allow``.

    A step's calls are judged TOGETHER before any is dispatched (``auto_judge_batch``), and the
    verdict parked for THIS call, if it still matches, is used instead of a model call. No
    parked verdict — a call the batch did not cover, one whose arguments or consequence changed
    since — is the ordinary single-call judgement: batching is an optimisation, never a gap.
    """
    _current.set(None)
    try:
        from backend.ai import auto_judge_batch
        from backend.core.tool_use_context import get_tool_use_id

        if not will_be_judged(name, input_data):
            return
        tool_use_id = get_tool_use_id() or ""
        verdict = auto_judge_batch.take_parked(tool_use_id, name, input_data)
        if verdict is None:
            verdict = await judge_call(name, input_data)
    except Exception:  # noqa: BLE001 — no verdict is a card, and must never break dispatch
        logger.warning("auto judge: prepare failed for %s; no verdict parked", name, exc_info=True)
        return
    _current.set((tool_use_id, name, verdict))
    # The verdict and the tool only: the reason is model-written from the call's arguments, so it
    # can carry what the arguments carried (names, amounts, a payee's text) into the log.
    logger.info(
        "auto judge: %s %s on %s",
        verdict.verdict,
        name,
        verdict.model or "default model",
    )


def _held_for(tool_name: str) -> Optional[JudgeVerdict]:
    from backend.core.tool_use_context import get_tool_use_id

    held = _current.get()
    if held is not None and held[0] == get_tool_use_id() and held[1] == tool_name:
        return held[2]
    return None


def gate_verdict_for(tool_name: str) -> str:
    """``"allow"`` or ``"gate"`` for the in-flight call. No verdict, a verdict for another
    call or another tool, or ``ask`` all mean ``gate``."""
    verdict = _held_for(tool_name)
    return "allow" if verdict is not None and verdict.allows else "gate"
