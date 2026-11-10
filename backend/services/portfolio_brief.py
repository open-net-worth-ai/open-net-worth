"""portfolio_brief — server-side Portfolio Brief signal computation.

Computes the 6 signal types shown in the investments Brief card:
  Concentration → Rebalance → Vesting → Income → Today → On-track (fallback)

Replaces the frontend useInvestmentSignals hook computation so the backend
drives all signal logic. Called by GET /api/investments/brief.

Priority order: concentration → rebalance → vesting → income → today → ontrack.
On any sub-call failure the signal is simply omitted (graceful degradation).
Never raises; returns an empty-signals / ontrack dict on any error.

Dependency rule: imports only core/, db/, and sibling services/ — never api/ or ai/.

Currency amounts are formatted with `_fmt_base` to match the frontend's fBase
(Intl narrowSymbol, e.g. "S$1.89"), so brief narratives read identically to the
rest of the UI.
"""

from __future__ import annotations

import logging
from datetime import date
from math import ceil
from typing import Any

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.db import read_cache
from backend.services import equity as equity_service
from backend.services import investments_book, untracked_holdings
from backend.services import positions as positions_service
from backend.services import profile as profile_service
from backend.services import rebalance as rebalance_service
from backend.services.dividend_calendar import upcoming_payments

_log = logging.getLogger(__name__)

# Rebalance: a class is notably off-target when drift exceeds this threshold (pp).
REBALANCE_DRIFT_NOTABLE_PCT = 5

# "Today" signal: only surfaces when day move is at least this % (avoids noise on flat days).
TODAY_SIGNAL_MIN_PCT = 1

# On-track fallback — emitted when no actionable signals are found.
_ONTRACK: dict[str, Any] = {
    "id": "ontrack",
    "kind": "ontrack",
    "tone": "good",
    "actionable": False,
    "tag": "On track",
    "narrative": "Your portfolio is within guidelines. No rebalancing or concentration alerts.",
}


# Narrow currency symbols — mirrors the frontend's fBase / fCurrency narrowSymbol
# output (utils/format.ts UNAMBIGUOUS map + common ISO codes) so brief narratives
# read identically to the rest of the UI (e.g. 'S$1.89', not 'SGD 1.89').
_CURRENCY_SYMBOL = {
    "SGD": "S$",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "HKD": "HK$",
    "NZD": "NZ$",
    "CNY": "¥",
    "INR": "₹",
    "KRW": "₩",
    "VND": "₫",
    "THB": "฿",
    "MYR": "RM",
    "IDR": "Rp",
}
# Currencies conventionally shown without minor units.
_ZERO_DECIMAL = {"JPY", "KRW", "VND", "IDR"}


def _fmt_base(amount: float, base: str) -> str:
    """Format an amount in base currency to match the frontend's fBase
    (Intl narrowSymbol). E.g. _fmt_base(1.89, 'SGD') -> 'S$1.89'."""
    code = (base or "SGD").upper()
    body = f"{amount:,.0f}" if code in _ZERO_DECIMAL else f"{amount:,.2f}"
    sym = _CURRENCY_SYMBOL.get(code)
    return f"{sym}{body}" if sym else f"{code} {body}"


def _days_until(iso_date: str) -> int:
    """Ceiling of days from today until the given ISO date string."""
    target = date.fromisoformat(iso_date)
    delta = target - user_today()
    return ceil(delta.total_seconds() / 86_400)


def _concentration_narrative(
    top: dict, over_by: float, guideline_pct: float, *, untracked: str | None = None
) -> str:
    """Information, not an order: what the name is, then how it sits against the guideline —
    the nudge used to tell a day-one user to trim against a 20% rule they never set (persona QA
    round 7). A guideline the user set is called theirs. `untracked` (a formatted amount): money
    at Brokerage total accounts not broken into holdings, which the share leaves out — so the
    share is "of the holdings you've entered", and the sentence says what wasn't counted."""
    of = "of the holdings you've entered" if untracked else "of your investments"
    share = f"{top['name']} is {top['pct']:.0f}% {of}"
    whose = "the" if guideline_pct == investments_book.CONCENTRATION_GUIDELINE_PCT else "your"
    text = (
        f"{share}, {over_by:.0f} percentage points over {whose} {guideline_pct:.0f}% "
        f"single-name guideline."
    )
    if untracked:
        text += f" {untracked} not broken into holdings isn't counted."
    return text


