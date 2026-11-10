"""
income_service — dividend / interest / distribution income (P2-INV2).

ALL income arithmetic, the write choke point, yield-on-cost, and the forward forecast
live HERE — never in a router, AI tool, or the UI (mirrors how lots_service owns the
realized-gain fold). Base figures are RECOMPUTED from the native leg (gross − tax) +
current FX on read, never trusted from storage after a base-currency change (the
CLAUDE.md "computed, never trusted" invariant — same as ledger.txn_base_amount /
lots.realized_pnl).

Key design (avoids double-counting, the High risk in the RFC):
  - ``apply_income_change`` is the ONLY writer. A CASH dividend's money flows through the
    existing ``ledger_service.create_transaction`` (kind="income", tagged "dividend") —
    so net worth / cash flow / spending see it EXACTLY once. The income event is a pure
    annotation linking that one cash leg to the instrument (it stores the returned txn_id).
  - A DRP (reinvested) instead acquires a lot via ``lots_service.apply_lot_change`` and
    books NO cash leg — reinvestment grows cost basis, not cash.
  - ``income_summary`` reads income EVENTS only (the investment-income lens), not all
    kind="income" txns — so it never double-counts and stays distinct from the ledger's
    total-income view.

Imports are downward/sibling only (db + core + services) — no api/ai (Contract 3).
ledger_service / lots_service are lazy-imported (services-tier siblings) like
lots._maybe_deposit_proceeds imports ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.core.errors import MissingFxRateError, NotFoundError, ValidationError
from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.core.write_receipt import record_write
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore, vesting_state_of
from backend.services import fixed_income, labels
from backend.services import lots as lots_service

# Trailing window for trailing-12m income + yield-on-cost (days).
TRAILING_DAYS = 365


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Convenience handles so tests/callers monkeypatch a single module surface (mirrors
# lots_service / ledger_service module-level store instances).
events = IncomeEventStore()
instruments = InstrumentStore()
positions = PositionStore()


# ── Pure math ────────────────────────────────────────────────────────────────────


def net_native(event: dict) -> float:
    """Net income in the event's NATIVE currency: gross_amount − tax_withheld. This is the
    authoritative figure; per-unit fields are informational. Rounded 2dp at the boundary."""
    gross = float(event.get("gross_amount") or 0)
    tax = float(event.get("tax_withheld") or 0)
    return round(gross - tax, 2)


def event_base_amount(event: dict, rates: dict | None = None, base: str | None = None) -> float:
    """The event's net income in the base currency — the HISTORICAL-RATE LOCK (mirrors
    ``core.fx.txn_base_amount``, ADR 2026-07-09-foreign-currency-historical-rate-lock).

    When the event's stored ``base_currency`` still matches the current base, TRUST the
    write-time ``base_amount`` instead of revaluing it at today's rate. Only when the base
    currency has CHANGED since the write (stamp mismatch) or the row is unstamped/legacy do
    we recompute from the native leg (gross − tax) at current FX.
    """
    base = (base or base_currency()).upper()
    if (event.get("base_currency") or "").upper() == base and event.get("base_amount") is not None:
        return float(event["base_amount"])
    if rates is None:
        rates = lots_service._load_rates()
    ccy = (event.get("currency") or base).upper()
    return to_base(net_native(event), ccy, base, rates=rates)


def _converted(amount: float, currency: str, rates: dict | None, base: str) -> float | None:
    """``to_base`` that answers None for an unavailable pair instead of raising — the income
    read degrades (flags the gap, leaves the figure out) rather than fabricate a base amount."""
    try:
        return to_base(amount, currency, base, rates=rates)
    except MissingFxRateError:
        return None


def _event_base_or_none(event: dict, rates: dict, base: str) -> float | None:
    """``event_base_amount``, or None when the event needs a conversion with no rate."""
    try:
        return event_base_amount(event, rates=rates, base=base)
    except MissingFxRateError:
        return None


def _gross_base(event: dict, rates: dict, base: str) -> float | None:
    ccy = (event.get("currency") or base).upper()
    return _converted(float(event.get("gross_amount") or 0), ccy, rates, base)


def _tax_base(event: dict, rates: dict, base: str) -> float | None:
    ccy = (event.get("currency") or base).upper()
    return _converted(float(event.get("tax_withheld") or 0), ccy, rates, base)


# ── Reads / analytics (computed) ──────────────────────────────────────────────────


def income_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    instrument_id: str | None = None,
    account_id: str | None = None,
    position_id: str | None = None,
    base: str | None = None,
    include_event_labels: bool = False,
    with_predecessors: bool = False,
) -> dict:
    """Income over a date range / instrument / account / position. Walks the income-event
    ledger and RECOMPUTES each event's base figure from its native net (gross − tax) +
    current FX. Returns total_base, gross/tax split, by_instrument, by_kind, trailing-12m
    total, and yield-on-cost (trailing income ÷ remaining cost basis).

    An empty ledger returns total_base 0.0 (no crash / div-by-zero).

    An event whose currency has no FX rate degrades honestly: its ``base_amount`` is None, it
    is left out of every base total/breakdown, and ``base_totals_available`` is False with the
    currency named in ``missing_fx_currencies`` — never the native figure relabelled as base.

    ``with_predecessors`` widens a ``position_id`` read to the holding's sold-out predecessors,
    as ``lots.realized_pnl`` does."""
    from backend.services.position_lineage import history_position_ids

    base = (base or base_currency()).upper()
    rates = lots_service._load_rates()
    lineage = history_position_ids(position_id) if with_predecessors and position_id else None
    rows = events.query_events(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
        position_id=None if lineage else position_id,
    )
    if lineage:
        rows = [r for r in rows if r.get("position_id") in lineage]

    today = user_today()
    trailing_cutoff = (today.fromordinal(today.toordinal() - TRAILING_DAYS)).isoformat()

    total_base = 0.0
    gross_base = 0.0
    tax_base = 0.0
    trailing_base = 0.0
    by_instrument: dict[str, float] = {}
    by_kind: dict[str, float] = {}
    out_events: list[dict] = []
    missing_fx_currencies: set[str] = set()

    display_maps = (
        labels.label_maps(instruments=True, accounts=True)
        if include_event_labels and rows
        else None
    )
    base_totals_available = True
    for ev in rows:
        net_base = _event_base_or_none(ev, rates, base)
        gross_ev = _gross_base(ev, rates, base)
        tax_ev = _tax_base(ev, rates, base)
        event_base_available = net_base is not None
        if net_base is None or gross_ev is None or tax_ev is None:
            base_totals_available = False
            missing_fx_currencies.add((ev.get("currency") or base).upper())
        gross_base += gross_ev or 0.0
        tax_base += tax_ev or 0.0
        event = {**ev, "base_amount": None}
        if net_base is not None:
            total_base += net_base
            iid = ev.get("instrument_id") or ""
            by_instrument[iid] = round(by_instrument.get(iid, 0.0) + net_base, 2)
            kind = ev.get("income_kind") or "dividend"
            by_kind[kind] = round(by_kind.get(kind, 0.0) + net_base, 2)
            if (ev.get("paid_at") or "") >= trailing_cutoff:
                trailing_base += net_base
            event["base_amount"] = round(net_base, 2)
        if include_event_labels:
            native_currency = (ev.get("currency") or base).upper()
            event.update(
                {
                    "base_amount_available": event_base_available,
                    "native_net_amount": net_native(ev),
                    "native_currency": native_currency,
                }
            )
        out_events.append(labels.label_instrument_row(event, display_maps))

    # Yield-on-cost: trailing income ÷ remaining cost basis of the in-scope positions.
    cost_basis_base, basis_missing_fx = _scope_remaining_basis_base(
        rows, instrument_id, position_id, rates, base
    )
    missing_fx_currencies.update(basis_missing_fx)
    yield_on_cost_available = base_totals_available and not basis_missing_fx
    yoc = (
        round(trailing_base / cost_basis_base * 100, 2)
        if cost_basis_base and yield_on_cost_available
        else None
    )

    return {
        "base_currency": base,
        "events": out_events,
        "total_base": round(total_base, 2),
        "gross_base": round(gross_base, 2),
        "tax_withheld_base": round(tax_base, 2),
        "trailing_12m_base": round(trailing_base, 2),
        "cost_basis_base": round(cost_basis_base, 2),
        "yield_on_cost_pct": yoc,
        "by_instrument": by_instrument,
        "by_kind": by_kind,
        "base_totals_available": base_totals_available,
        "yield_on_cost_available": yield_on_cost_available,
        "missing_fx_currencies": sorted(missing_fx_currencies),
    }


def _scope_remaining_basis_base(
    rows: list[dict],
    instrument_id: str | None,
    position_id: str | None,
    rates: dict | None,
    base: str,
) -> tuple[float, set[str]]:
    """Σ remaining cost basis (in base) over the positions in scope. If a position is
    explicitly scoped use it; else use every distinct position that paid income in `rows`.
    Each position's native remaining basis is converted into base via its cost currency."""
    position_ids: set[str] = set()
    if position_id:
        position_ids.add(position_id)
    else:
        for ev in rows:
            pid = ev.get("position_id")
            if pid:
                position_ids.add(pid)
    total = 0.0
    missing_fx_currencies: set[str] = set()
    for pid in position_ids:
        pos = positions.find_by_id(pid)
        if not pos:
            continue
        if instrument_id and pos.get("instrument_id") != instrument_id:
            continue
        native_basis = lots_service.position_remaining_basis(pid)
        cost_ccy = (
            pos.get("cost_currency") or _instrument_ccy(pos.get("instrument_id")) or base
        ).upper()
        if native_basis != 0:
            converted = _converted(native_basis, cost_ccy, rates, base)
            if converted is None:
                missing_fx_currencies.add(cost_ccy)
            else:
                total += converted
    return total, missing_fx_currencies


