"""Turn the subjects a trigger names — an account, a payee, a ticker — into the ids it stores.

Lucius and the editor write the same trigger: the editor picks from lists, Lucius may name a
subject the way a person would ("Grab", "Everyday Checking"). Either resolves here at the save
choke, by id or exact label; an unknown or ambiguous name is REFUSED rather than stored to
never match. A free-form price ticker is the one subject kept as typed (upper-cased).
"""

from __future__ import annotations

from copy import deepcopy

from backend.core.errors import ValidationError

from .options import automation_options, condition_references

_REFERENCE_FIELDS = {
    "txn": ("account", "accounts", "account"),
    "balance": ("account", "accounts", "account"),
    "price": ("symbol", "instruments", "instrument"),
}


def resolve_trigger_references(
    trigger: dict, *, options: dict | None = None, previous: dict | None = None
) -> dict:
    """Resolve visible subjects while retaining valid free-form price symbols. A condition's
    accounts and payees resolve too — by id or exact name, the way the editor picks them; an
    unknown or ambiguous one is refused rather than stored to never match.

    An id the ``previous`` trigger already held is kept as it is: a payee merged or deleted
    since must not refuse an unrelated edit (a rename, an effort change) of the Automation.
    Runs BEFORE validation, so "Grab" and its id meet as one payee in the conflict check."""
    if not isinstance(trigger, dict):
        return trigger
    resolved = deepcopy(trigger)
    if resolved.get("kind") != "event":
        return resolved
    if resolved.get("event") == "txn" and isinstance(resolved.get("when"), dict):
        _resolve_condition_references(resolved["when"], options, referenced_ids(previous))
    reference = _REFERENCE_FIELDS.get(str(resolved.get("event") or ""))
    if reference is None:
        return resolved
    field, option_key, noun = reference
    when = resolved.get("when")
    if not isinstance(when, dict):
        return resolved
    raw_ref = when.get(field)
    if raw_ref is None or raw_ref == "":
        return resolved
    rows = (options if options is not None else automation_options()).get(option_key) or []
    when[field] = _resolve_reference(raw_ref, rows, noun, str(resolved.get("event") or ""))
    return resolved


_CONDITION_REFERENCES = {"account": "accounts", "payee": "payees"}
_ID_OPS = ("is", "is_not")  # payee "contains" matches the NAME's text — not a reference


def _names_ids(condition: object) -> bool:
    return (
        isinstance(condition, dict)
        and condition.get("field") in _CONDITION_REFERENCES
        and condition.get("op", "is") in _ID_OPS
    )


def referenced_ids(trigger: object) -> set[str]:
    """Every account and payee id a trigger's conditions (or its flat account) point at."""
    when = trigger.get("when") if isinstance(trigger, dict) else None
    if not isinstance(when, dict):
        return set()
    ids = {str(when[key]) for key in ("account", "account_id") if when.get(key)}
    for condition in when.get("conditions") or []:
        if _names_ids(condition):
            value = condition.get("value")
            ids.update(str(v) for v in (value if isinstance(value, list) else [value]) if v)
    return ids


def _resolve_condition_references(when: dict, options: dict | None, keep: set[str]) -> None:
    conditions = when.get("conditions")
    if not isinstance(conditions, list):
        return
    _resolve_category_conditions(conditions)
    named = [c for c in conditions if _names_ids(c)]
    if not named:
        return
    references: dict | None = options

    def resolve(value: object, field: str) -> object:
        nonlocal references
        if isinstance(value, str) and value.strip() in keep:
            return value.strip()
        if references is None:
            references = condition_references()
        rows = references.get(_CONDITION_REFERENCES[field]) or []
        return _resolve_reference(value, rows, field, "txn")

    for condition in named:
        field = condition["field"]
        value = condition.get("value")
        if isinstance(value, list):
            condition["value"] = [resolve(v, field) for v in value]
        elif value not in (None, ""):
            condition["value"] = resolve(value, field)


def _resolve_category_conditions(conditions: list) -> None:
    """A "Category is" value may be a category's id or its label (Lucius names them); it is
    stored as the id. ``uncategorized`` stands for "no category" and is kept as is."""
    from backend.core.errors import ValidationError
    from backend.services import categories as categories_service

    for condition in conditions:
        if not isinstance(condition, dict) or condition.get("field") != "category":
            continue
        value = condition.get("value")
        items = value if isinstance(value, list) else [value]
        resolved = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                continue
            ref = item.strip()
            if ref.lower() == "uncategorized":
                resolved.append("uncategorized")
                continue
            category_id = categories_service.resolve_category_ref(ref)
            if category_id is None:
                raise ValidationError(f"category {ref!r} does not exist")
            resolved.append(category_id)
        if resolved:
            condition["value"] = resolved[0] if len(resolved) == 1 else resolved


def _resolve_reference(raw_ref: object, rows: list[dict], noun: str, event: str) -> str:
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise ValidationError(f"automation {noun} reference must be a non-empty string")
    ref = raw_ref.strip()
    exact_ids = [row for row in rows if str(row.get("id") or "") == ref]
    if exact_ids:
        return str(exact_ids[0]["id"])
    matches = [
        row for row in rows if str(row.get("label") or "").strip().casefold() == ref.casefold()
    ]
    if len(matches) == 1:
        return str(matches[0]["id"])
    if len(matches) > 1:
        ids = ", ".join(str(row.get("id") or "") for row in matches)
        raise ValidationError(f"automation {noun} reference {ref!r} is ambiguous: {ids}")
    if event == "price":
        return ref.upper()
    labels = ", ".join(str(row.get("label") or row.get("id")) for row in rows[:8])
    suffix = f" Existing: {labels}." if labels else " No persisted choices are available."
    raise ValidationError(
        f"automation {noun} reference {ref!r} does not match a persisted {noun}.{suffix}"
    )
