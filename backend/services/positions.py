"""
positions_service — the one place that composes instrument × position × account (× price)
into the flat, computed shape the rest of the system expects.

This keeps the join logic out of route handlers, the AI prompt, and ledger_service. It
emits a **superset of the legacy data.csv row** so existing consumers (usePortfolio's
compute(), the prompt's qty×price×fx fold, ledger_service.market_positions_value) keep
working with at most additive field changes:

    legacy CSV fields:  category, label, bucket, asset_class, quantity, price,
                        cost_basis, currency, ticker, note
    new identity fields: position_id, instrument_id, account_id,
                        price_source, price_source_id, asset_subclass

`price` is resolved in priority order: live `prices` arg → last-known from PriceHistoryStore
→ instrument.manual_price → 0. `currency` is the instrument's quote_currency, so the
caller's existing FX step values it correctly.

See .claude/rfcs/instrument-position-data-model.md.
"""

from __future__ import annotations

import logging
import threading

from backend.core.errors import ValidationError
from backend.core.instrument_display import display_name
from backend.db import read_cache
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import FIGURE_FIELDS, PositionStore, vesting_state_of
from backend.db.price_history_store import PriceHistoryStore
from backend.services import fixed_income
from backend.services import prices as prices_service

_log = logging.getLogger(__name__)

# Bound concurrent per-position backfill threads so a bulk connector sync (many positions
# added at once) cannot spawn a thread storm against the external price APIs.
_backfill_semaphore = threading.Semaphore(3)

# Track in-flight price_source_ids so a second trigger for an already-running backfill
# is dropped rather than double-spawning.
_backfill_lock = threading.Lock()
_backfill_in_flight: set[str] = set()


def _trigger_backfill_async(price_source_id: str) -> None:
    """Fire a best-effort historical-price backfill for price_source_id in a daemon thread.

    Called on position add so newly-held instruments get historical depth without
    blocking the create response. Coalesces duplicate triggers (same id already in-flight
    is dropped). Total concurrent threads are capped by _backfill_semaphore (~3).
    Failures are logged inside backfill_history and never propagate here.

    No-ops under ONW_QA_MODE — QA/isolated runs must not hit external APIs."""
    if not price_source_id:
        return
    from backend.core.config import get_settings

    if get_settings().onw_qa_mode:
        return

    with _backfill_lock:
        if price_source_id in _backfill_in_flight:
            return
        _backfill_in_flight.add(price_source_id)

    def _run() -> None:
        try:
            with _backfill_semaphore:
                from backend.services.prices import backfill_history

                backfill_history(price_source_id)
        except Exception as exc:
            _log.warning("_trigger_backfill_async: unexpected error (%s)", type(exc).__name__)
        finally:
            with _backfill_lock:
                _backfill_in_flight.discard(price_source_id)

    t = threading.Thread(target=_run, daemon=True, name=f"backfill-{price_source_id[:16]}")
    t.start()


def _resolve_price(
    inst: dict,
    prices: dict | None,
    last_known: dict,
    extended: dict | None = None,
    *,
    quote: prices_service.PriceQuote | None = None,
) -> float:
    """Live (extended-hours preferred over regular) → last-known → manual → 0. `last_known`
    is a {price_source_id: price} map loaded once by the caller (avoids one history-file
    read per position).

    A computed fixed-income mark (T-bill accretion, FD accrual, SSB par) out-ranks every
    other source: it is a deterministic function of the instrument's bond_terms + today, so
    it is always fresher than a stale manual mark. Market bonds / bond ETFs return None here
    and fall through to the normal live/manual ladder.

    The live tier itself is resolved by prices.resolve_price_quote() — the single read
    chokepoint shared with hydrated()'s own extended_* row stamps, asset_detail, and
    market_ticker, so this never disagrees with what those show. `quote`: a caller that
    already resolved one for this SAME instrument (hydrated() does, for its own display
    fields) passes it straight through instead of this function re-deriving an identical
    one a second time."""
    mark = fixed_income.computed_mark(inst)
    if mark is not None:
        return mark
    pid = inst.get("price_source_id") or ""
    if quote is None:
        quote = prices_service.resolve_price_quote(pid, prices=prices, extended=extended)
    if quote is not None:
        return quote.price
    if (inst.get("price_source") or "").lower() == "manual" and inst.get(
        "manual_price"
    ) is not None:
        return float(inst["manual_price"])
    if pid in last_known and last_known[pid] is not None:
        return float(last_known[pid])
    if inst.get("manual_price") is not None:
        return float(inst["manual_price"])
    return 0.0


def _resolve_prev(inst: dict, prev_prices: dict | None, prior_map: dict) -> float | None:
    """Day-P&L anchor, priority order (mirrors _resolve_price): provider-supplied previous
    close → our recorded prior-day close → None. Returning None (not 0) signals 'no prior
    day yet' so callers render '—' instead of a fake 100%. A broker/MCP source supplies prev
    directly via `prev_prices`; the recorded series is the fallback. `prior_map` is a
    {price_source_id: prior_close} map loaded once by the caller."""
    pid = inst.get("price_source_id") or ""
    if prev_prices and prev_prices.get(pid) is not None:
        return float(prev_prices[pid])
    if prior_map.get(pid) is not None:
        return float(prior_map[pid])
    return None


def coerce_cost(value) -> float | None:
    """Cost-basis semantics: 0/blank means 'unknown' → None."""
    if value in (None, ""):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def normalize_cost_currency(value: str) -> str:
    """Keep an explicit native basis currency; never infer an FX conversion."""
    from backend.core.errors import ValidationError

    if not isinstance(value, str):
        raise ValidationError("cost_currency must be a three-letter currency code")
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValidationError("cost_currency must be a three-letter currency code")
    return currency


