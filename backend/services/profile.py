"""profile_service — read/write the financial profile and related mutations.

Extracted from api/profile.py so ai/tools and other callers can import these
without coupling to the HTTP layer (api → services → db → core).
"""

from __future__ import annotations

import difflib
import math
import time
from datetime import date, datetime
from typing import Optional

from backend.core import user_time
from backend.db.profile_store import ProfileStore
from backend.models.profile import DEFAULT_BRIEFING_FOCUS

_store = ProfileStore()

DEFAULT_PROFILE: dict = {
    "name": None,
    "nationalities": [],
    "countries_of_residence": [],
    "tax_residencies": [],
    "employment_types": [],
    "marital_status": None,
    "risk_tolerance": "moderate",
    "risk_concentration_guideline": 20.0,
    "risk_stress_drop_pct": 30.0,
    # The benchmark every "vs the market" figure compares against — Performance, the Intel
    # Holdings brief and the watch pulse. A `risk_metrics.BENCHMARK_ROSTER` key.
    "preferred_benchmark": "SPY",
    "retirement_target_age": None,
    "date_of_birth": None,
    "monthly_net_income": None,
    "monthly_expenses": None,
    # What they put aside each month — its OWN fact, never derived on write (None = not stated,
    # readers fall back to income − expenses; see services.fire.monthly_saving).
    "monthly_saving": None,
    "cashflow_low_balance_threshold": None,
    "liabilities": [],
    "notes": "",
    "fire_expected_return_pct": 7.0,
    "fire_withdrawal_rate_pct": 4.0,
    "fire_monthly_retirement_spending": None,
    "target_allocation": {},
    # (The YNAB-engine credit-card `credit_card_mode` flag was removed 2026-07-10 — payment
    # envelopes are now the only engine; a card leaves cash and its debt earmarks in a per-card
    # payment envelope. See ADR 2026-07-09.)
    # Dedup / match engine (RFC transaction-dedup-engine): how the combined verdict maps to a
    # final action. review_all = never auto-merge; auto_transfers (default) = auto-merge only
    # agreed transfer pairs; auto_high = auto-merge agreed near-dups too. Disagreement /
    # single-signal ALWAYS routes to review regardless. Auto-merge requires deterministic+LLM
    # agreement (Phase 2) — until then every match is surfaced for review.
    "dedup_policy": "auto_transfers",
    # Statement-import review band (three-state review): the pre-selected action for a
    # review-band match. "skip" (default — a possible-but-unconfirmed match must never merge
    # silently for a user who bulk-accepts) or "merge" (the owner's opt-in, the 2026-07-16
    # durable-resolution default). Strong stays merge, weak stays new — only the review band
    # reads this.
    "import_review_default": "merge",
    # Master switch for the chat panel's AI-generated suggestion chips (both the
    # tailored opening questions on an empty chat and the post-turn follow-ups).
    # When False the chat panel never calls /api/chat/suggestions, so no Haiku
    # cost is incurred and the empty hero degrades to its static starter hints.
    "chat_suggestions_enabled": True,
    # Feature intros already shown, e.g. "assets" (UI state, never model-facing).
    "seen_intros": [],
    # Default OFF ("one turn only") — see ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md.
    "chat_multi_turn_enabled": False,
    "briefing_preferences": {
        "holdings_news": True,
        "fx": True,
        "spending": True,
        "accounts": True,
        "upcoming_actions": True,
        "custom_focus": DEFAULT_BRIEFING_FOCUS,
    },
    # The owner's IANA time zone (``user_time``) and how it got there: ``auto`` = captured from the
    # browser while unset, ``user`` = picked in Settings. Unset = the server's own local zone.
    "timezone": None,
    "timezone_source": None,
}