def _concentration_signal(
    rows: list[dict], base: str, concentration_guideline_pct: float
) -> dict[str, Any] | None:
    """Signal 1: the largest single name breaches the concentration guideline.

    Reads the ONE concentration authority (`investments_book.concentration_by_name`): the same
    instrument held at several accounts is one name, and the denominator is the invested book —
    exactly what the Risk card and the Intel risk story show."""
    projected, grand = investments_book.project_invested(rows, base)
    ranked = investments_book.concentration_by_name(projected, grand, concentration_guideline_pct)
    if not ranked or not ranked[0]["hot"]:
        return None
    top = ranked[0]
    over_by = top["pct"] - concentration_guideline_pct
    untracked_total = untracked_holdings.total_now(base)  # None while the flag is off
    untracked = _fmt_base(untracked_total, base) if untracked_total else None
    return {
        "id": "concentration",
        "kind": "concentration",
        "tone": "warn",
        "actionable": True,
        "tag": "Concentration",
        "narrative": _concentration_narrative(
            top, over_by, concentration_guideline_pct, untracked=untracked
        ),
        "amount": top["value"],
    }


# How an asset class reads as the subject of a sentence ("Equities are ..."), not as the raw key.
_CLASS_SUBJECT = {
    "equity": ("Equities", "are"),
    "bond": ("Bonds", "are"),
    "commodity": ("Commodities", "are"),
    "crypto": ("Crypto", "is"),
    "option": ("Options", "are"),
    "cash": ("Cash", "is"),
}


def _rebalance_signal() -> dict[str, Any] | None:
    """Signal 2: any asset class with notable drift when targets are configured."""
    try:
        plan = rebalance_service.rebalance_plan(mode="sell_to_target")
    except Exception:
        _log.debug("rebalance_plan failed", exc_info=True)
        return None

    if plan.get("status") != "ok":
        return None

    allocations = plan.get("allocations") or []
    off_target = [
        a
        for a in allocations
        if a.get("off_target") and abs(a.get("drift_pct") or 0) >= REBALANCE_DRIFT_NOTABLE_PCT
    ]
    if not off_target:
        return None

    worst = max(off_target, key=lambda a: abs(a.get("drift_pct") or 0))
    drift = worst.get("drift_pct") or 0
    direction = "above" if drift > 0 else "below"
    count = len(off_target)
    subject, verb = _CLASS_SUBJECT.get(worst.get("asset_class") or "", ("This asset class", "is"))
    return {
        "id": "rebalance",
        "kind": "rebalance",
        "tone": "accent",
        "actionable": True,
        "tag": "Rebalance",
        "narrative": (
            f"{count} asset class{'es' if count != 1 else ''} off target. "
            f"{subject} {verb} {abs(drift):.1f} percentage points {direction} your target. "
            f"Review your target mix."
        ),
    }


def _vesting_signal(base: str) -> dict[str, Any] | None:
    """Signal 3: next vest within 45 days."""
    try:
        items = equity_service.upcoming_vests(window_days=45)
    except Exception:
        _log.debug("upcoming_vests failed", exc_info=True)
        return None

    if not items:
        return None

    next_vest = items[0]
    vest_date = next_vest.get("vest_date") or ""
    if not vest_date:
        return None

    days = _days_until(vest_date)
    if not (0 <= days <= 45):
        return None

    indicative = next_vest.get("indicative_value_base")
    value_str = f" ({_fmt_base(indicative, base)} indicative)" if indicative else ""
    symbol = next_vest.get("symbol") or ""
    return {
        "id": "vesting",
        "kind": "vesting",
        "tone": "accent",
        "actionable": True,
        "tag": "Vesting",
        "narrative": (
            f"{symbol} vests in {days} day{'s' if days != 1 else ''}"
            f"{value_str}. Plan for the tax event."
        ),
        "amount": indicative,
    }