# ── Native write API (single source of truth for position mutations) ──────────
# Both the AI apply_position_* tools and the Telegram direct-apply path call these,
# so the apply logic lives in exactly one place.


def add_position(
    *,
    instrument_id: str,
    account_id: str,
    cost_currency: str | None = None,
    vesting_state: str = "vested",
    acquired_at: str | None = None,
) -> dict:
    """Create an EMPTY position shell. Its quantity and cost basis come from the records the
    caller writes next (a lot, a vest, the grant's schedule) via ``position_figures``."""
    return PositionStore().create(
        {
            "instrument_id": instrument_id,
            "account_id": account_id,
            "quantity": 0.0,
            "cost_basis": None,
            "cost_currency": normalize_cost_currency(cost_currency)
            if cost_currency is not None
            else None,
            "vesting_state": vesting_state or "vested",
            "acquired_at": acquired_at or None,
        }
    )


_UPDATABLE_FIELDS = {"vesting_state", "account_id"}

_FIGURES_ARE_DERIVED = (
    "a position's quantity and cost basis are derived from its records, so they can't be "
    "edited directly — record what happened instead: a buy or transfer in (a new lot), a sale "
    "or transfer out (a disposal), or a correction to an existing lot (its date, quantity "
    "or cost)"
)


def _grant_id_for_position(position_id: str) -> str | None:
    """Return the grant_id if this position is grant-managed, else None.

    Grant-managed = the grant's locked position (unvested units) OR its pending-release
    position (vested-but-undelivered units). Both are quantity-only mirrors of grant state:
    a hand-edit to either silently desyncs the position from the tranches/vest events that
    are supposed to explain it.
    """
    from backend.db.grant_store import GrantStore

    for grant in GrantStore().all():
        if position_id in (grant.get("locked_position_id"), grant.get("pending_position_id")):
            return grant.get("id")
    return None


def _validated_position_update(
    position_id: str, changes: dict, *, _grant_managed_ok: bool = False
) -> dict:
    """Resolve and validate fields without writing a position or creating a venue."""
    from backend.core.errors import NotFoundError, ValidationError

    protected = set(changes) & FIGURE_FIELDS
    if "vesting_state" in changes and not _grant_managed_ok:
        protected.add("vesting_state")
    grant_id = _grant_id_for_position(position_id) if protected else None
    if grant_id:
        raise ValidationError(
            f"this position is managed by equity grant {grant_id} — its units come from the "
            "grant's vesting schedule and vest records (update the grant, record or undo a "
            "vest) and can't be edited directly"
        )
    if protected & FIGURE_FIELDS:
        raise ValidationError(_FIGURES_ARE_DERIVED)
    if protected:
        raise ValidationError(
            "vesting state is set by the equity grant flow — unvested units belong to a grant "
            "(create one with its vesting schedule) rather than to a hand-edited position"
        )

    unknown = set(changes) - _UPDATABLE_FIELDS
    if unknown:
        supported = ", ".join(sorted(_UPDATABLE_FIELDS))
        raise ValidationError(
            f"unsupported position update field(s): {', '.join(sorted(unknown))} — supported: {supported}"
        )

    clean = {k: changes[k] for k in _UPDATABLE_FIELDS if k in changes}
    if "account_id" in clean:
        acc = AccountStore().find_by_id(clean["account_id"])
        if not acc:
            raise NotFoundError("account not found", account_id=clean["account_id"])
        _refuse_move_onto_a_holding(position_id, acc)
    return clean


def _refuse_move_onto_a_holding(position_id: str, dest: dict) -> None:
    """A move that would land on an account already holding the same instrument would leave
    TWO liquid positions there — the duplicate-holding state the add path refuses (found
    2026-10-09 while testing a KSTL release moved to IBKR, which already held KSTL). Shares
    moving onto an existing holding are a TRANSFER, recorded as such: a non-taxable disposal
    here naming the destination, and a transfer_in lot on the existing holding."""
    pos = PositionStore().find_by_id(position_id)
    if (
        not pos
        or pos.get("account_id") == dest["id"]
        or position_id in _grant_managed_position_ids()
    ):
        return
    existing = find_active_liquid_position(pos.get("instrument_id", ""), dest["id"])
    if existing and existing["id"] != position_id:
        raise ValidationError(
            f"{dest.get('name') or 'that account'} already holds this instrument "
            f"(position_id={existing['id']}) — a position can't be moved onto another holding. "
            "If the shares moved there, record a transfer: a non-taxable disposal on this "
            "position naming that account, and a transfer_in lot on the existing holding "
            "with the same cost basis.",
            existing_position_id=existing["id"],
        )


def validate_position_update(position_id: str, changes: dict) -> None:
    """Raise exactly what ``update_position`` would refuse, without writing — for previews."""
    _validated_position_update(position_id, changes)


def update_position(
    position_id: str, changes: dict, *, _grant_managed_ok: bool = False
) -> dict | None:
    if PositionStore().find_by_id(position_id) is None:
        return None  # "position not found" outranks any refusal of the fields
    clean = _validated_position_update(position_id, changes, _grant_managed_ok=_grant_managed_ok)
    if "account_id" not in clean:
        return PositionStore().touch(position_id, clean)
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import lots as lots_tbl
    from backend.db.tables import positions as positions_tbl

    with locked_unit_of_work(positions_tbl, lots_tbl) as conn:
        pos = PositionStore().touch(position_id, clean, conn=conn)
        if pos is not None:
            _refile_lots(position_id, clean["account_id"], conn=conn)
        return pos


