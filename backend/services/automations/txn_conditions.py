"""Conditions on a transaction trigger: each picks a field, an operation and a value.

``when.conditions`` is a list of ``{field, op, value}``; every condition must hold (AND), or just one
when ``when.match`` is ``"any"`` (a Rule's "any of these"). The older
keys (``account``, ``direction``, ``over``, ``contains``, ``keywords``, ``*_regex``…) keep working
beside it — existing Automations and migrated rules still carry them.

- **Account** is/is not: the transaction touches that account, on either side.
- **Account / Kind / Payee** "is" and "is not" take one value or a list: a list is any-of for
  "is", none-of for "is not". A transaction has one payee and one kind, so two separate "Payee
  is" conditions with different values never fire — one condition with both is the way.
- **Kind** is/is not: expense, income, transfer, investment, adjustment — or an investment buy
  / sale, told apart by its one cash leg (the ledger allows exactly one): a buy pays out of an
  account (``from_account_id``), a sale pays into one (``to_account_id``).
- **Amount** over / under / is / between (inclusive, ``[low, high]``) / approx (within ±7.5%,
  inclusive), all on an absolute value: on the account "Account is" names, in that account's currency (its own
  leg) — only when exactly one account is named across them; otherwise in the base currency
  (ADR 2026-10-11).
- Conditions that can never all hold are REFUSED, not stored to sit silently: two "Payee is" /
  "Kind is" with no value in common, an "is" every value of which an "is not" rules out, and
  Amount over X with under Y where X >= Y. The editor shows the same messages
  (``txnConditionConflicts.ts``).
- **Payee** is/is not: by ``payee_id`` — a transaction stores the id, never the name. "Is not"
  needs a payee: a transfer or a trade has none, so it is never "not Grab".
- **Description / Memo**: contains / doesn't contain (comma-separated, any of them), starts
  with, matches regex — case-insensitive.
"""

from __future__ import annotations

import re
from typing import Callable

FIELDS = ("account", "kind", "amount", "payee", "description", "memo")
KINDS = (
    "expense",
    "income",
    "transfer",
    "investment",
    "investment_buy",
    "investment_sale",
    "adjustment",
)
_INVESTMENT_LEG = {"investment_buy": "from_account_id", "investment_sale": "to_account_id"}
_IS_OPS = frozenset({"is", "is_not"})
_AMOUNT_OPS = frozenset({"over", "under", "is", "between", "approx"})
APPROX_TOLERANCE = 0.075
_TEXT_OPS = frozenset({"contains", "not_contains", "starts_with", "regex"})
_OPS = {
    "account": _IS_OPS,
    "kind": _IS_OPS,
    "amount": _AMOUNT_OPS,
    "payee": _IS_OPS,
    "description": _TEXT_OPS,
    "memo": _TEXT_OPS,
}
OPS = (
    "is",
    "is_not",
    "over",
    "under",
    "between",
    "approx",
    "contains",
    "not_contains",
    "starts_with",
    "regex",
)
MATCH_MODES = ("all", "any")
_LIST_OPS = frozenset({"contains", "not_contains"})
MAX_CONDITIONS = 10
MAX_PATTERN_LENGTH = 200
MAX_VALUES = 50

AmountOn = Callable[[str | None], "float | None"]


def validate_conditions(raw: object, match: str = "all") -> list[dict]:
    """``match="any"`` skips the never-all-true check: conditions that cannot hold together can
    still each hold alone."""
    if not isinstance(raw, list):
        raise ValueError("transaction event conditions must be a list")
    if len(raw) > MAX_CONDITIONS:
        raise ValueError(f"transaction event allows at most {MAX_CONDITIONS} conditions")
    conditions = [_validate_condition(item) for item in raw]
    conflict = _conflict(conditions) if match == "all" else None
    if conflict:
        raise ValueError(conflict)
    return conditions


