"""AI tool registry — schemas (sent to the model) + the dispatch executor.

This package replaces the old monolithic ``backend/ai/tools.py``. Each tool is a
``@tool``-decorated async handler living in one of the per-group modules below;
importing those modules registers their handlers (and schemas) in ``registry``.
The two public names the rest of the codebase depends on are re-exported
unchanged:

  * ``TOOL_SCHEMAS`` — ``list[dict]`` of ``{name, description, input_schema}``,
    passed to the provider (assistant.py:541).
  * ``execute_tool(name, input_data) -> Tuple[str, Optional[dict]]`` — the async
    dispatcher (assistant.py:620), including the ``unknown tool: <name>`` sentinel.

Group modules import the monkeypatch-sensitive call-time names (``read_rows``,
``_NAMES_CACHE``, ``read_actions``, ``write_actions``, …) through this package
namespace (``import backend.ai.tools as _root; _root.read_rows()``), so the
characterization tests' ``monkeypatch.setattr(backend.ai.tools, "read_rows", …)``
is observed exactly as it was against the old flat module.
"""

from __future__ import annotations

# Import every group module for its registration side effect. Order here ×
# decorator order within each module sets TOOL_SCHEMAS order (not behaviorally
# significant — the contract test snapshots the name *set*).
from backend.ai.tools import (  # noqa: E402  (after the re-exports above)
    _groups,
    accounts,
    aggregation,
    alerts,
    artifacts,
    asset_context,
    asset_values,
    assets,
    audit,
    broker_fetch,
    budget_engines,
    categories,
    categorize,
    checkpoints,
    code,
    commitments,
    connector_config,
    connector_fetch,
    connectors,
    describe,
    docs,
    email_ingest,
    envelopes,
    equity,
    goals,
    holding_plans,
    imports,
    income,
    intel,
    ledger,
    lot_updates,
    lots,
    matching,
    memory,
    misc,
    model_config,
    notifications,
    options,
    payees,
    plan_scenarios,
    planning,
    portfolio,
    prices,
    profile,
    provenance,
    rebalance,
    receipts,
    recurring,
    reports,
    review,
    screen,
    scripts,
    skills,
    spending,
    sql,
    statements,
    uploads,
    vault,
    watchlist,
    web_search_providers,
    xray,
)
from backend.ai.tools import (
    automations as automations,
)

# Names re-exported for backward compat (the characterization tests monkeypatch
# these on this package namespace; handlers read them via this module at call time).
from backend.ai.tools._shared import (
    _NAMES_CACHE,
    _resolve_coingecko_coin,
    _txn_data_from_ai,
    _validate_account_id,
    fetch_coingecko_quotes,
    fetch_history_result,
    fetch_live,
    fetch_yahoo_quotes,
    read_actions,
    read_profile,
    read_rows,
    svc,
    write_actions,
    write_profile,
)
from backend.ai.tools.registry import (  # noqa: E402
    build_tool_schemas,
    dispatch,
    registered_tool_names,
)
from backend.ai.tools.registry import mutated_domains as _registry_mutated_domains  # noqa: E402

# Built once at import — same shape as the old module-level TOOL_SCHEMAS list.
TOOL_SCHEMAS = build_tool_schemas()

# Register the registered-tool-names provider on the core seam so services/ (which
# must not import ai/ — import-linter Contract 3) can validate a skill's
# allowed_tools against the live registry. Mirrors core.io's write-hook inversion.
from backend.core.tool_registry_seam import (  # noqa: E402
    register_tool_name_provider as _register_tool_name_provider,
)
from backend.core.tool_registry_seam import (
    register_tool_writes_provider as _register_tool_writes_provider,
)

_register_tool_name_provider(registered_tool_names)
# ...and the write classifier, derived from each tool's own ``mutates=(…)`` tag, so services/
# can tell a read from a write without importing ai/ (used by self_harness to keep an
# auto-SUGGESTED playbook read-only). A factual classification, not a permission tier.
_register_tool_writes_provider(lambda name: bool(_registry_mutated_domains(name)))