def _refile_lots(position_id: str, account_id: str, *, conn) -> None:
    """A lot's ``account_id`` is its position's venue. Moving the position moves every lot,
    open and closed — leaving them behind filed 23 lots under settlement wallets (June 2026),
    which then blocked deleting those wallets and showed the buys under the wrong account."""
    from backend.db.lot_store import LotStore

    store = LotStore()
    for lot in store.lots_for_position(position_id, include_closed=True, conn=conn):
        if lot.get("account_id") != account_id:
            store.touch(lot["id"], {"account_id": account_id}, conn=conn)


def remove_position(position_id: str) -> bool:
    """Remove a position AND the lots that only it owned, in one DB transaction.

    Adding a position books a buy lot; removing one used to delete only the position row, so its
    lots stayed "held" and every lot-based figure (net-worth history, returns, per-holding P&L)
    kept counting it — live 2026-10-01: a removed test gold position still counted SGD 179K.

    Per lot: never sold from → deleted (with its linked purchase transaction, via the lots
    choke's core); fully sold → kept (real history, nets to zero); partly sold → the removal is
    REFUSED, because deleting the position would leave the unsold rest counted as held.
    Returns False when the position doesn't exist."""
    from backend.core.errors import ValidationError
    from backend.db.engine import locked_unit_of_work
    from backend.db.lot_store import LotStore
    from backend.db.tables import grants as grants_tbl
    from backend.db.tables import lots as lots_tbl
    from backend.db.tables import positions as positions_tbl
    from backend.db.tables import transactions as txns_tbl
    from backend.db.tables import txn_audit as txn_audit_tbl
    from backend.services import lots as lots_service

    with locked_unit_of_work(lots_tbl, positions_tbl, txns_tbl, txn_audit_tbl, grants_tbl) as conn:
        store = PositionStore()
        if store.find_by_id(position_id, conn=conn) is None:
            return False
        _release_grant_links(position_id, conn=conn)
        owned = [lot for lot in LotStore().all(conn=conn) if lot.get("position_id") == position_id]
        unsold = []
        for lot in owned:
            original = float(lot.get("original_qty") or 0)
            remaining = float(lot.get("remaining_qty") or 0)
            if 0 < remaining < original:
                raise ValidationError(
                    "This position still holds units from a lot that was partly sold — sell or "
                    "close the rest first, so removing it doesn't leave them counted as held."
                )
            if remaining >= original > 0:
                unsold.append(lot)
        txn_ids = [lot["txn_id"] for lot in unsold if lot.get("txn_id")]
        if txn_ids:
            from backend.services import ledger as ledger_service

            ledger_service.bulk_delete_transactions(txn_ids, conn=conn, reject_identities=False)
        for lot in unsold:
            result = lots_service._delete_lot_core(lot["id"], conn=conn)
            if result.get("error"):
                raise ValidationError(f"could not remove lot {lot['id']}: {result['error']}")
        return store.delete(position_id, conn=conn)


_GRANT_POSITION_KEYS = ("locked_position_id", "pending_position_id")
_LIVE_GRANT_STATUSES = ("active", "fully_vested")


def _release_grant_links(position_id: str, *, conn) -> None:
    """A live grant (active / fully vested) still holding ``position_id`` as its locked or
    pending position REFUSES the removal — vests would fall back to a position that no longer
    exists. A cancelled/forfeited grant just lets go of it (pointer cleared in the caller's
    unit of work), so its leftover position can still be cleaned up. Before this, removal
    left the grant pointing at nothing (2026-10-13 audit)."""
    from backend.core.errors import ValidationError
    from backend.db.grant_store import GrantStore

    grants = GrantStore()
    for grant in grants.all(conn=conn):
        keys = [k for k in _GRANT_POSITION_KEYS if grant.get(k) == position_id]
        if not keys:
            continue
        if grant.get("status") in _LIVE_GRANT_STATUSES:
            raise ValidationError(
                "This position holds an equity grant's units — cancel the grant (or move it "
                "to another account) before removing the position.",
                grant_id=grant.get("id"),
            )
        grants.update_by_id(grant["id"], {k: None for k in keys}, conn=conn)


def find_active_liquid_position(instrument_id: str, account_id: str) -> dict | None:
    """The duplicate-holding guard for apply_position_change(add): an ACTIVE (qty > 0)
    liquid position for the same instrument × account. GRANT-MANAGED positions never match
    (a grant legitimately holds its own unvested and pending-release rows, and since the
    ownership/liquidity split the pending one is `vested` too — so entitlement alone no
    longer excludes it and we must exclude it by identity). Closed (qty <= 0) records never
    match either: re-opening a sold-out holding goes to `find_sold_out_liquid_position`."""
    grant_owned = _grant_managed_position_ids()
    for pos in PositionStore().query_positions(instrument_id=instrument_id, account_id=account_id):
        if pos["id"] in grant_owned:
            continue
        if vesting_state_of(pos) == "vested" and float(pos.get("quantity") or 0) > 0:
            return pos
    return None


def find_sold_out_liquid_position(instrument_id: str, account_id: str) -> dict | None:
    """The position a buy of a sold-out holding REOPENS: a qty<=0 liquid row for the same
    instrument × account, never a grant's own. One rule for every acquire path — the Add-lot
    form (`lots._ensure_acquire_position`) and add_positions — so a sell-out then rebuy keeps
    its sales and dividends on one position instead of minting a sibling."""
    grant_owned = _grant_managed_position_ids()
    for pos in PositionStore().query_positions(instrument_id=instrument_id, account_id=account_id):
        if pos["id"] in grant_owned or vesting_state_of(pos) != "vested":
            continue
        if float(pos.get("quantity") or 0) <= 0:
            return pos
    return None


