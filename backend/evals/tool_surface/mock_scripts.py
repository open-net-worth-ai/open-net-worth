"""Scripted model behaviour for the zero-cost mock run.

Two personas, written once in canonical op terms and replayed through every arm:

* ``perfect`` — the behaviour a competent model should produce.
* ``sloppy``  — the specific mistakes each task is designed to catch.

The ``sloppy`` persona is the scorer's own test. Every one of its steps is a real bug
seen in this domain: booking a foreign charge at the re-converted amount rather than the
settled amount, treating the absolute ``assign`` as a delta, re-booking a statement row
that is already in the ledger, inventing an account rather than asking, and disposing
more units than are held. If the scorer reports ``sloppy`` as passing, the scorer is
broken.

Params can be resolved lazily (``resolver=``) because ids are only known at run time:
account ids come from the fixture reference, and position/transaction ids come from
earlier tool results in the same run.
"""

from __future__ import annotations

from typing import Any

from backend.evals.tool_surface.model_client import Step


def say(text: str) -> Step:
    return Step(op=None, params={"text": text})


# ── Lazy resolvers ───────────────────────────────────────────────────────────


def resolve(name: str, ref: dict, results: list[Any], params: dict) -> dict:
    """Fill in ids that are only knowable at run time."""
    out = dict(params)

    if name == "account":
        for key, account_name in list(out.items()):
            if key.endswith("_account_id") and isinstance(account_name, str):
                out[key] = ref["account_ids"].get(account_name, account_name)
        return out

    if name == "position":
        symbol = out.pop("_symbol", "VOO")
        out["position_id"] = ref["positions"][symbol]["position_id"]
        for key, account_name in list(out.items()):
            if key.endswith("_account_id") and isinstance(account_name, str):
                out[key] = ref["account_ids"].get(account_name, account_name)
        return out

    if name == "envelope":
        for key in ("envelope_id", "from_envelope_id", "to_envelope_id"):
            envelope_name = out.get(key)
            if isinstance(envelope_name, str) and envelope_name in ref["envelopes"]:
                out[key] = ref["envelopes"][envelope_name]["id"]
        return out

    if name == "netflix_txn":
        # Find the NETFLIX row id in whatever the previous read returned.
        for result in reversed(results):
            for row in (result or {}).get("transactions", []) or []:
                if "NETFLIX" in str(row.get("description") or "").upper():
                    return {"id": row.get("id"), "changes": out.get("changes", {})}
        return {"id": "txn_missing", "changes": out.get("changes", {})}

    return out


def render_text(template: str, ref: dict, results: list[Any]) -> str:
    """Let a scripted final message quote a value the run actually read.

    Read tasks are scored on the number in the final message, so the mock must state a
    number it genuinely obtained from a tool result rather than one hardcoded here —
    otherwise the read checks would pass without the read having happened.
    """
    if "{groceries_available}" in template:
        value = _find_envelope_available(results, "Groceries")
        template = template.replace("{groceries_available}", _fmt(value))
    if "{net_worth}" in template:
        value = _find(results, "net_worth")
        template = template.replace("{net_worth}", _fmt(value))
    if "{uob_balance}" in template:
        value = _find_account_balance(results, "UOB One Card")
        template = template.replace(
            "{uob_balance}", _fmt(abs(value) if value is not None else None)
        )
    if "{voo_unit_cost}" in template:
        value = _find_lot_field(results, "unit_cost")
        template = template.replace("{voo_unit_cost}", _fmt(value))
    if "{realized}" in template:
        value = _find(results, "realized_total_base")
        template = template.replace("{realized}", _fmt(value))
    return template


