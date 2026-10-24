"""
Lucius cross-session memory — extraction, consolidation, and explicit edits.

All functions operate on MemoryStore (backend/memory.json). Background functions
(extract_and_merge, consolidate, remove_fact) make a single non-streaming Claude
call via the configured provider and are best-effort: callers swallow exceptions
so memory never breaks chat. See docs/rfc/lucius-memory.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore, memory_writes_enabled

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


# Run a consolidation pass once active facts exceed this, after an extraction.
CONSOLIDATE_THRESHOLD = 60

# Cadence guard (cost fix 2026-07-11): after a consolidation run, the NEXT run only
# fires once the store has GROWN by this many facts since that run — ">60 after every
# extraction" ran the consolidator near-every-turn on any book that permanently sits
# above the threshold (69 runs / 114 extractions observed; ~70% of memory spend).
CONSOLIDATE_MIN_GROWTH = 15

# Cap on how many facts about a single entity/event the consolidator should keep
# (prompt-level guidance; bounds the "4 near-duplicate ONWAI-sale variants" failure).
MAX_FACTS_PER_ENTITY = 2

# Batch size for chunked map-reduce consolidation. ~50 facts per call keeps the
# input well within context while allowing max_tokens=16000 for a clean output.
CONSOLIDATE_BATCH_SIZE = 50

# How many most-recent existing facts to send to the extractor for dedup context.
# 80 is enough to catch near-duplicate newly-extracted facts; sending all 185+
# facts was the source of 91K-char extraction prompts.
EXTRACT_EXISTING_CAP = 80

# Max chars for a single fact's content when building LLM listing inputs.
# Keeps per-call token budgets bounded without losing dedup signal.
FACT_LISTING_TRUNCATE = 150

# Max facts to pass to the LLM in forget_fact after prefiltering.
FORGET_CANDIDATE_CAP = 20

_VALID_TIERS = {"durable", "time_bound"}

# Guards against concurrent consolidation runs (e.g. two interleaved extract_and_merge
# calls both crossing the threshold in the same async loop). asyncio.Lock is
# per-event-loop; using a simple flag is sufficient because consolidate() is
# always awaited on the same event loop as extract_and_merge().
#
# Built on FIRST USE, not at import. On Python 3.9 asyncio.Lock() binds the running loop
# at construction, so importing this module with no current loop raises outright — and
# that is decided by whoever imports first. In tests a module that had called
# ``asyncio.run`` (which CLOSES the loop it made) left the next importer to fail with
# "no current event loop", turning a passing test red purely by file order. Nothing about
# this lock needs to exist before someone awaits it.
_consolidation_lock: "asyncio.Lock | None" = None


def _lock() -> asyncio.Lock:
    global _consolidation_lock
    if _consolidation_lock is None:
        _consolidation_lock = asyncio.Lock()
    return _consolidation_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _provider():
    from backend.ai.assistant import get_provider
    from backend.db.model_config_store import ModelConfigStore

    override = ModelConfigStore().task_model_override("memory")
    return get_provider(override)


def _coerce_tier(tier: str | None) -> str:
    return tier if tier in _VALID_TIERS else "durable"


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
    """Hard-delete time_bound facts whose expires_at has passed. Returns count deleted.

    Called before extraction and consolidation so the LLM never sees stale facts
    and the store shrinks deterministically without any LLM spend.
    Uses user_today() (local clock) for consistency with MemoryStore.active_facts().
    """
    s = store or MemoryStore()
    today = user_today().isoformat()
    to_delete = [
        f["id"]
        for f in s.all_facts()
        if f.get("tier") == "time_bound" and f.get("expires_at") and f["expires_at"] < today
    ]
    for fid in to_delete:
        s.remove_by_id(fid)
    if to_delete:
        logger.info("purge_expired: removed %d expired facts", len(to_delete))
    return len(to_delete)


# ── Explicit edits (Phase 2) ───────────────────────────────────────────────────


class MemoryDisabledError(Exception):
    """Raised by explicit-edit paths when the global memory toggle is OFF."""

    message = "Memory is disabled. Enable it in Settings → Memory before adding or removing facts."


def add_explicit(content: str, tier: str = "durable", expires_at: str | None = None) -> dict:
    """Insert a user-requested fact immediately. No dedup — save it verbatim.

    Raises MemoryDisabledError when the global memory toggle is OFF so the
    calling tool handler can surface a clear refusal instead of silently writing
    a fact that stays dormant (prompt-injection write channel when memory is OFF).
    """
    if not memory_writes_enabled():
        raise MemoryDisabledError()
    item = {
        "id": new_id("mem"),
        "content": content.strip(),
        "tier": _coerce_tier(tier),
        "source": "explicit",
        "expires_at": expires_at,
        "created_at": _now(),
        "updated_at": _now(),
    }
    MemoryStore().insert(item)
    return item


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

    listing = "\n".join(
        f"{f['id']}: {_truncate(f.get('content', ''), FACT_LISTING_TRUNCATE)}" for f in candidates
    )
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
    return store.remove_by_id(target)


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens for overlap scoring."""
    return re.findall(r"\b\w+\b", text.lower())


