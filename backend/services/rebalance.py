"""
rebalance_service — allocation rebalancing recommendations (P4-RB1).

Compute-only. Given the live investment book (current per-asset_class base value) and
``profile.target_allocation`` (per-class target weights, fractions 0–1), this derives the
set of buy/sell amounts — in the base currency — that move the book toward its targets.
It produces SUGGESTIONS as DATA; it NEVER places an order, NEVER writes a position. If the
user wants to act on a suggestion that flows through the existing position
propose→confirm→apply tools — this module is the analysis layer only.

The "investment book" is the same one the allocation-drift view measures against:
markets only (cash / property / receivable / other assets are NOT rebalanced — see
``NON_INVESTMENT_CLASSES``). Each holding is valued in base via ``ledger.row_base_value``
(qty × price × quote_per_unit, FX-converted) — computed, never trusted from a stored amount.

Two modes:
  * ``sell_to_target`` (default) — full rebalance: each class's target value is its target
    weight × (current book + ``new_cash``); the trade is (target − current), so an
    overweight class is SOLD and an underweight class is BOUGHT. ``new_cash`` (optional) is
    treated as fresh capital added to the book before targets are applied.
  * ``cash_only`` — never sell: distribute ``new_cash`` ONLY across underweight classes, in
    proportion to each one's shortfall versus its target, so the contribution reduces drift
    without realizing anything. With no new cash → no trades.

Targets are normalized (fractions or percentages accepted, like the profile validator), then
RENORMALIZED across the classes that actually carry a target so a partial map (e.g. only
equity+crypto set) still yields a coherent split. No targets set / empty book → a clear
``status`` + zeros, never a 500.

Dependency rule: imports only db/core + sibling services (services → db → core). No api/ai.

Design: P4-RB1.
"""

from __future__ import annotations

import logging

from backend.core.fx import base_currency
from backend.models.account import UNTRACKED_HOLDINGS_BAND
from backend.services.ledger import row_base_value

_log = logging.getLogger(__name__)

# Exposures that own a dedicated tab (or, for `other_asset`, are a car or a watch you do not
# rebalance) — excluded from the rebalanced investment book. So is every account balance (cash,
# brokerage settlement cash, fixed deposits, retirement cash): a balance is not an exposure you
# trade to a target, and targets are set on market classes only — "Trim Settlement cash by
# S$43,234" against a 0% target was no one's trade. Everything else (equity, crypto,
# commodity, bond, …) counts as an investment, so a new exposure type is included automatically.
# `untracked_holdings` (a Brokerage total account's holdings not entered yet) IS invested, but in
# no known class: counted, it would make every known class look underweight. It is left out
# and reported beside the plan instead (`untracked_excluded`).
NON_INVESTMENT_CLASSES = frozenset(
    {
        "cash",
        "settlement_cash",
        UNTRACKED_HOLDINGS_BAND,
        "fixed_deposit",
        "retirement",
        "credit",
        "loan",
        "receivable",
        "property",
        "other_asset",
    }
)

# Drift (percentage points of the book) at/above which a class is flagged "off target". THE one
# authority for this threshold (MATH-37: it used to be 3, 4 or 5 points depending which surface
# you read — `portfolio_analytics.py` its own 3.0, `Allocation.tsx`/`FeedCards.tsx` a hardcoded 4,
# `Rebalance.tsx` a hardcoded 3). `portfolio_analytics` imports this constant directly; both the
# `investments_book()` and `portfolio_analytics()` payloads echo it as `threshold_pp`, and every
# frontend surface reads that field rather than hardcoding a number of its own.
DRIFT_FLAG_PP = 5.0

# A buy/sell below this base amount is noise — not worth a suggested trade.
MIN_TRADE_BASE = 1.0

_MODES = ("sell_to_target", "cash_only")


def _read_profile() -> dict:
    """Best-effort profile read (matches targets/budgets services). Unset → {}."""
    from backend.services.profile import read_profile_raw

    return read_profile_raw()


