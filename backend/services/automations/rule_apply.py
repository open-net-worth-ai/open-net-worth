"""Rules on the transactions already in the book: preview, apply one Rule, run them all.

Writes go through ``ledger.update_transaction`` — the ledger's one edit choke — composed into ONE
``locked_unit_of_work``, so a batch is atomic and ONE checkpoint (taken by the caller: the router,
or the Lucius turn) undoes it. Nothing here writes a store directly.

``apply_rule`` runs only the given Rule's actions on the rows it is handed, with no condition
re-check (the owner ticked them in a preview). ``apply_rule_to_matches`` is the same write over
EVERY row the Rule matches today, found again here rather than sent by the client, so it reaches
past the preview's first page. ``run_rules`` re-runs every active Rule, in order,
on the rows. Both keep the create-time guards: a category already on a row is replaced only by a
Rule with "Replace a category already set"; a row that is already split is never re-split; a
Starting Balance row is never touched. A row a Rule cannot act on is reported in ``skipped``.
"""

from __future__ import annotations

from typing import Callable

from backend.core.audit_context import rules_applied
from backend.core.errors import ConflictError, NotFoundError, OpenNetWorthError, ValidationError
from backend.db.automation_store import AutomationStore
from backend.db.engine import locked_unit_of_work
from backend.db.tables import transactions as txns_tbl
from backend.db.tables import txn_audit as txn_audit_tbl

from .executors import validate_execution
from .rule_actions import apply_actions
from .rule_engine import apply_rules, changed_fields, rules_enabled
from .rule_references import resolve_execution_references
from .service import apply_automation_change, rule_trigger, validated_trigger
from .transaction_events import (
    AutomationMatch,
    _is_transaction_transform,
    match_transaction_automation,
    ordered_transforms,
    record_committed_matches,
)

MAX_TRANSACTIONS = 500
MAX_ALL_MATCHES = 5000
_AFTER_FIELDS = ("category", "tags", "description", "memo", "payee_id", "splits")


def require_rules_enabled() -> None:
    """The Rules routes and tools do not exist while ``transaction_rules`` is off."""
    if not rules_enabled():
        raise NotFoundError("not found")


def rule_from_body(body: object) -> dict:
    """An unsaved Rule (``{trigger, execution}``) validated exactly as a save would."""
    if not isinstance(body, dict):
        raise ValidationError("rule must be an object")
    execution = body.get("execution")
    if not isinstance(execution, dict) or execution.get("kind") != "transaction_transform":
        raise ValidationError("rule execution must be a transaction_transform")
    return {
        "id": str(body.get("id") or "unsaved"),
        "name": str(body.get("name") or "Rule"),
        "status": "active",
        "trigger": validated_trigger(rule_trigger(body.get("trigger"))),
        # A SAVED Rule (it has an id) keeps references it already holds, as a save does; the
        # engine then skips one deleted since instead of refusing the whole call.
        "execution": resolve_execution_references(
            validate_execution(execution), execution if body.get("id") else None
        ),
    }


def _exists() -> Callable[[str, str], bool]:
    """One existence check per call, over the category rows read ONCE (not per transaction)."""
    from backend.services import ledger
    from backend.services.categories import all_category_rows

    return ledger._rule_ref_exists(all_category_rows())


def _finalize() -> Callable[[dict], dict]:
    from backend.services import ledger

    return ledger._apply_splits


def _same(key: str, before: object, after: object) -> bool:
    if key == "tags":  # the pass sorts and dedupes tags — a reorder is not a change
        return set(before or []) == set(after or [])
    return (after or None) == (before or None)


def _diff(before: dict, after: dict) -> dict:
    return {
        key: after.get(key)
        for key in _AFTER_FIELDS
        if not _same(key, before.get(key), after.get(key))
    }


def _opening_ids() -> set[str]:
    from backend.services import ledger

    return set(ledger.opening_txn_ids())


def _protected(row: dict | None, opening: set[str]) -> str | None:
    """Why a row can't take a Rule. ``opening`` is read once per call by the caller: reading it
    per row re-loaded every account for each transaction (25s for a 3,000-row preview)."""
    if row is None or row.get("deleted"):
        return "not found"
    if row.get("id") in opening:
        return "a Starting Balance can't change"
    return None


