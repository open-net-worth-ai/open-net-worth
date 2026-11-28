"""
xray_service — ETF look-through ("X-ray") portfolio exposure (P2-INV5).

A book that looks diversified by ticker can be concentrated once you look THROUGH the
ETFs. Each instrument may carry a `constituents` map (its exposure breakdown:
[{weight, sector, region, label}]) supplied by the user / Lucius. This service explodes
each held position's BASE value across its declared constituents and aggregates
portfolio-wide by sector and by region, with an honest "unmapped %".

Semantics:
  - A plain stock/coin (constituents == []) contributes 100% of its value to ITS OWN
    `sector` (and region "" → counted as unmapped on the region axis). It is NOT opaque —
    it is its own exposure.
  - An ETF with constituents spreads its value by weight. Any weight shortfall (Σ < 1)
    is counted as `unmapped` (the part of the fund we can't see through).
  - All value math is in BASE currency, valued the SAME way the rest of the app does
    (qty × price × quote_per_unit, FX-converted) by consuming hydrated rows. No
    revaluation here — we reuse positions_service.hydrated() + core.fx.to_base.

This is DISTINCT from RiskPanel's "sector by holding" (which groups whole positions by the
instrument's single sector); X-ray spreads an ETF across many sectors. Read-only insight.

Imports downward only (db + core) — no api/ai (Contract). Math lives HERE, never the
router/tool/UI.
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.core.fx import base_currency
from backend.db.instrument_store import InstrumentStore
from backend.services.classification import classify_instrument
from backend.services.ledger import row_base_value

# Weights are user-entered approximations; accept a small rounding slack around 1.0.
_WEIGHT_TOLERANCE = 0.02


def get_instrument(instrument_id: str) -> dict | None:
    """Fetch a single instrument by id. Returns None if not found."""
    return InstrumentStore().find_by_id(instrument_id)


def set_instrument_constituents(instrument_id: str, constituents: list) -> dict | None:
    """Store validated constituents on the instrument. Caller must pre-validate."""
    return InstrumentStore().set_constituents(instrument_id, constituents)


def validate_constituents(constituents: list) -> list[dict]:
    """Validate + return a normalized constituents list (the shape stored on the instrument).

    Rules (RFC §4/§7): each item is a dict with a numeric weight ≥ 0 and a non-empty sector
    OR region (a slice with neither bucket is meaningless); weights must each be ≤ 1+ε and sum
    to ≤ 1+ε (a fund can't be >100% of itself; a shortfall is allowed and surfaces as unmapped).
    Raises ValidationError on a bad shape. An empty list is valid (clears the map → opaque/self).
    """
    if constituents in (None, []):
        return []
    if not isinstance(constituents, list):
        raise ValidationError("constituents must be a list")
    out: list[dict] = []
    total = 0.0
    for c in constituents:
        if not isinstance(c, dict):
            raise ValidationError("each constituent must be an object")
        try:
            weight = float(c.get("weight"))
        except (TypeError, ValueError):
            raise ValidationError("constituent weight must be a number")
        if weight < 0:
            raise ValidationError("constituent weight must be ≥ 0")
        if weight > 1 + _WEIGHT_TOLERANCE:
            raise ValidationError("a constituent weight cannot exceed 1 (100%)")
        sector = str(c.get("sector") or "")
        region = str(c.get("region") or "")
        if not sector and not region:
            raise ValidationError("each constituent needs a sector and/or region")
        out.append(
            {
                "weight": weight,
                "sector": sector,
                "region": region,
                "label": str(c.get("label") or ""),
            }
        )
        total += weight
    if total > 1 + _WEIGHT_TOLERANCE:
        raise ValidationError(
            f"constituent weights sum to {round(total, 4)} (> 1); they must total ≤ 1"
        )
    return out


def portfolio_xray(rows: list[dict] | None = None, base: str | None = None) -> dict:
    """Explode the held book across each instrument's look-through constituents and aggregate
    portfolio-wide BY SECTOR and BY REGION, with an honest unmapped %.

    `rows` defaults to positions_service.hydrated() (the canonical valued projection). `base`
    defaults to the profile base currency. Each row is valued in base, then spread:
      - mapped weight w on (sector/region) adds w × value to that bucket;
      - a row with no constituents adds 100% to its instrument `sector` (region: unmapped);
      - any unspread value (constituent shortfall, or a missing sector/region) is `unmapped`.

    Returns {base_currency, total_base, by_sector:[{name,value_base,pct}], by_region:[…],
    unmapped_pct (max of the two axes' unmapped share), mapped_instruments, total_instruments}.
    Empty book → all-zeros, no div-by-zero.
    """
    base = (base or base_currency()).upper()
    if rows is None:
        from backend.services import positions as positions_service

        rows = positions_service.hydrated()

    # Look-through map per instrument (one store read).
    insts = {i["id"]: i for i in InstrumentStore().all()}

    # Flag `fund_lookthrough` — the X-ray's one switch point. Off: the code below, unchanged.
    from backend.services.feature_flags import enabled

    if enabled("fund_lookthrough"):
        from backend.services import xray_lookthrough

        return xray_lookthrough.portfolio_xray(rows, base, insts)

    total_base = 0.0
    by_sector: dict[str, float] = {}
    by_region: dict[str, float] = {}
    sector_unmapped = 0.0
    region_unmapped = 0.0
    # Value seen THROUGH a fund's declared constituents (true look-through), distinct from a plain
    # holding placed by its own sector. Drives an honest "X% of the book looked through" headline so
    # the panel never claims the whole sector column is "mapped" when it's only self-classified.
    looked_through = 0.0
    instrument_ids: set[str] = set()
    mapped_ids: set[str] = set()

    for row in rows:
        # Unvested equity-grant units are not owned yet (ADR
        # 2026-07-31-unvested-equity-excluded-from-net-worth) — excluded from the look-through
        # exposure for the same reason they are excluded from net worth.
        if row.get("vesting_state") == "unvested":
            continue
        value = row_base_value(row, base)
        if value <= 0:
            continue
        iid = row.get("instrument_id") or ""
        instrument_ids.add(iid)
        total_base += value

        inst = insts.get(iid) or {}
        constituents = inst.get("constituents") or []

        if constituents:
            mapped_ids.add(iid)
            looked_through += value
            spread = 0.0
            for c in constituents:
                w = float(c.get("weight") or 0)
                if w <= 0:
                    continue
                slice_value = value * w
                sector = c.get("sector") or ""
                region = c.get("region") or ""
                if sector:
                    by_sector[sector] = by_sector.get(sector, 0.0) + slice_value
                else:
                    sector_unmapped += slice_value
                if region:
                    by_region[region] = by_region.get(region, 0.0) + slice_value
                else:
                    region_unmapped += slice_value
                spread += w
            # Weight shortfall (Σ < 1): the part of the fund we can't see through.
            remainder = value * max(0.0, 1.0 - spread)
            if remainder > 0:
                sector_unmapped += remainder
                region_unmapped += remainder
        else:
            # Plain holding: classify via the service (sector + region).
            # classify_instrument respects manual > source > heuristic; manual
            # constituents already handled above (the `if constituents:` branch).
            classification = classify_instrument(inst)
            sector = classification.get("sector") or ""
            region = classification.get("region") or ""
            if sector:
                by_sector[sector] = by_sector.get(sector, 0.0) + value
            else:
                sector_unmapped += value
            if region:
                by_region[region] = by_region.get(region, 0.0) + value
            else:
                region_unmapped += value

    return {
        "base_currency": base,
        "total_base": round(total_base, 2),
        "by_sector": _bucket_rows(by_sector, total_base),
        "by_region": _bucket_rows(by_region, total_base),
        # Honesty number: the larger unmapped share across the two axes (the worse blind spot).
        "unmapped_pct": round(
            max(
                _pct(sector_unmapped, total_base),
                _pct(region_unmapped, total_base),
            ),
            2,
        ),
        # Per-axis unmapped (value + pct). The panel renders these as an explicit "Unmapped" slice
        # so each column visibly sums to 100% (mapped bars + unmapped) — no contradiction between a
        # "100% unmapped" headline and a fully-drawn column.
        "sector_unmapped_pct": round(_pct(sector_unmapped, total_base), 2),
        "region_unmapped_pct": round(_pct(region_unmapped, total_base), 2),
        "sector_unmapped_base": round(sector_unmapped, 2),
        "region_unmapped_base": round(region_unmapped, 2),
        # Share of the book actually seen THROUGH a fund's constituents (true look-through). 0 when
        # nothing has a constituents map — the panel then says so plainly rather than implying the
        # by-holding sector column is a look-through view.
        "looked_through_pct": round(_pct(looked_through, total_base), 2),
        "mapped_instruments": len(mapped_ids),
        "total_instruments": len(instrument_ids),
    }


def _pct(part: float, total: float) -> float:
    return (part / total * 100.0) if total else 0.0


def _bucket_rows(buckets: dict[str, float], total: float) -> list[dict]:
    """Sort buckets by value desc and attach a % of total. Rounds at the boundary."""
    return [
        {"name": name, "value_base": round(value, 2), "pct": round(_pct(value, total), 2)}
        for name, value in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    ]