def _validate_condition(item: object) -> dict:
    if not isinstance(item, dict) or set(item) - {"field", "op", "value"}:
        raise ValueError("a condition is {field, op, value}")
    field, op, value = item.get("field"), item.get("op"), item.get("value")
    if field not in _OPS:
        raise ValueError(f"condition field must be one of {', '.join(FIELDS)}")
    if op not in _OPS[field]:
        allowed = ", ".join(sorted(_OPS[field]))
        raise ValueError(f"{field} condition op must be one of {allowed}")
    if field == "amount":
        return {"field": field, "op": op, "value": _amount_operand(op, value)}
    if op in _IS_OPS:
        return {"field": field, "op": op, "value": _is_value(field, value)}
    if op in _LIST_OPS:
        phrases = _phrases(value)
        if not phrases:
            raise ValueError(f"{field} {op} needs at least one phrase")
        return {"field": field, "op": op, "value": phrases}
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} {op} needs a value")
    value = value.strip()
    if op == "regex":
        if len(value) > MAX_PATTERN_LENGTH:
            raise ValueError(f"a pattern is at most {MAX_PATTERN_LENGTH} characters")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid {field} pattern: {exc}") from exc
    return {"field": field, "op": op, "value": value}


def _is_value(field: str, value: object) -> str | list[str]:
    """One id, or a non-empty list of them (any-of / none-of). A one-item list is stored as
    the plain value, so a single pick reads the same whichever way it was written."""
    items = value if isinstance(value, list) else [value]
    strings = [item.strip() for item in items if isinstance(item, str)]
    if len(strings) != len(items):
        raise ValueError(f"{field} condition needs a value or a list of values")
    values = list(dict.fromkeys(item for item in strings if item))
    if not values:
        raise ValueError(f"{field} condition needs a value")
    if len(values) > MAX_VALUES:
        raise ValueError(f"a {field} condition takes at most {MAX_VALUES} values")
    if field == "kind":
        unknown = [v for v in values if v not in KINDS]
        if unknown:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return values[0] if len(values) == 1 else values


# "investment" is a buy or a sale — the ledger gives an investment exactly one cash leg.
_KIND_ATOMS = {"investment": ("investment_buy", "investment_sale")}


def _conflict(conditions: list[dict]) -> str | None:
    """Why these conditions can never all hold, or None. Mirrors txnConditionConflicts.ts —
    the same rules in the same words, so Lucius reads what the editor shows."""
    for field in ("payee", "kind"):
        message = _one_value_conflict(conditions, field)
        if message:
            return message
    return _account_conflict(conditions) or _amount_conflict(conditions)


def _atoms(condition: dict) -> set[str]:
    values = _values(condition["value"])
    if condition["field"] == "kind":
        return {atom for value in values for atom in _KIND_ATOMS.get(value, (value,))}
    return set(values)


def _one_value_conflict(conditions: list[dict], field: str) -> str | None:
    is_rows = [c for c in conditions if c["field"] == field and c["op"] == "is"]
    if not is_rows:
        return None
    allowed = _atoms(is_rows[0])
    for row in is_rows[1:]:
        allowed &= _atoms(row)
        if not allowed:
            return (
                f"A transaction has one {field}, so these can’t both be true — pick both in one "
                "condition instead."
            )
    not_rows = [c for c in conditions if c["field"] == field and c["op"] == "is_not"]
    excluded = set().union(*(_atoms(c) for c in not_rows))
    if not_rows and allowed <= excluded:
        return f"“Is” and “is not” rule out every {field} here, so this can never match."
    return None


def _account_conflict(conditions: list[dict]) -> str | None:
    accounts = [c for c in conditions if c["field"] == "account"]
    excluded = {v for c in accounts if c["op"] == "is_not" for v in _values(c["value"])}
    if any(c["op"] == "is" and set(_values(c["value"])) <= excluded for c in accounts):
        return "“Is” and “is not” rule out every account here, so this can never match."
    return None


def _amount_conflict(conditions: list[dict]) -> str | None:
    over = [c["value"] for c in conditions if c["field"] == "amount" and c["op"] == "over"]
    under = [c["value"] for c in conditions if c["field"] == "amount" and c["op"] == "under"]
    if over and under and max(over) >= min(under):
        return (
            f"An amount can’t be over {max(over):,.12g} and under {min(under):,.12g} at once, "
            "so this can never match."
        )
    return None


