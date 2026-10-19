"""Intelligence story-arc tools — run a scan, dismiss a signal, risk signals, digest.

These operate on the story-arc feed (``IntelStoriesStore`` → the ``intel_stories`` +
``signal_timeline_entries`` tables), which is distinct from the price-alert feed read by
``get_notifications`` (``NotificationLogStore``).

Batch-pluralized 2026-07-19 (owner directive: every AI tool that acts on ONE thing should
accept MANY): ``dismiss_intel_signal`` -> ``dismiss_intel_signals``,
``set_news_source_enabled`` -> ``set_news_sources_enabled``, ``add_news_source`` ->
``add_news_sources``, ``remove_news_source`` -> ``remove_news_sources`` each gained a
batch shape; the former singular name is kept as a registry alias (a tool name is
persisted data). ``scan_intel``/``get_risk_signals``/``get_intel_digest``/
``get_news_latency``/``get_news_sources`` are deliberately left singular — see the note
above each. Batches are NOT atomic — each item is an independent write, so one bad
id/url reports its own error and every other item still lands.

**Param-arity follow-up (2026-07-19d, ``backend/scripts/tool_inventory.py --lint``):** the
pass above renamed every TOOL to its plural form but left the entity-TARGETING param
singular even though it already took a list — the exact trap that made the model
stringify ``delete_account(account_id='["a","b"]')`` instead of passing an array. Renamed:
``dismiss_intel_signals.signal_id`` -> ``signal_ids``, ``remove_news_sources.source_id``
-> ``source_ids``, ``set_news_sources_enabled.source_id`` -> ``source_ids`` (top-level
broadcast key only — the per-item ``source_id`` inside ``updates`` stays singular, an
attribute of the one item in that slot). A param rename is not a tool rename — no
alias-table entry, just read-side tolerance (``_dual_ids``, mirroring ``payees.py``'s
``_payee_ids_param``/``ledger.py``'s ``_dual_filter``): the new plural key is read first,
falling back to the pre-rename singular key, so a stored/scripted caller or an in-flight
session on the old key keeps working. The old key is deliberately NOT re-declared in the
schema — that would re-trip this exact linter rule for a decoy the model is never meant
to pick.
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)

# ── Batch shape helpers (self-contained per-file, mirroring payees.py/watchlist.py) ───


def _as_list(value) -> list[str]:
    """Normalize a target that may arrive as a bare string OR a list into a plain list."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _dual_ids(input_data: dict, new_key: str, old_key: str) -> list[str]:
    """Read a multi-target id/value list under its current PLURAL name, falling back to
    the pre-2026-07-19d singular name — a param rename needs no alias-table entry the way
    a TOOL rename does, just this read-side tolerance (mirrors ``payees.py``'s
    ``_payee_ids_param``/``ledger.py``'s ``_dual_filter``), so a stored/scripted caller or
    an in-flight session still on the old key keeps working."""
    raw = input_data.get(new_key)
    if raw is None:
        raw = input_data.get(old_key)
    return _as_list(raw)


def _items_or_bare(input_data: dict, array_key: str, bare_keys: tuple[str, ...]) -> list[dict]:
    """The ``add_transactions`` shape-leniency convention: an explicit array is used
    verbatim; otherwise the top-level bare fields wrap into ONE item."""
    raw = input_data.get(array_key)
    if raw is None:
        bare = {k: input_data[k] for k in bare_keys if k in input_data}
        return [bare] if bare else []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _broadcast_or_updates(
    input_data: dict,
    updates_key: str,
    id_key: str,
    shared_fields: tuple[str, ...],
    *,
    ids_key: Optional[str] = None,
) -> list[dict]:
    """An explicit ``updates_key`` array of per-target dicts takes precedence; otherwise
    the top-level broadcast id(s) (scalar or list) apply the SAME shared field values to
    every id. ``id_key`` is also the PER-ITEM dict field name emitted below and read back
    inside ``updates_key`` entries — an attribute of the one item in that slot, correctly
    singular. ``ids_key``, when given, is the current PLURAL top-level broadcast key
    (falls back to ``id_key`` for a pre-2026-07-19d caller still on the singular name)."""
    raw = input_data.get(updates_key)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    raw_ids = input_data.get(ids_key) if ids_key else None
    if raw_ids is None:
        raw_ids = input_data.get(id_key)
    ids = _as_list(raw_ids)
    if not ids:
        return []
    shared = {k: input_data[k] for k in shared_fields if k in input_data}
    return [{id_key: v, **shared} for v in ids]


