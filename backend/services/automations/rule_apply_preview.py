"""What applying a Rule to past transactions WOULD change — the approval card's preview.

Lucius's ``apply_rule_to_transactions`` used to put up a card with nothing but a rule id and
transaction ids. This builds the same per-row plan the write uses (``rule_apply.rule_plan`` /
``rules_plan``), so the card names the Rule and shows, for each transaction, what changes from →
to, and which rows are skipped and why — read-only, nothing is written.
"""

from __future__ import annotations

from typing import Callable

from . import rule_apply

# The card lists this many transactions, then says how many more there are.
MAX_ITEMS = 25

_FIELD_NAMES = {
    "category": "Category",
    "tags": "Tags",
    "description": "Description",
    "memo": "Memo",
    "payee_id": "Payee",
    "splits": "Split",
}


def preview_apply(rule: dict | None, transaction_ids: object) -> dict:
    """``{"items": [...]}`` in the card's ``field_diff`` shape for ``rule`` (a stored Rule) or,
    with ``rule`` None, every active Rule in order."""
    from backend.services import ledger, payee_registry
    from backend.services.categories import all_category_rows

    ids = rule_apply._check_ids(transaction_ids)
    plan = rule_apply.rule_plan(_body(rule)) if rule else rule_apply.rules_plan()
    labels = _Labels(
        {c["id"]: c.get("label") or c["id"] for c in all_category_rows()},
        payee_registry.payee_names(),
    )
    opening = rule_apply._opening_ids()
    items: list[dict] = []
    counts = {"change": 0, "unchanged": 0, "skipped": 0}
    for txn_id in ids:
        row = ledger.transactions.find_by_id(txn_id)
        item = _item(txn_id, row, plan, opening, labels, counts)
        if item:
            items.append(item)
    head = {
        "id": (rule or {}).get("id") or "rules",
        "name": f"Rule “{rule.get('name')}”" if rule else "Every active rule, top to bottom",
        "changes": {"Transactions": {"old": f"{len(ids)} selected", "new": _summary(counts)}},
    }
    shown = items[:MAX_ITEMS]
    if len(items) > MAX_ITEMS:
        more = len(items) - MAX_ITEMS
        shown.append({"id": "more", "name": f"…and {more} more", "changes": {}})
    return {"items": [head, *shown]}


def _body(rule: dict) -> dict:
    return {
        "id": rule.get("id"),
        "name": rule.get("name"),
        "trigger": rule.get("trigger"),
        "execution": rule.get("execution"),
    }


def _item(
    txn_id: str,
    row: dict | None,
    plan: Callable,
    opening: set[str],
    labels: "_Labels",
    counts: dict,
) -> dict | None:
    name = labels.row_name(txn_id, row)
    reason = rule_apply._protected(row, opening)
    if reason is None:
        changes, why, _used = plan(row)
        if changes:
            counts["change"] += 1
            return {
                "id": txn_id,
                "name": name,
                "changes": {
                    _FIELD_NAMES.get(field, field): {
                        "old": labels.value(field, (row or {}).get(field)),
                        "new": labels.value(field, value),
                    }
                    for field, value in changes.items()
                    if field in _FIELD_NAMES
                },
            }
        if not why:
            counts["unchanged"] += 1
            return None
        reason = why
    counts["skipped"] += 1
    return {"id": txn_id, "name": name, "changes": {"Skipped": {"old": "", "new": reason}}}


def _summary(counts: dict) -> str:
    parts = [f"{counts['change']} will change"]
    if counts["unchanged"]:
        parts.append(f"{counts['unchanged']} already match")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped")
    return ", ".join(parts)


class _Labels:
    """Ids → the names a person reads on the card."""

    def __init__(self, categories: dict[str, str], payees: dict[str, str]):
        self.categories = categories
        self.payees = payees

    def row_name(self, txn_id: str, row: dict | None) -> str:
        if not row:
            return txn_id
        leg = "from" if row.get("from_account_id") else "to"
        amount = row.get(f"{leg}_amount")
        money = f"{row.get(f'{leg}_currency') or ''} {abs(float(amount)):,.2f}" if amount else ""
        who = self.payees.get(str(row.get("payee_id"))) or row.get("description") or txn_id
        return " · ".join(part for part in (str(row.get("date") or ""), who, money.strip()) if part)

    def value(self, field: str, value: object) -> str:
        if field == "category":
            if value == "split":
                return "Split"
            return self.categories.get(str(value), str(value)) if value else "Uncategorized"
        if field == "payee_id":
            return self.payees.get(str(value), str(value)) if value else "None"
        if field == "tags":
            return ", ".join(value) if isinstance(value, list) and value else "None"
        if field == "splits":
            return self._splits(value)
        return str(value or "") or "Empty"

    def _splits(self, value: object) -> str:
        if not isinstance(value, list) or not value:
            return "None"
        lines = []
        for line in value:
            if isinstance(line, dict):
                label = self.categories.get(str(line.get("category")), str(line.get("category")))
                lines.append(f"{float(line.get('amount') or 0):,.2f} {label}")
        return " + ".join(lines)
