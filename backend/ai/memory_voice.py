"""The house voice for a memory fact, and the guard on rewording an old one into it.

A fact is written about the person — "Splits rent with Priya 50/50", never "User splits…" — with
no "advisor" or "client" in it and no leading "As of <date>:". Facts the old memory wrote break
this, so the sort may reword one it files, and only through ``keeps_facts``: every number and
date, every quoted text, every name and every negation in the old wording must survive, and no
name may appear that the old wording lacked — so a rewording can move words around but never
change what the fact says. The one date it may drop is a leading "As of <date>:", the framing
being removed. ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 9).
"""

from __future__ import annotations

import re
from collections import Counter

from backend.ai import memory_structure as ms

VOICE_RULE = (
    "Write each fact about the person, in the house voice: start with the verb or the thing "
    'itself ("Splits rent with Priya 50/50", "Prefers short answers"), never "User…", "The '
    'user…" or "The client…", and never mention an advisor, a client or Lucius as an actor. Do '
    'not start a fact with "As of <date>:"; when a date matters, keep it inside the fact.'
)

_OFF_VOICE = re.compile(
    r"^\s*as of\b|\buser(?:s|'s|’s)?\b|\badvis[oe]r(?:s|'s|’s)?\b|\bclient(?:s|'s|’s)?\b",
    re.IGNORECASE,
)
_AS_OF_LEAD = re.compile(r"^\s*as of\s+\d[\d./-]*\d\s*[:,]?\s*", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
# A single-quoted span is only one whose quotes are not apostrophes inside words ("User's", "doesn't").
_QUOTED = re.compile(r"(?<!\w)'([^']{2,})'(?!\w)|\"([^\"]{2,})\"|‘([^’]{2,})’|“([^”]{2,})”")
_NEGATION = re.compile(r"\b(?:not|no|never|none|without)\b|n['’]t\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z][\w&*'’.-]*[\w&*]|[A-Za-z]")
# Words a rewording exists to drop: the old framing and the leading "As of".
_DROPPABLE = frozenset(
    {
        "user",
        "users",
        "user's",
        "user’s",
        "advisor",
        "advisors",
        "advisor's",
        "advisor’s",
        "adviser",
        "advisers",
        "client",
        "clients",
        "client's",
        "client’s",
        "as",
        "of",
        "the",
    }
)


def off_voice(text: str) -> bool:
    """True when ``text`` names a user, advisor or client, or opens with "As of"."""
    return bool(_OFF_VOICE.search(text or ""))


def _names(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD.findall(text)
        if any(ch.isupper() for ch in w) and w.lower() not in _DROPPABLE
    }


def _quoted(text: str) -> list[str]:
    return [next(g for g in m if g) for m in _QUOTED.findall(text)]


def keeps_facts(old: str, new: str) -> bool:
    """True when ``new`` is a rewording of ``old`` into the house voice that keeps every number
    and date (character for character, as many times — a leading "As of <date>:" aside), every
    quoted text, every capitalised word (a name, compared without case) and as many negations, and
    brings in no name the old wording lacked; it must itself be in the voice and within the
    length limit."""
    new = " ".join((new or "").split())
    if not new or len(new) > ms.FACT_MAX_CHARS or off_voice(new):
        return False
    old = _AS_OF_LEAD.sub("", old)
    if Counter(_NUMBER.findall(old)) != Counter(_NUMBER.findall(new)):
        return False
    if len(_NEGATION.findall(old)) != len(_NEGATION.findall(new)):
        return False
    if any(not re.search(rf"(?<!\w){re.escape(q)}(?!\w)", new) for q in _quoted(old)):
        return False
    old_words = {w.lower() for w in _WORD.findall(old)}
    return _names(old) <= {w.lower() for w in _WORD.findall(new)} and _names(new) <= old_words


__all__ = ["VOICE_RULE", "keeps_facts", "off_voice"]
