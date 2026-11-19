"""The thesis's view of a paper trade, judged at trade time by the ONE thesis authority
(``services.thesis_judgment``) and stored on the fill, so "did my buy-zone trades work?" is
answered from what was true then — never re-derived from later prices. ``held`` is the PAPER
portfolio's holding, passed in explicitly: a paper trade never reads or changes the real
thesis's held state."""

from __future__ import annotations

import logging

from backend.services import investment_theses, thesis_judgment
from backend.services.paper_trading.book import Book
from backend.services.paper_trading.model import Fill, PaperAsset

logger = logging.getLogger(__name__)

STANCE_LABEL = {
    "in_buy_zone": "In your buy zone",
    "below_buy_zone": "Below your buy zone",
    "above_buy_zone": "Above your buy zone",
    "below_stop": "Below your stop",
    "above_trim": "Above your trim level",
    "near_trim": "Near your trim level",
    "above_fair_value": "Above your fair value",
    "near_fair_value": "Near your fair value",
    "intact": "Thesis intact",
    "unpriced": "No price to judge against",
}


def stance(asset: PaperAsset, plan, book: Book) -> dict | None:
    if not asset.key.startswith("inst:"):
        return None
    price = plan.decision.price if isinstance(plan.decision, Fill) else None
    if price is None and plan.quantity and plan.gross:
        price = plan.gross / plan.quantity
    try:
        found = investment_theses.thesis_for(asset.key.split(":", 1)[1])
        j = thesis_judgment.judge(
            found,
            price,
            held=book.quantity(asset.key) > 0,
            currency=asset.currency,
            ticker=asset.extra.get("display_symbol") or asset.symbol,
        )
    except Exception:  # a thesis read must never block a paper trade
        logger.info("paper: thesis judgment unavailable", exc_info=True)
        return None
    if not j:
        return None
    return {"stance": j["stance"], "label": STANCE_LABEL.get(j["stance"], j["stance"])}
