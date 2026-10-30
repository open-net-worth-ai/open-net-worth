"""The memory dialog's own edits: rewrite one fact's text, or change memory from a typed
instruction ("Tell Lucius what to change").

One instruction ("my dog's name is Beans", "I moved out of the Tampines condo", "forget the
car loan") becomes ONE memory-model call that answers in the same {"new", "updates", "remove"}
shape extraction uses, applied through the same write choke (``memory_write``). Everything it
writes is the user's own word, so it is stored as ``explicit``; what the choke refuses comes back
in ``rejected`` and the rest still applies.
"""

from __future__ import annotations

import logging

from backend.ai import memory_structure as ms
from backend.ai.memory import (
    _EXTRACT_SYSTEM,
    _MEMORY_JSON,
    MemoryDisabledError,
    _memory_model,
    _parse_json,
    _provider,
    _tokenize,
    purge_expired,
)
from backend.ai.memory_voice import VOICE_RULE
from backend.ai.memory_write import apply_memory_change
from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

INSTRUCTION_MAX_CHARS = 1000


def _candidates(
    facts: list[dict], instruction: str, topic: str | None = None, section: str | None = None
) -> list[dict]:
    """Every fact, the ones the edit is most likely about first: those in the named topic (of
    the named section, when there is one), then those sharing a word with the instruction, then
    the rest."""
    key = ms.topic_key(topic)
    in_topic = [
        f
        for f in facts
        if key
        and ms.topic_key(f.get("topic")) == key
        and (section is None or f.get("section") == section)
    ]
    seen = {f["id"] for f in in_topic}
    words = set(_tokenize(instruction))
    matched = [
        f for f in facts if f["id"] not in seen and words & set(_tokenize(f.get("content", "")))
    ]
    seen |= {f["id"] for f in matched}
    rest = [f for f in facts if f["id"] not in seen]
    return in_topic + matched + rest


def _scope_section(facts: list[dict], topic: str | None, section: str | None) -> str | None:
    """The section the user opened: the one the page named, else — for a caller that named only
    a topic — the section of the first fact filed under it."""
    named = ms.normalise_section(section)
    if named or section:
        return named
    key = ms.topic_key(topic)
    if not key:
        return None
    return next(
        (f["section"] for f in facts if ms.topic_key(f.get("topic")) == key and f.get("section")),
        None,
    )


def _scope_note(topic: str | None, section: str | None) -> str:
    name = ms.normalise_topic(topic)
    if name:
        where = f'the topic "{name}"' + (f" in section {section}" if section else "")
    elif section:
        where = f"the facts with no topic in section {section}"
    else:
        return ""
    return (
        f"The user opened {where}: unless the instruction says otherwise, change facts there "
        "and file new ones under it.\n\n"
    )


def _prompt(instruction: str, listing: str, today: str, scope: str) -> str:
    return f"""Today is {today}.

The user is editing what you remember about them. Apply their instruction to the memory
below: add what is new, rewrite what it corrects (reuse that fact's id), and remove what it
says is no longer true or should be forgotten. Change nothing the instruction does not touch.
Keep each fact a concise, self-contained statement under {ms.FACT_TARGET_CHARS} characters (never over
{ms.FACT_MAX_CHARS}) in the third person ("Has a dog named Beans"). Every fact goes in a section ({", ".join(ms.SECTIONS)})
and, except in open_items, under a short topic name (reuse a listed one when it fits). Something
decided or in flight over the next two weeks — an agreement about a payment someone owes and how
to book it, something to come back to — is Recent (section open_items), written ready to act on,
and lapses after {ms.OPEN_ITEM_DEFAULT_DAYS} days unless an expires_at is implied.

{scope}Existing memory:
{listing}

Instruction: "{instruction}"

{VOICE_RULE}

Also write "reply": one or two plain sentences to the user, in the second person — what you
changed, or, when nothing needed changing, why (the instruction was unclear, it is already
remembered, it belongs somewhere else such as their profile or Instructions). No ids.

Return ONLY JSON in this exact shape:
{{
  "new":     [{{"content": "...", "section": "you|money|plans|open_items|other", "topic": "...|null", "expires_at": "YYYY-MM-DD|null"}}],
  "updates": [{{"id": "...", "content": "...", "section": "...", "topic": "...|null", "expires_at": "YYYY-MM-DD|null"}}],
  "remove":  ["id", ...],
  "reply":   "..."
}}"""


def _scoped_ops(parsed: dict, topic: str | None, section: str | None) -> dict:
    """The model's reply as choke ops, new facts defaulting to the topic and section the user
    opened."""
    ops = {key: parsed.get(key) for key in ("new", "updates", "remove")}
    name = ms.normalise_topic(topic)
    if not name and not section:
        return ops
    scoped = []
    for item in ops["new"] or []:
        if isinstance(item, dict):
            item = {
                **item,
                "topic": item.get("topic") or name or None,
                "section": item.get("section") or section,
            }
        scoped.append(item)
    return {**ops, "new": scoped}


