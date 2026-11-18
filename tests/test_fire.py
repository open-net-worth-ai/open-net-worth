"""
Unit tests for backend.services.fire — FIRE/retirement projection math.

Coverage targets:
  - recommended_swr()     — all SWR brackets
  - _current_age()        — normal, birthday-boundary, invalid, empty
  - _load_profile()       — file-present and file-absent branches
  - compute_fire()        — happy path, already-FIRE'd, zero savings, negative NW,
                            on-track vs off-track, SWR warning, trajectory shape,
                            monthly_savings_needed formula, zero-savings-needed clamp
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Any

import pytest

from backend.core.errors import ValidationError
from backend.services.fire import (
    DEFAULT_RETURN_PCT,
    DEFAULT_WITHDRAWAL_PCT,
    LIFE_EXPECTANCY,
    MAX_PROJECTION_YEARS,
    _current_age,
    compute_fire,
    derive_fire_inputs,
    fire_number,
    recommended_swr,
)

# ─────────────────────────────────────────────────────────────────────────────
# recommended_swr — bracket boundaries
# ─────────────────────────────────────────────────────────────────────────────


def test_swr_20_year_horizon():
    assert recommended_swr(20) == 5.0


def test_swr_below_20_year_horizon():
    # ≤ 20 → 5.0
    assert recommended_swr(10) == 5.0
    assert recommended_swr(1) == 5.0


def test_swr_25_year_horizon():
    assert recommended_swr(25) == 4.5


def test_swr_between_20_and_25():
    assert recommended_swr(21) == 4.5


def test_swr_30_year_horizon():
    # Classic Bengen 4% rule
    assert recommended_swr(30) == 4.0


def test_swr_between_25_and_30():
    assert recommended_swr(26) == 4.0


def test_swr_35_year_horizon():
    assert recommended_swr(35) == 3.75


def test_swr_40_year_horizon():
    assert recommended_swr(40) == 3.5


def test_swr_45_year_horizon():
    assert recommended_swr(45) == 3.25


def test_swr_50_year_horizon():
    assert recommended_swr(50) == 3.0


def test_swr_55_year_horizon():
    assert recommended_swr(55) == 2.75


def test_swr_beyond_55():
    # > 55 → 2.5
    assert recommended_swr(60) == 2.5
    assert recommended_swr(100) == 2.5


def test_swr_is_monotonically_non_increasing():
    """Longer horizons must require lower-or-equal withdrawal rates."""
    horizons = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
    rates = [recommended_swr(h) for h in horizons]
    for i in range(len(rates) - 1):
        assert rates[i] >= rates[i + 1], f"Rate rose from {horizons[i]} to {horizons[i + 1]} years"


# ─────────────────────────────────────────────────────────────────────────────
# _current_age — age calculation
# ─────────────────────────────────────────────────────────────────────────────


def test_current_age_empty_string_returns_none():
    """MATH-28: a missing date_of_birth (first-run never collects it) must not be
    silently treated as a real age of zero."""
    assert _current_age("") is None


def test_current_age_invalid_string_returns_none():
    assert _current_age("not-a-date") is None
    assert _current_age("2000-13-01") is None


def test_current_age_approximate_value():
    """Someone born 30 years ago (roughly) should report ~30."""
    today = date.today()
    dob = date(today.year - 30, today.month, today.day)
    # If today is their birthday they're exactly 30.
    age = _current_age(dob.isoformat())
    assert 29.9 <= age <= 30.1


def test_current_age_not_yet_birthday_this_year():
    """If birthday hasn't occurred this year the integer part is one less."""
    today = date.today()
    # Birthday is tomorrow → hasn't happened yet this year.
    next_month_day = today + timedelta(days=1)
    dob = date(today.year - 40, next_month_day.month, next_month_day.day)
    age = _current_age(dob.isoformat())
    assert 39.0 <= age < 40.0


def test_current_age_after_birthday_this_year():
    """If birthday already passed this year the integer part equals the full age."""
    today = date.today()
    # Birthday was yesterday → has already happened.
    yesterday = today - timedelta(days=1)
    dob = date(today.year - 25, yesterday.month, yesterday.day)
    age = _current_age(dob.isoformat())
    assert 25.0 <= age <= 25.1