def _instrument_ccy(instrument_id: str | None) -> str | None:
    if not instrument_id:
        return None
    inst = instruments.find_by_id(instrument_id)
    return (inst or {}).get("quote_currency")


def yield_on_cost(position_id: str, base: str | None = None) -> Optional[float]:
    """Trailing-12m income ÷ remaining cost basis, as a percent. None if no cost basis."""
    summary = income_summary(position_id=position_id, base=base)
    return summary.get("yield_on_cost_pct")


# ── Forward forecast (declared → trailing actual) ──────────────────────────────────


def forward_rate_for_position(
    pos: dict, inst: dict, rates: dict | None, base: str
) -> tuple[Optional[float], str]:
    """Forward ANNUAL income for this holding (in base) + the source label.

    Fallback ladder:
      (a) declared — instrument.dividend_rate_annual (per-unit, quote_currency) × held_qty.
          This is the company's current/announced dividend rate as relayed by the price
          source (yfinance dividendRate) — there is no more-authoritative source this app
          could ask the user to supply instead, so provenance (system-enriched vs. a user
          retyping the identical figure via update_instruments) is NOT a real confidence
          distinction here and is intentionally not surfaced as one. `dividend_rate_source`
          still exists as a write-guard (a one-way ratchet, "" → "enriched" → "manual",
          mirroring classification_source) purely so a user's manual correction survives
          the next price refresh instead of being silently clobbered — see
          docs/adr/2026-09-01-dividend-rate-source-provenance-ratchet.md for that
          write-protection rationale and its now-reverted labeling experiment.
      (b) trailing — trailing-12-month actual income for THIS position, used only when no
          current-period rate is known at all.
    """
    qty = float(pos.get("quantity") or 0)
    quote_ccy = (inst.get("quote_currency") or base).upper()
    # Fixed income: a coupon bond / FD / SSB projects face × rate × qty ("coupon"); a T-bill
    # projects its annualized discount accretion ("accretion"). A bond ETF has no coupon
    # (per_unit == 0) and falls through to the distribution rate below — correct, its income
    # is enriched dividend_rate_annual, not a coupon.
    if qty and fixed_income.is_fixed_income(inst):
        per_unit, src = fixed_income.annual_income_per_unit(inst)
        if per_unit:
            annual_native = per_unit * qty
            return round(to_base(annual_native, quote_ccy, base, rates=rates), 2), src
    rate = inst.get("dividend_rate_annual")
    if rate is not None and qty:
        annual_native = float(rate) * qty
        return round(to_base(annual_native, quote_ccy, base, rates=rates), 2), "declared"
    # (b) trailing actual income for this position over the last 12 months.
    today = user_today()
    cutoff = (today.fromordinal(today.toordinal() - TRAILING_DAYS)).isoformat()
    summary = income_summary(position_id=pos.get("id"), date_from=cutoff, base=base)
    trailing = summary.get("trailing_12m_base") or 0.0
    if trailing:
        return round(trailing, 2), "trailing"
    return None, "none"