# ── Recall (read-only search) ───────────────────────────────────────────────────
# The prompt block (`prompts._fmt_memory`) carries at most 30 facts; recall reaches the rest.

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


def recall_facts(query: str, limit: int = 10) -> dict:
    """Best-matching ACTIVE facts for ``query`` — relevance first, newest first on a tie.

    Raises MemoryDisabledError when the memory toggle is OFF: a switched-off memory
    surfaces nothing. Reading is not a write, so a demo's seeded facts stay readable —
    the same rule the prompt block follows (``enabled()``, not ``memory_writes_enabled()``).
    Raises ValueError when the query has no searchable word.
    """
    if not MemorySettingsStore().enabled():
        raise MemoryDisabledError()
    query = (query or "").strip()
    q_tokens = _recall_tokens(query)
    if not q_tokens:
        raise ValueError("query needs at least one specific word, e.g. 'apartment' or 'retire'")
    limit = max(1, min(int(limit or 10), RECALL_MAX_LIMIT))

    today = user_today().isoformat()
    facts = [
        f
        for f in MemoryStore().active_facts(today=today)
        if not (f.get("expires_at") and f["expires_at"] < today)
    ]
    scored = [(_recall_score(query, q_tokens, f.get("content", "")), f) for f in facts]
    hits = [(s, f) for s, f in scored if s > 0]
    hits.sort(key=lambda sf: (sf[1].get("created_at", ""), sf[1].get("id", "")), reverse=True)
    hits.sort(key=lambda sf: sf[0], reverse=True)
    return {
        "matches": [
            {
                "content": _truncate(f.get("content", ""), RECALL_CONTENT_TRUNCATE),
                "tier": f.get("tier"),
                "source": f.get("source"),
                "created_at": f.get("created_at"),
                "expires_at": f.get("expires_at"),
            }
            for _, f in hits[:limit]
        ],
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


def _load_last_consolidated_count() -> int | None:
    """Fact count right after the last consolidation run (None = never ran)."""
    from backend.db.memory_consolidate_state_store import MemoryConsolidateStateStore

    try:
        doc = MemoryConsolidateStateStore().get()
    except Exception:
        return None
    if not doc:
        return None
    try:
        return int(doc["last_count"])
    except Exception:
        return None


def _save_last_consolidated_count(count: int) -> None:
    from backend.db.memory_consolidate_state_store import MemoryConsolidateStateStore

    try:
        MemoryConsolidateStateStore().save({"last_count": int(count), "ts": _now()})
    except Exception:
        logger.debug("could not persist consolidate state", exc_info=True)


def _should_consolidate(active_count: int) -> bool:
    """Growth-based cadence: over the threshold AND grown enough since the last run.

    A store that a consolidation run cannot shrink below CONSOLIDATE_THRESHOLD must
    not re-consolidate every turn — it re-runs only after CONSOLIDATE_MIN_GROWTH new
    facts accumulate. First-ever run (no state) keeps the plain threshold rule.
    """
    if active_count <= CONSOLIDATE_THRESHOLD:
        return False
    last = _load_last_consolidated_count()
    if last is None:
        return True
    return active_count - last >= CONSOLIDATE_MIN_GROWTH


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


_EXTRACT_SYSTEM = (
    "You maintain the long-term memory of a personal financial advisor about their client. "
    "You extract durable facts and dated intentions from a conversation and reconcile them "
    "with what is already remembered. You are precise and conservative — only record things "
    "that are genuinely worth carrying into future conversations."
)


async def extract_and_merge(history: list[dict], session_id: str | None = None) -> None:
    """Extract facts from the new portion of a conversation and merge into the store.

    When session_id is provided, an extraction cursor sidecar tracks the last
    extracted history length so only newly-added messages are sent to the LLM on
    each call (plus a small overlap for dedup context).  First run on a legacy long
    session: the backfill is capped at EXTRACT_BACKFILL_CAP messages.

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
        return

    store = MemoryStore()
    # Purge expired facts before building the existing listing so the extractor
    # never sees already-stale time_bound facts (saves tokens + avoids confusing it).
    purge_expired(store)

    existing = store.active_facts()
    # Cap the listing sent to the extractor: truncate each fact and send only the
    # most-recent EXTRACT_EXISTING_CAP facts. Enough for dedup, not the full 91K.
    capped = existing[:EXTRACT_EXISTING_CAP]
    existing_listing = (
        "\n".join(
            f"{f['id']}: [{f.get('tier')}] {_truncate(f.get('content', ''), FACT_LISTING_TRUNCATE)}"
            for f in capped
        )
        or "(none yet)"
    )
    today = user_today().isoformat()

    prompt = f"""Today is {today}.

Given this conversation between a user and their financial advisor, extract facts worth
remembering across future conversations: decisions, intentions, plans, constraints, and
important personal/financial context. Ignore one-off data lookups and small talk.

NEVER record a transaction, sale, disposal, purchase, transfer, or rule-creation as a
completed or dated fact — those belong to the system of record (the ledger, lots/realized
events, positions, rules), NOT to memory. If the user merely discussed or proposed an action,
capture it (if at all) as an INTENTION ("user is considering selling X"), never as history
("user sold X on <date>").

Strongly prefer durable, low-churn facts: preferences, goals, risk posture, account
structure, and standing intentions. Prefer durable preferences/context over perishable figures
(share counts, live prices, balances) that the tools read live — those go stale immediately.
AVOID memorizing quantities, share counts, live prices, or account balances — a memorized
figure goes stale the moment the price moves or a trade lands (an "underwater META at $598"
note is wrong tomorrow). Record the durable kernel instead ("holds META as a long-term equity
position"), not the live figure. Where a specific figure is genuinely essential to a fact, mark
that fact time_bound with an as-of date in the content and a near-term expires_at, NEVER durable
— so it expires instead of misleading later.

For each fact, classify:
  - tier: "durable" (a standing preference or truth, e.g. risk tolerance, a hard constraint —
          must NOT carry a perishable quantity/price/balance)
          or "time_bound" (tied to a date/event, e.g. "selling ONWAI after the Q3 vest", or a
          figure that needs an as-of)
  - expires_at: an ISO date (YYYY-MM-DD) if time_bound and a date is implied, else null

Decide whether each fact UPDATES an existing memory (reuse its id) or is NEW. If the
conversation contradicts an existing memory, prefer an update (latest wins). If a fact is
no longer true, list its id under "remove".

Existing memory:
{existing_listing}

Conversation:
{convo}

Return ONLY JSON in this exact shape:
{{
  "new":     [{{"content": "...", "tier": "durable|time_bound", "expires_at": "YYYY-MM-DD|null"}}],
  "updates": [{{"id": "...", "content": "...", "tier": "durable|time_bound", "expires_at": "YYYY-MM-DD|null"}}],
  "remove":  ["id", ...]
}}
If there is nothing worth remembering, return {{"new": [], "updates": [], "remove": []}}."""

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

    apply_memory_ops(store, parsed, today, source="implicit")

    if session_id:
        _save_cursor(session_id, len(history))

    if _should_consolidate(len(store.active_facts())):
        await consolidate()


def apply_memory_ops(
    store: MemoryStore, parsed: dict, today: str, *, source: str, guard: bool = True
) -> dict:
    """Apply one {"new", "updates", "remove"} reply to the store; return what changed.

    Shared by extraction (source "implicit") and a typed instruction from the memory dialog
    (source "explicit" — the user's own words, so an edited fact becomes theirs and, like
    ``add_explicit``, is saved without the perishable-figure demotion). An update naming an
    unknown id is treated as new rather than dropped.
    """
    known_ids = {f["id"] for f in store.all_facts()}
    counts = {"added": 0, "updated": 0, "removed": 0}

    def _shape(raw: dict) -> dict:
        fact = {
            "content": (raw.get("content") or "").strip(),
            "tier": _coerce_tier(raw.get("tier")),
            "expires_at": _norm_expiry(raw.get("expires_at")),
        }
        return _guard_perishable_durable(fact, today) if guard else fact

    new_items = list(parsed.get("new", []) or [])
    for upd in parsed.get("updates", []) or []:
        if not isinstance(upd, dict):
            continue
        if upd.get("id") not in known_ids:
            new_items.append(upd)
            continue
        patch = _shape(upd)
        if not patch["content"]:
            continue
        extra = {"source": "explicit"} if source == "explicit" else {}
        store.update_by_id(upd["id"], {**patch, **extra, "updated_at": _now()})
        counts["updated"] += 1

    for fid in parsed.get("remove", []) or []:
        if fid in known_ids and store.remove_by_id(fid):
            counts["removed"] += 1

    for new in new_items:
        if not isinstance(new, dict):
            continue
        fact = _shape(new)
        if not fact["content"]:
            continue
        store.insert(
            {
                "id": new_id("mem"),
                **fact,
                "source": source,
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        counts["added"] += 1
    return counts


def _norm_expiry(value) -> str | None:
    if not value or value in ("null", "none", "None"):
        return None
    return str(value)


# ── MEM-2 perishable-fact guard ─────────────────────────────────────────────────
# A light, deterministic backstop to the extractor prompt: a *durable* fact that
# carries a live-readable figure (share count / live price / account balance) goes
# stale the moment the price moves or a trade lands. We demote such facts to
# time_bound with a short as-of window so they expire instead of misleading later.
# Conservative on purpose — a plain preference ("risk tolerance is moderate") never
# trips it; only quantity/price/balance phrasing does.

# How long a demoted figure-bearing fact stays active before expiry filters it out.
PERISHABLE_FACT_TTL_DAYS = 30

# A "money figure" is a number carrying a currency symbol, OR a decimal, OR a
# thousands separator — i.e. price/balance shaped, not a bare small integer like an
# age ("retiring at 45") or a percent.
_MONEY_FIGURE = r"(?:[$€£¥]\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+)"

_PERISHABLE_PATTERNS = (
    # "12.5 shares", "38 shares", "1,068.7 shares/units/coins/contracts"
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:shares?|units?|coins?|contracts?)\b", re.I),
    # "trading at ~$598", "at $522", "@ €30.00", "price of 1,234.56" — money-shaped only
    re.compile(rf"(?:trading at|priced? at|price of|@|at)\s*[~≈]?\s*{_MONEY_FIGURE}", re.I),
    # "balance of $12,000", "balance ~ 12000.50", "worth $1.2m"
    re.compile(rf"\b(?:balance|worth)\b[^.\n]{{0,20}}{_MONEY_FIGURE}", re.I),
)


def _is_perishable_figure(content: str) -> bool:
    """True if the text pins a live-readable figure (share count / price / balance)."""
    return any(p.search(content or "") for p in _PERISHABLE_PATTERNS)


def _guard_perishable_durable(fact: dict, today: str | None = None) -> dict:
    """
    Demote a *durable* fact that carries a perishable figure to time_bound with a
    near-term expires_at, so a stale share count / live price / balance ages out of
    the always-injected prompt instead of misleading indefinitely. Pure; idempotent;
    leaves genuinely-durable preference facts and already-time_bound facts untouched.
    """
    if fact.get("tier") != "durable" or not _is_perishable_figure(fact.get("content", "")):
        return fact
    today = today or datetime.now(timezone.utc).date().isoformat()
    expires = fact.get("expires_at") or _expiry_after(today, PERISHABLE_FACT_TTL_DAYS)
    return {**fact, "tier": "time_bound", "expires_at": expires}


def _expiry_after(today: str, days: int) -> str:
    base = datetime.fromisoformat(today).date()
    return (base + timedelta(days=days)).isoformat()


# ── Consolidation (Phase 4) ─────────────────────────────────────────────────────


def _dedup_exact(facts: list[dict]) -> list[dict]:
    """
    Deterministic backstop to the LLM consolidation: collapse facts whose content is
    identical once lowercased + whitespace-collapsed, keeping the most recently updated
    one. Stops byte-identical re-extractions of the same fact from surviving as separate
    rows even if the model misses them; never merges genuinely-distinct wording.
    """
    by_key: dict[str, dict] = {}
    for f in facts:
        key = " ".join((f.get("content") or "").lower().split())
        if not key:
            continue
        kept = by_key.get(key)
        if kept is None or (f.get("updated_at", "") > kept.get("updated_at", "")):
            by_key[key] = f
    # Preserve first-seen order of the surviving keys for stable output.
    seen: set[str] = set()
    out: list[dict] = []
    for f in facts:
        key = " ".join((f.get("content") or "").lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(by_key[key])
    return out


_CONSOLIDATE_SYSTEM = (
    "You are the hygiene pass over a financial advisor's long-term memory about their client. "
    "You drop stale items, merge duplicates, and resolve contradictions, returning a clean set."
)


def _consolidate_batch_prompt(batch: list[dict], today: str) -> str:
    listing = json.dumps(
        [
            {
                "id": f["id"],
                "content": f.get("content", ""),
                "tier": f.get("tier"),
                "source": f.get("source"),
                "expires_at": f.get("expires_at"),
                "updated_at": f.get("updated_at"),
            }
            for f in batch
        ],
        ensure_ascii=False,
    )
    return f"""Today is {today}. Clean up this financial advisor's memory about their client.

Rules:
- Drop any time_bound fact whose expires_at is before today.
- Group facts by the SAME entity or event (e.g. all facts about "the ONWAI sale", or all
  facts about one account) and collapse each group into ONE fact. Several extractions of the
  same event MUST NOT survive as separate facts, even if their numbers differ slightly.
- On a genuine contradiction within a group (conflicting numbers, dates, or destinations),
  keep ONLY the version with the latest updated_at and discard the older variants — do not
  hedge by keeping both.
- After grouping, keep at MOST {MAX_FACTS_PER_ENTITY} facts about any single entity/event;
  drop the rest (oldest updated_at first).
- Preserve each surviving fact's original id when possible (reuse the id of the kept item).
- Do not invent new facts. Keep wording faithful to the originals.

Current memory (JSON):
{listing}

Return ONLY JSON with the OPERATIONS to apply — never restate unchanged facts
(cost fix: output size must scale with the number of CHANGES, not the store size):
{{
  "merge":  [{{"keep_id": "...", "absorb_ids": ["...", "..."],
              "content": "optional rewritten content",
              "tier": "durable|time_bound (optional)",
              "expires_at": "YYYY-MM-DD|null (optional)"}}],
  "remove": ["id", ...]
}}
- merge: collapse a group into keep_id; every absorb_ids fact is deleted. Omitted
  content/tier/expires_at keep keep_id's current values.
- remove: delete these facts outright (expired, superseded, over the per-entity cap).
- A fact needing no change MUST NOT appear anywhere in the reply.
- Nothing to change → {{"merge": [], "remove": []}}."""


def _apply_batch_ops(batch: list[dict], parsed, today: str) -> "tuple[dict, set] | None":
    """Validate one batch's operations → ({id: updated_fact}, {removed_ids}) or None.

    None = reply unusable OR the wipe guard tripped — the caller keeps the batch
    untouched. Ops may only reference ids INSIDE this batch; a single batch may not
    delete more than half its facts (a degenerate "remove everything" reply must
    never wipe real memory — the diff shape makes this guard precise).
    """
    if not isinstance(parsed, dict):
        return None
    batch_by_id = {f["id"]: f for f in batch}
    updates: dict = {}
    removed: set = set()
    for op in parsed.get("merge") or []:
        if not isinstance(op, dict):
            continue
        keep = op.get("keep_id")
        original = batch_by_id.get(keep)
        if original is None:
            continue
        absorbs = {a for a in (op.get("absorb_ids") or []) if a in batch_by_id and a != keep}
        fact = dict(updates.get(keep) or original)
        content = (op.get("content") or "").strip()
        if content:
            fact["content"] = content
        if op.get("tier"):
            fact["tier"] = _coerce_tier(op.get("tier"))
        if "expires_at" in op:
            fact["expires_at"] = _norm_expiry(op.get("expires_at"))
        fact["updated_at"] = today
        updates[keep] = fact
        removed |= absorbs
    bare_removes = 0
    for rid in parsed.get("remove") or []:
        if rid in batch_by_id and rid not in updates and rid not in removed:
            removed.add(rid)
            bare_removes += 1
    # Wipe guards: merging N dupes into one keep_id is legitimate however many it
    # absorbs (the entity's info survives on keep_id) — but a degenerate reply must
    # never gut the store. Refuse when bare deletions (no surviving merge target)
    # exceed half the batch, or when fewer than a quarter of the batch would survive.
    survivors = len(batch) - len(removed)
    if bare_removes > len(batch) // 2 or survivors < max(1, len(batch) // 4):
        logger.warning(
            "memory consolidation batch too destructive (%d bare removes, %d/%d survive) — refusing",
            bare_removes,
            survivors,
            len(batch),
        )
        return None
    return updates, removed


async def consolidate() -> None:
    """Chunked, DIFF-based consolidation: shrinks the store toward ≤60 active facts.

    Each batch call returns only merge/remove OPERATIONS (cost fix 2026-07-11: the
    old shape re-emitted every fact — 431k output tokens across 69 runs, output ≈
    input at 5× the input price). Output now scales with the number of changes, so
    max_tokens drops 16000 → 2048. A lock prevents concurrent runs; expired facts
    are purged deterministically first; the last-run fact count is persisted for the
    growth-based cadence gate.

    Batch-seam note: deliberately NOT routed through core.llm_seam batching — the
    result must apply before the next extraction reads the store, and post-fix this
    runs rarely with tiny outputs; async collection plumbing isn't worth it.

    No-op when the global memory toggle is OFF.
    """
    if not memory_writes_enabled():
        return

    if _lock().locked():
        logger.debug("consolidate: run already in flight, skipping")
        return

    async with _lock():
        store = MemoryStore()
        purge_expired(store)

        facts = store.all_facts()
        if len(facts) < 2:
            return

        today = user_today().isoformat()
        batches = [
            facts[i : i + CONSOLIDATE_BATCH_SIZE]
            for i in range(0, len(facts), CONSOLIDATE_BATCH_SIZE)
        ]
        all_updates: dict = {}
        all_removed: set = set()

        for batch in batches:
            prompt = _consolidate_batch_prompt(batch, today)
            try:
                reply = await _provider().complete(
                    prompt,
                    system=_CONSOLIDATE_SYSTEM,
                    model=_memory_model(),
                    max_tokens=2048,
                    source="memory_consolidate",
                    response_format=_MEMORY_JSON,
                    effort="none",  # dedup/merge of facts — mechanical, skip reasoning
                )
            except Exception as exc:
                logger.warning("memory consolidation batch LLM call failed: %s", exc)
                continue

            applied = _apply_batch_ops(batch, _parse_json(reply), today)
            if applied is None:
                logger.warning(
                    "memory consolidation batch returned unusable ops; batch untouched. "
                    "reply_preview=%r",
                    (reply or "")[:200],
                )
                continue
            updates, removed = applied
            all_updates.update(updates)
            all_removed |= removed

        cleaned = _dedup_exact(
            [all_updates.get(f["id"], f) for f in facts if f["id"] not in all_removed]
        )
        if not cleaned:
            logger.warning("memory consolidation produced an empty set; leaving memory untouched")
            return

        if all_updates or all_removed or len(cleaned) != len(facts):
            store.replace_all(cleaned)
        _save_last_consolidated_count(len(cleaned))
        logger.info(
            "memory consolidated: %d → %d facts (%d merged/rewritten, %d removed)",
            len(facts),
            len(cleaned),
            len(all_updates),
            len(all_removed),
        )