def _batch_result(results: list[dict], *, count_key: str) -> dict:
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count == len(results), count_key: ok_count, "results": results}


class ScanIntelInput(ToolInput):
    """Schema-of-record for ``scan_intel`` (see ``schema_from_model``)."""


# Deliberately left singular: this runs ONE global scan across all holdings/watchlist —
# there is no plural target to batch (mirrors apply_payee_rules's "one ledger" reasoning).
@tool(
    "scan_intel",
    description=(
        "Run the market-intelligence scan now — fetches news/web context for the user's "
        "holdings + watchlist and upserts story-arc signals (the Intelligence tab feed). "
        "This is the same action as the Intel tab's 'Scan now' button. It performs external "
        "web searches and an LLM pass, so it costs money and takes time — only run it when "
        "the user explicitly asks to refresh intelligence / scan their holdings, and tell them "
        "you're kicking it off. Returns the number of story arcs created or updated."
    ),
    input_schema=schema_of(ScanIntelInput, tool_name="scan_intel"),
    # Refreshes the machine-made Intel feed (story-arc signals) and re-syncs the auto-managed
    # holdings watchlist (run_intel_scan) — the same action as the Intel tab's "Scan now".
    refreshes=("intel", "watchlist"),
)
async def scan_intel(input_data: dict):
    from backend.monitoring.intel_agent import run_intel_scan

    updated = await run_intel_scan()
    return json.dumps({"ok": True, "stories_updated": updated}, ensure_ascii=False), None


class GetRiskSignalsInput(ToolInput):
    """Schema-of-record for ``get_risk_signals`` (see ``schema_from_model``)."""


# Deliberately left singular: computes over the WHOLE portfolio in one pass — there is no
# single-holding form to pluralize (narrow by reading affected_instrument_ids in the result).
@tool(
    "get_risk_signals",
    description=(
        "Compute deterministic portfolio risk signals — single-name concentration, asset-class "
        "and sector overweight, unhedged FX mismatch, and drawdown from cost basis — scaled by "
        "the user's configured risk tolerance. Pure compute, no LLM, no network. Returns a list "
        "of risk findings (each with kind, severity, metric_pct, threshold_pct, status_reason, "
        "action_items, and affected_instrument_ids). Does NOT write to the store; use "
        "scan_intel to persist the findings as story arcs."
    ),
    input_schema=schema_of(GetRiskSignalsInput, tool_name="get_risk_signals"),
)
async def get_risk_signals(input_data: dict):
    from backend.services.risk_signals import compute_risk

    findings = compute_risk()
    return json.dumps({"findings": findings, "count": len(findings)}, ensure_ascii=False), None


class GetIntelDigestInput(ToolInput):
    """Schema-of-record for ``get_intel_digest`` (see ``schema_from_model``)."""

    period: Optional[Literal["1M", "3M", "YTD", "1Y", "ALL"]] = Field(
        default=None, description="Return period for the market backdrop benchmark. Default 1M."
    )


# Deliberately left singular: one digest over the whole portfolio for one period — there is
# no per-holding variant to pluralize.
@tool(
    "get_intel_digest",
    description=(
        "Return the deterministic Intel Brief digest: market backdrop (benchmark price return), "
        "top return drivers (holdings ranked by signed base-currency day change), and top movers "
        "(by day-change %). Pure compute from local price history — no LLM, no search. "
        "Narration is never added by this tool (interactive-only path). "
        "Optional `period`: 1M (default), 3M, YTD, 1Y, ALL."
    ),
    input_schema=schema_of(GetIntelDigestInput, tool_name="get_intel_digest"),
)
async def get_intel_digest(input_data: dict):
    from backend.services.intel_digest import build_digest

    period = (input_data.get("period") or "1M").upper()
    digest = build_digest(period=period)
    return json.dumps(digest, ensure_ascii=False, default=str), None


class GetNewsLatencyInput(ToolInput):
    """Schema-of-record for ``get_news_latency`` (see ``schema_from_model``)."""

    days: Optional[int] = Field(
        default=None, description="Trailing window in days (1-90). Default 7."
    )


