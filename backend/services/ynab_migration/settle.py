"""What an app's budget leaves for the review to settle or say, beyond ``mapping``: its currency
when it doesn't state one, and what a person should know before importing it. The result lands on
the plan (``tracking``, ``carryover``, ``notices``, ``known_problems``, ``review``); a plan of an
app that settles nothing (YNAB) carries none of them."""

from __future__ import annotations

import re

from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.ynab_migration import report
from backend.services.ynab_migration.mapping import amt

_CURRENCY = re.compile(r"^[A-Z]{3}$")


def check_code(asked: str | None) -> None:
    if asked and not _CURRENCY.match(asked.strip().upper()):
        raise ValidationError("A currency is a three-letter code, such as USD.")


def method_line(plan: dict) -> str:
    name = current().name
    if plan.get("tracking"):
        return (
            f"Budget method set to zero-based. {name} tracks this budget, so change it here if "
            "you prefer."
        )
    return f"Budget method set to zero-based, as in {name}."


def _no_valid_currency() -> ValidationError:
    name = current().name
    return ValidationError(
        f"That {name} {current().plan_word} has no valid currency set; open it in {name} once first."
    )


def currency(summary: dict, data: dict, asked: str | None, kind: str) -> tuple[str, bool]:
    """(the plan's currency, whether the review has to ask for it). A budget that states one keeps
    it. One that doesn't takes the person's word; until they give it the review starts from the
    book's base currency, and an import does not start."""
    app = current()
    own = (data.get("currency") or summary.get("currency") or "").strip().upper()
    if not app.asks_type_and_currency or own:
        if not _CURRENCY.match(own):
            raise _no_valid_currency()
    said = (asked or "").strip().upper()
    if own and said and said != own:
        raise ValidationError(f"This {app.name} {app.plan_word} is in {own}, not {said}.")
    if own:
        return own, False
    if said:
        return said, True
    if kind == "import":
        raise ValidationError(f"Say which currency this {app.name} {app.plan_word} is in first.")
    return ledger_service.base_currency().upper(), True


def _carryover(plan: dict, months: list[dict]) -> list[str]:
    """The categories whose overspending the app holds in the category, ending a month in the red
    with the flag on. Any other carryover flag makes no difference to a figure."""
    known = {c["id"] for c in plan["categories"] if c.get("kind") != "income"}
    return sorted(
        {
            c["id"]
            for m in months
            for c in m["categories"]
            if c["id"] in known and c.get("carryover") and c.get("balance", 0) < 0
        }
    )


def _gap_text(gap: dict, accounts: dict[str, str]) -> str:
    return (
        f"A split on {gap['date']} in {accounts.get(gap['account_id'], 'an account')} says "
        f"{amt(gap['parent']):,.2f}, but its lines add up to {amt(gap['lines']):,.2f}."
    )


def _gap_notice(name: str, gaps: list[dict], accounts: dict[str, str]) -> str:
    shown = " ".join(_gap_text(g, accounts) for g in gaps[:5])
    more = (
        f" And {len(gaps) - 5:,} more; the import's report lists the first "
        f"{report.PROBLEMS_LISTED} problems."
        if len(gaps) > 5
        else ""
    )
    return f"{shown}{more} It imports as the lines' figure, which is what {name} counts."


def _notices(plan: dict, data: dict, tracking: bool, accounts: dict[str, str]) -> list[str]:
    app = current()
    name, out = app.name, list(data.get("notices") or [])
    if tracking:
        out.append(
            f"This {name} budget is in tracking mode, so its Assigned figures aren't brought "
            "across and its cash starts in Ready to Assign."
        )
    else:
        out.append(
            f"Ready to Assign here won't match {name}'s To Budget: {name} has no card payment "
            "categories, so the two are worked out differently."
        )
    if plan["carryover"]:
        labels = {c["id"]: c["label"] for c in plan["categories"]}
        names = ", ".join(labels[c] for c in plan["carryover"])
        out.append(
            f"Carryover is on for {names} in {name}, which keeps their overspending in the "
            "category. Here it clears at the end of the month and comes out of the next month's "
            "Ready to Assign, so their available figures read differently."
        )
    for held in data.get("held") or []:
        out.append(
            f"{name} is holding {amt(held['amount']):,.2f} for later in {held['month']}. Here "
            "that money shows in Ready to Assign."
        )
    if gaps := data.get("split_gaps"):
        out.append(_gap_notice(name, gaps, accounts))
    return out


def note(plan: dict, data: dict, asked: bool) -> dict:
    """Puts what the review and the import say on ``plan``. Returns it."""
    if not current().asks_type_and_currency:
        return plan
    tracking = data.get("budget_mode") == "tracking"
    accounts = {a["id"]: a.get("name") for a in data["accounts"]}
    plan["tracking"] = tracking
    plan["carryover"] = [] if tracking else _carryover(plan, data["months"])
    plan["notices"] = _notices(plan, data, tracking, accounts)
    plan["known_problems"] = [
        {"ref": g["id"], "reason": _gap_text(g, accounts), "kind": "split_gap"}
        for g in data.get("split_gaps") or []
    ]
    plan["review"] = {"currency_asked": asked, "budget_mode": data.get("budget_mode")}
    return plan
