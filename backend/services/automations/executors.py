"""Validated, deterministic Automation executor dispatch."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from backend.core.errors import ValidationError
from backend.core.user_time import user_today

from .rule_actions import validate_transform

DETERMINISTIC_KINDS = frozenset(
    {
        "notify",
        "agent",
        "transaction",
        "card_payment_guard",
        "vest_reminder",
        "coupon_reminder",
        "option_expiry_sweep",
    }
)
EXECUTION_KINDS = DETERMINISTIC_KINDS | {"lucius", "transaction_transform"}
_SIMPLE_KINDS = frozenset({"lucius", "vest_reminder", "coupon_reminder", "option_expiry_sweep"})
_TXN_KINDS = frozenset({"expense", "income", "transfer", "adjustment", "investment"})


def validate_execution(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValidationError("execution must be an object")
    kind = value.get("kind")
    if kind not in EXECUTION_KINDS:
        raise ValidationError(f"unknown execution kind {kind!r}")
    allowed = {
        "lucius": {"kind"},
        "notify": {"kind", "message", "channels"},
        "agent": {"kind", "agent_id"},
        "transaction": {"kind", "transaction"},
        "transaction_transform": {
            "kind",
            "set",
            "priority",
            "position",
            "stop",
            "override_existing",
            "splits",
        },
        "card_payment_guard": {"kind", "card_account_id", "card_name", "channels"},
        "vest_reminder": {"kind", "channels"},
        "coupon_reminder": {"kind", "channels"},
        "option_expiry_sweep": {"kind", "channels"},
    }[kind]
    unknown = set(value) - allowed
    if unknown:
        raise ValidationError(f"unknown execution keys: {', '.join(sorted(unknown))}")
    clean = dict(value)
    if kind == "notify" and not str(value.get("message") or "").strip():
        raise ValidationError("notify execution requires message")
    if kind == "agent" and not str(value.get("agent_id") or "").strip():
        raise ValidationError("agent execution requires agent_id")
    if kind == "card_payment_guard" and not str(value.get("card_account_id") or "").strip():
        raise ValidationError("card payment guard requires card_account_id")
    if kind == "transaction":
        clean["transaction"] = _validate_transaction_template(value.get("transaction"))
    if kind == "transaction_transform":
        validate_transform(value)
        clean.setdefault("stop", False)
        clean.setdefault("override_existing", False)
    if kind not in _SIMPLE_KINDS and value.get("channels") is not None:
        _validate_channels(value.get("channels"))
    elif (
        kind in {"vest_reminder", "coupon_reminder", "option_expiry_sweep"}
        and value.get("channels") is not None
    ):
        _validate_channels(value.get("channels"))
    return clean


def _validate_channels(value: object) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError("execution channels must be a list of strings")


def _validate_transaction_template(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValidationError("transaction execution requires a transaction template")
    template = dict(value)
    kind = str(template.get("kind") or "expense").strip()
    if kind not in _TXN_KINDS:
        raise ValidationError(f"transaction template has unknown kind {kind!r}")
    amount = template.get("amount")
    try:
        positive = amount is not None and float(amount) > 0
    except (TypeError, ValueError):
        positive = False
    if not positive:
        raise ValidationError("transaction template requires a positive amount")
    from_account = template.get("from_account_id")
    to_account = template.get("to_account_id")
    if kind == "expense" and not from_account:
        raise ValidationError("expense transaction template requires from_account_id")
    if kind == "income" and not to_account:
        raise ValidationError("income transaction template requires to_account_id")
    if kind in {"transfer", "adjustment"} and not (from_account and to_account):
        raise ValidationError(f"{kind} transaction template requires both account legs")
    if kind == "investment" and bool(from_account) == bool(to_account):
        raise ValidationError("investment transaction template requires exactly one account leg")
    if kind in {"adjustment", "investment"} and template.get("category"):
        raise ValidationError(f"{kind} transaction template must not carry a category")
    template["kind"] = kind
    return template


async def execute_deterministic(automation: dict, payload: dict | None = None) -> dict:
    raw_kind = (automation.get("execution") or {}).get("kind")
    if raw_kind not in DETERMINISTIC_KINDS:
        raise ValidationError(f"unsupported deterministic execution kind {raw_kind!r}")
    execution = validate_execution(automation.get("execution"))
    kind = execution["kind"]
    return await _DISPATCH[kind](automation, execution, dict(payload or {}))


async def _notify(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.services.notify import notify

    return await notify(
        execution["message"],
        channels=execution.get("channels"),
        title=(automation.get("name") or "").strip() or "Automation",
    )


async def _transaction(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.core.audit_context import clear_audit_scope, set_audit_scope
    from backend.services import ledger

    template = execution["transaction"]
    run_iso = (
        payload.get("scheduled_for")
        or automation.get("next_run_at")
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )
    external_ref = f"{automation.get('id')}:{run_iso}"
    if ledger.transactions.has_external_ref("recurring", external_ref):
        return {"skipped": "duplicate", "external_ref": external_ref}
    data = {
        "kind": template["kind"],
        "from_account_id": template.get("from_account_id"),
        "to_account_id": template.get("to_account_id"),
        "description": template.get("description")
        or template.get("note")
        or automation.get("name")
        or "Recurring transaction",
        "memo": template.get("memo") or "",
        "category": template.get("category"),
        "tags": list(template.get("tags") or []),
        # WRITER = automation (RFC 2026-08-02). "recurring" was never in KNOWN_SOURCES, so every
        # scheduled post logged "unrecognized source" while ALSO being listed in
        # REVIEW_ON_CREATE_SOURCES — two sets in one file disagreeing about whether it existed.
        # The external_ref namespace stays "recurring": that is WHICH automation, and it is the
        # idempotency key (<automation_id>:<run_iso>).
        "source": "automation",
        "external_refs": [{"source": "recurring", "ref": external_ref}],
        "date": str(run_iso)[:10] or None,
    }
    amount, currency = template.get("amount"), template.get("currency")
    if data["kind"] == "income":
        data["to_amount"], data["to_currency"] = amount, currency
    else:
        data["from_amount"], data["from_currency"] = amount, currency
        if data["kind"] in {"transfer", "adjustment"}:
            data["to_amount"], data["to_currency"] = amount, currency
    set_audit_scope(
        "system",
        f"recurring:{automation.get('id')}",
        note=automation.get("name") or None,
        automation=automation.get("name") or None,
        automation_id=automation.get("id") or None,
    )
    try:
        stored = ledger.create_transaction(data)
    finally:
        clear_audit_scope()
    return {"transaction_id": stored.get("id"), "external_ref": external_ref}


async def _card_payment_guard(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.services.card_statements import statement_summary
    from backend.services.notify import notify

    card_id = execution["card_account_id"]
    summary = statement_summary(card_id)
    if summary is None:
        return {"skipped": "no_statement_config"}
    if summary.paid and not summary.partial:
        return {"skipped": "paid"}
    name = execution.get("card_name") or card_id
    days = summary.days_until_due
    due = (
        "payment due"
        if days is None
        else (
            f"{abs(days)} day{'s' if abs(days) != 1 else ''} overdue"
            if days < 0
            else "due today"
            if days == 0
            else f"due in {days} day{'s' if days != 1 else ''}"
        )
    )
    if summary.partial:
        remaining = round(summary.last_statement_balance - (summary.payment_amount or 0.0), 2)
        title = "Partial payment reminder"
        message = f"{name} — {summary.currency} {remaining:,.2f} still outstanding ({due}). Pay the remainder to avoid interest."
    else:
        title = "Payment reminder"
        message = f"{name} ({summary.currency} {summary.last_statement_balance:,.2f}) is {due}. Pay now to avoid interest."
    delivered = await notify(message, channels=execution.get("channels"), title=title)
    return {"notified": True, "message": message, "delivered": delivered}


async def _vest_reminder(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.services import equity
    from backend.services.notify import notify

    today = user_today().isoformat()
    due = [
        row for row in equity.upcoming_vests(window_days=None) if row.get("vest_date", "") <= today
    ]
    if not due:
        return {"skipped": "no_vests_due"}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in due:
        grouped[row["vest_date"]].append(row)
    sent = []
    for vest_date in sorted(grouped):
        label = f"{vest_date} (overdue)" if vest_date < today else "today"
        lines = [
            f"{row.get('symbol') or 'equity'}: {row.get('units', 0):g} units"
            for row in grouped[vest_date]
        ]
        message = f"Vesting {label}: {', '.join(lines)} — open the Equity tab or ask Lucius to mark it vested."
        sent.append(
            {
                "vest_date": vest_date,
                "message": message,
                "delivered": await notify(
                    message, channels=execution.get("channels"), title="Vest reminder"
                ),
            }
        )
    return {"notified": len(sent), "vests": sent}


async def _coupon_reminder(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.db.instrument_store import InstrumentStore
    from backend.services import fixed_income
    from backend.services.notify import notify

    window_days = 7
    today, horizon = user_today(), user_today() + timedelta(days=window_days)
    instruments = {row["id"]: row for row in InstrumentStore().all()}
    lines = []
    for holding in fixed_income.summary(as_of=today).get("holdings", []):
        try:
            coupon = fixed_income.next_coupon(
                instruments.get(holding.get("instrument_id")), as_of=today
            )
            coupon_date = date.fromisoformat((coupon or {}).get("date") or "")
        except (TypeError, ValueError, OverflowError):
            continue
        if today <= coupon_date <= horizon:
            name = (
                holding.get("name")
                or holding.get("symbol")
                or holding.get("instrument_id")
                or "bond"
            )
            amount = coupon.get("amount_per_unit")
            lines.append(
                f"{name}: coupon {coupon_date.isoformat()}{f' ({amount:.4g} per unit)' if amount else ''}"
            )
    if not lines:
        return {"skipped": "no_coupons_due"}
    message = (
        f"{len(lines)} payment{'s' if len(lines) != 1 else ''} due within {window_days} days:\n"
        + "\n".join(lines)
    )
    delivered = await notify(message, channels=execution.get("channels"), title="Coupon reminder")
    return {"notified": len(lines), "message": message, "delivered": delivered}


async def _option_expiry_sweep(automation: dict, execution: dict, payload: dict) -> dict:
    from backend.services import options

    result = options.run_expiry_sweep()
    flagged = result.get("itm_flagged") or []
    if result.get("expired", 0) and flagged:
        from backend.services.notify import notify

        await notify(
            f"{result['expired']} contract(s) expired ({len(flagged)} ITM — please record assignment/exercise manually).",
            channels=execution.get("channels"),
            title="Option expiry",
        )
    return result if result.get("expired", 0) else {"skipped": "no_contracts_expired"}


_DISPATCH = {
    "notify": _notify,
    "transaction": _transaction,
    "card_payment_guard": _card_payment_guard,
    "vest_reminder": _vest_reminder,
    "coupon_reminder": _coupon_reminder,
    "option_expiry_sweep": _option_expiry_sweep,
}
