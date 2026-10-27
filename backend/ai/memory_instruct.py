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
from backend.ai.memory_write import apply_memory_change
from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

INSTRUCTION_MAX_CHARS = 1000


def _candidates(facts: list[dict], instruction: str, topic: str | None = None) -> list[dict]:
    """Every fact, the ones the edit is most likely about first: those in the named topic, then
    those sharing a word with the instruction, then the rest."""
    key = ms.topic_key(topic)
    in_topic = [f for f in facts if key and ms.topic_key(f.get("topic")) == key]
    seen = {f["id"] for f in in_topic}
    words = set(_tokenize(instruction))
    matched = [
        f for f in facts if f["id"] not in seen and words & set(_tokenize(f.get("content", "")))
    ]
    seen |= {f["id"] for f in matched}
    rest = [f for f in facts if f["id"] not in seen]
    return in_topic + matched + rest


def _topic_section(facts: list[dict], topic: str | None) -> str | None:
    """The section the named topic lives in, from the first fact filed under it."""
    key = ms.topic_key(topic)
    return next(
        (f["section"] for f in facts if ms.topic_key(f.get("topic")) == key and f.get("section")),
        None,
    )


def _scope_note(topic: str | None, facts: list[dict]) -> str:
    if not ms.topic_key(topic):
        return ""
    section = _topic_section(facts, topic)
    where = f'the topic "{ms.normalise_topic(topic)}"' + (
        f" in section {section}" if section else ""
    )
    return (
        f"The user opened {where}: unless the instruction says otherwise, change facts there "
        "and file new ones under it.\n\n"
    )


def _prompt(instruction: str, listing: str, today: str, scope: str) -> str:
    return f"""Today is {today}.

The user is editing what you remember about them. Apply their instruction to the memory
below: add what is new, rewrite what it corrects (reuse that fact's id), and remove what it
says is no longer true or should be forgotten. Change nothing the instruction does not touch.
Keep each fact a concise, self-contained statement of at most {ms.FACT_MAX_CHARS} characters in the
third person ("Has a dog named Beans"). Every fact goes in a section ({", ".join(ms.SECTIONS)})
and, except in open_items, under a short topic name (reuse a listed one when it fits). Something
to come back to is an open item and expires after {ms.OPEN_ITEM_DEFAULT_DAYS} days unless an
expires_at is implied.

{scope}Existing memory:
{listing}

Instruction: "{instruction}"

Return ONLY JSON in this exact shape:
{{
  "new":     [{{"content": "...", "section": "you|money|plans|open_items|other", "topic": "...|null", "expires_at": "YYYY-MM-DD|null"}}],
  "updates": [{{"id": "...", "content": "...", "section": "...", "topic": "...|null", "expires_at": "YYYY-MM-DD|null"}}],
  "remove":  ["id", ...]
}}"""


def _scoped_ops(parsed: dict, facts: list[dict], topic: str | None) -> dict:
    """The model's reply as choke ops, new facts defaulting to the topic the user opened."""
    ops = {key: parsed.get(key) for key in ("new", "updates", "remove")}
    if not ms.topic_key(topic):
        return ops
    section = _topic_section(facts, topic)
    scoped = []
    for item in ops["new"] or []:
        if isinstance(item, dict):
            item = {
                **item,
                "topic": item.get("topic") or topic,
                "section": item.get("section") or section,
            }
        scoped.append(item)
    return {**ops, "new": scoped}


async def apply_instruction(instruction: str, topic: str | None = None) -> dict:
    """Apply one typed instruction to memory, optionally scoped to the topic the user opened.

    Returns ``{"added", "updated", "removed", "rejected"}``. Raises MemoryDisabledError while
    memory is off (the same refusal ``remember_fact`` gives), ValidationError for an empty or
    oversized instruction, and ExternalServiceError when the model gives nothing usable — in
    every refusal, nothing was changed.
    """
    text = (instruction or "").strip()
    if not text:
        raise ValidationError("Say what to remember or change.")
    if len(text) > INSTRUCTION_MAX_CHARS:
        raise ValidationError(f"Keep it under {INSTRUCTION_MAX_CHARS} characters.")
    if not memory_writes_enabled():
        raise MemoryDisabledError()

    store = MemoryStore()
    purge_expired(store)
    facts = store.remembered_facts()
    listing = (
        "\n".join(
            f"{f['id']}: [{ms.fact_label(f)}] {f.get('content', '')}"
            for f in _candidates(facts, text, topic)
        )
        or "(none yet)"
    )
    today = user_today().isoformat()
    try:
        reply = await _provider().complete(
            _prompt(text, listing, today, _scope_note(topic, facts)),
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
    result = apply_memory_change(
        _scoped_ops(parsed, facts, topic), source="explicit", strict=False, today=today
    )
    return {key: result[key] for key in ("added", "updated", "removed", "rejected")}


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
