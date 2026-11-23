"""
classification_service — automated sector/region classification (heuristic-first).

v1 ships a clean seam:
  - _classify_source(inst) → None  (provider not wired; a later wave fills this)
  - _classify_heuristic(inst) — deterministic, no network, the shippable brain
  - classify_instrument(inst) → {sector, region, source, fund_lookthrough}
  - enrich_instrument_classification() → {checked, updated}

Resolution precedence (first hit wins):
  1. manual — inst.constituents non-empty OR classification_source=="manual"
  2. source  — _classify_source (seam, None in v1)
  3. heuristic — asset-class fold + currency→region
  4. ("", "") — unknown / unmapped

Imports only db + core (layering contract).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from backend.db.instrument_store import InstrumentStore

logger = logging.getLogger(__name__)

# ── Region bucket constants ────────────────────────────────────────────────────

# ISO-ish region labels matching the Holdings Region toggle legend.
# Declarative table: quote_currency → region bucket.
_CURRENCY_TO_REGION: dict[str, str] = {
    "USD": "United States",
    "CAD": "Canada",
    "EUR": "Europe",
    "GBP": "Europe",
    "CHF": "Europe",
    "DKK": "Europe",
    "NOK": "Europe",
    "SEK": "Europe",
    "PLN": "Europe",
    "CZK": "Europe",
    "HUF": "Europe",
    "JPY": "Japan",
    "SGD": "Singapore",
    "HKD": "Greater China",
    "CNY": "Greater China",
    "CNH": "Greater China",
    "AUD": "Australia",
    "NZD": "Australia",
    "KRW": "South Korea",
    "TWD": "Taiwan",
    "INR": "India",
    "BRL": "Brazil",
    "MXN": "Mexico",
    "ZAR": "South Africa",
    "IDR": "Southeast Asia",
    "MYR": "Southeast Asia",
    "THB": "Southeast Asia",
    "PHP": "Southeast Asia",
    "VND": "Southeast Asia",
    "BTC": "Global",  # crypto base
    "ETH": "Global",
}

# International-fund name/symbol hints → region bucket, matched as WHOLE WORDS (a bare substring
# read "VanEck Semiconductor" as "Emerging Markets"). First match wins, so ex-US comes before
# WORLD: "FTSE All-World ex-US" holds no US.
_INTL_FUND_HINTS: tuple[tuple[str, str], ...] = (
    ("EX-US", "Global ex-US"),
    ("EX US", "Global ex-US"),
    ("VXUS", "Global ex-US"),
    ("VEU", "Global ex-US"),
    ("BNDX", "Global ex-US"),
    ("INTL", "Global ex-US"),
    ("INTERNATIONAL", "Global ex-US"),
    ("EAFE", "Global ex-US"),  # Europe, Australasia, Far East
    ("DEVELOPED", "Global ex-US"),
    ("EMERGING", "Emerging Markets"),
    ("EM", "Emerging Markets"),
    ("EEM", "Emerging Markets"),
    ("VWO", "Emerging Markets"),
    ("MCHI", "Greater China"),
    ("WORLD", "Global"),
    ("ALL-WORLD", "Global"),
    ("ACWI", "Global"),
    ("VWRA", "Global"),
    ("VWRL", "Global"),
    ("IWDA", "Global"),
    ("SWRD", "Global"),
)
_INTL_FUND_PATTERNS = tuple(
    (re.compile(r"(?<![A-Z0-9])" + re.escape(hint) + r"(?![A-Z0-9])"), region)
    for hint, region in _INTL_FUND_HINTS
)

# Asset subclasses we consider fund-wrappers (ETF/ETC/ETP/index/mutual fund).
# Used for intl-fund region hints and for skipping stock-style Fundamental Analysis.
FUND_SUBCLASSES = frozenset({"etf", "etp", "etc", "index", "mutual_fund"})
_FUND_SUBCLASSES = FUND_SUBCLASSES  # alias for private call sites


def is_fund_wrapper(inst: dict | None) -> bool:
    """True when the instrument is a fund wrapper, not a single-name security."""
    if not inst:
        return False
    return (inst.get("asset_subclass") or "").lower() in FUND_SUBCLASSES


def _region_from_currency(quote_currency: str) -> str:
    """Map a quote currency to a region bucket.

    Declarative table — unknown currencies return "".
    """
    return _CURRENCY_TO_REGION.get((quote_currency or "").upper(), "")


def _detect_intl_fund_region(inst: dict) -> str | None:
    """Check name/symbol for international-fund hints. Returns a region or None."""
    asset_subclass = (inst.get("asset_subclass") or "").lower()
    if asset_subclass not in _FUND_SUBCLASSES:
        return None
    symbol = (inst.get("symbol") or "").upper()
    name = (inst.get("name") or "").upper()
    combined = f"{symbol} {name}"
    for pattern, region in _INTL_FUND_PATTERNS:
        if pattern.search(combined):
            return region
    return None


def _classify_heuristic(inst: dict) -> dict:
    """Deterministic, no-network classification via asset-class fold + currency→region.

    Bond      → sector "Fixed income", region from currency
    Crypto    → sector "Digital assets", region "Global"
    Commodity → sector "Commodities", region "Global"
    Cash      → no sector, no region (not flagged)
    Property  → no sector (illiquid; regional logic unclear without data)
    Equity    → keep cached GICS sector; region from intl-fund hints or currency
    """
    asset_class = (inst.get("asset_class") or "equity").lower()
    quote_currency = (inst.get("quote_currency") or "USD").upper()

    if asset_class == "bond":
        return {
            "sector": "Fixed income",
            "region": _region_from_currency(quote_currency),
            "source": "heuristic",
        }

    if asset_class == "crypto":
        return {"sector": "Digital assets", "region": "Global", "source": "heuristic"}

    if asset_class == "commodity":
        return {"sector": "Commodities", "region": "Global", "source": "heuristic"}

    if asset_class in ("cash", "property"):
        return {"sector": "", "region": "", "source": "heuristic"}

    # Equity (default)
    sector = (inst.get("sector") or "").strip()
    # International-fund detection overrides currency-based region for fund wrappers.
    region = _detect_intl_fund_region(inst) or _region_from_currency(quote_currency)
    return {"sector": sector, "region": region, "source": "heuristic"}


def _classify_source(inst: dict) -> dict | None:
    """Provider data seam. v1 returns None (not wired).

    A later wave wires Yahoo .info / a fund-holdings source here, with SSRF guard.
    Callers must treat None as "fall through to heuristic".
    """
    return None


def classify_instrument(inst: dict) -> dict:
    """Return {sector, region, source} for one instrument.

    Resolution order (first hit wins):
      1. manual — constituents non-empty OR classification_source=="manual"
      2. source  — _classify_source (seam, None in v1)
      3. heuristic — asset-class fold + currency→region
      4. ("", "") — unknown/unmapped

    Never writes. Caller supplies the instrument dict as loaded from the store.
    """
    # 1. Manual is source of truth — never overwrite it.
    constituents = inst.get("constituents") or []
    if constituents or inst.get("classification_source") == "manual":
        return {
            "sector": inst.get("sector") or "",
            "region": inst.get("region") or "",
            "source": "manual",
        }

    # 2. Provider seam (v1 always None).
    source_result = _classify_source(inst)
    if source_result is not None:
        return source_result

    # 3. Heuristic.
    return _classify_heuristic(inst)


def enrich_instrument_classification() -> dict:
    """Idempotent backfill of sector/region onto instruments that lack classification.

    Targets rows where classification_source in ("", "heuristic") and either
    sector or region is blank. Never downgrades "source" or "manual".
    Returns {checked, updated}.
    """
    store = InstrumentStore()
    instruments = store.all()
    checked = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for inst in instruments:
        checked += 1
        source = inst.get("classification_source") or ""

        # Never overwrite "source" or "manual" — one-way ratchet.
        if source in ("source", "manual"):
            continue

        result = _classify_heuristic(inst)
        new_sector = result.get("sector") or ""
        new_region = result.get("region") or ""

        # Skip if there's nothing to fill in.
        current_sector = inst.get("sector") or ""
        current_region = inst.get("region") or ""
        if current_sector and current_region:
            continue
        if not new_sector and not new_region:
            continue

        changes: dict = {"classification_source": "heuristic", "classification_updated_at": now}
        if not current_sector and new_sector:
            changes["sector"] = new_sector
        if not current_region and new_region:
            changes["region"] = new_region

        try:
            store.touch(inst["id"], changes)
            updated += 1
        except Exception:
            logger.exception("classification: failed to update instrument %s", inst.get("id"))

    return {"checked": checked, "updated": updated}
