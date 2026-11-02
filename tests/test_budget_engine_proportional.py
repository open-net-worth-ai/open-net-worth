"""Unit tests for the 50/30/20 engine's GROUP-BASED model (owner design, 2026-09-26; ADR
``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md`` and its
2026-09-26 amendment).

Rows are still the book's ENVELOPES (unchanged since the 2026-09-25 redesign), but the
method's GROUPS are now the book's own Zero-Based groups, in their order — no more
Needs/Wants/Excluded bucket per envelope, no "To sort"/unsorted. A group carries an
optional target share of income (``group_targets``) or sits outside the split
(``excluded_groups``); savings is the residual.

Covers the pure layers with no database: ``proportional_state`` (state normalization,
group assembly, cap history, month validation, target-to-monthly-equivalent),
``proportional_fold`` (category activity, income sourcing, pace history,
``compute_month``), and ``proportional_view`` (``ViewModel`` assembly / ``fold()``).
``draft()`` now needs a database (it reads ``bot_managed_envelope_ids``/``read_profile``
directly rather than purely folding its ``shared`` argument) — those tests use
``db_isolated``, alongside the cross-engine equivalence section at the bottom.
Verb VALIDATION that needs real envelope/category data lives in
``tests/test_budget_engine_proportional_verbs.py`` (``db_isolated``).

``fold()``/``compute_month()`` tests pass a fully-formed ``shared``/``ctx`` dict directly
(mirrors every other engine's own test file — a complete ``shared`` short-circuits every
fallback read in ``shared_context.resolve_shared``, ``envelopes``/``groups`` included).
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.core.errors import ValidationError
from backend.services.budget_engines import proportional_fold as fold_
from backend.services.budget_engines import proportional_state as state_
from backend.services.budget_engines import proportional_view as view_
from backend.services.budget_engines.draft_signals import current_period
from backend.services.budget_engines.proportional import PROPORTIONAL_ENGINE

_RATES = {"SGD": 1.0, "USD": 1.35}  # 1 USD = 1.35 SGD

_CAT_RENT = "cat_rent"
_CAT_GROCERIES = "cat_groceries"
_CAT_DINING = "cat_dining"
_CAT_STREAMING = "cat_streaming"
_CAT_HOBBY = "cat_hobby"
_CAT_MISC = "cat_misc"
_CAT_SALARY = "cat_salary"

_CATEGORIES = [
    {"id": _CAT_RENT, "label": "Rent", "kind": "expense", "color": "#111111"},
    {"id": _CAT_GROCERIES, "label": "Groceries", "kind": "expense", "color": "#222222"},
    {"id": _CAT_DINING, "label": "Dining", "kind": "expense", "color": "#333333"},
    {"id": _CAT_STREAMING, "label": "Streaming", "kind": "expense", "color": "#444444"},
    {"id": _CAT_HOBBY, "label": "Hobby", "kind": "expense", "color": "#555555"},
    {"id": _CAT_MISC, "label": "Misc", "kind": "expense", "color": "#666666"},
    {"id": _CAT_SALARY, "label": "Salary", "kind": "income", "color": "#00ff00"},
]

# Three Zero-Based groups stand in for the old Needs/Wants/Excluded three-way split, but as
# real GROUPS now, not a per-envelope bucket: Bills ~ needs, Lifestyle ~ wants, and Hobbies
# is the one a test excludes wholesale (standing in for the old per-envelope "skip").
_GROUP_BILLS = "g_bills"
_GROUP_LIFESTYLE = "g_lifestyle"
_GROUP_HOBBIES = "g_hobbies"

_GROUPS = [
    {"id": _GROUP_BILLS, "name": "Bills", "order": 0, "is_others": False},
    {"id": _GROUP_LIFESTYLE, "name": "Lifestyle", "order": 1, "is_others": False},
    {"id": _GROUP_HOBBIES, "name": "Hobbies", "order": 2, "is_others": False},
]

_ENV_RENT = "env_rent"
_ENV_GROCERIES = "env_groceries"
_ENV_DINING = "env_dining"
_ENV_STREAMING = "env_streaming"
_ENV_HOBBY = "env_hobby"
_ENV_MISC = "env_misc"

# The one on-budget (plan-source) account every fixture transaction lands on by default, so
# the 2026-09-25 eligibility amendment (below) is a no-op for every test that isn't itself
# about eligibility — mirrors `planning._build_context`'s `plan_source_ids`/`cutoff_by_id`,
# which `compute_month` requires from `ctx` the same way it requires `envelopes`/`groups`.
_ACCT_CASH = "acct_cash"


def _envelope(env_id: str, name: str, cat_ids: list[str], order: int, **extra) -> dict:
    return {
        "id": env_id,
        "name": name,
        "category_ids": cat_ids,
        "group_id": _GROUP_BILLS,
        "order": order,
        "archived": False,
        "color": "#abcdef",
        **extra,
    }


_ENVELOPES = [
    _envelope(_ENV_RENT, "Rent", [_CAT_RENT], 0, group_id=_GROUP_BILLS),
    _envelope(_ENV_GROCERIES, "Groceries", [_CAT_GROCERIES], 1, group_id=_GROUP_BILLS),
    _envelope(_ENV_DINING, "Dining", [_CAT_DINING], 2, group_id=_GROUP_LIFESTYLE),
    _envelope(_ENV_STREAMING, "Streaming", [_CAT_STREAMING], 3, group_id=_GROUP_LIFESTYLE),
    _envelope(_ENV_HOBBY, "Hobby", [_CAT_HOBBY], 4, group_id=_GROUP_HOBBIES),
    _envelope(_ENV_MISC, "Misc", [_CAT_MISC], 5, group_id=_GROUP_LIFESTYLE),
]


def _ctx(txns: list[dict], base: str = "SGD", **overrides) -> dict:
    return {
        "base": base,
        "rates": _RATES,
        "txns": txns,
        "categories": _CATEGORIES,
        "envelopes": _ENVELOPES,
        "groups": _GROUPS,
        "plan_source_ids": frozenset({_ACCT_CASH}),
        "cutoff_by_id": {},
        **overrides,
    }


def _income(
    d: str, amount: float, category: str = "", currency: str = "SGD", account_id: str = _ACCT_CASH
) -> dict:
    return {
        "kind": "income",
        "date": d,
        "category": category,
        "to_amount": amount,
        "to_currency": currency,
        "to_account_id": account_id,
    }


def _expense(
    d: str,
    category: str | None,
    amount: float,
    currency: str = "SGD",
    account_id: str = _ACCT_CASH,
) -> dict:
    return {
        "kind": "expense",
        "date": d,
        "category": category,
        "from_amount": amount,
        "from_currency": currency,
        "from_account_id": account_id,
    }


def _transfer(d: str, category: str | None, amount: float) -> dict:
    return {
        "kind": "transfer",
        "date": d,
        "category": category,
        "from_account_id": "a",
        "to_account_id": "b",
        "from_amount": amount,
        "from_currency": "SGD",
        "to_amount": amount,
        "to_currency": "SGD",
    }


def _split_expense(
    d: str, splits: list[tuple[str, float]], amount: float, account_id: str = _ACCT_CASH
) -> dict:
    return {
        "kind": "expense",
        "date": d,
        "category": "split",
        "from_amount": amount,
        "from_currency": "SGD",
        "from_account_id": account_id,
        "splits": [{"category": cat, "amount": amt} for cat, amt in splits],
    }


def _basic_txns() -> list[dict]:
    return [
        _income("2026-06-01", 5000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 1500),
        _expense("2026-06-06", _CAT_GROCERIES, 400),
        _expense("2026-06-07", _CAT_DINING, 200),
        _expense("2026-06-08", _CAT_STREAMING, 50),
        _expense("2026-06-09", _CAT_HOBBY, 100),  # its own group — a test Excludes it
        _expense("2026-06-10", _CAT_MISC, 75),  # Lifestyle group — no more per-envelope "unsorted"
        _expense("2026-06-11", None, 60),  # uncategorized
    ]


def _own_state(**overrides) -> dict:
    return state_.normalize_own_state(overrides)


# ═════════════════════════════════════════════════════════════════════════════════════
# proportional_state.py — state shape, group assembly, cap history, month validation
# ═════════════════════════════════════════════════════════════════════════════════════


def test_normalize_own_state_defaults_every_field():
    state = state_.normalize_own_state(None)
    assert state == {
        "group_targets": {},
        "excluded_groups": None,
        "excluded_envelopes": [],
        "buckets": {},
        "caps": {},
        "income_category_ids": None,
    }


def test_normalize_own_state_tolerates_malformed_fields():
    state = state_.normalize_own_state(
        {
            "group_targets": "not a dict",
            "excluded_groups": "not-a-list",
            "buckets": {"e1": "needs", "e2": "bogus"},
            "caps": {"e1": [["2026-09", 100.0], ["bad-entry"], "not-a-list"]},
            "income_category_ids": "not-a-list",
        }
    )
    assert state["group_targets"] == {}
    assert state["excluded_groups"] is None
    assert state["buckets"] == {"e1": "needs"}  # "bogus" dropped, only legacy values pass
    assert state["caps"] == {"e1": [["2026-09", 100.0]]}
    assert state["income_category_ids"] is None


def test_normalize_own_state_drops_non_positive_group_targets_and_dedupes_excluded():
    state = state_.normalize_own_state(
        {
            "group_targets": {"g1": 50, "g2": 0, "g3": -5},
            "excluded_groups": ["g4", "g4", "", 5, None],
        }
    )
    assert state["group_targets"] == {"g1": 50.0}  # 0/negative shares are "no target"
    assert state["excluded_groups"] == ["g4"]  # deduped, blank/non-str entries dropped


def test_is_configured_true_once_group_targets_key_exists_even_when_empty():
    """A book that called `configure` with NO group_targets still saved the key (empty
    dict) — that alone means "set up at least once", never re-shown the setup screen."""
    assert state_.is_configured({"group_targets": {}}) is True


def test_is_configured_true_for_a_book_configured_under_the_legacy_targets_key():
    """A book configured before the 2026-09-25 redesign (Needs/Wants `targets`) stays
    configured — the legacy key alone is enough, never demoted back to "never set up"."""
    assert state_.is_configured({"targets": {"needs": 50, "wants": 30}}) is True


def test_is_configured_false_when_never_configured():
    assert state_.is_configured({}) is False
    assert state_.is_configured(None) is False


def test_row_envelopes_excludes_archived_and_managed_and_orders_by_group_then_order():
    envelopes = [
        _envelope(_ENV_DINING, "Dining", [], 1),
        _envelope(_ENV_RENT, "Rent", [], 0),
        _envelope("env_archived", "Old", [], 2, archived=True),
        _envelope("env_card", "Card Payment", [], 3),
    ]
    rows = state_.row_envelopes(envelopes, _GROUPS, {"env_card"})
    assert [r["id"] for r in rows] == [_ENV_RENT, _ENV_DINING]
    assert all(r["group_name"] == "Bills" for r in rows)


def test_row_envelopes_falls_back_to_empty_group_name_for_unresolvable_group_id():
    envelopes = [_envelope("env_x", "Loose", [], 0, group_id="grp_deleted")]
    rows = state_.row_envelopes(envelopes, _GROUPS, set())
    assert rows[0]["group_name"] == ""


def test_row_group_id_returns_the_envelopes_own_group_when_it_resolves():
    assert state_.row_group_id({"group_id": _GROUP_BILLS}, _GROUPS) == _GROUP_BILLS


def test_row_group_id_falls_back_to_others_for_an_unresolvable_group():
    groups = [
        {"id": "g1", "name": "Bills"},
        {"id": "g_others", "name": "Others", "is_others": True},
    ]
    assert state_.row_group_id({"group_id": "grp_deleted"}, groups) == "g_others"


def test_row_group_id_is_none_when_no_others_group_exists_either():
    assert state_.row_group_id({"group_id": "grp_deleted"}, _GROUPS) is None


def test_derive_excluded_groups_a_group_whose_envelopes_were_all_skip_starts_excluded():
    """The set derived from the old per-envelope Excluded choices, once (never chosen
    yet, ``excluded_groups is None``) — the owner's earlier "skip" choices carry over."""
    rows_by_group = {"g1": ["e1", "e2"], "g2": ["e3"]}
    buckets = {"e1": "skip", "e2": "skip", "e3": "needs"}
    assert state_.derive_excluded_groups(rows_by_group, buckets) == ["g1"]