def _fmt(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _find(results: list[Any], key: str) -> Any:
    for result in reversed(results):
        if isinstance(result, dict) and key in result:
            return result[key]
    return None


def _find_envelope_available(results: list[Any], label: str) -> Any:
    for result in reversed(results):
        for row in (result or {}).get("envelopes", []) or []:
            if str(row.get("label", "")).lower() == label.lower():
                return row.get("available")
    return None


def _find_account_balance(results: list[Any], name: str) -> Any:
    for result in reversed(results):
        for row in (result or {}).get("accounts", []) or []:
            if str(row.get("name", "")).lower() == name.lower():
                return row.get("balance")
    return None


def _find_lot_field(results: list[Any], key: str) -> Any:
    for result in reversed(results):
        for row in (result or {}).get("lots", []) or []:
            if key in row:
                return row[key]
    return None


# ── Perfect persona ──────────────────────────────────────────────────────────

_PERFECT: dict[str, list[Step]] = {
    "simple_expense": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-16",
                "kind": "expense",
                "description": "Hawker centre lunch",
                "category": "Food & Dining",
                "from_account_id": "DBS Multiplier",
                "from_amount": 8.40,
                "from_currency": "SGD",
            },
        ),
        say("Logged 8.40 for lunch on your DBS account."),
    ],
    "fx_settled_charge": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-13",
                "kind": "expense",
                "description": "US online store - jacket",
                "category": "Shopping",
                "from_account_id": "Amex Platinum",
                "from_amount": 164.20,
                "from_currency": "SGD",
                "original_amount": 120.0,
                "original_currency": "USD",
            },
        ),
        say("Booked the settled SGD 164.20 on the Amex, with the USD 120 original recorded."),
    ],
    "transfer_own_accounts": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-15",
                "kind": "transfer",
                "description": "Transfer OCBC to DBS",
                "from_account_id": "OCBC 360 Savings",
                "from_amount": 5000.0,
                "from_currency": "SGD",
                "to_account_id": "DBS Multiplier",
                "to_amount": 5000.0,
                "to_currency": "SGD",
            },
        ),
        say("Recorded the 5,000 transfer."),
    ],
    "categorised_transfer": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-14",
                "kind": "transfer",
                "description": "UOB card payment",
                "category": "Other",
                "from_account_id": "DBS Multiplier",
                "from_amount": 400.0,
                "from_currency": "SGD",
                "to_account_id": "UOB One Card",
                "to_amount": 400.0,
                "to_currency": "SGD",
            },
        ),
        say("Recorded the 400 card payment, tagged Other."),
    ],
    "statement_import_with_dup": [
        Step(op="accounts.list"),
        Step(
            op="txn.list", params={"date_from": "2026-07-01", "date_to": "2026-07-16", "limit": 100}
        ),
        Step(
            op="txn.add_many",
            resolver="account",
            params={
                "transactions": [],
                "_rows": "import",
            },
        ),
        say("Added 3 rows; the Cold Storage 62.35 was already in the ledger so I skipped it."),
    ],
    "investment_buy_with_cash_leg": [
        Step(op="positions.list"),
        Step(op="accounts.list"),
        Step(
            op="lots.acquire",
            resolver="position",
            params={
                "_symbol": "VOO",
                "quantity": 5.0,
                "unit_cost": 511.20,
                "cost_currency": "USD",
                "acquired_at": "2026-07-14",
            },
        ),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-14",
                "kind": "investment",
                "description": "Buy 5 VOO @ 511.20",
                "from_account_id": "IBKR USD",
                "from_amount": 2556.00,
                "from_currency": "USD",
            },
        ),
        say("Recorded the 5-share VOO buy and the USD 2,556.00 cash leg."),
    ],
    "lot_disposal": [
        Step(op="positions.list"),
        Step(op="accounts.list"),
        Step(
            op="lots.dispose",
            resolver="position",
            params={
                "_symbol": "TSLA",
                "quantity": 10.0,
                "proceeds_per_unit": 251.80,
                "proceeds_currency": "USD",
                "disposed_at": "2026-07-16",
                "method": "fifo",
            },
        ),
        say("Sold 10 TSLA at 251.80; realized gain 638.00 USD."),
    ],
    "budget_assign": [
        Step(op="budget.month", params={"month": "2026-07"}),
        Step(
            op="budget.assign",
            resolver="envelope",
            params={"month": "2026-07", "envelope_id": "Groceries", "amount": 750.0},
        ),
        say("Groceries is now assigned 750.00 for July (was 600.00)."),
    ],
    "budget_move_money": [
        Step(op="budget.month", params={"month": "2026-07"}),
        Step(
            op="budget.move_money",
            resolver="envelope",
            params={
                "month": "2026-07",
                "from_envelope_id": "Travel Fund",
                "to_envelope_id": "Fun Money",
                "amount": 80.0,
            },
        ),
        say("Moved 80 from Travel Fund to Fun Money."),
    ],
    "read_groceries_available": [
        Step(op="budget.month", params={"month": "2026-07"}),
        say("You have {groceries_available} left for groceries this month."),
    ],
    "read_net_worth": [
        Step(op="networth.get"),
        say("Your net worth is {net_worth} SGD."),
    ],
    "read_card_balance": [
        Step(op="accounts.list"),
        say("You're carrying {uob_balance} on the UOB One Card."),
    ],
    "read_lots_lazy": [
        Step(op="positions.list"),
        Step(op="lots.list", resolver="position", params={"_symbol": "VOO"}),
        say("You bought VOO on 2025-03-11 at {voo_unit_cost} a share."),
    ],
    "read_realized_pnl_lazy": [
        Step(op="lots.realized_pnl", params={"date_from": "2026-01-01", "date_to": "2026-12-31"}),
        say("Realized gains so far this year are {realized} SGD."),
    ],
    "recategorise_existing": [
        Step(op="txn.list", params={"q": "NETFLIX", "limit": 20}),
        Step(
            op="txn.update",
            resolver="netflix_txn",
            params={"changes": {"category": "Entertainment"}},
        ),
        say("Moved the Netflix charge to Entertainment."),
    ],
    "fx_transfer": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-16",
                "kind": "transfer",
                "description": "Wise USD to DBS",
                "from_account_id": "Wise USD",
                "from_amount": 1000.0,
                "from_currency": "USD",
                "to_account_id": "DBS Multiplier",
                "to_amount": 1350.0,
                "to_currency": "SGD",
            },
        ),
        say("Moved USD 1,000 (SGD 1,350.00) from Wise to DBS."),
    ],
    "near_miss_no_account": [
        Step(op="accounts.list"),
        say("Which account should I put the 45 dinner expense on? You have seven."),
    ],
    "near_miss_unknown_account": [
        Step(op="accounts.list"),
        say(
            "I don't see a Citibank card in your accounts. Did you mean the UOB One Card or the Amex Platinum?"
        ),
    ],
    "near_miss_oversell": [
        Step(op="positions.list"),
        say("You only hold 25 TSLA, not 500 — can you confirm the quantity you meant?"),
    ],
}


