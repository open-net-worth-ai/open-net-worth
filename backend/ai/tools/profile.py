"""Profile + FIRE-projection tools."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from backend.ai.memory_structure import routing_guide_for_tool
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.plan_scenarios import PlanEvent, PlanStream
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    bare_object_schema,
    schema_of,
)
from backend.services import profile as profile_service
from backend.services.countries import COUNTRY_CODES
from backend.services.currencies import CURRENCY_CODES


class GetProfileInput(ToolInput):
    """Schema-of-record for ``get_profile`` (see ``schema_from_model``)."""


@tool(
    "get_profile",
    description=(
        "Who the user is: name, age, household, residency and tax residencies, employment, "
        "base currency, monthly income and expenses, liabilities, risk tolerance, retirement "
        "age, FIRE assumptions, notes — plus `settings` (briefing sections, import review "
        "default, low-balance alert). Read it before personalised advice. Elsewhere: each "
        "envelope's Zero-Based target, 50/30/20 cap and note (list_envelopes), investment "
        "target mix (get_rebalance_plan), savings goals (get_goals)."
    ),
    input_schema=schema_of(GetProfileInput, tool_name="get_profile"),
)
async def get_profile(input_data: dict):
    import backend.ai.tools as _root

    return (
        json.dumps(profile_service.profile_for_model(_root.read_profile()), ensure_ascii=False),
        None,
    )


class GetFireProjectionInput(ToolInput):
    """Schema-of-record for ``get_fire_projection`` (see ``schema_from_model``)."""

    current_nw_base: float = Field(
        description=(
            "Current total net worth in the user's base currency. Required — get this "
            "from the portfolio or ask the user."
        )
    )
    withdrawal_rate_pct_override: Optional[float] = Field(
        default=None, description="Override safe withdrawal rate %. Default from profile (4%)."
    )
    monthly_retirement_spending_override: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Override monthly retirement spending in the user's base currency. "
            "Default from fire_monthly_retirement_spending, then monthly_expenses."
        ),
    )


_OUTLOOK_OWNED_KEYS = frozenset(
    {
        "trajectory",
        "on_track",
        "projected_fire_age",
        "years_to_fire",
        "monthly_savings_needed",
        "monthly_savings",
        "expected_return_pct",
    }
)


@tool(
    "get_fire_projection",
    description=(
        "The FIRE number only: target net worth (spending / withdrawal rate) and current "
        "progress toward it. Pass monthly_retirement_spending_override or "
        "withdrawal_rate_pct_override for a what-if on the number. NOT for 'am I on track', "
        "'when can I retire' or odds of lasting: that is simulate_retirement, the one "
        "retirement answer (Plan > Outlook). Never quote a second verdict beside it."
    ),
    input_schema=schema_of(GetFireProjectionInput, tool_name="get_fire_projection"),
)
async def get_fire_projection(input_data: dict):
    from backend.services.fire import _load_profile as _load_fire_profile
    from backend.services.fire import compute_fire, derive_fire_inputs

    profile = _load_fire_profile()
    current_nw = float(input_data["current_nw_base"])
    # MATH-31: derivation (savings/return/withdrawal-rate/expenses/life-expectancy from the
    # profile, with these what-if overrides layered on) lives in ONE place, services.fire —
    # this used to be duplicated here and in api/fire.py. MATH-30's `is not None` override
    # semantics and MATH-32's divide-by-zero guard (raised inside compute_fire) both come
    # along for free.
    overrides = {
        "withdrawal_rate_pct": input_data.get("withdrawal_rate_pct_override"),
        "monthly_retirement_spending": input_data.get("monthly_retirement_spending_override"),
    }
    inputs = derive_fire_inputs(profile, overrides=overrides)
    result = compute_fire(current_nw=current_nw, **inputs)
    # Timing and verdict fields belong to simulate_retirement, the one retirement engine — a
    # second "on track at 59" beside its "68" is the contradiction this strips at the source.
    result_for_ai = {k: v for k, v in result.items() if k not in _OUTLOOK_OWNED_KEYS}
    return json.dumps(result_for_ai, ensure_ascii=False), None


class SimulateRetirementInput(ToolInput):
    """The Outlook's own levers (the vocabulary of ``set_plan_baseline`` / ``create_plan_scenario``).
    Every one is optional: omitted = the SAVED plan's value."""

    saving: Optional[float] = Field(
        default=None,
        description="Monthly saving until retirement, base currency. Default: the saved plan's.",
    )
    ret: Optional[float] = Field(
        default=None,
        description=(
            "Expected annual return %, NOMINAL (before inflation; the engine takes off its "
            "inflation assumption). E.g. 7. Default: the saved plan's."
        ),
    )
    vol: Optional[float] = Field(
        default=None,
        description=(
            "Annual return volatility % (stdev). Raise to stress-test. Default: the saved plan's."
        ),
    )
    monthlyRetirementSpending: Optional[float] = Field(  # noqa: N815 — wire contract
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Monthly spending once retired, base currency. Default: the saved plan's.",
    )
    lifeExp: Optional[int] = Field(  # noqa: N815 — wire contract
        default=None, description="Age the money must last to. Default: the saved plan's."
    )
    retireAge: Optional[int] = Field(  # noqa: N815 — wire contract
        default=None,
        description="Age they stop saving and start drawing. Default: the saved plan's.",
    )
    swr: Optional[float] = Field(
        default=None,
        description=(
            "Safe withdrawal rate %. Only sets the FIRE number and the age the typical path "
            "reaches it; it does not change the odds. Default: the saved plan's."
        ),
    )
    streams: Optional[List[PlanStream]] = Field(
        default=None,
        description=(
            "Guaranteed income once retired (pension, rent, annuity; CPF LIFE = "
            "{label:'CPF LIFE', amount:<payout>, age:65}). REPLACES the saved list, so pass the "
            "FULL list you want. Default: the saved plan's streams."
        ),
    )
    events: Optional[List[PlanEvent]] = Field(
        default=None,
        description=(
            "One-off life events (negative = spend, positive = windfall). REPLACES the saved "
            "list, so pass the FULL list you want. Default: the saved plan's events."
        ),
    )
    crash: Optional[bool] = Field(
        default=None, description="A 30% market crash the year they retire (sequence-of-returns)."
    )
    weakSeq: Optional[bool] = Field(  # noqa: N815 — wire contract
        default=None, description="Weak returns for the first ten years of retirement."
    )
    lostDec: Optional[bool] = Field(  # noqa: N815 — wire contract
        default=None, description="A lost decade: weak returns for the next ten years."
    )
    highInfl: Optional[bool] = Field(  # noqa: N815 — wire contract
        default=None, description="Sustained high inflation eating into real returns."
    )
    startNW: Optional[float] = Field(  # noqa: N815 — wire contract
        default=None,
        description=(
            "Starting pot override. Default: net worth today (retirement accounts included). "
            "For a purchase now, pass net worth minus its price."
        ),
    )