def test_derive_excluded_groups_requires_every_envelope_in_the_group_to_be_skip():
    rows_by_group = {"g1": ["e1", "e2"]}
    buckets = {"e1": "skip", "e2": "wants"}  # not ALL skip -> the group stays counted
    assert state_.derive_excluded_groups(rows_by_group, buckets) == []


def test_derive_excluded_groups_an_empty_group_is_never_derived_excluded():
    assert state_.derive_excluded_groups({"g1": []}, {}) == []


def test_groups_out_lists_a_group_once_it_holds_envelopes():
    """A group holding envelopes is ALWAYS listed, whatever its flags — so no spend silently
    drops out of savings."""
    groups = [{"id": "g_bills", "name": "Bills", "synced": False}]
    rows_by_group = {"g_bills": ["env_x"]}
    rows = [{"group_id": "g_bills", "activity": 500.0}]
    state = state_.normalize_own_state({})
    out = state_.groups_out(groups, rows_by_group, rows, state)
    assert out == [
        {
            "id": "g_bills",
            "name": "Bills",
            "target": None,
            "excluded": False,
            "synced": False,
            "activity": 500.0,
        }
    ]


def test_groups_out_skips_an_empty_synced_group():
    """Commitments/Credit Card Payments: its envelopes are the card's own payments, not
    spending. Its ROWS are always bot-managed (filtered out before this function ever
    sees them), so once it has none the whole group disappears from the split rather
    than showing as an empty, un-fillable row."""
    groups = [{"id": "g_cc", "name": "Credit Card Payments", "synced": True}]
    state = state_.normalize_own_state({})
    assert state_.groups_out(groups, {}, [], state) == []


def test_groups_out_keeps_an_empty_owner_group_as_a_drop_target():
    """A user's own group with nothing in it yet (freshly created) still renders — unlike
    a synced group, it's fillable, so hiding it would make it
    unreachable."""
    groups = [{"id": "g_new", "name": "New Group", "synced": False}]
    state = state_.normalize_own_state({})
    out = state_.groups_out(groups, {}, [], state)
    assert out == [
        {
            "id": "g_new",
            "name": "New Group",
            "target": None,
            "excluded": False,
            "synced": False,
            "activity": 0.0,
        }
    ]


