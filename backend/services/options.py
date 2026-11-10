"""options — derivative overlay service (sys-options-data-model RFC).

The single home for option monetary math, the signed ITM/OTM lens, and the
``apply_option_change`` write choke point. Mirrors ``services/fixed_income.py``
(the bonds analog): pure functions for mark math, a lazy-import lens (``summary``),
and a deterministic lifecycle sweep (``option_expiry_sweep``).

Scope: covered-call/CSP overlays + defined-risk longs. Greeks, margin mechanics
beyond collateral reference, rolling, and assignment simulation are DEFERRED.

Key invariants enforced here:
  - All monetary math in services/ — never routers or tools.
  - ``realized_premium_base`` and any mark-in-base is RECOMPUTED on every read
    from native premium × current FX; stored snapshot never trusted.
  - Write choke point never bypassed: every mutation goes through
    ``apply_option_change``; every premium cash leg through
    ``ledger_service.create_transaction``.
  - Money movement always confirms — no silent premium/collateral transfer.
  - Short contracts contribute NEGATIVE mark to net worth (the liability side).

Services layer: imports db + core only (and sibling services via lazy import
to avoid cycles — same pattern as fixed_income.summary).
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import Connection

from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.option_contract_store import OptionContractStore
from backend.db.tables import option_contracts as option_contracts_tbl
from backend.db.tables import transactions as txns_tbl
from backend.db.tables import txn_audit as txn_audit_tbl

logger = logging.getLogger(__name__)

# ── Pure mark functions ────────────────────────────────────────────────────────


def intrinsic_per_share(contract: dict, underlying_price: float | None) -> float:
    """Intrinsic value per share in quote_currency (always ≥ 0).

    v1 = intrinsic only (no time/extrinsic value — IV/chain feed is deferred).
    call: max(underlying_price - strike, 0)
    put:  max(strike - underlying_price, 0)
    None underlying_price → 0 (caller must guard on this before using for NW fold)."""
    if underlying_price is None:
        return 0.0
    strike = float(contract.get("strike") or 0)
    option_type = (contract.get("option_type") or "call").lower()
    if option_type == "call":
        return max(underlying_price - strike, 0.0)
    return max(strike - underlying_price, 0.0)


def moneyness(contract: dict, underlying_price: float | None) -> str:
    """Derive the ITM/OTM/ATM status from the underlying price vs strike.

    Returns "ITM" | "OTM" | "ATM" | "unknown" (when price is None).
    Never stored — always derived on read."""
    if underlying_price is None:
        return "unknown"
    strike = float(contract.get("strike") or 0)
    option_type = (contract.get("option_type") or "call").lower()
    if option_type == "call":
        if underlying_price > strike:
            return "ITM"
        if underlying_price < strike:
            return "OTM"
        return "ATM"
    # put
    if underlying_price < strike:
        return "ITM"
    if underlying_price > strike:
        return "OTM"
    return "ATM"


def mark_value_base(
    contract: dict,
    underlying_price: float | None,
    as_of: date | None,
    base: str,
    rates: dict,
) -> float | None:
    """SIGNED holding value in base currency.

    Returns None when the underlying price is unknown (no opinion — the UI shows
    "—" rather than a fabricated 0). Returns 0 for expired/closed contracts.

    Long → positive (asset). Short → negative (liability: a written ITM option
    is a money-owed obligation). Multiplier + contracts applied once here."""
    status = (contract.get("status") or "open").lower()
    if status != "open":
        return 0.0

    if underlying_price is None:
        return None

    expiry_str = contract.get("expiry") or ""
    as_of = as_of or user_today()
    if expiry_str:
        try:
            exp = datetime.fromisoformat(expiry_str[:10]).date()
            if as_of > exp:
                return 0.0
        except (TypeError, ValueError):
            pass

    intrinsic = intrinsic_per_share(contract, underlying_price)
    contracts = float(contract.get("contracts") or 0)
    multiplier = int(contract.get("multiplier") or 100)
    gross = intrinsic * contracts * multiplier  # in quote_currency

    # FX into base — use quote_currency (the currency the underlying is priced in),
    # not premium_currency (the currency of the cash premium leg). For non-USD
    # underlyings these differ; quote_currency defaults to premium_currency for
    # back-compat with contracts created before this field was added.
    from backend.core.fx import to_base

    value_base = to_base(gross, _quote_currency(contract), base, rates=rates)

    side = (contract.get("side") or "long").lower()
    return round(-value_base if side == "short" else value_base, 2)


# ── Premium / collateral helpers ─────────────────────────────────────────────


def _quote_currency(contract: dict) -> str:
    """Currency the underlying (and therefore strike/intrinsic/collateral) is
    denominated in. Defaults to premium_currency for pre-field contracts."""
    return (contract.get("quote_currency") or contract.get("premium_currency") or "USD").upper()


def total_premium_native(contract: dict) -> float:
    """Total cash amount of the premium leg in premium_currency.

    OCC convention: premium_per_share is per SHARE, not per contract.
    Total = contracts × multiplier × premium_per_share."""
    contracts = float(contract.get("contracts") or 0)
    multiplier = int(contract.get("multiplier") or 100)
    pps = float(contract.get("premium_per_share") or 0)
    return round(contracts * multiplier * pps, 6)


def csp_collateral_native(contract: dict) -> float | None:
    """CSP (short put) required collateral = strike × contracts × multiplier.

    Returns None for non-CSP contracts (covered calls use share collateral)."""
    if (contract.get("strategy_tag") or "").lower() != "csp":
        return None
    strike = float(contract.get("strike") or 0)
    contracts = float(contract.get("contracts") or 0)
    multiplier = int(contract.get("multiplier") or 100)
    return round(strike * contracts * multiplier, 2)


# ── Per-contract hydration ────────────────────────────────────────────────────


def _days_to_expiry(contract: dict, as_of: date | None = None) -> int | None:
    expiry_str = contract.get("expiry") or ""
    if not expiry_str:
        return None
    try:
        exp = datetime.fromisoformat(expiry_str[:10]).date()
        return (exp - (as_of or user_today())).days
    except (TypeError, ValueError):
        return None


def _hydrate_contract(
    contract: dict,
    underlying_price: float | None,
    as_of: date | None,
    base: str,
    rates: dict,
) -> dict:
    """Add derived fields (moneyness, mark_value_base, days_to_expiry, intrinsic)."""
    dte = _days_to_expiry(contract, as_of)
    intr = intrinsic_per_share(contract, underlying_price)
    mvb = mark_value_base(contract, underlying_price, as_of, base, rates)
    return {
        **contract,
        "moneyness": moneyness(contract, underlying_price),
        "underlying_price": underlying_price,
        "intrinsic_per_share": intr,
        "mark_value_base": mvb,
        "days_to_expiry": dte,
        "total_premium_native": total_premium_native(contract),
        "collateral_amount_required": csp_collateral_native(contract),
        "base_currency": base,
    }


# ── Covered-call collateral coverage check ───────────────────────────────────


def _check_covered_call(contract: dict, positions_by_instrument: dict) -> str:
    """Return "covered" | "uncovered" | "unknown" for a short call.

    Looks up how many shares the collateral_account_id holds of the underlying
    and checks ≥ contracts × multiplier. "unknown" when collateral_account_id
    is absent (the operator hasn't linked it yet)."""
    if (contract.get("side") or "") != "short":
        return "n/a"
    if (contract.get("option_type") or "") != "call":
        return "n/a"
    coll_acct = contract.get("collateral_account_id")
    if not coll_acct:
        return "unknown"
    underlying_id = contract.get("underlying_instrument_id") or ""
    key = (underlying_id, coll_acct)
    held_qty = float(positions_by_instrument.get(key) or 0)
    required = float(contract.get("contracts") or 0) * int(contract.get("multiplier") or 100)
    return "covered" if held_qty >= required else "uncovered"


# ── Lens (summary) ────────────────────────────────────────────────────────────


def get_contract(contract_id: str) -> dict | None:
    """Return one hydrated contract (mark, moneyness, days_to_expiry, intrinsic) or None."""
    from backend.core.fx import base_currency
    from backend.services import lots as lots_service

    store = OptionContractStore()
    contract = store.find_by_id(contract_id)
    if not contract:
        return None
    base = base_currency().upper()
    rates = lots_service._load_rates()
    underlying_price = _resolve_underlying_price(contract)
    as_of = user_today()
    return _hydrate_contract(contract, underlying_price, as_of, base, rates)


def _resolve_underlying_price(contract: dict) -> float | None:
    """Look up the last-known price of the underlying instrument.

    Uses the existing InstrumentStore + the price ladder already maintained by
    services/prices — no new network call. Returns None when unknown."""
    underlying_id = contract.get("underlying_instrument_id") or ""
    if not underlying_id:
        return None
    try:
        from backend.db.instrument_store import InstrumentStore

        inst = InstrumentStore().find_by_id(underlying_id)
        if not inst:
            return None
        # Prefer manual_price if price_source=manual.
        if inst.get("price_source") == "manual" and inst.get("manual_price") is not None:
            return float(inst["manual_price"])
        # Fall back to price history (last cached price).
        price_source_id = inst.get("price_source_id") or ""
        if not price_source_id:
            return None
        from backend.db.price_history_store import PriceHistoryStore

        point = PriceHistoryStore().latest(price_source_id)
        if point is not None and point.get("price") is not None:
            return float(point["price"])
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "could not resolve underlying price for contract %s: %s",
            contract.get("id"),
            type(exc).__name__,
        )
    return None