def _amount_operand(op: str, value: object) -> float | list[float]:
    if op != "between":
        return _amount_value(value)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("amount between needs [low, high]")
    low, high = _amount_value(value[0]), _amount_value(value[1])
    if low > high:
        raise ValueError("amount between needs low <= high")
    return [low, high]


def _amount_value(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("amount condition needs a number") from None
    if number != number or number < 0 or number in (float("inf"), float("-inf")):
        raise ValueError("amount condition needs a non-negative number")
    return number


def _phrases(value: object) -> list[str]:
    """``"grabfood, ubereats"`` or ``["grabfood", "ubereats"]`` → the non-empty phrases."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def conditions_match(
    conditions: object, payload: dict, amount_on: AmountOn | None = None, match: str = "all"
) -> bool:
    """``amount_on(account)`` is the transaction's amount on that account (its own leg, in its
    currency), or in the base currency when ``account`` is None."""
    if not conditions:
        return True
    if not isinstance(conditions, list):
        return False
    rows = [c for c in conditions if isinstance(c, dict)]
    account = _amount_account(rows)
    hits = (_condition_matches(c, payload, account, amount_on) for c in rows)
    return any(hits) if match == "any" else all(hits)


def _amount_account(rows: list[dict]) -> str | None:
    """The account an amount is measured on: the one "Account is" names, when it names exactly
    one. Two (a transfer between them, or any of several) share no currency but the base."""
    named = {
        value
        for c in rows
        if c.get("field") == "account" and c.get("op") == "is"
        for value in _values(c.get("value"))
    }
    return next(iter(named)) if len(named) == 1 else None


def _values(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(item) for item in items if item not in (None, "")]


def _condition_matches(
    condition: dict, payload: dict, account: str | None, amount_on: AmountOn | None
) -> bool:
    field, op, value = condition.get("field"), condition.get("op"), condition.get("value")
    if field == "account":
        legs = {str(payload.get("from_account_id") or ""), str(payload.get("to_account_id") or "")}
        touches = any(v in legs for v in _values(value))
        return touches if op == "is" else not touches
    if field == "kind":
        same = any(_kind_is(payload, v) for v in _values(value))
        return same if op == "is" else not same
    if field == "amount":
        return _amount_matches(op, value, payload, account, amount_on)
    if field == "payee":
        payee = str(payload.get("payee_id") or "")
        named = payee in _values(value)
        return named if op == "is" else bool(payee) and not named
    return _text_matches(field, op, value, payload)


def _kind_is(payload: dict, kind: str) -> bool:
    actual = str(payload.get("kind") or "").casefold()
    leg = _INVESTMENT_LEG.get(kind)
    if leg:
        return actual == "investment" and bool(payload.get(leg))
    return actual == kind.casefold()


def _amount_matches(
    op: object, value: object, payload: dict, account: str | None, amount_on: AmountOn | None
) -> bool:
    if amount_on is not None:
        amount = amount_on(account)
    else:
        base = payload.get("base_amount")
        amount = abs(float(base)) if isinstance(base, (int, float)) else None
    if amount is None:
        return False
    try:
        if op == "between":
            low, high = (float(v) for v in value)  # type: ignore[union-attr]
            return low <= amount <= high
        threshold = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if op == "is":
        return round(amount, 2) == round(threshold, 2)
    if op == "approx":
        return abs(amount - threshold) <= threshold * APPROX_TOLERANCE + 1e-9
    return amount > threshold if op == "over" else amount < threshold


def _text_matches(field: object, op: object, value: object, payload: dict) -> bool:
    if field == "description":
        text = str(payload.get("description") or payload.get("note") or "")
    else:
        text = str(payload.get("memo") or "")
    folded = text.casefold()
    if op in _LIST_OPS:
        phrases = value if isinstance(value, list) else []
        hit = any(str(p).casefold() in folded for p in phrases)
        return hit if op == "contains" else not hit
    if op == "starts_with":
        return folded.startswith(str(value or "").casefold())
    if op == "regex":
        try:
            return re.search(str(value or ""), text, re.IGNORECASE) is not None
        except re.error:
            return False
    return False
