"""
"Start fresh from <month>" — a blank slate for one budget month, previewed without writing
(ADR 2026-09-08-budget-fresh-start-moves-the-floor-not-the-data + its same-day amendment,
amended again for the RESTORED YNAB identity, ADR
2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category).

The write, ``planning.fresh_start(m)``, does two things in ONE transaction: it clears ``m``'s
assignments (through ``apply_budget_change``) and moves the carry-walk floor to ``m``. It hands
back the snapshot ``undo_fresh_start`` needs, so both halves are exactly reversible.

``planning.fresh_start_preview(m)`` answers "what would Ready to Assign be afterwards" by running
the REAL RTA fold with ``ctx['floor']`` overridden AND ``m``'s assignments zeroed — never by
writing and reverting. The dialog's ledger is the SAME identity the RTA popover renders, exactly:

    after = cash_now − baseline − held_in_envelopes − held_outside_envelopes
            − card_spend_not_covered [− assigned_in_future]

plus that optional last term, the one case the others can't explain on their own (a dollar
already promised to a LATER month still clamps this month's displayed figure).

``held_outside_envelopes`` (ADR 2026-09-10) is the no-bucket term: uncategorized CASH spend and
CASH spend on a category linked to no envelope, each holding its own hole until the roll exactly
as an envelope does. A fresh start neither clears nor re-floors it — it is this month's own
activity — but it IS a term of the identity, so the ledger cannot close without it. This
fixture's 207.68 of Hobbies spending (an unlinked category) is exactly that term throughout.

``held_in_envelopes`` is the SIGNED Σ available over the envelope rows. At the hypothetical
floor nothing carries in and assigned is zeroed, so each row reduces to its OWN month's
activity — which means the term is 0 only on a book with no in-month envelope activity at all.
It goes NEGATIVE for a plain budgeted spend (the envelope holds its own hole until the roll) and
POSITIVE for a same-month refund or a same-month covered card swipe. An earlier draft omitted
the term on the assumption it was always 0, which happened to hold on the fixtures it tried but
was not true by construction (coordinator ruling, 2026-09-08 — still the ruling, and under the
restored identity the term is non-zero far more often, not less).

``card_spend_not_covered`` is THIS month's uncovered card-spend activity only: a card swipe in an
EARLIER month contributes nothing (standing debt never reduces Ready to Assign — that is what
makes "pay the card off over time" expressible), while an uncovered swipe inside ``m`` itself
cancels its own spending row's drain exactly.

These tests pin what makes the whole thing trustworthy: the preview EQUALS the post-write
reality, the preview writes nothing, the identity closes EXACTLY on every shape of book with no
residual term, and the write is atomic and byte-identically undoable.

Isolated (db_isolated + tmp_path): fixture style mirrors tests/test_rta_balance_identity.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.planning import router as planning_router
from backend.db.profile_store import ProfileStore
from backend.services import budgets as budgets_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning

_FX = {"SGD": 1.0}

# The synthetic book: one on-budget cash account, no opening balance (every dollar arrives
# as May income), so the identity's cash side is exactly the income minus the spending.
_ACCOUNTS = [
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 0.0,
        "opening_txn_id": None,
    },
    # Off-budget, so a transfer out of `bank` genuinely leaves on-budget cash — no dedicated
    # ledger term is needed for it any more: it just shows up in `cash_now`, like everything else.
    {
        "id": "broker",
        "name": "Broker",
        "kind": "asset",
        "type": "investment",
        "currency": "SGD",
        "initial_balance": 0.0,
        "opening_txn_id": None,
    },
]

PREV = "2026-05"
MONTH = "2026-06"
# Built-in expense categories (backend/models/category_defs.py) — the same literals every other
# planning test keys off; ``ensure_envelopes_migrated`` mints a 1:1 envelope for each.
FOOD = "cat_13095ba70000"
TRANSPORT = "cat_db5642ab0000"


def _income(date: str, amount: float) -> dict:
    return {
        "kind": "income",
        "date": date,
        "category": "",
        "to_account_id": "bank",
        "to_amount": amount,
        "to_currency": "SGD",
    }


def _expense(date: str, category: str, amount: float, frm: str = "bank") -> dict:
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": frm,
        "from_amount": amount,
        "from_currency": "SGD",
    }


@pytest.fixture
def env(monkeypatch, db_isolated):
    """A book with a POSITIVE carry (food) and an overspend (transport) in ``PREV``, plus
    fresh spending in ``MONTH`` — the exact shape the fresh-start arithmetic is claimed on.

    ``Hobbies`` is minted AFTER ``ensure_envelopes_migrated``, so it is a LABELED category with
    no envelope: its spending lands in the derived "Unbudgeted" group and HOLDS its own hole
    there until the month-end roll (``held_outside_envelopes``, ADR 2026-09-10) rather than
    draining Ready to Assign on the spot. That is the term the dialog's ledger has to name."""
    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    svc.accounts.replace_all(_ACCOUNTS)
    envelopes_service.ensure_envelopes_migrated()
    hobbies = categories_service.create_category("Hobbies")["id"]

    env_of = envelopes_service.env_of()
    svc.transactions.replace_all(
        [
            {**_income(f"{PREV}-01", 1000.0), "id": "t-income"},
            {**_expense(f"{PREV}-10", FOOD, 100.0), "id": "t-food-prev"},
            {**_expense(f"{PREV}-11", TRANSPORT, 80.0), "id": "t-transport-prev"},
            {**_expense(f"{MONTH}-05", FOOD, 40.0), "id": "t-food-cur"},
            {**_expense(f"{MONTH}-07", hobbies, 207.68), "id": "t-hobbies-cur"},
        ]
    )
    store = envelopes_service.assignments_store
    store.set_assigned(PREV, env_of[FOOD], 300.0)
    store.set_assigned(PREV, env_of[TRANSPORT], 50.0)

    return type(
        "Env", (), {"food": env_of[FOOD], "transport": env_of[TRANSPORT], "hobbies": hobbies}
    )


