"""Financial profile — risk tolerance, income, liabilities, FIRE settings.

Savings goals live in the first-class ``Target`` primitive (api/budgets.py + targets.json),
not on the profile; the legacy free-text ``profile.goals[]`` was retired in P2-GO1."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.core.errors import ValidationError
from backend.models.profile import DEFAULT_BRIEFING_FOCUS
from backend.services.countries import COUNTRY_CODE_SET

logger = logging.getLogger(__name__)

router = APIRouter()

_EMPLOYMENT_TYPE_SYNONYMS = {
    "salaried": "salaried",
    "employed": "salaried",
    "employee": "salaried",
    "salaried employee": "salaried",
    "full-time": "salaried",
    "full time": "salaried",
    "self_employed": "self_employed",
    "self-employed": "self_employed",
    "selfemployed": "self_employed",
    "freelance": "self_employed",
    "freelancer": "self_employed",
    "contractor": "self_employed",
    "business_owner": "business_owner",
    "business owner": "business_owner",
    "entrepreneur": "business_owner",
    "founder": "business_owner",
    "retired": "retired",
    "student": "student",
}


def _normalize_employment_type(v: str) -> str:
    normalized = _EMPLOYMENT_TYPE_SYNONYMS.get(v.lower().strip())
    if normalized is None:
        raise ValueError(
            "employment_types must each be one of: salaried, self_employed, business_owner, "
            "retired, student"
        )
    return normalized


def _normalize_country_code(v: str) -> str:
    code = v.strip().upper()
    if code not in COUNTRY_CODE_SET:
        raise ValueError(f"'{v}' is not a recognized ISO-3166 country code")
    return code


DEFAULT_PROFILE = {
    "name": None,
    # What the assistant should call you — a short form of `name`, fed to Lucius so it
    # addresses you the way you asked rather than by your legal name.
    "nickname": None,
    "nationalities": [],
    "countries_of_residence": [],
    # A separate concept from countries_of_residence: some jurisdictions (the US, notably) tax
    # by citizenship, so a person's tax obligations can span a country they no longer live in.
    "tax_residencies": [],
    "employment_types": [],
    "marital_status": None,
    "risk_tolerance": "moderate",
    "risk_concentration_guideline": 20.0,
    "risk_stress_drop_pct": 30.0,
    "preferred_benchmark": "SPY",
    "retirement_target_age": None,
    "date_of_birth": None,
    "monthly_net_income": None,
    "monthly_expenses": None,
    "monthly_saving": None,
    "cashflow_low_balance_threshold": None,
    "liabilities": [],
    "notes": "",
    "fire_expected_return_pct": 7.0,
    "fire_withdrawal_rate_pct": 4.0,
    "fire_monthly_retirement_spending": None,
    "fire_volatility_pct": None,
    "fire_life_expectancy": None,
    # Target portfolio allocation per investment asset_class, stored as fractions (0–1) that sum
    # to ~1 over the investment book (markets only — cash/property/receivable excluded). Keys are
    # asset_class values (equity|crypto|commodity|bond|…); not a fixed list. Empty = no targets set.
    "target_allocation": {},
    # Master switch for the chat panel's AI-generated suggestion chips (see services/profile.py).
    "chat_suggestions_enabled": False,
    "chat_cost_visible": True,
    "chat_multi_turn_enabled": False,
    # Draw reasoning as one merged block instead of a block per round. Default OFF (split).
    "chat_thinking_grouped": False,
    # A device with no choice of its own sends the screen with each message. Default ON.
    "screen_awareness_default": True,
    "seen_intros": [],
    # Display formats — how amounts and dates are WRITTEN. Purely presentational: they never
    # change a stored value, only how the frontend renders one. `None` means "follow the
    # browser locale", which is what every screen did before these existed.
    "date_format": None,  # dmy | mdy | iso
    "number_format": None,  # comma | space | dot
    "week_start": None,  # mon | sun
    # Which sections the daily briefing may include + an optional free-text focus the user gives
    # Lucius. Section flags are advisory toggles consumed by backend/api/briefing.py.
    "briefing_preferences": {
        "holdings_news": True,
        "fx": True,
        "spending": True,
        "accounts": True,
        "upcoming_actions": True,
        "custom_focus": DEFAULT_BRIEFING_FOCUS,
    },
}


from backend.services.profile import (  # noqa: E402 — re-export for callers that import from here
    capture_browser_timezone,
    currency_sensitive_fields,
    handle_base_currency_change,
    normalize_target_allocation,
    read_profile,
    refuse_base_currency_change_on_demo,
    set_timezone,
    write_profile,
)


def _blank_to_none(v):
    """Treat empty strings (sent by blank form fields) as 'unset' so a half-filled
    form never 422s the whole save."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


_CLEARABLE_DISPLAY_FIELDS = frozenset({"date_format", "number_format", "week_start"})


class Liability(BaseModel):
    name: str = ""
    balance: Optional[float] = None
    rate_pct: Optional[float] = None
    monthly_payment: Optional[float] = None


_INTRO_KEY = re.compile(r"[a-z][a-z0-9_-]{0,39}")


