"""
Lucius cross-session memory — extraction and explicit edits.

All functions operate on MemoryStore. Background functions (extract_and_merge, remove_fact)
make a single non-streaming call via the configured provider and are best-effort: callers
swallow exceptions so memory never breaks chat. Every write goes through
``memory_write.apply_memory_change``; ``memory_idle`` decides when extraction runs and
``memory_tidy`` is the daily clean-up. See docs/rfc/lucius-memory.md.
"""

from __future__ import annotations

import json
import logging
import os
import re

from backend.ai.memory_extract_prompt import EXTRACT_SYSTEM, existing_listing, extract_prompt
from backend.ai.memory_structure import topic_key
from backend.ai.memory_write import apply_memory_change
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore, memory_writes_enabled
from backend.db.memory_store import is_expired

logger = logging.getLogger(__name__)


# Overridable via AI_MEMORY_MODEL env var; empty string = use the instance default model.
MEMORY_MODEL = os.getenv("AI_MEMORY_MODEL", "")


def _memory_model() -> str:
    """Task-assigned model (Settings > AI Model > Task models) or the instance default."""
    from backend.db.model_config_store import ModelConfigStore

    store = ModelConfigStore()
    override = store.task_model_override("memory")
    if not override:
        return MEMORY_MODEL
    resolved = store.resolve_override(override)
    return resolved["model"] if resolved else MEMORY_MODEL


# Memory background LLM calls ask for JSON — force json_object mode so a reasoning model
# (GLM/OpenRouter) returns a parseable object. Reasoning stays ENABLED (owner decision); the
# hardened _parse_json below extracts ONLY the JSON, so any thinking preamble is discarded
# rather than stored. The provider degrades gracefully if it rejects the response_format.
_MEMORY_JSON = {"type": "json_object"}


# Max facts to pass to the LLM in forget_fact after prefiltering.
FORGET_CANDIDATE_CAP = 20


def _provider():
    from backend.ai.assistant import get_provider
    from backend.db.model_config_store import ModelConfigStore

    override = ModelConfigStore().task_model_override("memory")
    return get_provider(override)


def _extract_balanced_json(text: str) -> str | None:
    """First BALANCED {...} or [...] span in ``text``, respecting string literals and
    escapes. Unlike a greedy ``{.*}`` regex, this survives a reasoning preamble bleeding
    in FRONT of the JSON (the GLM-5.2 failure mode) AND trailing prose after it."""
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None  # unbalanced (e.g. truncated by max_tokens) → caller treats as failure


def _parse_json(text: str) -> dict | list | None:
    """Pull a JSON object/array out of a model reply that may be fenced, chatty, or
    prefixed with reasoning. Tries: whole text → fenced ```json block → the first
    BALANCED {...}/[...] span (survives a reasoning preamble + trailing prose)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    body = (fenced.group(1) if fenced else text).strip()
    try:
        return json.loads(body)
    except Exception:
        pass
    span = _extract_balanced_json(text)
    if span:
        try:
            return json.loads(span)
        except Exception:
            return None
    return None


def _truncate(text: str, limit: int) -> str:
    """Truncate a string to `limit` chars, appending '…' when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# ── Deterministic purge ────────────────────────────────────────────────────────


def purge_expired(store: MemoryStore | None = None) -> int:
    """Hard-delete facts whose expires_at has passed. Returns count deleted.

    Called before extraction and by the tidy pass so the LLM never sees stale facts and the
    store shrinks deterministically without any LLM spend. Goes through the write choke, so it
    cannot race an extraction or an edit. Uses user_today() (local clock) for consistency with
    MemoryStore.remembered_facts().
    """
    s = store or MemoryStore()
    today = user_today().isoformat()
    expired = [f["id"] for f in s.all_facts() if is_expired(f, today)]
    if expired:
        apply_memory_change({"remove": expired}, source="implicit", strict=False)
        logger.info("purge_expired: removed %d expired facts", len(expired))
    return len(expired)