def _identity_holds(p: dict) -> None:
    """Assert the dialog's ledger adds up exactly as the always-present rows read on screen:
    ``after = cash_now + card_credit − baseline − held_in_envelopes − held_outside_envelopes
    − card_spend_not_covered``, to the cent — the SAME identity the RTA popover renders, no
    residual term, ever. The subtracted terms are SIGNED sums, not "≥ 0" quantities:
    ``held_in_envelopes`` is routinely NEGATIVE at the hypothetical floor (a budgeted spend
    leaves its row red, and an envelope holds its own hole) and ``held_outside_envelopes`` is
    negative-or-zero by construction (ADR 2026-09-10), which is exactly why the check subtracts
    rather than assuming a sign. A present
    ``assigned_in_future`` (the one term this can't cover on its own — see
    ``planning.fresh_start_preview``) is subtracted too, so the check stays exact whether or not
    the clamp is live. Asserts inline (rather than returning a bool) so a failure prints the
    actual cent diff instead of a bare ``False``."""
    lhs = p["ready_to_assign_after"]
    rhs = round(
        p["cash_now"]
        + p["card_credit"]
        - p["baseline"]
        - p["held_in_envelopes"]
        - p["held_outside_envelopes"]
        - p["card_spend_not_covered"]
        - p.get("assigned_in_future", 0.0),
        2,
    )
    assert lhs == rhs, (lhs, rhs)


# ── The preview IS the reality ───────────────────────────────────────────────────


def test_preview_equals_the_post_write_reality(env):
    """The whole point of the preview: it runs the REAL fold at the hypothetical state, so the
    promised figure is what the budget reports once the real write lands — floor moved AND the
    month's assignments cleared."""
    preview = planning.fresh_start_preview(MONTH)
    planning.fresh_start(MONTH)
    assert planning.budget_month(MONTH)["ready_to_assign"] == preview["ready_to_assign_after"]


def test_preview_writes_nothing(env):
    before = budgets_service.read_budget_config()
    planning.fresh_start_preview(MONTH)
    assert budgets_service.read_budget_config() == before
    assert before["rollover_start"] is None


def test_preview_reports_the_new_identitys_terms(env):
    """May: 1000 in, 100 food, 80 transport (both assigned). June: 40 more food, plus 207.68 in
    Hobbies, an unlinked category.

    Derivation at the hypothetical floor (floor := June, June's assignments zeroed — there are
    none here anyway):
        cash_now                 = 1000 − 100 − 80 − 40 − 207.68        =  572.32
        held_in_envelopes        = Σ available, and every row is max(0, prev)=0 + assigned 0 +
                                   THIS month's activity: food −40, transport 0, everything else
                                   0; Hobbies is UNLINKED so it has no row at all      −40.00
        held_outside_envelopes   = Hobbies' own hole, held by the no-bucket bucket   −207.68
        card_spend_not_covered                                          =    0.00 (no card)
        baseline                                                        =    0.00
        after = 572.32 − 0 − (−40) − (−207.68) − 0                      =  820.00
    Note the after-figure sits ABOVE cash on hand, twice over: food HOLDS its own $40 hole and
    Hobbies HOLDS its own $207.68 until the roll (ADR 2026-09-09 retires `RTA ≤ cash`;
    2026-09-10 adds the second holder), and starting fresh in June does not change either."""
    preview = planning.fresh_start_preview(MONTH)

    assert preview["month"] == MONTH
    assert preview["cash_now"] == 572.32  # 1000 − 100 − 80 − 40 − 207.68, as of 30 June
    assert preview["baseline"] == 0.0
    assert preview["held_in_envelopes"] == -40.0  # food's own June spend, held, SIGNED
    assert preview["held_outside_envelopes"] == -207.68  # Hobbies', held the same way
    assert preview["card_spend_not_covered"] == 0.0  # no credit account on this fixture
    assert "assigned_in_future" not in preview
    assert preview["ready_to_assign_after"] == 820.0
    _identity_holds(preview)


def test_at_the_floor_month_the_identity_still_holds(env):
    """The earliest data month already carries nothing forward — a fresh start there is still
    not a no-op (it still clears May's assignments).

    Derivation (floor := May, May's 300/50 assignments zeroed):
        cash_now          = 1000 − 100 − 80                                        =  820.00
        held_in_envelopes = food (0 + 0 − 100) + transport (0 + 0 − 80)            = −180.00
        after = 820 − 0 − (−180) − 0                                               = 1000.00
    Clearing the assignments does NOT clear the spending: both rows go red and hold their own
    holes for the rest of May, which is why the promised figure is the full 1000 rather than the
    820 of cash actually in the bank."""
    preview = planning.fresh_start_preview(PREV)
    assert preview["cash_now"] == 820.0  # 1000 − 100 − 80, as of 31 May
    assert preview["held_in_envelopes"] == -180.0
    assert preview["ready_to_assign_after"] == 1000.0
    _identity_holds(preview)


