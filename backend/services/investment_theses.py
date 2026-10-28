"""Investment theses — why you own (or would buy) an instrument, and the prices that matter to you.

``apply_thesis_change`` is the ONE writer (REST, Lucius tools and the watchlist ``intent``
compatibility path all go through it); the rules themselves live in
``db.investment_thesis_store.validate_thesis``. A thesis is keyed by instrument and outlives
positions and watchlist membership — ADR ``2026-10-27-an-investment-thesis-lives-on-the-instrument``.

The watch rules still speak "intent" (thesis, buy zone, fair value): ``intent_for_item`` is that
view of a thesis, with the pre-migration ``item["intent"]`` as the fallback for a watched ticker the
migration could not match to an instrument. The first thesis written for that instrument absorbs
that leftover intent and clears it from the item, so the two never diverge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.db.engine import locked_unit_of_work
from backend.db.instrument_store import InstrumentStore
from backend.db.investment_thesis_store import THESIS_FIELDS, InvestmentThesisStore, validate_thesis
from backend.db.position_store import PositionStore
from backend.db.tables import investment_theses
from backend.db.watchlist_store import WatchlistStore

# The watch intent's own key → the thesis field it is stored as.
INTENT_TO_THESIS = {
    "thesis": "reason",
    "buy_below": "buy_below",
    "buy_above": "buy_above",
    "fair_value": "fair_value",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merged(existing: dict | None, fields: dict, clear: list) -> dict:
    """The thesis ``fields``/``clear`` would leave, validated as a whole (raises on a bad one)."""
    unknown = (set(fields) | set(clear)) - set(THESIS_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown thesis field(s): {', '.join(sorted(unknown))}.")
    merged = {k: (existing or {}).get(k) for k in THESIS_FIELDS}
    merged.update(fields)
    for key in clear:
        merged[key] = None
    return validate_thesis(merged)


def _intent_as_fields(intent: dict) -> dict:
    return {
        field: intent[key]
        for key, field in INTENT_TO_THESIS.items()
        if intent.get(key) not in (None, "")
    }


def _leftover_intent(instrument_id: str) -> tuple[dict, list[str]]:
    """Pre-thesis ``intent`` still sitting on watch items for this instrument (the migration
    could not match them), as thesis fields, plus those items' ids so the caller can clear them."""
    found: dict = {}
    ids: list[str] = []
    for item in WatchlistStore().query_items(type="ticker"):
        intent = item.get("intent")
        if not isinstance(intent, dict) or not intent or _item_instrument_id(item) != instrument_id:
            continue
        ids.append(item["id"])
        if not found:
            found = _intent_as_fields(intent)
    return found, ids


def apply_thesis_change(change: dict) -> dict:
    """``{"op": "set", "instrument_id", "fields": {...}, "clear": [...]}`` merges ``fields`` over
    the existing thesis and blanks every name in ``clear``; a result with nothing set deletes the
    thesis. ``{"op": "delete", "instrument_id"}`` deletes it. Returns ``{"thesis": dict | None}``."""
    op = change.get("op")
    instrument_id = (change.get("instrument_id") or "").strip()
    if not instrument_id:
        raise ValidationError("An instrument_id is required.")
    if op not in ("set", "delete"):
        raise ValidationError(f"Unknown thesis change '{op}'.")
    if InstrumentStore().find_by_id(instrument_id) is None:
        raise NotFoundError(f"No instrument '{instrument_id}'.")
    store = InvestmentThesisStore()
    absorbed: list[str] = []
    with locked_unit_of_work(investment_theses) as conn:
        existing = store.find_by_instrument(instrument_id, conn=conn)
        if op == "delete":
            if existing:
                store.delete(existing["id"], conn=conn)
            return {"thesis": None}
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
            row = {**{k: None for k in THESIS_FIELDS}, **valid, "updated_at": now}
            written = store.update_by_id(existing["id"], row, conn=conn)
        else:
            row = {
                "id": new_id("th"),
                "instrument_id": instrument_id,
                **{k: None for k in THESIS_FIELDS},
                **valid,
                "created_at": now,
                "updated_at": now,
            }
            written = store.insert(row, conn=conn)
    watch = WatchlistStore()
    for item_id in absorbed:
        watch.update_by_id(item_id, {"intent": None})
    return {"thesis": written}


def check_watch_intent(instrument_id: str, intent: dict | None) -> None:
    """Raise exactly what ``set_from_watch_intent`` would, without writing anything."""
    fields, clear = _intent_change(intent)
    try:
        _merged(thesis_for(instrument_id), fields, clear)
    except ValidationError as exc:
        raise ValidationError(f"intent: {exc}") from exc


def held_instrument_ids() -> set[str]:
    return {
        p.get("instrument_id")
        for p in PositionStore().all()
        if p.get("instrument_id") and float(p.get("quantity") or 0) > 0
    }