def summary(as_of: date | None = None, base: str | None = None) -> dict:
    """Options lens: every open contract valued/signed in base currency.

    Aggregates by strategy_tag, builds expiry buckets (≤7d/≤30d/≤90d), checks
    covered-call coverage, totals encumbered CSP cash. A malformed contract
    degrades to mark_value_base=None for that row only — never fails the whole
    response. Mirrors ``fixed_income.summary``."""
    from backend.core.fx import base_currency, to_base
    from backend.services import lots as lots_service
    from backend.services import positions as positions_service

    as_of = as_of or user_today()
    base = (base or base_currency()).upper()
    rates = lots_service._load_rates()

    store = OptionContractStore()
    contracts = store.all()

    # Build a {(instrument_id, account_id): qty} map for covered-call checks.
    positions_by_instrument: dict[tuple, float] = {}
    try:
        for p in positions_service.hydrated():
            key = (p.get("instrument_id") or "", p.get("account_id") or "")
            positions_by_instrument[key] = float(p.get("quantity") or 0)
    except Exception:
        pass

    holdings: list[dict] = []
    by_strategy: dict[str, float] = {}
    buckets = {"d7": 0.0, "d30": 0.0, "d90": 0.0}
    total_mark_base = 0.0
    total_encumbered_cash = 0.0
    uncovered_count = 0
    open_count = 0

    for c in contracts:
        underlying_price = _resolve_underlying_price(c)
        try:
            h = _hydrate_contract(c, underlying_price, as_of, base, rates)
        except Exception:
            logger.exception("options summary: malformed contract %s skipped", c.get("id"))
            h = {
                **c,
                "moneyness": "unknown",
                "mark_value_base": None,
                "days_to_expiry": None,
                "intrinsic_per_share": 0.0,
            }

        status = (c.get("status") or "open").lower()
        mvb = h.get("mark_value_base")
        dte = h.get("days_to_expiry")
        tag = c.get("strategy_tag") or ""

        # This lens is OPEN-ONLY (see the docstring). A closed/expired/assigned contract
        # has no live mark, no encumbered collateral and no coverage question. The mark /
        # bucket / collateral folds below were each individually status-guarded, but
        # holdings.append and the uncovered tally sat OUTSIDE those guards — so the lens
        # shipped every dead contract it had ever booked, and counted them as uncovered.
        if status != "open":
            continue

        open_count += 1
        if mvb is not None:
            total_mark_base = round(total_mark_base + mvb, 2)
            by_strategy[tag] = round(by_strategy.get(tag, 0.0) + mvb, 2)
        if dte is not None and dte >= 0 and mvb is not None:
            if dte <= 7:
                buckets["d7"] = round(buckets["d7"] + mvb, 2)
            if dte <= 30:
                buckets["d30"] = round(buckets["d30"] + mvb, 2)
            if dte <= 90:
                buckets["d90"] = round(buckets["d90"] + mvb, 2)

        # CSP encumbered cash.
        if (c.get("strategy_tag") or "").lower() == "csp":
            coll = csp_collateral_native(c)
            if coll is not None:
                # Collateral is strike-denominated → the underlying's quote currency,
                # NOT premium_currency (they differ on cross-currency contracts).
                total_encumbered_cash += to_base(coll, _quote_currency(c), base, rates=rates)

        # Covered-call check.
        coverage = _check_covered_call(c, positions_by_instrument)
        if coverage in ("uncovered", "unknown"):
            uncovered_count += 1

        holdings.append({**h, "coverage": coverage})

    return {
        "base_currency": base,
        "total_mark_base": round(total_mark_base, 2),
        "open_count": open_count,
        "holdings": holdings,
        "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
        "expiry_buckets": {k: round(v, 2) for k, v in buckets.items()},
        "total_encumbered_cash_base": round(total_encumbered_cash, 2),
        "uncovered_call_count": uncovered_count,
    }


