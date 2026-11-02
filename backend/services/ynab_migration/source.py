"""Reads one YNAB plan for the migration, through the importer's own guarded request
(``aggregation.ynab._safe_send``: stored token, size cap, sanitized errors). Read-only here;
``call`` is also how the two-way sync writes."""

from __future__ import annotations

import logging
import time
from typing import Callable

from backend.core.errors import ExternalServiceError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services.aggregation import ynab as ynab_service

logger = logging.getLogger(__name__)

# The whole plan in one document: ~10k transactions and ~90 months of ~250 categories is ~15 MiB,
# so 96 MiB leaves room for a much longer history while still bounding memory.
_PLAN_MAX_BYTES = 96 * 1024 * 1024
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_SLEEP = 60.0  # YNAB allows 200 requests/hour; a 429 clears slowly


def call(
    store: YnabConfigStore,
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    *,
    max_bytes=None,
    retry: bool = True,
) -> dict:
    """One guarded YNAB request (``aggregation.ynab._safe_send``: fixed base URL, stored token,
    size cap, sanitized errors) retried on HTTP 429 unless ``retry=False`` (the sync fails fast and waits for its next run); returns the response's ``data``. The two-way
    sync sends its writes through here too."""
    kwargs = {"max_bytes": max_bytes} if max_bytes else {}
    retries = _RATE_LIMIT_RETRIES if retry else 0
    for attempt in range(retries + 1):
        try:
            resp = ynab_service._safe_send(store, method, path, params or {}, body, **kwargs)
            break
        except ExternalServiceError as exc:
            if "HTTP 429" not in str(exc) or attempt == retries:
                raise
            logger.info("YNAB rate limit; waiting before retrying %s", path)
            time.sleep(_RATE_LIMIT_SLEEP)
    try:
        data = resp.json()["data"]
    except (ValueError, KeyError, TypeError):
        raise ExternalServiceError(f"YNAB returned an unexpected response for {path}") from None
    if not isinstance(data, dict):
        raise ExternalServiceError(f"YNAB returned an unexpected response for {path}")
    return data


def _data(store: YnabConfigStore, path: str, params: dict | None = None, *, max_bytes=None) -> dict:
    return call(store, "GET", path, params, max_bytes=max_bytes)


def list_plans(store: YnabConfigStore) -> list[dict]:
    return ynab_service.list_budgets(store)


def fetch_accounts(store: YnabConfigStore, plan_id: str) -> list[dict]:
    return _data(store, f"/plans/{plan_id}/accounts")["accounts"]


def fetch_month(store: YnabConfigStore, plan_id: str, month: str) -> dict:
    return _data(store, f"/plans/{plan_id}/months/{month}-01")["month"]


def fetch_plan(
    store: YnabConfigStore,
    plan_id: str,
    *,
    with_budget: bool,
    progress: Callable[[str, int, int], None] = lambda *_: None,
) -> dict:
    """Accounts, category groups, every transaction and (optionally) every budget month, from ONE
    full-plan export. YNAB allows 200 requests an hour; per-account and per-month reads of a
    long-lived plan spent ~140 of them, so a second run within the hour hit HTTP 429."""
    progress("reading the plan", 0, 1)
    data = _data(store, f"/plans/{plan_id}", max_bytes=_PLAN_MAX_BYTES)
    plan = data.get("plan") or data.get("budget")
    if not isinstance(plan, dict):
        raise ExternalServiceError("YNAB returned an unexpected response for the plan")
    progress("reading the plan", 1, 1)
    return {
        "accounts": plan.get("accounts") or [],
        "groups": _nest_groups(plan),
        "transactions": _nest_transactions(plan),
        "months": (plan.get("months") or []) if with_budget else [],
    }


def _nest_groups(plan: dict) -> list[dict]:
    by_group: dict[str, list] = {}
    for c in plan.get("categories") or []:
        by_group.setdefault(c.get("category_group_id"), []).append(c)
    return [
        {**g, "categories": by_group.get(g["id"], [])} for g in plan.get("category_groups") or []
    ]


def _nest_transactions(plan: dict) -> list[dict]:
    """The export is flat: payees by id, split lines in their own list. Rebuild the shape the
    per-account endpoint returns (``payee_name``, nested ``subtransactions``)."""
    payees = {p["id"]: p.get("name") for p in plan.get("payees") or []}
    subs: dict[str, list] = {}
    for s in plan.get("subtransactions") or []:
        subs.setdefault(s.get("transaction_id"), []).append(
            {**s, "payee_name": payees.get(s.get("payee_id"))}
        )
    return [
        {**t, "payee_name": payees.get(t.get("payee_id")), "subtransactions": subs.get(t["id"], [])}
        for t in plan.get("transactions") or []
    ]
