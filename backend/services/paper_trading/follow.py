"""The tickers held on paper, offered to Intel as names only (``core.paper_follow``).

Every active paper portfolio's held instruments — a stock, fund, ETF or coin, never a currency,
an index or an asset the owner prices — deduped, as ``{instrument_id, price_source_id, label,
asset_class}``. Nothing about the holding crosses: not its quantity, value, cost or which
portfolio holds it. Flag-gated: with ``paper_trading`` off, nothing is followed."""

from __future__ import annotations

from backend.core import paper_follow
from backend.db.instrument_store import InstrumentStore
from backend.db.paper_store import PaperPortfolioStore
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.paper_trading import assets, service

# A cost guard: each followed ticker is one more feed poll every 30 minutes.
MAX_FOLLOWED = 40
_KINDS = ("listed", "fund_nav", "crypto")


def held_tickers() -> list[dict]:
    if not flag_enabled("paper_trading"):
        return []
    keys: set[str] = set()
    for row in PaperPortfolioStore().all():
        if row.get("status") == "active":
            keys.update(k for k in service.derive(row["id"]).held_keys() if k.startswith("inst:"))
    store = InstrumentStore()
    out: list[dict] = []
    for key in sorted(keys):
        inst = store.find_by_id(key[5:])
        if not inst or assets.kind_of_instrument(inst) not in _KINDS:
            continue
        out.append(
            {
                "instrument_id": inst["id"],
                "price_source_id": inst.get("price_source_id") or inst.get("symbol") or "",
                "label": inst.get("name") or inst.get("symbol") or "",
                "asset_class": inst.get("asset_class") or "equity",
            }
        )
    return out[:MAX_FOLLOWED]


def register() -> None:
    """Offer the paper tickers to Intel. Called once at boot by the scheduler."""
    paper_follow.register_provider(held_tickers)