def test_current_age_fractional_precision():
    """Age returned is a float with a sensible fractional day component."""
    today = date.today()
    dob = date(today.year - 35, today.month, today.day)
    age = _current_age(dob.isoformat())
    # On their birthday the fractional part is 0 (days_since=0 / 365.25 = 0)
    assert age == pytest.approx(35.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# _load_profile — file I/O branch
# ─────────────────────────────────────────────────────────────────────────────


def test_load_profile_returns_empty_dict_when_file_absent():
    # profile is DB-backed now (Wave 5, JSON->DB) — "absent" is an empty row, which
    # tests/conftest.py's autouse per-test table clear already guarantees here.
    import backend.services.fire as mod

    assert mod._load_profile() == {}


def test_load_profile_parses_json_when_file_present():
    import backend.services.fire as mod
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "SGD", "fire": {"retirement_age": 55}})
    result = mod._load_profile()
    assert result["base_currency"] == "SGD"
    assert result["fire"]["retirement_age"] == 55


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for compute_fire
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_KWARGS: dict[str, Any] = dict(
    current_nw=500_000.0,
    monthly_savings=3_000.0,
    current_age=35.0,
    retirement_target_age=55,
    expected_return_pct=7.0,
    withdrawal_rate_pct=4.0,
    monthly_retirement_spending=5_000.0,
)


def _fire(**overrides: Any) -> dict:
    kwargs = {**_DEFAULT_KWARGS, **overrides}
    return compute_fire(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — output shape & required keys
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "fire_number",
    "current_nw",
    "progress_pct",
    "annual_retirement_spending",
    "monthly_retirement_spending",
    "monthly_savings",
    "expected_return_pct",
    "withdrawal_rate_pct",
    "current_age",
    "retirement_target_age",
    "years_to_fire",
    "projected_fire_age",
    "on_track",
    "monthly_savings_needed",
    "trajectory",
    "retirement_duration_years",
    "recommended_swr_pct",
    "swr_warning",
}


def test_compute_fire_returns_all_required_keys():
    result = _fire()
    assert _REQUIRED_KEYS.issubset(result.keys())


def test_compute_fire_trajectory_is_list_of_dicts():
    result = _fire()
    assert isinstance(result["trajectory"], list)
    assert len(result["trajectory"]) > 0
    point = result["trajectory"][0]
    assert {"year", "age", "nw", "target"}.issubset(point.keys())


def test_compute_fire_trajectory_first_point_is_current_nw():
    result = _fire(current_nw=500_000.0)
    assert result["trajectory"][0]["nw"] == 500_000.0


def test_compute_fire_trajectory_first_point_year_is_current_year():
    result = _fire()
    assert result["trajectory"][0]["year"] == date.today().year


def test_compute_fire_trajectory_ages_increase_by_one_per_row():
    result = _fire()
    traj = result["trajectory"]
    for i in range(1, len(traj)):
        # Each row is one year apart.
        assert pytest.approx(traj[i]["age"], abs=0.2) == traj[i - 1]["age"] + 1.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — fire_number formula
# ─────────────────────────────────────────────────────────────────────────────


def test_fire_number_annualizes_monthly_retirement_spending_once():
    assert fire_number(5_000, 4) == 1_500_000


def test_compute_fire_fire_number_is_expenses_over_swr():
    result = _fire(monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0)
    assert result["fire_number"] == pytest.approx(1_500_000.0, abs=1)


def test_compute_fire_fire_number_changes_with_withdrawal_rate():
    lower = _fire(monthly_retirement_spending=5_000.0, withdrawal_rate_pct=3.0)
    higher = _fire(monthly_retirement_spending=5_000.0, withdrawal_rate_pct=5.0)
    # Lower SWR → higher FIRE number.
    assert lower["fire_number"] > higher["fire_number"]


def test_compute_fire_fire_number_scales_with_expenses():
    cheap = _fire(monthly_retirement_spending=2_500.0, withdrawal_rate_pct=4.0)
    expensive = _fire(monthly_retirement_spending=10_000.0, withdrawal_rate_pct=4.0)
    assert expensive["fire_number"] == pytest.approx(2 * cheap["fire_number"] * 2, abs=1)


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — progress percentage
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_progress_pct_scales_with_nw():
    # fire_number = 60000/0.04 = 1,500,000. At 750k → 50%.
    result = _fire(
        current_nw=750_000.0, monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0
    )
    assert result["progress_pct"] == pytest.approx(50.0, abs=0.1)