class ProfileBody(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None
    nationalities: Optional[List[str]] = None
    countries_of_residence: Optional[List[str]] = None
    tax_residencies: Optional[List[str]] = None
    employment_types: Optional[List[str]] = None
    marital_status: Optional[str] = None
    base_currency: Optional[str] = None
    risk_tolerance: Optional[str] = None
    risk_concentration_guideline: Optional[float] = None
    risk_stress_drop_pct: Optional[float] = None
    preferred_benchmark: Optional[str] = None
    retirement_target_age: Optional[int] = None
    date_of_birth: Optional[str] = None
    monthly_net_income: Optional[float] = None
    monthly_expenses: Optional[float] = None
    # What they put aside each month — its own fact; never rewrites income (ADR 2026-10-14).
    monthly_saving: Optional[float] = Field(default=None, allow_inf_nan=False)
    cashflow_low_balance_threshold: Optional[float] = None
    liabilities: Optional[List[Liability]] = None
    notes: Optional[str] = None
    fire_expected_return_pct: Optional[float] = None
    fire_withdrawal_rate_pct: Optional[float] = None
    fire_monthly_retirement_spending: Optional[float] = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    fire_volatility_pct: Optional[float] = None
    fire_life_expectancy: Optional[int] = None
    target_allocation: Optional[Dict[str, float]] = None
    risk_concentration_guideline: Optional[float] = None
    risk_stress_drop_pct: Optional[float] = None
    briefing_preferences: Optional[Dict[str, object]] = None
    # Human-only UI preference — show/hide the chat panel's AI suggestion chips.
    chat_suggestions_enabled: Optional[bool] = None
    chat_cost_visible: Optional[bool] = None
    chat_multi_turn_enabled: Optional[bool] = None
    # Human-only UI preference — merge reasoning into one block instead of per round.
    chat_thinking_grouped: Optional[bool] = None
    # Human-only UI preference — a device's Screen awareness until it makes its own choice.
    screen_awareness_default: Optional[bool] = None
    # Statement-import review band's pre-selected action — "skip" (default) or "merge".
    import_review_default: Optional[str] = None
    # Display formats — presentational only, never applied to a stored value.
    date_format: Optional[str] = None
    number_format: Optional[str] = None
    week_start: Optional[str] = None
    # Human-only UI state — which feature intros this user has already seen (e.g. "assets").
    # Server-side so an intro shown on one device never repeats on another.
    seen_intros: Optional[List[str]] = Field(default=None, max_length=50)

    # Blank numeric / enum fields arrive as "" from the form — coerce to None before validation
    # so a partially-filled profile saves cleanly instead of 422-ing every field.
    @field_validator(
        "retirement_target_age",
        "monthly_net_income",
        "monthly_expenses",
        "monthly_saving",
        "cashflow_low_balance_threshold",
        "fire_expected_return_pct",
        "fire_withdrawal_rate_pct",
        "fire_monthly_retirement_spending",
        "fire_volatility_pct",
        "fire_life_expectancy",
        "risk_concentration_guideline",
        "risk_stress_drop_pct",
        "marital_status",
        "risk_tolerance",
        "import_review_default",
        "date_format",
        "number_format",
        "week_start",
        mode="before",
    )
    @classmethod
    def _coerce_blank(cls, v):
        return _blank_to_none(v)

    @field_validator("seen_intros")
    @classmethod
    def _intro_keys(cls, v):
        if v is None:
            return v
        if any(not _INTRO_KEY.fullmatch(k) for k in v):
            raise ValueError("seen_intros entries are short lowercase keys like 'assets'")
        return list(dict.fromkeys(v))

    @field_validator("date_format")
    @classmethod
    def validate_date_format(cls, v):
        if v is not None and v not in ("dmy", "mdy", "iso"):
            raise ValueError("date_format must be dmy, mdy, or iso")
        return v

    @field_validator("number_format")
    @classmethod
    def validate_number_format(cls, v):
        if v is not None and v not in ("comma", "space", "dot"):
            raise ValueError("number_format must be comma, space, or dot")
        return v

    @field_validator("week_start")
    @classmethod
    def validate_week_start(cls, v):
        if v is not None and v not in ("mon", "sun"):
            raise ValueError("week_start must be mon or sun")
        return v

    @field_validator("target_allocation")
    @classmethod
    def validate_targets(cls, v):
        if v is None:
            return v
        return normalize_target_allocation(v)

    @field_validator("risk_concentration_guideline", "risk_stress_drop_pct")
    @classmethod
    def _clamp_risk_pct(cls, v):
        if v is None:
            return v
        return max(1.0, min(100.0, float(v)))

    @field_validator("employment_types")
    @classmethod
    def validate_employment(cls, v):
        if v is None:
            return v
        return [_normalize_employment_type(item) for item in v]

    @field_validator("nationalities", "countries_of_residence", "tax_residencies")
    @classmethod
    def validate_country_codes(cls, v):
        if v is None:
            return v
        return [_normalize_country_code(item) for item in v]

    @field_validator("marital_status")
    @classmethod
    def validate_marital(cls, v):
        if v is None:
            return v
        _map = {
            "single": "single",
            "unmarried": "single",
            "married": "married",
            "wed": "married",
            "wedded": "married",
            "divorced": "divorced",
            "separated": "divorced",
            "widowed": "widowed",
            "widow": "widowed",
            "widower": "widowed",
        }
        normalized = _map.get(v.lower().strip())
        if normalized is None:
            raise ValueError("marital_status must be one of: single, married, divorced, widowed")
        return normalized

    @field_validator("risk_tolerance")
    @classmethod
    def validate_risk(cls, v):
        if v is not None and v not in ("conservative", "moderate", "aggressive"):
            raise ValueError("risk_tolerance must be conservative, moderate, or aggressive")
        return v

    @field_validator("import_review_default")
    @classmethod
    def validate_import_review_default(cls, v):
        if v is not None and v not in ("skip", "merge"):
            raise ValueError("import_review_default must be skip or merge")
        return v

    @field_validator("fire_expected_return_pct", "fire_withdrawal_rate_pct")
    @classmethod
    def validate_pct(cls, v):
        if v is not None and not (0 < v < 100):
            raise ValueError("Rate must be between 0 and 100")
        return v

    @field_validator("preferred_benchmark")
    @classmethod
    def validate_benchmark(cls, v):
        from backend.services.risk_metrics import BENCHMARK_ROSTER

        if v is not None and v not in BENCHMARK_ROSTER:
            raise ValueError(f"preferred_benchmark must be one of {', '.join(BENCHMARK_ROSTER)}")
        return v

    @field_validator("retirement_target_age")
    @classmethod
    def validate_age(cls, v):
        if v is not None and not (18 <= v <= 100):
            raise ValueError("retirement_target_age must be between 18 and 100")
        return v

    @field_validator("risk_concentration_guideline", "risk_stress_drop_pct", mode="before")
    @classmethod
    def clamp_risk_pct(cls, v):
        if v is None:
            return v
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("Value must be a number")
        return max(1.0, min(100.0, fv))


@router.get("/api/profile")
def get_profile():
    return read_profile()


@router.put("/api/profile")
def put_profile(body: ProfileBody):
    current = read_profile()
    updates = body.model_dump(exclude_none=True)
    # A display format sent blank is the user choosing "Browser default" — `exclude_none` would
    # drop it and leave the old choice in force, so an explicitly-sent blank clears it.
    for key in _CLEARABLE_DISPLAY_FIELDS & body.model_fields_set:
        if getattr(body, key) is None:
            updates[key] = None
    # Serialise Pydantic sub-models back to dicts
    if "liabilities" in updates:
        updates["liabilities"] = [
            lb.model_dump(exclude_none=True) for lb in (body.liabilities or [])
        ]
    # Deep-merge briefing_preferences so a partial update keeps the other flags on disk.
    if "briefing_preferences" in updates:
        updates["briefing_preferences"] = {
            **(current.get("briefing_preferences") or {}),
            **updates["briefing_preferences"],
        }
    old_base = current.get("base_currency")
    new_base = updates.get("base_currency")
    try:
        refuse_base_currency_change_on_demo(old_base, new_base)
    except ValidationError as exc:
        raise HTTPException(
            status_code=403, detail={"code": "demo_locked", "message": exc.message}
        ) from exc
    current.update(updates)
    # Clears the stale FX cache + flags non-converting manual amounts for review.
    handle_base_currency_change(current, old_base, new_base)
    write_profile(current)
    return {"ok": True, "profile": current}


class TimezoneBody(BaseModel):
    timezone: Optional[str] = None


@router.put("/api/profile/timezone")
def put_timezone(body: TimezoneBody):
    """The owner's explicit pick (``source: user``); a blank/None clears it back to the server's
    zone, so the browser's can be captured again."""
    try:
        state = set_timezone(_blank_to_none(body.timezone), "user")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return state


@router.post("/api/profile/timezone/detect")
def detect_timezone(body: TimezoneBody):
    """The web app reports the browser's zone on load; it lands only while none is stored."""
    if not body.timezone:
        raise HTTPException(status_code=422, detail="timezone is required")
    try:
        state = capture_browser_timezone(body.timezone)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return state


@router.get("/api/profile/base-currency-preview")
def preview_base_currency_change():
    """The manual amounts a base-currency switch would NOT convert.

    Balances and transactions carry a native currency and convert correctly; hand-typed
    amounts do not — the same numbers are simply re-read as the new currency. The Settings
    confirm modal names them BEFORE the switch, so this is the same computation
    `handle_base_currency_change` stamps afterwards, exposed as a preview rather than
    re-implemented in the client (where it would drift).
    """
    profile = read_profile()
    return {
        "base_currency": profile.get("base_currency"),
        "fields": currency_sensitive_fields(profile),
    }


@router.delete("/api/profile/base-currency-review")
def dismiss_base_currency_review():
    """Clear the review flag once the user has acknowledged / re-entered their amounts."""
    current = read_profile()
    current.pop("base_currency_review", None)
    write_profile(current)
    return {"ok": True}