# ── Explicit edits (Phase 2) ───────────────────────────────────────────────────


class MemoryDisabledError(Exception):
    """Raised by explicit-edit paths when the global memory toggle is OFF."""

    message = "Memory is disabled. Enable it in Settings → Memory before adding or removing facts."


def add_explicit(
    content: str,
    section: str,
    topic: str | None = None,
    expires_at: str | None = None,
    sensitive: bool = False,
) -> dict:
    """Insert a user-requested fact immediately, saved as written. No dedup.

    Raises MemoryDisabledError when the global memory toggle is OFF so the
    calling tool handler can surface a clear refusal instead of silently writing
    a fact that stays dormant (prompt-injection write channel when memory is OFF),
    and ValidationError (a sentence for the user) when the write choke refuses it —
    too long, no such section, a full card or ID number, a bad expiry, or a sensitive fact
    while the sensitive-topics setting is off.
    """
    if not memory_writes_enabled():
        raise MemoryDisabledError()
    item = {"content": content, "section": section, "topic": topic, "expires_at": expires_at}
    if sensitive:
        item["sensitive"] = True
    ops = {"new": [item]}
    return apply_memory_change(ops, source="explicit", strict=True)["created"][0]


async def remove_fact(description: str) -> bool:
    """Find the best-matching fact for a free-text description and delete it.

    Raises MemoryDisabledError when the global memory toggle is OFF so the
    calling tool handler can surface a clear refusal (read-modify path must be
    inert when memory is disabled).
    """
    if not memory_writes_enabled():
        raise MemoryDisabledError()

    store = MemoryStore()
    facts = store.all_facts()
    if not facts:
        return False

    # Deterministic prefilter: find facts whose content has token overlap with the
    # description or contains a substring. This narrows the LLM call to ≤20
    # candidates rather than shipping the full listing (~38K tokens).
    desc_tokens = set(_tokenize(description))
    scored: list[tuple[float, dict]] = []
    for f in facts:
        content = f.get("content", "")
        if description.lower() in content.lower():
            scored.append((1.0, f))
            continue
        fact_tokens = set(_tokenize(content))
        if desc_tokens and fact_tokens:
            overlap = len(desc_tokens & fact_tokens) / len(desc_tokens | fact_tokens)
            if overlap > 0:
                scored.append((overlap, f))

    scored.sort(key=lambda x: -x[0])
    candidates = [f for _, f in scored[:FORGET_CANDIDATE_CAP]]

    # When the prefilter finds nothing, fall back to the most-recent capped listing
    # so the LLM still has a chance to find an imperfect match.
    if not candidates:
        candidates = facts[:FORGET_CANDIDATE_CAP]

    listing = "\n".join(f"{f['id']}: {f.get('content', '')}" for f in candidates)
    prompt = (
        "The user wants to forget a remembered fact. From the list below, return the id of "
        'the single best match, or "none" if nothing matches.\n\n'
        f'User said to forget: "{description}"\n\n'
        f"Facts:\n{listing}\n\n"
        'Return only JSON: {"id": "<id or none>"}'
    )
    try:
        reply = await _provider().complete(
            prompt,
            model=_memory_model(),
            max_tokens=200,
            source="memory_remove",
            response_format=_MEMORY_JSON,
            effort="none",  # mechanical id-match — reasoning tokens are pure cost here
        )
    except Exception as exc:
        logger.warning("remove_fact LLM call failed: %s", exc)
        return False

    parsed = _parse_json(reply) or {}
    target = parsed.get("id") if isinstance(parsed, dict) else None
    if not target or target == "none":
        return False
    return apply_memory_change({"remove": [target]}, source="explicit", strict=False)["removed"] > 0


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens for overlap scoring."""
    return re.findall(r"\b\w+\b", text.lower())


# ── Recall (read-only search) ───────────────────────────────────────────────────
# The prompt block (`prompts._fmt_memory`) carries every live fact; recall also reaches the
# older ones not yet sorted into a section.

RECALL_MAX_LIMIT = 25
RECALL_CONTENT_TRUNCATE = 500

# Words that carry no subject: a recall query is phrased the way the user talks ("what do
# you know about my plans"), and letting "my"/"about" score would rank every fact.
_RECALL_STOPWORDS = frozenset(
    "a an and are about any at be by did do does for from have i in is it know me my of on "
    "or our s tell that the their they this to user was we what when where which who "
    "with you your".split()
)


def _recall_tokens(text: str) -> set[str]:
    return {t for t in _tokenize(text) if t not in _RECALL_STOPWORDS}


def _token_hit(q: str, fact_tokens: set[str]) -> bool:
    # A shared stem of 4+ chars counts ("plan" ~ "plans", "apartment" ~ "apartments").
    if q in fact_tokens:
        return True
    return len(q) >= 4 and any(
        len(t) >= 4 and (t.startswith(q) or q.startswith(t)) for t in fact_tokens
    )


def _recall_score(query: str, q_tokens: set[str], content: str) -> float:
    """Share of the query's words the fact contains; the whole phrase verbatim scores 1."""
    if query.lower() in content.lower():
        return 1.0
    fact_tokens = _recall_tokens(content)
    return sum(1 for q in q_tokens if _token_hit(q, fact_tokens)) / len(q_tokens)


