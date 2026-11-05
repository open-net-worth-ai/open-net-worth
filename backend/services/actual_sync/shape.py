"""Actual's tables -> the flat YNAB export shape the sync engine already reads (PURE: no I/O, no
``actual`` import). ``plan_from_rows`` builds the whole plan; ``delta_against`` reduces it to what
changed since the stored mirror, so ``ynab_sync.delta.apply_read`` works on it unchanged.

Actual keeps cents (value x 100) as integers; the engine's unit is the milliunit, so every figure
is multiplied by 10. Rows are read the way Actual's own views read them: a transaction's payee and
category go through ``payee_mapping`` / ``category_mapping`` (merged payees and categories), a
split's lines are rows with a ``parent_id``, and a dead parent takes its lines with it. A split
parent's amount is the sum of its live lines, because that is what Actual's balances and budget
sheets count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.services.actual_sync import available
from backend.services.actual_sync.shape_delta import delta_against
from backend.services.ynab_migration import mapping

__all__ = ["Rows", "delta_against", "plan_from_rows"]

SB_PAYEE_NAME = "Starting Balance"
SB_PAYEE_ID = "starting-balance"
SB_CATEGORY = "starting balances"
_NOT_WHOLE = "Actual Budget returned an amount that is not a whole number of cents."
_BAD_DATE = "Actual Budget returned a date that is not a real calendar date."
_BAD_MONTH = "Actual Budget returned a budget month that is not a real month."


@dataclass
class Rows:
    """Plain dicts straight from the copy's tables, keyed by Actual's own column names."""

    accounts: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    payees: list[dict] = field(default_factory=list)
    payee_mapping: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    category_groups: list[dict] = field(default_factory=list)
    category_mapping: list[dict] = field(default_factory=list)
    budgets: list[dict] = field(default_factory=list)  # the mode's table: zero_ or reflect_budgets
    notes: list[dict] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)  # zero_budget_months: id "YYYY-MM", buffered
    mode: str = "envelope"
    today: str | None = None  # ISO; the user's today


def _flag(value) -> bool:
    return bool(value)


def _milli(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_NOT_WHOLE)
    return value * 10


def _iso(value) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_BAD_DATE)
    try:
        return date(value // 10000, value // 100 % 100, value % 100).isoformat()
    except ValueError:
        raise ValidationError(_BAD_DATE) from None


def _month_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value % 100 <= 12:
        raise ValidationError(_BAD_MONTH)
    if value // 100 < 1:
        raise ValidationError(_BAD_MONTH)
    return value


def _text(value, limit: int) -> str:
    return mapping.clean(value, limit)


def _cleared(row: dict) -> str:
    if _flag(row.get("reconciled")):
        return "reconciled"
    return "cleared" if _flag(row.get("cleared")) else "uncleared"


class _Book:
    """The lookups every part of the plan needs, built once from the rows."""

    def __init__(self, rows: Rows):
        self.rows = rows
        self.account = {a["id"]: a for a in rows.accounts if a.get("id")}
        self.live_accounts = {i for i, a in self.account.items() if not _flag(a.get("tombstone"))}
        self.category_to = {m["id"]: m.get("transferId") for m in rows.category_mapping}
        self.payee_to = {m["id"]: m.get("targetId") for m in rows.payee_mapping}
        self.payee = {p["id"]: p for p in rows.payees if p.get("id")}
        self.live_categories = {
            c["id"]: c for c in rows.categories if c.get("id") and not _flag(c.get("tombstone"))
        }
        self.top, self.lines = self._live_transactions()

    def category_of(self, raw) -> str | None:
        return self.category_to.get(raw) if raw else None

    def payee_of(self, raw) -> str | None:
        return self.payee_to.get(raw) if raw else None

    def transfer_account(self, payee_id) -> str | None:
        acct = (self.payee.get(payee_id) or {}).get("transfer_acct")
        return acct if acct in self.account else None

    def _live_transactions(self) -> tuple[list[dict], dict[str, list[dict]]]:
        """Top-level rows and the lines of each split, as Actual's own alive view shows them."""
        base = [
            t
            for t in self.rows.transactions
            if t.get("id")
            and not _flag(t.get("tombstone"))
            and t.get("date") is not None
            and t.get("acct") in self.live_accounts
        ]
        top = [t for t in base if not _flag(t.get("isChild"))]
        live = {t["id"] for t in top}
        splits = {t["id"] for t in top if _flag(t.get("isParent"))}
        lines: dict[str, list[dict]] = {}
        for t in base:
            if not _flag(t.get("isChild")) or t.get("parent_id") not in live:
                continue
            if t["parent_id"] in splits:
                lines.setdefault(t["parent_id"], []).append(t)
            else:
                top.append(t)  # a live child under a plain row: Actual counts it as a row
        return top, lines

    def effective_amount(self, t: dict) -> int:
        if _flag(t.get("isParent")):
            return sum(_milli(c.get("amount")) for c in self.lines.get(t["id"], []))
        return _milli(t.get("amount"))