def test_compute_fire_progress_pct_at_zero_nw():
    result = _fire(current_nw=0.0)
    assert result["progress_pct"] == 0.0


def test_compute_fire_progress_pct_above_100_when_past_fire():
    # Already past FIRE number.
    result = _fire(
        current_nw=2_000_000.0, monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0
    )
    assert result["progress_pct"] > 100.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — on_track logic
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_already_at_fire_number_is_on_track():
    # NW already equals fire_number; years_to_fire should be very small.
    result = _fire(
        current_nw=1_500_000.0,
        monthly_retirement_spending=5_000.0,
        withdrawal_rate_pct=4.0,
        monthly_savings=1.0,  # minimal savings; growth alone crosses
        retirement_target_age=55,
        current_age=35.0,
    )
    assert result["on_track"] is True


def test_compute_fire_not_on_track_when_projected_age_exceeds_target():
    # Tiny NW, tiny savings, very early retirement target → impossible to reach in time.
    result = _fire(
        current_nw=10_000.0,
        monthly_savings=100.0,
        current_age=35.0,
        retirement_target_age=36,  # only 1 year to retire!
        expected_return_pct=7.0,
        withdrawal_rate_pct=4.0,
        monthly_retirement_spending=5_000.0,
    )
    assert result["on_track"] is False


def test_compute_fire_generous_scenario_is_on_track():
    # Large NW, high savings, late retirement target → should comfortably be on track.
    result = _fire(
        current_nw=1_000_000.0,
        monthly_savings=10_000.0,
        current_age=30.0,
        retirement_target_age=65,
        expected_return_pct=7.0,
        withdrawal_rate_pct=4.0,
        monthly_retirement_spending=4_166.666666666667,
    )
    assert result["on_track"] is True


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — years_to_fire / projected_fire_age
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_years_to_fire_is_none_when_unreachable():
    # Zero savings, zero growth → will never reach FIRE.
    result = _fire(
        current_nw=0.0,
        monthly_savings=0.0,
        expected_return_pct=0.0,
        withdrawal_rate_pct=4.0,
        monthly_retirement_spending=5_000.0,
    )
    assert result["years_to_fire"] is None
    assert result["projected_fire_age"] is None


def test_compute_fire_years_to_fire_positive_when_reachable():
    result = _fire(
        current_nw=500_000.0,
        monthly_savings=3_000.0,
        current_age=35.0,
    )
    assert result["years_to_fire"] is not None
    assert result["years_to_fire"] > 0


def test_compute_fire_projected_fire_age_equals_current_age_plus_years():
    result = _fire(current_age=35.0)
    if result["years_to_fire"] is not None:
        expected = round(35.0 + result["years_to_fire"], 1)
        assert result["projected_fire_age"] == pytest.approx(expected, abs=0.15)


def test_compute_fire_already_past_fire_number_is_zero_years_away():
    """At or past the FIRE number is 0 years away, and the projected age is today's age.

    Regression: the loop stepped a month before its first check, so a book already past its
    FIRE number read "0.1 yrs to independence" on the dashboard. And 0 must stay 0 in the
    payload, not turn into None (which the widget renders as "50+ yrs")."""
    for nw in (1_500_000.0, 2_000_000.0):  # fire_number = 1,500,000: equal to, and past it
        result = _fire(
            current_nw=nw,
            monthly_retirement_spending=5_000.0,
            withdrawal_rate_pct=4.0,
            current_age=35.0,
        )
        assert result["years_to_fire"] == 0.0
        assert result["projected_fire_age"] == 35.0
        assert result["on_track"] is True


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — monthly_savings_needed
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_savings_needed_is_none_when_on_track():
    result = _fire(
        current_nw=1_000_000.0,
        monthly_savings=10_000.0,
        current_age=30.0,
        retirement_target_age=65,
        monthly_retirement_spending=4_166.666666666667,
    )
    # Should be on_track → monthly_savings_needed is None.
    assert result["on_track"] is True
    assert result["monthly_savings_needed"] is None