def test_preview_rejects_a_malformed_month(env):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.fresh_start_preview("2026-6")


# ── The identity closes on every shape of book ───────────────────────────────────
# Regression for the owner report of 2026-09-08: the OLD dialog said "Ready to Assign becomes X
# — your cash on hand on the 1st, plus income, minus what you've assigned" and then printed a
# cash figure X did not follow from — the YNAB decomposition needed a growing pile of named
# terms to close. The dialog is now the SAME four terms the RTA popover renders (cash, baseline,
# held in envelopes, card spend not covered), and every scenario below (unlinked-category spend,
# a positive swing in an unlinked category, a transfer leaving the budget) closes without adding
# a residual term. What NONE of them do is move `held_in_envelopes` off the fixture's own −40:
# an unlinked or blank category has no envelope row, and a transfer touches no envelope, so
# only the food spend already in the fixture is being held.


def test_the_preview_ignores_this_months_assignment_because_the_write_clears_it(env):
    """A month with both an inflow and an assignment. The inflow moves cash (and therefore RTA)
    on its own; the ASSIGNMENT changes nothing here, because starting fresh releases it — and
    the preview says so before the write, not after.

    Derivation: cash 572.32 + 500 = 1072.32; held is still just food's own −40 (the 120
    assignment is zeroed by the preview, exactly as the write will zero it) plus Hobbies'
    −207.68 outside; after = 1072.32 + 40 + 207.68 = 1320.00."""
    svc.transactions.append({**_income(f"{MONTH}-02", 500.0), "id": "t-income-cur"})
    envelopes_service.assignments_store.set_assigned(MONTH, env.food, 120.0)

    p = planning.fresh_start_preview(MONTH)
    assert p["cash_now"] == 1072.32  # 572.32 + the extra 500 income
    assert p["held_in_envelopes"] == -40.0  # the ASSIGNMENT is cleared, not merely offset
    assert p["held_outside_envelopes"] == -207.68  # untouched by a fresh start
    assert p["ready_to_assign_after"] == 1320.0
    _identity_holds(p)

    # ...and the real write lands exactly there, assignment gone.
    planning.fresh_start(MONTH)
    assert envelopes_service.assignments_store.month_map(MONTH) == {}
    assert planning.budget_month(MONTH)["ready_to_assign"] == 1320.0


def test_unlinked_category_spend_needs_no_dedicated_row(env):
    """Uncategorized spending is held exactly like the unlinked Hobbies spend
    already on this fixture — neither is an ENVELOPE, and both hold their own hole in the SAME
    no-bucket term (ADR 2026-09-10: one rule for "no bucket", now the holding one). Neither
    needs a row of its own in the ledger: they share the term.

    Derivation: cash 572.32 − 33 = 539.32; held_in_envelopes unchanged at food's −40;
    held_outside_envelopes = −207.68 (Hobbies) + −33 (Uncategorized) = −240.68; after = 820.00."""
    svc.transactions.append({**_expense(f"{MONTH}-08", "", 33.0), "id": "t-unsorted"})

    p = planning.fresh_start_preview(MONTH)
    assert p["cash_now"] == 539.32  # 572.32 − 33
    assert p["held_in_envelopes"] == -40.0  # Uncategorized has no ENVELOPE row of its own
    assert p["held_outside_envelopes"] == -240.68  # ...it holds in the no-bucket term instead
    assert p["ready_to_assign_after"] == 820.0  # unmoved by the new spend — it is held
    _identity_holds(p)


def test_a_positive_swing_in_an_unlinked_category_needs_no_dedicated_row_either(env):
    """An unlinked category that finishes the month POSITIVE (a resale, say) is not spending —
    it is cash arriving, and a no-bucket bucket holds HOLES, never surpluses (``_no_bucket_held``
    clamps at 0 above: nothing can be assigned there, so the money is Ready to Assign's). It
    therefore closes the same way any other inflow does, raising RTA by the full 25.

    Derivation: cash 572.32 + 25 = 597.32; held_in_envelopes unchanged at food's −40;
    held_outside_envelopes still just Hobbies' −207.68 (Resale holds nothing); after = 845.00."""
    resale = categories_service.create_category("Resale")["id"]
    svc.transactions.append(
        {
            "id": "t-resale",
            "kind": "investment",
            "date": f"{MONTH}-09",
            "category": resale,
            "to_account_id": "bank",
            "to_amount": 25.0,
            "to_currency": "SGD",
        }
    )
    p = planning.fresh_start_preview(MONTH)
    assert p["cash_now"] == 597.32  # 572.32 + 25
    assert p["held_in_envelopes"] == -40.0  # Resale is unlinked too — no row holds the +25
    assert p["held_outside_envelopes"] == -207.68  # ...and the bucket doesn't hoard it either
    assert p["ready_to_assign_after"] == 845.0  # 820 + the full 25
    _identity_holds(p)