_PLAN_ECHO = (
    "saving",
    "ret",
    "vol",
    "monthlyRetirementSpending",
    "lifeExp",
    "retireAge",
    "swr",
    "streams",
    "events",
    "crash",
    "weakSeq",
    "lostDec",
    "highInfl",
    "startNW",
    "currentAge",
    "inflation",
    "baseCurrency",
    "fireNumber",
)


@tool(
    "simulate_retirement",
    description=(
        "Run the retirement simulation: the SAME engine and random paths as Plan > Outlook, so "
        "the user's plan has ONE answer. With no arguments it returns exactly the Outlook's "
        "numbers for their saved plan: the chance their money lasts to life expectancy, the "
        "safe-to-retire age, the extra monthly saving that lifts the odds to 90%, and the age "
        "they reach their FIRE number. Every input defaults to the saved plan (profile FIRE "
        "settings + their saved Outlook baseline, including guaranteed income such as CPF LIFE "
        "and life events); pass ONLY the levers a what-if changes. changed_from_saved_plan "
        "lists each lever you moved with its saved value: name them whenever you quote the "
        "result, and never present a what-if as the plan's answer. When the Outlook figures "
        "are in the screen context and nothing is being changed, quote those. Read-only. "
        "Summarize the odds, the p10 downside and the median ending; don't list band points. "
        "For 'how long until', quote current_age_years and years_to_*; never subtract ages."
    ),
    input_schema=schema_of(SimulateRetirementInput, tool_name="simulate_retirement"),
)
async def simulate_retirement(input_data: dict):
    from pydantic import ValidationError as ModelValidationError

    from backend.core.errors import OpenNetWorthError
    from backend.models.retirement import OutlookLevers
    from backend.services import retirement_plan

    try:
        # Validated against the closed schema first: an unknown or retired name must be refused,
        # never ignored — a silently dropped lever would answer "matches the Outlook" to a what-if.
        checked = SimulateRetirementInput(**input_data)
        answer = retirement_plan.answer(OutlookLevers(**checked.model_dump(exclude_none=True)))
    except ModelValidationError as exc:
        unknown = sorted(
            {str(e["loc"][-1]) for e in exc.errors() if e["type"] == "extra_forbidden"}
        )
        error = (
            f"Unknown input(s) {unknown}. The levers are {sorted(SimulateRetirementInput.model_fields)}; "
            "starting_balance, monthly_contribution, monthly_withdrawal, retire_after_years, "
            "annual_return_pct, income_streams and one_off_events were retired — use startNW, "
            "saving, monthlyRetirementSpending, retireAge, ret, streams and events."
            if unknown
            else f"Invalid retirement inputs: {exc}"
        )
        return json.dumps({"ok": False, "error": error}), None
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    result, insights, inputs = answer["result"], answer["insights"], answer["inputs"]
    bands = result["bands"]
    sample = bands[:: max(1, len(bands) // 6)]
    if bands[-1] not in sample:
        sample.append(bands[-1])
    earliest, number_age = insights["earliest"], insights["number_age"]
    current_age = inputs["currentAge"]

    def years_from_now(age: float | None) -> int | None:
        return None if age is None else max(0, int(age + 0.5) - int(current_age))

    summary = {
        "matches_the_outlook": not answer["changed"],
        "changed_from_saved_plan": [
            {"lever": lever, "saved": answer["saved_plan_values"].get(lever), "used": inputs[lever]}
            for lever in answer["changed"]
        ],
        "plan": {k: inputs[k] for k in _PLAN_ECHO},
        "current_age_years": int(current_age),
        "years_to_retire_age": years_from_now(inputs["retireAge"]),
        "years_to_safe_to_retire_age": years_from_now(earliest["age"] if earliest else None),
        "years_to_fire_number": years_from_now(number_age),
        "success_probability": result["success_probability"],
        "depletion_probability": result["depletion_probability"],
        "safe_to_retire_age": int(earliest["age"] + 0.5) if earliest else None,
        "extra_monthly_saving_for_90pct": insights["extra_save"],
        "extra_monthly_saving_for_80pct": insights["gap_save"],
        "fire_number_reached_at_age": int(number_age + 0.5) if number_age is not None else None,
        "pot_at_retirement_median": result["nest_egg_at_retire"],
        "median_ending": result["median_ending"],
        "p10_ending": result["p10_ending"],
        "p90_ending": result["p90_ending"],
        "n_paths": result["n_paths"],
        "bands_sample": sample,
    }
    return json.dumps(summary, ensure_ascii=False), None


class LiabilityChange(NestedInput):
    name: str = Field(default="", description="What this liability is")
    balance: Optional[float] = Field(default=None, description="Current outstanding balance")
    rate_pct: Optional[float] = Field(default=None, description="Annual interest rate %")
    monthly_payment: Optional[float] = Field(default=None, description="Required monthly payment")


class UpdateProfileChanges(NestedInput):
    name: Optional[str] = Field(default=None, description="The user's real name")
    nickname: Optional[str] = Field(
        default=None, description="What Lucius should call them — a short form of name"
    )
    # Literal[COUNTRY_CODES] / Literal[CURRENCY_CODES] below: a tuple subscript unpacks
    # identically to writing each element as its own Literal arg, so this is a real enum
    # constraint at runtime (and in the derived tool schema) — mypy just can't verify a
    # Literal built from a module-level tuple rather than inline constants.
    nationalities: Optional[List[Literal[COUNTRY_CODES]]] = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "ISO-3166 alpha-2 country codes, e.g. [VN, US] for dual citizenship. Pass the "
            "FULL updated list, not a delta."
        ),
    )
    countries_of_residence: Optional[List[Literal[COUNTRY_CODES]]] = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "ISO-3166 alpha-2 country codes — everywhere they currently live, not just one. "
            "Pass the FULL updated list, not a delta."
        ),
    )
    tax_residencies: Optional[List[Literal[COUNTRY_CODES]]] = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "ISO-3166 alpha-2 country codes for every jurisdiction they owe tax in — NOT "
            "always the same as countries_of_residence (e.g. a US citizen owes US tax "
            "regardless of where they live, under citizenship-based taxation). Pass the FULL "
            "updated list, not a delta."
        ),
    )
    base_currency: Optional[Literal[CURRENCY_CODES]] = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "ISO 4217 code, e.g. USD, SGD, EUR — the user's primary display currency; set "
            "this first during onboarding"
        ),
    )
    employment_types: Optional[
        List[Literal["salaried", "self_employed", "business_owner", "retired", "student"]]
    ] = Field(
        default=None,
        description=(
            "Every concurrent employment situation, e.g. [salaried, self_employed] for a "
            "salaried job plus a side business. Pass the FULL updated list, not a delta."
        ),
    )
    marital_status: Optional[Literal["single", "married", "divorced", "widowed"]] = Field(
        default=None
    )
    risk_tolerance: Optional[Literal["conservative", "moderate", "aggressive"]] = Field(
        default=None
    )
    retirement_target_age: Optional[int] = Field(default=None)
    date_of_birth: Optional[date] = Field(
        default=None,
        description=("ISO date the user GAVE, e.g. 1990-05-14; month+year only: the 1st."),
    )
    monthly_net_income: Optional[float] = Field(default=None)
    monthly_expenses: Optional[float] = Field(default=None)
    monthly_saving: Optional[float] = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "Monthly saving before retiring (negative = overspending). When the user states "
            "what they save, set THIS, never monthly_net_income. Unset = income − expenses."
        ),
    )
    liabilities: Optional[List[LiabilityChange]] = Field(
        default=None, description="The FULL updated list, not a delta"
    )
    notes: Optional[str] = Field(
        default=None,
        description=(
            "The user's Instructions for Lucius — standing rules for how you should behave. "
            "Change them when asked, or when the user states a rule they'd expect to find here; "
            "edit or add a line, never rewrite their text wholesale. " + routing_guide_for_tool()
        ),
    )
    fire_expected_return_pct: Optional[float] = Field(default=None)
    fire_withdrawal_rate_pct: Optional[float] = Field(default=None)
    fire_monthly_retirement_spending: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Expected monthly retirement spending in the user's base currency.",
    )
    fire_volatility_pct: Optional[float] = Field(
        default=None,
        description="Annual portfolio volatility % used in Monte Carlo, e.g. 15.0 for 15%",
    )
    fire_life_expectancy: Optional[float] = Field(
        default=None, description="Planning horizon end age in years, e.g. 90.0"
    )
    target_allocation: Optional[Dict[str, float]] = Field(
        default=None,
        json_schema_extra=bare_object_schema,
        description=(
            "Maps investment asset_class → target weight, "
            'e.g. {"equity": 0.6, "crypto": 0.2, "commodity": 0.2}; weights are over the '
            "INVESTMENT book only — cash/property excluded — and should sum to ~1. You may "
            "pass fractions (0.6) or percentages (60); pass the full updated map. Rebalancing "
            "advice is ADVISORY ONLY — never auto-trade; suggest buys/sells in words."
        ),
    )
    cashflow_low_balance_threshold: Optional[float] = Field(
        default=None,
        description="Flag projected months where the cash baseline dips below this, in base currency",
    )
    import_review_default: Optional[Literal["merge", "skip"]] = Field(
        default=None,
        description=(
            "The statement-import review band's pre-selected action; 'merge' (default) "
            "pre-selects linking a probable match into the existing transaction, 'skip' opts "
            "out to manual per-row decisions"
        ),
    )
    briefing_preferences: Optional[Dict[str, Any]] = Field(
        default=None,
        json_schema_extra=bare_object_schema,
        description=(
            "Controls which AI-briefing sections show: boolean flags holdings_news, fx, "
            "spending, accounts, upcoming_actions, plus a free-text custom_focus string; pass "
            "only the keys to change — they are deep-merged so other flags are preserved"
        ),
    )


