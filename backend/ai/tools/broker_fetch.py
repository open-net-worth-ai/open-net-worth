"""Broker connector fetch tools — Alpaca (positions-only) and IBKR (positions + activity),
RFC ``connectors-as-data-sources``.

Both call ``services.connector_fetch.fetch(provider_key, ...)`` — the SAME provider-agnostic
pipeline the bank tools in ``connector_fetch.py`` use (dormant gate, sanitize, stash raw
evidence, resolve the provider-account link, filter already-booked rows, warm FX, record
status) — then reshape its result for a broker caller: ``positions`` comes from
``FetchWindow.extra["positions"]`` (never ``rows``, which the shared service reserves for
transaction drafts), with each position's own ``linked_account_id`` filled in from the SAME
accounts block the shared pipeline already resolved. Alpaca has no transactions at all (a
positions+cash SNAPSHOT, ``lookback_days=0``); IBKR is a hybrid and returns both.

Nothing here books a position or a transaction — that is the model's own next call to
``add_positions``/``update_position`` (positions) or ``add_transactions`` (IBKR trades/cash
events), through the ordinary choke points.
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools.connector_fetch import FetchTransactionsInput
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import schema_of
from backend.services.aggregation.providers import PROVIDERS

_ALPACA = PROVIDERS["alpaca"]
_IBKR = PROVIDERS["ibkr"]


def _with_positions(result: dict) -> tuple[list[dict], dict, dict]:
    """Pull ``positions`` out of the shared service's ``extra`` channel (present only for a
    broker fetch — a bank fetch's ``window.extra`` is always empty, so this key never
    appears there) and fill in each one's ``linked_account_id`` from the SAME accounts block
    the shared pipeline already resolved — never a second lookup."""
    linked_by_id = {
        a["provider_account_id"]: a.get("linked_account_id") for a in result["accounts"]
    }
    extra = result.pop("extra", {}) or {}
    positions = []
    for pos in extra.get("positions") or []:
        pos = dict(pos)
        pos["linked_account_id"] = linked_by_id.get(pos.get("provider_account_id"))
        positions.append(pos)

    onboarding = extra.get("onboarding_starting_balances") or []
    for bal in onboarding:
        bal["linked_account_id"] = linked_by_id.get(bal.get("provider_account_id"))

    return positions, extra, result


@tool(
    "alpaca_fetch_positions",
    description=(
        "Fetch Alpaca's current holdings — a positions + cash SNAPSHOT, not a date-windowed "
        "feed (there is no 'since'/'before' to ask for; any window fields are ignored). "
        "Returns the linked brokerage account (with linked_account_id), the position list "
        "(symbol, instrument_hint, qty, avg_cost, market_value, currency, "
        "linked_account_id), and cash/buying_power. Positions are snapshot-shaped: use "
        "resolve_instruments then add_positions/update_position to apply them — never "
        "remove a position yourself; tell the user about any that are missing from this "
        "snapshot instead. A non-'ok' status is not a call failure — read it. Never "
        f"returns secrets. {_ALPACA.note}"
    ),
    input_schema=schema_of(FetchTransactionsInput, tool_name="alpaca_fetch_positions"),
    refreshes=("config",),
    connector="alpaca",
)
async def alpaca_fetch_positions(input_data: dict):
    from backend.services import connector_fetch as connector_fetch_service

    result = await asyncio.to_thread(
        connector_fetch_service.fetch,
        "alpaca",
        since=input_data.get("since"),
        before=input_data.get("before"),
        account=input_data.get("account"),
    )
    positions, _, result = _with_positions(result)
    result.pop("transactions", None)
    result.pop("changes", None)
    result["positions"] = positions
    return json.dumps(result, ensure_ascii=False), None


@tool(
    "ibkr_fetch_activity",
    description=(
        "Fetch Interactive Brokers (Flex Web Service) holdings + activity. Positions are a "
        "SNAPSHOT (never windowed) — symbol, instrument_hint (equity/crypto/manual — manual "
        "= no live price source, e.g. options/futures), qty, avg_cost, market_value, "
        "currency, linked_account_id. Trades/cash events (dividends, interest, fees) are "
        f"windowed transaction drafts (default {_IBKR.lookback_days}-day lookback), shaped "
        "like a bank fetch tool row — pass linked_account_id straight through as "
        "from_account_id/to_account_id to add_transactions. include_booked=true also "
        "returns already-booked trades/cash events, each with its booked_txn_id. Never "
        "remove a position yourself; tell the user about any missing from the snapshot "
        "instead. First IBKR fetch with no starting cash balances: use "
        "onboarding_starting_balances EXACT values (amount, currency, as_of) to call "
        "set_account_opening for each wallet before applying any transactions (create the "
        "account first if linked_account_id is null). VERY IMPORTANT: then discard every "
        "fetched transaction on or before that as_of date — apply only later ones, or you "
        "double-count. A non-'ok' status is not a call failure — read it. Never returns "
        f"secrets. {_IBKR.note}"
    ),
    input_schema=schema_of(FetchTransactionsInput, tool_name="ibkr_fetch_activity"),
    refreshes=("config",),
    connector="ibkr",
)
async def ibkr_fetch_activity(input_data: dict):
    from backend.services import connector_fetch as connector_fetch_service

    result = await asyncio.to_thread(
        connector_fetch_service.fetch,
        "ibkr",
        since=input_data.get("since"),
        before=input_data.get("before"),
        account=input_data.get("account"),
        include_booked=bool(input_data.get("include_booked") or False),
        include_pending=bool(input_data.get("include_pending") or False),
        limit=input_data.get("limit") or 200,
        offset=input_data.get("offset") or 0,
    )
    positions, extra, result = _with_positions(result)
    result["positions"] = positions
    if extra.get("onboarding_starting_balances"):
        result["onboarding_starting_balances"] = extra["onboarding_starting_balances"]
    return json.dumps(result, ensure_ascii=False), None