def test_groups_out_adds_a_synthetic_group_for_rows_with_no_real_group():
    """A row whose group resolves to nothing real (no live group, and no "Others" to fall
    back to) still counts — under one synthetic group — so its spend never silently drops
    out of savings."""
    rows_by_group = {state_.NO_GROUP: ["env_x"]}
    rows = [{"group_id": state_.NO_GROUP, "activity": 42.0}]
    state = state_.normalize_own_state({})
    out = state_.groups_out([], rows_by_group, rows, state)
    assert out == [
        {
            "id": state_.NO_GROUP,
            "name": "No group",
            "target": None,
            "excluded": False,
            "synced": False,
            "activity": 42.0,
        }
    ]


def test_cap_history_splice_replaces_current_and_future_entries_only():
    history = [["2026-06", 500.0], ["2026-08", 600.0]]
    spliced = state_.splice_cap_history(history, "2026-07", 550.0)
    assert spliced == [["2026-06", 500.0], ["2026-07", 550.0]]  # 2026-08 entry replaced


def test_cap_history_splice_of_zero_is_a_normal_stored_value():
    spliced = state_.splice_cap_history([["2026-06", 500.0]], "2026-08", 0.0)
    assert spliced == [["2026-06", 500.0], ["2026-08", 0.0]]


def test_cap_at_reads_the_last_entry_at_or_before_the_month():
    history = [["2026-06", 500.0], ["2026-09", 700.0]]
    assert state_.cap_at(history, "2026-06") == 500.0
    assert state_.cap_at(history, "2026-08") == 500.0  # carries forward
    assert state_.cap_at(history, "2026-09") == 700.0
    assert state_.cap_at(history, "2026-12") == 700.0


def test_cap_at_zero_or_absent_reads_as_no_cap():
    assert state_.cap_at([], "2026-06") is None
    assert state_.cap_at([["2026-06", 0.0]], "2026-06") is None
    assert state_.cap_at([["2026-05", 300.0]], "2026-01") is None  # nothing at-or-before


def test_valid_month_rejects_malformed_input():
    assert state_.valid_month("2026-09") == "2026-09"
    with pytest.raises(ValidationError):
        state_.valid_month("2026-9")
    with pytest.raises(ValidationError):
        state_.valid_month("not-a-month")
    with pytest.raises(ValidationError):
        state_.valid_month(None)


def test_require_not_past_month_refuses_before_current_only():
    state_.require_not_past_month("2026-09", "2026-09")  # current month: fine
    state_.require_not_past_month("2026-10", "2026-09")  # future: fine
    with pytest.raises(ValidationError, match="past months are history"):
        state_.require_not_past_month("2026-08", "2026-09")


def test_target_monthly_equivalent_monthly_is_the_amount_itself():
    assert (
        state_.target_monthly_equivalent({"type": "monthly", "amount": 650.0}, "2026-09") == 650.0
    )


def test_target_monthly_equivalent_yearly_is_one_twelfth():
    assert (
        state_.target_monthly_equivalent({"type": "yearly", "amount": 1200.0}, "2026-09") == 100.0
    )


def test_target_monthly_equivalent_weekly_multiplies_by_weekday_occurrences():
    # September 2026: Mondays fall on 7/14/21/28 -> 4 occurrences.
    equiv = state_.target_monthly_equivalent(
        {"type": "weekly", "amount": 50.0, "due_weekday": 0}, "2026-09"
    )
    assert equiv == 200.0


def test_target_monthly_equivalent_by_date_spreads_over_months_remaining():
    equiv = state_.target_monthly_equivalent(
        {"type": "by_date", "amount": 600.0, "date": "2026-12"}, "2026-09"
    )
    assert equiv == 150.0  # 600 / 4 months (Sep, Oct, Nov, Dec)


def test_target_monthly_equivalent_no_target_or_zero_amount_is_zero():
    assert state_.target_monthly_equivalent(None, "2026-09") == 0.0
    assert state_.target_monthly_equivalent({"type": "monthly", "amount": 0}, "2026-09") == 0.0
    assert state_.target_monthly_equivalent({"type": "bogus", "amount": 50}, "2026-09") == 0.0


# ═════════════════════════════════════════════════════════════════════════════════════
# proportional_fold.py — money math (unchanged by the group-based redesign)
# ═════════════════════════════════════════════════════════════════════════════════════


_PLAN_SOURCE_IDS = frozenset({_ACCT_CASH})


def test_category_activity_expense_adds_income_refund_subtracts_transfer_excluded():
    txns = [
        _expense("2026-06-05", _CAT_RENT, 1000),
        _income("2026-06-10", 100, category=_CAT_RENT),  # a refund posted to the same category
        _transfer("2026-06-11", _CAT_RENT, 50),  # never counts, whatever its category
    ]
    by_month = fold_.category_activity_window(
        txns, "SGD", _RATES, "2026-06", months=1, plan_source_ids=_PLAN_SOURCE_IDS
    )
    assert by_month["2026-06"][_CAT_RENT] == 900.0


def test_category_activity_is_split_aware():
    txns = [_split_expense("2026-06-05", [(_CAT_RENT, 300.0), (_CAT_DINING, 200.0)], 500.0)]
    by_month = fold_.category_activity_window(
        txns, "SGD", _RATES, "2026-06", months=1, plan_source_ids=_PLAN_SOURCE_IDS
    )
    assert by_month["2026-06"] == {_CAT_RENT: 300.0, _CAT_DINING: 200.0}


def test_category_activity_values_via_fx_for_a_foreign_leg():
    txns = [_expense("2026-06-05", _CAT_RENT, 100, currency="USD")]  # -> 135.0 SGD
    by_month = fold_.category_activity_window(
        txns, "SGD", _RATES, "2026-06", months=1, plan_source_ids=_PLAN_SOURCE_IDS
    )
    assert by_month["2026-06"][_CAT_RENT] == pytest.approx(135.0)


def test_category_activity_window_clipped_cuts_off_after_the_day_clamped_per_month():
    txns = [
        _expense("2026-08-25", _CAT_RENT, 100),  # day 25 — within a day=25 cutoff
        _expense("2026-08-28", _CAT_RENT, 40),  # day 28 — excluded
    ]
    clipped = fold_.category_activity_window_clipped(
        txns, "SGD", _RATES, ["2026-08"], 25, _PLAN_SOURCE_IDS
    )
    assert clipped["2026-08"][_CAT_RENT] == 100.0


def test_uncategorized_expense_is_never_netted_against_a_blank_category_refund():
    txns = [_expense("2026-06-05", None, 60), _income("2026-06-06", 20)]  # blank-category inflow
    amount, count = fold_.uncategorized_expense(txns, "2026-06", "SGD", _RATES, _PLAN_SOURCE_IDS)
    assert (amount, count) == (60.0, 1)


# ── Eligibility (2026-09-25 amendment): reuses planning.plan_leg_ok verbatim ────────────


def test_category_activity_excludes_an_off_budget_accounts_spend():
    """An expense on an account NOT in ``plan_source_ids`` (an investment/tracking account,
    say) must not fill an envelope — matching Zero-Based's own ``plan_leg_ok`` rule."""
    txns = [
        _expense("2026-06-05", _CAT_RENT, 1000, account_id=_ACCT_CASH),
        _expense("2026-06-06", _CAT_RENT, 500, account_id="acct_offbudget"),
    ]
    by_month = fold_.category_activity_window(
        txns, "SGD", _RATES, "2026-06", months=1, plan_source_ids=_PLAN_SOURCE_IDS
    )
    assert by_month["2026-06"][_CAT_RENT] == 1000.0  # the off-budget leg never counted


