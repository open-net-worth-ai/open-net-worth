"""Holding plans — why you own (or would buy) an instrument, and the prices that matter to you.

``apply_plan_change`` is the ONE writer (REST, Lucius tools and the watchlist ``intent``
compatibility path all go through it); the rules themselves live in
``db.holding_plan_store.validate_plan``. A plan is keyed by instrument and outlives positions and
watchlist membership — ADR ``2026-10-27-a-holding-plan-lives-on-the-instrument``.

The watch rules still speak "intent" (thesis, buy zone, fair value): ``intent_for_item`` is that
view of a plan, with the pre-migration ``item["intent"]`` as the fallback for a watched ticker the
migration could not match to an instrument. The first plan written for that instrument absorbs
that leftover intent and clears it from the item, so the two never diverge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.db.engine import locked_unit_of_work
from backend.db.holding_plan_store import PLAN_FIELDS, HoldingPlanStore, validate_plan
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.tables import holding_plans
from backend.db.watchlist_store import WatchlistStore

INTENT_FIELDS = ("thesis", "buy_below", "buy_above", "fair_value")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merged(existing: dict | None, fields: dict, clear: list) -> dict:
    """The plan ``fields``/``clear`` would leave, validated as a whole (raises on a bad plan)."""
    unknown = (set(fields) | set(clear)) - set(PLAN_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown plan field(s): {', '.join(sorted(unknown))}.")
    merged = {k: (existing or {}).get(k) for k in PLAN_FIELDS}
    merged.update(fields)
    for key in clear:
        merged[key] = None
    return validate_plan(merged)


def _leftover_intent(instrument_id: str) -> tuple[dict, list[str]]:
    """Pre-plan ``intent`` still sitting on watch items for this instrument (the migration could
    not match them), as plan fields, plus those items' ids so the caller can clear them."""
    found: dict = {}
    ids: list[str] = []
    for item in WatchlistStore().query_items(type="ticker"):
        intent = item.get("intent")
        if not isinstance(intent, dict) or not intent or _item_instrument_id(item) != instrument_id:
            continue
        ids.append(item["id"])
        if not found:
            found = {k: intent[k] for k in INTENT_FIELDS if intent.get(k) not in (None, "")}
    return found, ids


def apply_plan_change(change: dict) -> dict:
    """``{"op": "set", "instrument_id", "fields": {...}, "clear": [...]}`` merges ``fields`` over
    the existing plan and blanks every name in ``clear``; a result with nothing set deletes the
    plan. ``{"op": "delete", "instrument_id"}`` deletes it. Returns ``{"plan": dict | None}``."""
    op = change.get("op")
    instrument_id = (change.get("instrument_id") or "").strip()
    if not instrument_id:
        raise ValidationError("An instrument_id is required.")
    if op not in ("set", "delete"):
        raise ValidationError(f"Unknown plan change '{op}'.")
    if InstrumentStore().find_by_id(instrument_id) is None:
        raise NotFoundError(f"No instrument '{instrument_id}'.")
    store = HoldingPlanStore()
    absorbed: list[str] = []
    with locked_unit_of_work(holding_plans) as conn:
        existing = store.find_by_instrument(instrument_id, conn=conn)
        if op == "delete":
            if existing:
                store.delete(existing["id"], conn=conn)
            return {"plan": None}
        base = existing
        if existing is None:
            leftover, absorbed = _leftover_intent(instrument_id)
            base = leftover or None
        valid = _merged(base, change.get("fields") or {}, change.get("clear") or [])
        now = _now()
        if not valid:
            if existing:
                store.delete(existing["id"], conn=conn)
            written = None
        elif existing:
            row = {**{k: None for k in PLAN_FIELDS}, **valid, "updated_at": now}
            written = store.update_by_id(existing["id"], row, conn=conn)
        else:
            row = {
                "id": new_id("hp"),
                "instrument_id": instrument_id,
                **{k: None for k in PLAN_FIELDS},
                **valid,
                "created_at": now,
                "updated_at": now,
            }
            written = store.insert(row, conn=conn)
    watch = WatchlistStore()
    for item_id in absorbed:
        watch.update_by_id(item_id, {"intent": None})
    return {"plan": written}


def check_watch_intent(instrument_id: str, intent: dict | None) -> None:
    """Raise exactly what ``set_from_watch_intent`` would, without writing anything."""
    fields, clear = _intent_change(intent)
    try:
        _merged(plan_for(instrument_id), fields, clear)
    except ValidationError as exc:
        raise ValidationError(f"intent: {exc}") from exc


def held_instrument_ids() -> set[str]:
    return {
        p.get("instrument_id")
        for p in PositionStore().all()
        if p.get("instrument_id") and float(p.get("quantity") or 0) > 0
    }


def plan_for(instrument_id: str) -> dict | None:
    return HoldingPlanStore().find_by_instrument(instrument_id)