def _market_value_by_position(rates: dict | None, base: str) -> dict[str, float]:
    """Current market value (in base) per position_id, from the canonical hydrated holdings.
    The denominator for forward yield (true dividend yield = forward income ÷ market value, the
    ADR-0020 comparability fix). Lazy import keeps income↔positions a sibling, cycle-free."""
    from backend.services import positions as positions_service

    out: dict[str, float] = {}
    for h in positions_service.hydrated():
        pid = h.get("position_id")
        if not pid:
            continue
        qty = float(h.get("quantity") or 0)
        price = float(h.get("price") or 0)
        qpu = float(h.get("quote_per_unit") or 1.0)
        ccy = (h.get("quote_currency") or base).upper()
        out[pid] = to_base(qty * price * qpu, ccy, base, rates=rates)
    return out


def forecast_income(horizon_months: int = 12, base: str | None = None) -> dict:
    """Annualized forward-income estimate per holding + portfolio total + implied forward
    yield. Per holding: projected annual income (declared rate → trailing actual), scaled to
    the horizon. Each row labels its rate source so callers can be honest about confidence.

    Empty portfolio ⇒ {total: 0.0} with no div-by-zero (AC). NOT a dated schedule — a flat
    annualized number (the RFC's MVP scope)."""
    base = (base or base_currency()).upper()
    rates = lots_service._load_rates()
    scale = max(0, horizon_months) / 12.0

    insts = {i["id"]: i for i in instruments.all()}
    market_by_pos = _market_value_by_position(rates, base)
    rows: list[dict] = []
    total_annual = 0.0
    total_basis = 0.0
    total_market = 0.0

    for pos in positions.all():
        qty = float(pos.get("quantity") or 0)
        if qty <= 0:
            continue
        # Unvested equity-grant units are not owned yet (ADR
        # 2026-07-31-unvested-equity-excluded-from-net-worth) — they will not pay the
        # holder a real dividend. `positions.all()` is the RAW store (not
        # positions_service.hydrated(), which normalizes this field), so entitlement must be
        # resolved via `vesting_state_of()` — a bare `pos.get("vesting_state")` misses the
        # legacy `bucket="locked"`-only shape unvested positions are actually stored with.
        if vesting_state_of(pos) == "unvested":
            continue
        inst = insts.get(pos.get("instrument_id"))
        if not inst:
            continue
        annual_base, source = forward_rate_for_position(pos, inst, rates, base)
        if annual_base is None:
            continue
        horizon_base = round(annual_base * scale, 2)
        native_basis = lots_service.position_remaining_basis(pos["id"])
        cost_ccy = (pos.get("cost_currency") or inst.get("quote_currency") or base).upper()
        basis_base = to_base(native_basis, cost_ccy, base, rates=rates)
        total_annual += annual_base
        total_basis += basis_base
        total_market += market_by_pos.get(pos["id"], 0.0)
        rows.append(
            {
                "position_id": pos["id"],
                "instrument_id": inst["id"],
                "symbol": inst.get("symbol", ""),
                "name": inst.get("name", ""),
                "quantity": qty,
                "annual_income_base": annual_base,
                "horizon_income_base": horizon_base,
                "source": source,
            }
        )

    implied_yield = round(total_annual / total_basis * 100, 2) if total_basis else None
    # Forward yield on MARKET VALUE — the standard dividend yield (ADR 0020), comparable to a
    # quoted yield. `implied_forward_yield_pct` (cost basis) is retained for back-compat only.
    forward_yield = round(total_annual / total_market * 100, 2) if total_market else None
    return {
        "base_currency": base,
        "horizon_months": horizon_months,
        "holdings": rows,
        "annual_total_base": round(total_annual, 2),
        "horizon_total_base": round(total_annual * scale, 2),
        "cost_basis_base": round(total_basis, 2),
        "market_value_base": round(total_market, 2),
        "implied_forward_yield_pct": implied_yield,
        "forward_yield_pct": forward_yield,
    }