# strategy_tag → OptionStrategyGroup (matches frontend OptionStrategyGroup type)
_STRATEGY_GROUP: dict[str, str] = {
    "covered_call": "income",
    "csp": "income",
    "long_call": "hedge",
    "long_put": "hedge",
    "protective": "hedge",
}


def book(as_of: date | None = None, base: str | None = None) -> dict:
    """Frontend OptionsBook contract: {base_currency, summary, positions}.

    Returns ONLY open contracts, shaped to the OptionContract frontend type.
    Empty book returns zeros in summary and positions=[]; never crashes."""
    from backend.core.fx import base_currency, to_base
    from backend.db.instrument_store import InstrumentStore
    from backend.services import lots as lots_service

    as_of = as_of or user_today()
    base = (base or base_currency()).upper()
    rates = lots_service._load_rates()

    store = OptionContractStore()
    inst_store = InstrumentStore()
    contracts = store.all()

    sym_cache: dict[str, str] = {}

    def _sym(uid: str) -> str:
        if uid not in sym_cache:
            inst = inst_store.find_by_id(uid) if uid else None
            sym_cache[uid] = (inst or {}).get("symbol") or uid
        return sym_cache[uid]

    positions: list[dict] = []
    premium_collected = 0.0
    capital_committed = 0.0
    nearest: dict | None = None

    for c in contracts:
        if (c.get("status") or "open").lower() != "open":
            continue

        underlying_price = _resolve_underlying_price(c)
        try:
            h = _hydrate_contract(c, underlying_price, as_of, base, rates)
        except Exception:
            logger.exception("options book: malformed contract %s skipped", c.get("id"))
            continue

        dte = h.get("days_to_expiry")
        mvb = float(h.get("mark_value_base") or 0.0)
        side = (c.get("side") or "long").lower()
        tag = (c.get("strategy_tag") or "").lower()
        uid = c.get("underlying_instrument_id") or ""
        symbol = _sym(uid)

        # Premium in base: signed positive for short (received), negative for long (paid).
        tpn = float(h.get("total_premium_native") or 0.0)
        prem_currency = (c.get("premium_currency") or "USD").upper()
        prem_base = to_base(abs(tpn), prem_currency, base, rates=rates)
        prem_signed = round(prem_base if side == "short" else -prem_base, 2)
        if prem_signed > 0:
            premium_collected = round(premium_collected + prem_signed, 2)

        # Collateral in base: CSP uses cash (strike × size); covered calls use share value.
        # Strike-denominated → quote currency, not the premium leg's currency.
        coll_native = csp_collateral_native(c) or 0.0
        coll_base = round(to_base(coll_native, _quote_currency(c), base, rates=rates), 2)
        capital_committed = round(capital_committed + coll_base, 2)

        mny = h.get("moneyness") or "OTM"
        status_str = "ITM" if mny == "ITM" else "OTM"
        strategy_group = _STRATEGY_GROUP.get(tag, "hedge")

        if dte is not None and dte >= 0:
            if nearest is None or dte < nearest["dte"]:
                nearest = {"dte": dte, "expiry": c.get("expiry"), "symbol": symbol}

        positions.append(
            {
                "id": c.get("id") or "",
                "option_type": (c.get("option_type") or "call").upper(),
                "side": side,
                "underlying_symbol": symbol,
                "strategy_tag": c.get("strategy_tag") or "",
                "strategy_group": strategy_group,
                "contracts": float(c.get("contracts") or 0),
                "strike": float(c.get("strike") or 0),
                "expiry": c.get("expiry") or "",
                "dte": dte if dte is not None else 0,
                "premium": prem_signed,
                "mark": mvb,
                "status": status_str,
                "collateral": coll_base,
                "note": c.get("note") or "",
            }
        )

    return {
        "base_currency": base,
        "summary": {
            "open_positions": len(positions),
            "premium_collected": round(premium_collected, 2),
            "capital_committed": round(capital_committed, 2),
            "nearest_expiry_date": nearest["expiry"] if nearest else None,
            "nearest_expiry_dte": nearest["dte"] if nearest else None,
            "nearest_expiry_underlying": nearest["symbol"] if nearest else None,
        },
        "positions": positions,
    }


