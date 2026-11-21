"""Human-readable projections for private deterministic Automation executors."""

from __future__ import annotations

import json


def describe_lucius_transaction_event(trigger: dict, spec: dict) -> str:
    """Render the durable structured transaction-event spec as Lucius instructions."""
    action = dict(spec.get("set") or {})
    priority = int(spec.get("priority", 100))
    stop = bool(spec.get("stop", False))
    override = bool(spec.get("override_existing", False))
    when = dict(trigger.get("when") or {})
    return (
        "This Automation handles the transaction event supplied with the run. Re-check the "
        "match conditions below against that transaction and take no action if any condition "
        "does not match. Otherwise call update_transactions for only that transaction and "
        "apply exactly the transformation below. A category replaces an existing category "
        f"only when override_existing is true ({str(override).lower()}); tags are additive "
        "without duplicates; note_append and memo_append append once; route_to_account uses "
        "the ordinary validated account move. Do not modify any other field or transaction. "
        f"Execution order is priority {priority} with stop={str(stop).lower()}; those values "
        "coordinate matching Automations and do not authorize extra writes.\n\n"
        "Match conditions:\n"
        f"{json.dumps(when, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Transformation:\n"
        f"{json.dumps(action, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def _label(store_name: str, ref: object) -> str:
    """A category, payee or account id as its name; the id itself when it cannot be read."""
    try:
        if store_name == "category":
            from backend.db.category_store import CategoryStore

            return str((CategoryStore().get(ref) or {}).get("label") or ref)
        if store_name == "payee":
            from backend.services import payee_registry

            return payee_registry.payee_names().get(str(ref)) or str(ref)
        from backend.db.account_store import AccountStore

        return str((AccountStore().find_by_id(str(ref)) or {}).get("name") or ref)
    except Exception:  # display fallback must not block a referential rewrite
        return str(ref)


def _number(value: object) -> str:
    return f"{float(value):,.12g}"  # type: ignore[arg-type]


def _describe_condition(condition: dict) -> str:
    field, op, value = condition.get("field"), condition.get("op"), condition.get("value")
    if field == "amount":
        if op == "between":
            return f"amount is between {_number(value[0])} and {_number(value[1])}"
        phrase = {"over": "over", "under": "under", "is": "exactly", "approx": "about"}[str(op)]
        suffix = " (within 7.5%)" if op == "approx" else ""
        return f"amount is {phrase} {_number(value)}{suffix}"
    if field in {"payee", "account"}:
        values = value if isinstance(value, list) else [value]
        names = " or ".join(_label(str(field), v) for v in values)
        return f"{field} {'is' if op == 'is' else 'is not'} {names}"
    if field == "kind":
        values = value if isinstance(value, list) else [value]
        return f"kind {'is' if op == 'is' else 'is not'} {' or '.join(map(str, values))}"
    text = {
        "contains": "contains any of",
        "not_contains": "doesn't contain any of",
        "starts_with": "starts with",
        "regex": "matches",
    }[str(op)]
    shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return f"{field} {text} {shown}"


def _describe_split(lines: list[dict]) -> str:
    parts = []
    for line in lines:
        amount = line.get("amount") or {}
        kind = amount.get("type")
        share = (
            "the rest"
            if kind == "remainder"
            else f"{_number(amount.get('value'))}%"
            if kind == "percent"
            else _number(amount.get("value"))
        )
        tags = ", ".join(line.get("tags") or [])
        tagged = f" (tag {tags})" if tags else ""
        parts.append(f"{share} to {_label('category', line.get('category'))}{tagged}")
    return "split " + ", ".join(parts)


def describe_transaction_transform(automation: dict) -> str:
    """Describe a transaction transform (a Rule) as one readable sentence."""
    when = (automation.get("trigger") or {}).get("when") or {}
    execution = automation.get("execution") or {}
    action = execution.get("set") or {}
    conditions: list[str] = []
    actions: list[str] = []

    for key, label in (
        ("description_contains", "description contains"),
        ("description_regex", "description matches"),
        ("memo_contains", "memo contains"),
        ("memo_regex", "memo matches"),
        ("account_id", "account is"),
        ("kind", "kind is"),
        ("direction", "direction is"),
        ("currency", "currency is"),
    ):
        value = when.get(key)
        if value:
            conditions.append(f"{label} {value!r}")
    for key, label in (
        ("keywords", "description contains any of"),
        ("memo_keywords", "memo contains any of"),
    ):
        values = [str(value) for value in (when.get(key) or []) if str(value).strip()]
        if values:
            conditions.append(f"{label} {', '.join(values)}")
    if when.get("min_amount") is not None:
        conditions.append(f"amount is at least {when['min_amount']}")
    if when.get("max_amount") is not None:
        conditions.append(f"amount is at most {when['max_amount']}")
    joiner = " or " if when.get("match") == "any" else " and "
    conditions.extend(_describe_condition(c) for c in when.get("conditions") or [])

    if action.get("category"):
        actions.append(f"set category to {_label('category', action['category'])}")
    if action.get("tags"):
        actions.append(f"add tags {', '.join(str(tag) for tag in action['tags'])}")
    if action.get("note_append"):
        actions.append(f"append {action['note_append']!r} to the description")
    if action.get("memo_append"):
        actions.append(f"append {action['memo_append']!r} to the memo")
    if action.get("payee"):
        actions.append(f"set payee to {_label('payee', action['payee'])}")
    if execution.get("splits"):
        actions.append(_describe_split(execution["splits"]))
    if action.get("route_to_account"):
        actions.append(f"route statement rows to account {action['route_to_account']}")

    condition_text = joiner.join(conditions) or "the transaction matches"
    action_text = ", then ".join(actions) or "leave it unchanged"
    return f"When {condition_text}, {action_text}."