def test_a_transfer_off_budget_needs_no_dedicated_row_either(env):
    """A transfer OFF budget moves Ready to Assign by the cash it takes with it — no separate
    "other movements" residual is needed to explain it any more.

    Derivation: cash 572.32 − 1000 = −427.68; held_in_envelopes unchanged at food's −40;
    held_outside_envelopes unchanged at Hobbies' −207.68 (an UNCATEGORIZED transfer folds into
    no bucket at all — it simply leaves); after = −180.00 (and `_display_rta`'s clamp deducts
    nothing: there is no positive remainder to consume)."""
    svc.transactions.append(
        {
            "id": "t-to-broker",
            "kind": "transfer",
            "date": f"{MONTH}-09",
            "from_account_id": "bank",
            "from_amount": 1000.0,
            "from_currency": "SGD",
            "to_account_id": "broker",
            "to_amount": 1000.0,
            "to_currency": "SGD",
        }
    )
    p = planning.fresh_start_preview(MONTH)
    assert p["cash_now"] == -427.68  # 572.32 − 1000
    assert p["held_in_envelopes"] == -40.0  # a transfer touches no envelope
    assert p["held_outside_envelopes"] == -207.68  # ...nor, uncategorized, any bucket
    assert p["ready_to_assign_after"] == -180.0
    _identity_holds(p)


# ── Card spend not covered — THIS month's, never a standing debt ──────────────────


@pytest.fixture
def card_env(env):
    """``env`` plus a Visa card carrying STANDING debt from a purchase in April — well before
    the fresh-start window, uncovered (April has nothing assigned to Food yet), and with no card
    spend inside the fresh-start month itself, so its payment envelope holds back nothing new
    (the coverage cap never fires here) AND the identity's card term reads 0: under ADR
    2026-09-09 that term is this MONTH's uncovered card-spend activity, not a debt total."""
    accounts = svc.accounts.all() + [
        {
            "id": "cc",
            "name": "Visa",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 0.0,
            "opening_txn_id": None,
        }
    ]
    svc.accounts.replace_all(accounts)
    txns = svc.transactions.all() + [
        {**_expense("2026-04-15", FOOD, 150.0, frm="cc"), "id": "t-card-standing"}
    ]
    svc.transactions.replace_all(txns)
    planning.sync_card_payment_envelopes()
    return env


def test_a_prior_months_card_debt_is_not_a_term_at_all(card_env):
    """Standing card debt does NOT reduce the promised figure (ADR 2026-09-09 restores rule 4,
    retiring 2026-09-08's continuous netting) — a card's payment envelope starts at 0 and the
    user pays the balance down by assigning into it over time.

    Derivation: the April swipe is outside June, so it contributes to neither June's activity
    map nor June's `card_spend_not_covered`, and it never touched on-budget cash.
        572.32 − baseline 0 − held (food's −40) − outside (Hobbies' −207.68) − card 0 = 820.00
    i.e. exactly the same figure as the card-less fixture."""
    p = planning.fresh_start_preview(MONTH)
    assert p["cash_now"] == 572.32  # the card debt never touched on-budget cash
    assert p["held_in_envelopes"] == -40.0  # nothing credited the payment envelope THIS month
    assert p["held_outside_envelopes"] == -207.68  # Hobbies, as on the card-less fixture
    assert p["card_spend_not_covered"] == 0.0  # April's swipe is not June's activity
    assert p["ready_to_assign_after"] == 820.0
    _identity_holds(p)
    # The debt is still real and still visible — on the payment ROW, not in the identity.
    card_env_id = svc.accounts.find_by_id("cc")["payment_envelope_id"]
    row = next(
        c
        for g in planning.budget_month(MONTH)["groups"]
        for c in g["categories"]
        if c.get("envelope_id") == card_env_id
    )
    assert (row["owed"], row["underfunded"]) == (150.0, 150.0)


def test_funding_the_payment_envelope_in_the_cleared_month_changes_nothing(card_env):
    """Assigning into the payment envelope inside the fresh-start month is released by the
    write, so it cannot change the PREVIEWED after-figure — the preview zeroes the same month's
    assignments the write will."""
    envelopes_service.assignments_store.set_assigned(
        MONTH, svc.accounts.find_by_id("cc")["payment_envelope_id"], 150.0
    )
    p = planning.fresh_start_preview(MONTH)
    assert p["held_in_envelopes"] == -40.0  # the 150 is zeroed, exactly as the write will
    assert p["held_outside_envelopes"] == -207.68
    assert p["card_spend_not_covered"] == 0.0
    assert p["ready_to_assign_after"] == 820.0
    _identity_holds(p)


# ── Held in envelopes: the term a same-month refund or covered card swipe drives POSITIVE ──


def test_a_same_month_refund_drives_held_in_envelopes_positive(env):
    """A refund landing IN a spending envelope this same month is a POSITIVE row activity even
    at the hypothetical floor with assigned zeroed (``_available`` = 0 prev-carry + 0 assigned +
    activity) — the identity has to hold it back from Ready to Assign exactly like the RTA
    popover's own ``held_in_envelopes`` term does, or the preview would overstate the promised
    figure by the refund amount."""
    svc.transactions.append(
        {
            "id": "t-refund",
            "kind": "income",
            "date": f"{MONTH}-10",
            "category": FOOD,
            "to_account_id": "bank",
            "to_amount": 60.0,
            "to_currency": "SGD",
        }
    )
    p = planning.fresh_start_preview(MONTH)
    # Food's June activity is now −40 (spend) + 60 (refund) = +20, and every other envelope row
    # is 0, so held_in_envelopes flips from −40 to +20. Derivation:
    #   cash 572.32 + 60 = 632.32; 632.32 − 0 − 20 − (−207.68) − 0 = 820.00 — the SAME
    #   after-figure as the no-refund case (the 60 raised cash and the held term by the same 60).
    assert p["cash_now"] == 632.32  # 572.32 + 60
    assert p["held_in_envelopes"] == 20.0
    assert p["held_outside_envelopes"] == -207.68
    assert p["ready_to_assign_after"] == 820.0  # 632.32 − 0 − 20 + 207.68
    _identity_holds(p)

    # The promise has to hold once the write actually lands, not just in the preview's own
    # arithmetic — this is the whole point of running the REAL fold at the hypothetical state.
    planning.fresh_start(MONTH)
    assert planning.budget_month(MONTH)["ready_to_assign"] == p["ready_to_assign_after"] == 820.0