def _grant_managed_position_ids() -> set[str]:
    """Every position a grant owns outright — its unvested row and its pending-release row."""
    from backend.db.grant_store import GrantStore

    ids: set[str] = set()
    for g in GrantStore().all():
        for key in ("locked_position_id", "pending_position_id"):
            if g.get(key):
                ids.add(g[key])
    return ids


def _materialize_account_name(changes: dict) -> dict:
    """APPLY-time half of a position move named by account_name: mint (or reuse) the venue
    and hand ``update_position`` a concrete account_id.

    ``propose_position_update`` deliberately leaves an unmatched ``account_name`` unresolved
    so the preview can say "would create account X" without writing; this is where that
    promise is kept. A name that already resolves is folded to its id here too, so
    ``update_position``'s ``_UPDATABLE_FIELDS`` never has to know about the name form."""
    if "account_name" not in changes:
        return changes
    clean = dict(changes)
    name = clean.pop("account_name")
    if not clean.get("account_id"):
        clean["account_id"] = resolve_account(
            account_name=name, account_type=changes.get("account_type")
        )["id"]
    clean.pop("account_type", None)
    return clean


def _validate_add_cost(change: dict, cost_currency: str) -> None:
    """Reject an invalid auto-lot before creating either its venue or its position."""
    import math
    from datetime import date

    from backend.core.errors import MissingFxRateError, ValidationError
    from backend.core.fx import known_rate

    try:
        quantity = float(change.get("quantity") or 0)
        cost = change.get("cost_basis")
        if not math.isfinite(quantity):
            raise ValueError("quantity must be finite")
        if cost not in (None, "") and not math.isfinite(float(cost)):
            raise ValueError("cost_basis must be finite")
        if not change.get("acquired_at"):
            return
        date.fromisoformat(change["acquired_at"])
        if quantity <= 0:
            raise ValueError("quantity must be positive for an acquisition")
        if not math.isfinite(float(change.get("fees") or 0)):
            raise ValueError("fees must be finite")
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    if coerce_cost(cost) is None:
        return

    from backend.services import lots

    base = lots.base_currency().upper()
    if known_rate(cost_currency, base, lots._load_rates()) is None:
        raise MissingFxRateError(cost_currency, base)


def apply_position_change(change: dict) -> dict:
    """Apply a confirmed native position change (add | update | remove). Single source of
    truth shared by the AI apply_position_* tools and the Telegram direct-apply path."""
    ctype = change.get("type")
    if ctype == "update":
        changes = change.get("changes", {})
        if "account_name" in changes:
            if PositionStore().find_by_id(change["position_id"]) is None:
                return {"error": "position not found"}
            _validated_position_update(
                change["position_id"],
                {k: v for k, v in changes.items() if k not in {"account_name", "account_type"}},
                _grant_managed_ok=bool(change.get("_grant_managed_ok")),
            )
        pos = update_position(
            change["position_id"],
            _materialize_account_name(change.get("changes", {})),
            _grant_managed_ok=bool(change.get("_grant_managed_ok")),
        )
        return (
            {"ok": True, "position_id": change["position_id"]}
            if pos
            else {"error": "position not found"}
        )
    if ctype == "add":
        return _apply_add(change)
    if ctype == "remove":
        try:
            removed = remove_position(change["position_id"])
        except ValidationError as exc:
            return {"error": exc.message or str(exc)}
        return (
            {"ok": True, "position_id": change["position_id"]}
            if removed
            else {"error": "position not found"}
        )
    return {"error": f"unknown change type: {ctype}"}


def _apply_add(change: dict) -> dict:
    """Add a holding WITH its record (ADR 2026-10-07-a-position-quantity-is-derived-from-its-
    records): the position is an empty shell and its quantity comes from the lot booked right
    after — a ``buy`` lot on ``acquired_at``, or, with no date, an ``opening`` lot dated today
    (tracking starts here, like an account's Starting Balance; the user can set the real date
    on the lot later). Grant-managed rows (``_grant_managed_ok``) stay shells: the equity
    choke re-derives them from the grant and its vests. A quantity of 0 makes (or reuses) an
    empty shell for lots recorded next; a negative one is refused."""
    from backend.core.user_time import user_today

    inst = InstrumentStore().find_by_id(change.get("instrument_id", ""))
    if not inst:
        return {"error": "unknown instrument_id — resolve_instrument first"}
    grant_managed = bool(change.get("_grant_managed_ok"))
    vesting = change.get("vesting_state") or "vested"
    refused = _refuse_add(change, vesting, grant_managed)
    if refused:
        return refused
    cost_currency = normalize_cost_currency(
        change["cost_currency"]
        if change.get("cost_currency") is not None
        else inst.get("quote_currency")
    )
    quantity = 0.0 if grant_managed else float(change.get("quantity") or 0)
    # FX is required only for a DATED buy (its lot stamps the acquisition-date rate); an
    # undated opening balance tolerates an unknown rate, as legacy opening lots always have.
    _validate_add_cost(change, cost_currency)
    existing = _existing_record_for_add(change, quantity)
    if existing:
        return existing
    account = resolve_account(
        account_id=change.get("account_id"),
        account_name=change.get("account_name"),
        account_type=change.get("account_type"),
        currency=inst.get("quote_currency"),
    )
    reopened = None
    if vesting == "vested" and not grant_managed:
        held = _duplicate_holding(inst, account["id"])
        if held:
            return held
        reopened = find_sold_out_liquid_position(inst["id"], account["id"])
    pos = reopened or add_position(
        instrument_id=inst["id"],
        account_id=account["id"],
        cost_currency=cost_currency,
        vesting_state=vesting,
        acquired_at=change.get("acquired_at"),
    )
    result = {"ok": True, "position_id": pos["id"], "instrument": inst.get("symbol")}
    if reopened:
        result["reopened"] = True
    if quantity > 0:
        acquired_at = change.get("acquired_at") or user_today().isoformat()
        # A reopened row carries the holding's sales and dividends — a failed lot leaves it.
        book = _book_add_lot if reopened else _book_add_lot_or_undo
        result.update(book(pos, change, quantity, acquired_at, cost_currency))
        if result.get("error"):
            return {"error": result["error"]}
    _after_add(inst)
    return result