def _recall_row(fact: dict) -> dict:
    return {
        "content": _truncate(fact.get("content", ""), RECALL_CONTENT_TRUNCATE),
        "section": fact.get("section"),
        "topic": fact.get("topic"),
        "source": fact.get("source"),
        "created_at": fact.get("created_at"),
        "expires_at": fact.get("expires_at"),
        "last_used_at": fact.get("last_used_at"),
    }


def recall_facts(query: str | None, limit: int = 10, topic: str | None = None) -> dict:
    """Best-matching ACTIVE facts for ``query`` — relevance first, newest first on a tie. With a
    ``topic`` only that topic's facts are searched, and a query is optional: no query returns the
    topic's facts, newest first.

    Raises MemoryDisabledError when the memory toggle is OFF: a switched-off memory
    surfaces nothing. Reading is not a write, so a demo's seeded facts stay readable —
    the same rule the prompt block follows (``enabled()``, not ``memory_writes_enabled()``).
    Raises ValueError when there is neither a topic nor a query with a searchable word.
    """
    if not MemorySettingsStore().enabled():
        raise MemoryDisabledError()
    query = (query or "").strip()
    q_tokens = _recall_tokens(query)
    wanted = topic_key(topic)
    if not q_tokens and not wanted:
        raise ValueError(
            "give a topic, or a query with at least one specific word, e.g. 'apartment' or 'retire'"
        )
    if query and not q_tokens and wanted:
        query = ""
    limit = max(1, min(int(limit or 10), RECALL_MAX_LIMIT))

    today = user_today().isoformat()
    facts = [
        f
        for f in MemoryStore().remembered_facts(today=today)
        if not (f.get("expires_at") and f["expires_at"] < today)
    ]
    pool = [f for f in facts if topic_key(f.get("topic")) == wanted] if wanted else facts
    if q_tokens:
        scored = [(_recall_score(query, q_tokens, f.get("content", "")), f) for f in pool]
    else:
        scored = [(1.0, f) for f in pool]
    hits = [(s, f) for s, f in scored if s > 0]
    hits.sort(key=lambda sf: (sf[1].get("created_at", ""), sf[1].get("id", "")), reverse=True)
    hits.sort(key=lambda sf: sf[0], reverse=True)
    return {
        "matches": [_recall_row(f) for _, f in hits[:limit]],
        "total_matches": len(hits),
        "total_facts": len(facts),
    }