async def apply_instruction(
    instruction: str, topic: str | None = None, section: str | None = None
) -> dict:
    """Apply one typed instruction to memory, optionally scoped to the topic (and section) the
    user opened. A named ``section`` is used as given — the same topic name can live in two
    sections — and a section that does not exist is refused.

    Returns ``{"added", "updated", "removed", "rejected", "changes", "reply"}`` — ``changes``
    says what changed, one ``{"change", "where", "text", "was"}`` per fact, so the person can see
    it (a count alone told them nothing), and ``reply`` is the model's own sentence on what it did
    or why nothing changed ("" when it gave none). Raises MemoryDisabledError while
    memory is off (the same refusal ``remember_fact`` gives), ValidationError for an empty or
    oversized instruction, and ExternalServiceError when the model gives nothing usable — in
    every refusal, nothing was changed.
    """
    text = (instruction or "").strip()
    if not text:
        raise ValidationError("Say what to remember or change.")
    if len(text) > INSTRUCTION_MAX_CHARS:
        raise ValidationError(f"Keep it under {INSTRUCTION_MAX_CHARS} characters.")
    if section and ms.normalise_section(section) is None:
        raise ValidationError("Pick a section: " + ", ".join(ms.SECTIONS.values()) + ".")
    if not memory_writes_enabled():
        raise MemoryDisabledError()

    store = MemoryStore()
    purge_expired(store)
    facts = store.remembered_facts()
    scope = _scope_section(facts, topic, section)
    listing = (
        "\n".join(
            f"{f['id']}: [{ms.fact_label(f)}] {f.get('content', '')}"
            for f in _candidates(facts, text, topic, scope)
        )
        or "(none yet)"
    )
    today = user_today().isoformat()
    try:
        reply = await _provider().complete(
            _prompt(text, listing, today, _scope_note(topic, scope)),
            system=_EXTRACT_SYSTEM,
            model=_memory_model(),
            max_tokens=1024,
            source="memory_instruct",
            response_format=_MEMORY_JSON,
            effort="none",
        )
    except Exception as exc:
        logger.warning("memory instruction LLM call failed: %s", exc)
        raise ExternalServiceError("Lucius couldn't reach the model. Try again.") from exc

    parsed = _parse_json(reply)
    if not isinstance(parsed, dict):
        logger.warning("memory instruction returned an unparseable reply")
        raise ExternalServiceError("Lucius couldn't make sense of that. Try rewording it.")
    before = {f["id"]: f for f in facts}
    result = apply_memory_change(
        _scoped_ops(parsed, topic, scope), source="explicit", strict=False, today=today
    )
    out = {key: result[key] for key in ("added", "updated", "removed", "rejected")}
    changes = _changes(before, MemoryStore().remembered_facts())
    return {**out, "changes": changes, "reply": _reply(parsed.get("reply"))}


REPLY_MAX_CHARS = 400


def _reply(raw) -> str:
    """The model's sentence to the person, one line and capped; "" when it gave none."""
    if not isinstance(raw, str):
        return ""
    text = " ".join(raw.split())
    return text if len(text) <= REPLY_MAX_CHARS else text[: REPLY_MAX_CHARS - 1].rstrip() + "…"


def _where(fact: dict) -> str:
    section = ms.SECTIONS.get(fact.get("section") or "", "Older memories")
    return f"{section} · {fact['topic']}" if fact.get("topic") else section


def _changes(before: dict[str, dict], after: list[dict]) -> list[dict]:
    """What one instruction changed, fact by fact: added, updated (with its old wording when the
    words changed) and removed, in that order."""
    now = {f["id"]: f for f in after}
    changes: list[dict] = []
    for fid, fact in now.items():
        old = before.get(fid)
        if old is None:
            changes.append({"change": "added", "where": _where(fact), "text": fact["content"]})
        elif (old.get("content"), _where(old)) != (fact.get("content"), _where(fact)):
            was = old.get("content") if old.get("content") != fact.get("content") else None
            changes.append(
                {"change": "updated", "where": _where(fact), "text": fact["content"], "was": was}
            )
    for fid, fact in before.items():
        if fid not in now:
            changes.append({"change": "removed", "where": _where(fact), "text": fact["content"]})
    order = {"added": 0, "updated": 1, "removed": 2}
    return sorted(changes, key=lambda c: order[c["change"]])


def edit_fact(fact_id: str, content: str) -> dict:
    """Rewrite one fact's text exactly as typed; it becomes the user's own (``explicit``).

    Allowed while memory is off, like forgetting: a user correcting what is already stored is
    not a new write channel, and the fact stays dormant until memory is back on. Refused with a
    sentence when the text is empty, over the limit, or carries a full card or ID number.
    """
    store = MemoryStore()
    if store.find_by_id(fact_id) is None:
        raise NotFoundError("unknown memory")
    ops = {"updates": [{"id": fact_id, "content": content}]}
    apply_memory_change(ops, source="explicit", strict=True)
    return store.find_by_id(fact_id)


def delete_topic(section: str, topic: str) -> int:
    """Forget every fact filed under ``topic`` in ``section``; returns how many went.

    Allowed while memory is off, like forgetting one fact. Raises ValidationError for a blank
    topic or a section that has none, and NotFoundError when nothing is filed there.
    """
    if not ms.topic_key(topic):
        raise ValidationError("Name the topic to delete.")
    result = apply_memory_change(
        {"delete_topic": [{"section": section, "topic": topic}]}, source="explicit", strict=True
    )
    if not result["removed"]:
        raise NotFoundError("unknown topic")
    return result["removed"]


def forget_everything() -> int:
    """Forget every stored fact (their topics' summaries go with them); returns how many facts
    went. Allowed while memory is off."""
    ids = [f["id"] for f in MemoryStore().all_facts() if f.get("id")]
    return apply_memory_change({"remove": ids}, source="explicit", strict=False)["removed"]
