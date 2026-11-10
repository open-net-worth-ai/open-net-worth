"""Live-registry facts the claim contract tests judge prose against.

Split out of ``tests/test_claim_contracts.py`` so both claim-contract test modules share
ONE definition of "what counts as a real tool name" — two copies of that judgement would
be exactly the duplicated-claim problem these tests exist to catch.

Everything here imports ``backend.ai.tools`` LAZILY (inside the functions), so importing
this module is free and ``tests/claim_sources.py`` stays usable without booting the
registry at all.
"""

from __future__ import annotations

import difflib
import functools
import re

from tests.claim_sources import REPO_ROOT, tool_shaped_tokens


@functools.lru_cache(maxsize=1)
def _registry():
    import backend.ai.tools  # noqa: F401  (registers every @tool handler)
    from backend.ai.tools.registry import build_tool_schemas, registered_tool_names

    live = frozenset(registered_tool_names())
    return live, tuple(build_tool_schemas())


def live_tools() -> frozenset[str]:
    return _registry()[0]


def resolvable() -> frozenset[str]:
    """Names a stored list or a claim may legitimately use.

    This used to be ``live ∪ aliases`` — a former name was legitimate because dispatch
    resolved it. Nothing resolves one now (ADR 2026-08-25-tool-names-are-final), so the
    set is exactly the live tools and this sweep is correspondingly stricter: prose that
    names a retired tool is prose the model will act on and fail."""
    return _registry()[0]


def tool_verbs() -> set[str]:
    return {n.split("_")[0] for n in live_tools()}


def schemas() -> tuple[dict, ...]:
    return _registry()[1]


@functools.lru_cache(maxsize=1)
def schema_field_vocab() -> frozenset[str]:
    """Every property / required / enum name declared anywhere in the tool schemas.

    A token in this set is a FIELD the model passes, not a tool it calls. Subtracting it
    is what removes ``agent_id``, ``merge_into``, ``run_now``, ``search_queries`` and the
    other ~20 parameter names that are otherwise indistinguishable from tool names.
    """
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "properties" and isinstance(val, dict):
                    names.update(val)
                elif key == "required" and isinstance(val, list):
                    names.update(str(x) for x in val)
                elif key == "enum" and isinstance(val, list):
                    names.update(str(x) for x in val)
                walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    for schema in schemas():
        walk(schema.get("input_schema") or {})
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def backend_symbols() -> frozenset[str]:
    """Every function/class name defined under ``backend/``.

    Used ONLY by Tier B. A tool description may legitimately explain internals ("routes
    through ``apply_position_change``"); a name that is neither a tool NOR a live symbol
    is a dead reference — which is exactly the ``commit_import`` → ``decision_commit``
    class of workflow rot.
    """
    pattern = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
    names: set[str] = set()
    for path in (REPO_ROOT / "backend").rglob("*.py"):
        try:
            names.update(pattern.findall(path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    return frozenset(names)


# ── Reviewed exemptions ───────────────────────────────────────────────────────
#
# Domain vocabulary that is tool-SHAPED but is data, not a callable. Each entry is a
# reviewed decision with a reason, the same convention ``tests/test_tool_domains.py`` uses
# for ``READ_ONLY_TOOLS``. Keep this small: it is an exemption list, not a mute button.
# Staleness is policed by test_not_a_tool_exemptions_are_not_stale.
NOT_A_TOOL: dict[str, str] = {
    "open_setup": "the Connectors screen command connectors.open_setup, not a tool",
    "auto_pay": "a commitment KIND (purchase|loan_repayment|auto_pay|goal), not a tool",
    "auto_applied": "a field on the get_agent_metrics response payload",
    "import_review_default": "a profile.json setting name, not a tool",
    # A self-improvement PROPOSAL KEY an agent's reflection loop may emit, listed in
    # services/agent_reflection.py's forbidden-key set. It is a dict key rather than a
    # `def`, which is why backend_symbols() does not cover it.
    "add_tool": "a reflection-proposal key refused by agent_reflection, not a tool",
    # apply_budget_change `type` DISCRIMINATORS for the batch forms added when the planning
    # tools were pluralized (2026-07-19). Each loops services.planning.apply_budget_change
    # once per item through the same choke — they are values of a `type` field, not tools,
    # so there is nothing for the registry to resolve them against.
    "assign_batch": "an apply_budget_change `type` discriminator, not a tool",
    "move_money_batch": "an apply_budget_change `type` discriminator, not a tool",
    "set_target_batch": "an apply_budget_change `type` discriminator, not a tool",
    "move_category_batch": "an apply_budget_change `type` discriminator, not a tool",
    "group_rename_batch": "an apply_budget_change `type` discriminator, not a tool",
    "group_delete_batch": "an apply_budget_change `type` discriminator, not a tool",
    "set_targets": "an apply_budget_operation `operation` value (zero_based's own targets), not a tool",
    "set_group_targets": "an apply_budget_operation `operation` value (50/30/20's group share), not a tool",
    "set_group_excluded": "an apply_budget_operation `operation` value (50/30/20 group), not a tool",
    "set_cap": "an apply_budget_operation `operation` value (50/30/20), not a tool",
    "set_caps": "an apply_budget_operation `operation` value (50/30/20), not a tool",
    "set_income_categories": "an apply_budget_operation `operation` value (50/30/20), not a tool",
    "set_envelope_excluded": "an apply_budget_operation `operation` value (50/30/20 envelope), not a tool",
    # Connector KIND ids (services/connector_catalog.py) — the values get_connector_status and
    # set_connector_enabled take for `connectors`, not tools. Each starts with the same word as a
    # provider's tools (yahoo_get_ticker_prices, lunch_flow_get_status, ...), which is what makes
    # the tool-shaped-token scan pick them up.
    "yahoo_finance": "a connector kind id, not a tool",
    "enable_banking": "a connector kind id, not a tool",
    "lunch_flow": "a connector kind id, not a tool",
    "lunch_money": "a connector kind id, not a tool",
    # (`set_aside_base`/`unfunded_card_debt_base` lived here while ADR 2026-09-08's payload keys
    # existed. ADR 2026-09-09 replaced them with `held_in_envelopes`/`card_spend_not_covered`,
    # neither of which is tool-shaped — no verb first segment — so no exemption is needed.)
    "google_news": "an RSS feed-source tag (rss:google_news), not a tool",
}


def tier_a_dead_tokens(text: str) -> set[str]:
    """Tier A — INSTRUCTION surfaces (system prompts, skill bodies, bundle bodies).

    Here the model is being told what to DO. A tool-shaped token must name a tool it can
    actually call: naming a service function the model has no access to is the defect, so
    ``backend_symbols()`` deliberately does NOT excuse anything.
    """
    return {
        t
        for t in tool_shaped_tokens(text, tool_verbs())
        if t not in resolvable() and t not in schema_field_vocab() and t not in NOT_A_TOOL
    }


def tier_b_dead_tokens(text: str) -> set[str]:
    """Tier B — tool DESCRIPTIONS, which legitimately explain internals.

    A live backend symbol is allowed; a name matching neither a tool nor any live symbol
    is a dead reference to something that no longer exists.
    """
    return {t for t in tier_a_dead_tokens(text) if t not in backend_symbols()}


def nearest(name: str, pool) -> str:
    match = difflib.get_close_matches(name, sorted(pool), n=1, cutoff=0.6)
    return match[0] if match else "(no close match — remove the claim)"


def fmt(findings: list[str]) -> str:
    return "\n".join("  " + f for f in findings)