def test_compute_fire_savings_needed_is_none_when_years_remaining_nonpositive():
    # current_age >= retirement_target_age → years_remaining ≤ 0.
    result = _fire(
        current_age=60.0,
        retirement_target_age=55,  # already past target
        current_nw=100_000.0,
        monthly_retirement_spending=5_000.0,
    )
    assert result["monthly_savings_needed"] is None


def test_compute_fire_savings_needed_is_non_negative():
    # When not on track, the needed savings must be ≥ 0 (clamped by max(0, ...)).
    result = _fire(
        current_nw=10_000.0,
        monthly_savings=100.0,
        current_age=35.0,
        retirement_target_age=40,
        monthly_retirement_spending=5_000.0,
    )
    if result["monthly_savings_needed"] is not None:
        assert result["monthly_savings_needed"] >= 0.0


def test_compute_fire_savings_needed_is_integer_rounded():
    result = _fire(
        current_nw=10_000.0,
        monthly_savings=100.0,
        current_age=35.0,
        retirement_target_age=50,
        monthly_retirement_spending=5_000.0,
    )
    if result["monthly_savings_needed"] is not None:
        # round(x, 0) returns a float but should have no fractional component.
        assert result["monthly_savings_needed"] % 1.0 == 0.0


def test_compute_fire_savings_needed_decreases_with_higher_nw():
    low_nw = _fire(
        current_nw=50_000.0, retirement_target_age=50, monthly_retirement_spending=5_000.0
    )
    high_nw = _fire(
        current_nw=800_000.0, retirement_target_age=50, monthly_retirement_spending=5_000.0
    )
    # If both are not on_track, higher NW should require fewer extra savings.
    if (
        low_nw["monthly_savings_needed"] is not None
        and high_nw["monthly_savings_needed"] is not None
    ):
        assert low_nw["monthly_savings_needed"] >= high_nw["monthly_savings_needed"]


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — swr_warning
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_swr_warning_true_when_user_rate_exceeds_recommended():
    # retirement_target_age=55, LIFE_EXPECTANCY=90 → duration=35 → rec_swr=3.75.
    # Using 5.0% → aggressive → warning should be True.
    result = _fire(
        retirement_target_age=55,
        withdrawal_rate_pct=5.0,
        monthly_retirement_spending=5_000.0,
    )
    assert result["swr_warning"] is True


def test_compute_fire_swr_warning_false_when_user_rate_at_recommended():
    # retirement_target_age=70, duration=20 → rec_swr=5.0. Using 4.0% → conservative.
    result = _fire(
        retirement_target_age=70,
        withdrawal_rate_pct=4.0,
        monthly_retirement_spending=5_000.0,
    )
    assert result["swr_warning"] is False


def test_compute_fire_swr_warning_false_when_user_rate_equals_recommended():
    # duration=35 → rec_swr=3.75. Using exactly 3.75 → not more aggressive.
    result = _fire(
        retirement_target_age=55,
        withdrawal_rate_pct=3.75,
        monthly_retirement_spending=5_000.0,
    )
    assert result["swr_warning"] is False


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — retirement_duration_years
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_retirement_duration_is_life_expectancy_minus_retirement_age():
    result = _fire(retirement_target_age=55)
    assert result["retirement_duration_years"] == pytest.approx(LIFE_EXPECTANCY - 55, abs=0.1)


def test_compute_fire_retirement_duration_minimum_is_one():
    # target_age >= LIFE_EXPECTANCY → max(1.0, ...) floor.
    result = _fire(retirement_target_age=LIFE_EXPECTANCY + 5)
    assert result["retirement_duration_years"] >= 1.0


def test_compute_fire_default_life_expectancy_is_module_constant():
    # No life_expectancy passed → falls back to LIFE_EXPECTANCY (90).
    result = _fire(retirement_target_age=65, withdrawal_rate_pct=4.0)
    assert result["retirement_duration_years"] == pytest.approx(LIFE_EXPECTANCY - 65, abs=0.1)


