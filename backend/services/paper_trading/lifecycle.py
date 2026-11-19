"""What happens to a paper holding over time: dividends and fund distributions paid in cash,
splits applied on their effective date, and — when the data does not add up — a FREEZE, never a
guess. Each draft carries an ``idem_key`` (``div:inst_x:2026-12-15``), so the daily job can run
any number of times and still write each event once.

Freeze-not-guess: a move of more than 35% between two sessions that matches a common split
ratio, with no split reported for it, freezes the holding and says why; so does a holding with
no price for 5 sessions (a delisting). Any other corporate action (a spin-off, a merger) is
settled by hand. Withholding tax is not modelled: income is paid gross.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from backend.services import prices
from backend.services.paper_trading import assets, service
from backend.services.paper_trading import book as book_mod
from backend.services.paper_trading.model import PaperAsset, settings_of

logger = logging.getLogger(__name__)

GAP_THRESHOLD = 0.35
_COMMON_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 1.5)
_RATIO_TOLERANCE = 0.06
STALE_SESSIONS = 5
# Kinds whose holdings have dated events in phase 1. Crypto, metals, currencies, indices and
# your-priced assets have none (a price index pays no dividends; staking is not modelled).
HAS_LIFECYCLE = ("listed", "fund_nav")


def _held_on(events: list[dict], asset_key: str, day: str) -> float:
    """Quantity held at the START of ``day`` (events dated before it)."""
    before = [e for e in events if str(e.get("at") or "")[:10] < day]
    return book_mod.derive(before).quantity(asset_key)


def _looks_like_split(prev: float, nxt: float) -> float | None:
    if prev <= 0 or nxt <= 0:
        return None
    move = nxt / prev
    if abs(move - 1.0) <= GAP_THRESHOLD:
        return None
    for r in _COMMON_RATIOS:
        for cand in (1.0 / r, r):
            if abs(move - cand) / cand <= _RATIO_TOLERANCE:
                return 1.0 / cand
    return None


def drafts_for(
    asset: PaperAsset,
    actions: dict,
    events: list[dict],
    *,
    start: str,
    today: str,
    distributions: str = "cash",
) -> list[dict]:
    """PURE: the lifecycle events one holding is due, from its corporate actions and closes."""
    key, ccy = asset.key, asset.currency
    ident = key.split(":", 1)[1]
    out: list[dict] = []
    split_days = {s["date"] for s in actions.get("splits") or []}
    # Each split this run emits is folded into the working ledger before a later dividend is
    # sized: a run that catches up over a split pays the dividend on the post-split quantity.
    working = list(events)
    for s in sorted(actions.get("splits") or [], key=lambda x: x["date"]):
        d = s["date"]
        if not (start < d <= today) or _held_on(working, key, d) <= 0:
            continue
        draft = {
            "id": f"draft-split-{d}",
            "kind": "split",
            "at": f"{d}T00:00:00+00:00",
            "asset_key": key,
            "payload": {
                "ratio": s["ratio"],
                "label": s.get("label") or f"{s['ratio']:g}:1",
                "before": d,
            },
            "idem_key": f"split:{ident}:{d}",
        }
        out.append({k: v for k, v in draft.items() if k != "id"})
        working = sorted(working + [draft], key=lambda e: str(e.get("at") or ""))
    kind = "distribution" if asset.kind == "fund_nav" else "dividend"
    closes = {c["date"]: c["close"] for c in actions.get("closes") or []}
    for dv in actions.get("dividends") or []:
        d = dv["date"]
        if not (start < d <= today):
            continue
        qty = _held_on(working, key, d)
        if qty <= 0:
            continue
        amount = round(qty * float(dv["amount"]), 8)
        out.append(
            {
                "kind": kind,
                "at": f"{d}T00:00:00+00:00",
                "asset_key": key,
                "cash_delta": {ccy: amount},
                "price": float(dv["amount"]),
                "payload": {"per_unit": float(dv["amount"]), "quantity": qty, "gross": True},
                "idem_key": f"div:{ident}:{d}",
            }
        )
        nav = closes.get(d)
        if kind == "distribution" and distributions == "reinvest" and nav:
            # Reinvested at that day's NAV: the cash just paid buys more units.
            out.append(
                {
                    "kind": "buy",
                    "at": f"{d}T00:00:01+00:00",
                    "asset_key": key,
                    "quantity_delta": round(amount / nav, 6),
                    "cash_delta": {ccy: -amount},
                    "price": nav,
                    "price_basis": "nav",
                    "payload": {"currency": ccy, "commission": 0.0, "reinvested": True},
                    "idem_key": f"drip:{ident}:{d}",
                }
            )
    out += _freezes(asset, actions.get("closes") or [], events, split_days, start, today)
    return out


def _freezes(asset, closes, events, split_days, start, today) -> list[dict]:
    key = asset.key
    ident = key.split(":", 1)[1]
    out: list[dict] = []
    for prev, nxt in zip(closes, closes[1:]):
        if nxt["date"] <= start or nxt["date"] > today or nxt["date"] in split_days:
            continue
        ratio = _looks_like_split(prev["close"], nxt["close"])
        if ratio and _held_on(events, key, nxt["date"]) > 0:
            out.append(
                _freeze(
                    key,
                    nxt["date"],
                    f"The price moved {nxt['close'] / prev['close'] - 1:+.0%} on {nxt['date']}, like a "
                    f"{ratio:g}:1 split, but no split was reported. Check it and settle the holding.",
                    f"freeze:gap:{ident}:{nxt['date']}",
                )
            )
    if closes:
        last = closes[-1]["date"]
        gap = sum(1 for d in _weekdays(last, today)) - 1
        if gap >= STALE_SESSIONS:
            out.append(
                _freeze(
                    key,
                    today,
                    f"No price since {last}. It may have been delisted; settle it by hand.",
                    f"freeze:stale:{ident}:{last}",
                )
            )
    return out


def _weekdays(a: str, b: str) -> list[str]:
    from backend.services.paper_trading.performance import trading_days

    return trading_days(date.fromisoformat(a), date.fromisoformat(b))


def _freeze(key: str, day: str, reason: str, idem: str) -> dict:
    return {
        "kind": "freeze",
        "at": f"{day}T00:00:00+00:00",
        "asset_key": key,
        "payload": {"frozen": True, "reason": reason},
        "idem_key": idem,
    }


def run(row: dict, now: datetime) -> int:
    """Fetch actions for every held listed asset or fund, then write what is due through the
    choke. Returns how many events were written."""
    from backend.core.user_time import user_today

    evs = service.events(row["id"])
    bk = book_mod.derive(evs)
    start = str(row.get("started_at") or "")[:10]
    today = user_today().isoformat()
    settings = settings_of(row)
    drafts: list[dict] = []
    for key in bk.held_keys():
        if not key.startswith("inst:") or key in bk.frozen:
            continue
        asset = assets.resolve_key(key)
        if asset.kind not in HAS_LIFECYCLE:
            continue
        actions = prices.fetch_corporate_actions(asset.symbol)
        drafts += drafts_for(
            asset,
            actions,
            evs,
            start=start,
            today=today,
            distributions=settings.get("fund_distributions", "cash"),
        )
    if not drafts:
        return 0
    drafts.sort(key=lambda d: d["at"])
    result = service.apply_paper_change(
        {"op": "lifecycle", "portfolio_id": row["id"], "events": drafts}
    )
    return len(result["written"])
