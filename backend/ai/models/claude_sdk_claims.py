"""Which parked Claude Code query a host round continues — the claim half of ``claude_sdk_live``.

A round may continue a parked query only when its messages are exactly "that query's calls, then
ALL of their results, and nothing else", and the query was started with this round's tools,
instructions and model. Anything else — a call still awaiting approval (its result is
missing), a new user message, text merged in after the results (an activated skill), a tool
loaded by ``find_tools``, a folded transcript — closes the parked query and the round starts a
fresh one from the transcript, so ``assistant.run``'s transcript stays the one authority. Same
rules as ``codex_turn_claims`` (ADR ``2026-10-05-codex-turns-stay-open-across-tool-rounds``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

from .claude_sdk_live import LiveQuery, parked

log = logging.getLogger(__name__)


def _digest(value) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def query_key(system: str, tools: list[dict], model: str) -> dict:
    """What a query was STARTED with and cannot change afterwards, part by part, so a refused
    claim can say WHICH changed. Not the effort: the resume after an approval card carries the
    profile's default, not the one the message was sent with, and a query keeps the effort it
    started on rather than restarting (and re-reading its whole prompt) over it."""
    described = sorted(
        (t.get("name") or "", t.get("description") or "", t.get("input_schema") or {})
        for t in tools
    )
    return {
        "tools": _digest(described),
        "system prompt": _digest(system),
        "model": _digest(model),
    }


def _results_only(message: dict) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def claim(messages: list[dict], key: dict) -> Optional[tuple[LiveQuery, list[tuple]]]:
    """The parked query this round continues, with the host's answers — or None."""
    last = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant"),
        None,
    )
    if last is None or not isinstance(messages[last].get("content"), list):
        return None
    ids = [
        b.get("id")
        for b in messages[last]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    queries = {id(parked(i)): parked(i) for i in ids if parked(i) is not None}
    if not ids or not queries:
        return None
    live = next(iter(queries.values()))
    reason = _refusal(messages[last + 1 :], ids, live, key, len(queries))
    if reason:
        log.info("claude_sdk: not continuing the parked query (%s) — fresh query", reason)
        for query in queries.values():
            query.close()
        return None
    results = {b["tool_use_id"]: b for b in messages[last + 1]["content"]}
    return live, [(i, results[i].get("content"), bool(results[i].get("is_error"))) for i in ids]


def _refusal(tail: list[dict], ids: list[str], live: LiveQuery, key: dict, n_queries: int) -> str:
    if n_queries != 1 or set(ids) != live.bound:
        return "the calls are not one parked query's"
    if live.closed or live.loop is not asyncio.get_running_loop():
        return "the parked query is gone"
    if len(tail) != 1 or not _results_only(tail[0]):
        return "the calls' results are not the only thing that follows"
    if {b.get("tool_use_id") for b in tail[0]["content"]} != set(ids):
        return "a result is missing (a call awaiting approval?)"
    changed = [part for part in key if live.key.get(part) != key[part]]
    if changed:
        return "the " + " and ".join(changed) + " changed"
    return ""
