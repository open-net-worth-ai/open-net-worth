"""The ``describe`` tool — lets the model ask the domain about itself.

This is the AI-layer half of the inspectability primitive. All the derivation lives in
``services.domain_introspect`` (and its AST engine); this module only supplies the read side,
because the tool registry lives HERE and ``services`` may never import ``ai``. It passes the
registered read tools down as plain schema dicts, keeping the dependency edge pointing the
legal direction.

Read-only by construction: it parses source and reads schemas, touches no store, and is
registered with ``mutates=()`` so it is headless-safe.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import ConfigDict, Field

from backend.ai.tools.registry import (
    build_tool_schemas,
    mutating_tool_domains,
    refreshing_tool_domains,
    tool,
)
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import domain_introspect

# A view is a READ tool that leaves nothing behind. The registry already knows which tools
# write or refresh — they self-declare ``mutates`` / ``refreshes`` for the freshness seam — so
# the read set is derived from those same declarations instead of a second hand-kept list that
# could disagree with them.
_SELF = "describe"


def _views() -> list[dict]:
    """Every registered read tool, as a view. Derived from the registry's own ``mutates`` /
    ``refreshes`` declarations, so a tool that becomes a writer stops being listed as a view
    automatically."""
    writers = set(mutating_tool_domains()) | set(refreshing_tool_domains())
    return [
        schema
        for schema in build_tool_schemas()
        if schema.get("name") not in writers and schema.get("name") != _SELF
    ]


class DescribeInput(ToolInput):
    """Schema-of-record for ``describe`` (see ``schema_from_model``).

    ``extra="ignore"`` overrides ``ToolInput``'s ``extra="forbid"`` so no
    ``additionalProperties`` keyword is emitted — the pinned literal has never declared
    one, and adding ``false`` here would be a schema change, not a derivation.
    """

    model_config = ConfigDict(extra="ignore")

    target: Optional[str] = Field(
        default=None,
        description=(
            "'' | 'ops' | 'ops/<name>' | 'views' | 'views/<name>'. Prefix-matches. "
            "An unknown target returns the closest matches rather than nothing."
        ),
    )
    q: Optional[str] = Field(
        default=None,
        description="Keyword filter over operation names and their field names.",
    )


@tool(
    name=_SELF,
    description=(
        "Ask the domain about its own shape instead of guessing. Returns, for any write "
        "choke operation, the fields it requires, the fields it accepts, the constraints it "
        "enforces and a worked example — all derived from the code that enforces them, so it "
        "is never staler than the validator. Call this before constructing a change dict for "
        "apply_budget_change, apply_lot_change, apply_option_change, apply_equity_change, "
        "apply_position_change, apply_income_change or any other *_change choke.\n"
        "target: '' for the index, 'ops' to list every operation, 'ops/<name>' for one "
        "(prefix-matches, so 'ops/budget.' returns the whole budget family), 'views' / "
        "'views/<name>' for read tools.\n"
        "q: keyword filter, ranked exact > prefix > substring; matches operation names AND "
        "field names, so q='envelope_id' finds every op that takes one."
    ),
    input_schema=schema_of(DescribeInput, tool_name=_SELF),
)
async def describe_tool(input_data: dict) -> tuple[str, None]:
    result = domain_introspect.describe(
        target=str(input_data.get("target") or ""),
        q=str(input_data.get("q") or ""),
        views=_views(),
    )
    return json.dumps(result, default=str), None