# ── Expiry sweep ──────────────────────────────────────────────────────────────


def realized_outcomes(
    date_from: str,
    date_to: str,
    base: str | None = None,
    rates: dict | None = None,
) -> dict:
    """Realized option outcomes for a window — the tax look-back over contract FACTS.

    Derived on every read from the contract store (never stored): a contract whose
    ``closed_at`` falls in [date_from, date_to] and whose status is ``closed`` or
    ``expired`` realizes its premium:

      short: premium received − buyback cost   (expired → buyback 0, fully kept)
      long:  sale proceeds − premium paid      (expired → proceeds 0, fully lost)

    A ``closed`` contract with NO recorded close price realizes ``None`` — the price
    is genuinely unknown, and unknown is never fabricated as 0 (the lots
    unknown-basis invariant). ``assigned``/``exercised`` are surfaced as a
    ``deferred_count`` only (their share legs belong to the lot spine).

    Base figures are RECOMPUTED from the native premium × current FX at read time —
    a base-currency change can never stale this report. NO tax verdict is applied
    here: jurisdiction treatment lives in the tax report layer."""
    from backend.core.fx import base_currency, to_base
    from backend.services import lots as lots_service

    base = (base or base_currency()).upper()
    rates = rates if rates is not None else lots_service._load_rates()

    rows: list[dict] = []
    net_realized_base = 0.0
    incomplete = 0
    deferred = 0

    for c in OptionContractStore().all():
        status = (c.get("status") or "open").lower()
        closed_at = (c.get("closed_at") or "")[:10]
        if status in ("assigned", "exercised"):
            if closed_at and date_from <= closed_at <= date_to:
                deferred += 1
            continue
        if status not in ("closed", "expired"):
            continue
        if not closed_at or not (date_from <= closed_at <= date_to):
            continue

        contracts = float(c.get("contracts") or 0)
        multiplier = int(c.get("multiplier") or 100)
        currency = (c.get("premium_currency") or "USD").upper()
        side = (c.get("side") or "long").lower()
        premium_total = round(contracts * multiplier * float(c.get("premium_per_share") or 0), 6)

        close_pps = c.get("close_premium_per_share")
        if status == "expired":
            close_total: float | None = 0.0
        elif close_pps in (None, ""):
            close_total = None
        else:
            close_total = round(contracts * multiplier * float(close_pps), 6)

        if close_total is None:
            realized_native: float | None = None
            realized_base: float | None = None
            incomplete += 1
        else:
            realized_native = round(
                (premium_total - close_total) if side == "short" else (close_total - premium_total),
                6,
            )
            realized_base = round(to_base(realized_native, currency, base, rates=rates), 2)
            net_realized_base = round(net_realized_base + realized_base, 2)

        underlying_symbol = ""
        try:
            from backend.db.instrument_store import InstrumentStore

            inst = InstrumentStore().find_by_id(c.get("underlying_instrument_id") or "")
            underlying_symbol = (inst or {}).get("symbol") or ""
        except Exception:
            pass

        rows.append(
            {
                "contract_id": c.get("id"),
                "underlying_symbol": underlying_symbol,
                "option_type": c.get("option_type"),
                "side": side,
                "strike": c.get("strike"),
                "expiry": c.get("expiry"),
                "open_date": c.get("open_date"),
                "closed_at": closed_at,
                "status": status,
                "contracts": contracts,
                "multiplier": multiplier,
                "premium_currency": currency,
                "premium_total_native": premium_total,
                "close_total_native": close_total,
                "realized_native": realized_native,
                "realized_base": realized_base,
                "complete": close_total is not None,
            }
        )

    rows.sort(key=lambda r: (r["closed_at"], r["contract_id"] or ""), reverse=True)
    return {
        "base_currency": base,
        "rows": rows,
        "contract_count": len(rows),
        "incomplete_count": incomplete,
        "deferred_count": deferred,
        "net_realized_base": round(net_realized_base, 2),
    }


