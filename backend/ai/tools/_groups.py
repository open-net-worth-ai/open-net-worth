"""The ``find_tools`` meta-tool and the CORE tool offer (tool discovery).

Discovery has TWO mechanisms over the live registry, and a call passes exactly one:
``names=[…]`` (load these exact tools — "I know the name") and ``query="…"``
(keyword search — "I know what I need"). There are no tool "groups" — the
hand-maintained ``TOOL_GROUPS`` bucket dict and its ``groups=`` load path were
removed so the shape matches Claude Code's own ``ToolSearch``, whose
``select:Read,Edit`` form is what ``names`` mirrors.

Two names remain:

* ``CORE_TOOL_NAMES`` — the always-on interactive offer (~14 tools).  This is now
  the ONLY hand-maintained name list in the whole discovery system.
* ``rare_tool_names()`` — everything else, DERIVED as ``registry - CORE``.  It is a
  function, not a module constant, because ``_groups`` is imported FIRST in
  ``ai/tools/__init__.py`` — before any handler has registered — so a constant
  evaluated at import time would be computed against an empty registry.

Loaded names are session-sticky (via ``_extra_groups_scope``'s name scope) and
persisted in session meta so future turns see the same expanded offer.

``CORE_TOOL_NAMES`` is validated against the live registry at import (best-effort)
and from tests, so a rename trips loudly.

**CORE-diet 2026-07-19 (owner incident: a typed "hello" cost $0.194, 33,881 cache
tokens before the model produced anything):** membership is now gated on MEASURED
per-tool session-spread over the ~293 real Lucius session transcripts in
``~/.open-net-worth/data/sessions`` (the corpus the 2026-07-18 harness audit located;
see ``docs/adr/2026-07-19-core-tool-offer-diet.md`` for the full methodology and
numbers) — "in how many DISTINCT sessions was this tool called," not raw call count,
because a heavy repeat-caller in one session must not look like broad usage. The
same evidence bar the owner already applied to REJECT promoting the five
most-bounced tools ("just web search is fine, get screen detail is fine, but the
rest, i don't think so") now applies symmetrically to demotion: a tool earns a
permanent slot only when the data says so, not when a prior comment asserted it.
Seven tools that were previously hand-justified as "must-stay" (``notify``
0.3%, ``restore_checkpoint``/``list_checkpoints`` ~1%, ``create_payee`` 2.4%,
``fetch_emails``/``get_ledger_summary`` 3.8%, ``get_spending_summary`` 3.1% —
all measured session-spread, not call count) were demoted. They remain fully
reachable: every tool name is still listed (bare) in the system prompt's flat
tool catalog (``ai/prompts.py``'s ``_fmt_tool_catalog``, unaffected by this
module), so the model can call any of them directly and the dispatch loop's
discovery gate auto-loads + bounces once, same as any other non-core tool.
``import_transactions`` is the one deliberate EXCEPTION to the frequency rule:
its own measured spread is also low (~1%), but the corpus predates its CORE
placement (it was added specifically to fix the corpus's #1 measured waste
event — 24 sessions that hand-booked 1,597 rows one at a time with zero
``import_transactions`` calls, because the tool was find_tools-gated and
undiscoverable) — low PRE-core usage is not evidence against a fix that changes
future discoverability, so it stays core pending a fresh measurement.

**CORE-diet follow-up 2026-10-17:** ``get_net_worth`` (6.8%, also below the bar and not
structurally pinned) was demoted when the per-turn offer crossed the 50K budget after
``recall_memory`` joined. It is reachable the same way — find_tools, the flat catalog, or any
starter skill that names it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from pydantic import ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core tool names — always offered on the interactive path (the "Balanced" set).
# Everything outside this set (and not explicitly loaded by name via find_tools)
# is hidden so the default cold-start offer stays small (~14 tools).
#
# This is the ONLY hand-maintained name list in the discovery system: "rare" is
# DERIVED from it (see rare_tool_names()), never curated in parallel.
#
# Membership below the ~7% mark is a MEASURED-evidence bar (session-spread over the
# real ~293-session corpus), not a guess — see the module docstring's "CORE-diet
# 2026-07-19" note and docs/adr/2026-07-19-core-tool-offer-diet.md for the numbers
# and the seven tools that were cut. A demoted tool is not gone — every name is still
# listed in the system prompt's flat catalog and the dispatch loop's discovery gate
# auto-loads + bounces once on first use, exactly like any other non-core tool.
# ---------------------------------------------------------------------------
CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "find_tools",
        # The system prompt's saved-skill catalog tells the model to call activate_skill when
        # a trigger clearly fits.  Unlike an ordinary rare tool, the catalog cannot first
        # route through find_tools: activate_skill is the progressive-disclosure entrance that
        # loads the body itself.  Keeping its schema inline is therefore structural, like
        # import_transactions below, rather than justified by historical call frequency.
        "activate_skill",
        "calculate",  # 19.5% of sessions
        # Ledger reads + hot writes
        "get_transactions",  # 34.1% — the #2 tool in the corpus
        "add_transactions",  # 20.1%
        "update_transactions",  # 18.8% — the #4 real tool (merged single+bulk, 2026-07-19)
        # import_transactions is the ONE entrance for every statement format. It must be
        # core: add_transactions' own description points the model here for any statement,
        # and a non-core entrance previously left the model hand-parsing into
        # add_transactions instead.
        # Its OWN measured session-spread is low (~1%), but that corpus predates its CORE
        # placement and it exists specifically to fix the corpus's #1 waste event (24
        # sessions, 1,597 hand-booked rows) — see the module docstring. Kept on structural
        # grounds, not frequency.
        "import_transactions",
        # Accounts
        "list_accounts",  # 36.2% — the #1 tool in the corpus
        # get_net_worth (6.8% — below the ~7% evidence bar) demoted 2026-10-17: nothing structural
        # pins it, and its schema is what put the offer over the 50K per-turn budget once
        # recall_memory (owner call 2026-10-14) joined CORE. Reachable as before via find_tools +
        # the system prompt's flat catalog, and any starter skill that names it auto-loads it.
        # Portfolio
        "get_portfolio",  # 24.9% — the #3 tool in the corpus
        "get_live_prices",  # 14.7%
        # Profile / categories / payees
        "get_profile",  # 6.8%
        # Its OWN measured session-spread is low, but the onboarding skill's very first
        # instruction is to call this — so it is the FIRST tool call of every brand-new
        # user's very first session, every time. Non-core meant a guaranteed bounce-and-
        # retry (the discovery gate's one-shot "not loaded yet" cycle) on that first call,
        # visible in the transcript as a failed step before the profile save that actually
        # lands — the worst possible first impression. Kept core on structural grounds
        # (like import_transactions above), not frequency.
        "update_profile",
        "list_categories",  # 7.5%
        "get_payees",  # 7.2%
        # Owner call 2026-10-14, structural like import_transactions: each turn's prompt
        # carries at most 30 remembered facts, so recall_memory is the only way to the rest
        # — and the model must already hold it when the user says "my apartment", because
        # nothing in the memory block tells it more facts exist to go find. New tool, so
        # there is no session-spread to measure yet.
        "recall_memory",
    }
)

# The tools a first session reaches for, offered from the first turn while the book is empty
# instead of found one round trip at a time. Persona QA 2026-10-13: every onboarding spent
# a find_tools round (15–20s of a new user's first minutes) on create_account, show_guide
# or the skill's references before its first real step. Seeded into the session's loaded
# set, so they stay offered once the book fills mid-conversation (assistant.run).
ONBOARDING_TOOL_NAMES: frozenset[str] = frozenset(
    {"create_account", "show_guide", "read_skill_file"}
)

# ---------------------------------------------------------------------------
# Derived "rare" set + module-load invariant checks
# ---------------------------------------------------------------------------


def rare_tool_names() -> frozenset[str]:
    """Every registered tool NOT in the default interactive offer — derived, not curated.

    ``registry - CORE_TOOL_NAMES``.  Call only after ``backend.ai.tools`` has been
    imported (the registry is empty while this module itself is being imported).
    """
    from backend.ai.tools.registry import registered_tool_names

    return frozenset(registered_tool_names()) - CORE_TOOL_NAMES


def _assert_all_names_exist(names: frozenset[str], label: str) -> None:
    """Raise ImportError if any name in ``names`` is absent from the live registry.

    Called after the full tool package is loaded (i.e., all @tool handlers have
    registered) so a renamed/deleted tool trips loudly.  When the registry is
    not yet populated (e.g. _groups is imported before the other tool modules),
    the check is silently skipped — the full-stack smoke test and CI will catch
    any missing names once the complete package is loaded.
    """
    try:
        from backend.ai.tools.registry import registered_tool_names

        known = frozenset(registered_tool_names())
        # If the registry is nearly empty, _groups was imported before the tool
        # modules registered their handlers — skip validation for now.
        if len(known) < 5:
            logger.debug("_assert_all_names_exist: registry appears not yet populated, skipping")
            return
        missing = names - known
        if missing:
            raise ImportError(
                f"{label} references tool name(s) not in the live registry: {sorted(missing)}"
            )
    except ImportError as exc:
        if "references tool name(s)" in str(exc):
            raise
        # Registry unavailable (isolated import) — skip validation.
        logger.debug("_assert_all_names_exist: registry unavailable, skipping (%s)", exc)


def _assert_core_invariants() -> None:
    """Enforce structural invariants on CORE_TOOL_NAMES (pure set logic, no registry).

    The old CORE ∩ RARE disjointness check is gone: rare is now DERIVED as
    ``registry - CORE``, so the two sets are disjoint by construction.
    """
    if "find_tools" not in CORE_TOOL_NAMES:
        raise ImportError("CORE_TOOL_NAMES must include 'find_tools'")


# Pure set invariants — safe to run at module load (no registry dependency).
_assert_core_invariants()
# Registry-backed validation: best-effort at module load (skips if registry is empty),
# full validation runs from tests after the complete package is imported.
_assert_all_names_exist(CORE_TOOL_NAMES - {"find_tools"}, "CORE_TOOL_NAMES")
_assert_all_names_exist(ONBOARDING_TOOL_NAMES, "ONBOARDING_TOOL_NAMES")

_FIND_TOOLS_DESCRIPTION = (
    "Load tools that are not offered by default. REQUIRED: pass exactly one of the two "
    "below — never call this with neither. names=['get_returns'] loads those exact tools "
    "(use this when you already know the name — it is the cheap path); query='rebalance "
    "plan' scores a keyword search over every tool's name and description. Loaded tools "
    "stay available for the whole session."
)

# Bounds on ``max_results`` (the default matches _search_tools' own default).
_DEFAULT_MAX_RESULTS = 10
_MAX_MAX_RESULTS = 25

# A find_tools result is re-sent on EVERY later round of a run, so its size is a standing
# cost, not a one-off: an unattended run's five searches (7k-35k chars each, up to 50 full
# descriptions) were most of a 700k-input-token run. The ranked hits get their full
# description until this budget is spent; the rest keep their name (they are loaded either
# way) and a one-line summary, and the model asks for one by exact name via ``names=[...]``
# when it needs the whole text.
_SUMMARY_BUDGET_CHARS = 6000
_TAIL_SUMMARY_CHARS = 120

# ---------------------------------------------------------------------------
# Internal search helper
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric runs, drop tokens shorter than 2 chars."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2]


def _reachable_universe() -> Optional[frozenset]:
    """The full set of tool names the CURRENT turn may ever reach when an explicit
    restrictive offer is active (an agent/scheduled run) — else ``None`` (no restriction;
    ordinary interactive turns and inline skill instructions are untouched).

    ``find_tools`` is a delivery mechanism (ai/tools/_agent_offer.py bounds an agent's
    INLINE offer, not what it may ever reach), so its own search/load must never surface
    a tool outside the turn's caller-supplied offer — that would silently widen what
    ``services.agents.effective_scope`` already decided is reachable. Inline skill activation
    never changes the reachable universe.
    """
    try:
        from backend.ai.tools._skill_scope import offer_tool_names

        offer = offer_tool_names()
        return frozenset(offer) if offer is not None else None
    except Exception:
        return None


def _candidate_schemas() -> list[dict]:
    """Every tool schema discovery may reach: the registry (bounded to the active
    explicit offer, if any — see ``_reachable_universe``), minus catalog-hidden tools,
    minus connector-gated tools, plus external MCP when interactive.

    Shared by both ``find_tools`` paths (keyword ``query`` and exact ``names``) so a name
    lookup can never reach a tool the search could not, and the external-MCP interactive
    gate is enforced in ONE place. Fail-closed: any exception or active scope → registry only.

    ``_CATALOG_HIDDEN`` (``ai/tools/catalog.py``) is excluded here too — it hides a tool
    from the flat system-prompt catalog, and without this filter ``find_tools`` would
    still let a lucky keyword guess or an exact-name call surface/load it, defeating the
    hide. (The set is empty today — the tax tools it was built for were removed
    outright rather than left hidden — but the filter must stay for the next one.)

    ``_connector_gate.hidden_connector_tools()`` is excluded the SAME way, for the SAME
    reason: a ``connector=``-tagged tool whose connector isn't enabled must not be
    reachable through a lucky keyword guess or an exact ``names=[...]`` load either — the
    catalog and ``find_tools`` are the two halves of "is this even offered" (RFC
    ``.claude/rfcs/2026-08-10-connectors-architecture.md`` §B2), and they must agree. An
    untagged (internal) tool is never affected. This is DISCOVERY only — it does not stop
    the model from calling a known/already-loaded connector tool by name; that call still
    dispatches (see ``registry.tool``'s ``connector=`` docstring).
    """
    # Import TOOL_SCHEMAS from the package (populated at import time).
    from backend.ai.tools import TOOL_SCHEMAS
    from backend.ai.tools._connector_gate import hidden_connector_tools
    from backend.ai.tools.catalog import _CATALOG_HIDDEN

    hidden = _CATALOG_HIDDEN | hidden_connector_tools()
    candidates: list[dict] = [s for s in TOOL_SCHEMAS if s.get("name") not in hidden]
    universe = _reachable_universe()
    if universe is not None:
        return [s for s in candidates if s.get("name") in universe]
    if _is_interactive_scope():
        try:
            from backend.core import mcp_external as _mcp_ext

            dispatcher = _mcp_ext.get_external_dispatcher()
            if dispatcher is not None:
                candidates.extend(list(dispatcher.schemas()))
        except Exception:
            pass  # broken external manager must never break discovery
    return candidates


def _search_tools(query: str, limit: int = _DEFAULT_MAX_RESULTS) -> list[str]:
    """Return up to ``limit`` tool names scored against ``query``.

    Scoring: each query token that appears (as a substring) in the tool name scores
    ×3; in the description scores ×1.  Exact-token matches score an additional ×2
    bonus (×6 or ×3 respectively).  Ties broken alphabetically by name.  Only tools
    with score > 0 are returned.

    External MCP schemas (``mcp__`` prefix) are included when NOT under a headless or
    skill scope (interactive-only, fail-closed: any scope uncertainty → exclude).
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    candidates = _candidate_schemas()

    scores: list[tuple[int, str]] = []
    for schema in candidates:
        name: str = schema.get("name", "")
        desc: str = schema.get("description", "")
        name_lower = name.lower()
        desc_lower = desc.lower()
        score = 0
        for token in query_tokens:
            if token in name_lower:
                # Exact token boundary bonus: whole-word match in name
                score += (
                    6
                    if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", name_lower)
                    else 3
                )
            if token in desc_lower:
                score += (
                    3
                    if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", desc_lower)
                    else 1
                )
        if score > 0:
            scores.append((-score, name))  # negative for descending sort

    scores.sort(key=lambda t: (t[0], t[1]))
    return [name for _, name in scores[:limit]]


def _is_interactive_scope() -> bool:
    """True when the current turn is interactive (not headless or legacy-restricted).

    Fail-closed: returns False on any import error or uncertainty so external-MCP
    schemas are never surfaced on an unattended turn.
    """
    try:
        from backend.ai.tools._skill_scope import offer_tool_names

        return offer_tool_names() is None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# @tool registration
# ---------------------------------------------------------------------------
from backend.ai.tools.registry import tool  # noqa: E402
from backend.ai.tools.schema_from_model import (  # noqa: E402
    ToolInput,
    schema_of_no_required,
)


class FindToolsInput(ToolInput):
    """Schema-of-record for ``find_tools`` (see ``schema_from_model``).

    ``extra="ignore"`` overrides ``ToolInput``'s ``extra="forbid"`` so no
    ``additionalProperties`` keyword is emitted at all — the pinned literal has never
    declared one, and adding ``false`` here would be a schema change, not a derivation.
    """

    model_config = ConfigDict(extra="ignore")

    query: Optional[str] = Field(
        default=None,
        description=(
            "Keyword search over every tool's name and description "
            "(e.g. 'retirement simulation', 'rebalance plan'). "
            "Returns the top matching tools and loads them for this session."
        ),
    )
    names: Optional[List[str]] = Field(
        default=None,
        description=(
            "Exact tool names to load, when you already know what you want "
            "(e.g. ['get_returns', 'get_rebalance_plan']). Cheaper and more certain "
            "than a keyword search. Use INSTEAD of query, not with it."
        ),
    )
    max_results: Optional[int] = Field(
        default=None,
        ge=1,
        le=_MAX_MAX_RESULTS,
        description=(
            f"Maximum tools to return (default {_DEFAULT_MAX_RESULTS}). "
            "Applies to query search only."
        ),
    )


# `query`/`names` are each individually optional, but the call must carry at least one —
# a cross-field constraint plain `required` can't express. A top-level `anyOf: [{"required":
# [...]}, {"required": [...]}]` was tried here (2026-08-31) to catch a bare `find_tools()`
# call at the schema level instead of only at runtime — verified against OpenAI's
# Structured Outputs docs (which do allow `anyOf`), but NEVER against Anthropic's tool-use
# API, which rejects `oneOf`/`allOf`/`anyOf` at a tool's input_schema TOP LEVEL outright
# (HTTP 400 on the very first message). `find_tools` is a CORE tool present in every
# session, so this broke EVERY session for anyone on the raw Anthropic API-key transport —
# owner-reproduced live, 2026-09-03, a fresh onboarding session 400ing immediately. Reverted:
# the runtime handler below already rejects a missing/malformed call with an actionable
# error, so the schema-level catch was a nicety, never load-bearing — not worth carrying a
# footgun this wide for. See test_tool_schemas_never_use_top_level_oneof_allof_anyof for the
# regression guard against this recurring on any other tool.
_FIND_TOOLS_SCHEMA = schema_of_no_required(FindToolsInput, tool_name="find_tools")


@tool(
    "find_tools",
    description=_FIND_TOOLS_DESCRIPTION,
    input_schema=_FIND_TOOLS_SCHEMA,
)
async def find_tools(input_data: dict):
    from backend.ai.tools._extra_groups_scope import add_extra_names, get_extra_names

    query: str | None = input_data.get("query") or None
    raw_names = input_data.get("names")
    has_names = bool(raw_names)

    # Exactly one of the two intents. They are different questions ("I know the name" vs
    # "I know what I need"), and accepting both would silently pick one — so say so instead.
    if query is None and not has_names:
        # Covers a missing query AND any legacy call shape (e.g. the removed
        # `groups=[…]`): an unrecognized key is ignored, so the model gets one
        # actionable error instead of a crash.
        return (
            json.dumps(
                {
                    "error": (
                        "one of 'query' (keyword search) or 'names' (exact tool names) is required."
                    )
                }
            ),
            None,
        )
    if query is not None and has_names:
        return (
            json.dumps(
                {
                    "error": (
                        "pass 'query' OR 'names', not both — 'names' loads exact tools, "
                        "'query' searches by keyword."
                    )
                }
            ),
            None,
        )

    unknown: list[str] = []
    if has_names:
        found_names, unknown, already_core = _resolve_names(raw_names)
        if not found_names and unknown:
            return (
                json.dumps(
                    {
                        "error": (
                            f"no such tool(s): {sorted(unknown)}. Call "
                            'find_tools(query="…") to search by keyword instead.'
                        )
                    }
                ),
                None,
            )
        # No discovery_log entry on this path: that log measures SEARCH RECALL
        # (miss / core_only / ok), a question an exact-name lookup does not ask. The call
        # itself is instrumented by call_log like every other dispatch.
    else:
        limit = _clamp_max_results(input_data.get("max_results"))
        raw_hits = _search_tools(query or "", limit=limit)
        # Drop CORE hits: those tools are already offered, so "loading" them is a
        # no-op — reporting them would contradict the prompt ("core is already loaded")
        # and needlessly bloat the persisted name scope.
        found_names = [n for n in raw_hits if n not in CORE_TOOL_NAMES]
        already_core = []

        # Telemetry: record search quality (miss / core_only / ok) so recall can be
        # audited over time — see ai/tools/discovery_log.py. Best-effort, never raises.
        from backend.ai.tools import discovery_log

        discovery_log.record_query(query or "", raw_hits, found_names)

    loaded: list[dict] = []
    if found_names:
        add_extra_names(frozenset(found_names))
        loaded = _bounded_summaries(found_names)

    # Persist the name scope to session meta (best-effort).
    try:
        from backend.core.chat_session import get_session_id

        session_id = get_session_id()
        if session_id:
            from backend.ai.assistant import upsert_session_meta

            upsert_session_meta(session_id, extra_tool_names=sorted(get_extra_names()))
    except Exception:
        logger.debug("find_tools: could not persist session meta")

    payload: dict = {"loaded": loaded}
    # Only surfaced on the names path, and only when non-empty: a partial result must not
    # look like a clean one, but an empty key on every response is noise in the transcript.
    if already_core:
        payload["already_available"] = already_core
    if unknown:
        payload["unknown"] = sorted(unknown)
    return (json.dumps(payload), None)


# Bounds the names path: a request this long is a malformed call, not a real intent, and
# each accepted name is persisted into the session's sticky tool scope.
_MAX_NAMES = 20


def _resolve_names(raw: object) -> tuple[list[str], list[str], list[str]]:
    """Split caller-supplied tool names into (loadable, unknown, already-core).

    A name must exist in ``_candidate_schemas`` to load — so this path can never reach a
    tool the keyword search could not, including the external-MCP interactive gate.
    """
    if isinstance(raw, str):  # a single name passed unwrapped — obvious intent, accept it
        raw = [raw]
    if not isinstance(raw, list):
        return [], [], []
    known = {s.get("name", "") for s in _candidate_schemas()}
    loadable: list[str] = []
    unknown: list[str] = []
    already_core: list[str] = []
    seen: set[str] = set()
    for item in raw[:_MAX_NAMES]:
        name = item.strip() if isinstance(item, str) else ""
        if not name or name in seen:
            continue
        seen.add(name)
        if name in CORE_TOOL_NAMES:
            already_core.append(name)  # already offered — loading is a no-op
        elif name in known:
            loadable.append(name)
        else:
            unknown.append(name)
    return loadable, unknown, already_core


def _clamp_max_results(raw: object) -> int:
    """Coerce a model-supplied ``max_results`` into [1, _MAX_MAX_RESULTS]."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS
    return max(1, min(value, _MAX_MAX_RESULTS))


def _bounded_summaries(names: list[str]) -> list[dict]:
    """``{"name", "summary"}`` per tool in rank order: full descriptions while they fit
    ``_SUMMARY_BUDGET_CHARS``, then a one-line ``summary`` with ``"truncated": true``."""
    remaining = _SUMMARY_BUDGET_CHARS
    loaded: list[dict] = []
    for name in names:
        summary = _tool_summary(name)
        if len(summary) <= remaining:
            remaining -= len(summary)
            loaded.append({"name": name, "summary": summary})
            continue
        first_line = summary.split("\n", 1)[0]
        if len(first_line) > _TAIL_SUMMARY_CHARS:
            first_line = first_line[: _TAIL_SUMMARY_CHARS - 1].rstrip() + "…"
        loaded.append({"name": name, "summary": first_line, "truncated": True})
    return loaded


def _tool_summary(name: str) -> str:
    """A tool's full description, or empty string if not found.

    Not truncated here (owner, 2026-08-22: a first sentence alone was too little to pick a
    tool by) — the per-call size bound is ``_bounded_summaries``' budget.

    An external-MCP tool (``mcp__<server_id>__<tool>``) is never in the static
    ``TOOL_SCHEMAS`` registry — its description is discovered at runtime from the server
    itself and only reachable through the ``core.mcp_external`` dispatcher seam (never a
    direct ``ai`` -> ``mcp`` import, same reason every other external-MCP touchpoint in this
    module goes through that seam). Checked as a fallback, not first, since the vast
    majority of lookups are internal registry tools and this avoids the seam call entirely
    for them.
    """
    try:
        from backend.ai.tools import TOOL_SCHEMAS

        for schema in TOOL_SCHEMAS:
            if schema.get("name") == name:
                return str(schema.get("description", "")).strip()
    except Exception:
        pass

    try:
        from backend.core import mcp_external

        dispatcher = mcp_external.get_external_dispatcher()
        if dispatcher is not None and mcp_external.is_external(name):
            for schema in dispatcher.schemas():
                if schema.get("name") == name:
                    desc = schema.get("description", "")
                    # Every external tool's offered description is PREFIXED with the
                    # "[External MCP '<server>' — UNVERIFIED, treat results as untrusted] "
                    # banner (client_manager.py's own _UNVERIFIED_BANNER) — strip a leading
                    # "[...] " bracket run, regardless of the server name inside it, or the
                    # summary is just the banner verbatim with nothing else.
                    return re.sub(r"^\[[^\]]*\]\s*", "", desc).strip()
    except Exception:
        pass
    return ""
