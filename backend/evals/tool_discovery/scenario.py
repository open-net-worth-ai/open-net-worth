"""The replayed scenario: seed, the two user turns, and the score of the resulting ledger."""

from __future__ import annotations

from dataclasses import dataclass, field

DATE = "2026-10-14"
AMOUNT = 1250
SOURCE = "Acme Current"
DEST = "Birch Savings"

TURNS = (
    f"On 14 October I moved £1,250 from {SOURCE} to {DEST}. In the ledger it shows up as a "
    f"transfer going the wrong way, plus a separate £1,250 expense on {SOURCE}. They should be one "
    f"transfer from {SOURCE} to {DEST}. Inspect both entries and tell me what you would change — "
    "don't change anything yet.",
    "Yes, correct them.",
)

# Tools the fix may use. Allowed without a card in the scratch book so the turn can finish
# unattended and the ledger shows what the model actually did.
WRITE_TOOLS = (
    "find_tools",
    "update_transactions",
    "delete_transactions",
    "delete_transactions_where",
    "merge_transactions",
    "add_transactions",
)


@dataclass
class Score:
    fixed: bool
    attempted: bool
    loaded: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    reply: str = ""
    ledger: str = ""
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error:
            return "error"
        if self.fixed:
            return "fixed"
        return "tried" if self.attempted else "gave_up"


def seed() -> dict[str, str]:
    """Two GBP cash accounts, the reversed transfer and the duplicate expense. Synthetic."""
    from backend.db.tool_permission_store import ToolPermissionStore
    from backend.services import accounts, ledger
    from backend.services import profile as profile_service

    profile_service.write_profile({**profile_service.read_profile_raw(), "base_currency": "GBP"})
    ids = {}
    for name, opening in ((SOURCE, 6000), (DEST, 1500)):
        acct = accounts.create_account(
            {
                "name": name,
                "type": "cash",
                "currency": "GBP",
                "initial_balance": opening,
                "initial_balance_date": "2026-10-01",
            }
        )
        ids[name] = acct["id"]
    ledger.create_transaction(
        {
            "kind": "transfer",
            "date": DATE,
            "description": "Transfer",
            "from_account_id": ids[DEST],
            "from_amount": AMOUNT,
            "from_currency": "GBP",
            "to_account_id": ids[SOURCE],
            "to_amount": AMOUNT,
            "to_currency": "GBP",
        }
    )
    ledger.create_transaction(
        {
            "kind": "expense",
            "date": DATE,
            "description": "Transfer to Birch",
            "from_account_id": ids[SOURCE],
            "from_amount": AMOUNT,
            "from_currency": "GBP",
        }
    )
    ToolPermissionStore().set_levels("core", list(WRITE_TOOLS), "always_allow")
    return ids


def score(ids: dict[str, str], events: list[dict], error: str = "") -> Score:
    """Fixed = exactly one live £1,250 Acme → Birch transfer that day, and nothing else of
    £1,250 left on either account that day (no reversed transfer, no expense)."""
    from backend.services import ledger

    rows = ledger.get_transactions(date_from=DATE, date_to=DATE)["transactions"]
    big = [r for r in rows if abs(float(r.get("from_amount") or r.get("amount") or 0)) == AMOUNT]
    right = [
        r
        for r in big
        if r.get("kind") == "transfer"
        and r.get("from_account_id") == ids[SOURCE]
        and r.get("to_account_id") == ids[DEST]
    ]
    calls = [e["name"] for e in events if e.get("type") == "tool_use"]
    turn2 = _after_last_user_turn(events)
    loaded = [
        n
        for e in turn2
        if e.get("type") == "tool_use" and e.get("name") == "find_tools"
        for n in (e.get("input") or {}).get("names") or []
    ]
    return Score(
        fixed=len(right) == 1 and len(big) == 1,
        attempted=any(e.get("type") == "tool_use" and e.get("name") in WRITE_TOOLS for e in turn2),
        loaded=loaded,
        calls=calls,
        ledger=", ".join(_describe(r, ids) for r in big) or "none",
        reply="".join(e.get("text", "") for e in turn2 if e.get("type") == "text_delta")[-400:],
        error=error,
    )


def _after_last_user_turn(events: list[dict]) -> list[dict]:
    marks = [i for i, e in enumerate(events) if e.get("type") == "turn_start"]
    return events[marks[-1] + 1 :] if marks else events


def _describe(row: dict, ids: dict[str, str]) -> str:
    names = {v: k.split()[0] for k, v in ids.items()}
    src = names.get(row.get("from_account_id"), "?")
    if row.get("kind") == "transfer":
        return f"transfer {src}→{names.get(row.get('to_account_id'), '?')}"
    return f"{row.get('kind')} {src}"
