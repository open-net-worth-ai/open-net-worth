"""Autopilot proposals: the review proposes, code sizes and places.

A review records proposals with ``propose_paper_orders`` — buy (a new name, or more of one it
holds), trim or sell, each with a reason and, for a buy or trim, a conviction. After the review's
turn, ``execute`` places them: sells and trims first (a broken limit is fixed before anything is
bought), then buys from the most convinced down, each sized by ``autopilot_sizing`` and placed
through the normal paper choke with origin ``autopilot`` — so the mandate still refuses anything
that breaks it. Every proposal comes back with what happened to it: placed, skipped (nothing to
do, with why) or refused (with the choke's reason). One failing never stops the rest. ADR
``2026-11-21-autopilot-proposes-the-app-sizes``."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from backend.core.errors import OpenNetWorthError, ValidationError
from backend.services.paper_trading import (
    assets,
    autopilot_sizing,
    orders,
    quotes,
    service,
    valuation,
)
from backend.services.paper_trading.facade import place

logger = logging.getLogger(__name__)

ACTIONS = ("buy", "trim", "sell")
MAX_PROPOSALS = 10
MAX_WHY = 280
MAX_CASE = 600
_ORDER = {"sell": 0, "trim": 1, "buy": 2}
_SURE = {"high": 0, "medium": 1, "low": 2}


def clean(proposals: list | None) -> list[dict]:
    """The proposals as stored on the running review, or a ValidationError naming the fix."""
    items = list(proposals or [])
    if len(items) > MAX_PROPOSALS:
        raise ValidationError(f"Propose at most {MAX_PROPOSALS} orders a review.")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        p = raw or {}
        asset = str(p.get("asset") or "").strip()
        action = str(p.get("action") or "").strip().lower()
        conviction = str(p.get("conviction") or "").strip().lower() or None
        why = str(p.get("why") or "").strip()
        case = str(p.get("case") or "").strip() or None
        if not asset:
            raise ValidationError("Each proposal names its asset: a ticker or an asset_key.")
        if action not in ACTIONS:
            raise ValidationError(f"{asset}: the action is buy, trim or sell.")
        if action != "sell" and conviction not in autopilot_sizing.CONVICTIONS:
            raise ValidationError(f"{asset}: a {action} needs a conviction: low, medium or high.")
        if not why:
            raise ValidationError(f"{asset}: say why, in one sentence.")
        if len(why) > MAX_WHY or (case and len(case) > MAX_CASE):
            raise ValidationError(
                f"{asset}: keep why under {MAX_WHY} and the case under {MAX_CASE}."
            )
        if asset.upper() in seen:
            raise ValidationError(f"{asset} is proposed twice: one proposal per asset.")
        seen.add(asset.upper())
        out.append(
            {
                "asset": asset,
                "action": action,
                "conviction": conviction if action != "sell" else None,
                "why": why,
                "case": case,
            }
        )
    return out


def execute(pid: str, proposals: list[dict], now: datetime | None = None) -> list[dict]:
    """Place the review's proposals and return each with what happened to it. Never raises."""
    now = now or datetime.now(timezone.utc)
    ordered = sorted(
        proposals, key=lambda p: (_ORDER[p["action"]], _SURE.get(p.get("conviction") or "", 3))
    )
    state = _State(pid, now)
    out = []
    for p in ordered:
        try:
            out.append(_one(state, p))
        except OpenNetWorthError as exc:
            out.append(_outcome(p, "refused", str(exc)))
        except Exception:  # noqa: BLE001 — one proposal failing never stops the rest
            logger.exception("autopilot proposal failed for %s: %s", pid, p.get("asset"))
            out.append(_outcome(p, "refused", "It could not be placed: an unexpected error."))
    return out


class _State:
    """The portfolio as this run of proposals sees it: re-valued once sells and trims are done,
    then kept current by hand as buys are placed (a buy that waits for the open still holds its
    cash, which a fresh valuation would not show)."""

    def __init__(self, pid: str, now: datetime):
        self.pid = pid
        self.now = now
        self.row = service.portfolio(pid)
        self.mandate = self.row.get("mandate") or {}
        self.home = self.row["home_currency"]
        self.val: dict | None = None

    def valuation(self) -> dict:
        """The live valuation, with cash already promised to open orders (a buy from an earlier
        review still waiting for the open) taken out: a buy is sized against FREE cash."""
        if self.val is None:
            book = service.derive(self.pid)
            val = valuation.value(self.row, book, self.now)
            open_orders = [o for o in service.portfolio_orders(self.pid) if o["status"] == "open"]
            cash_reserved, _ = orders.reserved(open_orders)
            rates = val.get("rates") or {}
            promised = sum(
                amount * (1.0 if ccy == self.home else float(rates.get(ccy) or 0.0))
                for ccy, amount in cash_reserved.items()
            )
            val["cash_home"] = float(val["cash_home"] or 0.0) - promised
            self.val = val
        return self.val

    def holding(self, key: str) -> dict | None:
        return next(
            (h for h in self.valuation()["holdings"] if h["asset_key"] == key and h["quantity"]),
            None,
        )

    def spent(self, key: str, amount_home: float, new: bool) -> None:
        val = self.valuation()
        val["cash_home"] = float(val["cash_home"]) - amount_home
        h = self.holding(key)
        if h is not None:
            h["value_home"] = float(h["value_home"] or 0) + amount_home
        elif new:
            val["holdings"].append({"asset_key": key, "quantity": 1, "value_home": amount_home})