def run_expiry_sweep(as_of: date | None = None) -> dict:
    """Deterministic lifecycle sweep: open contracts past expiry → "expired".

    ITM-at-expiry contracts are flagged for manual handling (assignment/exercise
    automation is deferred). Called by the ``option_expiry_sweep`` scheduled task
    and also available for test injection. No LLM, no money movement."""
    as_of = as_of or user_today()
    store = OptionContractStore()
    flipped: list[str] = []
    itm_flagged: list[str] = []

    for c in store.query_contracts(status="open"):
        expiry_str = c.get("expiry") or ""
        if not expiry_str:
            continue
        try:
            exp = datetime.fromisoformat(expiry_str[:10]).date()
        except (TypeError, ValueError):
            continue
        if as_of <= exp:
            continue

        underlying_price = _resolve_underlying_price(c)
        itm = moneyness(c, underlying_price) == "ITM"

        store.update_contract(
            c["id"],
            {
                "status": "expired",
                "closed_at": as_of.isoformat(),
            },
        )
        flipped.append(c["id"])
        if itm:
            itm_flagged.append(c["id"])

    return {
        "ok": True,
        "expired": len(flipped),
        "itm_flagged": itm_flagged,
    }


# ── Write choke point ─────────────────────────────────────────────────────────


def apply_option_change(change: dict) -> dict:
    """Single write entrypoint for all option contract mutations.

    change["type"] ∈ {"open", "close", "expire", "update", "delete"}.

    open:
      - Validates required fields (type/side/strike/expiry/contracts>0).
      - IDEMPOTENT on change["idempotency_key"]: second call with same key → no-op.
      - Double-book guard: same (account, underlying, type, side, strike, expiry,
        contracts) signature already OPEN → REFUSED unless allow_duplicate=true.
      - If premium_account_id is supplied AND caller asks for premium booking,
        bridges the cash leg through ledger_service (confirmed, FX-converted).

    close / expire / update / delete:
      See inline docs below. All transitions respect the "never-bypass" rule."""
    op = (change.get("type") or "").lower()
    if op == "open":
        return _apply_open(change)
    if op == "close":
        return _apply_close(change)
    if op == "expire":
        return _apply_expire(change)
    if op == "update":
        return _apply_update(change)
    if op == "delete":
        return _apply_delete(change)
    return {"error": f"unknown option change type {op!r}"}


def _validate_open(change: dict) -> str | None:
    """Return an error string or None if valid."""
    for field in ("option_type", "side", "strike", "expiry", "contracts"):
        if not change.get(field) and change.get(field) != 0:
            return f"missing required field: {field}"
    if float(change.get("contracts") or 0) <= 0:
        return "contracts must be > 0"
    if float(change.get("strike") or 0) <= 0:
        return "strike must be > 0"
    if not change.get("account_id"):
        return "account_id is required"
    if not change.get("underlying_instrument_id"):
        return "underlying_instrument_id is required"
    return None