def test_a_same_month_covered_card_swipe_needs_both_card_terms(card_env):
    """A card swipe THIS month, covered (partly) by its spending envelope's own headroom,
    credits the payment envelope via the coverage cap (``_capped_card_credits``) — a positive
    row activity at the hypothetical floor, exactly like a same-month refund — while the
    UNCOVERED remainder lands in ``card_spend_not_covered``. Both terms move, and the identity
    still closes to the cent. The headroom has to come from CASH ACTIVITY, not an assignment:
    the hypothetical floor zeroes June's assigned amounts regardless of what is really assigned,
    so a same-month refund into Food is what covers the same-month swipe here (an
    assignment-funded cover is exactly
    ``test_the_preview_ignores_this_months_assignment_because_the_write_clears_it``'s case, and
    correctly does NOT cover anything in the preview). Food ALSO has its own $40 June spend on
    this fixture (from ``env``), which counts against the same headroom pool — so the $70 refund
    covers only $30 of the $70 swipe, not all of it."""
    svc.transactions.append(
        {
            "id": "t-food-refund-june",
            "kind": "income",
            "date": f"{MONTH}-11",
            "category": FOOD,
            "to_account_id": "bank",
            "to_amount": 70.0,
            "to_currency": "SGD",
        }
    )
    svc.transactions.append(
        {**_expense(f"{MONTH}-12", FOOD, 70.0, frm="cc"), "id": "t-card-covered-june"}
    )
    p = planning.fresh_start_preview(MONTH)
    # Headroom = 0 prev-carry + 0 assigned (zeroed) + cash activity (−40 existing food spend +
    # 70 refund) = 30 → covered = min(70, 30) = 30, uncovered = 40. Derivation:
    #   cash_now              = 572.32 + 70 (the refund lands in bank)          = 642.32
    #   Food's own June row   = 0 + 0 + (−40 spend + 70 refund − 70 card drain) = −40.00
    #   payment envelope row  = 0 + 0 + 30 (the capped credit)                  = +30.00
    #   held_in_envelopes     = −40 + 30                                        = −10.00
    #   held_outside_envelopes = Hobbies, untouched by any of this               = −207.68
    #   card_spend_not_covered = 70 − 30                                        =  40.00
    #   after = 642.32 − 0 − (−10) − (−207.68) − 40                             = 820.00
    # Same 820.00 as every other shape of this fixture: the uncovered part is RTA-neutral by
    # construction, and the covered part only relocated money already held.
    assert p["cash_now"] == 642.32  # 572.32 + the 70 refund into bank
    assert p["held_in_envelopes"] == -10.0  # Food's −40 plus the payment envelope's +30
    assert p["held_outside_envelopes"] == -207.68
    assert p["card_spend_not_covered"] == 40.0  # the part the headroom could not reach
    assert p["ready_to_assign_after"] == 820.0
    _identity_holds(p)

    # The promise has to hold once the write actually lands, not just in the preview's own
    # arithmetic — this is the whole point of running the REAL fold at the hypothetical state.
    planning.fresh_start(MONTH)
    assert planning.budget_month(MONTH)["ready_to_assign"] == p["ready_to_assign_after"] == 820.0


# ── Assigned in future months (the one clamp the other four terms can't see) ────


def test_assigned_in_future_is_named_when_the_clamp_is_live(env):
    """A dollar already promised to a LATER month still caps this preview's figure
    (``_display_rta``'s owner-decided rule) — the one case the other four terms can't explain on
    their own, so the preview names the deduction instead of leaving a silent gap.

    Derivation: the base identity is the fixture's usual
    572.32 − 0 − (−40) − (−207.68) − 0 = 820.00, then `min(10, max(0, 820)) = 10` is deducted
    → 810.00."""
    envelopes_service.assignments_store.set_assigned("2026-07", env.food, 10.0)
    p = planning.fresh_start_preview(MONTH)
    assert p["assigned_in_future"] == 10.0
    assert p["held_in_envelopes"] == -40.0
    assert p["held_outside_envelopes"] == -207.68
    assert p["ready_to_assign_after"] == 810.0  # 820.00 − 10
    _identity_holds(p)


def test_assigned_in_future_is_absent_when_nothing_is_promised_later(env):
    p = planning.fresh_start_preview(MONTH)
    assert "assigned_in_future" not in p


# ── The write: a blank slate, in one transaction, exactly undoable ──────────────
# Owner decision 2026-09-08 (amends "moves the floor, never the data"): a fresh start ALSO
# clears the month's assignments. Both halves land together or not at all, and both are
# snapshotted so Undo puts the book back byte-identically.


@pytest.fixture
def funded(env):
    """The target month with real money assigned across two envelopes — including a NEGATIVE
    slot, which ``move_money`` out of a row legitimately produces and which therefore has to
    survive the snapshot/restore round trip."""
    store = envelopes_service.assignments_store
    store.set_assigned(MONTH, env.food, 250.0)
    store.set_assigned(MONTH, env.transport, -40.0)
    return env