def test_category_activity_excludes_a_leg_dated_before_tracking_start():
    """A leg dated before its account's tracking start is already baked into the opening
    balance — Zero-Based excludes it (``plan_leg_ok``/``is_before_tracking_start``), and so
    must 50/30/20."""
    txns = [
        _expense("2026-06-04", _CAT_RENT, 300),  # before the 06-05 tracking start — excluded
        _expense("2026-06-05", _CAT_RENT, 1000),  # exactly at the cutoff — counts
    ]
    by_month = fold_.category_activity_window(
        txns,
        "SGD",
        _RATES,
        "2026-06",
        months=1,
        plan_source_ids=_PLAN_SOURCE_IDS,
        cutoff_by_id={_ACCT_CASH: "2026-06-05"},
    )
    assert by_month["2026-06"][_CAT_RENT] == 1000.0


def test_income_by_category_excludes_off_budget_and_pre_tracking_start_legs():
    """``income_by_category`` (50/30/20's "actual income") applies the same gate to the
    INCOME leg's ``to_account_id`` — an off-budget deposit or a pre-tracking-start leg is
    not money that reached the plan, mirroring Zero-Based's inflow treatment."""
    txns = [
        _income("2026-06-01", 4000, category=_CAT_SALARY, account_id=_ACCT_CASH),
        _income("2026-06-02", 9000, category=_CAT_SALARY, account_id="acct_offbudget"),
        _income("2026-06-03", 500, category=_CAT_SALARY, account_id=_ACCT_CASH),
    ]
    by_cat = fold_.income_by_category(
        txns,
        "2026-06",
        "SGD",
        _RATES,
        _PLAN_SOURCE_IDS,
        cutoff_by_id={_ACCT_CASH: "2026-06-03"},  # drops the 06-01 leg too
    )
    assert by_cat == {_CAT_SALARY: 500.0}