def normalize_targets(raw: dict) -> dict:
    """Coerce a target-allocation map to positive fractions (0–1).

    Accepts fractions (0.6) or percentages (60) — if any value exceeds 1 the whole map is read
    as percentages and divided by 100 (mirrors api.profile.normalize_target_allocation, kept here
    so services/ never imports api/ — Contract 3). Drops non-numeric / non-positive entries; keeps
    keys (asset_class) as-is. Does NOT force the sum to 1 — that's the caller's renormalization."""
    cleaned: dict[str, float] = {}
    for k, v in (raw or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            cleaned[str(k)] = f
    if cleaned and max(cleaned.values()) > 1:  # looks like percentages → fractions
        cleaned = {k: v / 100 for k, v in cleaned.items()}
    return cleaned


def _book_by_class(rows: list[dict], base: str) -> dict[str, float]:
    """Sum each investment asset_class's live base value over the investment book.

    `vesting_state="unvested"` rows (equity-grant units not yet vested, forfeited if the
    grantee leaves) are excluded — not owned yet, so not part of the book being rebalanced
    (ADR 2026-07-31-unvested-equity-excluded-from-net-worth)."""
    by_class: dict[str, float] = {}
    for row in rows:
        cls = row.get("asset_class") or "equity"
        if cls in NON_INVESTMENT_CLASSES or row.get("vesting_state") == "unvested":
            continue
        value = row_base_value(row, base)
        if value <= 0:
            continue
        by_class[cls] = by_class.get(cls, 0.0) + value
    return by_class


def rebalance_plan(
    *,
    rows: list[dict] | None = None,
    base: str | None = None,
    new_cash: float = 0.0,
    mode: str = "sell_to_target",
) -> dict:
    """Compute current-vs-target weights, drift, and suggested buy/sell trades (in base).

    ``rows`` defaults to the live hydrated holdings; ``base`` to the profile base currency.
    ``new_cash`` (>= 0) is fresh capital to deploy — in ``sell_to_target`` it's added to the
    book before targets apply; in ``cash_only`` it's the only money that moves (no sells).
    ``mode`` is ``sell_to_target`` (full rebalance) | ``cash_only`` (buys only).

    Returns a JSON-able dict:
      ``base_currency``, ``mode``, ``status`` (``ok`` | ``no_targets`` | ``empty_book``),
      ``new_cash``, ``current_total`` (book value, base), ``target_total`` (book + new_cash),
      ``allocations`` (per class: ``asset_class``, ``current_value``, ``current_pct``,
      ``target_pct``, ``target_value``, ``drift_pct``, ``off_target``), and ``trades`` (per
      suggested move: ``asset_class``, ``direction`` buy|sell, ``amount`` (base, positive),
      ``rationale``). Never raises on empty/no-target inputs — returns zeros + a status."""
    base = (base or base_currency()).upper()
    mode = mode if mode in _MODES else "sell_to_target"
    new_cash = max(0.0, float(new_cash or 0.0))

    if rows is None:
        from backend.services import positions as positions_service

        rows = positions_service.hydrated()

    by_class = _book_by_class(rows, base)
    current_total = sum(by_class.values())

    raw_targets = normalize_targets(_read_profile().get("target_allocation") or {})
    # Renormalize across the classes that actually carry a target, so a partial map still splits
    # coherently and target_pct sums to ~100% over the targeted classes.
    target_sum = sum(raw_targets.values())
    targets = {k: v / target_sum for k, v in raw_targets.items()} if target_sum > 0 else {}

    base_result = {
        "base_currency": base,
        "mode": mode,
        "new_cash": round(new_cash, 2),
        "current_total": round(current_total, 2),
        **_untracked_fields(base),
    }

    if not targets:
        return {
            **base_result,
            "status": "no_targets",
            "target_total": round(current_total + new_cash, 2),
            "allocations": [],
            "trades": [],
            "message": "No target allocation configured. Set per-asset-class targets first.",
        }

    if current_total <= 0 and new_cash <= 0:
        return {
            **base_result,
            "status": "empty_book",
            "target_total": 0.0,
            "allocations": _allocations(by_class, targets, current_total, current_total),
            "trades": [],
            "message": "No investment holdings to rebalance.",
        }

    target_total = current_total + new_cash
    allocations = _allocations(by_class, targets, current_total, target_total)
    trades = (
        _cash_only_trades(allocations, new_cash, base)
        if mode == "cash_only"
        else _sell_to_target_trades(allocations, base)
    )

    return {
        **base_result,
        "status": "ok",
        "target_total": round(target_total, 2),
        "allocations": allocations,
        "trades": trades,
    }


def _untracked_fields(base: str) -> dict:
    """What the plan leaves out because it isn't broken into holdings, and the line that says
    so (ADR 2026-11-10, amendment 1). Empty while investment_value_source is off — the old
    payload."""
    from backend.services import untracked_holdings

    excluded = untracked_holdings.untracked_excluded_now(base)
    if excluded is None:
        return {}
    return {
        "untracked_excluded": excluded,
        "untracked_note": untracked_holdings.note(excluded, base, action="rebalance"),
    }


def _allocations(
    by_class: dict[str, float],
    targets: dict[str, float],
    current_total: float,
    target_total: float,
) -> list[dict]:
    """One row per class (union of held + targeted), each with current/target weight + drift.

    ``current_pct`` is over the current book; ``target_value`` is the target weight × the
    POST-contribution ``target_total`` (so the sell_to_target math lands on the targets even
    with new cash); ``drift_pct`` = current_pct − target_pct (pp), the same lens the UI bars use.
    A targeted-but-unheld class shows 0% current with negative drift; a held-but-untargeted class
    shows target 0% (it's drifted-in and would be sold to 0 under a full rebalance)."""
    classes = sorted(set(by_class) | set(targets), key=lambda c: -by_class.get(c, 0.0))
    out: list[dict] = []
    for cls in classes:
        value = by_class.get(cls, 0.0)
        current_pct = (value / current_total * 100.0) if current_total > 0 else 0.0
        target_frac = targets.get(cls, 0.0)
        target_pct = target_frac * 100.0
        target_value = target_frac * target_total
        drift = current_pct - target_pct
        out.append(
            {
                "asset_class": cls,
                "current_value": round(value, 2),
                "current_pct": round(current_pct, 2),
                "target_pct": round(target_pct, 2),
                "target_value": round(target_value, 2),
                "drift_pct": round(drift, 2),
                "off_target": abs(drift) >= DRIFT_FLAG_PP,
            }
        )
    return out


def _sell_to_target_trades(allocations: list[dict], base: str) -> list[dict]:
    """Full rebalance: trade = target_value − current_value per class (buy if +, sell if −)."""
    trades: list[dict] = []
    for a in allocations:
        delta = a["target_value"] - a["current_value"]
        if abs(delta) < MIN_TRADE_BASE:
            continue
        direction = "buy" if delta > 0 else "sell"
        amount = abs(delta)
        verb = "Buy" if direction == "buy" else "Sell"
        trades.append(
            {
                "asset_class": a["asset_class"],
                "direction": direction,
                "amount": round(amount, 2),
                "rationale": (
                    f"{verb} {_money(amount, base)} of {a['asset_class']} to move "
                    f"{a['current_pct']:.0f}% → {a['target_pct']:.0f}% (target)."
                ),
            }
        )
    # Largest moves first (most drift-reducing), so the UI/Lucius surface the headline action.
    trades.sort(key=lambda t: -t["amount"])
    return trades


def _cash_only_trades(allocations: list[dict], new_cash: float, base: str) -> list[dict]:
    """Deploy ``new_cash`` across UNDERWEIGHT classes only (never sell), proportional to each
    one's shortfall (target_value − current_value), so the contribution maximally reduces drift.

    If the total shortfall is <= the cash, every underweight class is topped to target and any
    remainder is left unallocated (the book is at/above target — no further buy reduces drift)."""
    if new_cash < MIN_TRADE_BASE:
        return []
    shortfalls = {
        a["asset_class"]: a["target_value"] - a["current_value"]
        for a in allocations
        if a["target_value"] - a["current_value"] > 0
    }
    total_short = sum(shortfalls.values())
    if total_short <= 0:
        return []

    deployable = min(new_cash, total_short)  # cash beyond what closes the gap can't reduce drift
    trades: list[dict] = []
    for cls, short in sorted(shortfalls.items(), key=lambda kv: -kv[1]):
        amount = deployable * (short / total_short)
        if amount < MIN_TRADE_BASE:
            continue
        trades.append(
            {
                "asset_class": cls,
                "direction": "buy",
                "amount": round(amount, 2),
                "rationale": (
                    f"Invest {_money(amount, base)} of new cash into {cls} "
                    f"(underweight by {_money(short, base)})."
                ),
            }
        )
    return trades


def _money(amount: float, base: str) -> str:
    """Compact base-money label for a rationale string (no currency assumptions baked in)."""
    return f"{base} {amount:,.0f}"