# ── Sloppy persona: the bugs each task is designed to catch ──────────────────

_SLOPPY: dict[str, list[Step]] = {
    # Re-converts the USD price at our rate instead of using the settled amount.
    "fx_settled_charge": [
        Step(op="accounts.list"),
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-13",
                "kind": "expense",
                "description": "Jacket",
                "category": "Shopping",
                "from_account_id": "Amex Platinum",
                "from_amount": 162.00,
                "from_currency": "SGD",
            },
        ),
        say("Booked 162.00."),
    ],
    # Re-books the Cold Storage row that is already in the ledger.
    # Books the already-present row anyway. Since the pre-write duplicate guard (ADR
    # 2026-08-01) the ledger REFUSES that row, so the planted bug is now committed the only
    # way it still can be: allow_duplicate=True, past a refusal that named the existing transaction.
    # That IS the realistic careless behaviour now, and the scorer must still catch it —
    # this task exists to prove the scorer discriminates, not to prove the guard works.
    "statement_import_with_dup": [
        Step(op="accounts.list"),
        Step(
            op="txn.add_many",
            resolver="account",
            params={"transactions": [], "_rows": "import_with_dup", "allow_duplicate": True},
        ),
        say("Added all 4 rows."),
    ],
    # Treats the ABSOLUTE assign as a delta.
    "budget_assign": [
        Step(
            op="budget.assign",
            resolver="envelope",
            params={"month": "2026-07", "envelope_id": "Groceries", "amount": 150.0},
        ),
        say("Added 150 to groceries."),
    ],
    # Guesses an account rather than asking.
    "near_miss_no_account": [
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-16",
                "kind": "expense",
                "description": "Dinner",
                "from_account_id": "DBS Multiplier",
                "from_amount": 45.0,
                "from_currency": "SGD",
            },
        ),
        say("Logged 45 for dinner on your DBS account."),
    ],
    # Books against a real card as if it were the (non-existent) Citibank one.
    "near_miss_unknown_account": [
        Step(
            op="txn.add",
            resolver="account",
            params={
                "date": "2026-07-16",
                "kind": "expense",
                "description": "Groceries",
                "category": "Food & Dining",
                "from_account_id": "UOB One Card",
                "from_amount": 200.0,
                "from_currency": "SGD",
            },
        ),
        say("Logged 200 for groceries."),
    ],
    # Attempts a disposal far larger than the position.
    "near_miss_oversell": [
        Step(
            op="lots.dispose",
            resolver="position",
            params={
                "_symbol": "TSLA",
                "quantity": 500.0,
                "proceeds_per_unit": 250.0,
                "proceeds_currency": "USD",
                "disposed_at": "2026-07-16",
            },
        ),
        say("Sold 500 TSLA."),
    ],
    # Reports a confidently wrong number.
    "read_net_worth": [
        Step(op="networth.get"),
        say("Your net worth is about 88,000.00 SGD."),
    ],
    "read_groceries_available": [
        Step(op="budget.month", params={"month": "2026-07"}),
        say("You have 600.00 left for groceries."),
    ],
}


