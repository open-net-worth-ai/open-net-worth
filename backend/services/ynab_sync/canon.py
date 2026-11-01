"""The canonical form both sides are compared in.

A ledger transaction and a YNAB transaction (mapped through the migration's own
``mapping.plan_transactions`` + ``render_piece``) are both reduced to the same small dict, so
"did this change?" is plain equality — never a timestamp (YNAB has none per transaction) and
never a hash of a representation that legitimately differs (cents versus milliunits, a transfer's
one cleared flag versus YNAB's two). ``None`` stands for "does not exist" (deleted)."""

from __future__ import annotations

from typing import Optional

Canon = Optional[dict]

# The fields a sync can carry. Anything else on a ledger row (tags, payee entity, receipts…) is
# the ledger's own and never leaves it.
FIELDS = ("kind", "date", "description", "memo", "cleared", "category", "splits", "from", "to")


def _cents(value) -> Optional[int]:
    return None if value is None else round(round(abs(float(value)), 2) * 100)


def _leg(row: dict, side: str):
    account = row.get(f"{side}_account_id")
    return [account, _cents(row.get(f"{side}_amount"))] if account else None


def canon(row: dict) -> dict:
    """A ledger row, or a ``render_piece`` row, in canonical form."""
    splits = row.get("splits")
    transfer = row.get("kind") == "transfer"
    return {
        "kind": row.get("kind"),
        "date": row.get("date"),
        # A transfer's description is YNAB's "Transfer : <account>" payee, which YNAB derives.
        "description": "" if transfer else (row.get("description") or "").strip(),
        "memo": (row.get("memo") or "").strip(),
        "cleared": bool(row.get("cleared")),
        "category": None if splits else (row.get("category") or None),
        "splits": (
            [
                [s.get("category"), _cents(s.get("amount")), (s.get("memo") or "").strip()]
                for s in splits
            ]
            if splits
            else None
        ),
        "from": _leg(row, "from"),
        "to": _leg(row, "to"),
    }


def reconstruct_original(row: dict, edits: list[dict]) -> dict:
    """The ledger row as it was before its audited edits: for each field an edit changed, the
    ``before`` value of the EARLIEST edit that touched it. Update events carry a sparse
    before/after diff of exactly the fields they changed."""
    original = dict(row)
    seen: set[str] = set()
    for event in edits:
        if event.get("op") != "update":
            continue
        for key, value in (event.get("before") or {}).items():
            if key not in seen:
                seen.add(key)
                original[key] = value
    return original


def changed_fields(before: Canon, after: Canon) -> list[str]:
    """Which canonical fields differ (``["*"]`` when one side is absent)."""
    if before is None or after is None:
        return [] if before is after else ["*"]
    return [f for f in FIELDS if before.get(f) != after.get(f)]


def view(c: Canon, names: dict) -> Optional[dict]:
    """A canonical form as the review list shows it: account and category names, a signed
    amount in the plan's currency, nothing the person would have to decode."""
    if c is None:
        return None
    leg_out, leg_in = c["from"], c["to"]
    if c["kind"] == "transfer" and leg_out and leg_in:
        account = (
            f"{names['accounts'].get(leg_out[0], '?')} → {names['accounts'].get(leg_in[0], '?')}"
        )
        cents = leg_out[1]
    else:
        leg = leg_in or leg_out
        account = names["accounts"].get(leg[0], "?") if leg else ""
        cents = (leg[1] if leg_in else -leg[1]) if leg and leg[1] is not None else None
    category = (
        "Split: " + ", ".join(names["categories"].get(s[0], "?") for s in c["splits"])
        if c["splits"]
        else names["categories"].get(c["category"])
        if c["category"]
        else None
    )
    return {
        "date": c["date"],
        "account": account,
        "payee": c["description"],
        "memo": c["memo"],
        "category": category,
        "amount": None if cents is None else cents / 100,
        "cleared": c["cleared"],
    }