# Register the LLM provider on the core seam so services/ (which must not import ai/ —
# import-linter Contract 1) can run a single-shot completion + parse its JSON without an
# up-import. Consumed by services.reconcile (statement parsing) + the email ingest adapter
# (the LLM email parse). Mirrors core.io's write-hook inversion. (noqa E402: registration
# must run after the registry import above.)
from backend.ai.memory import _parse_json as _ai_parse_json  # noqa: E402
from backend.core.llm_seam import register_llm as _register_llm  # noqa: E402


async def _llm_complete(  # noqa: E402
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    max_tokens: int = 2048,
    source: str = "complete",
    images: list[dict] | None = None,
    response_format: dict | None = None,
    effort: str = "",
) -> str:
    """Single-shot completion on the active AI provider — the seam's ``complete`` hook.

    ``model`` may be a compound "provider_key:model_id" string (e.g. a per-task
    assignment) — the matching provider's credentials are resolved like any other
    override, and ProviderDisabledError propagates per the skip-don't-reroute
    contract. A bare model id runs on the instance default provider as before.
    ``images`` rides through to the provider's multimodal path (vision statement parse).
    ``response_format``/``effort`` carry structured-output + reasoning-budget requests
    (the OpenAI provider honors them with a graceful retry-without; Anthropic ignores).
    """
    from backend.ai.assistant import get_provider

    bare_model = model
    if model and ":" in model:
        from backend.db.model_config_store import ModelConfigStore

        resolved = ModelConfigStore().resolve_override(model)
        if resolved is not None:
            bare_model = resolved["model"]

    return await get_provider(model or None).complete(
        prompt,
        system=system,
        model=bare_model,
        max_tokens=max_tokens,
        source=source,
        images=images,
        response_format=response_format,
        effort=effort,
    )


_register_llm(_llm_complete, _ai_parse_json)

# Register the batch provider on the core seam.  Routing (which adapter runs for
# the active config, by ACTUAL endpoint capability — not the transport label) lives
# in ai/batch/dispatch.py + ai/batch/routing.py so it's independently unit-testable.
from backend.ai.batch.dispatch import build_batch_hooks as _build_batch_hooks  # noqa: E402
from backend.core.llm_seam import register_batch as _register_batch  # noqa: E402

_register_batch(*_build_batch_hooks())

# The public async dispatcher — same signature & tuple contract as before.
execute_tool = dispatch

__all__ = [
    "TOOL_SCHEMAS",
    "execute_tool",
    "registered_tool_names",
    "_txn_data_from_ai",
    "_validate_account_id",
    # re-exported call-time names
    "read_rows",
    "_NAMES_CACHE",
    "read_actions",
    "write_actions",
    "fetch_live",
    "fetch_yahoo_quotes",
    "fetch_coingecko_quotes",
    "fetch_history_result",
    "_resolve_coingecko_coin",
    "read_profile",
    "write_profile",
    "svc",
    # group modules (kept importable)
    "_groups",
    "accounts",
    "aggregation",
    "alerts",
    "artifacts",
    "asset_context",
    "asset_values",
    "assets",
    "audit",
    "broker_fetch",
    "ibkr",
    "budget_engines",
    "connector_config",
    "connector_fetch",
    "connectors",
    "describe",
    "docs",
    "categories",
    "categorize",
    "checkpoints",
    "code",
    "commitments",
    "email_ingest",
    "envelopes",
    "equity",
    "goals",
    "holding_plans",
    "imports",
    "income",
    "intel",
    "ledger",
    "lot_updates",
    "lots",
    "matching",
    "memory",
    "misc",
    "model_config",
    "notifications",
    "options",
    "payees",
    "plan_scenarios",
    "planning",
    "portfolio",
    "prices",
    "profile",
    "provenance",
    "rebalance",
    "receipts",
    "recurring",
    "reports",
    "review",
    "rules",
    "scheduling",
    "screen",
    "scripts",
    "skills",
    "spending",
    "sql",
    "statements",
    "uploads",
    "vault",
    "watchlist",
    "web_search_providers",
    "xray",
]
