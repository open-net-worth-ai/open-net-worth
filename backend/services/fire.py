"""fire_service — FIRE projection math, extracted from api/fire.py.

Allows ai/tools and services.retirement_plan to import the pure math without coupling
to the HTTP layer (api → services → db → core).
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Optional

from backend.core.errors import ValidationError
from backend.core.user_time import user_today

logger = logging.getLogger(__name__)

DEFAULT_RETURN_PCT = 7.0
DEFAULT_WITHDRAWAL_PCT = 4.0  # 30-year horizon (Bengen 1994 / Trinity study baseline)
MAX_PROJECTION_YEARS = 50
LIFE_EXPECTANCY = 90  # default planning horizon for SWR recommendations


def recommended_swr(retirement_duration_years: float) -> float:
    """Safe withdrawal rate calibrated to retirement duration.

    Based on Bengen (1994) and extensions for long early-retirement horizons.
    At 30 years this returns the classic 4% rule; shorter durations allow a
    higher rate; longer durations (typical FIRE) require a lower rate.
    """
    if retirement_duration_years <= 20:
        return 5.0
    if retirement_duration_years <= 25:
        return 4.5
    if retirement_duration_years <= 30:
        return 4.0
    if retirement_duration_years <= 35:
        return 3.75
    if retirement_duration_years <= 40:
        return 3.5
    if retirement_duration_years <= 45:
        return 3.25
    if retirement_duration_years <= 50:
        return 3.0
    if retirement_duration_years <= 55:
        return 2.75
    return 2.5


def fire_number(monthly_retirement_spending: float, withdrawal_rate_pct: float) -> float:
    """The FIRE number: annualised retirement spending ÷ withdrawal rate.

    This is the one place monthly retirement spending is annualised, shared by
    ``compute_fire()`` and callers that need only the FIRE number.

    MATH-32: a zero or negative withdrawal rate makes the ratio undefined (division by
    zero, or a negative "target"), and negative expenses are nonsensical — both raise
    rather than silently returning 0.0 or a garbage number for a caller to coerce away.
    """
    if not math.isfinite(withdrawal_rate_pct) or withdrawal_rate_pct <= 0:
        raise ValidationError(
            "withdrawal_rate_pct must be greater than 0 — a zero or negative withdrawal "
            "rate makes the FIRE number undefined"
        )
    if not math.isfinite(monthly_retirement_spending) or monthly_retirement_spending < 0:
        raise ValidationError("monthly_retirement_spending must be finite and non-negative")
    annual_expenses = monthly_retirement_spending * 12
    result = annual_expenses / (withdrawal_rate_pct / 100)
    if not math.isfinite(result):
        raise ValidationError("FIRE number must be finite")
    return result


def _load_profile() -> dict:
    from backend.services.profile import read_profile_raw

    return read_profile_raw()


def _current_age(dob_str: str) -> Optional[float]:
    """Age in years, or ``None`` when ``dob_str`` is unset/invalid (MATH-28).

    First-run never collects a date of birth, so a missing DOB is the common case —
    returning ``0.0`` used to be silently treated as a real age of zero, fabricating an
    age-0 trajectory and a confident (and wrong) ``on_track`` verdict. ``None`` degrades
    every age-anchored output field instead."""
    if not dob_str:
        return None
    try:
        dob = date.fromisoformat(dob_str)
        today = user_today()
        age = today.year - dob.year
        if (today.month, today.day) < (dob.month, dob.day):
            age -= 1
        bday_this_year = date(today.year, dob.month, dob.day)
        if today < bday_this_year:
            bday_this_year = date(today.year - 1, dob.month, dob.day)
        days_since = (today - bday_this_year).days
        return age + days_since / 365.25
    except ValueError:
        return None


def compute_fire(
    current_nw: float,
    monthly_savings: float,
    current_age: Optional[float],
    retirement_target_age: int,
    expected_return_pct: float,
    withdrawal_rate_pct: float,
    monthly_retirement_spending: float,
    life_expectancy: float = LIFE_EXPECTANCY,
) -> dict:
    """Core FIRE math — monthly-compounding projection.

    `current_age` is `None` when the user has no date of birth on file (MATH-28): the
    FIRE number, years-to-FI and the trajectory's NW/target never depended on age and
    still compute, but every age-anchored field — `current_age`, `projected_fire_age`,
    `on_track`, `monthly_savings_needed`, and each trajectory point's `age` — degrades to
    `None` rather than fabricating age 0 (and the confident "on track" verdict that
    produced).

    Raises `ValidationError` (via `fire_number()`) for a non-positive withdrawal rate or
    negative spending (MATH-32) — never silently divides by zero or fabricates a number.

    Returns a dict suitable for JSON serialisation.
    """
    # Retirement duration = years in retirement (retirement age → life expectancy).
    # Used to surface an evidence-based recommended SWR — longer horizons need lower rates.
    retirement_duration = max(1.0, life_expectancy - retirement_target_age)
    rec_swr = recommended_swr(retirement_duration)

    annual_expenses = monthly_retirement_spending * 12
    fire_num = fire_number(monthly_retirement_spending, withdrawal_rate_pct)
    r_monthly = (expected_return_pct / 100) / 12

    progress_pct = (current_nw / fire_num * 100) if fire_num > 0 else 0.0

    # Already at or past the FIRE number is zero years away; the loop below steps a month
    # before its first check, so without this it could only ever answer "0.1 years".
    years_to_fire: Optional[float] = 0.0 if fire_num > 0 and current_nw >= fire_num else None
    nw = current_nw
    if years_to_fire is None:
        for month in range(1, MAX_PROJECTION_YEARS * 12 + 1):
            nw = nw * (1 + r_monthly) + monthly_savings
            if nw >= fire_num:
                years_to_fire = month / 12
                break

    projected_fire_age = (
        current_age + years_to_fire
        if current_age is not None and years_to_fire is not None
        else None
    )
    on_track = (
        None
        if current_age is None
        else (projected_fire_age is not None and projected_fire_age <= retirement_target_age)
    )

    trajectory = []
    nw = current_nw
    for yr in range(MAX_PROJECTION_YEARS + 1):
        trajectory.append(
            {
                "year": user_today().year + yr,
                "age": round(current_age + yr, 1) if current_age is not None else None,
                "nw": round(nw, 0),
                "target": round(fire_num, 0),
            }
        )
        if nw >= fire_num * 3 or yr == MAX_PROJECTION_YEARS:
            break
        for _ in range(12):
            nw = nw * (1 + r_monthly) + monthly_savings

    fire_cross_idx = next((i for i, p in enumerate(trajectory) if p["nw"] >= fire_num), None)
    if fire_cross_idx is not None:
        # Show at least 10 years after crossing so the FIRE target line reads as horizontal.
        trajectory = trajectory[: max(15, fire_cross_idx + 10)]

    years_remaining = retirement_target_age - current_age if current_age is not None else None
    monthly_savings_needed: Optional[float] = None
    if years_remaining is not None and years_remaining > 0 and not on_track:
        months = years_remaining * 12
        factor = (1 + r_monthly) ** months
        if factor > 1:
            monthly_savings_needed = (fire_num - current_nw * factor) * r_monthly / (factor - 1)
            monthly_savings_needed = max(0.0, round(monthly_savings_needed, 0))

    return {
        "fire_number": round(fire_num, 0),
        "current_nw": round(current_nw, 0),
        "progress_pct": round(progress_pct, 1),
        "annual_retirement_spending": round(annual_expenses, 0),
        "monthly_retirement_spending": round(monthly_retirement_spending, 0),
        "monthly_savings": round(monthly_savings, 0),
        "expected_return_pct": expected_return_pct,
        "withdrawal_rate_pct": withdrawal_rate_pct,
        "current_age": round(current_age, 1) if current_age is not None else None,
        "retirement_target_age": retirement_target_age,
        "years_to_fire": round(years_to_fire, 1) if years_to_fire is not None else None,
        "projected_fire_age": (
            round(projected_fire_age, 1) if projected_fire_age is not None else None
        ),
        "on_track": on_track,
        "monthly_savings_needed": monthly_savings_needed,
        "trajectory": trajectory,
        "retirement_duration_years": round(retirement_duration, 1),
        "recommended_swr_pct": rec_swr,
        # True when user's SWR is more aggressive than evidence supports for this horizon
        "swr_warning": withdrawal_rate_pct > rec_swr,
    }


def monthly_saving(profile: dict) -> float:
    """What the user puts aside each month, before retiring.

    A stated ``monthly_saving`` is its own fact and wins; only when none was ever stated does it
    fall back to ``monthly_net_income − monthly_expenses``. Income and spending edits therefore
    never move a saving the user gave (ADR 2026-10-14-one-retirement-engine-saving-is-a-fact)."""
    stated = profile.get("monthly_saving")
    if stated is not None:
        return float(stated)
    return float(profile.get("monthly_net_income") or 0) - float(
        profile.get("monthly_expenses") or 0
    )


def derive_fire_inputs(profile: dict, overrides: Optional[dict] = None) -> dict:
    """Derive every input `compute_fire()` needs from a profile dict — the single place
    this derivation happens (MATH-31), previously duplicated between `api/fire.py` and
    `ai/tools/profile.py` (including the MATH-29 life-expectancy pass-through).

    `overrides` are what-if scenario values, keyed the same as the returned dict's fields
    (a subset: `monthly_savings`, `expected_return_pct`, `withdrawal_rate_pct`,
    `retirement_target_age`, `monthly_retirement_spending`). Every override uses `is not None`, never
    `or` (MATH-30) — an explicit 0 is a real override, not "unset".

    A profile value that is itself invalid (e.g. a stored `fire_withdrawal_rate_pct` of 0)
    is passed through AS-IS rather than silently replaced by the default (MATH-32) —
    `compute_fire()` is the one place that validates and raises; only a genuinely ABSENT
    field falls back to the module default.
    """
    overrides = overrides or {}

    expenses = float(profile.get("monthly_expenses") or 0)

    monthly_savings_override = overrides.get("monthly_savings")
    monthly_savings = (
        monthly_savings_override
        if monthly_savings_override is not None
        else monthly_saving(profile)
    )

    expected_return_override = overrides.get("expected_return_pct")
    expected_return_pct = (
        expected_return_override
        if expected_return_override is not None
        else float(profile.get("fire_expected_return_pct") or DEFAULT_RETURN_PCT)
    )

    withdrawal_rate_override = overrides.get("withdrawal_rate_pct")
    if withdrawal_rate_override is not None:
        withdrawal_rate_pct = float(withdrawal_rate_override)
    else:
        stored_withdrawal_rate = profile.get("fire_withdrawal_rate_pct")
        withdrawal_rate_pct = (
            float(stored_withdrawal_rate)
            if stored_withdrawal_rate is not None
            else DEFAULT_WITHDRAWAL_PCT
        )

    retirement_age_override = overrides.get("retirement_target_age")
    retirement_target_age = int(
        retirement_age_override
        if retirement_age_override is not None
        else (profile.get("retirement_target_age") or 65)
    )

    spending_override = overrides.get("monthly_retirement_spending")
    if spending_override is not None:
        monthly_retirement_spending = float(spending_override)
    else:
        stored_spending = profile.get("fire_monthly_retirement_spending")
        monthly_retirement_spending = (
            float(stored_spending) if stored_spending is not None else expenses
        )

    current_age = _current_age(profile.get("date_of_birth", ""))

    life_expectancy_raw = profile.get("fire_life_expectancy")
    life_expectancy = float(life_expectancy_raw) if life_expectancy_raw else float(LIFE_EXPECTANCY)
    if life_expectancy <= retirement_target_age:
        logger.warning(
            "fire_life_expectancy=%s is not after retirement_target_age=%s; "
            "falling back to default %s",
            life_expectancy,
            retirement_target_age,
            LIFE_EXPECTANCY,
        )
        life_expectancy = float(LIFE_EXPECTANCY)

    return {
        "monthly_savings": float(monthly_savings),
        "expected_return_pct": float(expected_return_pct),
        "withdrawal_rate_pct": withdrawal_rate_pct,
        "retirement_target_age": retirement_target_age,
        "monthly_retirement_spending": monthly_retirement_spending,
        "current_age": current_age,
        "life_expectancy": life_expectancy,
    }
