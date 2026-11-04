"""Conversation-to-review tools; activation remains on the human control plane."""

from __future__ import annotations

import json

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import schema_of
from backend.models.trading import DelegationCreate
from backend.services import trading
from backend.services.trading.delegation import review_delegation


@tool(
    "list_trading_workspaces",
    description=(
        "Read the user's delegated trading workspaces before proposing a new one. "
        "A workspace is a persistent mandate, not a fixed strategy or one decision cycle."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    },
    mutates=(),
)
async def list_trading_workspaces(input_data: dict):
    result = trading.list_workspaces(
        limit=input_data.get("limit", 50), offset=input_data.get("offset", 0)
    )
    return json.dumps(result, ensure_ascii=False, default=str), None


@tool(
    "preview_trading_workspace",
    description=(
        "Validate an UNSAVED trading delegation for human review. Preserve the user's "
        "original instructions verbatim. Discuss funds, period, environment, executor, "
        "markets, order limits and agent budget; resolve missing material choices rather "
        "than inventing them. The executor may research, code, trade or wait autonomously. "
        "This tool never creates a workspace or authorizes live orders. After a valid "
        "preview, call run_screen_command trading.review_draft with the returned delegation "
        "so the user can review and activate it. Use the same contract as Expert setup."
    ),
    input_schema=schema_of(DelegationCreate, tool_name="preview_trading_workspace"),
    mutates=(),
)
async def preview_trading_workspace(input_data: dict):
    draft = DelegationCreate.model_validate(input_data).model_dump(mode="json")
    draft["environment"]["live_authorized"] = False
    result = review_delegation(draft)
    return json.dumps(
        {**result, "saved": False, "delegation": draft, "command": "trading.review_draft"},
        ensure_ascii=False,
        default=str,
    ), None