def test_compute_month_activity_excludes_off_budget_spend_and_pre_tracking_start_legs():
    """The same two exclusions, proven at the ``compute_month`` level (not just the raw
    fold) — an envelope's displayed Activity must already reflect them."""
    txns = [
        _expense("2026-06-05", _CAT_RENT, 1000, account_id=_ACCT_CASH),
        _expense("2026-06-06", _CAT_RENT, 400, account_id="acct_offbudget"),  # excluded
        _expense("2026-06-01", _CAT_RENT, 250, account_id=_ACCT_CASH),  # pre-tracking-start
    ]
    computed = fold_.compute_month(
        "2026-06",
        _ctx(
            txns,
            plan_source_ids=frozenset({_ACCT_CASH}),
            cutoff_by_id={_ACCT_CASH: "2026-06-03"},
        ),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    by_id = {e["id"]: e for e in computed["envelopes"]}
    assert by_id[_ENV_RENT]["activity"] == 1000.0


def test_month_status_current_past_future():
    today = date(2026, 9, 25)
    assert fold_.month_status("2026-09", today) == ("current", 25, 30)
    assert fold_.month_status("2026-06", today) == ("past", 30, 30)
    assert fold_.month_status("2026-12", today) == ("future", 31, 31)


@pytest.mark.parametrize(
    "status,profile,actual,expected",
    [
        ("current", 5000.0, 3000.0, (5000.0, "profile")),  # current always profile
        ("current", None, 3000.0, (0.0, "profile")),  # unset profile -> 0, still "profile"
        ("future", 5000.0, 3000.0, (5000.0, "profile")),
        ("past", 5000.0, 3000.0, (3000.0, "actual")),  # past prefers what was received
        ("past", 5000.0, 0.0, (5000.0, "profile")),  # nothing received -> falls to profile
        ("past", None, 0.0, (0.0, "profile")),  # nothing anywhere -> 0
    ],
)
def test_resolve_income_rules(status, profile, actual, expected):
    assert fold_.resolve_income(status, profile, actual) == expected


def test_sum_income_filters_to_the_counted_category_set():
    by_cat = {"cat_a": 4000.0, "cat_b": 1000.0}
    assert fold_.sum_income(by_cat, {"cat_a", "cat_b"}) == 5000.0
    assert fold_.sum_income(by_cat, {"cat_a"}) == 4000.0


def test_envelope_history_last_and_avg3_share_the_trailing_window():
    by_month = {
        "2026-08": {_CAT_RENT: 100.0},
        "2026-07": {_CAT_RENT: 200.0},
        "2026-06": {_CAT_RENT: 300.0},
    }
    months_with_data = {"2026-08", "2026-07", "2026-06"}
    hist = fold_.envelope_history(
        [_CAT_RENT], by_month, None, ["2026-08", "2026-07", "2026-06"], months_with_data
    )
    assert hist["last"] == 100.0
    assert hist["avg3"] == pytest.approx(200.0)  # mean(100, 200, 300)
    assert hist["last_by_day"] == 100.0  # no clipping when not viewing the current month
    assert hist["avg3_by_day"] == pytest.approx(200.0)


def test_envelope_history_excludes_a_window_month_the_book_has_no_data_for_at_all():
    by_month = {"2026-08": {_CAT_RENT: 100.0}, "2026-07": {}, "2026-06": {}}
    months_with_data = {"2026-08"}  # the book only has transactions in August
    hist = fold_.envelope_history(
        [_CAT_RENT], by_month, None, ["2026-08", "2026-07", "2026-06"], months_with_data
    )
    assert hist["avg3"] == 100.0  # NOT mean(100, 0, 0) == 33.3 — July/June don't "exist"


def test_envelope_history_by_day_clips_when_a_clipped_map_is_given():
    by_month = {"2026-08": {_CAT_RENT: 500.0}}
    by_month_clipped = {"2026-08": {_CAT_RENT: 300.0}}  # only up to day 20, say
    hist = fold_.envelope_history([_CAT_RENT], by_month, by_month_clipped, ["2026-08"], {"2026-08"})
    assert hist["last"] == 500.0
    assert hist["last_by_day"] == 300.0


def test_envelope_history_medians_ignore_a_one_off_month():
    """One trip in three months is not the usual month: the mean forecast it again, the
    median doesn't."""
    by_month = {
        "2026-10": {_CAT_RENT: 0.0},
        "2026-09": {_CAT_RENT: 3900.0},
        "2026-08": {_CAT_RENT: 100.0},
    }
    by_month_clipped = {"2026-10": {}, "2026-09": {_CAT_RENT: 0.0}, "2026-08": {_CAT_RENT: 40.0}}
    window = ["2026-10", "2026-09", "2026-08"]
    hist = fold_.envelope_history([_CAT_RENT], by_month, by_month_clipped, window, set(window))
    assert hist["avg3"] == pytest.approx(1333.33)
    assert hist["median3"] == 100.0
    assert hist["rest_median3"] == 60.0  # median(0, 3900, 60)


def test_envelope_history_has_no_rest_to_add_outside_the_running_month():
    by_month = {"2026-08": {_CAT_RENT: 500.0}, "2026-07": {_CAT_RENT: 700.0}}
    window = ["2026-08", "2026-07"]
    hist = fold_.envelope_history([_CAT_RENT], by_month, None, window, set(window))
    assert hist["median3"] == 600.0  # two months: their mean
    assert hist["rest_median3"] == 0.0


# ── compute_month(): the shared computation, GROUP-based ────────────────────────────


def test_compute_month_basic_shape_and_savings_identity():
    """The residual identity (ADR 2026-09-25/-26): income − every COUNTED group's spend −
    the spend no row owns. Excluding Hobbies (Hobby's own group) here stands in for the
    old per-envelope "skip" bucket and lands on the exact same savings figure the old
    model produced — proving the redesign didn't quietly change what savings means, only
    how exclusion is expressed (by group, not by envelope)."""
    computed = fold_.compute_month(
        "2026-06",
        _ctx(_basic_txns()),
        _own_state(
            group_targets={_GROUP_BILLS: 50, _GROUP_LIFESTYLE: 30},
            excluded_groups=[_GROUP_HOBBIES],
        ),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["month"] == "2026-06"
    assert computed["status"] == "past"
    assert computed["income"]["amount"] == 5000.0
    assert computed["income"]["source"] == "actual"

    by_id = {e["id"]: e for e in computed["envelopes"]}
    assert by_id[_ENV_RENT]["activity"] == 1500.0
    assert by_id[_ENV_RENT]["group_id"] == _GROUP_BILLS
    assert by_id[_ENV_RENT]["group"] == "Bills"
    assert by_id[_ENV_GROCERIES]["activity"] == 400.0
    assert by_id[_ENV_DINING]["activity"] == 200.0
    assert by_id[_ENV_STREAMING]["activity"] == 50.0
    assert by_id[_ENV_HOBBY]["group_id"] == _GROUP_HOBBIES
    assert by_id[_ENV_MISC]["group_id"] == _GROUP_LIFESTYLE  # no more per-envelope "unsorted"
    assert "bucket" not in by_id[_ENV_RENT]

    assert computed["unassigned"] == {"uncategorized": 60.0, "no_envelope": 0.0, "categories": []}

    groups_by_id = {g["id"]: g for g in computed["groups"]}
    assert groups_by_id[_GROUP_BILLS]["activity"] == 1900.0  # rent(1500) + groceries(400)
    assert groups_by_id[_GROUP_BILLS]["target"] == 50
    assert groups_by_id[_GROUP_LIFESTYLE]["activity"] == 325.0  # dining+streaming+misc
    assert groups_by_id[_GROUP_HOBBIES]["excluded"] is True

    # Read the residual off the VIEW too — the number Lucius reports — so the fold and the
    # screen are proven to agree, not just asserted to.
    vm = view_.to_view_model(computed, "SGD")
    savings = next(
        m.amount_base
        for m in next(g for g in vm.groups if g.id == "savings").subtotal
        if m.key == "amount"
    )
    assert savings == pytest.approx(2715.0)  # 5000 - 1900 (Bills) - 325 (Lifestyle) - 60
    assert computed["savings"]["amount"] == pytest.approx(2715.0)
    assert computed["savings"]["target"] == pytest.approx(20.0)  # 100 - (50 + 30)


def test_savings_amount_excludes_excluded_groups_and_subtracts_unassigned():
    txns = [
        _income("2026-06-01", 2000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 500),  # Bills, counted
        _expense("2026-06-06", _CAT_HOBBY, 300),  # Hobbies, Excluded — never counts
        _expense("2026-06-07", _CAT_MISC, 100),  # Lifestyle, counted
        _expense("2026-06-08", None, 40),  # uncategorized
    ]
    computed = fold_.compute_month(
        "2026-06",
        _ctx(txns),
        _own_state(excluded_groups=[_GROUP_HOBBIES]),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["savings"]["amount"] == pytest.approx(1360.0)  # 2000 - 500 - 100 - 40


def test_savings_target_is_none_until_a_group_target_is_set():
    computed = fold_.compute_month(
        "2026-06",
        _ctx([]),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["savings"]["target"] is None


def test_savings_target_is_100_minus_the_sum_of_non_excluded_group_targets():
    computed = fold_.compute_month(
        "2026-06",
        _ctx([]),
        _own_state(
            group_targets={_GROUP_BILLS: 50, _GROUP_LIFESTYLE: 30, _GROUP_HOBBIES: 10},
            excluded_groups=[_GROUP_HOBBIES],
        ),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    # Hobbies is Excluded, so its own stored 10% target plays no role in the split either.
    assert computed["savings"]["target"] == pytest.approx(20.0)  # 100 - (50 + 30)


def test_compute_month_envelope_without_a_colour_wears_its_first_categorys():
    """Same fallback as Zero-Based's rows (planning._budget_rows): an envelope that never had a
    colour of its own shows its first category's, so it looks the same under both methods. A
    colour of its own always wins, and one with no categories and no colour has none."""
    envelopes = [
        _envelope(_ENV_RENT, "Rent", [_CAT_RENT], 0, color=""),
        _envelope(_ENV_GROCERIES, "Groceries", [_CAT_GROCERIES], 1),
        _envelope(_ENV_MISC, "Misc", [], 2, color=""),
    ]
    computed = fold_.compute_month(
        "2026-06",
        _ctx([], envelopes=envelopes),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    colour = {e["id"]: e["color"] for e in computed["envelopes"]}
    assert colour[_ENV_RENT] == "#111111"  # Rent category's colour
    assert colour[_ENV_GROCERIES] == "#abcdef"  # its own
    assert colour[_ENV_MISC] is None


def test_compute_month_no_envelope_spend_is_a_real_category_with_no_row():
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_MISC, 40),
    ]
    envelopes_without_misc = [e for e in _ENVELOPES if e["id"] != _ENV_MISC]
    computed = fold_.compute_month(
        "2026-06",
        _ctx(txns, envelopes=envelopes_without_misc),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["unassigned"]["no_envelope"] == 40.0
    assert computed["unassigned"]["uncategorized"] == 0.0


def test_compute_month_no_envelope_spend_never_goes_negative_from_a_refund():
    """A refund on a category with no row envelope must not manufacture negative
    "unassigned" spend (the same "holds holes, never surpluses" rule the RTA no-bucket
    hold already follows)."""
    txns = [_income("2026-06-10", 500, category=_CAT_MISC)]  # a pure refund, no prior spend
    envelopes_without_misc = [e for e in _ENVELOPES if e["id"] != _ENV_MISC]
    computed = fold_.compute_month(
        "2026-06",
        _ctx(txns, envelopes=envelopes_without_misc),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["unassigned"]["no_envelope"] == 0.0


def test_compute_month_excludes_bot_managed_envelopes_from_rows():
    computed = fold_.compute_month(
        "2026-06",
        _ctx(_basic_txns()),
        _own_state(),
        managed_ids={_ENV_RENT},
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert _ENV_RENT not in {e["id"] for e in computed["envelopes"]}


def test_compute_month_cap_and_income_categories_and_configured_flag():
    computed = fold_.compute_month(
        "2026-06",
        _ctx(_basic_txns()),
        _own_state(caps={_ENV_RENT: [["2026-06", 1600.0]]}),
        managed_ids=set(),
        profile_income=6000.0,
        configured=False,
        today=date(2026, 9, 25),
    )
    by_id = {e["id"]: e for e in computed["envelopes"]}
    assert by_id[_ENV_RENT]["cap"] == 1600.0
    assert by_id[_ENV_GROCERIES]["cap"] is None
    assert computed["configured"] is False
    salary = next(c for c in computed["income_categories"] if c["id"] == _CAT_SALARY)
    assert salary == {
        "id": _CAT_SALARY,
        "label": "Salary",
        "color": "#00ff00",
        "counted": True,
        "last_month": 0.0,  # no May income in this fixture
    }


def test_compute_month_income_category_ids_filters_actual_and_counted_flag():
    txns = [
        _income("2026-06-01", 4000, category=_CAT_SALARY),
        _income("2026-06-02", 1000, category=_CAT_MISC),  # not income-kind, ignored anyway
    ]
    computed = fold_.compute_month(
        "2026-06",
        _ctx(txns),
        _own_state(income_category_ids=[_CAT_SALARY]),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    assert computed["income"]["actual"] == 4000.0
    salary = next(c for c in computed["income_categories"] if c["id"] == _CAT_SALARY)
    assert salary["counted"] is True


# ═════════════════════════════════════════════════════════════════════════════════════
# proportional_view.py — ViewModel assembly, via ProportionalEngine.fold()
# ═════════════════════════════════════════════════════════════════════════════════════


def test_fold_groups_are_zero_based_groups_with_envelope_rows():
    """One Group per counted Zero-Based group; each row is an envelope, unchanged in
    shape from the pre-group-based redesign (activity/cap/left measures)."""
    own_state = {"caps": {_ENV_RENT: [["2026-06", 1600.0]]}}
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(_basic_txns()), own_state, None)
    assert {g.id for g in vm.groups} == {_GROUP_BILLS, _GROUP_LIFESTYLE, _GROUP_HOBBIES, "savings"}
    bills = next(g for g in vm.groups if g.id == _GROUP_BILLS)
    assert bills.label == "Bills"
    rent_row = next(r for r in bills.rows if r.id == _ENV_RENT)
    measures = {m.key: m.amount_base for m in rent_row.measures}
    assert measures["activity"] == 1500.0
    assert measures["cap"] == 1600.0
    assert measures["left"] == 100.0
    assert rent_row.status == "ok"  # under cap


def test_fold_row_over_cap_status():
    own_state = {"caps": {_ENV_RENT: [["2026-06", 1000.0]]}}
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(_basic_txns()), own_state, None)
    bills = next(g for g in vm.groups if g.id == _GROUP_BILLS)
    rent_row = next(r for r in bills.rows if r.id == _ENV_RENT)
    assert rent_row.status == "over_cap"


def test_fold_group_rows_include_zero_activity_envelopes():
    """The row universe is the fixed set of row envelopes in the group, not "whichever
    categories happened to have spend" — an envelope with $0 activity this month still
    renders."""
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 500),
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    bills = next(g for g in vm.groups if g.id == _GROUP_BILLS)
    assert {r.id for r in bills.rows} == {_ENV_RENT, _ENV_GROCERIES}
    groceries_row = next(r for r in bills.rows if r.id == _ENV_GROCERIES)
    assert {m.key: m.amount_base for m in groceries_row.measures}["activity"] == 0.0


def test_fold_rows_sorted_by_activity_descending():
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(_basic_txns()), {}, None)
    bills = next(g for g in vm.groups if g.id == _GROUP_BILLS)
    assert [r.id for r in bills.rows] == [_ENV_RENT, _ENV_GROCERIES]  # 1500 before 400
    for row in bills.rows:
        assert row.label


def test_fold_savings_group_carries_no_rows():
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(_basic_txns()), {}, None)
    savings_group = next(g for g in vm.groups if g.id == "savings")
    assert savings_group.rows == []
    income_measure = next(m for m in savings_group.subtotal if m.key == "income")
    assert income_measure.amount_base == 5000.0


def test_fold_unmapped_carries_excluded_group_rows_plus_no_envelope_and_uncategorized():
    vm = PROPORTIONAL_ENGINE.fold(
        "2026-06", _ctx(_basic_txns()), {"excluded_groups": [_GROUP_HOBBIES]}, None
    )
    assert vm.unmapped is not None
    row_ids = {r.id for r in vm.unmapped.rows}
    assert row_ids == {_ENV_HOBBY}
    statuses = {r.id: r.status for r in vm.unmapped.rows}
    assert statuses[_ENV_HOBBY] == _GROUP_HOBBIES  # Row.status carries the row's own group id
    assert vm.unmapped.skipped_amount_base == 100.0  # Hobby's own activity
    assert vm.unmapped.uncategorized_amount_base == 60.0
    assert vm.unmapped.uncategorized_count == 1
    assert vm.unmapped.amount_base == 160.0  # 100 (excluded group) + 0 (no_envelope) + 60


def test_fold_unmapped_is_none_when_nothing_is_excluded_or_unassigned():
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 500),
    ]
    vm = PROPORTIONAL_ENGINE.fold(
        "2026-06",
        _ctx(txns, envelopes=[_envelope(_ENV_RENT, "Rent", [_CAT_RENT], 0)]),
        {},
        None,
    )
    assert vm.unmapped is None


def test_fold_multi_currency_book_folds_in_base_currency():
    txns = [
        _income("2026-06-01", 3000, category=_CAT_SALARY, currency="SGD"),
        _expense("2026-06-05", _CAT_RENT, 100, currency="USD"),  # -> 135.0 SGD
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    bills_measures = {
        m.key: m.amount_base for m in next(g for g in vm.groups if g.id == _GROUP_BILLS).subtotal
    }
    assert bills_measures["amount"] == pytest.approx(135.0)


def test_fold_split_transaction_lines_attributed_to_their_own_envelope():
    txns = [
        _income("2026-06-01", 5000, category=_CAT_SALARY),
        _split_expense("2026-06-05", [(_CAT_RENT, 300.0), (_CAT_DINING, 200.0)], 500.0),
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    groups = {g.id: g for g in vm.groups}
    bills_measures = {m.key: m.amount_base for m in groups[_GROUP_BILLS].subtotal}
    lifestyle_measures = {m.key: m.amount_base for m in groups[_GROUP_LIFESTYLE].subtotal}
    assert bills_measures["amount"] == 300.0
    assert lifestyle_measures["amount"] == 200.0


def test_fold_headline_help_names_the_income_source():
    """Lucius's only qualitative signal for WHERE the income figure came from — the
    ViewModel has no other field for it (Measure is numeric-only)."""
    txns = [
        _income("2026-06-01", 5000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 500),
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    assert "actual" in vm.headline.help.lower() or "received" in vm.headline.help.lower()


def test_fold_zero_income_every_percent_is_none_never_zero():
    txns = [_expense("2026-06-05", _CAT_RENT, 500), _expense("2026-06-06", _CAT_DINING, 100)]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)

    assert vm.headline.status == "no_income"
    assert vm.headline.unit == "percent"
    assert vm.headline.value == 0.0

    for g in vm.groups:
        for m in g.subtotal:
            if m.key == "pct_of_income":
                assert m.percent is None
        for row in g.rows:
            for m in row.measures:
                if m.key == "pct_of_income":
                    assert m.percent is None

    assert vm.health.verdict == "unknown"


def test_fold_negative_savings_reports_real_negative_never_clamped():
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 800),
        _expense("2026-06-06", _CAT_DINING, 400),
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    savings_measures = {m.key: m for m in next(g for g in vm.groups if g.id == "savings").subtotal}
    assert savings_measures["amount"].amount_base == pytest.approx(-200.0)
    assert savings_measures["amount"].tone == "negative"
    assert savings_measures["pct_of_income"].percent == pytest.approx(-20.0)
    assert vm.headline.value == pytest.approx(-20.0)
    assert vm.headline.status == "negative"
    assert vm.health.verdict == "bad"


def test_fold_excluded_group_never_counts_against_savings_the_screen_agrees():
    """An Excluded GROUP is outside the split by the owner's choice — the view's savings,
    headline and health must not count it, exactly as the screen doesn't."""
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 600),
        _expense("2026-06-06", _CAT_DINING, 300),
        _expense("2026-06-07", _CAT_HOBBY, 400),  # Hobbies, Excluded
    ]
    vm = PROPORTIONAL_ENGINE.fold(
        "2026-06", _ctx(txns), {"excluded_groups": [_GROUP_HOBBIES]}, None
    )
    savings = {m.key: m for m in next(g for g in vm.groups if g.id == "savings").subtotal}
    assert savings["amount"].amount_base == pytest.approx(100.0)  # 1000 - 600 - 300
    assert vm.health.verdict != "bad"
    # Still visible to Lucius, beside the split rather than in it.
    assert {r.id: r.status for r in vm.unmapped.rows}[_ENV_HOBBY] == _GROUP_HOBBIES