def test_compute_fire_custom_life_expectancy_changes_duration_and_swr_warning():
    """MATH-29 regression: a user-set life_expectancy=100 (retire at 65, SWR 4%) must
    give duration 35 (not the hardcoded 25), recommended_swr_pct 3.75 (not 4.5), and
    swr_warning True (not False) — probe values from docs/review/money-math-audit.md."""
    result = _fire(
        retirement_target_age=65,
        withdrawal_rate_pct=4.0,
        life_expectancy=100,
    )
    assert result["retirement_duration_years"] == pytest.approx(35.0, abs=0.1)
    assert result["recommended_swr_pct"] == recommended_swr(35)
    assert result["recommended_swr_pct"] == 3.75
    assert result["swr_warning"] is True


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — recommended_swr_pct echoed through
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_recommended_swr_pct_matches_standalone_function():
    result = _fire(retirement_target_age=55)
    duration = LIFE_EXPECTANCY - 55
    assert result["recommended_swr_pct"] == recommended_swr(duration)


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — trajectory truncation
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_trajectory_truncated_at_3x_fire_number():
    """The loop breaks when NW >= fire_number * 3; trajectory shouldn't run all 50 years."""
    result = _fire(
        current_nw=0.0,
        monthly_savings=20_000.0,
        current_age=30.0,
        retirement_target_age=65,
        monthly_retirement_spending=1_666.666666666667,  # fire_number = 500,000
        expected_return_pct=10.0,
    )
    # With 10% return and $20k/mo savings this should reach 3x in << 50 years.
    assert len(result["trajectory"]) < MAX_PROJECTION_YEARS + 1


def test_compute_fire_trajectory_at_most_max_projection_years_plus_one_rows():
    # Even in the worst case the trajectory is bounded.
    result = _fire(
        current_nw=0.0,
        monthly_savings=1.0,
        expected_return_pct=0.0,
        monthly_retirement_spending=8_333_333.333333333,  # enormous fire_number → never 3x
    )
    assert len(result["trajectory"]) <= MAX_PROJECTION_YEARS + 1


def test_compute_fire_trajectory_trimmed_after_crossing():
    """Trajectory is trimmed to max(15, fire_cross_idx+10), capped by however many rows
    the 3x-growth or MAX_PROJECTION_YEARS limit produced.  It must include the crossing year."""
    result = _fire(
        current_nw=1_400_000.0,  # crosses in year 1 (1.56M >= 1.5M)
        monthly_savings=5_000.0,
        monthly_retirement_spending=5_000.0,
        withdrawal_rate_pct=4.0,
    )
    traj = result["trajectory"]
    # fire_number = 1,500,000.  The loop breaks when nw >= 4,500,000 (3x).
    # With these params that happens at year 13 → raw 14 rows.
    # Trim target = max(15, 1+10) = 15 but only 14 rows exist → stays at 14.
    fire_cross_idx = next((i for i, p in enumerate(traj) if p["nw"] >= result["fire_number"]), None)
    assert fire_cross_idx is not None, "Expected crossing within trajectory"
    # At minimum the crossing point itself must be present.
    assert len(traj) > fire_cross_idx


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — zero / negative edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_zero_savings_and_nw_never_reaches_fire():
    result = _fire(current_nw=0.0, monthly_savings=0.0, expected_return_pct=0.0)
    assert result["years_to_fire"] is None
    assert result["on_track"] is False


def test_compute_fire_negative_nw_handled_gracefully():
    """Negative NW (net debts) should not raise; progress_pct should be ≤ 0."""
    result = _fire(current_nw=-100_000.0)
    assert result["progress_pct"] <= 0.0
    assert isinstance(result["fire_number"], (float, int))


def test_compute_fire_negative_nw_progress_is_negative():
    result = _fire(
        current_nw=-50_000.0, monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0
    )
    # fire_number = 1,500,000 → progress = -50000/1500000 * 100 < 0
    assert result["progress_pct"] < 0.0


def test_compute_fire_very_high_return_reaches_fire_faster():
    low_return = _fire(expected_return_pct=2.0)
    high_return = _fire(expected_return_pct=12.0)
    if low_return["years_to_fire"] is not None and high_return["years_to_fire"] is not None:
        assert high_return["years_to_fire"] < low_return["years_to_fire"]