# ── Write choke point (checkpoint-covered, approve-tier) ───────────────────────────


def apply_income_change(change: dict) -> dict:
    """The single write entrypoint for income mutations. ``change["type"]`` ∈
    {"record", "delete"}. Shared by the AI apply tool + the REST routes so turn-checkpoints
    cover every income write (the writes funnel through JsonStore._save_raw → write hook).

    record: books the cash leg (via ledger.create_transaction, tagged "dividend") OR a DRP
            lot (via lots.apply_lot_change), then appends the linking income event. Idempotent
            on a client idempotency_key (a second apply with the same key is a no-op).
    delete: removes the income event. (The booked cash txn / DRP lot are left in place — the
            user undoes those via the ledger / lots surfaces, mirroring how realized events
            and their cash legs are independent; a checkpoint restore reverts all files.)
    """
    ctype = change.get("type")
    if ctype == "record":
        return _record_receipt(_apply_record(change), "create", ctype)
    if ctype == "delete":
        return _record_receipt(_apply_delete(change), "delete", ctype)
    return {"error": f"unknown change type: {ctype}"}


def _record_receipt(result: dict, op: str, ctype: str) -> dict:
    """Report this income write onto the turn's receipt (a no-op outside an assistant turn).

    Records ONLY the income EVENT. ``record`` also books a cash transaction (through the
    ledger's audit choke) or a DRP lot (through ``lots.apply_lot_change``) — both report
    themselves, so recording them here too would double-count one economic event."""
    try:
        if not isinstance(result, dict) or result.get("error"):
            return result
        record_write(op, result.get("event_id") or "", entity_type="income", meta={"verb": ctype})
    except Exception:  # noqa: BLE001 - describing a write must never be able to fail it
        pass
    return result