def test_fresh_start_clears_the_month_and_moves_the_floor(funded):
    result = planning.fresh_start(MONTH)

    assert envelopes_service.assignments_store.month_map(MONTH) == {}
    assert budgets_service.read_budget_config()["rollover_start"] == MONTH
    # The response is a HANDLE, never the data — see test_the_undo_before_image_never_leaves_the_server.
    assert set(result) == {"token", "month"}
    assert result["month"] == MONTH and result["token"].startswith("fsu_")


def test_the_undo_before_image_never_leaves_the_server(funded):
    """The whole point of the token: the amounts Undo will replay are held server-side, under
    the same singleton `plan_state` row the home-lens switch uses."""
    result = planning.fresh_start(MONTH)
    assert "assignments" not in str(result)

    stored = planning.plan_state_store.get_fresh_start_undo()
    assert stored["token"] == result["token"]
    assert stored["month"] == MONTH
    assert stored["rollover_start"] is None
    assert stored["assignments"] == {funded.food: 250.0, funded.transport: -40.0}
    assert stored["created_at"]


def test_the_snapshot_survives_a_restart(funded):
    """A process-local dict would lose the undo window across a restart mid-window. Reading it
    back through a FRESH store instance is the closest in-test proxy for that."""
    from backend.db.plan_state_store import PlanStateStore

    token = planning.fresh_start(MONTH)["token"]
    assert PlanStateStore().get_fresh_start_undo()["token"] == token
    planning.undo_fresh_start(token)
    assert PlanStateStore().get_fresh_start_undo() is None


def test_fresh_start_touches_no_other_month(funded):
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    planning.fresh_start(MONTH)
    after = envelopes_service.assignments_store.all_map()
    assert after == {m: v for m, v in before.items() if m != MONTH}


def test_undo_restores_the_assignment_table_byte_identically(funded):
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    rta_before = planning.budget_month(MONTH)["ready_to_assign"]

    planning.undo_fresh_start(planning.fresh_start(MONTH)["token"])

    assert envelopes_service.assignments_store.all_map() == before
    assert budgets_service.read_budget_config()["rollover_start"] is None
    assert planning.budget_month(MONTH)["ready_to_assign"] == rta_before


def test_undo_restores_an_earlier_floor_not_a_blanket_clear(funded):
    """The floor was ALREADY set to an earlier month — Undo must put THAT back."""
    planning.set_rollover_start(PREV)
    token = planning.fresh_start(MONTH)["token"]
    assert planning.plan_state_store.get_fresh_start_undo()["rollover_start"] == PREV
    planning.undo_fresh_start(token)
    assert budgets_service.read_budget_config()["rollover_start"] == PREV


def test_undo_is_one_shot(funded):
    """A second undo must not re-write stale amounts over whatever the user has done since."""
    from backend.core.errors import ConflictError

    token = planning.fresh_start(MONTH)["token"]
    planning.undo_fresh_start(token)
    after = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}

    with pytest.raises(ConflictError):
        planning.undo_fresh_start(token)
    assert envelopes_service.assignments_store.all_map() == after


def test_undo_refuses_a_token_that_is_not_the_pending_one(funded):
    """A stale token from another tab, or an invented one, restores nothing."""
    from backend.core.errors import ConflictError

    planning.fresh_start(MONTH)
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    floor_before = budgets_service.read_budget_config()["rollover_start"]

    with pytest.raises(ConflictError):
        planning.undo_fresh_start("fsu_deadbeefcafe")
    assert envelopes_service.assignments_store.all_map() == before
    assert budgets_service.read_budget_config()["rollover_start"] == floor_before


def test_fresh_start_is_atomic_when_the_floor_write_fails(funded, monkeypatch):
    """The assignments are zeroed BEFORE the floor moves. If the floor write raises, the whole
    transaction rolls back — a book that says nothing about a fresh start must not have lost a
    month of assignments to it."""
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(budgets_service, "write_budget_config", boom)
    with pytest.raises(RuntimeError):
        planning.fresh_start(MONTH)

    assert envelopes_service.assignments_store.all_map() == before
    assert budgets_service.read_budget_config()["rollover_start"] is None


def test_fresh_start_refuses_a_future_month_before_writing_anything(funded):
    """Validation lives in ``write_budget_config``, which runs LAST — so the guard that matters
    is the transaction: a refused month must leave the assignments untouched."""
    from backend.core.errors import ValidationError

    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    with pytest.raises(ValidationError):
        planning.fresh_start("2099-01")
    assert envelopes_service.assignments_store.all_map() == before


def test_fresh_start_leaves_the_other_engines_state_alone(funded):
    """Scope: the ZERO-BASED lens' assignment table only. A fresh start in this budget must not
    silently rewrite the 50/30/20 lens the user isn't looking at."""
    from backend.db.budget_engine_state_store import BudgetEngineStateStore

    config = {"targets": {"needs": 50.0, "wants": 30.0}, "mapping": {}}
    store = BudgetEngineStateStore()
    store.set("proportional", config)
    planning.fresh_start(MONTH)
    assert store.get("proportional") == config