class UpdateProfileInput(ToolInput):
    changes: UpdateProfileChanges = Field(description="Fields to update — pass only what changed")
    reason: Optional[str] = Field(default=None, description="Why this change is being made")


@tool(
    "update_profile",
    description=(
        "Update the user's financial profile. "
        "Savings goals are NOT profile fields (create_goal / update_goal). "
        "IMPORTANT — changing base_currency does NOT convert manually-entered amounts "
        "(monthly income/expenses/saving, retirement spending, liability amounts, budget "
        "targets and caps): they are read in the new currency as-is. Account balances and "
        "transactions DO convert. So confirm with the user and warn them first, then remind "
        "them which amounts to re-enter."
    ),
    input_schema=schema_of(UpdateProfileInput, tool_name="update_profile"),
    mutates=("profile",),
)
async def update_profile(input_data: dict):
    import backend.ai.tools as _root

    profile = _root.read_profile()
    changes = input_data.get("changes", {})
    # An id-only/empty-changes call cannot alter anything — short-circuit BEFORE any
    # coercion/merge/write so it never touches write_profile (no store write, no checkpoint).
    # Mirrors the update_transactions/_no_op_update_result fix (2026-07-21): the pre-fix
    # behavior wrote the profile back unchanged and returned plain ok:true — indistinguishable
    # from a real edit.
    if not changes:
        return json.dumps(no_op_write_result({"profile": profile}), ensure_ascii=False), None
    # Coerce numeric fields so AI can't accidentally write strings
    _num_fields = {
        "retirement_target_age",
        "monthly_net_income",
        "monthly_expenses",
        "monthly_saving",
        "fire_expected_return_pct",
        "fire_withdrawal_rate_pct",
        "fire_monthly_retirement_spending",
        "fire_volatility_pct",
        "fire_life_expectancy",
    }
    for k in _num_fields:
        if k in changes and changes[k] is not None:
            try:
                changes[k] = (
                    float(changes[k]) if k != "retirement_target_age" else int(float(changes[k]))
                )
            except (TypeError, ValueError):
                pass
    if (
        "cashflow_low_balance_threshold" in changes
        and changes["cashflow_low_balance_threshold"] is not None
    ):
        try:
            changes["cashflow_low_balance_threshold"] = float(
                changes["cashflow_low_balance_threshold"]
            )
        except (TypeError, ValueError):
            pass
    # Normalize target allocation (accept fractions or percentages) before persisting.
    if isinstance(changes.get("target_allocation"), dict):
        from backend.services.profile import normalize_target_allocation

        changes["target_allocation"] = normalize_target_allocation(changes["target_allocation"])
    # Deep-merge briefing_preferences so a partial block keeps the other section flags on disk
    # (mirrors the REST PUT /api/profile behaviour — a partial save must not clobber siblings).
    if isinstance(changes.get("briefing_preferences"), dict):
        changes["briefing_preferences"] = {
            **(profile.get("briefing_preferences") or {}),
            **changes["briefing_preferences"],
        }
    old_base = profile.get("base_currency")
    profile.update(changes)
    # Same side effects as the REST path: clear the stale FX cache + flag the manual
    # base-denominated amounts that don't auto-convert (so the user is reminded to revisit).
    from backend.services.profile import handle_base_currency_change

    handle_base_currency_change(profile, old_base, changes.get("base_currency"))
    _root.write_profile(profile)
    return json.dumps({"ok": True, "profile": profile}, ensure_ascii=False), None


class DismissBaseCurrencyReviewInput(ToolInput):
    """Schema-of-record for ``dismiss_base_currency_review`` (see ``schema_from_model``)."""


@tool(
    "dismiss_base_currency_review",
    description=(
        "Clear the base-currency review flag — the reminder banner that lists the manual "
        "base-denominated amounts (income, expenses, FIRE/goal/liability figures, budgets) that "
        "did NOT auto-convert after a base-currency change. Call this once the user confirms they "
        "have reviewed / re-entered those amounts (or that they're fine as-is)."
    ),
    input_schema=schema_of(
        DismissBaseCurrencyReviewInput, tool_name="dismiss_base_currency_review"
    ),
    mutates=("profile",),
)
async def dismiss_base_currency_review(input_data: dict):
    import backend.ai.tools as _root

    profile = _root.read_profile()
    existed = profile.pop("base_currency_review", None) is not None
    _root.write_profile(profile)
    return json.dumps({"ok": True, "cleared": existed}, ensure_ascii=False), None
