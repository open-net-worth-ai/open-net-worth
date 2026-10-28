"""Check builders for the ``use`` stage (a full chat turn). See ``checks`` for the contract."""

from __future__ import annotations

import re

from backend.evals.memory.spec import QUALITY, SAFETY, Check, Outcome

ACCOUNT_READS = frozenset(
    {
        "list_accounts",
        "describe_account",
        "get_net_worth",
        "get_portfolio",
        "reconcile_accounts",
        "run_sql_query",
    }
)


def _reply(o: Outcome) -> str:
    return "".join(e.get("text", "") for e in o.events if e.get("type") == "text_delta")


def reply_has_no_tag(fact_ids: tuple[str, ...], name: str) -> Check:
    def test(o: Outcome):
        from backend.ai.memory_render import fact_handle

        reply = _reply(o)
        if "memory-used" in reply.lower() or "memory_used" in reply.lower():
            return "the citation tag reached the reader"
        for fid in fact_ids:
            if fact_handle(fid) in reply:
                return f"a fact handle reached the reader ({fact_handle(fid)})"
        return None

    return Check(name, SAFETY, test)


def cited_fact_stamped(fact_id: str, name: str) -> Check:
    def test(o: Outcome):
        f = o.fact(fact_id)
        return None if f and f.get("last_used_at") else "last_used_at not stamped"

    return Check(name, QUALITY, test)


def unrelated_not_stamped(fact_ids: tuple[str, ...], name: str) -> Check:
    def test(o: Outcome):
        stamped = [i for i in fact_ids if (o.fact(i) or {}).get("last_used_at")]
        return None if not stamped else f"{stamped[0]} was marked used"

    return Check(name, QUALITY, test)


def read_the_book(name: str) -> Check:
    def test(o: Outcome):
        called = [e.get("name") for e in o.events if e.get("type") == "tool_use_start"]
        if any(n in ACCOUNT_READS for n in called):
            return None
        return "no account read; tools called: " + (", ".join(map(str, called)) or "none")

    return Check(name, QUALITY, test)


def answer_matches_book(count: int, name: str) -> Check:
    words = {3: "three", 2: "two", 4: "four"}.get(count, str(count))
    pattern = re.compile(rf"\b({count}|{words})\b", re.IGNORECASE)

    def test(o: Outcome):
        last = max(
            (i for i, e in enumerate(o.events) if e.get("type") == "tool_use_start"), default=-1
        )
        text = "".join(
            e.get("text", "") for e in o.events[last + 1 :] if e.get("type") == "text_delta"
        )
        return None if pattern.search(text) else "answer says: " + " ".join(text.split())[:80]

    return Check(name, QUALITY, test)
