"""Turn the names a Rule's actions mention into ids at the save choke.

Lucius may write "Groceries", the editor writes an id; either lands as the id. An unknown or
ambiguous name is REFUSED rather than stored to never apply. A value the ``previous`` execution
already held is kept as it is, so a category deleted since must not refuse an unrelated edit
(a pause, a reorder).
"""

from __future__ import annotations

from copy import deepcopy

from backend.core.errors import ValidationError


def resolve_execution_references(execution: dict, previous: object = None) -> dict:
    resolved = deepcopy(execution)
    kept = _held(previous)

    def category(ref: str) -> str:
        from backend.services import categories

        if ref in kept:
            return ref
        found = categories.resolve_category_ref(ref)
        if found is None:
            raise ValidationError(
                f"rule category {ref!r} does not match an existing category — use "
                "list_categories or create it first"
            )
        return found

    action = resolved.get("set") or {}
    if action.get("category"):
        action["category"] = category(action["category"].strip())
    if action.get("payee"):
        action["payee"] = _payee(action["payee"].strip(), kept)
    if action.get("route_to_account"):
        action["route_to_account"] = _account(action["route_to_account"].strip(), kept)
    for line in resolved.get("splits") or []:
        line["category"] = category(line["category"].strip())
    return resolved


def _held(previous: object) -> set[str]:
    if not isinstance(previous, dict):
        return set()
    action = previous.get("set") or {}
    held = {action.get(key) for key in ("category", "payee", "route_to_account")}
    held |= {line.get("category") for line in previous.get("splits") or []}
    return {str(v) for v in held if v}


def _payee(ref: str, kept: set[str]) -> str:
    from backend.services import payee_registry

    if ref in kept:
        return ref
    if payee_registry._store.find_by_id(ref) is not None:
        return ref
    found = payee_registry.resolve_payee_id_by_name(ref, create=False)
    if not found:
        raise ValidationError(f"rule payee {ref!r} does not match an existing payee")
    return found


def _account(ref: str, kept: set[str]) -> str:
    from backend.services import accounts

    if ref in kept:
        return ref
    found = accounts.resolve_account_ref(ref)
    if not found:
        raise ValidationError(f"rule account {ref!r} does not match an existing account")
    return found
