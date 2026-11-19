"""Owned assets inside a return (``portfolio_returns(band="all_assets")``): the value each day
and the pocket flows, so a purchase is neither gain nor loss and only a revaluation is return.

Every figure comes from ``owned_assets_valuation`` — the newest valuation on or before the day,
the purchase point before the first one, share-adjusted and converted at current rates. Assets
are valued GROSS: a loan against one never enters, and cash is not part of any of it.
"""

from __future__ import annotations

from backend.core.fx import to_base
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.services import owned_assets_valuation as valuation


def valued_assets(rows: list[dict] | None = None) -> list[dict]:
    """The owned assets with at least one point that values them: an asset nothing values has
    no value to move and no flow to neutralize."""
    return [a for a in (OwnedAssetsStore().all() if rows is None else rows) if valuation.series(a)]


def value_on(assets: list[dict], day: str, base: str, rates: dict | None) -> float:
    """What the owned assets are worth on one day, in base currency."""
    return sum(valuation.values_by_band_from_rows(assets, base, as_of=day, rates=rates).values())


def flows(
    assets: list[dict], start: str, end: str, base: str, rates: dict | None
) -> list[tuple[str, float]]:
    """Pocket flows in (start, end]: what each asset cost when it first took a value (its
    purchase, or its first recorded value when it was never bought here) as money out, and
    what a sale brought in as money back."""
    out: list[tuple[str, float]] = []
    for asset in assets:
        first = valuation.series(asset)[-1]
        when = first.get("date") or ""
        if start < when <= end:
            share = (asset.get("share_pct") if asset.get("share_pct") is not None else 100) / 100.0
            cost = to_base(
                float(first["value"]) * share, asset.get("currency") or base, base, rates
            )
            out.append((when, -round(cost, 2)))
        sold = asset.get("disposed_on")
        if sold and start < sold <= end:
            # Booked to a cash account in the asset's own currency (``owned_assets_disposal``).
            proceeds = to_base(
                float(asset.get("disposal_proceeds") or 0),
                asset.get("currency") or base,
                base,
                rates,
            )
            out.append((sold, round(proceeds, 2)))
    return sorted(out)