def _apply_open(change: dict) -> dict:
    err = _validate_open(change)
    if err:
        return {"error": err}

    store = OptionContractStore()

    # Idempotency: same broker order id → no-op.
    idem_key = change.get("idempotency_key")
    if idem_key:
        existing = store.find_by_idempotency_key(idem_key)
        if existing:
            return {"ok": True, "contract_id": existing["id"], "idempotent": True}

    # Double-book guard: open contract with same signature.
    if not change.get("allow_duplicate"):
        dup = _find_duplicate_open(store, change)
        if dup:
            return {
                "error": (
                    f"an open contract with the same signature already exists "
                    f"(contract_id={dup['id']}, {dup.get('expiry')}, "
                    f"{dup.get('contracts')} × {dup.get('option_type')} "
                    f"@ strike {dup.get('strike')} — this looks already booked. "
                    "Only if these are truly separate fills, re-apply with allow_duplicate=true."
                ),
                "existing_contract_id": dup["id"],
            }

    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    if not AccountStore().find_by_id(change.get("account_id", "")):
        return {"error": "account not found"}
    if not InstrumentStore().find_by_id(change.get("underlying_instrument_id", "")):
        return {"error": "underlying instrument not found"}
    # Fail CLOSED on a phantom wallet: ledger's income/expense path never checks
    # account existence, so an unresolved id would book an orphaned cash leg.
    if change.get("premium_account_id") and not AccountStore().find_by_id(
        change["premium_account_id"]
    ):
        return {"error": "premium account not found"}
    if change.get("collateral_account_id") and not AccountStore().find_by_id(
        change["collateral_account_id"]
    ):
        return {"error": "collateral account not found"}

    # TASK 4 (storage plan 4b): the contract insert + the premium cash leg now commit as
    # ONE transaction — a failed premium leg used to leave an ORPHANED contract (created,
    # no cash leg) since _maybe_book_premium_leg re-raises past this function uncaught;
    # now the whole open rolls back. txn_audit is locked alongside `transactions` for the
    # same reason every other Task 4 choke locks it (create_transaction, composed via
    # conn=, writes both as one unit).
    with locked_unit_of_work(option_contracts_tbl, txns_tbl, txn_audit_tbl) as conn:
        contract = store.create_contract(
            {
                "account_id": change["account_id"],
                "underlying_instrument_id": change["underlying_instrument_id"],
                "option_type": change["option_type"],
                "side": change["side"],
                "strike": change["strike"],
                "expiry": change["expiry"],
                "contracts": change["contracts"],
                "multiplier": change.get("multiplier", 100),
                "premium_per_share": change.get("premium_per_share", 0.0),
                "premium_currency": change.get("premium_currency", "USD"),
                "quote_currency": change.get("quote_currency"),
                "open_date": change.get("open_date"),
                "collateral_account_id": change.get("collateral_account_id"),
                "collateral_amount": change.get("collateral_amount"),
                "strategy_tag": change.get("strategy_tag", ""),
                "idempotency_key": idem_key,
                "note": change.get("note", ""),
            },
            conn=conn,
        )
        _maybe_book_premium_leg(contract, change, conn=conn)

    _seed_expiry_sweep_if_needed()

    return {"ok": True, "contract_id": contract["id"]}


def _find_duplicate_open(store: OptionContractStore, change: dict) -> dict | None:
    """Find an existing open contract with the same economic signature."""
    for c in store.query_contracts(
        account_id=change.get("account_id"),
        status="open",
        underlying_instrument_id=change.get("underlying_instrument_id"),
    ):
        if (
            c.get("option_type") == change.get("option_type")
            and c.get("side") == change.get("side")
            and abs(float(c.get("strike") or 0) - float(change.get("strike") or 0)) < 1e-9
            and c.get("expiry") == change.get("expiry")
            and abs(float(c.get("contracts") or 0) - float(change.get("contracts") or 0)) < 1e-9
        ):
            return c
    return None


def _maybe_book_premium_leg(
    contract: dict, change: dict, *, conn: Connection | None = None
) -> None:
    """Bridge the cash premium leg through ledger_service when caller supplies
    premium_account_id. Confirmed, FX-converted, never silent.

    Omitted → no leg (the "money movement always confirms" posture).

    ``conn``, when given, composes the ledger write into a caller's own open transaction
    (Task 4, storage plan 4b). ``conn=None`` is byte-identical to before."""
    premium_account_id = change.get("premium_account_id")
    if not premium_account_id:
        return
    total_premium = total_premium_native(contract)
    if total_premium == 0:
        return
    try:
        from backend.services import ledger as ledger_service

        side = (contract.get("side") or "long").lower()
        currency = (contract.get("premium_currency") or "USD").upper()
        description = (
            f"Option premium — {contract.get('option_type', '').upper()} "
            f"@ {contract.get('strike')} exp {contract.get('expiry')}"
        )
        if side == "short":
            # Received premium → income to the cash account. The write path records FACTS
            # ONLY (ADR 2026-07-12, options-tax-facts-not-verdicts); the deterministic
            # provenance source is the authoritative marker that keeps these legs out of the
            # employment-income bucket.
            ledger_service.create_transaction(
                {
                    "date": contract.get("open_date") or _today_str(),
                    "kind": "income",
                    "description": description,
                    "to_account_id": premium_account_id,
                    "to_amount": total_premium,
                    "to_currency": currency,
                    "external_refs": [{"source": "option-premium", "ref": contract["id"]}],
                },
                allow_duplicate=bool(change.get("allow_duplicate")),
                conn=conn,
            )
        else:
            # Paid premium → expense from the cash account (no taxability concept on an
            # expense; no category either — same decision #3).
            ledger_service.create_transaction(
                {
                    "date": contract.get("open_date") or _today_str(),
                    "kind": "expense",
                    "description": description,
                    "from_account_id": premium_account_id,
                    "from_amount": total_premium,
                    "from_currency": currency,
                    "external_refs": [{"source": "option-premium", "ref": contract["id"]}],
                },
                allow_duplicate=bool(change.get("allow_duplicate")),
                conn=conn,
            )
    except Exception:
        logger.exception("option premium leg booking failed for contract %s", contract.get("id"))
        raise