def test_fold_unconfigured_engine_counts_every_group_until_something_is_excluded():
    """An engine that's never been configured (own_state={}) still folds every real
    Zero-Based group into the split — nothing is Excluded until the owner (or a legacy
    "skip" bucket) says so. There is no per-envelope "unsorted" state anymore: an
    envelope's group is a fact about the book (Zero-Based's own group_id), not something
    50/30/20 configuration can leave undecided."""
    txns = [
        _income("2026-06-01", 1000, category=_CAT_SALARY),
        _expense("2026-06-05", _CAT_RENT, 200),
    ]
    vm = PROPORTIONAL_ENGINE.fold("2026-06", _ctx(txns), {}, None)
    bills = next(g for g in vm.groups if g.id == _GROUP_BILLS)
    assert any(r.id == _ENV_RENT for r in bills.rows)
    assert vm.unmapped is None  # nothing excluded, nothing unassigned


# ═════════════════════════════════════════════════════════════════════════════════════
# proportional_view helpers, in isolation
# ═════════════════════════════════════════════════════════════════════════════════════


def test_pct_is_none_without_income_never_a_fabricated_zero():
    assert view_.pct(100.0, 0.0) is None
    assert view_.pct(0.0, 0.0) is None
    assert view_.pct(50.0, 200.0) == 25.0