def _payee_ref(book: _Book, t: dict, opening: bool) -> str | None:
    """The payee id a row carries. An opening balance is recognised by Actual's own flag and
    always reads as the Starting Balance payee, whatever that payee has been renamed to."""
    mapped = book.payee_of(t.get("description"))
    if not opening:
        return mapped
    name = _text((book.payee.get(mapped) or {}).get("name"), 200) if mapped else ""
    return mapped if name.lower() == mapping.SB_PAYEE else SB_PAYEE_ID


def _is_opening(t: dict) -> bool:
    return _flag(t.get("starting_balance_flag")) and not t.get("transferred_id")


def _link_fields(book: _Book, t: dict, payee_id) -> dict:
    return {
        "payee_id": payee_id,
        "category_id": book.category_of(t.get("category")),
        "transfer_account_id": book.transfer_account(payee_id),
        "transfer_transaction_id": t.get("transferred_id") or None,
    }


def _transaction(book: _Book, t: dict) -> dict:
    opening = _is_opening(t)
    parent = _flag(t.get("isParent"))
    fields = _link_fields(book, t, _payee_ref(book, t, opening))
    if parent:
        fields["category_id"] = None
    return {
        "id": t["id"],
        "account_id": t["acct"],
        "date": _iso(t["date"]),
        "amount": book.effective_amount(t),
        "memo": _text(t.get("notes"), 500) or None,
        "cleared": _cleared(t),
        "approved": True,
        "flag_color": None,
        "deleted": False,
        **fields,
    }


def _line(book: _Book, c: dict) -> dict:
    return {
        "id": c["id"],
        "transaction_id": c["parent_id"],
        "amount": _milli(c.get("amount")),
        "memo": _text(c.get("notes"), 500) or None,
        "deleted": False,
        **_link_fields(book, c, book.payee_of(c.get("description"))),
    }


def _accounts(book: _Book, transactions: list[dict]) -> list[dict]:
    balance: dict[str, int] = {}
    cleared: dict[str, int] = {}
    for t in transactions:
        balance[t["account_id"]] = balance.get(t["account_id"], 0) + t["amount"]
        if t["cleared"] != "uncleared":
            cleared[t["account_id"]] = cleared.get(t["account_id"], 0) + t["amount"]
    transfer_payee: dict[str, str] = {}
    for p in sorted(book.rows.payees, key=lambda p: bool(_flag(p.get("tombstone")))):
        if p.get("transfer_acct") and p.get("id"):
            transfer_payee.setdefault(p["transfer_acct"], p["id"])
    notes = {n["id"]: n.get("note") for n in book.rows.notes}
    return [
        {
            "id": a["id"],
            "name": _text(a.get("name"), 120),
            "type": "checking",
            "on_budget": not _flag(a.get("offbudget")),
            "closed": _flag(a.get("closed")),
            "deleted": _flag(a.get("tombstone")),
            "balance": balance.get(a["id"], 0),
            "cleared_balance": cleared.get(a["id"], 0),
            "transfer_payee_id": transfer_payee.get(a["id"]),
            "note": _text(notes.get(a["id"]), 500) or None,
        }
        for a in book.rows.accounts
        if a.get("id")
    ]


def _payees(book: _Book, used: set[str]) -> list[dict]:
    """Every payee Actual shows, plus any dead one a live row still points at (so its name is
    not lost), plus the Starting Balance payee when an opening row needed it."""
    out = []
    for p in book.rows.payees:
        pid, acct = p.get("id"), p.get("transfer_acct")
        dead = _flag(p.get("tombstone"))
        if not pid or (pid not in used and (dead or (acct and acct not in book.live_accounts))):
            continue
        name = (book.account.get(acct) or {}).get("name") if acct else None
        out.append(
            {
                "id": pid,
                "name": _text(name if name is not None else p.get("name"), 200),
                "transfer_account_id": book.transfer_account(pid),
                "deleted": dead,
            }
        )
    if SB_PAYEE_ID in used:
        out.append(
            {
                "id": SB_PAYEE_ID,
                "name": SB_PAYEE_NAME,
                "transfer_account_id": None,
                "deleted": False,
            }
        )
    return out


def _groups_and_categories(book: _Book) -> tuple[list[dict], list[dict]]:
    notes = {n["id"]: n.get("note") for n in book.rows.notes}
    groups = [
        {
            "id": g["id"],
            "name": _text(g.get("name"), 120),
            "hidden": _flag(g.get("hidden")),
            "deleted": _flag(g.get("tombstone")),
            "is_income": _flag(g.get("is_income")),
        }
        for g in book.rows.category_groups
        if g.get("id")
    ]
    categories = []
    for c in book.rows.categories:
        if not c.get("id"):
            continue
        name, income = _text(c.get("name"), 120), _flag(c.get("is_income"))
        categories.append(
            {
                "id": c["id"],
                "name": name,
                "category_group_id": c.get("cat_group"),
                "original_category_group_id": None,
                "hidden": _flag(c.get("hidden")),
                "internal": income and name.lower() == SB_CATEGORY,
                "deleted": _flag(c.get("tombstone")),
                "note": _text(notes.get(c["id"]), 500) or None,
                "is_income": income,
            }
        )
    return groups, categories