def _one(state: _State, p: dict) -> dict:
    asset = _asset(p["asset"])
    if asset.kind in ("fx", "index"):
        return _outcome(p, "skipped", f"{asset.symbol} isn't tradeable this way.", asset)
    if p["action"] in ("sell", "trim"):
        result = _sell(state, p, asset)
        state.val = None  # sells change cash: re-value before the first buy
        return result
    return _buy(state, p, asset)


def _sell(state: _State, p: dict, asset) -> dict:
    held = service.derive(state.pid).quantity(asset.key)
    if held <= 0:
        return _outcome(p, "skipped", f"{asset.symbol} isn't held.", asset)
    qty, detail = held, "sell all"
    if p["action"] == "trim":
        h = state.holding(asset.key)
        total = float(state.valuation()["value_home"] or 0)
        if not h or not h.get("value_home"):
            return _outcome(p, "skipped", f"{asset.symbol} has no price to trim against.", asset)
        amount, detail = autopilot_sizing.trim_amount(
            conviction=p["conviction"],
            total=total,
            current=float(h["value_home"]),
            cap_pct=state.mandate.get("max_position_pct"),
        )
        if amount <= 0:
            return _outcome(p, "skipped", detail, asset)
        share = amount / float(h["value_home"])
        # Rounded UP: a trim must end at or under its target (a holding over its cap, under it).
        raw = held * share
        qty = float(math.ceil(raw)) if asset.kind == "listed" else math.ceil(raw * 1e6) / 1e6
        qty = min(qty, held)
        if qty <= 0:
            return _outcome(p, "skipped", "less than one share to sell", asset)
    return _place(state, p, asset, "sell", qty, detail)


def _buy(state: _State, p: dict, asset) -> dict:
    val = state.valuation()
    held = state.holding(asset.key)
    most = state.mandate.get("max_positions")
    count = len([h for h in val["holdings"] if h.get("quantity")])
    if held is None and most is not None and count >= most:
        return _outcome(p, "skipped", f"already at the mandate's {most} holdings", asset)
    amount, detail = autopilot_sizing.buy_amount(
        conviction=p["conviction"],
        total=float(val["value_home"] or 0),
        current=float((held or {}).get("value_home") or 0),
        cash=float(val["cash_home"] or 0),
        floor_pct=state.mandate.get("min_cash_pct"),
        cap_pct=state.mandate.get("max_position_pct"),
    )
    if amount <= 0:
        return _outcome(p, "skipped", detail, asset)
    price, rate = _price_and_rate(state, asset)
    if price is None or rate is None:
        return _outcome(p, "skipped", f"no price for {asset.symbol} right now", asset)
    qty = autopilot_sizing.units(amount / rate, price, fractional=asset.kind != "listed")
    if qty <= 0:
        return _outcome(p, "skipped", f"{detail}: less than one share", asset)
    out = _place(state, p, asset, "buy", qty, detail)
    if out["status"] == "placed":
        state.spent(asset.key, qty * price * rate, new=held is None)
    return out


def _place(state: _State, p: dict, asset, side: str, qty: float, detail: str) -> dict:
    body = {"asset_key": asset.key, "side": side, "quantity": qty, "note": p["why"][:MAX_WHY]}
    out = place(state.pid, body, origin="autopilot")
    status = (out.get("order") or {}).get("status")
    if status == "rejected":
        reason = (out.get("order") or {}).get("state_reason") or "the order was rejected"
        return _outcome(p, "refused", reason, asset)
    return _outcome(p, "placed", detail, asset, order=out.get("toast"))


def _asset(ref: str):
    """A known instrument first (by its quote id, then a symbol only one carries), so a ticker
    the app already has never mints a duplicate; only an unseen one goes to the front door."""
    if ref.split(":", 1)[0] in ("inst", "ccy", "ctr"):
        return assets.resolve_key(ref)
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.find_by_price_source_id(ref)
    if inst is None:
        matches = store.find_all_by_symbol(ref)
        inst = matches[0] if len(matches) == 1 else None
    if inst is not None:
        return assets.from_instrument(inst)
    return assets.resolve_key(assets.resolve_request({"symbol": ref}))


def _price_and_rate(state: _State, asset) -> tuple[float | None, float | None]:
    quote = quotes.fetch([asset], state.now).get(asset.key)
    price = float(quote.last) if quote and quote.last else None
    if asset.currency.upper() == state.home.upper():
        return price, 1.0
    rate = quotes.fx_rates({asset.currency}, state.home).get(asset.currency)
    return price, rate


def _outcome(p: dict, status: str, detail: str, asset=None, order: str | None = None) -> dict:
    return {
        "asset": p["asset"],
        "symbol": getattr(asset, "symbol", None) or p["asset"],
        "action": p["action"],
        "conviction": p.get("conviction"),
        "why": p["why"],
        "case": p.get("case"),
        "status": status,
        "detail": detail,
        "order": order if isinstance(order, str) else None,
    }