def test_apply_verb_unsupported_verb_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        PROPORTIONAL_ENGINE.apply_verb("assign", {}, {})


# ═════════════════════════════════════════════════════════════════════════════════════
# draft() — group-avg-spend/income proposal (2026-09-26). Unlike every test above, this
# now touches the database directly (``envelopes_service.bot_managed_envelope_ids()``/
# ``profile_service.read_profile()``), so every test here needs ``db_isolated`` even
# though the ``shared`` ctx itself is still fully synthetic.
# ═════════════════════════════════════════════════════════════════════════════════════


def test_draft_returns_none_when_there_is_no_income(db_isolated):
    """A book with no ``monthly_net_income`` on file (the default profile) has nothing to
    propose a SHARE of — honestly "we don't know" rather than a fabricated split."""
    assert PROPORTIONAL_ENGINE.draft(_ctx([]), {}) is None


def test_draft_proposes_group_targets_from_three_month_average_spend(db_isolated, monkeypatch):
    """Each group's target is its 3-month average spend as a rounded whole percent of
    income — the SAME history math ``compute_month`` uses for the screen's own pace
    figures (``fold_.envelope_history``), so the proposal and the screen can never
    disagree. Groups with nothing spent still ride along in ``rows`` (for display) but
    are left out of ``group_targets`` (a 0% "target" isn't a target)."""
    from backend.services import profile as profile_service

    monkeypatch.setattr(profile_service, "read_profile", lambda: {"monthly_net_income": 4000.0})
    period = current_period()
    prior_months = fold_.window_months(period, 4)[1:]
    txns = [_expense(f"{m}-05", _CAT_RENT, 2000) for m in prior_months]

    proposal = PROPORTIONAL_ENGINE.draft(_ctx(txns), {})
    assert proposal is not None
    assert proposal["group_targets"] == {_GROUP_BILLS: 50}  # 2000 / 4000 = 50%
    rows_by_group = {r["group_id"]: r for r in proposal["rows"]}
    assert set(rows_by_group) == {_GROUP_BILLS, _GROUP_LIFESTYLE, _GROUP_HOBBIES}
    assert rows_by_group[_GROUP_BILLS] == {
        "group_id": _GROUP_BILLS,
        "name": "Bills",
        "avg_spend": 2000.0,
        "percent": 50,
    }
    assert rows_by_group[_GROUP_LIFESTYLE]["avg_spend"] == 0.0
    assert rows_by_group[_GROUP_LIFESTYLE]["percent"] == 0


def test_draft_rounds_group_percent_to_the_nearest_whole_number(db_isolated, monkeypatch):
    from backend.services import profile as profile_service

    monkeypatch.setattr(profile_service, "read_profile", lambda: {"monthly_net_income": 900.0})
    one_prior_month = fold_.window_months(current_period(), 4)[1]
    txns = [_expense(f"{one_prior_month}-05", _CAT_RENT, 300.0)]  # one active month: avg3 = 300

    proposal = PROPORTIONAL_ENGINE.draft(_ctx(txns), {})
    assert proposal["group_targets"][_GROUP_BILLS] == 33  # 300 / 900 = 33.33...% -> 33


# ═════════════════════════════════════════════════════════════════════════════════════
# Cross-engine equivalence (2026-09-25 amendment): reusing plan_leg_ok closes the gap
# between Zero-Based's and 50/30/20's envelope activity. DB-backed (unlike every test
# above) — mirrors tests/test_budget_engine_equivalence.py's own `env` fixture (duplicated
# rather than imported, matching that file's stated convention: each planning-adjacent
# test file owns its own copy).
# ═════════════════════════════════════════════════════════════════════════════════════

_FOOD_ID = "cat_13095ba70000"  # Food & Dining (built-in, stable id)
_TRANSPORT_ID = "cat_db5642ab0000"  # Transport