def plans_by_instrument() -> dict[str, dict]:
    return {p["instrument_id"]: p for p in HoldingPlanStore().all() if p.get("instrument_id")}


def list_plans(tickers: list[str] | None = None) -> list[dict]:
    """Every plan with its instrument's ticker/name and whether it is held, optionally narrowed
    to ``tickers`` (case-insensitive, matched on price_source_id or symbol)."""
    instruments = {i["id"]: i for i in InstrumentStore().all()}
    held = held_instrument_ids()
    wanted = {t.strip().upper() for t in tickers or [] if t and t.strip()}
    out = []
    for plan in HoldingPlanStore().all():
        inst = instruments.get(plan.get("instrument_id")) or {}
        ticker = (inst.get("price_source_id") or inst.get("symbol") or "").upper()
        if wanted and ticker not in wanted and (inst.get("symbol") or "").upper() not in wanted:
            continue
        out.append(
            {
                **plan,
                "ticker": ticker,
                "name": inst.get("name") or "",
                "currency": inst.get("quote_currency") or "",
                "held": plan.get("instrument_id") in held,
            }
        )
    return out


def _local_instrument(ticker: str) -> dict | None:
    """No-network lookup, the same first two rungs as ``instruments.resolve_by_ticker``: the
    exact price_source_id, else the one instrument carrying this bare symbol (held crypto rows
    carry "BTC" while the instrument is priced as "bitcoin")."""
    store = InstrumentStore()
    found = store.find_by_price_source_id(ticker)
    if found:
        return found
    same_symbol = store.find_all_by_symbol(ticker)
    return same_symbol[0] if len(same_symbol) == 1 else None


def instrument_for_ticker(ticker: str, *, resolve: bool = False) -> dict | None:
    """The instrument a ticker names. ``resolve=True`` falls through to the find-or-create
    ladder (may hit the network) — writes do that, reads never do."""
    ticker = (ticker or "").strip()
    if not ticker:
        return None
    found = _local_instrument(ticker)
    if found or not resolve:
        return found
    from backend.services.instruments import resolve_by_ticker

    inst, _created = resolve_by_ticker(ticker)
    return inst


def _item_instrument_id(item: dict) -> str | None:
    iid = item.get("instrument_id")
    if iid:
        return iid
    inst = instrument_for_ticker(item.get("value") or "")
    return inst["id"] if inst else None


def intent_for_item(item: dict, plans: dict[str, dict] | None = None) -> dict | None:
    """The watch-rule view of a ticker item's plan: ``{thesis?, buy_below?, buy_above?,
    fair_value?}`` or None. ``plans`` (``plans_by_instrument()``) lets a loop read the table
    once."""
    if (item.get("type") or "ticker") != "ticker":
        return None
    plans = plans if plans is not None else plans_by_instrument()
    iid = _item_instrument_id(item)
    plan = plans.get(iid) if iid else None
    intent = {k: plan[k] for k in INTENT_FIELDS if plan.get(k) not in (None, "")} if plan else {}
    return intent or item.get("intent") or None


def with_intent(item: dict, plans: dict[str, dict] | None = None) -> dict:
    """A watch item as callers have always seen it: ``intent`` projected from the plan."""
    return {**item, "intent": intent_for_item(item, plans)}


def with_intents(items: list[dict]) -> list[dict]:
    """``with_intent`` over a list, reading the plans table once."""
    plans = plans_by_instrument()
    return [with_intent(i, plans) for i in items]


def plan_instrument_for_item(item: dict) -> str | None:
    """The instrument a watch item's ``intent`` write lands on, or None when the ticker has no
    instrument yet (then the intent stays on the item — the pre-plan storage — until the
    asset page or a Lucius plan write creates one). Never hits the network."""
    if (item.get("type") or "ticker") != "ticker":
        return None
    iid = _item_instrument_id(item)
    return iid if iid and InstrumentStore().find_by_id(iid) else None


def _intent_change(intent: dict | None) -> tuple[dict, list[str]]:
    intent = intent or {}
    unknown = set(intent) - set(INTENT_FIELDS)
    if unknown:
        raise ValidationError(f"intent: unknown field(s) {', '.join(sorted(unknown))}")
    fields = {k: intent.get(k) for k in INTENT_FIELDS if intent.get(k) not in (None, "")}
    return fields, [k for k in INTENT_FIELDS if k not in fields]


def set_from_watch_intent(instrument_id: str, intent: dict | None) -> dict:
    """The watchlist ``intent`` compatibility write: replaces the plan's four intent fields
    (``{}``/None clears them), leaving role, sell condition, stop and trim alone."""
    fields, clear = _intent_change(intent)
    try:
        return apply_plan_change(
            {"op": "set", "instrument_id": instrument_id, "fields": fields, "clear": clear}
        )
    except ValidationError as exc:
        raise ValidationError(f"intent: {exc}") from exc