def _apply_record(change: dict) -> dict:
    idempotency_key = change.get("idempotency_key")
    if idempotency_key:
        existing = events.find_by_idempotency_key(idempotency_key)
        if existing:  # already applied (re-apply / post-restore) → no-op
            return {
                "ok": True,
                "event_id": existing["id"],
                "position_id": existing.get("position_id"),
                "idempotent": True,
            }

    pos = positions.find_by_id(change.get("position_id", ""))
    if not pos:
        raise ValidationError("position not found", position_id=change.get("position_id"))
    inst = instruments.find_by_id(pos.get("instrument_id", "")) or {}

    base = base_currency()
    rates = lots_service._load_rates()
    currency = (
        change.get("currency") or pos.get("cost_currency") or inst.get("quote_currency") or base
    ).upper()
    paid_at = change.get("paid_at") or user_today().isoformat()
    gross = round(float(change.get("gross_amount") or 0), 2)
    tax = round(float(change.get("tax_withheld") or 0), 2)
    net = round(gross - tax, 2)
    is_drp = bool(change.get("is_drp"))

    txn_id: Optional[str] = None
    drp_lot_id: Optional[str] = None

    if is_drp:
        # Reinvest: acquire a lot, book NO cash. Reinvested qty/price are user/parser input.
        drp_qty = float(change.get("drp_quantity") or 0)
        unit_cost = change.get("drp_unit_cost")
        if unit_cost is None and drp_qty:
            unit_cost = round(net / drp_qty, 6) if drp_qty else None
        acq = lots_service.apply_lot_change(
            {
                "type": "acquire",
                "position_id": pos["id"],
                "quantity": drp_qty,
                "unit_cost": unit_cost,
                "cost_currency": currency,
                "acquired_at": paid_at,
                # Distinct kind so basis provenance survives in lots.json — a reinvested
                # dividend is not an ordinary purchase (C6 cleanup 2026-07-15). The
                # double-book guard treats drp exactly like buy.
                "kind": "drp",
                "note": "DRP reinvestment",
                "source": change.get("source", "manual"),
            }
        )
        # An identical-lot refusal means this reinvestment is already booked — refuse the
        # whole record BEFORE the income event is appended (no dangling drp_lot_id=None).
        if acq.get("error"):
            raise ValidationError(acq["error"], position_id=pos["id"])
        drp_lot_id = acq.get("lot_id")
        account_id = pos.get("account_id", "")
    else:
        # Cash dividend: book the inflow through the EXISTING ledger choke point (the ONLY
        # cash movement), tagged "dividend" so it's the investment-income lens. Net of tax
        # is what actually lands in the account.
        # An id or exact name resolves to the id the event STORES — the ledger leg would
        # accept a name, but the event used to persist it raw as its "account_id".
        if change.get("account_id"):
            from backend.services.accounts import require_account_id

            account_id = require_account_id(change["account_id"])
        else:
            account_id = pos.get("account_id", "")
        if not account_id:
            raise ValidationError("a cash dividend needs a destination account_id")
        from backend.services import ledger as ledger_service

        txn = ledger_service.create_transaction(
            {
                "date": paid_at,
                "kind": "income",
                "description": _txn_description(inst, change.get("income_kind", "dividend")),
                "to_account_id": account_id,
                "to_amount": net,
                "to_currency": currency,
                "tags": ["dividend"],
                "ticker": inst.get("price_source_id") or inst.get("symbol") or None,
                "source": change.get("source", "manual"),
            }
        )
        txn_id = txn.get("id")

    base_amount = to_base(net, currency, base, rates=rates)
    fx_rate = round(base_amount / net, 6) if net else _fx_rate_only(currency, base, rates)

    event = events.append(
        {
            "position_id": pos["id"],
            "instrument_id": pos.get("instrument_id", ""),
            "account_id": account_id,
            "paid_at": paid_at,
            "income_kind": change.get("income_kind", "dividend"),
            "quantity": change.get("quantity"),
            "amount_per_unit": change.get("amount_per_unit"),
            "gross_amount": gross,
            "tax_withheld": tax,
            "currency": currency,
            "is_drp": is_drp,
            "drp_lot_id": drp_lot_id,
            "txn_id": txn_id,
            "base_amount": round(base_amount, 2),
            "base_currency": base,
            "fx_rate": fx_rate,
            "idempotency_key": idempotency_key,
            "source": change.get("source", "manual"),
            "note": change.get("note", ""),
        }
    )
    return {
        "ok": True,
        "event_id": event["id"],
        "position_id": pos["id"],
        "txn_id": txn_id,
        "drp_lot_id": drp_lot_id,
        "is_drp": is_drp,
        "base_amount": round(base_amount, 2),
    }


