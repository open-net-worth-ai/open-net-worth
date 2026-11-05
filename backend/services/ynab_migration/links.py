"""Linking a YNAB account to an account already in the book instead of creating one (ADR
``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``, amendment 2026-11-03).

A link is the YNAB id on the existing account's ``external_ids`` — exactly what an account the
import created carries, so the two-way sync pairs with it the same way. The hard half is the
transactions both sides already hold. They are matched by the ledger's own engine
(``match_preflight.check_proposed_rows`` — ``check_duplicates``' backend, ``match_batch``'s 1:1
assignment), never a matcher of our own, and each YNAB row on a linked account lands in one of:

  * ``link``  — the engine is sure (strong band) it is a row already here, on the same accounts:
    that row takes the YNAB ids (``ledger.attach_external_refs``) and nothing new is booked;
  * ``check`` — the engine thinks it may be (review band, or sure but on other accounts): booked
    as a new row unless the person said in the review that it is the same one (``same_as``);
  * ``add``   — no evidence: booked as a new row.

Categories play no part in a match (the engine keys on accounts, amounts, dates and wording), so
the preview, which has no categories yet, decides exactly what the import does.
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.core.txn_sources import canonical_writer
from backend.services import ledger as ledger_service
from backend.services import match_preflight
from backend.services.budget_app import current
from backend.services.matching import DUPLICATE_CLASSES
from backend.services.ynab_migration.mapping import amt, render_piece

# A tracking (off-budget) asset may live in any off-budget asset account; a budget account only
# in an account of its own type, or the budget's cash and card coverage would change.
_OFF_BUDGET_ASSETS = frozenset({"fixed_deposit", "investment", "retirement", "receivable"})
_COMPATIBLE = {
    "cash": frozenset({"cash"}),
    "credit": frozenset({"credit"}),
    "loan": frozenset({"loan"}),
    "fixed_deposit": _OFF_BUDGET_ASSETS,
    "investment": _OFF_BUDGET_ASSETS,
}
_DUPLICATE = frozenset(k.value for k in DUPLICATE_CLASSES)
CHUNK = match_preflight.MAX_PROPOSED_ROWS
PAIRS_CAP = 100  # possible matches listed per account in the review


class _NoCategory(dict):
    def __missing__(self, key):
        return None


def ynab_ref(account: dict) -> str | None:
    return next(
        (
            e.get("ref")
            for e in account.get("external_ids") or []
            if e.get("source") == current().source
        ),
        None,
    )


def _fits(pa: dict, account: dict, currency: str) -> bool:
    return (
        not pa["closed"]
        and not account.get("archived")
        and (account.get("currency") or "").upper() == currency
        and account.get("type") in _COMPATIBLE.get(pa["payload"]["type"], ())
        and ynab_ref(account) in (None, pa["id"])
    )


def candidates(plan: dict, existing: list[dict]) -> dict[str, list[dict]]:
    """YNAB account id -> the accounts here it may be linked to (none for one already linked)."""
    linked = {ynab_ref(a) for a in existing}
    return {
        pa["id"]: []
        if pa["id"] in linked
        else [
            {"id": a["id"], "name": a["name"], "type": a["type"]}
            for a in existing
            if _fits(pa, a, plan["currency"])
        ]
        for pa in plan["accounts"]
    }


def resolve(plan: dict, links: dict[str, str] | None, existing: list[dict]) -> dict[str, dict]:
    """Validate the person's choice (YNAB account id -> account id here); returns the rows."""
    plan_accounts = {pa["id"]: pa for pa in plan["accounts"]}
    by_id = {a["id"]: a for a in existing}
    already = {ynab_ref(a): a for a in existing if ynab_ref(a)}
    out: dict[str, dict] = {}
    for ynab_id, account_id in (links or {}).items():
        pa, account = plan_accounts.get(ynab_id), by_id.get(account_id)
        if pa is None:
            raise ValidationError(
                f"That {current().name} account is not in the plan; review it again."
            )
        name = pa["payload"]["name"]
        if account is None:
            raise ValidationError(f"The account chosen for {name} is not in this book.")
        held = already.get(ynab_id)
        if held is not None and held["id"] != account_id:
            raise ValidationError(f"{name} is already linked to {held['name']}.")
        if not _fits(pa, account, plan["currency"]):
            raise ValidationError(
                f"{name} cannot be linked to {account['name']}: it must be an open "
                f"{plan['currency']} account of a matching type that no other {current().name} "
                "account uses."
            )
        if account_id in {a["id"] for a in out.values()}:
            raise ValidationError(f"{account['name']} is chosen for two {current().name} accounts.")
        out[ynab_id] = account
    return out


