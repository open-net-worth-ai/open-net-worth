"""A model step's Ask calls are judged TOGETHER, in one request, before any is dispatched.

A step with N Ask calls used to cost N sequential judge requests (3–5s each on a CLI model, and
the judge could never see that a read of private data and a send to the outside came in the same
breath). ``judge_step`` runs once per step, collects the calls that would reach the judge, asks
``auto_judge.judge_calls`` about all of them at once, and PARKS each verdict for ``prepare``
(``auto_judge``) to pick up when the call is dispatched. ADR
``2026-10-10-auto-approval-is-a-per-call-judge`` (amendment 2026-10-13).

## An optimisation layer, never a gap

* Which calls are judged is ``auto_judge.will_be_judged`` — the same predicate ``prepare`` asks —
  fed the arguments ``registry.dispatch`` will really hand the judge (``judgeable_input``). A
  call that is Block, Always allow, Always-allowed in the Automation, Skip, Manual or refused
  before the gate is never sent.
* 0 or 1 such calls: nothing happens here and the single-call path runs exactly as before.
  More than ``MAX_BATCH``: split into even chunks, one request each.
* A parked verdict is bound to ``(scope, tool_use_id)``, the tool, and a fingerprint of the
  arguments AND the trusted consequence line. ``prepare`` consumes it once, and only if all of
  that still matches what it is asked about; anything else is judged alone. A call this module
  never saw (any other path into ``dispatch``) is simply judged alone.
* The scope is the conversation's / Automation's session, read from the approval context the
  turn bound before the loop started — not from a ContextVar set inside the loop, which a
  consumer iterating the turn from several tasks would not see.
* Every failure here parks nothing, and no verdict reads as ``gate`` (a card).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from backend.ai import auto_judge
from backend.ai.auto_judge import JudgeVerdict
from backend.core import mcp_external

logger = logging.getLogger(__name__)

#: The most calls one judge request carries. A bigger step is split into even chunks.
MAX_BATCH = 8


@dataclass(frozen=True)
class _Parked:
    tool: str
    fingerprint: str
    verdict: JudgeVerdict


# (scope, tool_use_id) -> the verdict judged for that call. One-shot: ``take_parked`` pops it, and
# the next step for the scope drops whatever was never taken (a call a discovery gate bounced).
_parked: dict[tuple[str, str], _Parked] = {}


def _scope() -> Optional[str]:
    """The session whose approval mode governs this turn — an Automation's first, then the
    conversation's, the same order ``auto_judgment_needed`` reads — or ``None`` with no mode."""
    from backend.core.automation_runner import get_automation_approval
    from backend.core.conversation_approval import get_conversation_approval

    context = get_automation_approval() or get_conversation_approval()
    return (context.session_id or None) if context is not None else None


def _fingerprint(name: str, args: dict) -> str:
    """What a verdict was judged against: the tool, its arguments as dispatch hands them over,
    and the trusted consequence line (which reads STORED state an earlier call in the step may
    have changed — a changed fingerprint sends the call back to the single-call judge)."""
    blob = json.dumps(
        [name, args or {}, auto_judge.automation_consequence(name, args or {})],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def take_parked(tool_use_id: str, name: str, input_data: dict) -> Optional[JudgeVerdict]:
    """The verdict the step's batch judged for THIS call, consumed; ``None`` when there is none or
    it no longer matches the call. Never raises."""
    try:
        scope = _scope()
        parked = _parked.pop((scope, tool_use_id), None) if scope else None
        if parked is None:
            return None
        if parked.tool != name or parked.fingerprint != _fingerprint(name, input_data):
            return None
        return parked.verdict
    except Exception:  # noqa: BLE001 — no parked verdict is the single-call judge
        logger.warning("auto judge: could not read a parked verdict for %s", name, exc_info=True)
        return None


def _drop_scope(scope: str) -> None:
    for key in [key for key in _parked if key[0] == scope]:
        del _parked[key]


def _candidates(calls: Sequence[tuple[str, str, dict]]) -> list[tuple[str, str, dict]]:
    """``(tool_use_id, name, args)`` for each call that ``prepare`` would put to the judge."""
    from backend.ai.tools.registry import judgeable_input
    from backend.core.tool_use_context import get_tool_use_id, set_tool_use_id

    bound = get_tool_use_id()
    found: list[tuple[str, str, dict]] = []
    try:
        for tool_use_id, name, raw in calls:
            args = (
                raw if mcp_external.is_external(name) else judgeable_input(name, raw, tool_use_id)
            )
            if args is None:
                continue
            set_tool_use_id(tool_use_id)  # the predicate asks "can a card be staged for THIS id?"
            if auto_judge.will_be_judged(name, args):
                found.append((tool_use_id, name, args))
    finally:
        set_tool_use_id(bound)
    return found


def _chunks(items: list, size: int) -> list[list]:
    """Even chunks of at most ``size`` (9 -> 5 + 4, never 8 + 1): a lone call would go back to
    the single-call path and cost a whole request of its own."""
    count = math.ceil(len(items) / size)
    each = math.ceil(len(items) / count)
    return [items[i : i + each] for i in range(0, len(items), each)]


async def judge_step(calls: Sequence[tuple[str, str, dict]]) -> None:
    """Judge a model step's calls together and park each verdict. ``calls`` are the step's
    ``(tool_use_id, name, input)`` in order. Never raises (except cancellation); nothing to
    judge, or a failure, parks nothing."""
    try:
        scope = _scope()
        if scope is None:
            return
        _drop_scope(scope)
        pending = _candidates(calls)
        if len(pending) < 2:
            return
        for chunk in _chunks(pending, MAX_BATCH):
            verdicts = await auto_judge.judge_calls([(name, args) for _, name, args in chunk])
            for (tool_use_id, name, args), verdict in zip(chunk, verdicts):
                _parked[(scope, tool_use_id)] = _Parked(name, _fingerprint(name, args), verdict)
            logger.info(
                "auto judge: batch of %d (%s)",
                len(chunk),
                ", ".join(
                    f"{verdict.verdict} {name}" for (_, name, _), verdict in zip(chunk, verdicts)
                ),
            )
    except Exception:  # noqa: BLE001 — batching is an optimisation; calls are judged alone instead
        logger.warning("auto judge: batching failed; calls will be judged alone", exc_info=True)
