"""The paper fill engine. ``tick`` retries every open order once: one batched quote per kind
for the assets that have open orders (fetched before any lock), then each order is decided and
written through the choke. ``daily`` applies dated lifecycle events and writes each active
portfolio's end-of-day value row. Both return at once while the flag is off or there is
nothing to do — no provider is called for an idle book.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.core import data_events
from backend.db.paper_store import PaperOrderStore, PaperPortfolioStore
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.paper_trading import assets, quotes, service

logger = logging.getLogger(__name__)

FLAG = "paper_trading"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return flag_enabled(FLAG)


def tick(now: datetime | None = None) -> dict:
    """Retry every open order. Returns counts by outcome."""
    if not enabled():
        return {"skipped": "flag off"}
    now = now or _now()
    open_orders = PaperOrderStore().open_orders()
    if not open_orders:
        return {"open": 0}
    portfolios = {p["id"]: p for p in PaperPortfolioStore().all()}
    resolved = {}
    for o in open_orders:
        try:
            resolved[o["id"]] = assets.resolve_key(o["asset_key"])
        except Exception:
            logger.info("paper: order %s names an unknown asset", o["id"])
    priced = [a for a in resolved.values() if a.kind not in ("fx", "manual")]
    by_key = {a.key: a for a in priced}
    marks: dict = {}
    got = quotes.fetch(list(by_key.values()), now, marks=marks)
    counts = {"open": len(open_orders), "filled": 0, "expired": 0, "rejected": 0, "waiting": 0}
    for o in open_orders:
        asset = resolved.get(o["id"])
        row = portfolios.get(o.get("portfolio_id"))
        if asset is None or row is None:
            continue
        home = row["home_currency"]
        if asset.kind == "fx":
            fxq = quotes.fx_rate(o.get("from_currency") or home, asset.currency, now)
            quote = None
        else:
            quote = got.get(asset.key)
            fxq = quotes.fx_rate(home, asset.currency, now) if asset.currency != home else None
        try:
            after = service.retry_order(o, quote, fxq, now)
        except Exception:
            logger.exception("paper: retrying order %s failed", o["id"])
            continue
        status = (after or {}).get("status")
        bucket = status if status in ("filled", "expired", "rejected") else "waiting"
        counts[bucket] += 1
    if counts["filled"] or counts["expired"] or counts["rejected"]:
        # An open paper page refreshes on its own when the engine fills in the background.
        data_events.publish(["paper"])
    return counts


def daily(now: datetime | None = None) -> dict:
    """Lifecycle events (dividends, splits, freezes) then the end-of-day value row."""
    if not enabled():
        return {"skipped": "flag off"}
    from backend.services.paper_trading import lifecycle

    now = now or _now()
    done = {"portfolios": 0, "events": 0}
    for row in PaperPortfolioStore().all():
        if row.get("status") != "active":
            continue
        try:
            done["events"] += lifecycle.run(row, now)
            service.snapshot_values(row["id"])
            done["portfolios"] += 1
        except Exception:
            logger.exception("paper: daily run failed for %s", row.get("id"))
    return done