def _refuse_add(change: dict, vesting: str, grant_managed: bool) -> dict | None:
    import math

    if vesting == "unvested" and not grant_managed:
        return {
            "error": (
                "unvested units come from an equity grant's vesting schedule — create the "
                "grant (with its tranches) instead of an unvested position"
            )
        }
    try:
        quantity = float(change.get("quantity") or 0)
    except (TypeError, ValueError):
        return {"error": "quantity must be a number"}
    if not grant_managed and (not math.isfinite(quantity) or quantity < 0):
        return {"error": "quantity must be zero or a positive number of units"}
    return None


def _existing_record_for_add(change: dict, quantity: float) -> dict | None:
    """A retried add (same idempotency_key) returns the lot it already booked."""
    from backend.db.lot_store import LotStore

    key = change.get("idempotency_key")
    if quantity <= 0 or not key:
        return None
    lot = LotStore().find_by_idempotency_key(key)
    if not lot:
        return None
    return {
        "ok": True,
        "position_id": lot.get("position_id"),
        "lot_id": lot["id"],
        "idempotent": True,
    }


def _duplicate_holding(inst: dict, account_id: str) -> dict | None:
    """Refuse a second liquid position for the same instrument × account — a buy of an
    existing holding is a lot acquisition, not a new position (the duplicate-position /
    double-quantity incident). A buy of a SOLD-OUT holding is the caller's to reopen."""
    existing = find_active_liquid_position(inst["id"], account_id)
    if existing:
        return {
            "error": (
                f"{inst.get('symbol') or 'this instrument'} already has an active "
                f"position in this account (position_id={existing['id']}, "
                f"qty={existing.get('quantity')}). To record a buy of an existing "
                "holding call acquire_lots with that position_id — do NOT "
                "create a duplicate position."
            ),
            "position_id": existing["id"],
        }
    return None


def _book_add_lot_or_undo(
    pos: dict, change: dict, quantity: float, acquired_at: str, cost_currency: str
) -> dict:
    """Book the added holding's lot; if that fails in ANY way — a refusal or an exception —
    remove the empty shell, so a failed add never leaves a holding behind."""
    try:
        lot_res = _book_add_lot(pos, change, quantity, acquired_at, cost_currency)
    except Exception:
        PositionStore().delete(pos["id"])
        raise
    if lot_res.get("error"):
        PositionStore().delete(pos["id"])
    return lot_res


def _book_add_lot(
    pos: dict, change: dict, quantity: float, acquired_at: str, cost_currency: str
) -> dict:
    """The added holding's record. The caller's idempotency_key lands on this lot, so a later
    acquire_lots for the same trade (same key) dedupes instead of double-booking."""
    from backend.services import lots as lots_service

    dated = bool(change.get("acquired_at"))
    lot_res = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": quantity,
            "unit_cost": change.get("cost_basis"),
            "cost_currency": cost_currency,
            "fees": change.get("fees", 0) or 0,
            "acquired_at": acquired_at,
            "kind": "buy" if dated else "opening",
            "idempotency_key": change.get("idempotency_key"),
            "source": "position_add",
        }
    )
    if lot_res.get("error"):
        return {"error": lot_res["error"]}
    out = {"lot_id": lot_res.get("lot_id")}
    if dated:
        out["note"] = "buy lot already recorded for this trade — do NOT call acquire_lots for it"
    else:
        out["note"] = (
            "no purchase date given — recorded as an opening balance dated today; if the "
            "real purchase date is known, set it on this lot (update_lots)"
        )
    return out


def _after_add(inst: dict) -> None:
    # Best-effort backfill of historical closes for the newly-held instrument, in a daemon
    # thread so a slow/failed network call never blocks the add.
    _trigger_backfill_async(inst.get("price_source_id") or "")
    # Seed the daily coupon-reminder task on the first coupon-paying bond (idempotent).
    if fixed_income.is_fixed_income(inst):
        bt = fixed_income.terms(inst) or {}
        if bt.get("subtype") in fixed_income._COUPON_SUBTYPES and bt.get("coupon_freq_months"):
            from backend.services.automations.service import seed_coupon_reminder

            seed_coupon_reminder()


def find_account(
    *,
    account_id: str | None = None,
    account_name: str | None = None,
) -> dict | None:
    """READ-ONLY venue lookup (by id, then by exact name, then by institution) — returns None
    when no account matches and RAISES when a name or institution names several live
    accounts. This is the half of ``resolve_account`` a PREVIEW may call: a propose_*
    handler needs the resolved venue to build the diff it shows the user, but must never
    mint one before the user confirms (a typo'd account_name used to leave a real orphan
    account behind). The creating half runs at apply time via ``resolve_account``, which is
    layered on top of this."""
    acc = AccountStore()
    if account_id:
        found = acc.find_by_id(account_id)
        if found:
            return found
    if account_name:
        from backend.services.accounts import resolve_venue_ref

        rows = acc.all()
        resolved = resolve_venue_ref(account_name, rows=rows)
        return next((a for a in rows if a["id"] == resolved), None)
    return None