def test_compute_fire_zero_return_still_works():
    """0% return should not cause a division error and should compute correctly."""
    result = _fire(
        current_nw=0.0,
        monthly_savings=5_000.0,
        expected_return_pct=0.0,
        monthly_retirement_spending=5_000.0,
        withdrawal_rate_pct=4.0,
    )
    # fire_number = 1,500,000; at 0% return and $5000/mo savings: 1,500,000/5,000 = 300 months = 25 yrs
    assert result["years_to_fire"] is not None
    assert result["years_to_fire"] == pytest.approx(25.0, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — output types / JSON-serialisability
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_result_is_json_serialisable():
    result = _fire()
    # Should not raise.
    serialised = json.dumps(result)
    assert len(serialised) > 0


def test_compute_fire_numeric_fields_are_finite():
    result = _fire()
    for key in (
        "fire_number",
        "progress_pct",
        "current_nw",
        "annual_retirement_spending",
        "monthly_savings",
    ):
        assert math.isfinite(result[key]), f"{key} is not finite"


def test_compute_fire_passthrough_scalar_fields():
    result = _fire(
        expected_return_pct=6.5,
        withdrawal_rate_pct=3.5,
        retirement_target_age=60,
        current_age=40.0,
        monthly_retirement_spending=6_666.666666666667,
        monthly_savings=2_500.0,
    )
    assert result["expected_return_pct"] == 6.5
    assert result["withdrawal_rate_pct"] == 3.5
    assert result["retirement_target_age"] == 60
    assert result["current_age"] == pytest.approx(40.0, abs=0.1)
    assert result["annual_retirement_spending"] == 80_000.0
    assert result["monthly_savings"] == 2_500.0


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — delegates to the module-level fire_number() helper (MATH-47)
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_delegates_to_fire_number_helper(monkeypatch):
    """MATH-47: compute_fire's fire_number must be computed via the module-level
    fire_number() helper (the single source of truth also called directly by callers
    like the AI system prompt) instead of restating the ratio inline. Proven via a
    sentinel monkeypatch, not numeric equality — a duplicated-but-consistent formula
    would also pass a numeric check."""
    import backend.services.fire as fire_module

    monkeypatch.setattr(
        fire_module,
        "fire_number",
        lambda monthly_retirement_spending, withdrawal_rate_pct: 999_999.0,
    )
    result = _fire(monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0)
    assert result["fire_number"] == pytest.approx(999_999.0)


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire — current_age=None degrades age-anchored fields (MATH-28)
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_none_age_reports_none_current_age():
    result = _fire(current_age=None)
    assert result["current_age"] is None


def test_compute_fire_none_age_projected_fire_age_is_none():
    result = _fire(current_age=None, current_nw=500_000.0, monthly_savings=3_000.0)
    assert result["years_to_fire"] is not None  # age-independent, still computed
    assert result["projected_fire_age"] is None


def test_compute_fire_none_age_on_track_is_none():
    # Even a generous scenario that WOULD be on_track with a known age reports None,
    # never a fabricated True/False verdict.
    result = _fire(
        current_age=None,
        current_nw=1_000_000.0,
        monthly_savings=10_000.0,
        retirement_target_age=65,
        monthly_retirement_spending=4_166.666666666667,
    )
    assert result["on_track"] is None


def test_compute_fire_none_age_trajectory_has_no_age():
    result = _fire(current_age=None)
    for point in result["trajectory"]:
        assert point["age"] is None
    # The years-based figures are unaffected — the trajectory still has NW/target/year rows.
    assert result["trajectory"][0]["nw"] == result["current_nw"]
    assert result["trajectory"][0]["year"] == date.today().year


def test_compute_fire_none_age_monthly_savings_needed_is_none():
    result = _fire(
        current_age=None,
        current_nw=10_000.0,
        monthly_savings=100.0,
        retirement_target_age=40,
        monthly_retirement_spending=5_000.0,
    )
    assert result["monthly_savings_needed"] is None


def test_compute_fire_none_age_fire_number_and_years_to_fire_unaffected():
    """Age-independent figures must be identical with and without a known age."""
    with_age = _fire(current_age=35.0, monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0)
    without_age = _fire(
        current_age=None, monthly_retirement_spending=5_000.0, withdrawal_rate_pct=4.0
    )
    assert with_age["fire_number"] == without_age["fire_number"]
    assert with_age["years_to_fire"] == without_age["years_to_fire"]
    assert with_age["progress_pct"] == without_age["progress_pct"]


# ─────────────────────────────────────────────────────────────────────────────
# compute_fire / fire_number — zero/negative withdrawal rate and expenses raise (MATH-32)
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_fire_raises_on_zero_withdrawal_rate():
    with pytest.raises(ValidationError):
        _fire(withdrawal_rate_pct=0.0)


def test_compute_fire_raises_on_negative_withdrawal_rate():
    with pytest.raises(ValidationError):
        _fire(withdrawal_rate_pct=-4.0)


def test_compute_fire_raises_on_negative_monthly_retirement_spending():
    with pytest.raises(ValidationError):
        _fire(monthly_retirement_spending=-60_000.0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 1e308])