def test_both_halves_are_checkpoint_covered(funded, monkeypatch):
    """Clearing an assignment is a money write, so it must reach the checkpoint layer like any
    other — and the floor write with it. Both go through their normal stores, so the DB write
    hook fires with a before-image per row; the point of pinning it is that composing them into
    ONE transaction did not accidentally route around the hook.

    The hook MUST receive this transaction's own connection (not ``None``) — fired conn-less
    from inside an open write transaction it would stall on SQLite's single-writer slot, be
    swallowed as best-effort, and silently capture nothing (``core.io.fire_db_write_hook``)."""
    from backend.core import io as core_io

    seen: list[tuple[str, list, bool]] = []
    monkeypatch.setattr(
        core_io,
        "_db_write_hook",
        lambda table, rows, conn=None: seen.append((table, rows, conn is not None)),
    )
    planning.fresh_start(MONTH)

    tables = {t for t, _rows, _c in seen}
    assert "budget_assignments" in tables and "profile" in tables
    assert all(has_conn for _t, _r, has_conn in seen), "a hook fired outside the transaction"
    # Both cleared rows carry their BEFORE image — that is what a rewind restores.
    before_amounts = {
        float(row[1]["amount"]) for t, rows, _c in seen if t == "budget_assignments" for row in rows
    }
    assert before_amounts == {250.0, -40.0}


def test_undo_refuses_a_blank_token(env):
    from backend.core.errors import ValidationError

    for bad in (None, "", "   ", 5, {"token": "x"}):
        with pytest.raises(ValidationError):
            planning.undo_fresh_start(bad)


# ── The config write choke (the ONLY field this surface sets) ───────────────────


def test_set_rollover_start_moves_the_floor_and_clears_back(env):
    planning.set_rollover_start(MONTH)
    assert budgets_service.read_budget_config()["rollover_start"] == MONTH
    planning.set_rollover_start(None)
    assert budgets_service.read_budget_config()["rollover_start"] is None


def test_set_rollover_start_preserves_a_captured_baseline(env):
    budgets_service.write_budget_config({"pre_budget_baseline": 1234.5})
    planning.set_rollover_start(MONTH)
    assert budgets_service.read_budget_config()["pre_budget_baseline"] == 1234.5


def test_set_rollover_start_rejects_a_malformed_month(env):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.set_rollover_start("June 2026")


def test_set_rollover_start_rejects_a_future_month(env):
    """A floor ahead of today blanks RTA in every month the user can actually see (months
    strictly before the floor read 0) — refuse it rather than render a book of zeros."""
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.set_rollover_start("2099-01")
    assert budgets_service.read_budget_config()["rollover_start"] is None


def test_set_rollover_start_allows_a_month_before_any_data(env):
    """A floor earlier than the first transaction is just "carry everything" — always legal."""
    planning.set_rollover_start("2020-01")
    assert budgets_service.read_budget_config()["rollover_start"] == "2020-01"


@pytest.mark.parametrize("bad", ["June 2026", "2026-99", "2026/04", "20260-1", "2099-01"])
def test_write_budget_config_is_where_the_anchor_is_refused(env, bad):
    """The refusal lives in the SINGLE writer, not at each caller — ``planning`` (REST) and the
    ``set_spending_limit_rollover_start`` tool both write here, and the tool's own check used to
    wave through ``2026-99`` and any future month."""
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        budgets_service.write_budget_config({"rollover_start": bad})
    assert budgets_service.read_budget_config()["rollover_start"] is None


def test_write_budget_config_still_clears_and_still_merges(env):
    """Validation must not have cost the two behaviours every caller relies on."""
    budgets_service.write_budget_config({"pre_budget_baseline": 42.0, "rollover_start": MONTH})
    budgets_service.write_budget_config({"rollover_start": ""})  # blank clears, never raises
    cfg = budgets_service.read_budget_config()
    assert cfg["rollover_start"] is None
    assert cfg["pre_budget_baseline"] == 42.0


# ── The disclosed side effect: the floor is also the month-nav bound ─────────────


def test_fresh_start_locks_month_navigation_until_history_resumes(env):
    """The carry floor doubles as ``nav.earliest``/``can_go_back`` — after starting fresh, the
    Budget cannot page back past that month. Disclosed in the confirm dialog ("Earlier months
    stop showing in Budget until you resume history") and undone by clearing the floor."""
    assert planning.budget_month(MONTH)["nav"]["can_go_back"] is True

    planning.set_rollover_start(MONTH)
    nav = planning.budget_month(MONTH)["nav"]
    assert nav["can_go_back"] is False
    assert nav["earliest"] == MONTH

    planning.set_rollover_start(None)
    nav = planning.budget_month(MONTH)["nav"]
    assert nav["can_go_back"] is True
    assert nav["earliest"] == PREV


# ── Endpoint smoke ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(planning_router)
    return TestClient(app)


def test_get_budget_config(client):
    r = client.get("/api/planning/budget-config")
    assert r.status_code == 200
    assert set(r.json()) == {"rollover_start", "budget_start", "pre_budget_baseline"}


def test_put_budget_config_sets_and_clears(client):
    r = client.put("/api/planning/budget-config", json={"rollover_start": MONTH})
    assert r.status_code == 200
    assert r.json()["rollover_start"] == MONTH
    r = client.put("/api/planning/budget-config", json={"rollover_start": None})
    assert r.status_code == 200
    assert r.json()["rollover_start"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"rollover_start": "2026-13"},
        {"rollover_start": "nope"},
        {"rollover_start": "2099-01"},  # a future floor blanks every visible month
        {},
    ],
)
def test_put_budget_config_refuses_a_bad_body(client, body):
    r = client.put("/api/planning/budget-config", json=body)
    assert 400 <= r.status_code < 500