def _today_str() -> str:
    return user_today().isoformat()


def _maybe_book_close_premium_leg(
    contract: dict, change: dict, *, conn: Connection | None = None
) -> None:
    """Book the buy-to-close / sell-to-close cash leg through ledger_service.

    Mirrors _maybe_book_premium_leg: confirmed, FX-converted, never silent.
    Short → expense (buying the contract back); long → income.
    Omitted fields / zero amount → no leg.

    ``conn``, when given, composes the ledger write into a caller's own open transaction
    (Task 4, storage plan 4b). ``conn=None`` is byte-identical to before."""
    premium_account_id = change.get("premium_account_id")
    pps = change.get("close_premium_per_share")
    if not premium_account_id or pps in (None, ""):
        return
    total = round(
        float(contract.get("contracts") or 0) * int(contract.get("multiplier") or 100) * float(pps),
        6,
    )
    if total == 0:
        return
    from backend.services import ledger as ledger_service

    side = (contract.get("side") or "long").lower()
    currency = (contract.get("premium_currency") or "USD").upper()
    description = (
        f"Option close — {contract.get('option_type', '').upper()} "
        f"@ {contract.get('strike')} exp {contract.get('expiry')}"
    )
    closed_at = change.get("closed_at") or _today_str()
    if side == "short":
        ledger_service.create_transaction(
            {
                "date": closed_at,
                "kind": "expense",
                "description": description,
                "from_account_id": premium_account_id,
                "from_amount": total,
                "from_currency": currency,
                "external_refs": [{"source": "option-close", "ref": contract["id"]}],
            },
            conn=conn,
        )
    else:
        ledger_service.create_transaction(
            {
                "date": closed_at,
                "kind": "income",
                "description": description,
                "to_account_id": premium_account_id,
                "to_amount": total,
                "to_currency": currency,
                "external_refs": [{"source": "option-close", "ref": contract["id"]}],
            },
            conn=conn,
        )


def _apply_close(change: dict) -> dict:
    """Set status='closed', closed_at. Idempotent if already closed.

    When close_premium_per_share + premium_account_id are both supplied, books
    the buyback/sell-to-close cash leg FIRST — a failed leg leaves the contract
    open and retryable (the "option-close" provenance entry keeps it traceable)."""
    contract_id = change.get("contract_id") or change.get("id")
    if not contract_id:
        return {"error": "contract_id is required"}
    store = OptionContractStore()
    contract = store.find_by_id(contract_id)
    if not contract:
        return {"error": "contract not found"}
    if (contract.get("status") or "open") == "closed":
        return {"ok": True, "contract_id": contract_id, "idempotent": True}
    if change.get("premium_account_id") and change.get("close_premium_per_share") not in (None, ""):
        from backend.db.account_store import AccountStore

        if not AccountStore().find_by_id(change["premium_account_id"]):
            return {"error": "premium account not found"}

    patch = {
        "status": "closed",
        "closed_at": change.get("closed_at") or _today_str(),
    }
    if change.get("close_premium_per_share") not in (None, ""):
        patch["close_premium_per_share"] = float(change["close_premium_per_share"])

    # TASK 4 (storage plan 4b): the close leg + the status flip now commit as ONE
    # transaction. The try/except wraps the WHOLE `with` block (never just the leg call
    # alone) so an exception rolls back through locked_unit_of_work's exceptional exit
    # BEFORE being caught here — store.update_contract must never appear to have run when
    # the leg failed, and catching INSIDE the `with` would instead let it exit cleanly
    # (via `return`) and COMMIT whatever partial write the leg made. This preserves the
    # "contract left open and retryable" contract exactly, now backed by a real rollback
    # instead of just "the next write statement never happened to run".
    try:
        with locked_unit_of_work(option_contracts_tbl, txns_tbl, txn_audit_tbl) as conn:
            _maybe_book_close_premium_leg(contract, change, conn=conn)
            store.update_contract(contract_id, patch, conn=conn)
    except Exception as exc:
        logger.exception("option close leg booking failed for %s", contract_id)
        detail = getattr(exc, "message", None) or str(exc)
        return {"error": f"close cash leg failed to book — contract left open ({detail})"}
    return {"ok": True, "contract_id": contract_id}