# Statement rows for the import task, expanded at plan time.
_IMPORT_ROWS = [
    ("2026-07-11", 23.90, "GUARDIAN HEALTH & BEAUTY"),
    ("2026-07-12", 68.00, "SHELL SERVICE STATION"),
    ("2026-07-14", 45.60, "DIN TAI FUNG PLAZA SING"),
]
_DUP_ROW = ("2026-07-08", 62.35, "COLD STORAGE GREAT WORLD")


def _expand_import(step: Step, ref: dict, include_dup: bool) -> Step:
    card = ref["account_ids"]["UOB One Card"]
    rows = ([_DUP_ROW] if include_dup else []) + _IMPORT_ROWS
    # Carry the step's OTHER params through — this used to rebuild `params` from scratch, so
    # anything the script set beside `_rows` (e.g. the sloppy arm's `allow_duplicate`) was silently
    # dropped and the planted behaviour never reached the op.
    carried = {k: v for k, v in (step.params or {}).items() if k not in ("_rows", "transactions")}
    return Step(
        op=step.op,
        params={
            **carried,
            "transactions": [
                {
                    "date": date,
                    "kind": "expense",
                    "description": description,
                    "from_account_id": card,
                    "from_amount": amount,
                    "from_currency": "SGD",
                }
                for date, amount, description in rows
            ],
        },
    )


def plan_for(persona: str, task_id: str, ref: dict) -> list[Step]:
    table = _SLOPPY if persona == "sloppy" else _PERFECT
    plan = table.get(task_id)
    if plan is None:
        # Sloppy has no bespoke script for this task — fall back to correct behaviour so
        # the persona only differs where a real failure mode was deliberately encoded.
        plan = _PERFECT.get(task_id, [say("I'm not sure how to do that.")])

    expanded: list[Step] = []
    for step in plan:
        marker = step.params.get("_rows") if step.params else None
        if marker == "import":
            expanded.append(_expand_import(step, ref, include_dup=False))
        elif marker == "import_with_dup":
            expanded.append(_expand_import(step, ref, include_dup=True))
        else:
            expanded.append(step)
    return expanded
