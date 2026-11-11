"""What a follow tells the rest of the app: one Intel story per new filing (a daily ETF: per day with a change)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from backend.db.intel_stories_store import IntelStoriesStore
from backend.services.intel_stories import apply_story_change

logger = logging.getLogger(__name__)

_KEY_PREFIX = "followed_portfolio"
_NO_CHANGE = "no change in the top holdings"
_WHAT = {
    "fund_13f": "filed a new 13F",
    "house_member": "disclosed new trades",
    "etf_daily": "changed its holdings",
}


def _counts(model: dict) -> str:
    by_change: dict[str, int] = {}
    for holding in model["holdings"]:
        by_change[holding["change"]] = by_change.get(holding["change"], 0) + 1
    parts = [f"{by_change[k]} {k}" for k in ("new", "increased", "reduced") if by_change.get(k)]
    if model["exited"]:
        parts.append(f"{len(model['exited'])} exited")
    return ", ".join(parts) or _NO_CHANGE


def emit_new_filing_story(follow: dict, model: dict) -> bool:
    """One Intel story per (follow, filing). Returns False when that filing already has one.
    Never raises: a story is a courtesy and must not fail the refresh that produced it."""
    key = f"{_KEY_PREFIX}:{follow['id']}:{(follow.get('current') or {}).get('filing_id')}"
    try:
        if any(s.get("synthetic_key") == key for s in IntelStoriesStore().all()):
            return False
        now = datetime.now(timezone.utc).isoformat()
        kind = follow["kind"]
        if kind == "etf_daily" and _counts(model) == _NO_CHANGE:
            return False  # a day with no move is not news; one story per day at most
        what = _WHAT[kind]
        basis = (
            " Weights are estimated from disclosed trade ranges." if kind == "house_member" else ""
        )
        apply_story_change(
            {
                "id": str(uuid.uuid4()),
                "type": "topic",
                "label": f"{follow['name']} {what}",
                "ticker": "",
                "status": "monitoring",
                "current_summary": (
                    f"{_counts(model).capitalize()}. Filed {model['filed_at']}, "
                    f"positions as of {model['as_of']}.{basis}"
                ),
                "portfolio_impact": "",
                "action_items": [],
                "archived": False,
                "resolved": False,
                "synthetic_key": key,
                "created_at": now,
                "updated_at": now,
            }
        )
        return True
    except Exception:
        logger.exception("followed portfolios: could not raise the new-filing story")
        return False