def normalize_target_allocation(raw: dict) -> dict:
    """Coerce a target-allocation map to fractions (0–1).

    Accepts either fractions (0.6) or percentages (60) — if any value exceeds 1 we treat
    the whole map as percentages and divide by 100. Drops non-numeric / non-positive entries.
    Does NOT force the sum to 1 — under/over-allocation is surfaced to the user."""
    cleaned: dict[str, float] = {}
    for k, v in (raw or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            cleaned[str(k)] = f
    if cleaned and max(cleaned.values()) > 1:
        cleaned = {k: v / 100 for k, v in cleaned.items()}
    return cleaned


DEDUP_POLICIES = ("review_all", "auto_transfers", "auto_high")


def normalize_dedup_policy(raw) -> str:
    """Coerce a dedup_policy value to a known policy; unknown/blank → the default."""
    return raw if raw in DEDUP_POLICIES else "auto_transfers"


IMPORT_REVIEW_DEFAULTS = ("skip", "merge")


def normalize_import_review_default(raw) -> str:
    """Coerce an import_review_default value to a known action; unknown/blank → "merge"
    (owner decision 2026-07-17: the review band's factory default is merge — a probable
    match pre-selects linking; skip remains the explicit opt-out)."""
    return raw if raw in IMPORT_REVIEW_DEFAULTS else "merge"


# Category state that CategoryStore has owned since the opaque-ids migration. The migration
# COPIED these into the store and never deleted the originals, so profile.json kept serving a
# second, CONTRADICTORY answer: still keyed by RETIRED SLUGS ("startup", "family",
# "sister_support") while every live category id is an opaque cat_<12hex>. `get_profile` is a
# CORE tool, so every session paid for them — 1,696 of 3,599 response bytes (47%) — and the
# model received two shapes for one concept, both presented as current. Each was verified to
# have ZERO readers before landing here (the frontend's only mentions are stale COMMENTS;
# `categories.hidden_categories()` reads the store). `category_notes` is deliberately NOT in
# this list — it is LIVE (services.planning.read_category_notes / write_category_note, the
# per-envelope note behind GET/PUT /api/planning/notes), and its stale slug-keyed VALUE is a
# data problem, not a dead field. Stored copies are removed by
# a one-shot migration since removed.
_DEAD_CATEGORY_FIELDS = (
    "custom_categories",
    "deleted_builtins",
    "hidden_categories",
    "category_color_overrides",
)


def read_profile_raw() -> dict:
    """The stored profile dict VERBATIM (no DEFAULT merge / normalization) — for callers that only
    need a raw field (budgets, fire, targets, rebalance, cashflow, portability). ``{}`` when unset."""
    return _store.get_raw()


def read_profile() -> dict:
    data = read_profile_raw()
    merged = {**DEFAULT_PROFILE, **data}
    merged.pop("goals", None)
    for _dead in _DEAD_CATEGORY_FIELDS:
        merged.pop(_dead, None)
    merged["dedup_policy"] = normalize_dedup_policy(merged.get("dedup_policy"))
    merged["import_review_default"] = normalize_import_review_default(
        merged.get("import_review_default")
    )
    merged["briefing_preferences"] = {
        **DEFAULT_PROFILE["briefing_preferences"],
        **(data.get("briefing_preferences") or {}),
    }
    # A book that never wrote its own instruction (stored "" by the old default) gets the
    # sensible one, not an unguided briefing.
    focus = str(merged["briefing_preferences"].get("custom_focus") or "").strip()
    merged["briefing_preferences"]["custom_focus"] = focus or DEFAULT_BRIEFING_FOCUS
    return merged


# ── What the model reads (get_profile) ─────────────────────────────────────────────────────
# The person and their financial situation — an ALLOWLIST, so a key added to the stored profile
# row later never reaches the model raw. The row also carries other domains' state (Zero-Based
# targets and notes keyed by envelope id, budget config, the investment target mix, the Risk
# card's limits, chat/import UI toggles); each of those is read through its own tool, where ids
# arrive with their labels (ADR 2026-07-20). Shipped raw, the envelope-keyed targets came with no
# names and the model paired amounts with the wrong envelopes (owner, 2026-09-26).
_MODEL_FIELDS = (
    "name",
    "nickname",
    "date_of_birth",
    "marital_status",
    "nationalities",
    "countries_of_residence",
    "tax_residencies",
    "employment_types",
    "base_currency",
    "monthly_net_income",
    "monthly_expenses",
    "monthly_saving",
    "liabilities",
    "risk_tolerance",
    "retirement_target_age",
    "fire_expected_return_pct",
    "fire_withdrawal_rate_pct",
    "fire_monthly_retirement_spending",
    "fire_volatility_pct",
    "fire_life_expectancy",
    "notes",
)
# Preferences update_profile can set that aren't about the person — grouped, never mixed in.
_MODEL_SETTINGS = (
    "briefing_preferences",
    "import_review_default",
    "cashflow_low_balance_threshold",
)


def _age(date_of_birth: object, today: date) -> Optional[int]:
    try:
        born = date.fromisoformat(str(date_of_birth)[:10])
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def profile_for_model(profile: dict, today: Optional[date] = None) -> dict:
    """``get_profile``'s payload: ``profile`` (a ``read_profile()`` result) narrowed to the person,
    with their ``age`` beside ``date_of_birth`` (so no model does date arithmetic) and the
    preferences ``update_profile`` can set under ``settings``."""
    out: dict = {}
    for key in _MODEL_FIELDS:
        if key in profile:
            out[key] = profile[key]
        if key == "date_of_birth" and profile.get("date_of_birth"):
            age = _age(profile["date_of_birth"], today or user_time.user_today())
            if age is not None:
                out["age"] = age
    out["settings"] = {k: profile[k] for k in _MODEL_SETTINGS if k in profile}
    # The ONE benchmark every "vs the market" figure uses (set from the Performance picker, not
    # update_profile) — resolved, so a never-set profile still says which index that is.
    from backend.services.risk_metrics import preferred_benchmark

    out["preferred_benchmark"] = preferred_benchmark(profile)
    return out


# ── The owner's time zone (core.user_time reads it through the provider registered below) ──
_ZONE_TTL_SECONDS = 5.0
_zone_cache: tuple[object, float, Optional[str]] | None = None


def _stored_zone() -> Optional[str]:
    """The stored zone name, memoised briefly per engine — ``user_today()`` sits in loops, and
    the zone changes about never. Dropped on every zone write here."""
    global _zone_cache
    from backend.db.engine import get_engine

    engine = get_engine()
    now = time.monotonic()
    if _zone_cache and _zone_cache[0] is engine and now - _zone_cache[1] < _ZONE_TTL_SECONDS:
        return _zone_cache[2]
    name = _store.get_raw().get("timezone")
    _zone_cache = (engine, now, name if isinstance(name, str) else None)
    return _zone_cache[2]


user_time.register_zone_provider(_stored_zone)


def timezone_state() -> dict:
    data = read_profile_raw()
    return {"timezone": data.get("timezone"), "timezone_source": data.get("timezone_source")}


def set_timezone(name: Optional[str], source: str) -> dict:
    """Store ``name`` (None clears it back to the server's zone) — the ONE write both the Settings
    select and the browser auto-capture funnel through."""
    from backend.core.errors import ValidationError

    if name is not None and not user_time.is_valid_zone(name):
        raise ValidationError(f"'{name}' is not a recognized IANA time zone")
    data = read_profile_raw()
    data["timezone"] = name
    data["timezone_source"] = source if name else None
    write_profile(data)
    return timezone_state()


def capture_browser_timezone(name: str) -> dict:
    """Adopt the browser's zone ONLY while none is stored — a zone the user picked (or an earlier
    capture) is never overwritten by whatever the browser reports later. ``applied`` says which."""
    state = timezone_state()
    if state["timezone"]:
        return {**state, "applied": False}
    return {**set_timezone(name, "auto"), "applied": True}


# The profile settings the Outlook baseline keeps its own copy of (profile field → baseline lever).
_BASELINE_MIRRORS = {
    "monthly_saving": "saving",
    "fire_expected_return_pct": "ret",
    "fire_volatility_pct": "vol",
    "fire_monthly_retirement_spending": "monthlyRetirementSpending",
    "fire_withdrawal_rate_pct": "swr",
    "fire_life_expectancy": "lifeExp",
    "retirement_target_age": "retireAge",
}


def _release_stale_baseline(before: dict, after: dict) -> None:
    """The saved plan is the profile overlaid with the Outlook baseline, so a baseline lever left
    from an earlier "Update my FIRE plan" would hide a figure the user has since stated (to Lucius,
    in Settings). Hand the edited settings back to the profile."""
    edited = {
        lever: after.get(field)
        for field, lever in _BASELINE_MIRRORS.items()
        if after.get(field) != before.get(field)
    }
    if edited:
        from backend.services import plan_state

        plan_state.release_stale_baseline_levers(edited)


def write_profile(profile: dict) -> None:
    """Persist via the single authoritative write path in services/budgets.

    The ONE choke both `PUT /api/profile` and the `update_profile` AI tool funnel through
    (the REST route's Pydantic model validates first, but the AI tool writes straight
    here) — so a stored-value invariant belongs here, not in the Pydantic model alone
    (MATH-32: an explicit `fire_withdrawal_rate_pct` of 0 makes `compute_fire`'s FIRE
    number undefined; refuse it at the write choke instead of letting it land and be
    silently coerced back to the default by every reader)."""
    from backend.core.errors import ValidationError

    fire_wr = profile.get("fire_withdrawal_rate_pct")
    if fire_wr is not None:
        try:
            fire_wr = float(fire_wr)
        except (TypeError, ValueError) as exc:
            raise ValidationError("fire_withdrawal_rate_pct must be a finite number") from exc
        if not math.isfinite(fire_wr) or fire_wr <= 0:
            raise ValidationError("fire_withdrawal_rate_pct must be a finite number greater than 0")

    spending = profile.get("fire_monthly_retirement_spending")
    if spending is not None:
        try:
            spending = float(spending)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "fire_monthly_retirement_spending must be a finite non-negative number"
            ) from exc
        if not math.isfinite(spending) or spending < 0:
            raise ValidationError(
                "fire_monthly_retirement_spending must be a finite non-negative number"
            )
        from backend.services.fire import DEFAULT_WITHDRAWAL_PCT, fire_number

        fire_number(spending, fire_wr if fire_wr is not None else DEFAULT_WITHDRAWAL_PCT)

    saving = profile.get("monthly_saving")
    if saving is not None:
        try:
            saving = float(saving)
        except (TypeError, ValueError) as exc:
            raise ValidationError("monthly_saving must be a finite number") from exc
        if not math.isfinite(saving):
            raise ValidationError("monthly_saving must be a finite number")

    zone = profile.get("timezone")
    if zone is not None and not user_time.is_valid_zone(zone):
        raise ValidationError(f"'{zone}' is not a recognized IANA time zone")

    from backend.services.budgets import write_profile_raw

    # Defaults merged, as read_profile() hands them to a caller: a first write that merely persists
    # a default (return 7%) is not an edit.
    before = {**DEFAULT_PROFILE, **read_profile_raw()}
    write_profile_raw(profile)
    _release_stale_baseline(before, profile)
    global _zone_cache
    _zone_cache = None