def test_fire_number_rejects_non_finite_or_overflowing_monthly_spending(value):
    with pytest.raises(ValidationError, match="monthly_retirement_spending|FIRE number"):
        fire_number(value, 4)


# ─────────────────────────────────────────────────────────────────────────────
# derive_fire_inputs — single derivation point for both callers (MATH-31)
# ─────────────────────────────────────────────────────────────────────────────


def _profile(**overrides: Any) -> dict:
    base: dict[str, Any] = {
        "monthly_net_income": 8_000.0,
        "monthly_expenses": 5_000.0,
        "fire_expected_return_pct": 6.5,
        "fire_withdrawal_rate_pct": 3.5,
        "fire_monthly_retirement_spending": None,
        "retirement_target_age": 60,
        "date_of_birth": None,
        "fire_life_expectancy": None,
    }
    base.update(overrides)
    return base


def test_derive_fire_inputs_reads_stored_profile_values():
    inputs = derive_fire_inputs(_profile())
    assert inputs["monthly_savings"] == pytest.approx(3_000.0)
    assert inputs["expected_return_pct"] == 6.5
    assert inputs["withdrawal_rate_pct"] == 3.5
    assert inputs["retirement_target_age"] == 60
    assert inputs["monthly_retirement_spending"] == pytest.approx(5_000.0)
    assert inputs["current_age"] is None
    assert inputs["life_expectancy"] == float(LIFE_EXPECTANCY)


def test_derive_fire_inputs_falls_back_to_module_defaults_when_unset():
    inputs = derive_fire_inputs(
        _profile(fire_expected_return_pct=None, fire_withdrawal_rate_pct=None)
    )
    assert inputs["expected_return_pct"] == DEFAULT_RETURN_PCT
    assert inputs["withdrawal_rate_pct"] == DEFAULT_WITHDRAWAL_PCT


def test_derive_fire_inputs_passes_through_an_invalid_stored_rate_unmodified():
    """MATH-32: derive_fire_inputs must NOT mask an invalid stored value with the default
    via `or` — that hides the problem instead of letting compute_fire raise on it."""
    inputs = derive_fire_inputs(_profile(fire_withdrawal_rate_pct=0.0))
    assert inputs["withdrawal_rate_pct"] == 0.0
    with pytest.raises(ValidationError):
        compute_fire(current_nw=100_000.0, **inputs)


def test_derive_fire_inputs_prefers_explicit_retirement_spending_over_living_expenses():
    inputs = derive_fire_inputs(_profile(fire_monthly_retirement_spending=3_500.0))
    assert inputs["monthly_retirement_spending"] == 3_500.0


def test_fire_uses_canonical_monthly_retirement_spending_and_annualizes_once():
    inputs = derive_fire_inputs(_profile(fire_monthly_retirement_spending=3_500.0))

    assert inputs["monthly_retirement_spending"] == 3_500.0
    result = compute_fire(current_nw=100_000.0, **inputs)
    assert result["monthly_retirement_spending"] == 3_500.0
    assert result["annual_retirement_spending"] == 42_000.0
    assert result["fire_number"] == 1_200_000.0


