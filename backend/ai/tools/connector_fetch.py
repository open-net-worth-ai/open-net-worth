"""Bank connector fetch tools — one per offered provider (RFC ``connectors-as-data-sources``).

Each ``<provider>_fetch_transactions`` tool is the ONLY way a Lucius turn sees a bank
provider's rows: it calls ``services.connector_fetch.fetch(provider_key, ...)`` (the
provider-agnostic pipeline — dormant gate, fetch, sanitize, stash raw evidence, resolve the
provider-account link, filter already-booked rows, detect a vanished/modified row, warm FX,
record status) and returns the RFC §A result shape verbatim. Nothing here books a row —
that is the model's OWN next call to ``add_transactions`` (or a position tool for the
brokers in ``broker_fetch.py``), through the ordinary ledger choke, which is what makes a
re-fetch idempotent (a ref it already holds is refused there, not here).

Every tool shares ONE schema (``FetchTransactionsInput``) and is built by
``_register_fetch_tool`` so seven near-identical registrations don't repeat the same
description/handler five times over (see ``docs/ai/TOOL_CONVENTIONS.md`` on
``schema_of``/derived schemas — each still gets its OWN ``schema_of(..., tool_name=...)``
call so ``derived_schemas()`` records all seven individually).

Yodlee's tool is registered here too (byte-identical shape) but is hidden from discovery
AND refused at dispatch by ``ai.tools._connector_gate.DISABLED_CONNECTORS`` — enterprise-only
pricing (ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing``), same policy as
``yodlee_get_status``.

``connector_sync`` and every ``<provider>_sync_now`` tool are RETIRED (RFC §E, no aliases —
ADR ``2026-08-25-tool-names-are-final``); do not name them in a description (the naming
linter, ``backend/scripts/tool_inventory.py --lint``, fails on a dead-name reference).
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, explicit_null, schema_of
from backend.services.aggregation.providers import PROVIDERS


class FetchTransactionsInput(ToolInput):
    """Schema-of-record shared by every ``<provider>_fetch_transactions`` /
    ``ibkr_fetch_activity`` tool (see ``schema_from_model``)."""

    since: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description=(
            "ISO date (YYYY-MM-DD), inclusive. Omit for the provider's own default lookback "
            "window (wide enough to see a late-posted row or a bank-side correction)."
        ),
    )
    before: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description="ISO date (YYYY-MM-DD), inclusive. Omit for today.",
    )
    account: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description=(
            "Limit to one provider account id (from this tool's own 'accounts' list, or a "
            "prior call). Omit for every linked account."
        ),
    )
    include_booked: bool = Field(
        default=False,
        description=(
            "False (default): only rows NOT already booked — 'new' means no live ledger row "
            "carries the row's ref, never 'since the last run', so a re-fetch of the same "
            "window is safe. True: also return already-booked rows, each carrying its "
            "booked_txn_id — use this to list a window's full activity."
        ),
    )
    include_pending: bool = Field(
        default=False,
        description=(
            "False (default): skip not-yet-posted/pending rows. True: include them too — a "
            "pending row that later posts may carry supersedes_ref pointing at the pending "
            "row it replaces."
        ),
    )
    limit: int = Field(
        default=200,
        description="Max transactions to return (hard cap 500). Use offset to page further.",
    )
    offset: int = Field(default=0, description="Skip this many matching rows before returning.")


def _register_fetch_tool(provider_key: str, tool_name: str) -> None:
    """Register one ``<provider>_fetch_transactions`` tool. ``connector=provider_key`` gates
    discovery + owns the permission decision (``registry.tool``'s own docstring); the handler
    itself does the SAME work regardless of provider — ``services.connector_fetch.fetch``
    already knows how to route to that provider's own wire protocol."""
    provider = PROVIDERS[provider_key]
    description = (
        f"Fetch {provider.label} transactions for a date window (default: the provider's own "
        f"{provider.lookback_days}-day lookback). Returns the window, the linked provider "
        "accounts (with linked_account_id — the app account each is tied to, or null if "
        "unlinked), the transaction drafts themselves (external_refs/date/amount/currency/"
        "description/pending — amount is signed, positive = money in), and any modified/"
        "vanished changes detected since the last fetch. Rows are draft-shaped: pass "
        "linked_account_id straight through as from_account_id/to_account_id when you call "
        "add_transactions — never re-derive it. include_booked=true also returns rows already "
        "booked (each with its booked_txn_id); leave it false to see only what's new. A "
        "non-'ok' status is not a call failure — it's the connector's own state, read it "
        "and tell the user. Never returns secrets. "
        f"{provider.note}"
    )

    async def handler(input_data: dict):
        from backend.services import connector_fetch as connector_fetch_service

        result = await asyncio.to_thread(
            connector_fetch_service.fetch,
            provider_key,
            since=input_data.get("since"),
            before=input_data.get("before"),
            account=input_data.get("account"),
            include_booked=bool(input_data.get("include_booked") or False),
            include_pending=bool(input_data.get("include_pending") or False),
            limit=input_data.get("limit") or 200,
            offset=input_data.get("offset") or 0,
        )
        return json.dumps(result, ensure_ascii=False), None

    tool(
        tool_name,
        description=description,
        input_schema=schema_of(FetchTransactionsInput, tool_name=tool_name),
        # A read that leaves a raw-evidence snapshot per row and the connector's own
        # last-synced stamp behind — the email_search_messages precedent.
        refreshes=("config",),
        connector=provider_key,
    )(handler)


_register_fetch_tool("simplefin", "simplefin_fetch_transactions")
_register_fetch_tool("plaid", "plaid_fetch_transactions")
_register_fetch_tool("lunch_money", "lunch_money_fetch_transactions")
_register_fetch_tool("lunch_flow", "lunch_flow_fetch_transactions")
_register_fetch_tool("ynab", "ynab_fetch_transactions")
_register_fetch_tool("enable_banking", "enable_banking_fetch_transactions")
_register_fetch_tool("yodlee", "yodlee_fetch_transactions")