@pytest.fixture
def real_book(monkeypatch, db_isolated):
    """One on-budget cash account, one OFF-BUDGET investment account, BUILTIN categories,
    and the first-boot 1:1 envelope migration — real enough that ``planning.budget_month()``
    and ``proportional_fold.compute_month()`` read the exact same store data."""
    from backend.db.category_store import CategoryStore
    from backend.db.profile_store import ProfileStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from backend.services import envelopes as envelopes_service
    from backend.services import ledger as svc
    from backend.services import planning as planning_module

    monkeypatch.setattr(planning_module, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    svc.accounts.replace_all(
        [
            {"id": "bank", "name": "Bank", "kind": "asset", "type": "cash", "currency": "SGD"},
            {
                "id": "brokerage",
                "name": "Brokerage",
                "kind": "asset",
                "type": "investment",  # off-budget: is_plan_source() is False
                "currency": "SGD",
            },
        ]
    )
    envelopes_service.ensure_envelopes_migrated()

    def _set_txns(rows):
        from backend.core.ids import new_id

        svc.transactions.replace_all(
            [r if r.get("id") else {**r, "id": new_id("txn")} for r in rows]
        )

    return type("Env", (), {"set_txns": staticmethod(_set_txns)})


def _proportional_ctx_from_store() -> dict:
    """The real ``ctx`` ``compute_month`` needs, assembled the same way
    ``shared_context.resolve_shared``/``service._shared_context`` do — built by hand here
    (rather than calling either) so this test depends on nothing but store reads + the
    eligibility rule itself."""
    from backend.db.envelope_groups_store import EnvelopeGroupsStore
    from backend.services import categories as categories_service
    from backend.services import envelopes as envelopes_service
    from backend.services import ledger as svc
    from backend.services.cashflow import is_plan_source

    txns = svc.transactions.all()
    accounts = svc.accounts.all()
    return {
        "base": "SGD",
        "rates": {"SGD": 1.0},
        "txns": txns,
        "categories": categories_service.all_category_rows(),
        "envelopes": envelopes_service.all_envelopes(),
        "groups": EnvelopeGroupsStore().list_ordered(),
        "plan_source_ids": frozenset(a["id"] for a in accounts if is_plan_source(a)),
        "cutoff_by_id": {
            aid: cut for aid, cut in svc.cutoffs_by_account(accounts, txns).items() if cut
        },
    }


def test_proportional_activity_equals_zero_based_for_every_envelope(real_book):
    """THE gate for the 2026-09-25 eligibility amendment: 50/30/20's per-envelope activity
    equals Zero-Based's ``budget_month()`` activity for the SAME envelope/month (sign-
    adjusted — Zero-Based is negative for spend, 50/30/20 is positive), even with an
    off-budget account's spend in the mix (proving the equivalence isn't an accident of a
    single-account book)."""
    from backend.services import planning as planning_module

    real_book.set_txns(
        [
            _income("2026-06-01", 5000, category="", account_id="bank"),
            _expense("2026-06-05", _FOOD_ID, 300, account_id="bank"),
            _expense("2026-06-06", _FOOD_ID, 150, account_id="brokerage"),  # off-budget
            _expense("2026-06-07", _TRANSPORT_ID, 80, account_id="bank"),
            _expense("2026-06-08", None, 40, account_id="bank"),  # uncategorized
        ]
    )
    table = planning_module.budget_month("2026-06")
    zb_by_id = {
        c["key"]: c for g in table["groups"] if g["id"] != "__unsorted__" for c in g["categories"]
    }

    computed = fold_.compute_month(
        "2026-06",
        _proportional_ctx_from_store(),
        _own_state(),
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )
    p_by_id = {e["id"]: e for e in computed["envelopes"]}

    assert p_by_id  # the migration actually produced rows to compare
    for row_id, p_row in p_by_id.items():
        assert p_row["activity"] == pytest.approx(-zb_by_id[row_id]["activity"]), row_id

    # And concretely, not just in aggregate: Food nets 300 (bank) − 0 (the brokerage leg
    # never counted) = 300; Transport is 80; every other envelope is untouched.
    assert p_by_id[_env_id_for(_FOOD_ID)]["activity"] == 300.0
    assert p_by_id[_env_id_for(_TRANSPORT_ID)]["activity"] == 80.0


def _env_id_for(category_id: str) -> str:
    from backend.services import envelopes as envelopes_service

    return envelopes_service.env_of()[category_id]


# ═════════════════════════════════════════════════════════════════════════════════════
# 2026-09-28 — one envelope outside the split; a commitment seen when it spends; the spend
# "not in an envelope" as rows (owner: "do all of them e2e")
# ═════════════════════════════════════════════════════════════════════════════════════


def _june(state: dict, txns: list[dict] | None = None, **ctx_overrides) -> dict:
    return fold_.compute_month(
        "2026-06",
        _ctx(txns if txns is not None else _basic_txns(), **ctx_overrides),
        state,
        managed_ids=set(),
        profile_income=None,
        configured=True,
        today=date(2026, 9, 25),
    )


def test_an_envelope_excluded_on_its_own_stays_in_its_group_but_its_spend_is_uncounted():
    base = _june(_own_state())
    out = _june(_own_state(excluded_envelopes=[_ENV_DINING]))
    dining = next(e for e in out["envelopes"] if e["id"] == _ENV_DINING)
    assert dining["excluded"] is True and dining["group_id"] == _GROUP_LIFESTYLE
    lifestyle = {g["id"]: g for g in out["groups"]}[_GROUP_LIFESTYLE]
    assert lifestyle["activity"] == 125.0  # streaming 50 + misc 75 — dining's 200 left out
    assert out["savings"]["amount"] == round(base["savings"]["amount"] + 200.0, 2)
    # Lucius sees it outside the split too, not in its group's rows.
    vm = view_.to_view_model(out, "SGD")
    assert vm.unmapped is not None
    assert any(r.id == _ENV_DINING for r in vm.unmapped.rows)
    assert all(r.id != _ENV_DINING for g in vm.groups for r in g.rows)


def test_a_commitment_envelope_is_a_row_only_in_a_month_it_spends():
    commit_group = {"id": "g_commit", "name": "Commitments", "synced": True}
    commit_env = {
        "id": "env_commit",
        "name": "Insurance",
        "category_ids": ["cat_insurance"],
        "group_id": "g_commit",
        "order": 0,
    }
    cats = [*_CATEGORIES, {"id": "cat_insurance", "label": "Insurance", "kind": "expense"}]
    ctx = {
        "groups": [*_GROUPS, commit_group],
        "envelopes": [*_ENVELOPES, commit_env],
        "categories": cats,
    }

    quiet = _june(_own_state(), **ctx)
    assert all(e["id"] != "env_commit" for e in quiet["envelopes"])
    assert all(g["id"] != "g_commit" for g in quiet["groups"])

    paid = _june(
        _own_state(), [*_basic_txns(), _expense("2026-06-15", "cat_insurance", 900)], **ctx
    )
    row = next(e for e in paid["envelopes"] if e["id"] == "env_commit")
    assert row["activity"] == 900.0 and row["group_id"] == "g_commit"
    assert {g["id"]: g for g in paid["groups"]}["g_commit"]["activity"] == 900.0
    # Seen as a row, so no longer folded unseen into "not in an envelope".
    assert paid["unassigned"]["no_envelope"] == 0.0
    assert paid["savings"]["amount"] == round(quiet["savings"]["amount"] - 900.0, 2)


def test_spend_not_in_an_envelope_is_listed_by_category():
    cats = [
        *_CATEGORIES,
        {"id": "cat_pets", "label": "Pets", "kind": "expense", "color": "#e0a032"},
        {"id": "cat_bonus", "label": "Bonus", "kind": "income"},
    ]
    txns = [
        *_basic_txns(),
        _expense("2026-06-12", "cat_pets", 80),
        _income("2026-06-13", 300, category="cat_bonus"),
    ]
    out = _june(_own_state(), txns, categories=cats)
    assert out["unassigned"]["no_envelope"] == 80.0
    assert out["unassigned"]["categories"] == [
        {"id": "cat_pets", "label": "Pets", "color": "#e0a032", "activity": 80.0}
    ]