def _income_signal(base: str) -> dict[str, Any] | None:
    """Signal 4: next ex-dividend within 30 days."""
    try:
        cal = upcoming_payments(days=30, base=base)
    except Exception:
        _log.debug("upcoming_payments failed", exc_info=True)
        return None

    next_payment = cal.get("next_payment")
    if not next_payment:
        return None

    ex_date = next_payment.get("ex_date") or ""
    if not ex_date:
        return None

    days = _days_until(ex_date)
    if not (0 <= days <= 30):
        return None

    symbol = next_payment.get("symbol") or ""
    amount_base = next_payment.get("amount_base") or 0.0
    return {
        "id": "income",
        "kind": "income",
        "tone": "good",
        "actionable": False,
        "tag": "Income",
        "narrative": (
            f"{symbol} goes ex-div in {days} day{'s' if days != 1 else ''} — "
            f"{_fmt_base(amount_base, base)} projected."
        ),
        "amount": amount_base,
    }


def _today_signal(rows: list[dict], base: str) -> dict[str, Any] | None:
    """Signal 5: notable day move (suppressed when flat or minimal).

    Reads the ONE "today" authority (`investments_book.today_move`) — the masthead's own figure,
    so the brief and the masthead can never quote two different moves for the same day."""
    projected, _grand = investments_book.project_invested(rows, base)
    today = investments_book.today_move(projected)
    if not today["has_data"] or abs(today["pct"]) < TODAY_SIGNAL_MIN_PCT:
        return None
    total_day, day_pct = today["pl"], today["pct"]
    tone = "good" if total_day >= 0 else "bad"
    sign = "+" if total_day >= 0 else ""
    return {
        "id": "today",
        "kind": "today",
        "tone": tone,
        "actionable": False,
        "tag": "Today",
        "narrative": (
            f"Portfolio is {sign}{_fmt_base(total_day, base)} ({sign}{day_pct:.1f}%) today."
        ),
        "amount": total_day,
    }


@read_cache.cached(lambda: None)
def compute_brief() -> dict:
    """Compute the Portfolio Brief signals. Never raises; degrades to ontrack on any error.

    Returns:
        {
            "signals": list[dict],   # ordered by priority
            "decisions_count": int,  # count of actionable signals
            "base_currency": str,
        }
    """
    try:
        base = base_currency().upper()
    except Exception:
        base = "SGD"

    signals: list[dict[str, Any]] = []

    try:
        rows = positions_service.hydrated()
    except Exception:
        _log.debug("positions.hydrated() failed", exc_info=True)
        rows = []

    try:
        profile = profile_service.read_profile()
    except Exception:
        _log.debug("read_profile failed", exc_info=True)
        profile = {}

    concentration_guideline_pct = investments_book.concentration_guideline(profile)

    # 1 · Concentration
    sig = _concentration_signal(rows, base, concentration_guideline_pct)
    if sig:
        signals.append(sig)

    # 2 · Rebalance
    sig = _rebalance_signal()
    if sig:
        signals.append(sig)

    # 3 · Vesting
    sig = _vesting_signal(base)
    if sig:
        signals.append(sig)

    # 4 · Income
    sig = _income_signal(base)
    if sig:
        signals.append(sig)

    # 5 · Today
    sig = _today_signal(rows, base)
    if sig:
        signals.append(sig)

    # Fallback: on-track when nothing else fires
    if not signals:
        signals.append(_ONTRACK)

    decisions_count = sum(1 for s in signals if s.get("actionable"))

    return {
        "signals": signals,
        "decisions_count": decisions_count,
        "base_currency": base,
    }