def test_derive_fire_inputs_reads_date_of_birth_via_current_age():
    today = date.today()
    dob = date(today.year - 40, today.month, today.day)
    inputs = derive_fire_inputs(_profile(date_of_birth=dob.isoformat()))
    assert inputs["current_age"] == pytest.approx(40.0, abs=0.1)


def test_derive_fire_inputs_life_expectancy_not_after_retirement_falls_back():
    inputs = derive_fire_inputs(_profile(retirement_target_age=65, fire_life_expectancy=60))
    assert inputs["life_expectancy"] == float(LIFE_EXPECTANCY)


# ── overrides use `is not None`, never `or` (MATH-30 semantics, unified here) ──


def test_derive_fire_inputs_zero_override_is_honored_not_defaulted():
    inputs = derive_fire_inputs(_profile(), overrides={"monthly_savings": 0.0})
    assert inputs["monthly_savings"] == 0.0


def test_derive_fire_inputs_zero_return_override_is_honored():
    inputs = derive_fire_inputs(_profile(), overrides={"expected_return_pct": 0.0})
    assert inputs["expected_return_pct"] == 0.0


def test_derive_fire_inputs_override_takes_precedence_over_profile():
    inputs = derive_fire_inputs(
        _profile(fire_expected_return_pct=6.5), overrides={"expected_return_pct": 9.0}
    )
    assert inputs["expected_return_pct"] == 9.0


def test_derive_fire_inputs_none_override_falls_back_to_profile():
    inputs = derive_fire_inputs(
        _profile(fire_withdrawal_rate_pct=3.5), overrides={"withdrawal_rate_pct": None}
    )
    assert inputs["withdrawal_rate_pct"] == 3.5


# ── both real callers delegate to derive_fire_inputs, not their own derivation ──


def test_api_fire_delegates_to_derive_fire_inputs(monkeypatch):
    import backend.api.fire as api_fire_module
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw(_profile())
    sentinel = dict(
        monthly_savings=1.0,
        expected_return_pct=1.0,
        withdrawal_rate_pct=1.0,
        retirement_target_age=99,
        monthly_retirement_spending=0.08333333333333333,
        current_age=1.0,
        life_expectancy=1.0,
    )
    monkeypatch.setattr(api_fire_module, "derive_fire_inputs", lambda profile: sentinel)
    result = api_fire_module.get_fire(current_nw=0.0)
    assert result["retirement_target_age"] == 99


def test_tool_get_fire_projection_delegates_to_derive_fire_inputs(monkeypatch):
    import asyncio

    import backend.services.fire as fire_module
    from backend.ai.tools import execute_tool
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw(_profile())
    sentinel = dict(
        monthly_savings=1.0,
        expected_return_pct=1.0,
        withdrawal_rate_pct=1.0,
        retirement_target_age=99,
        monthly_retirement_spending=0.08333333333333333,
        current_age=1.0,
        life_expectancy=1.0,
    )
    # get_fire_projection imports derive_fire_inputs locally (lazy, avoids the api/ai import
    # cycle) — so the monkeypatch target is the real defining module, not the tool module.
    monkeypatch.setattr(
        fire_module,
        "derive_fire_inputs",
        lambda profile, overrides=None: sentinel,
    )
    body, _ = asyncio.run(execute_tool("get_fire_projection", {"current_nw_base": 0.0}))
    result = json.loads(body)
    assert result["retirement_target_age"] == 99


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/profile — a zero/negative fire_withdrawal_rate_pct is refused on write (MATH-32)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def profile_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.profile import router as profile_router

    app = FastAPI()
    app.include_router(profile_router)
    return TestClient(app)


def test_put_profile_rejects_zero_withdrawal_rate(profile_client):
    r = profile_client.put("/api/profile", json={"fire_withdrawal_rate_pct": 0.0})
    assert r.status_code == 422


def test_put_profile_rejects_negative_withdrawal_rate(profile_client):
    r = profile_client.put("/api/profile", json={"fire_withdrawal_rate_pct": -4.0})
    assert r.status_code == 422


def test_put_profile_accepts_a_valid_withdrawal_rate(profile_client):
    r = profile_client.put("/api/profile", json={"fire_withdrawal_rate_pct": 3.5})
    assert r.status_code == 200
    assert r.json()["profile"]["fire_withdrawal_rate_pct"] == 3.5