def _budgets(rows: Rows) -> available.Budgets:
    out: available.Budgets = {}
    for b in rows.budgets:
        if b.get("category"):
            out[(_month_int(b.get("month")), b["category"])] = (
                _milli(b.get("amount")),
                _flag(b.get("carryover")),
            )
    return out


def _yyyymm(value) -> int:
    day = _iso(value)
    return int(day[:4]) * 100 + int(day[5:7])


def _activity(book: _Book, leaves: list[dict]) -> available.Activity:
    """What Actual's sheets call sum-amount: the lines and plain rows of on-budget accounts."""
    out: available.Activity = {}
    for t in leaves:
        category = book.category_of(t.get("category"))
        if category and not _flag(book.account[t["acct"]].get("offbudget")):
            key = (_yyyymm(t["date"]), category)
            out[key] = out.get(key, 0) + _milli(t.get("amount"))
    return out


def _months(book: _Book, categories: list[dict], today: str) -> list[dict]:
    rows = book.rows
    live = [c for c in categories if not c["deleted"] and not c["internal"]]
    shown_ids = {c["id"] for c in live}
    budgets = {k: v for k, v in _budgets(rows).items() if k[1] in shown_ids}
    leaves = [t for t in book.top if not _flag(t.get("isParent"))]
    leaves += [c for lines in book.lines.values() for c in lines]
    activity = _activity(book, leaves)
    current = _yyyymm(int(today.replace("-", "")))
    with_rows = {m for m, _ in budgets}
    seen = {_yyyymm(t["date"]) for t in leaves} | with_rows | {current}
    series = available.month_range(min(seen), max(with_rows | {current}))
    shown = [m for m in series if m <= current or m in with_rows]
    balance = available.available(
        series, [c["id"] for c in live if not c["is_income"]], budgets, activity, rows.mode
    )
    out = []
    for m in shown:
        entries = []
        for c in live:
            assigned, carry = budgets.get((m, c["id"]), (0, False))
            entry = {
                "id": c["id"],
                "budgeted": assigned,
                "activity": activity.get((m, c["id"]), 0),
                "balance": 0 if c["is_income"] else balance[(m, c["id"])],
            }
            if carry:
                entry["carryover"] = True
            entries.append(entry)
        out.append({"month": f"{m // 100:04d}-{m % 100:02d}-01", "categories": entries})
    return out


def _split_gaps(book: _Book) -> list[dict]:
    """Splits whose stated amount is not the sum of their lines. Actual allows it and counts the
    lines, so the plan does too, and these go on the record for a person to see."""
    out = []
    for t in book.top:
        if _flag(t.get("isParent")):
            stated, lines = _milli(t.get("amount")), book.effective_amount(t)
            if stated != lines:
                out.append(
                    {
                        "id": t["id"],
                        "account_id": t["acct"],
                        "date": _iso(t["date"]),
                        "parent": stated,
                        "lines": lines,
                    }
                )
    return out


def _held(rows: Rows, today: str) -> list[dict]:
    """Money held for later in this month or a coming one. An earlier hold has already come back
    to Actual's To Budget."""
    out = []
    for h in rows.held:
        month = _text(h.get("id"), 7)
        amount = _milli(h.get("buffered"))
        if amount and month >= today[:7]:
            out.append({"month": month, "amount": amount})
    return out


def plan_from_rows(rows: Rows) -> dict:
    """The whole plan in YNAB's flat export shape, in milliunits, with what that shape has no
    place for beside it (``budget_mode``, ``split_gaps``, ``held``). Raises ``ValidationError`` on
    an amount, date or month that is not a plain whole value."""
    book = _Book(rows)
    today = rows.today or user_today().isoformat()
    transactions = [_transaction(book, t) for t in book.top]
    subtransactions = [
        _line(book, c) for parent in book.top for c in book.lines.get(parent["id"], [])
    ]
    used = {t["payee_id"] for t in transactions if t["payee_id"]}
    used |= {s["payee_id"] for s in subtransactions if s["payee_id"]}
    groups, categories = _groups_and_categories(book)
    return {
        "accounts": _accounts(book, transactions),
        "category_groups": groups,
        "categories": categories,
        "payees": _payees(book, used),
        "transactions": transactions,
        "subtransactions": subtransactions,
        "months": _months(book, categories, today),
        "budget_mode": rows.mode,
        "split_gaps": _split_gaps(book),
        "held": _held(rows, today) if rows.mode == "envelope" else [],
    }
