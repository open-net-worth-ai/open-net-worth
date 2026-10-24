"""Change memory from a typed instruction — the memory dialog's "Tell Lucius what to change".

One instruction ("my dog's name is Beans", "I moved out of the Tampines condo", "forget the
car loan") becomes ONE memory-model call that answers in the same {"new", "updates", "remove"}
shape extraction uses, applied through the same ``apply_memory_ops``. Everything it writes is
the user's own word, so it is stored as ``explicit``.
"""

from __future__ import annotations

import logging

from backend.ai.memory import (
    _EXTRACT_SYSTEM,
    _MEMORY_JSON,
    EXTRACT_EXISTING_CAP,
    FACT_LISTING_TRUNCATE,
    MemoryDisabledError,
    _memory_model,
    _parse_json,
    _provider,
    _tokenize,
    _truncate,
    apply_memory_ops,
    purge_expired,
)
from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

INSTRUCTION_MAX_CHARS = 1000


def _candidates(facts: list[dict], instruction: str) -> list[dict]:
    """Facts sharing a word with the instruction first, then the newest, up to the cap —
    an edit must SEE the fact it rewrites, however old it is."""
    words = set(_tokenize(instruction))
    matched = [f for f in facts if words & set(_tokenize(f.get("content", "")))]
    seen = {f["id"] for f in matched}
    rest = [f for f in facts if f["id"] not in seen]
    return (matched + rest)[:EXTRACT_EXISTING_CAP]


def _prompt(instruction: str, listing: str, today: str) -> str:
    return f"""Today is {today}.

The user is editing what you remember about them. Apply their instruction to the memory
below: add what is new, rewrite what it corrects (reuse that fact's id), and remove what it
says is no longer true or should be forgotten. Change nothing the instruction does not touch.
Keep each fact a concise, self-contained statement in the third person ("Has a dog named
Beans"). A fact tied to a date is "time_bound" with an expires_at if one is implied; anything
else is "durable".

Existing memory:
{listing}

Instruction: "{instruction}"

Return ONLY JSON in this exact shape:
{{
  "new":     [{{"content": "...", "tier": "durable|time_bound", "expires_at": "YYYY-MM-DD|null"}}],
  "updates": [{{"id": "...", "content": "...", "tier": "durable|time_bound", "expires_at": "YYYY-MM-DD|null"}}],
  "remove":  ["id", ...]
}}"""


async def apply_instruction(instruction: str) -> dict:
    """Apply one typed instruction to memory. Returns {"added", "updated", "removed"}.

    Raises MemoryDisabledError while memory is off (the same refusal ``remember_fact`` gives),
    ValidationError for an empty or oversized instruction, and ExternalServiceError when
    the model gives nothing usable — in every refusal, nothing was changed.
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
    listing = (
        "\n".join(
            f"{f['id']}: [{f.get('tier')}] {_truncate(f.get('content', ''), FACT_LISTING_TRUNCATE)}"
            for f in _candidates(store.active_facts(), text)
        )
        or "(none yet)"
    )
    today = user_today().isoformat()
    try:
        reply = await _provider().complete(
            _prompt(text, listing, today),
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
    return apply_memory_ops(store, parsed, today, source="explicit", guard=False)