def resolve_account(
    *,
    account_id: str | None = None,
    account_name: str | None = None,
    account_type: str | None = None,
    currency: str | None = None,
) -> dict:
    """Find an existing venue (by id, then by exact name, then by institution) or create one. Used by the
    APPLY path so a holding can name a venue that doesn't exist yet. A preview/propose
    caller wants ``find_account`` instead — this one writes.

    Only an ``account_name`` may mint a venue. An ``account_id`` (or a name passed as one)
    must name a real account — an unknown one RAISES rather than silently booking the holding
    into a freshly minted, empty "Brokerage" account (2026-10-13 audit)."""
    if account_id:
        from backend.services.accounts import require_account_id

        return AccountStore().find_by_id(require_account_id(account_id))
    found = find_account(account_name=account_name)
    if found:
        return found
    from backend.services.accounts import create_account

    return create_account(
        {
            "name": account_name or "Brokerage",
            "type": account_type or "investment",
            "institution": account_name or "",
            "currency": (currency or "USD").upper(),
            "initial_balance": 0,
        }
    )


def get_position(position_id: str) -> dict | None:
    return PositionStore().find_by_id(position_id)


def hydrated_one(position_id: str) -> dict | None:
    """A single hydrated row by position_id — for building change diffs/displays."""
    return next((r for r in hydrated() if r.get("position_id") == position_id), None)


def price_targets() -> list[dict]:
    """Distinct (price_source, price_source_id, quote_currency) triples referenced by any
    position — the fetch layer routes these to yfinance / CoinGecko / manual. Replaces the
    'guess crypto by membership in COINGECKO_IDS' approach."""
    insts = {i["id"]: i for i in InstrumentStore().all()}
    seen: dict[tuple, dict] = {}
    for pos in PositionStore().all():
        inst = insts.get(pos.get("instrument_id"))
        if not inst:
            continue
        pid = inst.get("price_source_id") or ""
        if not pid:
            continue
        key = ((inst.get("price_source") or "").lower(), pid)
        if key not in seen:
            seen[key] = {
                "price_source": key[0],
                "price_source_id": pid,
                "quote_currency": inst.get("quote_currency", "USD"),
            }
    return list(seen.values())


def _grants_by_position() -> dict[str, dict]:
    """Map locked_position_id → grant dict for all grants that have one.
    Imported lazily so the module stays import-clean on legacy paths that never
    use equity grants (GrantStore is not imported at module level)."""
    from backend.db.grant_store import GrantStore

    return {
        g["locked_position_id"]: g
        for g in GrantStore().all()
        if g.get("locked_position_id") and g.get("grant_price_per_unit") is not None
    }


# ── Liquidity: DERIVED, never stored (ADR 2026-07-31-split-position-bucket) ──────────
#
# The three tiers are the standard personal-finance split (SmartAsset, NerdWallet, First
# Citizens, ProjectionLab), not ours: liquid = convertible within days; semi-liquid =
# sellable but gated by a delay; illiquid = property/pensions/locked-up capital.
#
# This says NOTHING about entitlement. Property is illiquid AND vested. Keeping the two
# apart is the entire point of the split — a single field could not express both, and the
# attempt to make it do so is what nearly deleted real property from net worth.

# Instrument classes that cannot be sold in days no matter which account holds them.
_ILLIQUID_ASSET_CLASSES = {"property", "receivable"}
# Account types whose balance is real but gated (statutory schemes, term deposits).
_ILLIQUID_ACCOUNT_TYPES = {"retirement", "receivable", "fixed_deposit"}


def _pending_release_position_ids() -> set[str]:
    """Positions holding vested-but-undelivered grant shares (the two-step vest)."""
    from backend.db.grant_store import GrantStore

    return {g["pending_position_id"] for g in GrantStore().all() if g.get("pending_position_id")}


def liquidity_of(
    pos: dict,
    inst: dict | None = None,
    acct: dict | None = None,
    pending_position_ids: set[str] | None = None,
) -> str:
    """How reachable is this holding — 'liquid' | 'semi_liquid' | 'illiquid'.

    ``pending_position_ids`` is passed in by ``hydrated`` so a whole-book join costs ONE
    grant read rather than one per row; omitted, it is looked up (fine for a single row).
    """
    if vesting_state_of(pos) == "unvested":
        # Not the holder's yet, so not reachable. Net worth excludes these on the
        # ENTITLEMENT axis — this value exists only so the field is total.
        return "illiquid"

    if pending_position_ids is None:
        pending_position_ids = _pending_release_position_ids()
    if pos.get("id") in pending_position_ids:
        # Vested and owned, but the shares have not been delivered — sellable soon, not now.
        return "semi_liquid"

    if (inst or {}).get("asset_class") in _ILLIQUID_ASSET_CLASSES:
        return "illiquid"
    if (acct or {}).get("type") in _ILLIQUID_ACCOUNT_TYPES:
        return "illiquid"
    return "liquid"


def _row_ticker(inst: dict) -> str:
    """Display ticker for a holding. Crypto prices via a CoinGecko id (e.g. "bitcoin")
    but should READ as its market symbol ("BTC"); everything else keeps price_source_id,
    which the price maps + Yahoo routing key on (incl. exchange suffixes like KSTL.DE)."""
    if (inst.get("price_source") or "").lower() == "coingecko":
        return (inst.get("symbol") or inst.get("price_source_id") or "").upper()
    return inst.get("price_source_id", "")


