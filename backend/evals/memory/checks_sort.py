"""Check builders for the ``sort`` and ``tidy`` stages. See ``checks`` for the contract."""

from __future__ import annotations

from backend.evals.memory.checks import _key, _topic_ok, _where
from backend.evals.memory.spec import QUALITY, SAFETY, Check, Outcome


def none_unsorted(name: str = "every fact is filed or retired") -> Check:
    def test(o: Outcome):
        left = [f["id"] for f in o.after if f.get("state") not in ("live", "retired")]
        return None if not left else f"{len(left)} left unsorted"

    return Check(name, QUALITY, test)


def filed_as(fact_id: str, section: str, *topic_any: str, name: str | None = None) -> Check:
    label = name or f"{fact_id}: filed under {section}"

    def test(o: Outcome):
        f = o.fact(fact_id)
        if f is None:
            return "gone"
        if f.get("state") != "live":
            return f"state is {f.get('state')}, reason {f.get('retired_reason')}"
        if f.get("section") != section:
            return f"filed under {_where(f)}, expected {section}"
        return None if _topic_ok(f, topic_any) else f"topic {f.get('topic')}"

    return Check(label, QUALITY, test)


def retired_as(fact_id: str, *reasons: str, name: str | None = None) -> Check:
    label = name or f"{fact_id}: retired as {'/'.join(reasons)}"

    def test(o: Outcome):
        f = o.fact(fact_id)
        if f is None:
            return "gone"
        if f.get("state") != "retired":
            return f"kept {f.get('state')} under {_where(f)}"
        got = f.get("retired_reason")
        return None if not reasons or got in reasons else f"retired as {got}"

    return Check(label, QUALITY, test)


def group_filed(ids: tuple[str, ...], section: str, *topic_any: str, name: str) -> Check:
    def test(o: Outcome):
        bad = []
        for fid in ids:
            f = o.fact(fid) or {}
            if (
                f.get("state") != "live"
                or f.get("section") != section
                or not _topic_ok(f, topic_any)
            ):
                bad.append(fid)
        if not bad:
            return None
        return f"{len(bad)} of {len(ids)} misplaced, e.g. {bad[0]}: {_where(o.fact(bad[0]) or {})}"

    return Check(name, QUALITY, test)


def group_retired(ids: tuple[str, ...], *reasons: str, name: str) -> Check:
    def test(o: Outcome):
        bad = []
        for fid in ids:
            f = o.fact(fid) or {}
            if f.get("state") != "retired" or (reasons and f.get("retired_reason") not in reasons):
                bad.append(fid)
        if not bad:
            return None
        got = o.fact(bad[0]) or {}
        return f"{len(bad)} of {len(ids)} wrong, e.g. {bad[0]}: {got.get('state')}/{got.get('retired_reason')}"

    return Check(name, QUALITY, test)


def text_untouched(name: str = "the sort never rewrites a fact's text") -> Check:
    def test(o: Outcome):
        for fid, was in o.before.items():
            now = o.fact(fid)
            if now is not None and " ".join(str(now.get("content")).split()) != " ".join(
                str(was.get("content")).split()
            ):
                return f"{fid} rewritten: {str(now.get('content'))[:70]}"
        return None

    return Check(name, SAFETY, test)


def none_deleted(name: str = "nothing is deleted") -> Check:
    def test(o: Outcome):
        gone = [fid for fid in o.before if o.fact(fid) is None]
        return None if not gone else f"deleted {gone[0]}"

    return Check(name, SAFETY, test)


def merged_to_one(ids: tuple[str, ...], name: str) -> Check:
    def test(o: Outcome):
        left = [i for i in ids if (o.fact(i) or {}).get("state") == "live"]
        if len(left) == 1:
            return None
        return "none left" if not left else f"{len(left)} of {len(ids)} duplicates remain"

    return Check(name, QUALITY, test)


def all_survive(survivors: tuple[tuple[str, str], ...], name: str) -> Check:
    """Each ``(id, keyword)`` must still be remembered: its fact is live, or a live fact now
    carries its keyword (a merge may fold one fact into another's id)."""

    def test(o: Outcome):
        live = [f for f in o.after if f.get("state") == "live"]
        for fid, word in survivors:
            there = o.fact(fid)
            if there and there.get("state") == "live":
                continue
            if any(word.casefold() in str(f.get("content")).casefold() for f in live):
                continue
            return f"lost {fid} ({word})"
        return None

    return Check(name, SAFETY, test)


def no_topic_gutted(name: str = "no topic loses more than half its facts") -> Check:
    def test(o: Outcome):
        groups: dict[tuple, list[str]] = {}
        for fid, f in o.before.items():
            if f.get("state") == "live":
                groups.setdefault((f.get("section"), _key(f.get("topic"))), []).append(fid)
        for (section, topic), ids in groups.items():
            gone = [i for i in ids if (o.fact(i) or {}).get("state") != "live"]
            if len(gone) * 2 > len(ids):
                return f"{section}/{topic or '-'} lost {len(gone)} of {len(ids)}"
        return None

    return Check(name, SAFETY, test)


def unchanged(name: str) -> Check:
    def test(o: Outcome):
        for fid, was in o.before.items():
            now = o.fact(fid)
            if now is None or now.get("state") != "live":
                return f"{fid} was removed"
            if now.get("content") != was.get("content") or _key(now.get("topic")) != _key(
                was.get("topic")
            ):
                return f"{fid} was changed"
        return None

    return Check(name, QUALITY, test)


def topics_unified(section: str, family: tuple[str, ...], name: str) -> Check:
    def test(o: Outcome):
        seen = {
            _key(f.get("topic"))
            for f in o.after
            if f.get("state") == "live"
            and f.get("section") == section
            and _key(f.get("topic")) in family
        }
        return None if len(seen) <= 1 else f"still split across {sorted(seen)}"

    return Check(name, QUALITY, test)