# Deliberately left singular: already reports EVERY source's latency stats in one call —
# there is no single-source form to pluralize (get_news_sources is the row-level read).
@tool(
    "get_news_latency",
    description=(
        "Per-source publish→arrival news latency (news-ingestion-latency.md §5) — for each "
        "feed source ('via': rss:yahoo, rss:google_news, rss:fn_adhoc, atom:edgar, api:gdelt, "
        "rss:custom, ...) over the trailing N days, the article count, median, and p75 lag in "
        "HOURS between when a story published and when it first landed in the evidence cache. "
        "Pure read, no network. Use to answer 'which news source is fastest/slowest' or to "
        "diagnose a stale/rotted feed."
    ),
    input_schema=schema_of(GetNewsLatencyInput, tool_name="get_news_latency"),
)
async def get_news_latency(input_data: dict):
    import asyncio

    from backend.services.news_latency import lag_stats

    days = int(input_data.get("days") or 7)
    days = max(1, min(90, days))
    # SF-4 (code review): lag_stats scans the WHOLE evidence.json — the exact 2026-07-06
    # Plan-tab-stall class this codebase already fixed elsewhere (see collect.py/analyst.py's
    # own asyncio.to_thread reads). Off the event loop so a large evidence file never stalls
    # in-flight requests. The REST twin (api/intel_latency.py) stays a plain sync def — FastAPI
    # already runs a sync route in its own threadpool.
    stats = await asyncio.to_thread(lag_stats, days=days)
    return json.dumps({"days": days, "sources": stats}, ensure_ascii=False), None


class GetNewsSourcesInput(ToolInput):
    """Schema-of-record for ``get_news_sources`` (see ``schema_from_model``)."""

    days: Optional[int] = Field(
        default=None,
        description=(
            "Trailing window, in days, for each source's `latency` stats (default 7, clamped "
            "to 1–90). Only latency is windowed — `last_ok_at` always looks back over all "
            "recorded evidence, so a source dead longer than this window still reports when "
            "it last worked."
        ),
    )


# Deliberately left singular: already returns the FULL bounded source registry (builtin +
# up to 20 user rows, ~30 total) in one call — no filter needed at this scale.
@tool(
    "get_news_sources",
    description=(
        "List news sources (builtin + user-added) with health: latency, last_ok_at, "
        "failure_streak. Pure read."
    ),
    input_schema=schema_of(GetNewsSourcesInput, tool_name="get_news_sources"),
)
async def get_news_sources(input_data: dict):
    import asyncio

    from backend.services import news_sources as news_sources_service

    days = max(1, min(90, int(input_data.get("days") or 7)))

    def _joined() -> list[dict]:
        from backend.monitoring.rss_poller_hot import source_health_snapshot

        rows = news_sources_service.list_sources(days=days)
        health = source_health_snapshot()
        for row in rows:
            row["failure_streak"] = health.get(row.get("via"))
        return rows

    # Off the event loop — list_sources scans the whole evidence store twice (lag_stats +
    # last_ok_at), same SF-4 discipline as get_news_latency above.
    sources = await asyncio.to_thread(_joined)
    return json.dumps({"sources": sources}, ensure_ascii=False), None


# ── set_news_sources_enabled ──────────────────────────────────────────────────────────


class SourceEnabledItem(NestedInput):
    """Schema-of-record for a ``set_news_sources_enabled.updates`` batch item (see
    ``schema_from_model``)."""

    source_id: str = Field(
        description="A source id from get_news_sources (e.g. 'src_ft_companies')."
    )
    enabled: bool = Field(
        description=(
            "false stops this source being fetched from the next poll tick; true resumes it. "
            "Already-cached headlines from it are kept either way. A disabled builtin stays "
            "disabled across upgrades — the boot seed never overwrites an existing row."
        )
    )