# ── Extraction cursor ──────────────────────────────────────────────────────────
# Per-session cursor tracking the last history length that was fully extracted.
# Wave 5F (JSON->DB): rides the sessions.extraction_cursor column (backend/db/
# session_store.py) — was a sidecar file alongside the session,
# {session_id}.extraction_cursor.json. Format unchanged: {"last_extracted_len": N}
#
# First run on a legacy (long) conversation: cap the backfill window to at most
# EXTRACT_BACKFILL_CAP messages so a first extraction doesn't send the entire history
# through the LLM (4M+ tokens for a long session).  Each subsequent run only sends the
# new messages since the last cursor (plus EXTRACT_OVERLAP for dedup context).

EXTRACT_BACKFILL_CAP = 200  # ~100 turns = 200 messages on first run for a legacy session
EXTRACT_OVERLAP = 4  # re-include this many already-extracted messages for dedup context


def _load_cursor(session_id: str) -> int:
    """Return the last_extracted_len for the session (0 if never extracted, or if the
    session has no row at all — extraction is best-effort and never creates one)."""
    from backend.db.session_store import SessionStore

    try:
        doc = SessionStore().get_extraction_cursor(session_id)
    except Exception:
        return 0
    if not doc:
        return 0
    try:
        return int(doc.get("last_extracted_len", 0))
    except Exception:
        return 0


def _save_cursor(session_id: str, last_extracted_len: int) -> None:
    """Persist the extraction cursor for the session. Best-effort."""
    from backend.db.session_store import SessionStore

    try:
        SessionStore().set_extraction_cursor(session_id, {"last_extracted_len": last_extracted_len})
    except Exception:
        logger.debug("could not persist extraction cursor", exc_info=True)


def _incremental_slice(history: list[dict], last_extracted_len: int) -> list[dict]:
    """Return the slice of history to send for incremental extraction.

    Returns [] when there are no new messages (history length equals the cursor),
    allowing extract_and_merge to skip the LLM call entirely.

    For a session that has never been extracted (last_extracted_len == 0), caps the
    backfill to the last EXTRACT_BACKFILL_CAP messages to avoid sending entire long
    histories on a first run.  For subsequent runs, sends messages from
    (last_extracted_len - EXTRACT_OVERLAP) onward so a small overlap gives the extractor
    dedup context for facts it already saw.

    If the history appears truncated/rewritten (len < last_extracted_len), the cursor
    is treated as stale and the full capped backfill is used instead.
    """
    n = len(history)
    if last_extracted_len != 0 and last_extracted_len == n:
        # No new messages since the last extraction — nothing to do.
        return []
    if last_extracted_len == 0 or last_extracted_len > n:
        # First run or cursor reset: cap the backfill.
        start = max(0, n - EXTRACT_BACKFILL_CAP)
    else:
        start = max(0, last_extracted_len - EXTRACT_OVERLAP)
    return history[start:]


# ── Auto-extraction (Phase 3) ───────────────────────────────────────────────────


def _conversation_text(history: list[dict]) -> str:
    """Flatten session history to plain user/assistant text, dropping tool noise."""
    lines: list[str] = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict)
                and b.get("type") == "text"
                and not (b.get("text") or "").startswith("<turn_context>")
            ]
            text = " ".join(p.strip() for p in parts if p.strip())
        else:
            text = ""
        if text:
            lines.append(f"{role.upper()}: {text}")
    return "\n".join(lines)


_TRIVIAL_MSG_RE = re.compile(
    r"^(?:ok(?:ay)?|k+|thanks?|thank\s+you|thx|ty|yes|yep|yeah|no|nope|nah|sure|great|"
    r"good|nice|cool|perfect|awesome|got\s+it|sounds\s+good|go\s+(?:ahead|on)|continue|"
    r"proceed|do\s+it|please(?:\s+do)?|hi|hello|hey|yo|lol|haha|hm+)[\s!.?,]*$",
    re.IGNORECASE,
)


