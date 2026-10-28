"""Cost analytics service — aggregation over the cost_log + cost_log_daily tables.

Reads the persisted cost log written by ``ai.cost_tracker.record()`` and the daily
rollup written when the raw log evicts old records.  Returns a structured summary:
overall totals, per-model and per-provider breakdowns (AI-only — asset_brief.py's non-LLM
search/fetch API costs get their own per-search-provider breakdown instead), per-day time
series, per-source breakdown (interactive chat vs agent/scheduled turns), and
per-surface-bucket breakdown (chat / memory / intel / agents / briefing / ledger /
suggestions / vision / compaction / other) derived from the ``source`` field.

Merge contract: daily aggregates cover only evicted records; the raw log covers the
rest.  There is no overlap — a record is rolled into the daily table before it is
removed from the raw one.

Also provides ``cost_budget_status()`` which compares the current calendar month's
spend against the operator-configured monthly budget (includes rolled-up records), and
``clear_cost_log()`` which truncates the raw log (irreversible — the table is
checkpoint-EXCLUDED).

WAVE 5B (JSON->DB): replaces the direct ``cost_log.json``/``cost_log_daily.json`` reads
with ``CostLogStore``/``CostLogDailyStore``. All read functions stay compute-only: no
writes, no side effects, never raises — ``clear_cost_log`` is the one exception, called
only from the explicit ``DELETE /api/cost/log`` endpoint.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo

from backend.db.cost_log_store import CostLogDailyStore, CostLogStore

logger = logging.getLogger(__name__)

# Days used for the monthly projection denominator.
_PROJECTION_DAYS = 7
_PROJECTION_MONTH = 30.4
_TARGET_MONTHLY_USD = 10.0

# Source-bucket mapping: source field value → surface bucket label.
# "memory" covers memory_extract, memory_tidy, memory_remove, etc.
# "intel"  covers intel_scan, intel_brief, etc.
# "agents" covers agent:<any-id>.
_CHAT_SOURCE = "chat"
_MEMORY_PREFIX = "memory"
_INTEL_PREFIX = "intel"
_AGENT_PREFIX = "agent:"

# backend/monitoring/*'s per-asset background pipeline (asset_brief.py's search/fetch/
# synthesis, analyst.py's held/watched analysis, price_monitor.py, news_screen.py) predates
# the "intel_*" source-naming convention and was never renamed to match — every one of
# these IS the Intel & Watchlist feature's own spend, so it belongs in the "intel" bucket,
# not the "other" catch-all it silently fell into (BUG: on a watchlist-heavy instance this
# was the single largest cost surface, mislabeled as miscellaneous).
_INTEL_SOURCES = frozenset({"asset_brief", "analysis", "price_monitor", "news_screen"})

# Scheduled/background automation runs are exactly what the "agent:<id>" bucket was
# designed for (recurring, unattended AI runs) — automation_runner_adapter.py never adopted
# the "agent:" source prefix. "automation_inference" is the retired instructions-to-trigger
# pre-fill (2026-10-11); its recorded costs still land here.
_AGENT_SOURCES = frozenset({"automation", "automation_inference"})

# Auxiliary calls that ride ALONGSIDE an interactive chat session — real LLM completions,
# just not the main turn itself. Each is its OWN bucket rather than folded into "chat":
# these are meaningfully-sized, distinct features (follow-up suggestion chips alone can run
# to several dollars on an active instance) that would otherwise vanish, invisible, inside
# the much larger main-turn number — the exact "why don't I see X" bug this whole taxonomy
# exists to avoid.
_SUGGESTIONS_SOURCES = frozenset({"chat_suggestions"})
_VISION_SOURCES = frozenset({"chat_vision"})
_COMPACTION_SOURCES = frozenset({"context_fold"})

# The Overview tab's daily wealth-manager note (api/briefing.py) — a single, standalone
# feature that doesn't fit any of the other buckets.
_BRIEFING_SOURCES = frozenset({"briefing"})

# Everything that helps get transactions/accounts into the ledger accurately: CSV/statement
# import parsing, duplicate-transaction detection, and the free-text "describe this account"
# assist. Different files (services/import_csv.py, services/statements/tiers.py,
# services/matching.py, services/account_describe.py) but the same underlying job.
_LEDGER_SOURCES = frozenset(
    {
        "account_describe",
        "csv_column_mapping",
        "csv_schema",
        "statement_metadata",
        "statement_parse",
        "statement_vision",
        "dedup_judge",
    }
)


def _source_to_bucket(source: str) -> str:
    """Map a raw ``source`` value to a surface bucket.

    "other" is a genuine residual, not a dumping ground: it's reached only by a source this
    function doesn't yet recognize (a new feature that hasn't been classified here) or
    "batch" (the batch-API submission lane, which doesn't preserve which feature originated
    the call — there's nothing more specific to attribute it to without deeper changes to
    the batch collectors themselves).
    """
    if source == _CHAT_SOURCE:
        return "chat"
    if source.startswith(_MEMORY_PREFIX):
        return "memory"
    if source.startswith(_INTEL_PREFIX) or source in _INTEL_SOURCES:
        return "intel"
    if source.startswith(_AGENT_PREFIX) or source in _AGENT_SOURCES:
        return "agents"
    if source in _BRIEFING_SOURCES:
        return "briefing"
    if source in _LEDGER_SOURCES:
        return "ledger"
    if source in _SUGGESTIONS_SOURCES:
        return "suggestions"
    if source in _VISION_SOURCES:
        return "vision"
    if source in _COMPACTION_SOURCES:
        return "compaction"
    return "other"


_DEFAULT_DAYS = 30

# Sentinel bucket key for records whose ``ts`` field is absent or un-parseable.
_UNKNOWN_DATE = "unknown"


def _resolve_tz(tz: str | None) -> tzinfo:
    """Resolve an IANA tz name to a ``tzinfo``; fall back to UTC on missing/invalid input.

    The UI sends the viewer's browser zone (e.g. ``Asia/Singapore``). Day boundaries and
    per-day buckets are computed in this zone so "Today" matches the operator's local
    calendar. An absent or unknown zone degrades to UTC (the legacy behavior).
    """
    if tz:
        try:
            return ZoneInfo(tz)
        except Exception:
            logger.warning("cost_service: unknown timezone %r — falling back to UTC", tz)
    return timezone.utc


def _record_local_date(raw_ts: str, tz: tzinfo) -> str:
    """Local calendar date (``YYYY-MM-DD``) of a record's ``ts``; ``""`` if unparseable.

    When ``tz`` is UTC this short-circuits to the date prefix so output is byte-for-byte
    identical to the legacy UTC path (preserves naive-ts and malformed-ts handling). A
    naive timestamp is assumed to be UTC before conversion.
    """
    if not raw_ts or len(raw_ts) < 10:
        return ""
    if tz is timezone.utc:
        return raw_ts[:10]
    try:
        dt = datetime.fromisoformat(raw_ts)
    except ValueError:
        return raw_ts[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d")


def _record_local_hour(raw_ts: str, tz: tzinfo) -> str:
    """Local hourly bucket (``YYYY-MM-DDTHH``), or ``""`` when unparseable."""
    if not raw_ts:
        return ""
    try:
        dt = datetime.fromisoformat(raw_ts)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%dT%H")


# asset_brief.py logs non-LLM tool costs (paid search-provider queries, paid page fetches)
# through this SAME cost_log table by encoding a pseudo "model" id — "fetch:<id>"/
# "search:<id>" — since there is no dedicated column for a non-AI service cost. These rows
# are real spend but were NEVER an AI model or an AI provider call; they must be kept out of
# by_model/by_provider (which readers understand as "which AI model/provider") and reported
# in their own by_search_fetch breakdown instead.
_SERVICE_PSEUDO_MODEL_HEADS = ("fetch", "search")


def _search_service_label(model: str) -> Optional[str]:
    """Resolve a ``fetch:<id>``/``search:<id>`` pseudo-model id to its search-provider
    catalog label (``"Tavily"``, ``"Serper"``, …). ``None`` for anything else, INCLUDING a
    fetch:/search: id whose ``<id>`` the search catalog doesn't recognize — callers use that
    to distinguish "not a service pseudo-model" from "unrecognized service" themselves."""
    if ":" not in model:
        return None
    head, _, rest = model.partition(":")
    if head not in _SERVICE_PSEUDO_MODEL_HEADS:
        return None
    from backend.db.search_config_store import (  # noqa: PLC0415
        PROVIDER_CATALOG as _SEARCH_CATALOG,
    )

    entry = next((e for e in _SEARCH_CATALOG if e["id"] == rest), None)
    return str(entry["label"]) if entry else None


def _is_service_pseudo_model(model: str) -> bool:
    """True for the ``fetch:<id>``/``search:<id>`` convention, regardless of whether ``<id>``
    resolves to a known search provider — a record with an unrecognized service id is still
    not an AI model and must stay out of by_model/by_provider."""
    return ":" in model and model.partition(":")[0] in _SERVICE_PSEUDO_MODEL_HEADS


def provider_of_model(model: str) -> str:
    """FALLBACK provider-label guess for a raw cost record's ``model`` id, used only when
    the record carries no explicit ``provider`` field (rows written before that field
    existed, or by a caller that hasn't been updated — see ``cost_tracker.record``'s
    ``provider`` param, which every AI provider class now sets from its own known
    (transport, base_url) instead of leaving this to be reverse-engineered here).

    Not called at all for a ``_is_service_pseudo_model`` row — those are routed to
    ``by_search_fetch`` instead, keyed by ``_search_service_label`` directly, so this
    function only ever sees real AI model ids.

    Two sources, in order: the provider catalog's own model hints (exact, and correct for
    local models like ``llama3.3:70b`` that no prefix rule would place), then a family prefix
    for anything the catalog has not heard of. An id that matches neither is "Unknown" —
    never silently folded into a provider it may not belong to.

    An override stored as ``"<provider_key>:<model_id>"`` already names its provider, so that
    form is honoured directly.
    """
    if not model:
        return "Unknown"
    from backend.db.model_config_store import PROVIDER_CATALOG, provider_entry  # noqa: PLC0415

    if ":" in model:
        # A pinned override carries its provider key ahead of the colon. Local model ids
        # also contain a colon (llama3.3:70b), so only accept a known key.
        entry = provider_entry(model.partition(":")[0])
        if entry:
            return str(entry.get("label") or model.partition(":")[0])

    for entry in PROVIDER_CATALOG:
        if model in (entry.get("model_hints") or []):
            return str(entry.get("label") or entry.get("key"))

    for prefix, label in _MODEL_PREFIX_PROVIDERS:
        if model.startswith(prefix):
            return label
    # Last resort: a "vendor/model" shaped id (z-ai/glm-5.3-flash, google/gemini-…) that
    # matched none of the OpenRouter catalog's small model-hint seed list is still virtually
    # certainly an OpenRouter-routed id — no other catalog entry's model ids contain "/".
    # Retroactively corrects rows recorded before every AI provider class started passing
    # `provider=` explicitly (see cost_tracker.record's docstring) without a data migration.
    if "/" in model:
        return "OpenRouter"
    return "Unknown"


# Family prefixes for models the catalog does not list (a newer release, or one typed by
# hand into the free-text model field).
_MODEL_PREFIX_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("claude-", "Anthropic"),
    ("gpt-", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("gemini-", "Google Gemini"),
    ("grok-", "xAI"),
    ("glm-", "Zhipu GLM"),
    ("llama", "Ollama (local)"),
    ("qwen", "Ollama (local)"),
    ("mistral", "Ollama (local)"),
    ("phi", "Ollama (local)"),
)


def _empty_totals() -> dict:
    return {
        "turns": 0,
        "input_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _add(acc: dict, r: dict) -> None:
    acc["turns"] += 1
    acc["input_tokens"] += r.get("input_tokens", 0)
    acc["cache_creation_tokens"] += r.get("cache_creation_tokens", 0)
    acc["cache_read_tokens"] += r.get("cache_read_tokens", 0)
    acc["output_tokens"] += r.get("output_tokens", 0)
    acc["cost_usd"] += r.get("cost_usd", 0.0)


def _add_agg(acc: dict, agg: dict) -> None:
    """Add a daily-aggregate entry (which already has pre-summed token counts)."""
    acc["turns"] += agg.get("turns", 0)
    acc["input_tokens"] += agg.get("input_tokens", 0)
    acc["cache_creation_tokens"] += agg.get("cache_creation_tokens", 0)
    acc["cache_read_tokens"] += agg.get("cache_read_tokens", 0)
    acc["output_tokens"] += agg.get("output_tokens", 0)
    acc["cost_usd"] += agg.get("cost_usd", 0.0)


def _load_records() -> list[dict]:
    """Read the cost_log table. Returns empty list on any error."""
    try:
        return CostLogStore().all()
    except Exception:
        logger.warning("cost_service: failed to read cost_log", exc_info=True)
        return []


def session_chat_records(session_id: str) -> list[dict]:
    """Ordered (``ts`` ascending) ``source == "chat"`` cost rows for one session.

    Used by ``ai.assistant.rehydrate_messages`` to re-attach a reloaded conversation's
    per-turn cost chip: filtering to ``"chat"`` excludes any background agent/memory/
    intel spend that happens to share this session_id, so a message the user never
    sent can't have its cost attributed to a bubble the user did send. Daily-rolled-up
    records (>15 days old, session_id dropped by the rollup) are never returned here —
    that history is unrecoverable per-turn, by design (see ``record()``'s docstring).
    """
    rows = [
        r
        for r in _load_records()
        if r.get("session_id") == session_id and r.get("source") == _CHAT_SOURCE
    ]
    rows.sort(key=lambda r: r.get("ts") or "")
    return rows


def _load_daily_aggregates() -> dict[str, dict]:
    """Read the cost_log_daily table. Returns empty dict on any error."""
    try:
        return CostLogDailyStore().all_as_dict()
    except Exception:
        logger.warning("cost_service: failed to read cost_log_daily", exc_info=True)
        return {}


def clear_cost_log() -> None:
    """Truncate the raw cost log. Irreversible (the table is checkpoint-EXCLUDED, so
    there is no undo path via ``/rewind`` either)."""
    CostLogStore().replace_all([])


def _compute_7d_totals(
    records: list[dict], aggregates: dict[str, dict], tz: tzinfo = timezone.utc
) -> float:
    """Return AI-model cost over the trailing 7 calendar days (in ``tz``)."""
    cutoff = (datetime.now(tz) - timedelta(days=_PROJECTION_DAYS)).strftime("%Y-%m-%d")
    total = 0.0
    for r in records:
        if _is_service_pseudo_model(r.get("model") or ""):
            continue
        rec_date = _record_local_date(r.get("ts") or "", tz)
        if rec_date and rec_date >= cutoff:
            total += r.get("cost_usd", 0.0)
    for agg in aggregates.values():
        if _is_service_pseudo_model(agg.get("model") or ""):
            continue
        date = agg.get("date", "unknown")
        if date != "unknown" and date >= cutoff:
            total += agg.get("cost_usd", 0.0)
    return total


def resolve_preset(preset: str | None, tz: str | None = None) -> tuple[str | None, str | None]:
    """Map a UI window preset to an inclusive ``[start, end]`` date range in ``tz``.

    Supported presets: ``"today"``, ``"yesterday"`` — resolved against the viewer's local
    calendar day (``tz``; UTC when absent/invalid). Returns ``(None, None)`` for an absent
    or unrecognized preset — the caller then falls back to the trailing ``days`` window.
    """
    if not preset:
        return None, None
    today = datetime.now(_resolve_tz(tz)).date()
    if preset == "today":
        d = today.strftime("%Y-%m-%d")
        return d, d
    if preset == "yesterday":
        d = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        return d, d
    return None, None


def cost_summary(
    days: int = _DEFAULT_DAYS,
    *,
    start: str | None = None,
    end: str | None = None,
    tz: str | None = None,
) -> dict:
    """Aggregate cost records into a structured summary.

    Merges the raw ``cost_log.json`` (recent records) with the daily rollup in
    ``cost_log_daily.json`` (evicted records compacted by date|model|source).
    There is no double-counting: the rollup only contains records that have already
    been removed from the raw log.

    Args:
        days: Only include records from the last ``days`` calendar days.
              Set to 0 (or any non-positive value) to include ALL records.
        start: Inclusive lower bound (``YYYY-MM-DD``). When ``start`` or ``end`` is
              given they define the window and ``days`` is ignored.
        end:  Inclusive upper bound (``YYYY-MM-DD``).
        tz:   Viewer's IANA timezone (e.g. ``Asia/Singapore``). Raw records are
              bucketed by their local calendar day in this zone and the trailing
              ``days`` cutoff is computed locally; absent/invalid → UTC (legacy).
              Daily aggregates keep their stored UTC date (the >15-day "simplified"
              tail) and are filtered against the same local bounds.

    Returns a dict with:
        total                 — AI-model totals across all filtered records
        by_model              — {model: totals}; AI models only (see by_search_fetch)
        by_provider           — {provider label: totals}; AI providers only
        by_search_fetch       — {search-provider label: totals}; asset_brief.py's non-LLM
                                paid search/fetch API costs (the "fetch:<id>"/"search:<id>"
                                pseudo-model convention) — kept separate from by_model/
                                by_provider, which are AI-only breakdowns
        by_day                — [{date, ...totals}] sorted ascending
        by_hour               — [{hour, ...totals}] for raw turns, sorted ascending
        by_source             — {source: totals}; raw source field values
        by_surface            — {bucket: totals}; source bucketed into
                                chat / memory / intel / agents / briefing / ledger /
                                suggestions / vision / compaction / other
        recent                — last 20 records, most-recent first
        window_days           — the ``days`` param used (0 = all time)
        projected_monthly_usd — trailing-7d spend ÷ 7 × 30.4
        target_monthly_usd    — constant cost target ($10.00)
    """
    records = _load_records()
    aggregates = _load_daily_aggregates()
    local_tz = _resolve_tz(tz)

    # Raw records are bucketed to whole *local* calendar days; daily aggregates keep their
    # stored UTC date (they can't be re-bucketed once compacted — the accepted >15-day
    # "simplified" tail). A time-of-day cutoff would include partial boundary-day
    # aggregates but exclude raw records from earlier that same day, producing
    # inconsistent totals — hence calendar-day boundaries throughout.
    #
    # An explicit inclusive [lo, hi] range (the "Today"/"Yesterday" presets) takes
    # precedence over the trailing ``days`` window.
    lo: str | None = start
    hi: str | None = end
    if lo is None and hi is None and days > 0:
        lo = (datetime.now(local_tz) - timedelta(days=days)).strftime("%Y-%m-%d")

    def _in_window(date_str: str) -> bool:
        return (lo is None or date_str >= lo) and (hi is None or date_str <= hi)

    # The inclusive day count of an explicit range; falls back to ``days`` otherwise.
    window_days = days
    if start is not None and end is not None:
        try:
            d0 = datetime.strptime(start, "%Y-%m-%d")
            d1 = datetime.strptime(end, "%Y-%m-%d")
            window_days = (d1 - d0).days + 1
        except ValueError:
            window_days = days

    # Filter raw records by their *local* date; carry the resolved date so the
    # accumulation loop below doesn't re-parse the timestamp.
    filtered_raw: list[tuple[dict, str]] = []
    for r in records:
        rec_date = _record_local_date(r.get("ts") or "", local_tz)
        # Malformed/short ts (rec_date == "") — include rather than silently drop.
        if rec_date and not _in_window(rec_date):
            continue
        filtered_raw.append((r, rec_date))

    # Filter aggregate buckets by date.  Aggregate keys: "YYYY-MM-DD|model|source".
    # "unknown" date buckets are always included (we can't place them in time).
    filtered_agg: list[dict] = []
    for agg in aggregates.values():
        date = agg.get("date", "unknown")
        if date != "unknown" and not _in_window(date):
            continue
        filtered_agg.append(agg)

    # Projection is always computed from the trailing 7 calendar days regardless of
    # the caller's ``days`` window — it answers "at today's burn rate, what will the
    # month cost?" independent of how far back the summary looks.
    seven_day_cost = _compute_7d_totals(records, aggregates, local_tz)
    projected = round(seven_day_cost / _PROJECTION_DAYS * _PROJECTION_MONTH, 6)

    has_any = bool(filtered_raw) or bool(filtered_agg) or bool(records)
    if not has_any:
        return {
            "total": _empty_totals(),
            "by_model": {},
            "by_provider": {},
            "by_search_fetch": {},
            "by_day": [],
            "by_hour": [],
            "by_source": {},
            "by_surface": {},
            "recent": [],
            "window_days": window_days,
            "projected_monthly_usd": projected,
            "target_monthly_usd": _TARGET_MONTHLY_USD,
        }

    total = _empty_totals()
    by_model: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_provider: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_search_fetch: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_day: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_hour: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_source: dict[str, dict] = defaultdict(lambda: _empty_totals())
    by_surface: dict[str, dict] = defaultdict(lambda: _empty_totals())

    # Accumulate raw records. Search/fetch service calls remain available in their own
    # breakdown but never leak into AI totals, timelines, sources, surfaces, or recent turns.
    for r, rec_date in filtered_raw:
        model_id = r.get("model") or ""
        if _is_service_pseudo_model(model_id):
            # Non-LLM search/fetch API cost (asset_brief.py) — never an AI model or
            # provider, so it gets its own breakdown instead of polluting by_model/
            # by_provider. Falls back to the raw pseudo-model id only if the specific
            # service id isn't in the search-provider catalog (never silently dropped).
            _add(by_search_fetch[_search_service_label(model_id) or model_id], r)
            continue
        _add(total, r)
        _add(by_model[r.get("model", "unknown")], r)
        # The explicit provider field (cost_tracker.record's `provider` param) wins
        # when present — set by the AI provider class that actually made the call,
        # from its own known (transport, base_url). Older rows and any caller that
        # hasn't been updated fall back to guessing from the bare model id.
        _add(by_provider[r.get("provider") or provider_of_model(model_id)], r)
        # F1: a missing or malformed ts must never produce a "" date bucket.
        date_key = rec_date or _UNKNOWN_DATE
        _add(by_day[date_key], r)
        hour_key = _record_local_hour(r.get("ts") or "", local_tz)
        if hour_key:
            _add(by_hour[hour_key], r)
        _add(by_source[r.get("source", "unknown")], r)
        _add(by_surface[_source_to_bucket(r.get("source") or "")], r)

    # Accumulate daily-aggregate buckets (pre-evicted, pre-summed). The explicit provider
    # field never survives the rollup (CostLogDailyStore.merge_records keys/sums only
    # date|model|source — see that method's docstring), so aged-out rows always fall back
    # to the model-id guess; that's an accepted precision loss, matching session_id's own
    # "simplified" tail (cost_tracker.record's docstring).
    for agg in filtered_agg:
        agg_model_id = agg.get("model") or ""
        if _is_service_pseudo_model(agg_model_id):
            _add_agg(by_search_fetch[_search_service_label(agg_model_id) or agg_model_id], agg)
            continue
        _add_agg(total, agg)
        _add_agg(by_model[agg.get("model", "unknown")], agg)
        _add_agg(by_provider[provider_of_model(agg_model_id)], agg)
        _add_agg(by_day[agg.get("date", _UNKNOWN_DATE)], agg)
        _add_agg(by_source[agg.get("source", "unknown")], agg)
        _add_agg(by_surface[_source_to_bucket(agg.get("source") or "")], agg)

    return {
        "total": total,
        "by_model": dict(by_model),
        "by_provider": dict(by_provider),
        "by_search_fetch": dict(by_search_fetch),
        "by_day": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "by_hour": [{"hour": h, **v} for h, v in sorted(by_hour.items())],
        "by_source": dict(by_source),
        "by_surface": dict(by_surface),
        # The chart is part of the selected summary, so its turns must obey the same
        # date window as every total and breakdown above. Returning the raw tail here
        # made "Today" quietly plot yesterday's turns whenever today's log was short.
        "recent": [
            record
            for record, _date in filtered_raw
            if not _is_service_pseudo_model(record.get("model") or "")
        ][-20:][::-1],
        "window_days": window_days,
        "projected_monthly_usd": projected,
        "target_monthly_usd": _TARGET_MONTHLY_USD,
    }


# Fraction of the budget at which we consider the month "approaching" the cap.
_APPROACHING_THRESHOLD = 0.80


def cost_budget_status(budget_usd: Optional[float] = None, tz: str | None = None) -> dict:
    """Return month-to-date spend vs the optional monthly budget.

    Computes spend for the current calendar month (1st 00:00 → now, in ``tz``) from both
    cost_log.json and cost_log_daily.json (rolled-up records). ``tz`` is the viewer's IANA
    zone so "this month" matches the rest of the panel; absent/invalid → UTC (legacy).
    ``budget_usd`` is the operator-configured cap; pass ``None`` (or 0 / negative) when no
    budget has been set.

    Returns:
        month_to_date_usd  — float, MTD spend
        budget_usd         — float | None (None when unset)
        pct_used           — float | None (None when no budget)
        approaching        — bool | None (True if pct_used >= 80%)
        over_budget        — bool | None (True if pct_used >= 100%)
    """
    now = datetime.now(_resolve_tz(tz))
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_date = month_start.strftime("%Y-%m-%d")

    records = _load_records()
    mtd = 0.0
    for r in records:
        if _is_service_pseudo_model(r.get("model") or ""):
            continue
        raw_ts: str = r.get("ts") or ""
        if not raw_ts:
            continue
        try:
            ts = datetime.fromisoformat(raw_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= month_start:
            mtd += r.get("cost_usd", 0.0)

    # Include rolled-up daily aggregates for the current month.
    aggregates = _load_daily_aggregates()
    for agg in aggregates.values():
        if _is_service_pseudo_model(agg.get("model") or ""):
            continue
        date = agg.get("date", "unknown")
        if date == "unknown" or date < month_start_date:
            continue
        mtd += agg.get("cost_usd", 0.0)

    # Normalise budget: treat 0 or negative as "unset".
    effective_budget: Optional[float] = None
    if budget_usd is not None and budget_usd > 0:
        effective_budget = float(budget_usd)

    if effective_budget is None:
        return {
            "month_to_date_usd": mtd,
            "budget_usd": None,
            "pct_used": None,
            "approaching": None,
            "over_budget": None,
        }

    pct = mtd / effective_budget
    return {
        "month_to_date_usd": mtd,
        "budget_usd": effective_budget,
        "pct_used": pct,
        "approaching": pct >= _APPROACHING_THRESHOLD,
        "over_budget": pct >= 1.0,
    }