def _apply_expire(change: dict) -> dict:
    """Deterministic lifecycle flip: all open past-expiry contracts → "expired".

    Called by the scheduled ``option_expiry_sweep`` task. Can also target a
    single contract when change["contract_id"] is supplied."""
    contract_id = change.get("contract_id") or change.get("id")
    as_of_str = change.get("as_of") or _today_str()
    try:
        as_of = datetime.fromisoformat(as_of_str[:10]).date()
    except (TypeError, ValueError):
        as_of = user_today()

    if contract_id:
        store = OptionContractStore()
        c = store.find_by_id(contract_id)
        if not c:
            return {"error": "contract not found"}
        if (c.get("status") or "open") != "open":
            return {
                "error": f"contract status is '{c.get('status')}' — only open contracts can be expired"
            }
        store.update_contract(contract_id, {"status": "expired", "closed_at": as_of.isoformat()})
        underlying_price = _resolve_underlying_price(c)
        itm = moneyness(c, underlying_price) == "ITM"
        return {
            "ok": True,
            "expired": 1,
            "itm_flagged": [contract_id] if itm else [],
            "contract_id": contract_id,
        }

    return run_expiry_sweep(as_of)


def _apply_update(change: dict) -> dict:
    """Edit metadata (note, strategy_tag, collateral linkage). NEVER edits a derived mark."""
    contract_id = change.get("contract_id") or change.get("id")
    if not contract_id:
        return {"error": "contract_id is required"}
    store = OptionContractStore()
    if not store.find_by_id(contract_id):
        return {"error": "contract not found"}
    allowed = {"note", "strategy_tag", "collateral_account_id", "collateral_amount", "open_date"}
    patch = {k: v for k, v in change.items() if k in allowed}
    if not patch:
        return {"ok": True, "contract_id": contract_id, "no_change": True}
    if patch.get("collateral_account_id"):
        # Same check open applies — the covered-call test keys on this account, so an
        # unchecked update could flip a contract's coverage (2026-10-13 audit). A name
        # resolves to the id stored; None/"" still clears the link.
        from backend.core.errors import ValidationError
        from backend.services.accounts import require_account_id

        try:
            patch["collateral_account_id"] = require_account_id(
                patch["collateral_account_id"], field="collateral_account_id"
            )
        except ValidationError as exc:
            return {"error": exc.message or str(exc)}
    store.update_contract(contract_id, patch)
    return {"ok": True, "contract_id": contract_id}


def _apply_delete(change: dict) -> dict:
    """Remove a contract (mistaken entry). Only allowed while status='open' unless forced."""
    contract_id = change.get("contract_id") or change.get("id")
    if not contract_id:
        return {"error": "contract_id is required"}
    store = OptionContractStore()
    contract = store.find_by_id(contract_id)
    if not contract:
        return {"error": "contract not found"}
    status = (contract.get("status") or "open").lower()
    if status != "open" and not change.get("force"):
        return {
            "error": (
                f"contract status is '{status}' — only open contracts can be deleted. "
                "Pass force=true to delete a non-open contract."
            )
        }
    store.delete(contract_id)
    return {"ok": True}


# ── Sweep task seeding ────────────────────────────────────────────────────────

EXPIRY_SWEEP_TITLE = "Option expiry sweep"


def seed_expiry_sweep(*, time: str = "08:00") -> bool:
    """Seed the daily option-expiry Automation on first contract creation.

    Idempotent and best-effort."""
    try:
        from backend.services.automations.service import create_system_automation

        _, created = create_system_automation(
            {
                "name": EXPIRY_SWEEP_TITLE,
                "description": "Checks open option expiries.",
                "instructions": (
                    "Read all open option contracts. For each contract whose expiry date is "
                    "before today, update it to expired using the ordinary option write path. "
                    "If an expired contract was in the money, message the user once (via "
                    "telegram_send_message or webhook_send_message, whichever is configured) "
                    "with the count and identify the contracts that need manual assignment or "
                    "exercise records. If nothing expired, take no action."
                ),
                "trigger": {"kind": "schedule", "type": "daily", "time": time},
                "execution": {"kind": "lucius"},
                "effort": "low",
                "permission_mode": "auto",
                "always_allowed": [
                    {
                        "src": "Open Net Worth",
                        "desc": "Expire elapsed option contracts and send ITM reminders.",
                        "tools": [
                            "update_option",
                            "telegram_send_message",
                            "webhook_send_message",
                        ],
                    }
                ],
                "provenance": {"source": "system", "system_key": "option_expiry_sweep"},
            },
            system_key="option_expiry_sweep",
        )
        return created
    except Exception:
        logger.exception("seed_expiry_sweep failed (non-fatal)")
        return False


def _seed_expiry_sweep_if_needed() -> None:
    """Call seed_expiry_sweep on first-ever contract creation (best-effort)."""
    try:
        seed_expiry_sweep()
    except Exception:
        pass