def preview_rule_matches(body: object, limit: int = 50) -> dict:
    """The live transactions the (unsaved) Rule matches, newest first, each with its before →
    after. ``total`` counts every match; ``changeable`` those the Rule would actually change
    (what "Apply to all" writes); ``matches`` is the first ``limit``."""
    from backend.services import ledger, payee_registry

    rule = rule_from_body(body)
    finalize = _finalize()
    accounts = {a["id"]: a.get("name") or a["id"] for a in ledger.accounts.all()}
    payees = payee_registry.payee_names()
    exists = _exists()
    matched = _matching_rows(rule)
    matches = []
    changeable = 0
    for index, row in enumerate(matched):
        outcome = apply_actions(row, rule, finalize=finalize, exists=exists)
        after = _diff(row, outcome.row)
        changeable += bool(after) and not outcome.skipped
        if index < limit:
            matches.append(_match_row(row, after, outcome.skipped, accounts, payees))
    return {"total": len(matched), "changeable": changeable, "matches": matches}


def _matching_rows(rule: dict) -> list[dict]:
    """Every live, non-opening transaction the Rule matches, newest first."""
    from backend.services import ledger

    opening = _opening_ids()
    rows = [r for r in ledger.transactions.all() if _protected(r, opening) is None]
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return [r for r in rows if match_transaction_automation(rule, r)]


def _match_row(row: dict, after: dict, skipped: str | None, accounts: dict, payees: dict) -> dict:
    account_id = row.get("from_account_id") or row.get("to_account_id")
    leg = "from" if row.get("from_account_id") else "to"
    amount = row.get(f"{leg}_amount")
    return {
        "transaction_id": row["id"],
        "date": row.get("date"),
        "description": row.get("description") or "",
        "memo": row.get("memo") or "",
        "payee_id": row.get("payee_id"),
        "payee_name": payees.get(str(row.get("payee_id"))) if row.get("payee_id") else None,
        "account_id": account_id,
        "account_name": accounts.get(account_id),
        "amount": abs(float(amount)) if amount is not None else None,
        "currency": row.get(f"{leg}_currency"),
        "kind": row.get("kind"),
        "category": row.get("category"),
        "tags": list(row.get("tags") or []),
        "splits": row.get("splits"),
        "after": ({key: after.get(key, row.get(key)) for key in _AFTER_FIELDS} if after else None),
        "skipped_reason": skipped,
    }


def _check_ids(transaction_ids: object) -> list[str]:
    if not isinstance(transaction_ids, list) or not transaction_ids:
        raise ValidationError("transaction_ids must be a non-empty list")
    if len(transaction_ids) > MAX_TRANSACTIONS:
        raise ValidationError(f"at most {MAX_TRANSACTIONS} transactions at a time")
    return list(dict.fromkeys(str(i) for i in transaction_ids))


def rule_plan(body: object) -> Callable[[dict], tuple[dict, str | None, list[dict]]]:
    """What ONE Rule's actions would change on a row: ``(changes, skipped_reason, rules)``."""
    rule = rule_from_body(body)
    finalize = _finalize()
    exists = _exists()

    def plan(row: dict) -> tuple[dict, str | None, list[dict]]:
        outcome = apply_actions(row, rule, finalize=finalize, exists=exists)
        changes = _diff(row, outcome.row)
        used = [_attribution(rule.get("id"), rule.get("name"), changed_fields(row, outcome.row))]
        return changes, outcome.skipped, used

    return plan


def rules_plan() -> Callable[[dict], tuple[dict, str | None, list[dict]]]:
    """What every active Rule, in order, would change on a row."""
    rules = ordered_transforms(r for r in AutomationStore().active() if _is_active_rule(r))
    finalize = _finalize()
    exists = _exists()

    def plan(row: dict) -> tuple[dict, str | None, list[dict]]:
        after, matches, skipped = apply_rules(row, rules, finalize=finalize, exists=exists)
        reason = skipped[0]["reason"] if skipped else None
        used = [_attribution(m.automation_id, m.automation_name, m.fields) for m in matches]
        return _diff(row, after), reason, used

    return plan


def apply_rule(body: object, transaction_ids: object) -> dict:
    """Apply ONE Rule's actions to the given transactions."""
    return _write(_check_ids(transaction_ids), rule_plan(body))


