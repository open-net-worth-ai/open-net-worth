"""Unit tests for backend.ai.tools.profile.

Covers all four @tool handlers:
  - get_profile
  - get_fire_projection
  - simulate_retirement
  - update_profile
  - dismiss_base_currency_review

Tests verify:
  * Shape of returned JSON (required keys present)
  * proposed_change is always None (all are reads/writes, not proposals)
  * update_profile merges changes, coerces numeric types, normalizes allocations
  * update_profile deep-merges briefing_preferences
  * update_profile triggers base-currency side effects when currency changes
  * dismiss_base_currency_review clears the flag
  * FIRE projection strips trajectory from AI output
  * simulate_retirement returns a bands_sample (not the full verbose bands)

Offline, isolated to tmp_path; no LLM / network calls.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.services.profile import read_profile

# ── helpers ───────────────────────────────────────────────────────────────────


def call(tool: str, **inp):
    """Run execute_tool synchronously; return (parsed_dict, proposed_change)."""
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── path isolation fixture ────────────────────────────────────────────────────


@pytest.fixture()
def isolated_profile(tmp_path, monkeypatch):
    """profile is DB-backed now (Wave 5, JSON->DB) — conftest's autouse fixture already
    resets it to {} before every test, no path monkeypatch needed; yields the
    ProfileStore itself for tests that seed specific content. FX cache path is still
    redirected so handle_base_currency_change can delete it safely."""
    import backend.core.paths as core_paths
    from backend.db.profile_store import ProfileStore

    fx_path = tmp_path / "fx_cache.json"

    # Patch FX cache path so handle_base_currency_change can delete it safely
    monkeypatch.setattr(core_paths, "FX_CACHE_JSON", fx_path)
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx_path)

    yield ProfileStore()


# ══════════════════════════════════════════════════════════════════════════════
# get_profile
# ══════════════════════════════════════════════════════════════════════════════


class TestGetProfile:
    def test_proposed_is_none(self, isolated_profile):
        _, proposed = call("get_profile")
        assert proposed is None

    def test_returns_dict(self, isolated_profile):
        body, _ = call("get_profile")
        assert isinstance(body, dict)

    def test_default_profile_risk_tolerance(self, isolated_profile):
        """A fresh profile has risk_tolerance='moderate' by default."""
        body, _ = call("get_profile")
        assert body.get("risk_tolerance") == "moderate"

    def test_default_profile_has_briefing_prefs(self, isolated_profile):
        """A fresh profile includes briefing_preferences with the expected boolean flags."""
        body, _ = call("get_profile")
        prefs = body["settings"].get("briefing_preferences", {})
        for flag in ("holdings_news", "fx", "spending", "accounts", "upcoming_actions"):
            assert flag in prefs, f"briefing_preferences missing '{flag}'"

    def test_default_profile_fire_rates_present(self, isolated_profile):
        """Default FIRE return and withdrawal rates are present."""
        body, _ = call("get_profile")
        assert "fire_expected_return_pct" in body
        assert "fire_withdrawal_rate_pct" in body

    def test_reads_persisted_value(self, isolated_profile):
        """After writing a profile, get_profile returns the updated value."""
        import backend.ai.tools as tools_root

        profile = tools_root.read_profile()
        profile["name"] = "Alice"
        tools_root.write_profile(profile)

        body, _ = call("get_profile")
        assert body.get("name") == "Alice"

    def test_no_goals_field_leaked(self, isolated_profile):
        """The legacy 'goals' field is stripped from the returned profile."""
        isolated_profile.set_raw({"name": "Bob", "goals": [{"id": "g1"}]})
        body, _ = call("get_profile")
        assert "goals" not in body


# ══════════════════════════════════════════════════════════════════════════════
# update_profile
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateProfile:
    def test_proposed_is_none(self, isolated_profile):
        _, proposed = call("update_profile", changes={"risk_tolerance": "aggressive"})
        assert proposed is None

    def test_returns_ok_true(self, isolated_profile):
        body, _ = call("update_profile", changes={"risk_tolerance": "conservative"})
        assert body.get("ok") is True
        # A real write's shape is byte-identical to before the no-op guard — no `no_op` key.
        assert "no_op" not in body

    def test_returns_updated_profile(self, isolated_profile):
        body, _ = call("update_profile", changes={"risk_tolerance": "aggressive"})
        assert "profile" in body
        assert body["profile"]["risk_tolerance"] == "aggressive"

    def test_instructions_change_reports_the_line_diff(self, isolated_profile):
        call("update_profile", changes={"notes": "Always reply briefly.\nUse SGD."})
        body, _ = call(
            "update_profile",
            changes={"notes": "Always reply briefly.\nMeal rule: ACME dinner after 17:00."},
        )
        assert body["changed"] == [
            {
                "field": "notes",
                "label": "Instructions",
                "type": "text",
                "diff": {"added": ["Meal rule: ACME dinner after 17:00."], "removed": ["Use SGD."]},
            }
        ]

    def test_unchanged_or_other_fields_report_no_instructions_diff(self, isolated_profile):
        call("update_profile", changes={"notes": "Use SGD."})
        same, _ = call("update_profile", changes={"notes": "Use SGD."})
        other, _ = call("update_profile", changes={"risk_tolerance": "aggressive"})
        assert "changed" not in same
        assert "changed" not in other

    def test_updates_string_field(self, isolated_profile):
        call("update_profile", changes={"name": "Carol"})
        body, _ = call("get_profile")
        assert body.get("name") == "Carol"

    def test_import_review_default_flows_and_normalizes(self, isolated_profile):
        """import_review_default passes through the tool; junk normalizes to the factory
        default 'merge' (owner 2026-07-17); explicit 'skip' persists as the opt-out."""
        call("update_profile", changes={"import_review_default": "skip"})
        body, _ = call("get_profile")
        assert body["settings"]["import_review_default"] == "skip"
        call("update_profile", changes={"import_review_default": "bogus"})
        body, _ = call("get_profile")
        assert body["settings"]["import_review_default"] == "merge"

    def test_coerces_retirement_age_to_int(self, isolated_profile):
        """retirement_target_age must be coerced to int, even when sent as a float string."""
        call("update_profile", changes={"retirement_target_age": "55.0"})
        body, _ = call("get_profile")
        assert body["retirement_target_age"] == 55
        assert isinstance(body["retirement_target_age"], int)

    def test_coerces_monthly_income_to_float(self, isolated_profile):
        call("update_profile", changes={"monthly_net_income": "8500"})
        body, _ = call("get_profile")
        assert body["monthly_net_income"] == 8500.0
        assert isinstance(body["monthly_net_income"], float)

    def test_coerces_monthly_expenses_to_float(self, isolated_profile):
        call("update_profile", changes={"monthly_expenses": "3000"})
        body, _ = call("get_profile")
        assert body["monthly_expenses"] == 3000.0

    def test_coerces_fire_return_pct_to_float(self, isolated_profile):
        call("update_profile", changes={"fire_expected_return_pct": "8"})
        body, _ = call("get_profile")
        assert body["fire_expected_return_pct"] == 8.0

    def test_coerces_fire_withdrawal_rate_to_float(self, isolated_profile):
        call("update_profile", changes={"fire_withdrawal_rate_pct": "3.5"})
        body, _ = call("get_profile")
        assert body["fire_withdrawal_rate_pct"] == 3.5

    def test_coerces_fire_monthly_retirement_spending_to_float(self, isolated_profile):
        call("update_profile", changes={"fire_monthly_retirement_spending": "10000"})
        body, _ = call("get_profile")
        assert body["fire_monthly_retirement_spending"] == 10000.0

    def test_coerces_cashflow_threshold_to_float(self, isolated_profile):
        call("update_profile", changes={"cashflow_low_balance_threshold": "500"})
        body, _ = call("get_profile")
        assert body["settings"]["cashflow_low_balance_threshold"] == 500.0

    def test_normalizes_target_allocation_fractions(self, isolated_profile):
        """Allocation values that are already fractions (≤1) pass through unchanged."""
        call(
            "update_profile",
            changes={"target_allocation": {"equity": 0.6, "bonds": 0.4}},
        )
        alloc = read_profile().get("target_allocation", {})  # get_rebalance_plan reads it
        assert abs(alloc.get("equity", 0) - 0.6) < 0.001
        assert abs(alloc.get("bonds", 0) - 0.4) < 0.001

    def test_normalizes_target_allocation_percentages(self, isolated_profile):
        """Allocation values expressed as percentages (>1) are divided by 100."""
        call(
            "update_profile",
            changes={"target_allocation": {"equity": 60, "crypto": 20, "commodity": 20}},
        )
        alloc = read_profile().get("target_allocation", {})  # get_rebalance_plan reads it
        assert abs(alloc.get("equity", 0) - 0.6) < 0.001
        assert abs(alloc.get("crypto", 0) - 0.2) < 0.001

    def test_deep_merges_briefing_preferences(self, isolated_profile):
        """Passing a partial briefing_preferences block keeps existing flags intact."""
        # Set fx=False initially.
        call("update_profile", changes={"briefing_preferences": {"fx": False}})
        # Now update only spending; fx must remain False.
        call("update_profile", changes={"briefing_preferences": {"spending": False}})
        body, _ = call("get_profile")
        prefs = body["settings"]["briefing_preferences"]
        assert prefs["fx"] is False, "fx flag should be preserved after partial update"
        assert prefs["spending"] is False

    def test_briefing_preferences_custom_focus_stored(self, isolated_profile):
        call(
            "update_profile",
            changes={"briefing_preferences": {"custom_focus": "Focus on tech holdings"}},
        )
        body, _ = call("get_profile")
        assert body["settings"]["briefing_preferences"]["custom_focus"] == "Focus on tech holdings"

    def test_updates_liabilities_list(self, isolated_profile):
        liabilities = [{"name": "Car loan", "balance": 30000, "monthly_payment": 600}]
        call("update_profile", changes={"liabilities": liabilities})
        body, _ = call("get_profile")
        assert body["liabilities"] == liabilities

    def test_updates_notes(self, isolated_profile):
        call("update_profile", changes={"notes": "Expecting a bonus in Q4"})
        body, _ = call("get_profile")
        assert body["notes"] == "Expecting a bonus in Q4"

    def test_base_currency_change_stamps_review_flag(self, isolated_profile):
        """Changing base_currency when manual amounts exist stamps base_currency_review."""
        # Pre-seed some manual amounts so the flag fires.
        call(
            "update_profile",
            changes={
                "base_currency": "SGD",
                "monthly_net_income": 10000.0,
                "monthly_expenses": 5000.0,
            },
        )
        # Now change the currency.
        body, _ = call("update_profile", changes={"base_currency": "USD"})
        profile = body["profile"]
        # The review flag should be set (manual amounts present → stamp required).
        assert "base_currency_review" in profile
        review = profile["base_currency_review"]
        assert review["from"] == "SGD"
        assert review["to"] == "USD"
        assert review["fields"]

    def test_no_base_currency_review_when_no_manual_amounts(self, isolated_profile):
        """No review flag is stamped when there are no manual amounts to re-enter."""
        # Set base_currency once with no manual amounts.
        call("update_profile", changes={"base_currency": "SGD"})
        # Change it — profile has no income/expense/etc, so no flag needed.
        body, _ = call("update_profile", changes={"base_currency": "USD"})
        # Flag should NOT be present (or fields list is empty).
        profile = body["profile"]
        review = profile.get("base_currency_review")
        if review:
            assert not review.get("fields"), "no manual amounts → empty fields list"

    def test_same_currency_no_review_flag(self, isolated_profile):
        """Setting base_currency to the same value as already stored must not trigger a new
        currency-change review (the old_base == new_base guard returns early)."""
        # First: set the currency with NO manual amounts, so no review flag is stamped.
        call("update_profile", changes={"base_currency": "SGD"})
        # Second: set the same currency again — guard must fire, no review stamped.
        body, _ = call("update_profile", changes={"base_currency": "SGD"})
        assert "base_currency_review" not in body["profile"]

    def test_reason_field_ignored_gracefully(self, isolated_profile):
        """A 'reason' input does not crash the handler (it's informational only)."""
        body, _ = call(
            "update_profile",
            changes={"risk_tolerance": "moderate"},
            reason="User requested change",
        )
        assert body["ok"] is True

    def test_empty_changes_is_no_op_and_does_not_write(self, isolated_profile, monkeypatch):
        """An empty `changes` dict must return no_op:true (never a plain ok:true success
        indistinguishable from a real edit) and must NOT write the profile back — the
        pre-fix behavior called write_profile() unconditionally even with nothing to change."""
        import backend.ai.tools as tools_root

        profile = tools_root.read_profile()
        profile["name"] = "Dana"
        tools_root.write_profile(profile)

        write_calls = []
        monkeypatch.setattr(tools_root, "write_profile", lambda p: write_calls.append(p))

        body, proposed = call("update_profile", changes={})

        assert body["ok"] is True
        assert body["no_op"] is True
        assert "message" in body
        assert body["profile"]["name"] == "Dana"
        assert proposed is None
        assert write_calls == [], "an empty-changes call must never call write_profile()"

    def test_omitted_changes_key_is_also_a_no_op(self, isolated_profile, monkeypatch):
        """update_profile called with no `changes` key at all (defaults to {}) takes the
        same no-op path — not a KeyError, and not a silent unconditional write."""
        import backend.ai.tools as tools_root

        write_calls = []
        monkeypatch.setattr(tools_root, "write_profile", lambda p: write_calls.append(p))

        body, _ = call("update_profile")

        assert body["ok"] is True
        assert body["no_op"] is True
        assert write_calls == []

    def test_invalid_numeric_string_for_numeric_field_is_rejected(self, isolated_profile):
        """A non-coercible string for a numeric field is now REJECTED at the schema boundary —
        it never reaches the store, unlike the old silent-skip-and-still-succeed behavior
        (which actually let a raw garbage string land straight on the profile whenever it
        DIDN'T look coercible enough to trip the handler's own try/except)."""
        call("update_profile", changes={"monthly_net_income": 5000.0})
        body, _ = call("update_profile", changes={"monthly_net_income": "not-a-number"})
        assert "error" in body
        assert "monthly_net_income" in body["error"]

    def test_invalid_cashflow_threshold_string_is_rejected(self, isolated_profile):
        """A non-coercible string for cashflow_low_balance_threshold is rejected outright."""
        body, _ = call(
            "update_profile",
            changes={"cashflow_low_balance_threshold": "not-a-number"},
        )
        assert "error" in body
        assert "cashflow_low_balance_threshold" in body["error"]


# ══════════════════════════════════════════════════════════════════════════════
# dismiss_base_currency_review
# ══════════════════════════════════════════════════════════════════════════════


class TestDismissBaseCurrencyReview:
    def test_proposed_is_none(self, isolated_profile):
        _, proposed = call("dismiss_base_currency_review")
        assert proposed is None

    def test_returns_ok_true(self, isolated_profile):
        body, _ = call("dismiss_base_currency_review")
        assert body.get("ok") is True

    def test_cleared_false_when_no_flag(self, isolated_profile):
        """If there is no base_currency_review flag, cleared must be False."""
        body, _ = call("dismiss_base_currency_review")
        assert body.get("cleared") is False

    def test_cleared_true_when_flag_existed(self, isolated_profile):
        """After a currency change, dismiss must return cleared=True and remove the flag."""
        # Plant a base_currency_review flag via update_profile.
        import backend.ai.tools as tools_root

        profile = tools_root.read_profile()
        profile["base_currency_review"] = {
            "from": "SGD",
            "to": "USD",
            "changed_at": "2026-06-20T00:00:00",
            "fields": [{"label": "Monthly net income", "value": 10000.0}],
        }
        tools_root.write_profile(profile)

        body, _ = call("dismiss_base_currency_review")
        assert body.get("ok") is True
        assert body.get("cleared") is True

    def test_flag_is_removed_from_profile(self, isolated_profile):
        """After dismiss, get_profile must not include the base_currency_review field."""
        import backend.ai.tools as tools_root

        profile = tools_root.read_profile()
        profile["base_currency_review"] = {
            "from": "SGD",
            "to": "EUR",
            "changed_at": "2026-06-20T00:00:00",
            "fields": [],
        }
        tools_root.write_profile(profile)

        call("dismiss_base_currency_review")

        refreshed, _ = call("get_profile")
        assert "base_currency_review" not in refreshed

    def test_idempotent_second_call(self, isolated_profile):
        """Calling dismiss twice is safe — second call returns cleared=False."""
        import backend.ai.tools as tools_root

        profile = tools_root.read_profile()
        profile["base_currency_review"] = {
            "from": "SGD",
            "to": "USD",
            "changed_at": "now",
            "fields": [],
        }
        tools_root.write_profile(profile)

        call("dismiss_base_currency_review")
        body, _ = call("dismiss_base_currency_review")
        assert body["ok"] is True
        assert body["cleared"] is False


# ══════════════════════════════════════════════════════════════════════════════
# get_fire_projection
# ══════════════════════════════════════════════════════════════════════════════


class TestGetFireProjection:
    def test_proposed_is_none(self, isolated_profile, monkeypatch):
        """get_fire_projection always returns None for proposed_change."""
        from backend.services import fire as fire_svc

        monkeypatch.setattr(
            fire_svc,
            "compute_fire",
            lambda **kw: {
                "fire_number": 1_000_000,
                "progress_pct": 25.0,
                "on_track": False,
                "trajectory": [{"year": 2026, "balance": 250_000}],
            },
        )
        _, proposed = call("get_fire_projection", current_nw_base=250_000)
        assert proposed is None

    def test_the_retirement_verdict_is_not_in_the_model_facing_result(
        self, isolated_profile, monkeypatch
    ):
        """simulate_retirement is the ONE retirement answer. A persona run quoted this tool's
        "on track at 59" beside the simulation's 68 — the timing and verdict fields must not
        reach the model at all; only the FIRE number and progress do."""
        from backend.services import fire as fire_svc

        monkeypatch.setattr(
            fire_svc,
            "compute_fire",
            lambda **kw: {
                "fire_number": 2_000_000,
                "progress_pct": 12.5,
                "on_track": True,
                "projected_fire_age": 59.0,
                "years_to_fire": 15.0,
                "monthly_savings_needed": 0.0,
                "monthly_savings": 2_000.0,
                "expected_return_pct": 7.0,
                "trajectory": [{"year": 2030, "balance": 500_000}],
            },
        )
        body, _ = call("get_fire_projection", current_nw_base=250_000)
        assert body == {"fire_number": 2_000_000, "progress_pct": 12.5}

    def test_trajectory_stripped(self, isolated_profile, monkeypatch):
        """The 'trajectory' key must NOT appear in the AI output (too verbose)."""
        from backend.services import fire as fire_svc

        monkeypatch.setattr(
            fire_svc,
            "compute_fire",
            lambda **kw: {
                "fire_number": 2_000_000,
                "progress_pct": 12.5,
                "on_track": False,
                "trajectory": [{"year": 2030, "balance": 500_000}],
            },
        )
        body, _ = call("get_fire_projection", current_nw_base=250_000)
        assert "trajectory" not in body

    def test_fire_number_present(self, isolated_profile, monkeypatch):
        from backend.services import fire as fire_svc

        monkeypatch.setattr(
            fire_svc,
            "compute_fire",
            lambda **kw: {
                "fire_number": 1_500_000,
                "progress_pct": 33.3,
                "on_track": True,
            },
        )
        body, _ = call("get_fire_projection", current_nw_base=500_000)
        assert "fire_number" in body
        assert body["fire_number"] == 1_500_000

    def test_uses_profile_defaults(self, isolated_profile, monkeypatch):
        """With no overrides the tool reads expected_return / withdrawal_rate from the profile."""
        from backend.services import fire as fire_svc

        captured = {}

        def fake_compute(**kw):
            captured.update(kw)
            return {"fire_number": 1_000_000, "progress_pct": 0, "on_track": False}

        monkeypatch.setattr(fire_svc, "compute_fire", fake_compute)
        # Profile has defaults: fire_expected_return_pct=7, fire_withdrawal_rate_pct=4
        call("get_fire_projection", current_nw_base=100_000)
        assert captured.get("expected_return_pct") == 7.0
        assert captured.get("withdrawal_rate_pct") == 4.0

    def test_override_withdrawal_rate(self, isolated_profile, monkeypatch):
        """withdrawal_rate_pct_override is forwarded to compute_fire."""
        from backend.services import fire as fire_svc

        captured = {}

        def fake_compute(**kw):
            captured.update(kw)
            return {"fire_number": 1_000_000, "progress_pct": 0, "on_track": False}

        monkeypatch.setattr(fire_svc, "compute_fire", fake_compute)
        call("get_fire_projection", current_nw_base=100_000, withdrawal_rate_pct_override=3.0)
        assert captured.get("withdrawal_rate_pct") == 3.0

    def test_fire_life_expectancy_from_profile_is_forwarded(self, isolated_profile, monkeypatch):
        """MATH-29 regression: the profile's fire_life_expectancy must reach compute_fire,
        not be silently ignored (previously always used the module default of 90)."""
        from backend.services import fire as fire_svc

        captured = {}

        def fake_compute(**kw):
            captured.update(kw)
            return {"fire_number": 1_000_000, "progress_pct": 0, "on_track": False}

        monkeypatch.setattr(fire_svc, "compute_fire", fake_compute)
        call(
            "update_profile",
            changes={"retirement_target_age": 65, "fire_life_expectancy": 100.0},
        )
        call("get_fire_projection", current_nw_base=100_000)
        assert captured.get("life_expectancy") == 100.0

    def test_monthly_retirement_spending_override_of_zero_is_respected(
        self, isolated_profile, monkeypatch
    ):
        """An explicit zero retirement-spending what-if must not fall back to living expenses."""
        from backend.services import fire as fire_svc

        captured = {}

        def fake_compute(**kw):
            captured.update(kw)
            return {"fire_number": 1_000_000, "progress_pct": 0, "on_track": False}

        monkeypatch.setattr(fire_svc, "compute_fire", fake_compute)
        call("update_profile", changes={"monthly_expenses": 2000.0})
        call(
            "get_fire_projection",
            current_nw_base=100_000,
            monthly_retirement_spending_override=0,
        )
        assert captured.get("monthly_retirement_spending") == 0.0

    def test_withdrawal_rate_override_of_zero_is_rejected(self, isolated_profile):
        """MATH-32-adjacent: a zero (or negative) withdrawal rate divides by zero in
        compute_fire — refuse it at the tool boundary with a clear error instead of crashing
        or silently substituting the default."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            call(
                "get_fire_projection",
                current_nw_base=100_000,
                withdrawal_rate_pct_override=0,
            )

    def test_update_profile_refuses_a_stored_zero_withdrawal_rate(self, isolated_profile):
        """MATH-32: `update_profile` writes straight to `services.profile.write_profile`,
        bypassing PUT /api/profile's Pydantic validator — the choke itself must refuse a
        zero (or negative) `fire_withdrawal_rate_pct` so it can never land on disk and be
        silently coerced back to the default by every reader."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            call("update_profile", changes={"fire_withdrawal_rate_pct": 0.0})
        body, _ = call("get_profile")
        assert body["fire_withdrawal_rate_pct"] == 4.0  # untouched — the write never landed

    def test_update_profile_refuses_a_negative_withdrawal_rate(self, isolated_profile):
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            call("update_profile", changes={"fire_withdrawal_rate_pct": -4.0})


# ══════════════════════════════════════════════════════════════════════════════
# simulate_retirement
# ══════════════════════════════════════════════════════════════════════════════


class TestSimulateRetirement:
    """The tool is a thin, trimmed view of retirement_plan.answer — the numbers themselves are
    pinned in test_retirement_plan.py / test_plan_outlook_api.py."""

    @pytest.fixture(autouse=True)
    def _book(self, isolated_profile, monkeypatch):
        from backend.services import montecarlo as mc
        from backend.services import retirement_plan

        isolated_profile.set_raw(
            {
                "date_of_birth": "1982-03-10",
                "retirement_target_age": 62,
                "monthly_saving": 2_000,
                "fire_monthly_retirement_spending": 3_500,
            }
        )
        monkeypatch.setattr(retirement_plan, "_current_age", lambda dob: 44.0 if dob else None)
        monkeypatch.setattr(retirement_plan.ledger_service, "base_currency", lambda: "SGD")
        monkeypatch.setattr(
            retirement_plan.ledger_service, "net_worth", lambda base=None: {"net_worth": 480_000.0}
        )
        mc._cache.clear()

    def test_proposed_is_none(self):
        _, proposed = call("simulate_retirement")
        assert proposed is None

    def test_no_arguments_is_the_saved_plan_with_the_headline_figures(self):
        body, _ = call("simulate_retirement")
        assert body["matches_the_outlook"] is True
        assert body["changed_from_saved_plan"] == []
        assert body["plan"]["saving"] == 2_000 and body["plan"]["retireAge"] == 62
        for key in (
            "success_probability",
            "safe_to_retire_age",
            "extra_monthly_saving_for_90pct",
            "fire_number_reached_at_age",
            "pot_at_retirement_median",
            "p10_ending",
            "median_ending",
        ):
            assert key in body

    def test_years_to_go_are_given_not_left_for_the_model_to_subtract(self):
        """Lucius wrote "At 62 you'd be 44 years from today's date" doing age math in prose."""
        body, _ = call("simulate_retirement")
        assert body["current_age_years"] == 44
        assert body["years_to_retire_age"] == 18
        assert body["years_to_safe_to_retire_age"] == body["safe_to_retire_age"] - 44
        body, _ = call("simulate_retirement", retireAge=50)
        assert body["years_to_retire_age"] == 6

    def test_bands_are_a_sparse_sample_ending_on_the_last_year(self):
        body, _ = call("simulate_retirement")
        assert "bands" not in body
        sample = body["bands_sample"]
        assert 0 < len(sample) < 20
        assert sample[-1]["age"] == 44.0 + 46  # life expectancy 90

    def test_a_what_if_names_what_it_changed_and_the_saved_value(self):
        body, _ = call("simulate_retirement", retireAge=58)
        assert body["matches_the_outlook"] is False
        assert body["changed_from_saved_plan"] == [{"lever": "retireAge", "saved": 62, "used": 58}]
        assert body["success_probability"] < call("simulate_retirement")[0]["success_probability"]

    def test_guaranteed_income_such_as_cpf_life_is_accepted(self):
        base = call("simulate_retirement")[0]["success_probability"]
        body, _ = call(
            "simulate_retirement",
            streams=[{"label": "CPF LIFE", "amount": 1_800, "age": 65}],
        )
        assert body["success_probability"] > base
        assert body["changed_from_saved_plan"][0]["lever"] == "streams"

    def test_an_unknown_or_retired_input_is_refused_not_ignored(self):
        """A silently dropped lever would answer "matches the Outlook" to a what-if."""
        body, _ = call("simulate_retirement", starting_balance=1, monthly_withdrawal=99_999)
        assert body["ok"] is False
        assert "monthly_withdrawal" in body["error"] and "starting_balance" in body["error"]
        assert "monthlyRetirementSpending" in body["error"] and "startNW" in body["error"]
        body, _ = call(
            "simulate_retirement", streams=[{"label": "x", "amount": 1, "age": 65, "end_age": 70}]
        )
        assert body["ok"] is False and "end_age" in body["error"]

    def test_a_missing_birth_date_is_a_readable_error_not_a_crash(self, isolated_profile):
        isolated_profile.set_raw({"retirement_target_age": 62})
        body, _ = call("simulate_retirement")
        assert body["ok"] is False
        assert "date of birth" in body["error"]
