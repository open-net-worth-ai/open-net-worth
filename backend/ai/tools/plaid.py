"""Plaid actions for Lucius beyond reading: hand the user a sign-in link to reconnect a bank.

The sign-in itself is always the user's, on Plaid's own page — Lucius never sees a bank
credential, and this tool changes no key, bank or account link (linking a NEW bank stays on
the Plaid page). Service: ``services.aggregation.plaid_reconnect``; ADR
``2026-11-03-lucius-may-hand-the-user-a-plaid-reconnect-link``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, explicit_null, schema_of


class PlaidReconnectBankInput(ToolInput):
    bank: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description=(
            "The linked bank's name as plaid_get_status lists it. Omit when only one bank "
            "needs reconnecting."
        ),
    )


@tool(
    "plaid_reconnect_bank",
    description=(
        "Get a Plaid sign-in link that renews ONE linked bank whose login expired (plaid_get_status "
        "shows needs_reconnect; a fetch warns 'sign-in expired'). Same bank, same accounts — every "
        "link survives. Use only when the user asks or a bank needs it. Give the user the link as "
        "a markdown link ('Sign in to <bank> again'); THEY sign in at their bank, you never ask "
        "for a password or code. It expires, so don't reuse an old one. After they say they're "
        "done, call plaid_list_accounts to confirm. Can't link a new bank — that's Connectors → Plaid."
    ),
    input_schema=schema_of(PlaidReconnectBankInput, tool_name="plaid_reconnect_bank"),
    # Writes the in-flight Hosted Link session (link token + which bank it renews).
    mutates=("config",),
    connector="plaid",
)
async def plaid_reconnect_bank(input_data: dict):
    from backend.services.aggregation import plaid_reconnect

    result = await asyncio.to_thread(plaid_reconnect.start_reconnect, input_data.get("bank") or "")
    return json.dumps(result, ensure_ascii=False), None