def _apply_delete(change: dict) -> dict:
    event_id = change.get("event_id") or change.get("id")
    ev = events.find_by_id(event_id) if event_id else None
    if not ev:
        raise NotFoundError("income event not found", event_id=event_id)
    events.delete(event_id)
    return {"ok": True, "event_id": event_id, "txn_id": ev.get("txn_id")}


def _txn_description(inst: dict, income_kind: str) -> str:
    label = (inst or {}).get("name") or (inst or {}).get("symbol") or "holding"
    return f"{income_kind.capitalize()} — {label}"


def _fx_rate_only(currency: str, base: str, rates: dict | None) -> Optional[float]:
    """The currency→base FX rate to snapshot when the net is 0 (so a rate is still recorded)."""
    if (currency or "").upper() == (base or "").upper():
        return 1.0
    converted = to_base(1.0, currency, base, rates=rates)
    return round(converted, 6) if converted else None


# ── Preview (approve-tier propose) ─────────────────────────────────────────────────


def preview_income(change: dict) -> dict:
    """PURE preview of a record (no writes) — the confirm card the propose flow shows. Loads
    the position + instrument + FX and computes the base figure exactly as the apply would.
    Returns {error} for an unknown position so the propose tool surfaces it without writing."""
    pos = positions.find_by_id(change.get("position_id", ""))
    if not pos:
        return {"error": "position not found"}
    inst = instruments.find_by_id(pos.get("instrument_id", "")) or {}
    base = base_currency()
    rates = lots_service._load_rates()
    currency = (
        change.get("currency") or pos.get("cost_currency") or inst.get("quote_currency") or base
    ).upper()
    gross = round(float(change.get("gross_amount") or 0), 2)
    tax = round(float(change.get("tax_withheld") or 0), 2)
    net = round(gross - tax, 2)
    base_amount = round(to_base(net, currency, base, rates=rates), 2)
    return {
        "position_id": pos["id"],
        "instrument_id": pos.get("instrument_id", ""),
        "symbol": inst.get("symbol", ""),
        "name": inst.get("name", ""),
        "income_kind": change.get("income_kind", "dividend"),
        "gross_amount": gross,
        "tax_withheld": tax,
        "net_amount": net,
        "currency": currency,
        "is_drp": bool(change.get("is_drp")),
        "base_amount": base_amount,
        "base_currency": base,
        "paid_at": change.get("paid_at") or user_today().isoformat(),
    }