def apply_rule_to_matches(
    body: object, exclude_ids: object = None, expected_total: int | None = None
) -> dict:
    """Apply ONE Rule's actions to every transaction it matches today, minus ``exclude_ids``
    (rows the owner unticked in the preview). ``expected_total`` is the preview's ``total``: if
    the matches moved since, nothing is written, so the owner never approves a count they did
    not see."""
    rule = rule_from_body(body)
    matched = [str(r["id"]) for r in _matching_rows(rule)]
    if expected_total is not None and len(matched) != expected_total:
        raise ConflictError(
            f"The rule now matches {len(matched)} transactions, not {expected_total} — "
            "check the preview again."
        )
    excluded = {str(i) for i in (exclude_ids or [])}
    ids = [i for i in matched if i not in excluded]
    if not ids:
        return {"applied": 0, "unchanged": 0, "skipped": []}
    if len(ids) > MAX_ALL_MATCHES:
        raise ValidationError(
            f"The rule matches {len(ids)} transactions; at most {MAX_ALL_MATCHES} at a time — "
            "narrow its conditions (an account or a date) and apply in parts."
        )
    return _write(ids, rule_plan(body))


def run_rules(transaction_ids: object) -> dict:
    """Re-run every active Rule, in order, on the given transactions."""
    return _write(_check_ids(transaction_ids), rules_plan())


def _attribution(rule_id: object, name: object, fields: object) -> dict:
    """One Rule as the audit trail names it on the edit it caused."""
    return {
        "automation_id": str(rule_id or ""),
        "automation_name": str(name or ""),
        "fields": list(fields or ()),
    }


def _is_active_rule(row: dict) -> bool:
    return row.get("status") == "active" and _is_transaction_transform(row)


def _write(ids: list[str], plan: Callable) -> dict:
    """Plan every row, probe each change against the ledger's own validation, then write them
    all in one unit of work. A row the ledger would refuse is skipped, never half-written."""
    from backend.services import ledger

    applied: list[str] = []
    unchanged = 0
    skipped: list[dict] = []
    todo: list[tuple[str, dict, list[dict]]] = []
    opening = _opening_ids()
    for txn_id in ids:
        row = ledger.transactions.find_by_id(txn_id)
        reason = _protected(row, opening)
        if reason:
            skipped.append({"transaction_id": txn_id, "reason": reason})
            continue
        changes, why, used = plan(row)
        if not changes:
            if why:
                skipped.append({"transaction_id": txn_id, "reason": why})
            else:
                unchanged += 1
            continue
        try:
            ledger.resolve_transaction_row(
                {**row, **changes},
                skip_payee_resolve=True,
                labels_as_given=True,
                force_transfer_rate=True,
                mint_payee=False,
            )
        except OpenNetWorthError as exc:
            skipped.append({"transaction_id": txn_id, "reason": str(exc)[:200]})
            continue
        todo.append((txn_id, changes, used))
    if todo:
        with locked_unit_of_work(txns_tbl, txn_audit_tbl) as conn:
            for txn_id, changes, used in todo:
                with rules_applied([rule for rule in used if rule["fields"]]):
                    ledger.update_transaction(txn_id, changes, labels_as_given=True, conn=conn)
                applied.append(txn_id)
        record_committed_matches(
            [
                AutomationMatch(
                    rule["automation_id"], "", "transaction_pre_commit", None, None, False
                )
                for _txn_id, _changes, used in todo
                for rule in used
                if rule["automation_id"] != "unsaved"
            ],
            transaction_id=None,
            context="rule_apply",
        )
    return {"applied": len(applied), "unchanged": unchanged, "skipped": skipped}


def reorder_rules(ids: object) -> list[dict]:
    """Set every Rule's ``position`` to its index in ``ids`` (which must name every Rule)."""
    store = AutomationStore()
    rules = [row for row in store.all() if _is_transaction_transform(row)]
    if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(r["id"] for r in rules):
        raise ValidationError("ids must name every Rule exactly once")
    by_id = {r["id"]: r for r in rules}
    for position, rule_id in enumerate(map(str, ids)):
        execution = {**by_id[rule_id]["execution"], "position": position}
        if execution != by_id[rule_id]["execution"]:
            apply_automation_change(
                {"op": "update", "id": rule_id, "changes": {"execution": execution}}
            )
    return list_rules()


def list_rules() -> list[dict]:
    return ordered_transforms(r for r in AutomationStore().all() if _is_transaction_transform(r))
