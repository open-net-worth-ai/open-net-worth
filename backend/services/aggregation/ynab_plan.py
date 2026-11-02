"""Read-only views of one YNAB plan, for Lucius: its accounts, and one month's budget.

The YNAB connector holds only the token; which plan and which accounts to read is decided per
call (``ai/tools/ynab.py``), never configured in advance. Every request goes through
``ynab._safe_get`` (stored token, fixed base URL, size cap, sanitized errors). Amounts arrive in
YNAB milliunits and leave as plain decimals; every name is third-party text and is sanitized
here, where it enters the app.
"""

from __future__ import annotations

from typing import Optional

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.db.ynab_config_store import YnabConfigStore
from backend.services.aggregation import ynab


def _amount(milli: object) -> float:
    try:
        return round(int(milli) / 1000, 2)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _name(value: object, fallback: str) -> str:
    return sanitize_untrusted(str(value or fallback), max_len=120)


def _data(store: YnabConfigStore, path: str) -> dict:
    resp = ynab._safe_get(store, path, {})
    try:
        body = resp.json()
    except ValueError as exc:
        raise ExternalServiceError(
            f"YNAB returned a malformed response ({type(exc).__name__})"
        ) from None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise ExternalServiceError(f"YNAB returned an unexpected response for {path}")
    return data


def plan(store: YnabConfigStore, plan_id: Optional[str]) -> dict:
    """``{id, name, currency}`` for ``plan_id`` — or the token's only plan when none is given.
    Raises ``ValidationError`` naming the real plans when it is not one of them, or when the
    token sees several and none was named."""
    plans = ynab.list_budgets(store)
    if plan_id:
        for p in plans:
            if p["id"] == plan_id:
                return p
        names = ", ".join(f"'{p['name']}' ({p['id']})" for p in plans) or "none"
        raise ValidationError(f"No YNAB plan {plan_id!r} for this token. Plans: {names}")
    if len(plans) == 1:
        return plans[0]
    names = ", ".join(f"'{p['name']}' ({p['id']})" for p in plans) or "none"
    raise ValidationError(f"Name the YNAB plan to read (plan_id). Plans: {names}")


def accounts(store: YnabConfigStore, plan_id: str) -> list[dict]:
    """Every open (non-deleted) account in the plan, with YNAB's own balances."""
    rows = _data(store, f"/plans/{plan_id}/accounts").get("accounts") or []
    return [
        {
            "id": a["id"],
            "name": _name(a.get("name"), "YNAB account"),
            "type": a.get("type"),
            "on_budget": bool(a.get("on_budget")),
            "closed": bool(a.get("closed")),
            "balance": _amount(a.get("balance")),
            "cleared_balance": _amount(a.get("cleared_balance")),
        }
        for a in rows
        if isinstance(a, dict) and a.get("id") and not a.get("deleted")
    ]


def _groups(store: YnabConfigStore, plan_id: str) -> dict[str, str]:
    """category id -> its group's name."""
    out: dict[str, str] = {}
    for g in _data(store, f"/plans/{plan_id}/categories").get("category_groups") or []:
        if not isinstance(g, dict) or g.get("deleted"):
            continue
        for c in g.get("categories") or []:
            if isinstance(c, dict) and c.get("id"):
                out[c["id"]] = _name(g.get("name"), "Group")
    return out


def month(store: YnabConfigStore, plan_id: str, ym: Optional[str], include_hidden: bool) -> dict:
    """One month of the plan's budget (YYYY-MM; omitted = YNAB's current month): Ready to
    Assign and its totals, then each category's assigned, activity and available."""
    path_month = f"{ym}-01" if ym else "current"
    m = _data(store, f"/plans/{plan_id}/months/{path_month}").get("month") or {}
    group_of = _groups(store, plan_id)
    categories = [
        {
            "id": c["id"],
            "name": _name(c.get("name"), "Category"),
            "group": group_of.get(c["id"]),
            "assigned": _amount(c.get("budgeted")),
            "activity": _amount(c.get("activity")),
            "available": _amount(c.get("balance")),
            "hidden": bool(c.get("hidden")),
        }
        for c in m.get("categories") or []
        if isinstance(c, dict)
        and c.get("id")
        and not c.get("deleted")
        and (include_hidden or not c.get("hidden"))
    ]
    return {
        "month": str(m.get("month") or "")[:7],
        "ready_to_assign": _amount(m.get("to_be_budgeted")),
        "income": _amount(m.get("income")),
        "assigned": _amount(m.get("budgeted")),
        "activity": _amount(m.get("activity")),
        "age_of_money": m.get("age_of_money"),
        "categories": categories,
    }


def _store() -> YnabConfigStore:
    store = YnabConfigStore()
    if not store.is_configured():
        raise ValidationError("YNAB isn't connected: the user adds a token in Connectors → YNAB.")
    return store


def list_plans() -> list[dict]:
    """The plans the saved token can see: ``[{id, name, currency}]``."""
    return ynab.list_budgets(_store())


def detail(plan_id: Optional[str], ym: Optional[str], include_hidden: bool = False) -> dict:
    """One plan, read live: its accounts with balances, and one month's budget."""
    store = _store()
    p = plan(store, plan_id)
    return {
        "plan": p,
        "accounts": accounts(store, p["id"]),
        "budget": month(store, p["id"], ym, include_hidden),
    }
