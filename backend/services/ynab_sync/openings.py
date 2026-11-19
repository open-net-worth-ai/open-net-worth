"""A Starting Balance changed in the app comes across. The transaction sync leaves openings out
(``units._eligible``): here one is an account's ``opening_txn_id``, written only through
``ledger.set_account_opening``, and the app's may carry categorized parts the import booked as
rows of their own. So each run compares the app's Starting Balance with what the last run saw
(``openings_seen`` in the sync state) and moves this book's by the same difference. A change
made here survives it, and is not sent to the app."""

from __future__ import annotations

import logging
from collections import Counter

from backend.core.errors import OpenNetWorthError
from backend.services import ledger as ledger_service
from backend.services.budget_app import current
from backend.services.ynab_sync import recorder
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


def _moved(acct: dict, was: list, now: list) -> tuple[float, str | None]:
    """This book's opening moved by the app's difference. Its date follows only when it still
    is the app's old one: an import may have dated it earlier, to cover older rows."""
    amount = ledger_service.opening_amount(acct)
    date = ledger_service.opening_date(acct)
    new_amount = round(amount + (now[0] - was[0]) / 1000, 2)
    return new_amount, (now[1] if date == was[1] else date)


def pull(ctx: Context, st: dict, tally: Counter) -> None:
    seen: dict[str, list] = st.setdefault("openings_seen", {})
    now_all = _seen_now(ctx)
    pulls = st["direction"] != "to_ynab"
    for ynab_acct, now in now_all.items():
        was = seen.get(ynab_acct)
        seen[ynab_acct] = now
        if not pulls or was is None or was == now:
            continue
        acct = ledger_service.accounts.find_by_id(ctx.acct_ids[ynab_acct])
        if acct is None or not acct.get("opening_txn_id"):
            continue
        amount, date = _moved(acct, was, now)
        try:
            ledger_service.set_account_opening(acct["id"], amount, date)
        except OpenNetWorthError as exc:
            logger.info("%s Starting Balance refused for %s: %s", current().name, acct["id"], exc)
            seen[ynab_acct] = was
            recorder.error(f"{acct['name']} Starting Balance", str(exc))
            continue
        recorder.note(
            f"{acct['name']} Starting Balance",
            f"Changed in {current().name}: now {amount:,.2f} on {date}, as there.",
        )
        tally["pulled"] += 1