@read_cache.cached(lambda: None)
def hydration_context() -> dict:
    """The price-INDEPENDENT inputs `hydrated()` needs — split out so a caller building rows
    against two different price snapshots in one request (e.g. `portfolio_rows.projected_rows`,
    disk prices then live prices) loads every underlying store exactly once and passes the same
    context to both `hydrated()` calls, instead of each call independently re-reading
    instruments/accounts/price-history/grants/positions for identical, price-agnostic data."""
    insts = {i["id"]: i for i in InstrumentStore().all()}
    accts = {a["id"]: a for a in AccountStore().all()}
    store = PriceHistoryStore()
    last_known = store.latest_map()  # single file read each, reused for all rows
    # A caller that doesn't fetch its OWN live price (net worth, income, watchlist, most AI
    # tools — the ~25 bare `hydrated()` callers) otherwise falls all the way back to the last
    # RECORDED regular-session close, however stale, even seconds after a live extended print
    # was resolved elsewhere in the very same process (owner-reproduced 2026-09-01, PLTR).
    # `prices.latest_live_prices()` is a separate, non-day-keyed, extended-preferred cache kept
    # warm by the scheduled price-check sweep — see its own module docstring for why this can't
    # just be written into the day-keyed store itself. Overlaid, never trusted alone: an
    # instrument nothing has fetched live recently still correctly falls back to last_known.
    last_known = {**last_known, **prices_service.latest_live_prices()}
    prior_map = store.prior_to_map()
    grant_map = _grants_by_position()
    pending_ids = _pending_release_position_ids()  # one grant read for the whole book
    return {
        "insts": insts,
        "accts": accts,
        "last_known": last_known,
        "prior_map": prior_map,
        "grant_map": grant_map,
        "pending_ids": pending_ids,
        "positions": PositionStore().all(),
    }


def hydrated(
    prices: dict | None = None,
    prev_prices: dict | None = None,
    *,
    extended: dict | None = None,
    _ctx: dict | None = None,
) -> list[dict]:
    """Join every position with its instrument + account into a flat superset row.

    `prices`/`prev_prices` (optional) map price_source_id → live price / previous close.
    Day-change is emitted per row, resolved provider → recorded prior close → None (see
    _resolve_prev), in the instrument's quote currency (consumers apply FX; the % is
    FX-independent). Orphan positions (missing instrument) and fully-disposed holdings
    (quantity <= 0) are skipped — the latter stay on disk for history/realized-P&L lineage
    but aren't a displayed/valued row; a missing account degrades gracefully to blank
    venue fields.

    `extended` (optional): price_source_id → the pre/post-market quote `prices.fetch_live`
    surfaced. Fed into `price` above via `_resolve_price` (an extended print wins over the
    regular-session one when live for a ticker — owner ask 2026-08-31: "use pre/post values
    widely, in everything") — so net worth, unrealized P&L, and `day_change`/`day_change_pct`
    now move with it too, same as `price` itself. `day_change`'s baseline (`prev`, via
    `_resolve_prev`) stays the regular prior close regardless, so "today's change" still
    means today's change. The separate `extended_*`/`is_extended` fields below are for a
    caller that wants to know explicitly whether `price` is an extended print (e.g. to badge
    it), not a different value from `price`.

    `_ctx` (optional, internal): a pre-built `hydration_context()` — skips this call's own
    store reads entirely when a caller already loaded one. Defaults to loading fresh, so
    every other caller's behavior is unchanged."""
    ctx = _ctx or hydration_context()
    insts = ctx["insts"]
    accts = ctx["accts"]
    last_known = ctx["last_known"]
    prior_map = ctx["prior_map"]
    grant_map = ctx["grant_map"]
    pending_ids = ctx["pending_ids"]

    rows: list[dict] = []
    for pos in ctx["positions"]:
        inst = insts.get(pos.get("instrument_id"))
        if not inst:
            continue  # orphan position — nothing to value or display
        qty = float(pos.get("quantity", 0) or 0)
        if qty <= 0:
            continue  # fully-disposed holding — kept on disk for history/realized-P&L
            # lineage, but not a displayed/valued row (qty×price is 0 anyway)
        acct = accts.get(pos.get("account_id")) or {}
        # Resolved ONCE per row, fed into BOTH `price` (via _resolve_price's `quote` param)
        # and the display fields below — not recomputed a second time for the display
        # fields, which is not just wasteful but was the actual bug: a second, independent
        # resolve_price_quote() call here previously omitted `prev_prices`, so
        # `quote.change_pct`/`change_abs` silently came back None (the function's own
        # `if prev else None` guard) — `extended_change_pct` on every held row read as
        # permanently blank regardless of a real live extended print.
        quote = prices_service.resolve_price_quote(
            inst.get("price_source_id") or "",
            prices=prices,
            extended=extended,
            prev_prices=prev_prices,
        )
        price = _resolve_price(inst, prices, last_known, extended, quote=quote)
        prev = _resolve_prev(inst, prev_prices, prior_map)
        cost = pos.get("cost_basis")
        qpu = (
            float(inst.get("quote_per_unit") or 1.0) or 1.0
        )  # value = qty × price × qpu (units layer)
        day_change = (price - prev) * qty * qpu if (prev is not None and price) else None
        day_change_pct = (price / prev - 1) * 100 if (prev not in (None, 0) and price) else None

        rows.append(
            {
                # ── legacy data.csv superset (keeps existing consumers working) ──
                "category": acct.get("institution") or acct.get("name") or "",
                "label": display_name(inst.get("name") or inst.get("symbol") or ""),
                # The two orthogonal axes that replaced the overloaded `bucket`
                # (ADR 2026-07-31-split-position-bucket): entitlement is STORED,
                # reachability is DERIVED. Never collapse them back into one field.
                "vesting_state": vesting_state_of(pos),
                "liquidity": liquidity_of(pos, inst, acct, pending_ids),
                "asset_class": inst.get("asset_class", "equity"),
                "quantity": pos.get("quantity", 0),
                "price": price,
                "cost_basis": cost if cost is not None else 0,  # 0 = unknown, matches CSV semantics
                # quote_currency: the currency in which the market price is denominated.
                # Used by row_base_value for the FX conversion (qty × price × qpu is in
                # quote_currency, not cost_currency).  Exposed separately so callers don't
                # have to know this distinction; the legacy "currency" field is preserved
                # for downstream consumers (it equals cost_currency when present, else
                # quote_currency — used for cost-basis display, not market valuation).
                "quote_currency": inst.get("quote_currency", "USD"),
                "currency": pos.get("cost_currency") or inst.get("quote_currency", "USD"),
                "ticker": _row_ticker(inst),
                "unit": inst.get("unit", ""),
                "quote_per_unit": qpu,
                # ── day-P&L (Pillar 1): quote currency; None = no prior day yet ──
                "prev_price": prev,
                "day_change": day_change,
                "day_change_pct": day_change_pct,
                # Pre/post-market figures, when `quote` (above) is an extended print — note
                # `price` above is already the SAME value as `extended_price` here in that
                # case (see this function's own docstring: extended now wins in the ladder).
                # These stay as their own fields for existing consumers that want them
                # explicitly rather than inferring from `is_extended`.
                "is_extended": quote.is_extended if quote else False,
                "extended_session": quote.extended_session if quote else None,
                "extended_price": quote.price if (quote and quote.is_extended) else None,
                "extended_change_pct": quote.change_pct if (quote and quote.is_extended) else None,
                # ── new identity / metadata fields ──
                "position_id": pos.get("id"),
                "instrument_id": inst.get("id"),
                "account_id": acct.get("id", ""),
                "account_name": acct.get("name", ""),
                "institution": acct.get("institution", ""),
                "symbol": inst.get("symbol", ""),
                "asset_subclass": inst.get("asset_subclass", ""),
                "sector": inst.get("sector", ""),
                "price_source": inst.get("price_source", ""),
                "price_source_id": inst.get("price_source_id", ""),
                **_ref_grant_fields(pos.get("id"), grant_map),
                **_bond_row_fields(inst),
            }
        )
    return rows


