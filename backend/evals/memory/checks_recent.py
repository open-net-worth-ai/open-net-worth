"""Check builders for Recent context (amendment 10). See ``checks`` for the contract."""

from __future__ import annotations

import json
import re

from backend.evals.memory.spec import QUALITY, Check, Outcome


def recent_closed(fact_id: str, name: str) -> Check:
    def test(o: Outcome):
        f = o.fact(fact_id) or {}
        if f.get("state") == "retired" and f.get("retired_reason") == "resolved":
            return None
        return f"state {f.get('state')}, reason {f.get('retired_reason')}"

    return Check(name, QUALITY, test)


def run_mentions(pattern: str, name: str) -> Check:
    """The run's reply or any tool call it made carries ``pattern``."""
    rx = re.compile(pattern, re.IGNORECASE)

    def test(o: Outcome):
        reply = "".join(e.get("text", "") for e in o.events if e.get("type") == "text_delta")
        calls = [
            json.dumps(e.get("input"), ensure_ascii=False)
            for e in o.events
            if e.get("type") == "tool_use"
        ]
        if rx.search(reply) or any(rx.search(c) for c in calls):
            return None
        return "not in the reply or any tool call: " + " ".join(reply.split())[:80]

    return Check(name, QUALITY, test)


def usual_fact_kept(fact_id: str, name: str) -> Check:
    def test(o: Outcome):
        f = o.fact(fact_id) or {}
        before = (o.before.get(fact_id) or {}).get("content")
        if f.get("state") == "live" and f.get("content") == before:
            return None
        return f"now {f.get('state')}: {str(f.get('content'))[:60]}"

    return Check(name, QUALITY, test)


__all__ = ["recent_closed", "run_mentions", "usual_fact_kept"]
