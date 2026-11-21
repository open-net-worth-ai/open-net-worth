"""What a Rule does to one transaction: validate its actions, compute its split, apply it.

A Rule is an Automation row whose execution is ``transaction_transform`` (ADR
``2026-11-21-rules-are-a-visible-kind-of-automation``). This module is the pure half: no store
write, no flag read. ``rule_engine`` decides which Rules run and in what order; the ledger and
``rule_apply`` decide where the result is written.

Split lines are ``{amount: {type: fixed|percent|remainder, value?}, category, tags?, memo?}``.
Fixed lines take their figure; percent lines take a share of what the fixed lines leave; the one
remainder line takes whatever is left. Rounding lands on the last line computed from the
remainder (the remainder line, else the last percent line when the shares total 100). A split
that cannot be made to add up is not applied — the Rule is skipped for that row, never raised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from backend.core.errors import ValidationError
from backend.core.fx import allocated_leg_amount

MAX_SPLIT_LINES = 10
SPLIT_TYPES = ("fixed", "percent", "remainder")
_SET_KEYS = frozenset(
    {"category", "tags", "note_append", "memo_append", "payee", "route_to_account"}
)
_SPLIT_KINDS = frozenset({"expense", "income"})
_NO_CATEGORY_KINDS = frozenset({"adjustment"})
_PAYEE_KINDS = frozenset({"expense", "income"})  # the only kinds the ledger keeps a payee on
_CENT = 0.01

Finalize = Callable[[dict], dict]
Exists = Callable[[str, str], bool]


@dataclass
class Outcome:
    """The row after one Rule, plus what it did. ``skipped`` is set when the Rule could not
    (fully) act; ``row`` is then the input untouched if nothing was applied."""

    row: dict
    owned: set[str] = field(default_factory=set)
    category_applied: bool = False
    skipped: str | None = None


def validate_transform(value: dict) -> None:
    """Shape of a ``transaction_transform`` execution; raises ``ValidationError``."""
    action = value.get("set")
    if not isinstance(action, dict):
        raise ValidationError("transaction transform requires set")
    unknown = set(action) - _SET_KEYS
    if unknown:
        raise ValidationError(f"unknown transform actions: {', '.join(sorted(unknown))}")
    tags = action.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)
    ):
        raise ValidationError("transform tags must be a list of strings")
    for key in ("category", "note_append", "memo_append", "payee", "route_to_account"):
        if action.get(key) is not None and not isinstance(action[key], str):
            raise ValidationError(f"transform {key} must be a string")
    for key in ("priority", "position"):
        number = value.get(key, 0)
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValidationError(f"transaction transform {key} must be an integer")
    if not all(isinstance(value.get(key, False), bool) for key in ("stop", "override_existing")):
        raise ValidationError("transaction transform flags must be booleans")
    if value.get("splits") is not None:
        validate_splits(value["splits"])


def validate_splits(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValidationError("splits must be a non-empty list of lines")
    if len(raw) > MAX_SPLIT_LINES:
        raise ValidationError(f"a Rule has at most {MAX_SPLIT_LINES} split lines")
    for line in raw:
        _validate_line(line)
    if sum(1 for line in raw if line["amount"]["type"] == "remainder") > 1:
        raise ValidationError("a split has at most one remainder line")
    return raw


def _validate_line(line: object) -> None:
    if not isinstance(line, dict) or set(line) - {"amount", "category", "tags", "memo"}:
        raise ValidationError("a split line is {amount, category, tags?, memo?}")
    amount = line.get("amount")
    if not isinstance(amount, dict) or amount.get("type") not in SPLIT_TYPES:
        raise ValidationError(f"a split amount is one of {', '.join(SPLIT_TYPES)}")
    if set(amount) - {"type", "value"}:
        raise ValidationError("a split amount is {type, value?}")
    if amount["type"] != "remainder":
        number = amount.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValidationError("a fixed or percent split amount needs a number")
        if not math.isfinite(number) or number <= 0:
            raise ValidationError("a split amount must be greater than 0")
        if amount["type"] == "percent" and number > 100:
            raise ValidationError("a split percent is at most 100")
    category = line.get("category")
    if not isinstance(category, str) or not category.strip():
        raise ValidationError("each split line needs a category")
    tags = line.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)
    ):
        raise ValidationError("split line tags must be a list of strings")
    if line.get("memo") is not None and not isinstance(line["memo"], str):
        raise ValidationError("a split line memo must be a string")


def compute_split(leg: float, lines: list[dict]) -> list[dict] | None:
    """The split as ``[{category, amount, memo}]`` summing to ``leg``, or None when it cannot
    be made to add up."""
    leg = round(abs(leg), 2)
    amounts: list[float | None] = []
    fixed = sum(line["amount"]["value"] for line in lines if line["amount"]["type"] == "fixed")
    left = leg - fixed
    for line in lines:
        kind = line["amount"]["type"]
        if kind == "fixed":
            amounts.append(round(line["amount"]["value"], 2))
        elif kind == "percent":
            amounts.append(round(left * line["amount"]["value"] / 100, 2))
        else:
            amounts.append(None)
    placed = sum(a for a in amounts if a is not None)
    gap = round(leg - placed, 2)
    if None in amounts:
        amounts[amounts.index(None)] = gap
    elif abs(gap) > _CENT:
        return None
    elif gap:
        percents = [i for i, line in enumerate(lines) if line["amount"]["type"] == "percent"]
        if not percents:
            return None
        amounts[percents[-1]] = round(amounts[percents[-1]] + gap, 2)
    if any(a is None or a <= 0 for a in amounts):
        return None
    return [
        {"category": line["category"], "amount": amount, "memo": line.get("memo") or ""}
        for line, amount in zip(lines, amounts)
    ]


def _append_once(value: str, suffix: object) -> str:
    suffix = str(suffix or "")
    if not suffix or suffix in value:
        return value
    return f"{value} {suffix}".strip() if value else suffix


def _text(row: dict, key: str) -> str:
    fallback = row.get("note") if key == "description" else ""
    return str(row.get(key) or fallback or "")


def apply_actions(
    row: dict,
    rule: dict,
    *,
    finalize: Finalize,
    owned: frozenset[str] = frozenset(),
    exists: Exists | None = None,
) -> Outcome:
    """Apply one Rule's actions to ``row`` and return the outcome; ``row`` is never mutated.

    ``owned`` names the fields an earlier Rule of this same pass set (``category``, ``splits``):
    a later Rule overwrites those without "Replace a category already set" — later wins — while
    a category or split that was already on the row still needs it. ``finalize`` validates and
    stamps a row's splits (the ledger's own authority). ``exists(kind, id)`` says whether a
    category or payee a Rule names is still in the book: one deleted since is skipped, never
    written, so the ledger never refuses a transaction because of a Rule."""
    execution = rule.get("execution") or {}
    action = execution.get("set") or {}
    result = dict(row)
    outcome = Outcome(result, set(owned))
    reasons: list[str] = []
    user_pick = bool(row.get("category_explicit"))
    replace = bool(execution.get("override_existing"))
    kind = row.get("kind")

    def may_replace(field_name: str) -> bool:
        if field_name in owned:
            return True
        return field_name == "category" and replace and not user_pick

    if execution.get("splits"):
        reason = _apply_split(result, execution["splits"], finalize, may_replace, user_pick)
        if reason == "split":
            return Outcome(dict(row), set(owned), skipped="split doesn't add up")
        if reason == "invalid":
            return Outcome(dict(row), set(owned), skipped="split isn't valid")
        if reason:
            reasons.append(reason)
        else:
            outcome.owned |= {"category", "splits"}
    category = action.get("category")
    if category and exists is not None and not exists("category", category):
        reasons.append("category no longer exists")
        category = None
    if category and kind not in _NO_CATEGORY_KINDS and not result.get("splits"):
        if user_pick:
            reasons.append("category picked by you")
        elif result.get("category") and not may_replace("category"):
            reasons.append("category already set")
        elif result.get("category") != category:
            result["category"] = category
            outcome.owned.add("category")
            outcome.category_applied = True
    elif category and result.get("splits") and "splits" not in outcome.owned:
        reasons.append("already split")
    tags = sorted({*(result.get("tags") or []), *(action.get("tags") or [])})
    if tags or "tags" in result or action.get("tags"):
        result["tags"] = tags
    description = _append_once(_text(result, "description"), action.get("note_append"))
    if description != _text(result, "description"):
        result["description"] = description
    memo = _append_once(_text(result, "memo"), action.get("memo_append"))
    if memo != _text(result, "memo"):
        result["memo"] = memo
    payee = action.get("payee")
    if payee and exists is not None and not exists("payee", payee):
        reasons.append("payee no longer exists")
    elif payee and kind in _PAYEE_KINDS:
        result["payee_id"] = payee
    outcome.skipped = reasons[0] if reasons else None
    return outcome


def _apply_split(
    result: dict,
    lines: list[dict],
    finalize: Finalize,
    may_replace: Callable[[str], bool],
    user_pick: bool,
) -> str | None:
    """Split ``result`` in place. Returns None on success, ``"split"`` / ``"invalid"`` when the
    lines cannot be applied at all, or a reason when the guards leave the row alone."""
    if result.get("kind") not in _SPLIT_KINDS:
        return "splits only apply to expenses and income"
    if result.get("splits") and not may_replace("splits"):
        return "already split"
    if user_pick:
        return "category picked by you"
    if result.get("category") and not result.get("splits") and not may_replace("category"):
        return "category already set"
    leg = allocated_leg_amount(result)
    computed = compute_split(leg, lines) if leg is not None else None
    if computed is None:
        return "split"
    candidate = {**result, "splits": computed, "category": None}
    try:
        stamped = finalize(candidate)
    except ValidationError:
        return "invalid"
    result.clear()
    result.update(stamped)
    extra = {tag for line in lines for tag in line.get("tags") or []}
    if extra:
        result["tags"] = sorted({*(result.get("tags") or []), *extra})
    return None
