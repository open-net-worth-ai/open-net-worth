"""Ready to Assign here beside the app's own To Budget, for the same month, with the part the two
are known to count differently: money in card payment envelopes, which the app (Actual) has no
place for. Read once per run and kept in the sync's meta, so the page doesn't recompute a budget
on every look. The app's figure is as the run read it, before its own writes."""

from __future__ import annotations

import logging

from backend.core.errors import OpenNetWorthError
from backend.services import planning

logger = logging.getLogger(__name__)


def _payment_envelopes_hold(month_view: dict) -> float:
    total = 0.0
    for group in month_view.get("groups") or []:
        for env in group.get("categories") or []:
            if env.get("is_payment_envelope"):
                total += float(env.get("available") or 0)
    return round(total, 2)


def compare(theirs: dict) -> dict | None:
    """``{"month", "theirs", "here", "difference", "card_payment_envelopes"}`` in the book's
    unit, or ``None`` when the app has no figure (a tracking budget) or the budget can't be read."""
    if theirs.get("amount") is None:
        return None
    month = theirs["month"]
    try:
        view = planning.budget_month(month)
    except OpenNetWorthError:
        logger.info("budget month %s unreadable for the To Budget comparison", month)
        return None
    here = round(float(view["ready_to_assign"]), 2)
    there = round(theirs["amount"] / 1000, 2)
    return {
        "month": month,
        "theirs": there,
        "here": here,
        "difference": round(here - there, 2),
        "card_payment_envelopes": _payment_envelopes_hold(view),
    }