def _only_trivial_new_messages(history: list[dict], last_extracted_len: int) -> bool:
    """True when the strictly-new messages carry nothing worth extracting.

    Cost fix 2026-07-11: a bare acknowledgment ("ok", "thanks", "do it") still paid a
    full ~9.6k-token extraction call. Judged ONLY on the never-extracted portion (the
    dedup overlap is excluded) and only for user text — an all-assistant addition is
    also skipped (extraction memorializes what the USER said/decided; the user side
    of any decision was already in an earlier extracted turn). Conservative: any
    non-trivial user text → extract as before.
    """
    for msg in history[last_extracted_len:]:
        if msg.get("role") != "user":
            continue
        text = _conversation_text([msg]).partition(":")[2].strip()
        if not text:
            continue
        if len(text) >= 4 and not _TRIVIAL_MSG_RE.match(text):
            return False
    return True


_EXTRACT_SYSTEM = EXTRACT_SYSTEM


def _profile_instructions() -> str:
    """The user's Instructions for Lucius (profile notes), shown to the extractor so it does
    not store what they already say."""
    from backend.services.profile import read_profile_raw

    notes = read_profile_raw().get("notes")
    return notes if isinstance(notes, str) else ""


def _extraction_ops(parsed: dict) -> dict:
    ops = {key: parsed.get(key) for key in ("new", "updates", "remove")}
    mentioned = parsed.get("mentioned")
    if isinstance(mentioned, list):
        ops["touch"] = {"mentioned": mentioned}
    return ops


async def extract_and_merge(history: list[dict], session_id: str | None = None) -> None:
    """Extract facts from the new portion of a conversation and merge into the store.

    When session_id is provided, the extraction cursor tracks the last extracted history
    length so only newly-added messages are sent to the LLM (plus a small overlap for dedup
    context); it advances only after a pass that succeeded, so a failed one is retried by the
    next idle sweep. First run on a legacy long session: the backfill is capped at
    EXTRACT_BACKFILL_CAP messages.

    If the global memory toggle is OFF, this is a no-op.
    """
    if not memory_writes_enabled():
        return

    last_extracted_len = _load_cursor(session_id) if session_id else 0
    slice_history = _incremental_slice(history, last_extracted_len)
    if not slice_history:
        return

    # Trivial-turn skip: pure acknowledgments never justify an extraction call.
    # Advance the cursor so the trivial tail doesn't accumulate into a later slice.
    # (last_extracted_len > len(history) = stale/truncated cursor — that's the
    # reset-backfill path, never a skip candidate.)
    if (
        session_id
        and 0 < last_extracted_len <= len(history)
        and _only_trivial_new_messages(history, last_extracted_len)
    ):
        _save_cursor(session_id, len(history))
        return

    convo = _conversation_text(slice_history)
    if not convo.strip():
        if session_id:
            _save_cursor(session_id, len(history))
        return

    store = MemoryStore()
    purge_expired(store)

    today = user_today().isoformat()
    prompt = extract_prompt(
        today, _profile_instructions(), existing_listing(store.remembered_facts()), convo
    )

    try:
        reply = await _provider().complete(
            prompt,
            system=_EXTRACT_SYSTEM,
            model=_memory_model(),
            max_tokens=2048,
            source="memory_extract",
            response_format=_MEMORY_JSON,
            effort="none",  # structured extraction — no reasoning needed, don't pay for it
        )
    except Exception as exc:
        logger.warning("memory extraction LLM call failed: %s", exc)
        return

    parsed = _parse_json(reply)
    if not isinstance(parsed, dict):
        logger.warning("memory extraction returned unparseable reply")
        return

    apply_memory_change(_extraction_ops(parsed), source="implicit", strict=False, today=today)

    if session_id:
        _save_cursor(session_id, len(history))