def currency_sensitive_fields(profile: dict) -> list[dict]:
    """Manual base-denominated amounts that do NOT auto-convert on base_currency change."""
    out: list[dict] = []

    def add(label: str, val) -> None:
        try:
            v = float(val or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v:
            out.append({"label": label, "value": v})

    add("Monthly net income", profile.get("monthly_net_income"))
    add("Monthly expenses", profile.get("monthly_expenses"))
    add("Monthly saving", profile.get("monthly_saving"))
    add(
        "Monthly retirement spending",
        profile.get("fire_monthly_retirement_spending"),
    )
    add("Low-balance threshold", profile.get("cashflow_low_balance_threshold"))
    for lb in profile.get("liabilities") or []:
        name = lb.get("name") or "unnamed"
        add(f'Liability "{name}" — balance', lb.get("balance"))
        add(f'Liability "{name}" — monthly payment', lb.get("monthly_payment"))
    from backend.services.budgets import normalize_budgets

    budgets = normalize_budgets(profile.get("monthly_budgets"))
    n_budgets = len(budgets)
    if n_budgets:
        add(
            f"Category budgets ({n_budgets})",
            sum(float(cb.get("limit") or 0) for cb in budgets.values()),
        )
    return out


def text_diff(before: object, after: object) -> dict:
    """Line diff of a free-text field: ``{"added": [...], "removed": [...]}``, both empty when
    the text is unchanged. Blank lines are not content and never appear."""
    old = str(before or "").splitlines()
    new = str(after or "").splitlines()
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.ndiff(old, new):
        text = line[2:].strip()
        if not text:
            continue
        if line.startswith("+ "):
            added.append(text)
        elif line.startswith("- "):
            removed.append(text)
    return {"added": added, "removed": removed}


def handle_base_currency_change(
    profile: dict, old_base: Optional[str], new_base: Optional[str]
) -> None:
    """Side effects of a base-currency change (mutates `profile`).

    1. Clears the FX cache so the next /api/prices re-anchors on the new base.
    2. Stamps a base_currency_review flag for manual amounts the user should revisit.
    """
    if not new_base or new_base.upper() == (old_base or "").upper():
        return
    try:
        from backend.paths import FX_CACHE_JSON

        FX_CACHE_JSON.unlink(missing_ok=True)
    except Exception:
        pass
    fields = currency_sensitive_fields(profile)
    if fields:
        profile["base_currency_review"] = {
            "from": (old_base or "").upper(),
            "to": new_base.upper(),
            "changed_at": datetime.now().isoformat(),
            "fields": fields,
        }
