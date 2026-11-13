"""Dividend / income tools (P2-INV2).

Lucius reads income + forward forecast and, on an interactive/connector turn, PREVIEWS
recording income before writing it (a cash dividend books an income txn; a DRP grows
cost basis):

  * ``get_income`` / ``get_income_forecast``     — reads (proposed_change = None)
  * ``record_income`` — ONE tool per write (harness reliability program,
    2026-07-19 — collapses the former ``propose_record_income``/``record_income``
    pair; see ``ai/tools/portfolio.py``'s ``remove_positions`` for the full
    rationale). On a card-capable/attended turn: PREVIEWS (net of tax, in the proceeds
    and base currency), returns a ``proposed_change``, writes NOTHING and PAUSES on its
    own ``tool_use_id``. ``assistant.resolve_pending_tool_use`` re-runs it on approval
    with the STORED args — routes through ``income_service.apply_income_change``. An
    unattended/automated caller records immediately, no card.
  * ``delete_income``                            — delete an event (confirm-first).

ALL income math + the cash-leg/DRP choke point live in ``services/income.py`` — this
module only marshals I/O and shapes the confirm card. Respects the dependency rule.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import dividend_calendar as dividend_calendar_service
from backend.services import income as income_service
from backend.services import labels

# ── Reads ─────────────────────────────────────────────────────────────────────


class GetIncomeInput(ToolInput):
    """Schema-of-record for ``get_income`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    date_to: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    instrument_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to income paid by ONE instrument (id from get_portfolio or "
            "resolve_instruments). Also narrows the yield-on-cost denominator to that "
            "instrument's own remaining basis. Omit for every instrument."
        ),
    )
    account_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to income recorded against ONE account (id from list_accounts) — for a "
            "cash dividend this is the account it landed in. Omit for every account."
        ),
    )


@tool(
    "get_income",
    description=(
        "Investment income (dividends / interest / distributions) over an optional date "
        "range / instrument / account: total in the base currency, the gross-vs-tax split, "
        "per-instrument and per-kind breakdowns, trailing-12-month total, and yield-on-cost. "
        "Reads income EVENTS only (the investment-income lens), not all income transactions. "
        "Base figures are recomputed from the native leg + current FX (never stale)."
    ),
    input_schema=schema_of(GetIncomeInput, tool_name="get_income"),
)
async def get_income(input_data: dict):
    out = income_service.income_summary(
        date_from=input_data.get("date_from"),
        date_to=input_data.get("date_to"),
        instrument_id=input_data.get("instrument_id"),
        account_id=input_data.get("account_id"),
    )
    # An income EVENT identifies the payer only by opaque uuids, so a dividend read had no
    # ticker or name anywhere in it. Maps built ONCE, then attached per row (additive).
    # `by_instrument` stays keyed by id deliberately — every one of its keys also appears as
    # an event here WITH its label, so the aggregate is resolvable without inventing a second
    # labelled shape for the same concept.
    events = out.get("events")
    if isinstance(events, list) and events:
        maps = labels.label_maps(instruments=True, accounts=True)
        out["events"] = [labels.label_instrument_row(e, maps) for e in events]
    return json.dumps(out, ensure_ascii=False), None


class GetIncomeForecastInput(ToolInput):
    """Schema-of-record for ``get_income_forecast`` (see ``schema_from_model``)."""

    horizon_months: Optional[int] = Field(
        default=None, description="Forecast horizon in months (default 12)"
    )


@tool(
    "get_income_forecast",
    description=(
        "Annualized forward-income ESTIMATE: per holding the projected income over the "
        "horizon (default 12 months) + the portfolio total + implied forward yield. Each "
        "holding labels its rate source (declared = a set per-unit rate; trailing = last "
        "12 months' actual income) so you can be honest about confidence. This is a flat "
        "annualized estimate, NOT a dated payment schedule — present it as an estimate."
    ),
    input_schema=schema_of(GetIncomeForecastInput, tool_name="get_income_forecast"),
)
async def get_income_forecast(input_data: dict):
    out = income_service.forecast_income(horizon_months=int(input_data.get("horizon_months", 12)))
    return json.dumps(out, ensure_ascii=False), None


class GetDividendCalendarInput(ToolInput):
    """Schema-of-record for ``get_dividend_calendar`` (see ``schema_from_model``)."""

    days: Optional[int] = Field(
        default=None, description="Horizon in days (default 90, clamped 1–365)"
    )


@tool(
    "get_dividend_calendar",
    description=(
        "Upcoming dividend payments per holding within `days` (default 90): next ex-date, pay "
        "date, estimated amount (base currency), and status — 'scheduled' (declared) or "
        "'estimated' (projected from history). Non-payers excluded; dates/amounts are estimates "
        "(own a holding before its ex-date to be paid). Reads cached data — no network."
    ),
    input_schema=schema_of(GetDividendCalendarInput, tool_name="get_dividend_calendar"),
)
async def get_dividend_calendar(input_data: dict):
    out = dividend_calendar_service.upcoming_payments(days=int(input_data.get("days", 90)))
    return json.dumps(out, ensure_ascii=False), None


# ── Apply / delete (write; a card-capable/attended turn previews first) ─────────


def _build_record_income_change(
    input_data: dict,
) -> tuple[Optional[dict], Optional[str], dict]:
    """Shared preview-build for the ``record_income`` confirm gate — the former
    body of ``propose_record_income``. Called only when previewing or re-verifying a
    confirm's outcome.

    Returns ``(change, error, resolved_input)``. ``resolved_input`` is ``input_data`` with
    a missing ``idempotency_key`` freshly minted — exactly what ``propose_record_income``
    used to do unconditionally — BAKED IN so the eventual write uses the SAME key the card
    implied rather than a fresh one at write time.
    """
    change = _change_from_input(input_data)
    preview = income_service.preview_income({**change, "type": "record"})
    if preview.get("error"):
        return (
            None,
            "position not found — call get_portfolio for current position_ids",
            dict(input_data),
        )

    resolved = dict(input_data)
    if not resolved.get("idempotency_key"):
        resolved["idempotency_key"] = str(uuid.uuid4())

    change["type"] = "record_income"
    change["idempotency_key"] = resolved["idempotency_key"]
    change["display"] = {
        "symbol": preview.get("symbol"),
        "name": preview.get("name"),
        "income_kind": preview.get("income_kind"),
        "gross_amount": preview.get("gross_amount"),
        "tax_withheld": preview.get("tax_withheld"),
        "net_amount": preview.get("net_amount"),
        "currency": preview.get("currency"),
        "is_drp": preview.get("is_drp"),
        "base_amount": preview.get("base_amount"),
        "base_currency": preview.get("base_currency"),
        "paid_at": preview.get("paid_at"),
    }
    return change, None, resolved


class ApplyRecordIncomeInput(ToolInput):
    """Schema-of-record for ``record_income`` (see ``schema_from_model``)."""

    position_id: str = Field(description="position_id from get_portfolio")
    gross_amount: float = Field(description="Total payment BEFORE withholding, in currency")
    tax_withheld: Optional[float] = Field(default=None, description="Withholding tax in currency")
    currency: Optional[str] = Field(default=None, description="Currency of the payment")
    income_kind: Optional[Literal["dividend", "interest", "distribution"]] = Field(
        default=None, description="Income kind (default dividend)"
    )
    paid_at: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (default today)")
    account_id: Optional[str] = Field(
        default=None, description="Cash account the dividend lands in (required unless is_drp)"
    )
    is_drp: Optional[bool] = Field(
        default=None, description="true = reinvested (acquire a lot, book no cash)"
    )
    drp_quantity: Optional[float] = Field(default=None, description="Units reinvested (DRP only)")
    drp_unit_cost: Optional[float] = Field(
        default=None, description="Reinvestment price per unit (DRP only; defaults to net/qty)"
    )
    quantity: Optional[float] = Field(default=None, description="Units held at pay date (optional)")
    amount_per_unit: Optional[float] = Field(
        default=None, description="Per-unit payment (optional)"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Caller-supplied dedup key. A second call with the SAME key returns the original "
            "income event and writes nothing — no second cash transaction, no second DRP lot. "
            "Pass a stable id from the source (broker/email payment id) on any automated or "
            "retried flow; without one, a repeat WILL double-book."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note stored on the income EVENT record. It is not the booked "
            "transaction's description, which is generated from the instrument and income "
            "kind. Defaults to empty."
        ),
    )


@tool(
    "record_income",
    description=(
        "Record a dividend / interest / distribution a holding paid — books the cash dividend "
        "through the ledger, or acquires the DRP lot, and links the income event. Identify the "
        "position by position_id (from get_portfolio). A CASH dividend (is_drp=false) needs "
        "account_id (the cash account it lands in); a DRP / reinvestment (is_drp=true) needs "
        "drp_quantity (units reinvested) and optionally drp_unit_cost, and books NO cash. If the call asks the user first, their answer runs it server-side with exactly the values shown — never call it again and never restate the values. Pass idempotency_key so a retry doesn't double-book."
    ),
    input_schema=schema_of(ApplyRecordIncomeInput, tool_name="record_income"),
    mutates=("income", "ledger"),
    # propose_record_income is the former FIRST HALF of this merge (harness reliability
    # program, 2026-07-19 — see portfolio.py's remove_positions aliases note for the
    # full rationale). propose_record_income used to MINT a fresh idempotency_key into
    # every proposal (its own schema had no such field) and rely on the model copying it
    # into the apply call; the merge does the SAME minting server-side
    # (_build_record_income_change) instead of trusting the model to echo it back.
    self_stages=True,
)
async def record_income(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "record_income")
        if entry is None:
            return _gate.unknown_token_result("record_income")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    if gated or promised is not None:
        change, error, resolved = _build_record_income_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        input_data = resolved
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # e.g. the position or FX rate could have changed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("record_income")

    try:
        result = income_service.apply_income_change(
            {
                **_change_from_input(input_data),
                "type": "record",
                "idempotency_key": input_data.get("idempotency_key"),
                "source": "ai",
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


class DeleteIncomeInput(ToolInput):
    """Schema-of-record for ``delete_income`` (see ``schema_from_model``)."""

    event_id: str = Field(description="income event id (from get_income)")


@tool(
    "delete_income",
    description=(
        "Delete a recorded income event by its event id. This removes the income annotation (the booked cash transaction / DRP lot stay; undo those "
        "via the ledger / lots if needed)."
    ),
    input_schema=schema_of(DeleteIncomeInput, tool_name="delete_income"),
    mutates=("income", "ledger"),
)
async def delete_income(input_data: dict):
    try:
        result = income_service.apply_income_change(
            {"type": "delete", "event_id": input_data["event_id"]}
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


def _change_from_input(input_data: dict) -> dict:
    return {
        "position_id": input_data["position_id"],
        "paid_at": input_data.get("paid_at", ""),
        "income_kind": input_data.get("income_kind", "dividend"),
        "quantity": input_data.get("quantity"),
        "amount_per_unit": input_data.get("amount_per_unit"),
        "gross_amount": input_data.get("gross_amount", 0),
        "tax_withheld": input_data.get("tax_withheld", 0),
        "currency": input_data.get("currency"),
        "account_id": input_data.get("account_id"),
        "is_drp": input_data.get("is_drp", False),
        "drp_quantity": input_data.get("drp_quantity"),
        "drp_unit_cost": input_data.get("drp_unit_cost"),
        "note": input_data.get("note", ""),
    }
