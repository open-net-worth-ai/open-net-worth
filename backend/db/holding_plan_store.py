"""
HoldingPlanStore — why you own (or would buy) an instrument, and the prices that matter to you.

One plan per (book, instrument), independent of positions and watchlist membership: buying,
selling or leaving every watchlist never touches it (a held ticker's auto "My holdings" watch
item is deleted on a full sell, which is why the plan cannot live there — ADR
``2026-10-27-a-holding-plan-lives-on-the-instrument``). Held vs watched is derived at read time,
never stored. User intent: checkpoint-included and portable.

Plan schema:
    id            : "hp_<12hex>"
    instrument_id : str
    role          : "core" | "satellite" | "speculative" | None
    thesis        : str (<= 280)     why you own it / would buy it
    exit_if       : str (<= 280)     what would make you sell
    stop_below    : float | None     prices in the instrument's quote currency
    buy_below     : float | None     top of the buy zone
    buy_above     : float | None     optional floor of the buy zone (needs buy_below)
    fair_value    : float | None
    trim_above    : float | None
    created_at    : ISO
    updated_at    : ISO

``validate_plan`` is the one place these rules live; ``services.holding_plans.apply_plan_change``
is the one writer and calls it before every write.
"""

from __future__ import annotations

import math

from backend.core.errors import ValidationError
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import holding_plans

ROLES = ("core", "satellite", "speculative")
TEXT_FIELDS = ("thesis", "exit_if")
PRICE_FIELDS = ("stop_below", "buy_below", "buy_above", "fair_value", "trim_above")
PLAN_FIELDS = ("role", *TEXT_FIELDS, *PRICE_FIELDS)
MAX_TEXT_CHARS = 280

_LABELS = {
    "thesis": "Why you own it",
    "exit_if": "You'd sell if",
    "stop_below": "Stop below",
    "buy_below": "Buy at or under",
    "buy_above": "…but not under",
    "fair_value": "Fair value",
    "trim_above": "Trim above",
}


def _price(key: str, raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{_LABELS[key]} must be a number.") from None
    if not math.isfinite(value) or value <= 0:
        raise ValidationError(f"{_LABELS[key]} must be a positive price.")
    return value


def validate_plan(fields: dict) -> dict:
    """Normalize and check one plan's fields as a whole. Returns only the fields that are set;
    an empty dict means the plan is blank (the caller deletes it)."""
    unknown = set(fields) - set(PLAN_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown plan field(s): {', '.join(sorted(unknown))}.")
    out: dict = {}
    role = fields.get("role")
    if role not in (None, ""):
        if role not in ROLES:
            raise ValidationError(f"Role must be one of: {', '.join(ROLES)}.")
        out["role"] = role
    for key in TEXT_FIELDS:
        text = " ".join(str(fields.get(key) or "").split())
        if len(text) > MAX_TEXT_CHARS:
            raise ValidationError(f"{_LABELS[key]} is at most {MAX_TEXT_CHARS} characters.")
        if text:
            out[key] = text
    for key in PRICE_FIELDS:
        value = _price(key, fields.get(key))
        if value is not None:
            out[key] = value
    if "buy_above" in out and "buy_below" not in out:
        raise ValidationError('Set "Buy at or under" first; the floor needs a top.')
    if "buy_above" in out and out["buy_above"] >= out["buy_below"]:
        raise ValidationError("The floor must be below the top of the buy zone.")
    if "stop_below" in out and "trim_above" in out and out["stop_below"] >= out["trim_above"]:
        raise ValidationError("Your stop must be below your trim level.")
    return out


class HoldingPlanStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(holding_plans, id_field="id", engine=engine)

    def find_by_instrument(self, instrument_id: str, *, conn=None) -> dict | None:
        if not instrument_id:
            return None
        for plan in self.all(conn=conn):
            if plan.get("instrument_id") == instrument_id:
                return plan
        return None