def test_get_fresh_start_preview(client):
    """The WIRE shape is the promised figure plus the four always-present ledger rows the
    dialog renders under it. ``assigned_in_future`` is absent here (nothing promised to a
    later month on this fixture) — see ``test_assigned_in_future_is_named_when_the_clamp_is_live``
    for the case where it appears."""
    r = client.get("/api/planning/fresh-start-preview", params={"ym": MONTH})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "month",
        "ready_to_assign_after",
        "cash_now",
        "card_credit",
        "held_in_envelopes",
        "held_outside_envelopes",
        "card_spend_not_covered",
        "baseline",
    }
    _identity_holds(body)


def test_get_fresh_start_preview_refuses_a_bad_month(client):
    r = client.get("/api/planning/fresh-start-preview", params={"ym": "2026-13"})
    assert 400 <= r.status_code < 500


def test_post_fresh_start_then_undo_round_trips(client, env):
    envelopes_service.assignments_store.set_assigned(MONTH, env.food, 250.0)
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    promised = client.get("/api/planning/fresh-start-preview", params={"ym": MONTH}).json()

    r = client.post("/api/planning/fresh-start", json={"ym": MONTH})
    assert r.status_code == 200
    handle = r.json()
    assert set(handle) == {"token", "month"}  # a handle, never the amounts
    # The promise held, to the cent.
    body = client.get("/api/planning/month", params={"ym": MONTH}).json()
    assert body["ready_to_assign"] == promised["ready_to_assign_after"]
    assert body["rollover_start"] == MONTH

    r = client.post("/api/planning/fresh-start/undo", json={"token": handle["token"]})
    assert r.status_code == 204
    assert not r.content
    assert envelopes_service.assignments_store.all_map() == before
    assert client.get("/api/planning/budget-config").json()["rollover_start"] is None


@pytest.mark.parametrize("body", [{"ym": "2026-13"}, {"ym": "2099-01"}, {}])
def test_post_fresh_start_refuses_a_bad_body(client, body):
    r = client.post("/api/planning/fresh-start", json=body)
    assert 400 <= r.status_code < 500


# ── The undo route is NOT a bulk-assign back door (pre-merge review, BLOCKER 1) ──
# It is the one path that relaxes ``_op_assign``'s negative-amount guard. An earlier revision
# round-tripped the whole snapshot through the client, so a caller could post its own
# ``assignments`` map and have it replayed with that guard off — a forged
# ``{"env": -5000}`` in a FUTURE month moved displayed Ready to Assign (1000 → 6000 via the
# ``_display_rta`` clamp) and the defaulted null ``rollover_start`` cleared the carry floor.


def test_undo_ignores_a_forged_assignments_map(client, env):
    """The body carries a token and nothing else. Extra fields are dropped (`ONBaseModel` is
    `extra="ignore"`) and the SERVER-HELD before-image is what gets replayed."""
    forged_month = "2026-12"  # a FUTURE month: where the exploit's payload paid off
    envelopes_service.assignments_store.set_assigned(MONTH, env.food, 250.0)
    rta_before = client.get("/api/planning/month", params={"ym": MONTH}).json()["ready_to_assign"]
    token = client.post("/api/planning/fresh-start", json={"ym": MONTH}).json()["token"]

    r = client.post(
        "/api/planning/fresh-start/undo",
        json={
            "token": token,
            "month": forged_month,
            "rollover_start": None,
            "assignments": {env.food: -5000.0, env.transport: -5000.0},
        },
    )
    assert r.status_code == 204
    # The forged month was never touched, and the real month got its REAL amount back.
    assert envelopes_service.assignments_store.month_map(forged_month) == {}
    assert envelopes_service.assignments_store.month_map(MONTH) == {env.food: 250.0}
    # The exploit's payoff was a negative FUTURE assignment inflating the DISPLAYED Ready to
    # Assign through `_display_rta`'s clamp. Nothing moved.
    assert client.get("/api/planning/month", params={"ym": MONTH}).json()["ready_to_assign"] == (
        rta_before
    )
    # ...and the floor came back to the STORED value, not the body's defaulted null.
    assert client.get("/api/planning/budget-config").json()["rollover_start"] is None


def test_undo_without_a_prior_fresh_start_writes_nothing(client, env):
    envelopes_service.assignments_store.set_assigned(MONTH, env.food, 250.0)
    before = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    r = client.post("/api/planning/fresh-start/undo", json={"token": "fsu_deadbeefcafe"})
    assert r.status_code == 409
    assert envelopes_service.assignments_store.all_map() == before


def test_undo_twice_is_a_409(client, env):
    envelopes_service.assignments_store.set_assigned(MONTH, env.food, 250.0)
    token = client.post("/api/planning/fresh-start", json={"ym": MONTH}).json()["token"]
    assert client.post("/api/planning/fresh-start/undo", json={"token": token}).status_code == 204
    after = {m: dict(v) for m, v in envelopes_service.assignments_store.all_map().items()}
    assert client.post("/api/planning/fresh-start/undo", json={"token": token}).status_code == 409
    assert envelopes_service.assignments_store.all_map() == after


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": None}])
def test_post_fresh_start_undo_refuses_a_bad_body(client, body):
    r = client.post("/api/planning/fresh-start/undo", json=body)
    assert 400 <= r.status_code < 500


def test_month_payload_echoes_rollover_start(client):
    assert client.get("/api/planning/month", params={"ym": MONTH}).json()["rollover_start"] is None
    client.put("/api/planning/budget-config", json={"rollover_start": MONTH})
    body = client.get("/api/planning/month", params={"ym": MONTH}).json()
    assert body["rollover_start"] == MONTH
