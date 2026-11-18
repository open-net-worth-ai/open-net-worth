"""YNAB's own transaction rows, as ``budget_app_snapshots`` keeps them: each as the plan export
sent it, with a split's lines nested under it the way the per-account endpoint nests them. Nothing
of ours (``payee_name``) is added, so the raw is YNAB's, not the engine's copy."""

from __future__ import annotations

from backend.services import budget_app_snapshots as snapshots
from backend.services import ledger as ledger_service
from backend.services.aggregation import ynab as ynab_service


def from_plan(plan: dict) -> list[dict]:
    """One row per live, non-zero transaction of a plan export (whole, or a delta)."""
    payees = {p["id"]: p.get("name") for p in plan.get("payees") or []}
    lines: dict[str, list[dict]] = {}
    for s in plan.get("subtransactions") or []:
        lines.setdefault(s.get("transaction_id"), []).append(s)
    currency = (plan.get("currency_format") or {}).get("iso_code") or ledger_service.base_currency()
    rows = []
    for t in plan.get("transactions") or []:
        named = {**t, "payee_name": payees.get(t.get("payee_id"))}
        fetched = ynab_service._txn_to_row(named, str(t.get("account_id") or ""), currency.upper())
        if fetched is None or not t.get("account_id"):
            continue
        rows.append(
            snapshots.snapshot_row(
                fetched["external_refs"][0]["ref"],
                {**t, "subtransactions": lines.get(t["id"], [])},
                date=fetched["date"],
                amount=fetched["amount"],
                currency=fetched["currency"],
                description=fetched["description"],
                account=t["account_id"],
            )
        )
    return rows