def _signed(p: dict, ynab_id: str) -> float:
    return -amt(p["from"][1]) if p.get("from") and p["from"][0] == ynab_id else amt(p["to"][1])


def _legs(p: dict) -> set[str]:
    """The YNAB accounts a piece moves money in."""
    return {p[s][0] for s in ("from", "to") if p.get(s)}


def _accounts_of(row: dict) -> set:
    return {row.get("from_account_id"), row.get("to_account_id")} - {None}


def _decide(row: dict, result: dict, taken: set[str], openings: frozenset[str]) -> tuple:
    best = result["candidates"][0] if result["candidates"] else None
    txn = best and best["existing"]
    if (
        txn is None
        or best["klass"] not in _DUPLICATE
        or result["band"] not in ("strong", "review")
        or txn["id"] in taken
        or txn["id"] in openings
        or any(e.get("source") == current().source for e in txn.get("external_refs") or [])
    ):
        return "add", None
    sure = result["band"] == "strong" and _accounts_of(txn) == _accounts_of(row)
    return ("link" if sure else "check"), txn


def match(
    plan: dict,
    acct_ids: dict[str, str],
    linked: set[str],
    same_as: dict[str, str] | None = None,
) -> dict[int, dict]:
    """Piece index -> ``{action, existing}`` for every piece on a ``linked`` YNAB account that is
    not in the ledger yet. ``acct_ids`` maps every YNAB account to an id (a placeholder will do
    for one not created yet: nothing here can match it). ``same_as`` (first YNAB ref -> ledger
    id) is the person's word that a possible match is the same transaction."""
    same_as = same_as or {}
    pieces = plan["pieces"]
    todo = [
        i
        for i, p in enumerate(pieces)
        if _legs(p) & linked
        and not any(
            ledger_service.transactions.has_external_ref(current().source, r) for r in p["refs"]
        )
    ]
    # One account's rows together, in date order: a chunk boundary then cuts the fewest pairs.
    todo.sort(key=lambda i: (min(_legs(pieces[i]) & linked), pieces[i]["date"]))
    source = canonical_writer(current().source) or current().source
    openings = ledger_service.opening_txn_ids()
    taken: set[str] = set()
    out: dict[int, dict] = {}
    for start in range(0, len(todo), CHUNK):
        chunk = todo[start : start + CHUNK]
        rows = [
            {**render_piece(pieces[i], acct_ids, _NoCategory(), plan["currency"]), "source": source}
            for i in chunk
        ]
        results = match_preflight.check_proposed_rows(rows, max_candidates=1)
        for i, row, result in zip(chunk, rows, results):
            action, txn = _decide(row, result, taken, openings)
            if action == "check" and same_as.get(pieces[i]["refs"][0]) == txn["id"]:
                action = "link"
            if txn is not None:
                taken.add(txn["id"])
            out[i] = {"action": action, "existing": txn}
    return out


def _existing_row(txn: dict, account_id: str) -> dict:
    signed = (
        -float(txn.get("from_amount") or 0)
        if txn.get("from_account_id") == account_id
        else float(txn.get("to_amount") or 0)
    )
    return {
        "id": txn["id"],
        "date": txn["date"],
        "payee": txn.get("description") or None,
        "amount": signed,
    }


def summary(plan: dict, rows: dict[str, dict], decisions: dict[int, dict]) -> dict[str, dict]:
    """Per linked YNAB account: how many of its rows match, wait on the person, or are added,
    how many of the account's own rows YNAB does not hold, and the possible pairs to check."""
    claimed = {d["existing"]["id"] for d in decisions.values() if d["existing"]}
    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    openings = ledger_service.opening_txn_ids()
    out: dict[str, dict] = {}
    for ynab_id, account in rows.items():
        acct = account["id"]
        mine = [(i, d) for i, d in decisions.items() if ynab_id in _legs(plan["pieces"][i])]
        count = {"link": 0, "check": 0, "add": 0}
        pairs = []
        for i, d in mine:
            count[d["action"]] += 1
            if d["action"] == "check" and len(pairs) < PAIRS_CAP:
                p = plan["pieces"][i]
                pairs.append(
                    {
                        "ref": p["refs"][0],
                        "date": p["date"],
                        "payee": p["description"] or None,
                        "amount": _signed(p, ynab_id),
                        "existing": _existing_row(d["existing"], acct),
                    }
                )
        own = sum(
            1
            for t in txns
            if acct in _accounts_of(t)
            and t["id"] not in openings
            and t["id"] not in claimed
            and not any(e.get("source") == current().source for e in t.get("external_refs") or [])
        )
        out[ynab_id] = {
            "matched": count["link"],
            "to_check": count["check"],
            "added": count["add"],
            "own_only": own,
            "pairs": pairs,
        }
    return out
