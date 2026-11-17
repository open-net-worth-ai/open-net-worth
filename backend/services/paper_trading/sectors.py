"""A paper portfolio's holdings by sector — the same rule and the same sector cache as Copy
Trading's "By sector" (``services.portfolio_compare``), so a stock is in one sector on both
pages. Funds, crypto and metals are slices of their own; a currency or an index is its own
slice too. Reads the instrument store and the price service's cache; writes nothing."""

from __future__ import annotations

import threading

from backend.db.instrument_store import InstrumentStore
from backend.services import investments_book, prices
from backend.services.portfolio_compare import sector_label

# A paper kind that is never one company: the slice it goes in.
_KIND_CLASS = {"crypto": "crypto", "metal": "commodity", "fund_nav": "fund"}
_KIND_SLICE = {"fx": "Currencies", "index": "Indices", "manual": "Other"}


def of_holdings(holdings: list[dict]) -> dict[str, str]:
    """``{asset_key: sector}`` for each holding. A stock whose sector is not cached yet is
    looked up in the background, so the next view has it; until then it is "Other"."""
    store = InstrumentStore()
    out: dict[str, str] = {}
    to_find: dict[str, str] = {}  # asset_key -> listing
    for h in holdings:
        key, kind = h["asset_key"], h.get("kind")
        if kind in _KIND_SLICE:
            out[key] = _KIND_SLICE[kind]
            continue
        inst = store.find_by_id(key[5:]) if key.startswith("inst:") else None
        cls = _KIND_CLASS.get(kind or "") or (
            "fund" if inst and investments_book.is_diversified_fund(inst) else "equity"
        )
        if cls != "equity":
            out[key] = sector_label(key, {key: cls}, {})
        elif inst and inst.get("sector"):
            out[key] = str(inst["sector"])
        else:
            listing = str((inst or {}).get("price_source_id") or h.get("symbol") or "")
            if listing:
                to_find[key] = listing
            out[key] = "Other"
    if to_find:
        known = prices.company_sectors(list(to_find.values()), fetch_missing=False)
        for key, listing in to_find.items():
            if known.get(listing):
                out[key] = known[listing]
        unknown = [listing for listing in to_find.values() if not known.get(listing)]
        if unknown:
            threading.Thread(
                target=prices.company_sectors,
                args=(unknown,),
                kwargs={"fetch_missing": True},
                daemon=True,
            ).start()
    return out


def breakdown(holdings: list[dict], by_key: dict[str, str]) -> list[dict]:
    """Each sector's share of what is invested (cash left out, as Copy Trading's is), largest
    first, naming every holding in it."""
    priced = [h for h in holdings if h.get("value_home")]
    invested = sum(h["value_home"] for h in priced)
    if invested <= 0:
        return []
    by: dict[str, float] = {}
    members: dict[str, list[dict]] = {}
    for h in priced:
        sector = by_key.get(h["asset_key"], "Other")
        w = h["value_home"] / invested
        by[sector] = by.get(sector, 0.0) + w
        members.setdefault(sector, []).append({"symbol": h.get("symbol"), "weight": round(w, 6)})
    return [
        {
            "sector": s,
            "weight": round(w, 6),
            "holdings": sorted(members[s], key=lambda m: -m["weight"]),
        }
        for s, w in sorted(by.items(), key=lambda x: -x[1])
    ]
