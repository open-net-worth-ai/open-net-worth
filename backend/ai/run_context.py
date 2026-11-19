"""What an Automation run actually touched, collected from its own tool calls.

The run card has always had a Context section — connectors, accounts it read, skills it
ran, what it remembered — and it has always said "No context recorded", because the only
thing stored under ``context`` was the trigger and its payload. For a manual trigger with
an empty payload that is genuinely nothing.

Nobody needs to declare any of this: the run's tool calls already say it. A
``telegram_send_message`` call IS "it used Telegram". A ``get_transactions(account_id=…)`` IS
"it read DBS Multiplier". So this watches the tool stream and builds the four lists.

Same mutable-box-in-a-ContextVar shape as ``run_plan``, and for the same reason: tools
dispatch as asyncio Tasks and a Task rebinding a ContextVar rebinds only its own copy.
Here the adapter is the only writer — it consumes events on the parent side — but the box
keeps the two modules symmetric and the invariant hard to break by accident later.

Everything is best-effort and cosmetic. A lookup that fails drops the entry rather than
raising: this is a summary of a run, and it must never be the reason a run fails.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tool-input keys that name an account. The value may be an id or a name — both resolve.
_ACCOUNT_KEYS = (
    "account_id",
    "account",
    "account_ids",
    "accounts",
    "from_account",
    "to_account",
)

# Per-list cap. The section is a glance, not an inventory; a run that reads forty accounts
# should say so by being truncated rather than by unrolling down the page.
MAX_PER_GROUP = 8

# The per-provider fetch tools (RFC connectors-as-data-sources) — the direct
# replacement for the retired bulk `connector_sync`, which this module used to watch for
# instead (S-F10). None of these take a `connector`/`connector_id` input field the way
# `get_connector_status`/`set_connector_enabled` do — the provider is implicit in the tool
# NAME — so its owner is read off the registry's own `connector=` tag, not an arg.
_FETCH_TOOL_NAMES = frozenset(
    {
        "simplefin_fetch_transactions",
        "plaid_fetch_transactions",
        "lunch_money_fetch_transactions",
        "lunch_flow_fetch_transactions",
        "ynab_fetch_transactions",
        "enable_banking_fetch_transactions",
        "gocardless_fetch_transactions",
        "yodlee_fetch_transactions",
        "alpaca_fetch_positions",
        "ibkr_fetch_activity",
    }
)

_ctx: ContextVar[Optional[dict]] = ContextVar("automation_run_context", default=None)


def begin() -> Token:
    return _ctx.set({"connectors": [], "accounts": [], "skills": [], "remembered": []})


def reset(token: Token) -> None:
    _ctx.reset(token)


def _add(group: str, value: str) -> None:
    box = _ctx.get()
    if box is None:
        return
    text = (value or "").strip()
    items = box[group]
    if text and text not in items and len(items) < MAX_PER_GROUP:
        items.append(text)


def collected() -> dict:
    """The four lists, omitting any that stayed empty."""
    box = _ctx.get()
    if box is None:
        return {}
    return {group: list(items) for group, items in box.items() if items}


def observe(tool: str, tool_input: Any) -> None:
    """Record what one tool call reveals about the run. Never raises."""
    try:
        _observe(tool, tool_input if isinstance(tool_input, dict) else {})
    except Exception:  # a cosmetic summary must never break a run
        logger.debug("run context: could not read %s", tool, exc_info=True)


def _observe(tool: str, args: dict) -> None:
    if tool == "telegram_send_message":
        _add("connectors", _channel_label("telegram"))
    elif tool == "webhook_send_message":
        _add("connectors", _channel_label("webhook"))
    elif tool in _FETCH_TOOL_NAMES:
        from backend.ai.tools.registry import tool_connector

        owner = tool_connector(tool)
        if owner:
            _add("connectors", _channel_label(owner))
    elif tool in {"get_connector_status", "set_connector_enabled"}:
        for connector in _as_list(
            args.get("connectors") or args.get("connector") or args.get("connector_id")
        ):
            _add("connectors", _channel_label(connector))
    elif tool in {"email_search_messages", "email_get_messages"}:
        _add("connectors", "email")
    elif tool == "activate_skill":
        for skill in _as_list(args.get("skill") or args.get("name")):
            _add("skills", skill)
    elif tool == "remember_fact":
        for fact in _as_list(args.get("fact") or args.get("text") or args.get("content")):
            _add("remembered", fact[:120])

    for key in _ACCOUNT_KEYS:
        for ref in _as_list(args.get(key)):
            name = _account_name(ref)
            if name:
                _add("accounts", name)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int)) and str(v).strip()]
    return []


def _channel_label(value: str) -> str:
    """The connector's id, lowercased — the screen resolves its name and logo from the
    catalog at render time, so a stored run never freezes a display string."""
    return value.strip().lower()


def _account_name(ref: str) -> Optional[str]:
    """Resolve an account id (or an exact name) to its display name.

    An unresolvable ref is DROPPED, not shown raw: "acc_4e82cf390000" in a section headed
    "Accounts it read" is worse than a shorter list.
    """
    from backend.db.account_store import AccountStore

    ref = (ref or "").strip()
    if not ref:
        return None
    for row in AccountStore().query_accounts():
        if row.get("id") == ref:
            return row.get("name") or None
        if (row.get("name") or "") == ref:
            return row["name"]
    return None