class SetNewsSourcesEnabledInput(ToolInput):
    """Schema-of-record for ``set_news_sources_enabled`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` rather than Optional[Union[...]] — the literal's anyOf has
    # no null branch even though the field is optional.
    source_ids: Union[str, List[str]] = Field(
        default=None,
        description="One source id, or a list to set the SAME enabled value on several at once.",
    )
    enabled: Optional[bool] = Field(
        default=None, description="Broadcast to every id in source_ids."
    )
    updates: List[SourceEnabledItem] = Field(
        default_factory=list,
        description=(
            "Per-source overrides: [{source_id, enabled}, ...]. Takes precedence "
            "over source_ids/enabled above."
        ),
    )


@tool(
    "set_news_sources_enabled",
    description=(
        "Enable/disable one or many news sources by id (see get_news_sources).\n\n"
        "Single source: source_ids (one id) + enabled. The SAME value on SEVERAL sources: "
        "source_ids as a LIST. Different values per source in one call: `updates` — "
        "[{source_id, enabled}, ...]."
    ),
    input_schema=schema_of(SetNewsSourcesEnabledInput, tool_name="set_news_sources_enabled"),
    mutates=("news_sources",),
)
async def set_news_sources_enabled(input_data: dict):
    from backend.core.errors import NotFoundError
    from backend.services import news_sources as news_sources_service

    entries = _broadcast_or_updates(
        input_data, "updates", "source_id", ("enabled",), ids_key="source_ids"
    )
    if not entries:
        return json.dumps({"ok": False, "error": "source_ids (or updates) is required"}), None

    single = len(entries) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, entry in enumerate(entries):
        source_id = entry.get("source_id", "")
        enabled = bool(entry.get("enabled"))
        try:
            row = news_sources_service.update_source_policy(source_id, {"enabled": enabled})
            results.append({"index": i, "ok": True, "id": source_id})
            if single:
                single_value = row
        except NotFoundError as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    if single:
        if single_value is not None:
            out["source"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── add_news_sources ──────────────────────────────────────────────────────────────────


# Shared by the `sources[]` batch item and its top-level twin below — one constant each so
# the two shapes cannot drift apart. Verified against NewsSourceStore.validate_source /
# create_source and services.news_sources.active_custom_firehose_sources.
_SOURCE_LABEL_DESC = (
    "Human-readable name for the feed, shown in the sources UI (max 200 chars). "
    "Display only — it does not affect matching or which entities the feed's hits attach to."
)
_SOURCE_URL_DESC = (
    "The RSS/Atom feed URL, http(s) only, max 2048 chars. SSRF-validated at this one moment "
    "(a private/loopback/link-local target is refused), so pass the real feed URL, not a site "
    "homepage. Hits fan out to whichever watchlist entities their text matches."
)
_SOURCE_CADENCE_DESC = (
    "How politely to poll: 'slow' (default, ~30 min) | 'fast' | 'hot'. For a user-added "
    "source this sets the INTERVAL only — every enabled custom source is checked by the same "
    "lane regardless of cadence, so even 'slow' starts working within one tick, never dormant."
)


class AddSourceItem(NestedInput):
    """Schema-of-record for an ``add_news_sources.sources`` batch item (see
    ``schema_from_model``)."""

    label: str = Field(description=_SOURCE_LABEL_DESC)
    url: str = Field(description=_SOURCE_URL_DESC)
    cadence: Optional[Literal["hot", "fast", "slow"]] = Field(
        default=None, description=_SOURCE_CADENCE_DESC
    )


class AddNewsSourcesInput(ToolInput):
    """Schema-of-record for ``add_news_sources`` (see ``schema_from_model``)."""

    label: Optional[str] = Field(default=None, description=_SOURCE_LABEL_DESC)
    url: Optional[str] = Field(default=None, description=_SOURCE_URL_DESC)
    cadence: Optional[Literal["hot", "fast", "slow"]] = Field(
        default=None, description=_SOURCE_CADENCE_DESC
    )
    sources: List[AddSourceItem] = Field(
        default_factory=list,
        description=(
            "Add MULTIPLE sources in one call: [{label, url, cadence?}, ...]. Omit "
            "when adding a single source via the top-level fields above."
        ),
    )


@tool(
    "add_news_sources",
    description=(
        "Add one or many custom RSS feed sources. Each URL is SSRF-validated; the total "
        "user-added source count is capped at 20 ACROSS the whole batch (an item beyond the "
        "cap fails independently, the rest still land). Default cadence 'slow' (politely "
        "infrequent, ~30 min) — each is fetched right away either way.\n\n"
        "Single source: label + url (+ optional cadence). Several in one call: `sources` — "
        "[{label, url, cadence?}, ...]."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        AddNewsSourcesInput, array="sources", tool_name="add_news_sources"
    ),
    mutates=("news_sources",),
)
async def add_news_sources(input_data: dict):
    from backend.core.errors import ValidationError
    from backend.services import news_sources as news_sources_service

    items = _items_or_bare(input_data, "sources", ("label", "url", "cadence"))
    if not items:
        return json.dumps({"ok": False, "error": "label and url (or sources) is required"}), None

    single = len(items) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        try:
            # create_source (the store choke) re-reads the CURRENT user-row count under its
            # own write lock on every call, so the 20-row cap is enforced across the whole
            # batch as it's inserted, not just per item — the Nth-over-the-cap item fails
            # while the earlier ones that fit still land.
            row = news_sources_service.create_user_source(
                label=item.get("label", ""),
                url=item.get("url", ""),
                cadence=item.get("cadence") or "slow",
            )
            results.append({"index": i, "ok": True, "id": row["id"]})
            if single:
                single_value = row
        except ValidationError as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="created")
    if single:
        if single_value is not None:
            out["source"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── remove_news_sources ────────────────────────────────────────────────────────────────


class RemoveNewsSourcesInput(ToolInput):
    """Schema-of-record for ``remove_news_sources`` (see ``schema_from_model``)."""

    source_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="One source id, or a list to delete several at once.",
    )


@tool(
    "remove_news_sources",
    description=(
        "Delete one or many user-added news sources. Builtins: disable instead.\n\n"
        "Pass source_ids — one id, or a LIST to delete several in one call — each is "
        "independent, so one builtin-refusal doesn't block the rest."
    ),
    input_schema=schema_of(RemoveNewsSourcesInput, tool_name="remove_news_sources"),
    mutates=("news_sources",),
)
async def remove_news_sources(input_data: dict):
    from backend.core.errors import ConflictError, NotFoundError
    from backend.services import news_sources as news_sources_service

    ids = _dual_ids(input_data, "source_ids", "source_id")
    if not ids:
        return json.dumps({"ok": False, "error": "source_ids is required"}), None

    single = len(ids) == 1
    results: list[dict] = []
    for i, source_id in enumerate(ids):
        try:
            deleted = news_sources_service.delete_user_source(source_id)
            results.append({"index": i, "ok": True, "deleted_id": deleted["id"]})
        except (NotFoundError, ConflictError) as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="deleted_count")
    if single:
        if results[0].get("ok"):
            out["deleted_id"] = results[0]["deleted_id"]
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── dismiss_intel_signals ──────────────────────────────────────────────────────────────


class DismissIntelSignalsInput(ToolInput):
    """Schema-of-record for ``dismiss_intel_signals`` (see ``schema_from_model``)."""

    signal_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="The intel signal id to dismiss, or a list to dismiss several at once.",
    )


@tool(
    "dismiss_intel_signals",
    description=(
        "Dismiss (delete) one or many story-arc intelligence signals from the Intelligence "
        "tab by id. This is the × button on a story card. It is destructive and cannot be "
        "undone, so confirm with the user which signal(s) they mean before calling. Get the "
        "id(s) from the Intelligence feed (the signals shown in the Intel tab).\n\n"
        "Pass signal_ids — one id, or a LIST to dismiss several in one call."
    ),
    input_schema=schema_of(DismissIntelSignalsInput, tool_name="dismiss_intel_signals"),
    mutates=("intel",),
)
async def dismiss_intel_signals(input_data: dict):
    from backend.services.intel_stories import delete_signal

    ids = _dual_ids(input_data, "signal_ids", "signal_id")
    if not ids:
        return json.dumps({"ok": False, "error": "signal_ids is required"}), None

    single = len(ids) == 1
    results: list[dict] = []
    for i, signal_id in enumerate(ids):
        if delete_signal(signal_id):
            results.append({"index": i, "ok": True, "dismissed": signal_id})
        else:
            results.append({"index": i, "error": "intel signal not found"})

    out = _batch_result(results, count_key="dismissed_count")
    if single:
        if results[0].get("ok"):
            out["dismissed"] = results[0]["dismissed"]
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None
