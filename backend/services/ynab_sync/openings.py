"""A Starting Balance changed on either side comes across. The transaction sync leaves openings out
(``units._eligible``): here one is an account's ``opening_txn_id``, written only through
``ledger.set_account_opening``, and the app's may carry categorized parts the import booked as
rows of their own, so the two amounts need not be equal. Each run therefore compares both sides
with what the last run saw (``openings_seen``: the app's, ``openings_ours``: this book's) and
moves the other side by the same difference. Both changed: each side takes the other's difference,
so neither change is lost. The app's date follows here when it still was ours; ours is never sent:
an opening here may be dated earlier on purpose, to cover older rows (``starts``). A one-way
direction holds the other way's difference until it is allowed through."""

from __future__ import annotations

import logging
from collections import Counter

from backend.core.errors import ExternalServiceError, OpenNetWorthError
from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.ynab_sync import recorder
from backend.services.ynab_sync.client import RateCapReached, YnabClient
from backend.services.ynab_sync.context import Context

logger = logging.getLogger(__name__)


def _seen_now(ctx: Context) -> dict[str, list]:
    """The app's account id -> [amount in milliunits, date] of its live Starting Balance."""
    out: dict[str, list] = {}
    for ynab_acct, tid in ctx.openings.items():
        t = ctx.mirror.get(tid)
        if t and not t.get("deleted") and ynab_acct in ctx.acct_ids:
            out[ynab_acct] = [int(t["amount"]), t["date"]]
    return out


def _ours(acct: dict) -> list:
    """This book's opening of ``acct`` as [milliunits, date]."""
    return [round(ledger_service.opening_amount(acct) * 1000), ledger_service.opening_date(acct)]


def _moved(at: list, was: list, now: list) -> list:
    """``at`` moved by the difference ``was`` -> ``now``; its date follows when it still was
    ``was``'s."""
    return [at[0] + now[0] - was[0], now[1] if at[1] == was[1] else at[1]]


def sync(ctx: Context, st: dict, tally: Counter, client: YnabClient | None = None) -> None:
    seen: dict[str, list] = st.setdefault("openings_seen", {})
    ours_seen: dict[str, list] = st.setdefault("openings_ours", {})
    pulls, pushes = st["direction"] != "to_ynab", st["direction"] != "from_ynab"
    for ynab_acct, theirs in _seen_now(ctx).items():
        acct = ledger_service.accounts.find_by_id(ctx.acct_ids[ynab_acct])
        if acct is None or not acct.get("opening_txn_id"):
            continue
        mine = _ours(acct)
        was_theirs, was_mine = seen.get(ynab_acct), ours_seen.get(ynab_acct)
        if was_theirs is None or was_mine is None:
            seen[ynab_acct], ours_seen[ynab_acct] = theirs, mine  # first run: nothing to compare
            continue
        if theirs != was_theirs and pulls:
            if _pull(acct, mine, was_theirs, theirs, tally) is None:
                continue  # refused here: both differences wait for the next run
            # Ours moved by theirs: both baselines now include the app's change.
            was_mine = _moved(was_mine, was_theirs, theirs)
            mine = _ours(ledger_service.accounts.find_by_id(acct["id"]) or acct)
            was_theirs = theirs
        if mine[0] != was_mine[0] and pushes and client is not None:
            if _push(client, ctx.openings[ynab_acct], acct, theirs, was_mine, mine) is None:
                seen[ynab_acct], ours_seen[ynab_acct] = was_theirs, was_mine
                continue
            tally["sent"] += 1
            # The app took our difference on top of what the baseline says it held.
            was_theirs = [was_theirs[0] + mine[0] - was_mine[0], was_theirs[1]]
        sends = pushes and client is not None
        was_mine = mine if sends or mine[0] == was_mine[0] else [was_mine[0], mine[1]]
        seen[ynab_acct], ours_seen[ynab_acct] = was_theirs, was_mine


def _pull(acct: dict, mine: list, was: list, now: list, tally: Counter) -> list | None:
    """Move this book's opening by the app's difference; ``None`` when the ledger refuses it."""
    amount, date = _moved(mine, was, now)
    try:
        ledger_service.set_account_opening(acct["id"], round(amount / 1000, 2), date)
    except OpenNetWorthError as exc:
        logger.info("%s Starting Balance refused for %s: %s", current().name, acct["id"], exc)
        recorder.error(f"{acct['name']} Starting Balance", str(exc))
        return None
    recorder.note(
        f"{acct['name']} Starting Balance",
        f"Changed in {current().name}: now {amount / 1000:,.2f} on {date}, as there.",
    )
    tally["pulled"] += 1
    return [amount, date]


def _push(client: YnabClient, tid: str, acct: dict, theirs: list, was: list, now: list):
    """Move the app's Starting Balance by this book's difference ``was`` -> ``now``, keeping its
    date; returns what the app now holds, ``None`` when it couldn't be sent this run."""
    amount, date = theirs[0] + now[0] - was[0], theirs[1]
    try:
        client.save_transactions("PATCH", [{"id": tid, "amount": amount}])
    except RateCapReached:
        return None  # the next run finds the same difference
    except ExternalServiceError as exc:
        logger.info("%s refused a Starting Balance for %s: %s", current().name, acct["id"], exc)
        recorder.error(f"{acct['name']} Starting Balance", f"{current().name} refused it.")
        return None
    recorder.note(
        f"{acct['name']} Starting Balance",
        f"Changed here: now {amount / 1000:,.2f} on {date} in {current().name} too.",
    )
    return [amount, date]