def thesis_for(instrument_id: str) -> dict | None:
    return InvestmentThesisStore().find_by_instrument(instrument_id)


def theses_by_instrument() -> dict[str, dict]:
    return {t["instrument_id"]: t for t in InvestmentThesisStore().all() if t.get("instrument_id")}


def list_theses(tickers: list[str] | None = None) -> list[dict]:
    """Every thesis with its instrument's ticker/name and whether it is held, optionally narrowed
    to ``tickers`` (case-insensitive, matched on price_source_id or symbol)."""
    instruments = {i["id"]: i for i in InstrumentStore().all()}
    held = held_instrument_ids()
    wanted = {t.strip().upper() for t in tickers or [] if t and t.strip()}
    out = []
    for thesis in InvestmentThesisStore().all():
        inst = instruments.get(thesis.get("instrument_id")) or {}
        ticker = (inst.get("price_source_id") or inst.get("symbol") or "").upper()
        if wanted and ticker not in wanted and (inst.get("symbol") or "").upper() not in wanted:
            continue
        out.append(
            {
                **thesis,
                "ticker": ticker,
                "name": inst.get("name") or "",
                "currency": inst.get("quote_currency") or "",
                "held": thesis.get("instrument_id") in held,
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


def editor_context(instrument_id: str | None = None, ticker: str | None = None) -> dict | None:
    """What a thesis editor opened away from the asset page needs — the instrument the id (else
    the ticker) names, its quote currency, whether it is held, and its thesis — or None when
    neither names an instrument. Never hits the network."""
    inst = InstrumentStore().find_by_id(instrument_id) if instrument_id else None
    if inst is None and ticker:
        inst = instrument_for_ticker(ticker)
    if inst is None:
        return None
    return {
        "instrument_id": inst["id"],
        "currency": (inst.get("quote_currency") or "USD").upper(),
        "held": inst["id"] in held_instrument_ids(),
        "thesis": thesis_for(inst["id"]),
    }


def _item_instrument_id(item: dict) -> str | None:
    iid = item.get("instrument_id")
    if iid:
        return iid
    inst = instrument_for_ticker(item.get("value") or "")
    return inst["id"] if inst else None


def intent_for_item(item: dict, theses: dict[str, dict] | None = None) -> dict | None:
    """The watch-rule view of a ticker item's thesis: ``{thesis?, buy_below?, buy_above?,
    fair_value?}`` (``thesis`` carries the stored ``reason``) or None. ``theses``
    (``theses_by_instrument()``) lets a loop read the table once."""
    if (item.get("type") or "ticker") != "ticker":
        return None
    theses = theses if theses is not None else theses_by_instrument()
    iid = _item_instrument_id(item)
    thesis = theses.get(iid) if iid else None
    intent = (
        {
            key: thesis[field]
            for key, field in INTENT_TO_THESIS.items()
            if thesis.get(field) not in (None, "")
        }
        if thesis
        else {}
    )
    return intent or item.get("intent") or None


def with_intent(item: dict, theses: dict[str, dict] | None = None) -> dict:
    """A watch item as callers have always seen it: ``intent`` projected from the thesis."""
    return {**item, "intent": intent_for_item(item, theses)}


def with_intents(items: list[dict]) -> list[dict]:
    """``with_intent`` over a list, reading the theses table once."""
    theses = theses_by_instrument()
    return [with_intent(i, theses) for i in items]


def thesis_instrument_for_item(item: dict) -> str | None:
    """The instrument a watch item's ``intent`` write lands on, or None when the ticker has no
    instrument yet (then the intent stays on the item — the pre-thesis storage — until the
    asset page or a Lucius thesis write creates one). Never hits the network."""
    if (item.get("type") or "ticker") != "ticker":
        return None
    iid = _item_instrument_id(item)
    return iid if iid and InstrumentStore().find_by_id(iid) else None


def _intent_change(intent: dict | None) -> tuple[dict, list[str]]:
    intent = intent or {}
    unknown = set(intent) - set(INTENT_TO_THESIS)
    if unknown:
        raise ValidationError(f"intent: unknown field(s) {', '.join(sorted(unknown))}")
    fields = _intent_as_fields(intent)
    return fields, [f for f in INTENT_TO_THESIS.values() if f not in fields]


def set_from_watch_intent(instrument_id: str, intent: dict | None) -> dict:
    """The watchlist ``intent`` compatibility write: replaces the thesis's four intent fields
    (``{}``/None clears them), leaving role, breaks_if, stop and trim alone."""
    fields, clear = _intent_change(intent)
    try:
        return apply_thesis_change(
            {"op": "set", "instrument_id": instrument_id, "fields": fields, "clear": clear}
        )
    except ValidationError as exc:
        raise ValidationError(f"intent: {exc}") from exc
