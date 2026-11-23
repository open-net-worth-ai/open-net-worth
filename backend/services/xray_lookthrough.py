"""The X-ray with funds looked through — `xray.portfolio_xray` when flag `fund_lookthrough` is on.

ADR 2026-11-23-a-fund-is-looked-through-by-what-the-price-source-says-it-holds. Same output
shape as the old X-ray, plus per-axis coverage, per-bucket instrument ids and the sector verdict.
Each holding is spread on EACH axis by the first of:

  1. its saved look-through (`constituents`) when any slice names that axis — a person's word;
  2. its own classification when a person set it (`classification_source == "manual"`);
  3. the fetched look-through (`instrument.lookthrough`, sectors only);
  4. its heuristic classification;
  5. Unmapped.

Every sector name goes through `sector_names.canonical_sector`, so a fund's "consumer_cyclical"
and a stock's "Consumer Cyclical" are one bucket.
"""

from __future__ import annotations

from backend.services import fund_lookthrough
from backend.services.classification import classify_instrument
from backend.services.ledger import row_base_value
from backend.services.sector_names import canonical_sector

# A single equity sector at or above this share of the book is flagged on the Sector view —
# the rule the browser used to apply to Technology alone.
SECTOR_FLAG_PCT = 30.0
_NON_EQUITY = frozenset({"Fixed income", "Digital assets", "Commodities", "Cash"})

LOOKED_THROUGH = "looked_through"
CLASSIFIED = "classified"
UNMAPPED = "unmapped"


class _Axis:
    """One axis' running totals: value per bucket, the ids behind it, and how it was placed."""

    def __init__(self) -> None:
        self.buckets: dict[str, float] = {}
        self.ids: dict[str, set[str]] = {}
        self.how = {LOOKED_THROUGH: 0.0, CLASSIFIED: 0.0, UNMAPPED: 0.0}

    def add(self, name: str, value: float, iid: str, how: str) -> None:
        if not name:
            self.how[UNMAPPED] += value
            return
        self.buckets[name] = self.buckets.get(name, 0.0) + value
        if iid:
            self.ids.setdefault(name, set()).add(iid)
        self.how[how] += value

    def spread(self, slices: list[tuple[str, float]], value: float, iid: str) -> None:
        placed = 0.0
        for name, weight in slices:
            if weight > 0:
                self.add(name, value * weight, iid, LOOKED_THROUGH)  # no name → Unmapped
                placed += weight
        remainder = value * max(0.0, 1.0 - placed)
        if remainder > 0:
            self.how[UNMAPPED] += remainder

    def rows(self, total: float) -> list[dict]:
        return [
            {"name": name, "value_base": round(v, 2), "pct": round(_pct(v, total), 2)}
            for name, v in sorted(self.buckets.items(), key=lambda kv: kv[1], reverse=True)
        ]

    def coverage(self, total: float) -> dict:
        return {k: round(_pct(v, total), 2) for k, v in self.how.items()}


def _pct(part: float, total: float) -> float:
    return (part / total * 100.0) if total else 0.0


def _saved_slices(inst: dict, key: str) -> list[tuple[str, float]] | None:
    """A person's look-through on one axis, or None when no slice names that axis."""
    cons = inst.get("constituents") or []
    if not any((c.get(key) or "").strip() for c in cons):
        return None
    norm = canonical_sector if key == "sector" else (lambda s: (s or "").strip())
    return [(norm(c.get(key)), float(c.get("weight") or 0)) for c in cons]


def _own(inst: dict) -> dict:
    """The instrument's own classification. A saved look-through that names only the OTHER
    axis must not make this one "manual" (classify_instrument treats any constituents as
    manual), so it is classified as if it had none."""
    return classify_instrument({**inst, "constituents": []})


def _place_sector(axis: _Axis, inst: dict, value: float, iid: str) -> None:
    saved = _saved_slices(inst, "sector")
    if saved is not None:
        return axis.spread(saved, value, iid)
    if inst.get("classification_source") == "manual":
        return axis.add(canonical_sector(inst.get("sector")), value, iid, CLASSIFIED)
    fetched = fund_lookthrough.sectors_of(inst)
    if fetched:
        return axis.spread([(s["name"], float(s["weight"])) for s in fetched], value, iid)
    axis.add(canonical_sector(_own(inst).get("sector")), value, iid, CLASSIFIED)


def _place_region(axis: _Axis, inst: dict, value: float, iid: str) -> None:
    saved = _saved_slices(inst, "region")
    if saved is not None:
        return axis.spread(saved, value, iid)
    if inst.get("classification_source") == "manual":
        return axis.add((inst.get("region") or "").strip(), value, iid, CLASSIFIED)
    axis.add((_own(inst).get("region") or "").strip(), value, iid, CLASSIFIED)


def _sector_flag(rows: list[dict]) -> dict | None:
    top = next((r for r in rows if r["name"] not in _NON_EQUITY), None)
    if top and top["pct"] >= SECTOR_FLAG_PCT:
        return {"name": top["name"], "pct": top["pct"], "threshold_pct": SECTOR_FLAG_PCT}
    return None


def equity_sector_groups(
    projected: list[dict], insts: dict[str, dict]
) -> dict[str, tuple[float, list[str]]]:
    """Projected rows (`instrument_id`, `base_value`) spread by sector exactly as the X-ray
    spreads them, equity sectors only (bonds, cash and the rest are the asset-class check's) —
    sector name → (base value, instrument ids). The risk sector finding reads this, so "how much
    technology" has one answer."""
    axis = _Axis()
    for p in projected:
        iid = p.get("instrument_id") or ""
        value = float(p.get("base_value") or 0)
        if value > 0:
            _place_sector(axis, insts.get(iid) or {}, value, iid)
    return {
        name: (value, sorted(axis.ids.get(name, ())))
        for name, value in axis.buckets.items()
        if name not in _NON_EQUITY
    }


def portfolio_xray(rows: list[dict], base: str, insts: dict[str, dict]) -> dict:
    """The looked-through X-ray of `rows` (hydrated positions) in `base`."""
    sectors, regions = _Axis(), _Axis()
    total = 0.0
    seen: set[str] = set()
    looked: set[str] = set()
    for row in rows:
        if row.get("vesting_state") == "unvested":
            continue
        value = row_base_value(row, base)
        if value <= 0:
            continue
        iid = row.get("instrument_id") or ""
        inst = insts.get(iid) or {}
        seen.add(iid)
        total += value
        if inst.get("constituents") or fund_lookthrough.sectors_of(inst):
            looked.add(iid)
        _place_sector(sectors, inst, value, iid)
        _place_region(regions, inst, value, iid)
    by_sector, by_region = sectors.rows(total), regions.rows(total)
    sector_cov, region_cov = sectors.coverage(total), regions.coverage(total)
    return {
        "base_currency": base,
        "total_base": round(total, 2),
        "by_sector": by_sector,
        "by_region": by_region,
        "unmapped_pct": max(sector_cov[UNMAPPED], region_cov[UNMAPPED]),
        "sector_unmapped_pct": sector_cov[UNMAPPED],
        "region_unmapped_pct": region_cov[UNMAPPED],
        "sector_unmapped_base": round(sectors.how[UNMAPPED], 2),
        "region_unmapped_base": round(regions.how[UNMAPPED], 2),
        "looked_through_pct": sector_cov[LOOKED_THROUGH],
        "sector_coverage": sector_cov,
        "region_coverage": region_cov,
        "sector_flag": _sector_flag(by_sector),
        "mapped_instruments": len(looked),
        "total_instruments": len(seen),
    }
