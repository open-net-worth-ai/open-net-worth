"""Profile schemas — financial profile (FIRE settings, income, prefs).

Mirrors ``data/profile.json`` keys and the inline ``Liability``/``ProfileBody`` in
``api/profile.py``. ``ProfileUpdate`` is the PATCH shape that replaces ``ProfileBody``
(and the duplicated ``Liability``/``_blank_to_none``) in wave 2.

Savings goals are NOT profile fields — they are the first-class ``Target`` primitive
(``models/target.py`` + ``db/targets_store.py``); the legacy free-text ``profile.goals[]``
was retired in P2-GO1 (folded into targets.json by ``scripts/migrate_goals.py``).

``BriefingPreferences`` replaces the loose ``Dict[str, object]`` typing in
``ProfileBody.briefing_preferences``; the deep-merge-on-read behavior stays in
``api/profile.py`` / the ``update_profile`` tool — the model only describes the shape.

The blank→None coercions are load-bearing today (partial form saves) and are
preserved here via the shared ``_blank_to_none`` from ``base.py``.

Scaffold only (RFC P1-A3). Monetary fields are denominated in ``base_currency``.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field, field_validator

from .base import ONBaseModel, _blank_to_none

# The daily briefing's standing instruction when the user has not written their own — what the
# briefing widget's prompt box shows and the server honours. An empty value falls back to this.
DEFAULT_BRIEFING_FOCUS = (
    "Keep it short. Lead with what changed or needs my attention, and sum up everything "
    "that's fine in one sentence."
)


class Liability(ONBaseModel):
    name: str = ""
    balance: Optional[float] = None
    rate_pct: Optional[float] = None
    monthly_payment: Optional[float] = None


class BriefingPreferences(ONBaseModel):
    """Per-section daily-briefing toggles + a free-text focus.

    Replaces the loose ``Dict[str, object]`` in ``ProfileBody`` (wave 2).
    """

    holdings_news: bool = True
    fx: bool = True
    spending: bool = True
    accounts: bool = True
    upcoming_actions: bool = True
    custom_focus: str = DEFAULT_BRIEFING_FOCUS


class Profile(ONBaseModel):
    """The stored profile shape (all of ``profile.json``)."""

    name: Optional[str] = None
    nationalities: List[str] = []
    countries_of_residence: List[str] = []
    tax_residencies: List[str] = []
    employment_types: List[str] = []
    marital_status: Optional[str] = None
    risk_tolerance: Optional[str] = "moderate"
    risk_concentration_guideline: Optional[float] = 20.0
    risk_stress_drop_pct: Optional[float] = 30.0
    preferred_benchmark: Optional[str] = "SPY"
    retirement_target_age: Optional[int] = None
    date_of_birth: Optional[str] = None
    monthly_net_income: Optional[float] = None
    monthly_expenses: Optional[float] = None
    monthly_saving: Optional[float] = Field(default=None, allow_inf_nan=False)
    cashflow_low_balance_threshold: Optional[float] = None
    liabilities: List[Liability] = []
    notes: str = ""
    fire_expected_return_pct: Optional[float] = 7.0
    fire_withdrawal_rate_pct: Optional[float] = 4.0
    fire_monthly_retirement_spending: Optional[float] = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    fire_volatility_pct: Optional[float] = None
    # per-investment-asset_class target weights, fractions 0–1 (keys not a fixed list).
    target_allocation: Dict[str, float] = {}
    # Single-name concentration guideline (% of book; a holding at or above this weight is flagged).
    risk_concentration_guideline: Optional[float] = 20.0
    # Hypothetical drop % for the stress-loss estimate on the largest position.
    risk_stress_drop_pct: Optional[float] = 30.0
    briefing_preferences: BriefingPreferences = BriefingPreferences()
    base_currency: str = "SGD"
    # Show/hide the chat panel's AI-generated suggestion chips (starters + follow-ups).
    # Default OFF (owner, 2026-08-10): the chips cost a model call per turn and most turns
    # do not want them. Opt in from Settings -> AI -> Chat.
    chat_suggestions_enabled: bool = False
    # Default ON: the per-turn cost chip is how spend stays visible while you work.
    chat_cost_visible: bool = True
    # Default OFF ("one turn only") — see ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md.
    chat_multi_turn_enabled: bool = False
    # Reasoning as ONE block above the turn, instead of a block per round in the position it
    # happened. Default OFF (split): a turn that thinks, calls a tool, then thinks again has
    # two thoughts belonging either side of that tool, and merging them puts the second one
    # above the call it followed. Purely presentational — the transcript records the rounds
    # separately either way, so this only chooses how they are drawn.
    chat_thinking_grouped: bool = False
    # Whether a device with no choice of its own sends the screen with each message. Default ON.
    screen_awareness_default: bool = True
    # Feature intros already shown (UI state, never model-facing).
    seen_intros: List[str] = []


class ProfileUpdate(ONBaseModel):
    """PATCH shape == today's ``api/profile.py ProfileBody`` (all-Optional + blank→None).

    Replaces ``ProfileBody`` in wave 2.
    """

    name: Optional[str] = None
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
    target_allocation: Optional[Dict[str, float]] = None
    risk_concentration_guideline: Optional[float] = None
    risk_stress_drop_pct: Optional[float] = None
    briefing_preferences: Optional[BriefingPreferences] = None
    chat_suggestions_enabled: Optional[bool] = None
    chat_cost_visible: Optional[bool] = None
    chat_multi_turn_enabled: Optional[bool] = None
    chat_thinking_grouped: Optional[bool] = None
    screen_awareness_default: Optional[bool] = None
    seen_intros: Optional[List[str]] = None

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
        "risk_concentration_guideline",
        "risk_stress_drop_pct",
        "marital_status",
        "risk_tolerance",
        mode="before",
    )
    @classmethod
    def _coerce_blank(cls, v):
        return _blank_to_none(v)

    @field_validator("risk_concentration_guideline", "risk_stress_drop_pct", mode="before")
    @classmethod
    def _clamp_risk_pct(cls, v):
        if v is None:
            return v
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("Value must be a number")
        return max(1.0, min(100.0, fv))
