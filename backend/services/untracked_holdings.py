"""The Untracked holdings band — what a Brokerage total account's ledger balance IS.

ADR 2026-11-10-an-investment-account-is-valued-by-its-brokerage-total-or-its-live-holdings
(amendment 2). On a Brokerage total account (``account_value.TOTAL``) the ledger balance is the
part of the total not entered as holdings: invested at the brokerage, asset class unknown. It
bands as ``untracked_holdings`` — never as settlement cash, never as retirement cash. A Live
holdings account is unchanged: its balance is still its cash.

Net worth and cash on hand don't move; only the bucketing does. Readers that measure a book of
broken-down holdings (rebalance, concentration, today's move) keep this amount out of their
denominators and report it beside them (``untracked_excluded``).

Every entry point is empty / None while ``allow_brokerage_total_valuation`` is off, read through
``account_value.enabled`` (docs/FEATURE_FLAGS.md rule 1), so the old outputs stay as they were.
"""

from __future__ import annotations

from typing import Optional

from backend.models.account import UNTRACKED_HOLDINGS_BAND
from backend.services import account_value

BAND = UNTRACKED_HOLDINGS_BAND
LABEL = "Untracked holdings"


def account_ids(accounts: list[dict]) -> frozenset[str]:
    """The accounts whose ledger balance bands as ``untracked_holdings``. Read from each
    account's CURRENT source, so a past chart point bands the way today's does and the chart
    doesn't jump on the day the source is switched. Holdings and account rows are read once,
    and only when some account has no stored source yet."""
    candidates = [a for a in accounts if account_value.applies(a)]
    if not candidates or not account_value.enabled():
        return frozenset()
    held: set[str] | None = None
    rows: list[dict] | None = None
    if any(a.get("value_source") not in account_value.SOURCES for a in candidates):
        from backend.services.accounts import all_account_rows

        held, rows = account_value.held_account_ids(), all_account_rows()
    return frozenset(
        a["id"]
        for a in candidates
        if account_value.source_of(a, held=held, rows=rows) == account_value.TOTAL
    )


def untracked_excluded(rows: list[dict]) -> Optional[dict]:
    """What a book of broken-down holdings leaves out: each Brokerage total account's untracked
    part, folded from projected portfolio rows (``portfolio_rows.projected_rows``) that already
    valued it — no second valuation. None while the flag is off."""
    if not account_value.enabled():
        return None
    accounts = [
        {
            "account_id": r.get("account_id") or "",
            "name": r.get("account_name") or r.get("label") or "",
            "type": r.get("account_type") or "",
            "value": round(float(r.get("base_value") or 0), 2),
        }
        for r in rows
        if r.get("asset_class") == BAND and round(float(r.get("base_value") or 0), 2) != 0
    ]
    accounts.sort(key=lambda a: -a["value"])
    return {"total": round(sum(a["value"] for a in accounts), 2), "accounts": accounts}


def untracked_excluded_now(base: Optional[str] = None) -> Optional[dict]:
    """``untracked_excluded`` for a caller holding no projection (the rebalance plan), at
    last-known prices. None while the flag is off, with nothing read."""
    if not account_value.enabled():
        return None
    from backend.services.portfolio_rows import projected_rows

    return untracked_excluded(projected_rows(base, live=False)["rows"])


def total_now(base: str) -> Optional[float]:
    """The band's total today, read from net worth's own fold (``ledger.net_worth``) — for a
    caller that only names the amount (the Portfolio brief). None while the flag is off."""
    if not account_value.enabled():
        return None
    from backend.services import ledger as ledger_service

    classes = ledger_service.net_worth(base)["breakdown"]["account_classes"]
    return round(float(classes.get(BAND) or 0.0), 2)


def note(excluded: Optional[dict], base: str, *, action: str) -> Optional[str]:
    """One sentence naming what was left out and how to bring it in, e.g. "USD 4,980 at Old
    401(k) isn't broken into holdings — add its holdings to rebalance it." (`action`:
    "rebalance", "track"). None when nothing was left out."""
    if not excluded or not excluded.get("accounts"):
        return None
    accounts = excluded["accounts"]
    where = (
        accounts[0]["name"] or "a Brokerage total account"
        if len(accounts) == 1
        else f"{len(accounts)} Brokerage total accounts"
    )
    its, it = ("its", "it") if len(accounts) == 1 else ("their", "them")
    return (
        f"{base} {excluded['total']:,.0f} at {where} isn't broken into holdings — "
        f"add {its} holdings to {action} {it}."
    )