def _bond_row_fields(inst: dict) -> dict:
    """Flat fixed-income fields for a bond row (empty dict for non-bonds), so the frontend
    reads them off the row without knowing the nested bond_terms shape. ``coupon_freq`` is
    coupons-per-year (0 = zero-coupon, e.g. a T-bill); ``coupon_rate`` is a fraction."""
    t = fixed_income.terms(inst)
    if not t:
        return {}
    freq_months = t.get("coupon_freq_months")
    coupon_freq = round(12 / freq_months, 4) if freq_months else 0
    return {
        "bond_type": t.get("subtype") or "",
        "face_value": t.get("face_value"),
        "coupon_rate": t.get("coupon_rate_annual"),
        "coupon_freq": coupon_freq,
        "maturity_date": t.get("maturity_date"),
        "issue_date": t.get("issue_date"),
        "days_to_maturity": fixed_income.days_to_maturity(inst),
    }


def _ref_grant_fields(position_id: str | None, grant_map: dict[str, dict]) -> dict:
    """Return ref_grant_* fields for the row when position_id is a grant's locked position."""
    if not position_id:
        return {}
    grant = grant_map.get(position_id)
    if not grant:
        return {}
    tranches = grant.get("tranches") or []
    vest_dates = [t.get("vest_date") for t in tranches if t.get("vest_date")]
    last_vest = max(vest_dates) if vest_dates else None
    return {
        "ref_grant_price": grant.get("grant_price_per_unit"),
        "ref_grant_currency": grant.get("currency") or "",
        "ref_grant_last_vest": last_vest,
    }


# ── Entitlement-aware value split (ADR 2026-07-31-unvested-equity-excluded-from-net-worth) ──


def owned_vs_unvested_base(rows: list[dict], base: str | None = None) -> dict:
    """Σ market value of ``rows`` (already-``hydrated`` position rows) split into
    ``owned_base`` (vesting_state=vested) vs ``unvested_base`` (vesting_state=unvested —
    unvested equity-grant units, forfeited if the grantee leaves, so not owned yet).

    Reuses ``ledger.row_base_value`` and ``ledger.MARKET_ASSET_CLASSES`` — the SAME
    per-row valuation and asset-class gate ``ledger._market_value_base``/
    ``_unvested_value_base`` use for the whole-book net-worth fold — so this split over
    an arbitrary row SUBSET (e.g. get_portfolio's filtered or unfiltered selection)
    reconciles exactly with get_net_worth's ``net_worth``/``unvested_base`` when given
    every position. A row whose asset_class isn't market-valued (cash/none) counts in
    neither total, mirroring ledger's own fold rather than silently including it."""
    from backend.core.fx import base_currency as _base_currency
    from backend.services.fx_cache import load_fx_cache
    from backend.services.ledger import MARKET_ASSET_CLASSES, row_base_value

    base = (base or _base_currency()).upper()
    rates = load_fx_cache()  # once per fold — to_base(rates=None) reloads FX+profile per row
    owned = 0.0
    unvested = 0.0
    for row in rows:
        if row.get("asset_class") not in MARKET_ASSET_CLASSES:
            continue
        value = row_base_value(row, base, rates=rates)
        if row.get("vesting_state") == "unvested":
            unvested += value
        else:
            owned += value
    return {
        "owned_base": round(owned, 2),
        "unvested_base": round(unvested, 2),
        "base_currency": base,
    }
