"""Synchronous transaction-event Automation matching and transformations.

The module owns the deterministic, pre-persist special case of Automations. It is
pure when explicit rows are supplied and never calls the retired Rules service or
repository. Usage statistics are recorded separately, after the caller's money write.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from backend.core.errors import MissingFxRateError
from backend.core.fx import txn_base_amount
from backend.db.automation_store import AutomationStore

from .projections import describe_lucius_transaction_event, describe_transaction_transform
from .txn_conditions import conditions_match

logger = logging.getLogger(__name__)

_NO_CATEGORY_KINDS = frozenset({"adjustment"})


@dataclass(frozen=True)
class AutomationMatch:
    automation_id: str
    automation_name: str
    phase: Literal["transaction_pre_commit", "statement_draft"]
    category_from: str | None
    category_to: str | None
    category_applied: bool
    # Which stored fields this Rule changed on the row (audit provenance); empty when unknown.
    fields: tuple[str, ...] = ()


def _is_transaction_transform(row: dict) -> bool:
    trigger = row.get("trigger") or {}
    execution = row.get("execution") or {}
    return (
        trigger.get("kind") == "event"
        and trigger.get("event") == "txn"
        and execution.get("kind") == "transaction_transform"
    )


def _is_transform(row: dict) -> bool:
    return row.get("status") == "active" and _is_transaction_transform(row)


def active_transaction_transforms(rows: Sequence[dict] | None = None) -> list[dict]:
    """Return active transaction transforms in deterministic execution order."""
    source = list(rows) if rows is not None else AutomationStore().active()
    return ordered_transforms(row for row in source if _is_transform(row))


def ordered_transforms(rows: Sequence[dict] | object) -> list[dict]:
    """Rules by ``position`` — the order they are listed in — and legacy rows by ``priority``."""
    return sorted(
        list(rows),
        key=lambda row: (
            int(
                (row.get("execution") or {}).get(
                    "position", (row.get("execution") or {}).get("priority", 100)
                )
            ),
            row.get("created_at") or "",
            row.get("id") or "",
        ),
    )


def _txn_desc(draft: dict) -> str:
    return str(draft.get("description") or draft.get("note") or "")


def _txn_memo(draft: dict) -> str:
    return str(draft.get("memo") or "")


def _direction(draft: dict) -> str | None:
    """Return the legacy leg-based direction used by transaction Rules.

    A two-legged transfer is a debit because its source leg is present. This is
    intentionally about the stored legs, not the transaction kind: imports and
    drafts can reach this matcher before their kind has been fully normalized.
    """
    if draft.get("from_account_id") or draft.get("from_amount") is not None:
        return "debit"
    if draft.get("to_account_id") or draft.get("to_amount") is not None:
        return "credit"
    return None


def _leg_amount(draft: dict, currency: str) -> float | None:
    wanted = currency.upper()
    for side in ("from", "to"):
        if str(draft.get(f"{side}_currency") or "").upper() != wanted:
            continue
        amount = draft.get(f"{side}_amount")
        if amount is not None:
            return abs(float(amount))
    return None


def _amount_for_match(draft: dict, when: dict) -> float | None:
    currency = when.get("currency")
    if currency:
        return _leg_amount(draft, str(currency))
    amount = draft.get("base_amount")
    if amount is not None:
        return abs(float(amount))
    for key in ("from_amount", "to_amount"):
        if draft.get(key) is not None:
            return abs(float(draft[key]))
    return None


def draft_amount_on(draft: dict):
    """``amount_on`` for ``conditions_match``: the transaction's amount on one account's own leg,
    or in the base currency when no single account is named."""

    def amount_on(account: str | None) -> float | None:
        if account:
            for side in ("from", "to"):
                amount = draft.get(f"{side}_amount")
                if draft.get(f"{side}_account_id") == account and amount is not None:
                    return abs(float(amount))
            return None
        try:
            return txn_base_amount(draft)
        except (TypeError, ValueError, MissingFxRateError):
            return None

    return amount_on


def match_transaction_automation(automation: dict, draft: dict) -> bool:
    """Return whether all configured transaction conditions match ``draft``."""
    if not _is_transform(automation):
        return False
    when = (automation.get("trigger") or {}).get("when") or {}
    description = _txn_desc(draft)
    lowered_description = description.lower()
    contains = when.get("description_contains")
    if contains and str(contains).lower() not in lowered_description:
        return False
    keywords = when.get("keywords")
    if keywords and not any(
        str(keyword).lower() in lowered_description for keyword in keywords if str(keyword).strip()
    ):
        return False
    regex = when.get("description_regex")
    if regex:
        try:
            if not re.search(str(regex), description, re.IGNORECASE):
                return False
        except re.error:
            return False

    memo = _txn_memo(draft)
    lowered_memo = memo.lower()
    memo_contains = when.get("memo_contains")
    if memo_contains and str(memo_contains).lower() not in lowered_memo:
        return False
    memo_keywords = when.get("memo_keywords")
    if memo_keywords and not any(
        str(keyword).lower() in lowered_memo for keyword in memo_keywords if str(keyword).strip()
    ):
        return False
    memo_regex = when.get("memo_regex")
    if memo_regex:
        try:
            if not re.search(str(memo_regex), memo, re.IGNORECASE):
                return False
        except re.error:
            return False

    account_id = when.get("account_id")
    if account_id and account_id not in (
        draft.get("from_account_id"),
        draft.get("to_account_id"),
    ):
        return False
    if when.get("kind") and draft.get("kind") != when["kind"]:
        return False
    if when.get("direction") and _direction(draft) != str(when["direction"]).lower():
        return False
    currency = when.get("currency")
    if currency and str(currency).upper() not in {
        str(draft.get("from_currency") or "").upper(),
        str(draft.get("to_currency") or "").upper(),
    }:
        return False
    minimum = when.get("min_amount")
    maximum = when.get("max_amount")
    if minimum is not None or maximum is not None:
        amount = _amount_for_match(draft, when)
        if amount is None:
            return False
        if minimum is not None and amount < float(minimum):
            return False
        if maximum is not None and amount > float(maximum):
            return False
    return conditions_match(
        when.get("conditions"),
        draft,
        amount_on=draft_amount_on(draft),
        match=when.get("match") or "all",
    )


def _is_routing_transform(automation: dict) -> bool:
    return bool(((automation.get("execution") or {}).get("set") or {}).get("route_to_account"))


def _append_once(value: str, suffix: object) -> str:
    suffix = str(suffix or "")
    if not suffix or suffix in value:
        return value
    return f"{value} {suffix}".strip() if value else suffix


def _apply_labels(result: dict, automation: dict) -> tuple[dict, bool]:
    execution = automation.get("execution") or {}
    action = execution.get("set") or {}
    category_applied = False
    user_pick = bool(result.get("category_explicit"))
    has_splits = bool(result.get("splits"))
    category = action.get("category")
    if (
        category
        and not has_splits
        and not user_pick
        and result.get("kind") not in _NO_CATEGORY_KINDS
        and (execution.get("override_existing") or not result.get("category"))
    ):
        result["category"] = category
        category_applied = True
    tags = sorted({*(result.get("tags") or []), *(action.get("tags") or [])})
    if tags or "tags" in result or action.get("tags"):
        result["tags"] = tags
    description = _append_once(_txn_desc(result), action.get("note_append"))
    if description != _txn_desc(result):
        result["description"] = description
    memo = _append_once(_txn_memo(result), action.get("memo_append"))
    if memo != _txn_memo(result):
        result["memo"] = memo
    return result, category_applied


def _match(
    automation: dict,
    result: dict,
    phase: str,
    category_from: str | None,
    *,
    category_applied: bool,
) -> AutomationMatch:
    return AutomationMatch(
        automation_id=str(automation.get("id") or ""),
        automation_name=str(automation.get("name") or ""),
        phase=phase,  # type: ignore[arg-type]
        category_from=category_from,
        category_to=result.get("category") or None,
        category_applied=category_applied,
    )


def apply_transaction_automations(
    data: dict, automations: Sequence[dict] | None = None
) -> tuple[dict, list[AutomationMatch]]:
    """Apply label-only transaction transforms; routing transforms are inert here."""
    rows = (
        active_transaction_transforms()
        if automations is None
        else active_transaction_transforms(automations)
    )
    if not rows:
        return data, []
    result = dict(data)
    # Preserve the former transform phase's normalization of the retired ``note`` input
    # alias whenever a label transform exists, even when this particular row does not match.
    if any(not _is_routing_transform(row) for row in rows):
        result["description"] = _txn_desc(result)
        result["memo"] = _txn_memo(result)
        result["tags"] = sorted(set(result.get("tags") or []))
    matches: list[AutomationMatch] = []
    for automation in rows:
        if _is_routing_transform(automation) or not match_transaction_automation(
            automation, result
        ):
            continue
        category_from = result.get("category") or None
        result, category_applied = _apply_labels(result, automation)
        matches.append(
            _match(
                automation,
                result,
                "transaction_pre_commit",
                category_from,
                category_applied=category_applied,
            )
        )
        if (automation.get("execution") or {}).get("stop", True):
            break
    return result, matches


def _route_draft(result: dict, target_account_id: str) -> dict | None:
    if result.get("kind") == "income":
        statement_account = result.get("to_account_id")
        amount = result.get("to_amount")
        currency = result.get("to_currency")
        if not statement_account or target_account_id == statement_account:
            return None
        return {
            **result,
            "kind": "transfer",
            "from_account_id": target_account_id,
            "from_amount": amount,
            "from_currency": currency,
            "to_account_id": statement_account,
            "to_amount": amount,
            "to_currency": currency,
        }
    statement_account = result.get("from_account_id")
    amount = result.get("from_amount")
    currency = result.get("from_currency")
    if not statement_account or target_account_id == statement_account:
        return None
    return {
        **result,
        "kind": "transfer",
        "from_account_id": statement_account,
        "from_amount": amount,
        "from_currency": currency,
        "to_account_id": target_account_id,
        "to_amount": amount,
        "to_currency": currency,
    }


def route_statement_draft_with_automations(
    data: dict, automations: Sequence[dict] | None = None
) -> tuple[dict, list[AutomationMatch]]:
    """Apply the first valid routing transform before transfer-leg validation."""
    rows = (
        active_transaction_transforms()
        if automations is None
        else active_transaction_transforms(automations)
    )
    result = dict(data)
    for automation in rows:
        if not _is_routing_transform(automation) or not match_transaction_automation(
            automation, result
        ):
            continue
        action = (automation.get("execution") or {}).get("set") or {}
        routed = _route_draft(result, str(action["route_to_account"]))
        if routed is None:
            return result, []
        category_from = routed.get("category") or None
        routed, category_applied = _apply_labels(routed, automation)
        return routed, [
            _match(
                automation,
                routed,
                "statement_draft",
                category_from,
                category_applied=category_applied,
            )
        ]
    return result, []


def record_committed_matches(
    matches: Sequence[AutomationMatch], *, transaction_id: str | None, context: str
) -> None:
    """Best-effort usage accounting, called only after the surrounding commit succeeds."""
    if not matches:
        return
    try:
        store = AutomationStore()
        counts: dict[str, int] = {}
        for match in matches:
            if match.automation_id:
                counts[match.automation_id] = counts.get(match.automation_id, 0) + 1
        now = datetime.now(timezone.utc).isoformat()
        for automation_id, count in counts.items():
            row = store.find_by_id(automation_id)
            if row is None:
                continue
            stats = dict(row.get("stats") or {})
            stats.update(
                {
                    "match_count": int(stats.get("match_count") or 0) + count,
                    "last_matched_at": now,
                    "last_context": context,
                }
            )
            if transaction_id:
                stats["last_transaction_id"] = transaction_id
            store.update_by_id(automation_id, {"stats": stats, "updated_at": now})
    except Exception:  # noqa: BLE001 - usage metadata never breaks a committed money write
        logger.exception("automation match usage update failed after commit")


def rewrite_rule_category_refs(old_id: str, new_id: str | None) -> list[str]:
    """Point every Rule's category (its ``set.category`` and each split line) from ``old_id`` to
    ``new_id``. With no replacement the Rule is PAUSED, never deleted (its conditions are the
    owner's work): ``set.category`` is cleared, a split line keeps the dead id until edited.
    Returns the ids of the Rules changed."""
    store = AutomationStore()
    changed: list[str] = []
    for automation in ordered_transforms(
        row for row in store.all() if _is_transaction_transform(row)
    ):
        execution = dict(automation.get("execution") or {})
        action = dict(execution.get("set") or {})
        splits = [dict(line) for line in execution.get("splits") or []]
        in_set = action.get("category") == old_id
        in_splits = any(line.get("category") == old_id for line in splits)
        trigger, in_conditions = _rewrite_condition_category(
            automation.get("trigger") or {}, old_id, new_id
        )
        if not (in_set or in_splits or in_conditions):
            continue
        if in_set:
            action["category"] = new_id
            execution["set"] = action
        if in_splits and new_id is not None:
            # With no replacement the dead id STAYS on its line (the Rule is paused below and the
            # engine skips a split naming a missing category): a line with no category fails
            # validation, which would refuse every later save of the Rule — a reorder included.
            for line in splits:
                if line.get("category") == old_id:
                    line["category"] = new_id
            execution["splits"] = splits
        patch = {
            "execution": execution,
            "trigger": trigger,
            "instructions": describe_transaction_transform(
                {**automation, "execution": execution, "trigger": trigger}
            ),
        }
        if new_id is None:
            patch["status"] = "paused"
        store.update_by_id(automation["id"], patch)
        changed.append(str(automation["id"]))
    return changed


def _rewrite_condition_category(
    trigger: dict, old_id: str, new_id: str | None
) -> tuple[dict, bool]:
    """A "Category is …" condition naming ``old_id`` names ``new_id`` instead. With no
    replacement the dead id stays (the Rule is paused): emptying the condition would make it
    fail validation, and dropping it would widen what the Rule matches."""
    when = dict(trigger.get("when") or {})
    conditions = [dict(c) for c in when.get("conditions") or [] if isinstance(c, dict)]
    touched = False
    for condition in conditions:
        if condition.get("field") != "category":
            continue
        values = condition.get("value")
        items = values if isinstance(values, list) else [values]
        if old_id not in items:
            continue
        touched = True
        if new_id is not None:
            merged = list(dict.fromkeys(new_id if v == old_id else v for v in items))
            condition["value"] = merged[0] if len(merged) == 1 else merged
    if not touched:
        return trigger, False
    return {**trigger, "when": {**when, "conditions": conditions}}, True


def rewrite_category_refs(old_id: str, new_id: str | None) -> int:
    """Rewrite category actions and regenerate their instructions in the same row write."""
    store = AutomationStore()
    changed = len(rewrite_rule_category_refs(old_id, new_id))
    for automation in store.all():
        provenance = dict(automation.get("provenance") or {})
        migration = dict(provenance.get("lucius_execution_migration") or {})
        spec = dict(migration.get("transaction_event") or {})
        action = dict(spec.get("set") or {})
        if (
            (automation.get("execution") or {}).get("kind") != "lucius"
            or migration.get("from_kind") != "transaction_transform"
            or action.get("category") != old_id
        ):
            continue
        if new_id is None:
            action.pop("category", None)
        else:
            action["category"] = new_id
        spec["set"] = action
        migration["transaction_event"] = spec
        provenance["lucius_execution_migration"] = migration
        remaining = bool(
            action.get("category")
            or action.get("tags")
            or action.get("note_append")
            or action.get("memo_append")
            or action.get("route_to_account")
        )
        instructions = (
            describe_lucius_transaction_event(automation.get("trigger") or {}, spec)
            if remaining
            else (
                "This Automation is paused because its category action was removed. "
                "Edit its instructions and trigger before resuming it."
            )
        )
        changes = {"instructions": instructions, "provenance": provenance}
        if new_id is None:
            changes["status"] = "paused"
        from .service import apply_automation_change

        apply_automation_change({"op": "update", "id": automation["id"], "changes": changes})
        changed += 1
    return changed
