"""Check builders for the keep test (amendment 9): the house voice, and Open items only when the
person asked. See ``checks`` for the contract."""

from __future__ import annotations

from backend.ai.memory_voice import off_voice
from backend.evals.memory.checks import _show
from backend.evals.memory.spec import QUALITY, Check, Outcome


def in_house_voice(name: str = "no stored fact says user, advisor or As of") -> Check:
    def test(o: Outcome):
        bad = [f for f in o.new_facts() if off_voice(str(f.get("content") or ""))]
        return None if not bad else "off voice: " + _show(bad[0])

    return Check(name, QUALITY, test)


def open_items_added(count: int, name: str) -> Check:
    def test(o: Outcome):
        added = [f for f in o.new_facts() if f.get("section") == "open_items"]
        return (
            None
            if len(added) == count
            else f"{len(added)} open items: " + "; ".join(_show(f) for f in added[:2])
        )

    return Check(name, QUALITY, test)


def filed_in_voice(ids: tuple[str, ...], name: str) -> Check:
    def test(o: Outcome):
        bad = [
            f
            for f in (o.fact(i) or {} for i in ids)
            if f.get("state") == "live" and off_voice(str(f.get("content") or ""))
        ]
        return None if not bad else "kept the old framing: " + _show(bad[0])

    return Check(name, QUALITY, test)


def none_filed_as_open_items(name: str = "no older note is filed as an open item") -> Check:
    def test(o: Outcome):
        bad = [f for f in o.after if f.get("state") == "live" and f.get("section") == "open_items"]
        return None if not bad else "filed as open item: " + _show(bad[0])

    return Check(name, QUALITY, test)


__all__ = ["filed_in_voice", "in_house_voice", "none_filed_as_open_items", "open_items_added"]
