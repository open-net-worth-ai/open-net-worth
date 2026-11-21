"""The Rules pass: every active Rule, in list order, each seeing the row the last one left.

The ONE seam for the ``transaction_rules`` flag. Off, the ledger keeps its old pass
(``transaction_events.apply_transaction_automations``: first match by ``priority`` stops, before
payee resolution, labels only). On, ``apply_rules`` runs after payee resolution so "payee is"
matches, a later Rule overwrites what an earlier one set, and a Rule can split the amount.

Rules run on create and when the owner asks (``rule_apply``); an edit never re-runs them.
"""

from __future__ import annotations

import logging
from typing import Sequence

from .rule_actions import Exists, Finalize, apply_actions
from .transaction_events import (
    AutomationMatch,
    _is_routing_transform,
    active_transaction_transforms,
    match_transaction_automation,
)

logger = logging.getLogger(__name__)


def rules_enabled() -> bool:
    from backend.services.feature_flags import enabled

    return enabled("transaction_rules")


def stops(rule: dict) -> bool:
    """A stored row with no ``stop`` is a legacy first-match transform; every new Rule stores
    ``stop: false`` and runs alongside the others."""
    return bool((rule.get("execution") or {}).get("stop", True))


# The fields a Rule can change, in the order the audit trail lists them.
_AUDITED_FIELDS = ("category", "splits", "tags", "payee_id", "description", "memo")


def changed_fields(before: dict, after: dict) -> tuple[str, ...]:
    """The stored fields that differ between ``before`` and ``after`` (tags as a set)."""

    def norm(key: str, value: object) -> object:
        if key == "tags":
            return sorted(value or [])
        return value or None

    return tuple(k for k in _AUDITED_FIELDS if norm(k, before.get(k)) != norm(k, after.get(k)))


def apply_rules(
    data: dict,
    rules: Sequence[dict] | None,
    *,
    finalize: Finalize,
    exists: Exists | None = None,
) -> tuple[dict, list[AutomationMatch], list[dict]]:
    """Run every active label/split Rule over ``data``; returns ``(row, matches, skipped)``.

    A routing Rule is inert here (statement-draft phase). A Rule that cannot act on this row —
    a split that does not add up, a category the row already has — is reported in ``skipped``
    as ``{automation_id, automation_name, reason}`` and the row is never refused for it."""
    ordered = active_transaction_transforms(rules)
    if not ordered:
        return data, [], []
    result = dict(data)
    # The retired ``note`` input alias becomes ``description`` whenever a label Rule exists.
    result["description"] = str(result.get("description") or result.get("note") or "")
    result["memo"] = str(result.get("memo") or "")
    result["tags"] = sorted(set(result.get("tags") or []))
    matches: list[AutomationMatch] = []
    skipped: list[dict] = []
    owned: frozenset[str] = frozenset()
    for rule in ordered:
        if _is_routing_transform(rule) or not match_transaction_automation(rule, result):
            continue
        category_from = result.get("category") or None
        before = result
        outcome = apply_actions(result, rule, finalize=finalize, owned=owned, exists=exists)
        if outcome.skipped:
            skipped.append(
                {
                    "automation_id": str(rule.get("id") or ""),
                    "automation_name": str(rule.get("name") or ""),
                    "reason": outcome.skipped,
                }
            )
        acted = outcome.row != result
        result, owned = outcome.row, frozenset(outcome.owned)
        if outcome.skipped and not acted:
            continue
        matches.append(
            AutomationMatch(
                automation_id=str(rule.get("id") or ""),
                automation_name=str(rule.get("name") or ""),
                phase="transaction_pre_commit",
                category_from=category_from,
                category_to=result.get("category") or None,
                category_applied=outcome.category_applied,
                fields=changed_fields(before, result),
            )
        )
        if stops(rule):
            break
    if skipped:
        logger.info("rules skipped for one transaction: %s", skipped)
    return result, matches, skipped
