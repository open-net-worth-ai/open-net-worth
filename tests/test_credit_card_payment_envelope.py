"""
Unit tests — YNAB-engine credit-card payment envelopes (ADR
docs/adr/2026-07-09-ynab-engine-credit-card-payment-envelopes.md).

Re-pinned 2026-09-09 against the RESTORED YNAB identity (ADR
docs/adr/2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category.md, which supersedes
2026-09-08-ready-to-assign-is-unassigned-cash one day after it shipped):

    ready_to_assign(m) = cash(m) − baseline − held_in_envelopes(m) − card_spend_not_covered(m)
    available(m, env)  = max(0, available(m-1, env)) + assigned(m, env) + activity(m, env)

``held_in_envelopes`` is the SIGNED Σ available over the envelope rows (an in-month overspend
counts NEGATIVE), and ``card_spend_not_covered`` is THIS month's uncovered card-spend activity
only — never a standing debt total. The two 2026-09-08 payload keys those replace
(``set_aside_base``/``unfunded_card_debt_base``) no longer exist.

Covers: the "signed" default (unset AND explicit) stays byte-identical to the pre-ADR
balance-based-RTA design (a card's signed balance nets against cash); "payment_envelope" mode
excludes a payment-tracked card from cash and makes a COVERED card spend exactly RTA-neutral via
the auto-move fold — and an UNCOVERED one RTA-neutral too, this month AND next (the
``_CARD_UNCOVERED_KEY`` term cancels the spending row's own drain; the debt lives on the card,
surfaced by the payment row's ``underfunded``, never subtracted from Ready to Assign); a
card-paying transfer drains the payment envelope using the TO-leg's own native amount (never
``txn_base_amount``, which prefers the from-leg) and may drive it NEGATIVE, which the clamped
carry RESETS to 0 at the next month roll instead of accumulating (the regression behind the
owner's "sent above what you'd set aside" figure climbing past S$12,000); standing card debt
shifts entirely from cash to the payment envelope's starting-at-$0 pool and never reduces Ready
to Assign, with net worth (the account-balance side) untouched; the fold is shared byte-for-byte
between ``_activity_by_category`` and ``activity_transactions``; ``sync_card_payment_envelopes`` is
idempotent and retires envelopes for cards that drop out (hard-deleted, ADR
2026-09-26-deleting-an-envelope-deletes-it), releasing any assigned money to Ready-to-Assign; the merge/split/delete/move_category guards refuse a
card-payment-synced envelope exactly like a Commitments-synced one, while move_money OUT to
``__rta`` stays allowed (YNAB-faithful).

Fully isolated + offline: mirrors the ``env`` fixture in tests/test_planning_service.py /
tests/test_sync_commitment_categories.py. The profile is DB-backed (Wave 5, JSON->DB) — both
``budgets_service`` and ``services.cashflow`` read it through the same ``ProfileStore`` row now,
and ``db_isolated`` (via the ``env`` fixture) already gives every test its own fresh row, so no
per-module path patch is needed any more.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import cashflow as cashflow_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

# FX pivot on SGD; 1 USD = 1.35 SGD.
_FX = {"SGD": 1.0, "USD": 1.35}


def _opening_txn(account_id, amount, currency="SGD", d="2020-01-01"):
    """A Starting Balance row for `account_id` — the new opening shape moved the amount/date
    off the account and onto a real transaction (account["opening_txn_id"] -> this row's id).
    Dated in the deep past so it never shifts the fixture's pinned rollover_start floor or
    shows up inside a test's own June activity window; mirrors services.ledger.set_account_
    opening's row shape (a to-leg for a positive amount, a from-leg for negative)."""
    row = {
        "id": f"open_{account_id}",
        "date": d,
        "kind": "adjustment",
        "description": "Starting Balance",
        "category": None,
        "source": "manual",
        "cleared": True,
    }
    if amount >= 0:
        row.update({"to_account_id": account_id, "to_amount": amount, "to_currency": currency})
    else:
        row.update(
            {"from_account_id": account_id, "from_amount": -amount, "from_currency": currency}
        )
    return row


_DEFAULT_ACCOUNTS = [
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 1000.0,
    },
    {
        "id": "bank_usd",
        "name": "Bank USD",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "initial_balance": 0.0,
    },
    {
        "id": "amex",
        "name": "Amex",
        "kind": "liability",
        "type": "credit",
        "currency": "SGD",
        "initial_balance": 0.0,
    },
]


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency into tmp_path — mirrors test_planning_service.py's `env`,
    plus a credit-card account ("amex"). Accounts/transactions are DB-backed now (plan 3 task 4)
    — `db_isolated` redirects the process-wide DB singleton, so `svc.accounts`/`svc.transactions`
    (and any fresh `AccountStore()`/`TransactionStore()`, e.g. inside
    `envelopes.is_card_payment_synced`) all resolve against the same isolated database with no
    per-instance path-sync needed. The profile is DB-backed too (Wave 5, JSON->DB) — both
    `budgets_service` and `cashflow_service` read it through the SAME `ProfileStore` singleton
    now (no more separate PROFILE_JSON/PROFILE_FILE module bindings to keep in sync), so seeding
    that one store is enough for both."""
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already redirects
    # planning.assignments_store / envelopes_service.assignments_store to the same fresh,
    # empty per-test database as every other unpinned store.

    # rollover_start pins the recursive-walk floor to "2026-06" regardless of whether a given
    # test happens to seed a transaction dated that month — every test below targets "2026-06".
    ProfileStore().set_raw({"base_currency": "SGD", "budget_config": {"rollover_start": "2026-06"}})
    # New opening shape: an account's Starting Balance is a REAL transaction
    # (account["opening_txn_id"] -> that row), not an account-level field. `openings` tracks
    # the currently-live Starting Balance row per account id — populated by `_normalize_
    # accounts` whenever a test's fixture dict still carries the input-only `initial_balance`
    # key (mirrors services.accounts.create_account's own pop-and-mint) — and `_write_txns`
    # re-seeds every known opening row into the transactions table on EVERY write (accounts OR
    # txns), since a pointer whose row goes missing is a DataIntegrityError, not a silent 0.
    openings: dict[str, dict] = {}
    scenario_txns: list[dict] = []

    def _write_txns():
        from tests.conftest import seed_stub_accounts

        seeded_ids = {t.get("id") for t in scenario_txns if t.get("id")}
        seed = [row for row in openings.values() if row["id"] not in seeded_ids]
        rows = seed + scenario_txns
        # from/to_account_id are now real FKs (task 2) — a stray id a test never routed
        # through set_accounts still needs a stub row; set_accounts's own real rows always
        # win (seed_stub_accounts never overwrites an existing id).
        seed_stub_accounts(rows)
        svc.transactions.replace_all(rows)

    def _normalize_accounts(rows):
        out = []
        for a in rows:
            a = dict(a)
            if "initial_balance" in a:
                amount = round(float(a.pop("initial_balance") or 0), 2)
                if amount != 0:
                    row = _opening_txn(a["id"], amount, a.get("currency", "SGD"))
                    openings[a["id"]] = row
                    a["opening_txn_id"] = row["id"]
                else:
                    openings.pop(a["id"], None)
                    a.setdefault("opening_txn_id", None)
            out.append(a)
        return out

    def _set_txns(rows):
        from backend.core.ids import new_id

        # id is now a real (NOT NULL) DB primary key — mint one for any row that doesn't
        # already carry an explicit id (this file's _expense/_transfer/_income helpers never
        # set one), same as a real create would, and ONCE per row so the id stays stable
        # across any later _write_txns() re-invocation (e.g. triggered by set_accounts).
        scenario_txns[:] = [r if r.get("id") else {**r, "id": new_id("txn")} for r in rows]
        _write_txns()

    def _set_profile(**keys):
        store = ProfileStore()
        cur = store.get_raw()
        cur.update(keys)
        store.set_raw(cur)

    def _set_accounts(rows):
        # from_account_id/to_account_id are RESTRICT FKs onto accounts.id — clear the ledger
        # FIRST so replacing the account list (a real DELETE+INSERT) can never trip over a
        # still-referencing transaction row, then rewrite accounts, then re-seed transactions.
        svc.transactions.replace_all([])
        svc.accounts.replace_all(_normalize_accounts(rows))
        _write_txns()  # keep any newly-registered/changed opening pointer's row present

    _set_accounts(_DEFAULT_ACCOUNTS)

    # First-boot migration (mirrors backend.main's boot sequence): mints a real 1:1 envelope
    # for every BUILTIN expense category.
    envelopes_service.ensure_envelopes_migrated()

    return type(
        "Env",
        (),
        {
            "set_txns": staticmethod(_set_txns),
            "set_profile": staticmethod(_set_profile),
            "set_accounts": staticmethod(_set_accounts),
        },
    )


def _acct_for(currency):
    return "bank" if currency == "SGD" else "bank_usd"


def _expense(date, category, amount, currency="SGD", from_account_id=None):
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": from_account_id or _acct_for(currency),
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def _transfer(
    date,
    from_account_id,
    to_account_id,
    amount,
    currency="SGD",
    category=None,
    to_amount=None,
    to_currency=None,
):
    return {
        "kind": "transfer",
        "date": date,
        "category": category,
        "from_account_id": from_account_id,
        "from_amount": amount,
        "from_currency": currency,
        "to_account_id": to_account_id,
        "to_amount": to_amount if to_amount is not None else amount,
        "to_currency": to_currency or currency,
        "base_amount": amount,
    }


def _cat(table, key):
    for g in table["groups"]:
        for c in g["categories"]:
            if key in (c.get("category_ids") or []):
                return c
    raise AssertionError(f"category {key} not in table")


def _row_by_env(table, env_id):
    for g in table["groups"]:
        for c in g["categories"]:
            if c.get("envelope_id") == env_id:
                return c
    raise AssertionError(f"envelope {env_id} not in table")


def _env(key):
    return envelopes_service.env_of()[key]


def _account_net_worth_base():
    base = svc.base_currency()
    txns = svc.transactions.all()
    accts = svc.accounts.query_accounts(include_archived=True)
    cutoffs = svc.cutoffs_by_account(accts, txns)
    total = 0.0
    for a in accts:
        bal = svc.account_balance(a, txns, cutoffs[a["id"]])
        total += svc.to_base(bal, a.get("currency") or base, base)
    return round(total, 2)


def _sync_and_card_env_id():
    planning.sync_card_payment_envelopes()
    return svc.accounts.find_by_id("amex")["payment_envelope_id"]


# ── Byte-identical guard: default + explicit "signed" ─────────────────────────────


def test_payment_envelope_mode_card_excluded_from_cash(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -300.0},
        ]
    )
    _sync_and_card_env_id()
    table = planning.budget_month("2026-06")
    assert table["on_budget_cash"] == 1000.0  # card excluded entirely, whatever its balance


def test_payment_envelope_starts_at_zero_no_auto_set_aside(env):
    """A payment envelope mints with NO auto-target and $0 available even when the card
    already carries standing debt — the freed RTA is the owner's to allocate, not pre-spent."""
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -300.0},
        ]
    )
    env_id = _sync_and_card_env_id()
    row = _row_by_env(planning.budget_month("2026-06"), env_id)
    assert row["assigned"] == 0.0
    assert row["available"] == 0.0
    assert row["target"] is None


# ── Budgeted card spend: coverage-capped auto-move (v2, ADR v2 revision) ──────────
# moved = min(spend, max(0, spending envelope's Available BEFORE this month's card spends)),
# resolved at the ENVELOPE (envelope⊥category — a merged row shares one pool). A fully covered
# spend is exactly RTA-neutral: the set-aside relocates from the spending envelope to the
# payment envelope, so `held_in_envelopes` ends where it started. An UNCOVERED spend's shortfall
# never reaches the payment envelope — it is yielded under `_CARD_UNCOVERED_KEY` instead, which
# cancels the spending row's own drain inside the identity, so an unfunded swipe is ALSO exactly
# RTA-neutral (ADR 2026-09-09, restoring 2026-07-09; the 2026-09-08 "an uncovered swipe lowers
# RTA by its shortfall" rule is retired — a card charge has not cost you cash, and netting it
# continuously left a revolver's Ready to Assign permanently negative by their old balance).
# The real gap surfaces on the payment row's own `underfunded` figure.
# Supersedes v1 (uncapped: the full spend always credited the envelope).


def test_budgeted_card_spend_is_rta_neutral_in_month(env):
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 200,
        }
    )
    rta_before_spend = planning.budget_month("2026-06")["ready_to_assign"]

    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 150, from_account_id="amex")])
    table = planning.budget_month("2026-06")
    rta_after_spend = table["ready_to_assign"]

    assert rta_after_spend == rta_before_spend  # exactly neutral — fully covered
    grocery = _cat(table, "cat_13095ba70000")
    assert grocery["activity"] == -150.0
    assert grocery["available"] == 50.0  # 200 assigned - 150 spent

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    card_row = _row_by_env(table, card_env_id)
    assert card_row["activity"] == 150.0
    assert card_row["available"] == 150.0


def test_covered_spend_exact_coverage_available_zero(env):
    """Mode A, exact coverage: assigned $100, spend $100 → spending envelope $0, payment
    envelope +$100, RTA unchanged."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 100}
    )
    rta_before = planning.budget_month("2026-06")["ready_to_assign"]

    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 100, from_account_id="amex")])
    table = planning.budget_month("2026-06")

    # Neutral: cash never moved, and the 100 set aside just relocated (Groceries 100 → 0, the
    # payment envelope 0 → 100), so `held_in_envelopes` is still 100 and nothing is uncovered.
    # Derivation, both sides:
    #   before:  cash 1000 − baseline 0 − held (Groceries 100)          − uncovered 0  = 900
    #   after:   cash 1000 − baseline 0 − held (Groceries 0 + card 100) − uncovered 0  = 900
    assert table["ready_to_assign"] == rta_before == 900.0
    assert table["held_in_envelopes"] == 100.0
    assert table["card_spend_not_covered"] == 0.0
    grocery = _row_by_env(table, grocery_env)
    assert grocery["available"] == 0.0

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == 100.0
    assert card_row["owed"] == 100.0  # the card's real balance, on the wire in its own right
    assert card_row["available_for_payment"] == 100.0
    assert card_row["underfunded"] == 0.0
    assert card_row["is_payment_envelope"] is True


def test_unfunded_card_spend_is_rta_neutral_this_month_and_next(env):
    """Mode A, overspend: Groceries assigned $0, spend $20 on card → Groceries available -$20
    (credit overspend), Payment envelope UNCHANGED at $0 (nothing covered).

    THE KEY REVERSAL of ADR 2026-09-09 over 2026-09-08 (which subtracted unfunded card debt
    continuously): Ready to Assign does not move — this month OR next. Derivation; cash never
    moves on a card swipe, so only the subtracted terms do, and they cancel:
        June before: cash 1000 − baseline 0 − held 0        − uncovered 0     = 1000
        June after:  Groceries holds its own −20 (SIGNED, in-month) → held −20;
                     the $20 nothing covered → uncovered +20
                     1000 − 0 − (−20) − 20                                    = 1000
        July:        the carry clamps (max(0, −20) = 0) so Groceries resets → held 0;
                     `card_spend_not_covered` is recomputed for JULY only → 0
                     1000 − 0 − 0 − 0                                         = 1000
    The debt is real and permanent, but it lives on the CARD: it surfaces through the payment
    row's own `underfunded`, and is paid down by assigning into the payment envelope over time
    (the "pay a card off over time" requirement) — never by silently draining Ready to Assign."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    rta_before = planning.budget_month("2026-06")["ready_to_assign"]
    assert rta_before == 1000.0

    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 20, from_account_id="amex")])
    table = planning.budget_month("2026-06")

    assert table["ready_to_assign"] == rta_before == 1000.0
    assert table["held_in_envelopes"] == -20.0  # SIGNED — the hole counts negative
    assert table["card_spend_not_covered"] == 20.0
    grocery = _row_by_env(table, grocery_env)
    assert grocery["available"] == -20.0

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == 0.0  # unchanged — nothing credited
    assert card_row["activity"] == 0.0
    assert card_row["owed"] == 20.0
    assert card_row["underfunded"] == 20.0  # the real, surfaced gap

    # ...and the roll charges nothing: the hole resets, the uncovered term is not carried.
    july = planning.budget_month("2026-07")
    assert _row_by_env(july, grocery_env)["available"] == 0.0
    assert july["held_in_envelopes"] == 0.0
    assert july["card_spend_not_covered"] == 0.0
    assert july["ready_to_assign"] == 1000.0
    assert _row_by_env(july, card_env_id)["underfunded"] == 20.0  # still owed, still visible


def test_partial_coverage_moves_only_the_covered_amount(env):
    """Groceries assigned $30, spend $50 on card → Payment envelope +$30 (capped, not the full
    $50), Groceries available -$20 (still drains in full).

    RTA is unmoved: the covered $30 merely relocated, and the uncovered $20 cancels itself.
    Derivation:
        before: cash 1000 − baseline 0 − held 30 (Groceries) − uncovered 0             = 970
        after:  held = Groceries (30 − 50 = −20) + payment envelope (+30) = +10;
                uncovered = 50 − 30 = 20
                1000 − 0 − 10 − 20                                                     = 970"""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 30}
    )
    rta_before_spend = planning.budget_month("2026-06")["ready_to_assign"]
    assert rta_before_spend == 970.0

    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 50, from_account_id="amex")])
    table = planning.budget_month("2026-06")

    assert table["ready_to_assign"] == 970.0  # unmoved — the swipe cost no cash
    assert table["held_in_envelopes"] == 10.0  # −20 Groceries + 30 payment envelope
    assert table["card_spend_not_covered"] == 20.0
    grocery = _row_by_env(table, grocery_env)
    assert grocery["available"] == -20.0  # 30 assigned - 50 spent, overspent

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == 30.0  # CAPPED at Groceries' own coverage, not the full 50
    assert card_row["owed"] == 50.0
    assert card_row["underfunded"] == 20.0


def test_partial_coverage_next_month_hole_resets_surplus_carries(env):
    """The carry ASYMMETRY is back (ADR 2026-09-09 restores `max(0, prev)`, retiring the
    unclamped carry of 2026-09-08): the overspent spending envelope RESETS to $0 next month,
    while the payment envelope's capped +$30 carries in full.

    July's RTA, by hand: cash is still 1000 (no cash ever moved), held = Groceries max(0,−20)=0
    plus the payment envelope's carried 30 = 30, and `card_spend_not_covered` is recomputed for
    JULY alone → 0 (June's uncovered $20 is never carried).
        July RTA = 1000 − 0 − 30 − 0 = 970 — the same as June. The hole's reset costs nothing
    here because the swipe that made it was on a CARD: the debt stayed on the card instead of
    being charged to Ready to Assign (contrast a CASH overspend, which the roll does charge —
    tests/test_rta_balance_identity.py)."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 30,
        }
    )
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 50, from_account_id="amex")])

    july = planning.budget_month("2026-07")
    grocery_july = _cat(july, "cat_13095ba70000")
    assert grocery_july["available"] == 0.0  # the hole RESETS — the clamped carry

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    card_row_july = _row_by_env(july, card_env_id)
    assert card_row_july["available"] == 30.0  # the CAPPED positive Available carries
    assert july["held_in_envelopes"] == 30.0
    assert july["card_spend_not_covered"] == 0.0  # never carried — June's term was June's
    assert july["ready_to_assign"] == 970.0  # same as June — a card hole is never charged


def test_cover_then_move_retroactively_credits_payment_envelope(env):
    """After an overspend, assigning money into the now-covered category retroactively raises
    available_for_payment — nothing is persisted/order-dependent, everything is recomputed
    fresh from the SAME stored data each read."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 50, from_account_id="amex")])

    card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    before = _row_by_env(planning.budget_month("2026-06"), card_env_id)
    assert before["available_for_payment"] == 0.0  # nothing covered yet

    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 50}
    )
    after = _row_by_env(planning.budget_month("2026-06"), card_env_id)
    assert after["available_for_payment"] == 50.0  # now fully covered, retroactively moved


# ── cover_overspending never touches a payment envelope (security review 2026-09-08, HIGH) ──


def test_cover_overspending_skips_payment_envelopes_and_costs_exactly_the_hole(env):
    """The reviewer's counterexample, pinned, re-derived on the restored identity. Covering used
    to propose the payment row too, and because ``_capped_card_credits`` retroactively credits it
    the moment the SPENDING row is funded, the assignment landed on top of a credit that did not
    exist when the step was computed — money set aside for a card that owes nothing.

    Derivation by hand. Bank opens at 1000, Amex at 0, floor pinned at 2026-06, baseline 0.
      June: a 900 grocery swipe ON the card, then a 900 bank→Amex transfer paying it off.
        cash         = 1000 − 900 (the transfer leaves bank; the card is not a cash account) = 100
        Amex balance = −900 + 900 = 0  → owed 0
        groceries    = 0 assigned − 900 (a card swipe drains its envelope in FULL) = −900
        payment env  = coverage min(900, headroom 0 + 0 + 0) = 0 credited, −900 drained by the
                       paying transfer = −900
        held_in_envelopes = −900 + −900 = −1800 (SIGNED — both rows are in-month holes)
        card_spend_not_covered = 900 (nothing covered the swipe)
        RTA = 100 − 0 − (−1800) − 900 = 1000
      RTA reads ABOVE cash on hand, which ADR 2026-09-09 deliberately permits: two holes are
      HELD in-month and the roll, not an instantaneous clamp, is what resolves them.
    The preview must list ONLY the spending row, 0 → 900 (capped at the displayed RTA of 1000 —
    restored B4 — which does not bite here). Applying it:
        groceries    = 900 − 900 = 0
        payment env  = headroom is now 0 + 900 + 0 = 900, so the swipe credits 900; the transfer
                       still drains 900 → available 0, with NO assignment of its own
        held = 0, card_spend_not_covered = 0, cash untouched at 100
        RTA = 100 − 0 − 0 − 0 = 100
    So covering COSTS Ready to Assign 900 — exactly the hole (ADR 2026-09-09 reverses
    2026-09-08's "covering is free"), and a re-run has nothing left to propose. Had the payment
    row not been excluded it would have proposed a second 900 on top; the B4 cap would clip it
    to the 100 left, parking 100 on a card that owes nothing and leaving RTA at 0."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    env.set_txns(
        [
            _expense("2026-06-10", "cat_13095ba70000", 900, from_account_id="amex"),
            _transfer("2026-06-25", "bank", "amex", 900),
        ]
    )
    before = planning.budget_month("2026-06")
    assert before["ready_to_assign"] == pytest.approx(1000.0, abs=0.01)
    assert before["on_budget_cash"] == pytest.approx(100.0, abs=0.01)
    assert before["held_in_envelopes"] == pytest.approx(-1800.0, abs=0.01)
    assert before["card_spend_not_covered"] == pytest.approx(900.0, abs=0.01)
    assert _cat(before, "cat_13095ba70000")["available"] == pytest.approx(-900.0, abs=0.01)
    assert _row_by_env(before, card_env_id)["available"] == pytest.approx(-900.0, abs=0.01)

    preview = planning.auto_assign_preview("2026-06", "cover_overspending")
    assert [
        (s["envelope_id"], s["current_assigned"], s["new_assigned"]) for s in preview["steps"]
    ] == [(_env("cat_13095ba70000"), 0.0, 900.0)]
    assert preview["total_delta"] == pytest.approx(900.0, abs=0.01)

    result = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "cover_overspending"}
    )
    assert result["applied"] == 1
    assert result["total_delta"] == pytest.approx(900.0, abs=0.01)

    after = planning.budget_month("2026-06")
    assert after["ready_to_assign"] == pytest.approx(100.0, abs=0.01)  # 1000 − the 900 hole
    assert _cat(after, "cat_13095ba70000")["available"] == pytest.approx(0.0, abs=0.01)
    card_row = _row_by_env(after, card_env_id)
    assert card_row["assigned"] == 0.0  # funded by coverage, never by an assignment
    assert card_row["available"] == pytest.approx(0.0, abs=0.01)
    assert after["held_in_envelopes"] == pytest.approx(0.0, abs=0.01)
    assert after["card_spend_not_covered"] == pytest.approx(0.0, abs=0.01)
    # Preview == apply: nothing left to propose on the book the apply produced.
    assert planning.auto_assign_preview("2026-06", "cover_overspending")["steps"] == []


# ── Uncategorized/unlinked card spend: no envelope, no coverage, moved is always 0 ────


def test_uncategorized_card_spend_has_no_pool_and_is_rta_neutral(env):
    """v2 tightening (supersedes v1's unconditional credit): a blank-category card spend has NO
    linked envelope to draw coverage from, so moved is always 0 (nothing credited to the payment
    envelope) — but it is RTA-NEUTRAL, exactly like a linked-but-unfunded swipe, not a drain.

    2026-09-09 REGRESSION, fixed same day (security review): the fix that made uncategorized
    CASH spend drain immediately (removing ``reserved += _unsorted_activity(...)``) also removed
    the ONE offset that made an uncategorized CARD spend neutral in the pre-2026-09-08 engine —
    ``_unsorted_activity`` used to hold the blank-key activity as a mini-envelope, which
    cancelled ``_CARD_UNCOVERED_KEY``'s own +80 yield for this exact candidate. Deleting one side
    of that cancellation without touching the other left a bare +80 subtraction that then
    evaporated at the roll (``card_spend_not_covered`` is per-month, never carried) — RTA dropped
    to 920 in June and silently bounced back to 1000 in July with no user action. Fixed by never
    yielding ``_CARD_UNCOVERED_KEY`` for a candidate with no envelope to cancel against
    (``spend_env_id is None``) — a card never had cash to lose in the first place, so its debt is
    the CARD's business, never RTA's, whether or not a category happens to be attached; the debt
    surfaces only through the payment row's own ``underfunded`` figure, and touches RTA only
    through funding it (assigning into the payment envelope).

    Derivation, June: cash 1000 (a swipe moves none of it, card excluded from cash) − baseline 0
    − held 0 (the "Uncategorized" bucket is a display row, not a term of the identity) − uncovered 0
    (no longer yielded for an unlinked candidate) = 1000, unchanged. July: identical, 1000 — no
    asymmetry to pin, because nothing moved in the first place."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    rta_before = planning.budget_month("2026-06")["ready_to_assign"]
    assert rta_before == 1000.0

    env.set_txns([_expense("2026-06-10", None, 80, from_account_id="amex")])
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 1000.0 == rta_before
    assert table["held_in_envelopes"] == 0.0  # Uncategorized is not an envelope row
    assert table["card_spend_not_covered"] == 0.0  # nothing to cancel, nothing yielded
    card_row = _row_by_env(table, card_env_id)
    assert card_row["activity"] == 0.0  # nothing credited — no envelope to draw coverage from
    assert card_row["underfunded"] == 80.0  # the real gap still surfaces here, not via RTA

    july = planning.budget_month("2026-07")
    assert july["card_spend_not_covered"] == 0.0
    assert july["ready_to_assign"] == 1000.0  # stable across the roll — nothing to release


# ── Card-payment transfer drains the payment envelope (unchanged, uncapped — rule 2/3) ─────


def test_card_payment_transfer_drains_payment_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 150,
        }
    )
    env.set_txns(
        [
            _expense("2026-06-10", "cat_13095ba70000", 150, from_account_id="amex"),
            _transfer("2026-06-20", "bank", "amex", 150),
        ]
    )
    table = planning.budget_month("2026-06")
    card_row = _row_by_env(table, card_env_id)
    assert card_row["activity"] == 0.0  # +150 (fully covered spend) - 150 (payment)
    assert card_row["available"] == 0.0


def test_pay_unbudgeted_drives_payment_envelope_negative(env):
    """Rule 3: paying the card with money that was never set aside sends the payment envelope's
    Available NEGATIVE — NOT clamped.

    The card is now PREPAID ($150 paid against a $0 balance), so `owed` is 0 and `underfunded`
    is 0 with it: `underfunded = max(0, owed − max(available, 0))` clamps the funding side, so an
    overdrawn payment envelope funds nothing but never *owes* extra on top of the balance its
    payment already knocked down. The envelope's own deficit still shows, unclamped, in
    `available_for_payment` — that is the "you sent more than you'd set aside" signal.

    This is exactly why `owed` goes on the wire (ADR 2026-09-08, kept): the client's old
    inversion `underfunded + max(available_for_payment, 0)` reads 0 + 0 = 0 here, which happens
    to be right, but reads 40 for a paid-off card whose envelope still holds 40 — see the
    paid-off case in `useBudgetModel.rtaDelta.test.ts`.

    Ready to Assign, by hand: the payment envelope HOLDS its own −150 in-month (restored ADR
    2026-09-09), and the 150 now sits on the card as credit, which is budget money (ADR
    2026-11-02-a-card-in-credit-is-budget-money): cash 850 + card credit 150 − baseline 0 − held
    (−150) − uncovered 0 = 1150. The −150 is charged at the ROLL, once, and the credit offsets it,
    so the opening 1000 is what remains — as YNAB shows it (its August on the 2026-11-02 test plan
    read +450 above the same identity for a 450 overpayment): see
    ``test_overpaid_payment_envelope_resets_next_month_instead_of_accumulating``."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    env.set_txns([_transfer("2026-06-20", "bank", "amex", 150)])

    table = planning.budget_month("2026-06")
    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == -150.0
    assert card_row["available_for_payment"] == -150.0  # raw, never clamped
    assert card_row["owed"] == 0.0  # prepaid — the balance is +150, not a debt
    assert card_row["underfunded"] == 0.0  # an overdrawn envelope funds nothing, owes nothing
    assert table["on_budget_cash"] == 850.0
    assert table["card_credit"] == 150.0
    assert table["held_in_envelopes"] == -150.0
    assert table["card_spend_not_covered"] == 0.0
    assert table["ready_to_assign"] == 1150.0  # held in-month, charged at the roll


def test_overpaid_payment_envelope_resets_next_month_instead_of_accumulating(env):
    """THE regression test for the complaint that started ADR 2026-09-09: four card rows on the
    owner's live book each reading "S$X sent above what you'd set aside", one past S$12,000,
    because the 2026-09-08 unclamped carry let a payment envelope's negative accumulate every
    month it was overpaid, forever, with no reset.

    Restored: ``available(m) = max(0, available(m-1)) + assigned(m) + activity(m)``. A payment
    envelope driven negative is bounded by THAT MONTH's own overpayment and nothing more.

    Derivation. Bank opens 1000, Amex 0, floor 2026-06, nothing ever assigned. Every payment
    lands on a card that owes nothing, so it becomes card credit — budget money (ADR
    2026-11-02-a-card-in-credit-is-budget-money):
      June  pay 150:  available = max(0, 0)    + 0 − 150 = −150   cash 850 credit 150  held −150
                      RTA 1150 (the −150 held in-month)
      July  pay 200:  available = max(0, −150) + 0 − 200 = −200   cash 650 credit 350  held −200
                      RTA 1200 (NOT −350 — the June hole reset; June's charge landed here, once)
      Aug   pay   0:  available = max(0, −200) + 0 +   0 =    0   cash 650 credit 350  held    0
                      RTA 1000 (July's charge lands here, once; the row is back to 0)
    Before card credit counted, the same book read RTA 1000 / 850 / 650: each charge landed, but
    the money it paid for vanished with it. Under 2026-09-08 the row read −150 / −350 / −350 and
    climbed with every payment."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    env.set_txns(
        [
            _transfer("2026-06-20", "bank", "amex", 150),
            _transfer("2026-07-20", "bank", "amex", 200),
        ]
    )

    june = planning.budget_month("2026-06")
    assert _row_by_env(june, card_env_id)["available_for_payment"] == -150.0
    assert june["on_budget_cash"] == 850.0
    assert june["ready_to_assign"] == 1150.0

    july = planning.budget_month("2026-07")
    july_row = _row_by_env(july, card_env_id)
    assert july_row["available_for_payment"] == -200.0  # THIS month's overpayment only
    assert july_row["available"] == -200.0  # NOT −350: the June hole reset at the roll
    assert july["on_budget_cash"] == 650.0
    assert july["held_in_envelopes"] == -200.0
    assert july["card_credit"] == 350.0
    assert july["ready_to_assign"] == 1200.0  # June's hole charged exactly once, here

    august = planning.budget_month("2026-08")
    assert _row_by_env(august, card_env_id)["available"] == 0.0  # fully reset, never carried
    assert august["held_in_envelopes"] == 0.0
    assert august["ready_to_assign"] == 1000.0  # the overpaid 350 is still yours, as credit


def test_starting_debt_does_not_push_payment_envelope_negative(env):
    """Rule 4, BOTH halves, restored (ADR 2026-09-09 — 2026-09-08's continuous netting is
    retired). A card with ONLY its opening Starting-Balance adjustment (-$150) and no other
    activity: the adjustment never touches an envelope (adjustments carry no category, a
    pre-existing invariant), so the payment envelope's Available stays $0 (not negative, no
    overspend) and the card's real debt surfaces via `underfunded`.

    Standing debt does NOT reduce Ready to Assign. Derivation: cash 1000 (the card is out of the
    cash sum) − baseline 0 − held 0 (the payment envelope starts at 0) − uncovered 0 (no card
    spend THIS month; `card_spend_not_covered` is a per-month activity term, never a debt total)
    = 1000. This is precisely what makes "pay the card off over time" expressible: the user
    assigns into the payment envelope on their own schedule instead of finding their budget
    permanently short by an old balance."""
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -150.0},
        ]
    )
    card_env_id = _sync_and_card_env_id()

    table = planning.budget_month("2026-06")
    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == 0.0  # no overspend from starting debt
    assert card_row["available_for_payment"] == 0.0
    assert card_row["owed"] == 150.0  # the card's true balance, adjustment included
    assert card_row["underfunded"] == 150.0  # nothing funds it
    assert table["ready_to_assign"] == 1000.0  # standing debt never reduces RTA
    assert table["held_in_envelopes"] == 0.0
    assert table["card_spend_not_covered"] == 0.0  # a per-month term, not a debt total


def test_multi_currency_card_spend_cap_uses_native_to_base_amount(env):
    """The cap's spend amount is the card leg's own native→base conversion (never
    txn_base_amount's from-leg preference ambiguity, which doesn't even apply to an expense's
    single leg — this locks in that a USD card spend is capped against a SGD-assigned envelope
    correctly, base-currency-correct throughout)."""
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {
                "id": "amex_usd",
                "name": "Amex USD",
                "kind": "liability",
                "type": "credit",
                "currency": "USD",
                "initial_balance": 0.0,
            },
        ]
    )
    planning.sync_card_payment_envelopes()
    card_env_id = svc.accounts.find_by_id("amex_usd")["payment_envelope_id"]
    grocery_env = _env("cat_13095ba70000")
    # Assign 135 SGD — exactly covers a 100 USD spend at 1.35 SGD/USD (base-converted 135 SGD).
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 135}
    )
    env.set_txns(
        [
            {
                "kind": "expense",
                "date": "2026-06-10",
                "category": "cat_13095ba70000",
                "from_account_id": "amex_usd",
                "from_amount": 100,
                "from_currency": "USD",
                "base_amount": 100,
            }
        ]
    )
    table = planning.budget_month("2026-06")
    grocery = _row_by_env(table, grocery_env)
    assert grocery["available"] == 0.0  # 135 assigned - 135 (100 USD -> base) spent

    card_row = _row_by_env(table, card_env_id)
    assert card_row["available"] == 135.0  # fully covered at the base-converted amount


def test_card_payment_transfer_fires_regardless_of_own_category(env):
    """A card-directed transfer is ALWAYS a payment — its own `category` field (if any stray
    label exists) is inert; the categorized-transfer XOR fold never runs for it."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    stray_cat_env = _env("cat_db5642ab0000")
    env.set_txns(
        [
            _transfer("2026-06-20", "bank", "amex", 150, category="cat_db5642ab0000"),
        ]
    )
    table = planning.budget_month("2026-06")
    card_row = _row_by_env(table, card_env_id)
    assert card_row["activity"] == -150.0
    transport = _cat(table, "cat_db5642ab0000")
    assert transport["activity"] == 0.0  # the stray category never absorbed anything
    assert stray_cat_env  # sanity: the envelope really exists


# ── Multi-currency: the TO-leg's own native amount, never txn_base_amount ─────────


def test_multi_currency_card_payment_uses_to_leg_native_amount(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {
                "id": "amex_usd",
                "name": "Amex USD",
                "kind": "liability",
                "type": "credit",
                "currency": "USD",
                "initial_balance": 0.0,
            },
        ]
    )
    planning.sync_card_payment_envelopes()
    card_env_id = svc.accounts.find_by_id("amex_usd")["payment_envelope_id"]

    # from_amount (100 SGD) deliberately does NOT equal to_amount*rate (50*1.35=67.5) — proves
    # the drain uses the TO-leg (the card's own native amount), never txn_base_amount (which
    # always prefers the from-leg and would wrongly read 100).
    env.set_txns(
        [
            _transfer(
                "2026-06-15",
                "bank",
                "amex_usd",
                100,
                currency="SGD",
                to_amount=50,
                to_currency="USD",
            )
        ]
    )
    table = planning.budget_month("2026-06")
    card_row = _row_by_env(table, card_env_id)
    assert card_row["activity"] == -67.5
    assert card_row["available"] == -67.5


# ── Fold parity: _activity_by_category vs activity_transactions ───────────────────


def test_fold_parity_activity_by_category_matches_activity_transactions(env):
    card_env_id = None
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 150,
        }
    )
    env.set_txns(
        [{**_expense("2026-06-10", "cat_13095ba70000", 150, from_account_id="amex"), "id": "t1"}]
    )

    ctx = planning._build_context("2026-06")
    activity_map = planning._activity_by_category(
        ctx["txns"],
        "2026-06",
        ctx["base"],
        ctx["rates"],
        ctx["plan_source_ids"],
        ctx["cutoff_by_id"],
        ctx["card_pay_env_of"],
        ctx,
    )
    assert activity_map[card_env_id] == 150.0  # fully covered — the whole $150 moves

    # CARD-CENTRIC popover (owner 2026-07-10): the payment-envelope Activity list shows the card's
    # OWN transactions — here the $150 spend, signed as the card leg (−150, a spend). The envelope's
    # covered credit (+150) is the FOLD asserted above / the breakdown box's Funded Spending, not a
    # list row.
    detail = planning.activity_transactions([card_env_id], "2026-06")
    assert [r["id"] for r in detail["transactions"]] == ["t1"]
    assert detail["transactions"][0]["card_leg_amount"] == -150.0
    assert detail["transactions"][0]["txn_type"] == "spend"


def test_fold_parity_partial_coverage_agrees_on_the_capped_amount(env):
    """The ENVELOPE fold caps the credit at the assigned $30 (never the raw $50 spend); the
    card-centric Activity popover list, by contrast, shows the card's raw $50 leg (owner
    2026-07-10) — deliberately different views (envelope accounting vs the card's own register)."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 30,
        }
    )
    env.set_txns(
        [{**_expense("2026-06-10", "cat_13095ba70000", 50, from_account_id="amex"), "id": "t1"}]
    )

    ctx = planning._build_context("2026-06")
    activity_map = planning._activity_by_category(
        ctx["txns"],
        "2026-06",
        ctx["base"],
        ctx["rates"],
        ctx["plan_source_ids"],
        ctx["cutoff_by_id"],
        ctx["card_pay_env_of"],
        ctx,
    )
    assert activity_map[card_env_id] == 30.0  # capped, not the full 50

    detail = planning.activity_transactions([card_env_id], "2026-06")
    assert [r["id"] for r in detail["transactions"]] == ["t1"]
    assert detail["transactions"][0]["card_leg_amount"] == -50.0  # the card's RAW leg, not the cap


def test_unbudgeted_group_never_shows_a_payment_envelope_row(env):
    """Regression guard: a zero-category envelope that receives a DIRECT fold (keyed by its
    OWN row id, not a real category) must not leak into the synthetic Unbudgeted group — it is
    already budgeted via its own row."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 150, from_account_id="amex")])
    table = planning.budget_month("2026-06")
    unbudgeted = next((g for g in table["groups"] if g.get("unbudgeted")), None)
    if unbudgeted is not None:
        keys = {c["key"] for c in unbudgeted["categories"]}
        card_env_id = svc.accounts.find_by_id("amex")["payment_envelope_id"]
        assert card_env_id not in keys


# ── sync_card_payment_envelopes: idempotency + retirement ─────────────────────────


def test_sync_card_payment_envelopes_idempotent(env):
    env.set_profile(credit_card_mode="payment_envelope")
    first = planning.sync_card_payment_envelopes()
    env_id_1 = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    second = planning.sync_card_payment_envelopes()
    env_id_2 = svc.accounts.find_by_id("amex")["payment_envelope_id"]

    assert first["synced"] == second["synced"] == 1
    assert first["removed"] == second["removed"] == 0
    assert env_id_1 == env_id_2
    group_envelopes = [
        e
        for e in envelopes_service.envelope_store.all()
        if not e.get("archived") and e.get("group_id") == first["group_id"]
    ]
    assert len(group_envelopes) == 1  # not duplicated on re-run


def test_sync_mints_pinned_group_with_canonical_name(env):
    env.set_profile(credit_card_mode="payment_envelope")
    result = planning.sync_card_payment_envelopes()
    group = planning.groups_store.find_by_id(result["group_id"])
    assert group["name"] == "Credit Card Payments"


def test_card_archive_retires_envelope_and_releases_assigned_money(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    planning.assignments_store.set_assigned("2026-06", env_id, 300.0)

    accts = svc.accounts.all()
    for a in accts:
        if a["id"] == "amex":
            a["archived"] = True
    env.set_accounts(accts)

    result = planning.sync_card_payment_envelopes()
    assert result["removed"] == 1
    assert envelopes_service.envelope_store.find_by_id(env_id) is None
    assert planning.assignments_store.assigned("2026-06", env_id) == 0.0  # released to RTA
    assert svc.accounts.find_by_id("amex").get("payment_envelope_id") is None


def test_hard_deleted_account_orphaned_envelope_is_still_retired(env):
    """Unlike a commitment (never hard-deleted), an account CAN be — the retire step diffs
    against the pinned group's LIVE membership, not the accounts list, so an orphaned envelope
    (its owning account row is simply gone) is still caught."""
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()

    accts = svc.accounts.all()
    env.set_accounts([a for a in accts if a["id"] != "amex"])  # hard-delete the row

    result = planning.sync_card_payment_envelopes()
    assert result["removed"] == 1
    assert envelopes_service.envelope_store.find_by_id(env_id) is None


# ── Guards: merge/split/delete/move_category refuse a synced envelope ─────────────


def test_manual_delete_refuses_card_payment_synced_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    with pytest.raises(ValidationError, match="cannot be deleted"):
        envelopes_service.delete_envelope(env_id)


def test_manual_merge_refuses_card_payment_synced_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    other = envelopes_service.create_envelope("Other")
    with pytest.raises(ValidationError, match="cannot be merged"):
        envelopes_service.merge_envelope(env_id, other["id"])


def test_manual_split_refuses_card_payment_synced_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    with pytest.raises(ValidationError, match="cannot be split"):
        envelopes_service.split_category(env_id, "cat_13095ba70000")


def test_move_category_refuses_card_payment_synced_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    other_group = planning.groups_store.create({"name": "Somewhere else", "categories": []})
    with pytest.raises(ValidationError, match="cannot be moved"):
        planning.apply_budget_change(
            {"type": "move_category", "category": env_id, "group_id": other_group["id"]}
        )


def test_move_money_out_of_card_payment_envelope_to_rta_is_allowed(env):
    """YNAB-faithful: unlike merge/split/delete/move_category, moving money OUT of a payment
    envelope to Ready-to-Assign is a normal, allowed operation, and it RELEASES the money
    (acceptance BUD-MOVE-RTA).

    Derivation. Groceries is assigned 150 and a 150 card swipe draws all of it, so coverage
    moves the whole 150 into the payment envelope: cash 1000, held = Groceries 0 + payment 150
    = 150, uncovered 0 → RTA 850. Pulling 50 back to Ready to Assign sets the payment row's
    assigned to −50, so its available is 150 − 50 = 100 and held drops to 100 → RTA 900, i.e.
    +50. Under 2026-09-08 this was a NET ZERO (the release was cancelled by `unfunded_card_debt`
    rising 0 → 50); ADR 2026-09-09 retires that netting — the card's real gap now shows up on
    the row's own `underfunded`, never as a second subtraction from Ready to Assign. The cap is
    the row's AVAILABLE, not its assigned — assigned goes to −50, which is a legal state (ADR
    2026-07-04-move-money-caps-at-available-not-assigned)."""
    env.set_profile(credit_card_mode="payment_envelope")
    env_id = _sync_and_card_env_id()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 150,
        }
    )
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 150, from_account_id="amex")])
    before = planning.budget_month("2026-06")
    assert (before["held_in_envelopes"], before["card_spend_not_covered"]) == (150.0, 0.0)
    assert before["ready_to_assign"] == 850.0
    result = planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": env_id,
            "to_envelope_id": "__rta",
            "amount": 50,
        }
    )
    assert result["ok"] is True
    assert planning.assignments_store.assigned("2026-06", env_id) == -50.0
    after = planning.budget_month("2026-06")
    assert (after["held_in_envelopes"], after["card_spend_not_covered"]) == (100.0, 0.0)
    assert after["ready_to_assign"] == 900.0 == before["ready_to_assign"] + 50.0  # released
    assert _row_by_env(after, env_id)["underfunded"] == 50.0  # the gap shows HERE, not in RTA


# ── Endpoint-shape smoke: the pinned group + on_budget_cash exclusion ─────────────


def test_budget_month_wire_shows_pinned_group_and_card_excluded_from_cash(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -200.0},
        ]
    )
    planning.sync_card_payment_envelopes()
    table = planning.budget_month("2026-06")

    card_group = next(g for g in table["groups"] if g["name"] == "Credit Card Payments")
    assert card_group["synced"] is True
    assert table["on_budget_cash"] == 1000.0  # card's -200 balance excluded entirely


# ── cashflow predicates: is_payment_tracked_card / is_cash_account / is_plan_source ────


def test_is_payment_tracked_card_type_only_every_credit_account():
    """ADR 2026-09-07 widens the v1 `credit_card`-only rule (ADR 2026-07-09): every `credit`
    account — card, BNPL, line of credit alike, now one type — is payment-tracked."""
    assert cashflow_service.is_payment_tracked_card({"type": "credit"}) is True
    assert cashflow_service.is_payment_tracked_card({"type": "cash"}) is False
    assert cashflow_service.is_payment_tracked_card({"type": "loan"}) is False


def test_is_cash_account_excludes_card(env):
    card = {"type": "credit"}
    assert cashflow_service.is_cash_account(card) is False
    # is_plan_source is IDENTICAL to is_on_budget — a card's own spending category still drains.
    assert cashflow_service.is_plan_source(card) is True


def test_line_of_credit_style_card_now_gets_a_payment_envelope(env):
    """Pre-ADR, a v1 `credit_card`-only rule meant a `line_of_credit` account never synced a
    payment envelope. Every `credit` account is now payment-tracked, so a revolving
    line-of-credit account (still just `type: credit`, its own name/purpose only) gets one too."""
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {
                "id": "loc",
                "name": "Revolving Line of Credit",
                "kind": "liability",
                "type": "credit",
                "currency": "SGD",
                "initial_balance": -500.0,
            },
        ]
    )
    planning.sync_card_payment_envelopes()
    assert svc.accounts.find_by_id("loc")["payment_envelope_id"] is not None


# ── payment_activity_breakdown: the YNAB two-column Activity-popover box ──────────


def test_payment_activity_breakdown_matches_ynab_reference(env):
    """The owner-provided YNAB reference scenario: a card opens with a pre-existing -$150
    balance, $20 is assigned to Groceries, a $20 spend hits the card (fully covered), and a
    $150 payment is made. Reconciles both columns of the popover box; `total_activity` must
    equal the row's own Activity in `budget_month` — one money path, two read-only views."""
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -150.0},
        ]
    )
    card_env_id = _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 20}
    )
    env.set_txns(
        [
            _expense("2026-06-09", "cat_13095ba70000", 20, from_account_id="amex"),
            _transfer("2026-06-10", "bank", "amex", 150),
        ]
    )

    breakdown = planning.payment_activity_breakdown(card_env_id, "2026-06")
    assert breakdown == {
        "spending": -20.0,
        "returns": 0.0,
        "total_spending": -20.0,
        "funded_spending": 20.0,
        "payments_returns": -150.0,
        "total_activity": -130.0,
    }

    row = _row_by_env(planning.budget_month("2026-06"), card_env_id)
    assert row["activity"] == breakdown["total_activity"]


def test_payment_activity_breakdown_none_for_ordinary_envelope(env):
    grocery_env = _env("cat_13095ba70000")
    assert planning.payment_activity_breakdown(grocery_env, "2026-06") is None


def test_payment_activity_breakdown_none_under_signed_mode(env):
    """ "signed" is the default — card_pay_env_of is empty, so even a card account with no
    payment envelope FK at all resolves to None, never a KeyError/crash."""
    assert planning.payment_activity_breakdown("amex", "2026-06") is None


def test_payment_activity_breakdown_counts_a_card_refund_as_returns(env):
    """A merchant refund credited straight to the card (kind=income, to_account_id=card,
    the same convention `card_statements.py` already uses) offsets `spending` in `returns`
    but is invisible to the envelope auto-move fold (income routed at the card, not a
    category) — so it moves `total_spending` without moving `total_activity`."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 20}
    )
    env.set_txns(
        [
            _expense("2026-06-09", "cat_13095ba70000", 20, from_account_id="amex"),
            {
                "kind": "income",
                "date": "2026-06-11",
                "category": None,
                "to_account_id": "amex",
                "to_amount": 5,
                "to_currency": "SGD",
                "base_amount": 5,
            },
        ]
    )
    breakdown = planning.payment_activity_breakdown(card_env_id, "2026-06")
    assert breakdown["spending"] == -20.0
    assert breakdown["returns"] == 5.0
    assert breakdown["total_spending"] == -15.0
    assert breakdown["funded_spending"] == 20.0  # unaffected — the refund never touched an envelope
    assert breakdown["total_activity"] == 20.0


# ── activity_transactions: card_leg_amount/txn_type + embedded breakdown ──────────


def test_activity_transactions_card_leg_amount_and_breakdown_for_payment_envelope(env):
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    grocery_env = _env("cat_13095ba70000")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": grocery_env, "amount": 20}
    )
    env.set_txns(
        [
            {
                **_expense("2026-06-09", "cat_13095ba70000", 20, from_account_id="amex"),
                "id": "spend1",
            },
            {**_transfer("2026-06-10", "bank", "amex", 150), "id": "pay1"},
        ]
    )

    detail = planning.activity_transactions([card_env_id], "2026-06")
    by_id = {r["id"]: r for r in detail["transactions"]}
    assert by_id["spend1"]["card_leg_amount"] == -20.0  # YNAB sign: the card's own drain
    assert by_id["spend1"]["txn_type"] == "spend"
    assert by_id["spend1"]["activity_amount"] == -20.0  # card-centric: mirrors the card leg
    assert by_id["pay1"]["card_leg_amount"] == 150.0  # YNAB sign: money arriving at the card
    assert by_id["pay1"]["txn_type"] == "payment"
    assert by_id["pay1"]["activity_amount"] == 150.0  # card-centric: mirrors the card leg
    assert detail["breakdown"]["total_activity"] == -130.0


def test_payment_envelope_activity_lists_uncovered_card_spends(env):
    """The fix behind the owner's screenshot (2026-07-10): a card spend with NO envelope coverage
    (Funded Spending $0) must STILL appear in the payment-envelope Activity popover, alongside its
    merchant payee — the popover is CARD-CENTRIC (every transaction ON the card), not scoped to the
    envelope's own contributions. The old envelope-scoped list omitted the uncovered spend entirely
    (it only showed the payment), which is exactly what the owner reported."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    # nothing assigned → the $50 spend is fully UNCOVERED (Funded Spending $0)
    env.set_txns(
        [
            {
                **_expense("2026-06-09", "cat_13095ba70000", 50, from_account_id="amex"),
                "id": "spend1",
                "payee_id": "pay_merchant",
            },
            {**_transfer("2026-06-10", "bank", "amex", 100), "id": "pay1"},
        ]
    )
    detail = planning.activity_transactions([card_env_id], "2026-06")
    by_id = {r["id"]: r for r in detail["transactions"]}
    assert set(by_id) == {
        "spend1",
        "pay1",
    }  # the UNCOVERED spend is listed (old behavior dropped it)
    assert by_id["spend1"]["card_leg_amount"] == -50.0
    assert by_id["spend1"]["payee_id"] == "pay_merchant"  # its merchant payee comes along
    assert by_id["pay1"]["card_leg_amount"] == 100.0
    assert detail["breakdown"]["funded_spending"] == 0.0  # nothing was covered


def test_activity_transactions_ordinary_category_unchanged(env):
    """An ordinary (non-payment) envelope fetch stays byte-identical — no `breakdown` key, no
    `card_leg_amount`/`txn_type` on its rows."""
    env.set_txns([{**_expense("2026-06-09", "cat_13095ba70000", 20), "id": "t1"}])
    detail = planning.activity_transactions(["cat_13095ba70000"], "2026-06")
    assert "breakdown" not in detail
    row = detail["transactions"][0]
    assert "card_leg_amount" not in row
    assert "txn_type" not in row


# ── A card created through the running app, not at boot, still gets synced ───────────────


def test_creating_a_credit_account_syncs_its_payment_envelope_immediately(env):
    """`sync_card_payment_envelopes()` was previously wired ONLY into `main.py`'s boot
    sequence — a card added through the running app (the UI, an import, an AI tool call) got
    no payment envelope until the next restart, so it synced 0 in the Credit Card Payments
    group and its spend had nowhere to look "underfunded" against (owner-reported: a fresh
    Citi Rewards card added via the app showed `count: 0` there). `services.accounts.
    create_account` now calls the sync itself for any account it creates with `type="credit"`
    — idempotent, so this is safe on every call, not just the first. This test creates the
    account through THAT service choke (not the fixture's `env.set_accounts`, which writes
    rows directly and would not exercise the fix) and asserts the payment envelope exists with
    no separate sync call in between."""
    from backend.services import accounts as accounts_service

    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts([{**_DEFAULT_ACCOUNTS[0]}])  # just the bank; no card yet

    created = accounts_service.create_account(
        {
            "id": "citi",
            "name": "Citi Rewards",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -50.0,
        }
    )
    env_id = created.get("payment_envelope_id")
    assert env_id, "create_account did not sync a payment envelope for the new card"

    envelope = envelopes_service.envelope_store.find_by_id(env_id)
    assert envelope is not None
    assert envelope.get("group_id") is not None
    table = planning.budget_month("2026-06")
    card_row = next((r for r in table["groups"] if r["name"] == "Credit Card Payments"), None)
    assert card_row is not None
    assert any(c["envelope_id"] == env_id for c in card_row["categories"])


# ── The migration script: dry-run, apply, net-worth-neutral, idempotent ───────────


def test_categorized_card_refund_drains_the_payment_envelope_like_ynab(env):
    """YNAB parity (2026-09-28): a refund landing on the card under a spending category gives the
    money back to that category AND takes it out of the card's payment envelope — the debt it
    was set aside for shrank. 400 assigned, 100 cash spend, 350 card spend, a 20 card refund:
    YNAB shows the category −30 and the payment envelope 300; ONW used to keep 320 of coverage
    there, holding the refund twice and pushing Ready to Assign 20 low."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    key = "cat_13095ba70000"
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env(key), "amount": 400}
    )
    spends = [
        _expense("2026-06-05", key, 100),
        _expense("2026-06-06", key, 350, from_account_id="amex"),
    ]
    refund = {
        "kind": "income",
        "date": "2026-06-13",
        "category": key,
        "to_account_id": "amex",
        "to_amount": 20,
        "to_currency": "SGD",
        "base_amount": 20,
    }
    env.set_txns(spends)
    rta_before = planning.budget_month("2026-06")["ready_to_assign"]
    env.set_txns([*spends, refund])
    table = planning.budget_month("2026-06")
    assert _cat(table, key)["available"] == -30
    assert _row_by_env(table, card_env_id)["available"] == 300
    assert table["card_spend_not_covered"] == 30
    assert table["ready_to_assign"] == rta_before  # a card refund brings no cash


def test_ynab_parity_month_and_rollover_with_pre_budget_card_debt(env):
    """The shapes a real book compared side by side with YNAB on 2026-09-28 (every figure matched
    to the cent), rebuilt with invented numbers so a future change can't silently break them:
    a budget that starts with debt already on the card, card + cash spending in one category,
    a card refund, a category overspent on the card, and a card paid past what was set aside.

    June: assign Food 100, Fun 50. Card: Food 80, Fun 70; cash: Food 40; card refund Food 10;
    pay the card 200 (it owed 300 before the budget began).
      Food  = 100 − 80 − 40 + 10 = −10 (card-overspent: the cash counts first)
      Fun   = 50 − 70            = −20 (card-overspent)
      card payment row = covered 60+10 (Food) + 50 (Fun) − refund 10 − payment 200 = −90
      RTA   = 1000 − 150 assigned = 850 (a card overspend and the card's old debt cost nothing)
    July: the three negatives reset to 0; only the payment row's −90 (real cash, never set
    aside) is charged, so RTA = 850 − 90 = 760 = cash. The card-overspent 30 is card debt."""
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -300.0},
        ]
    )
    card_env_id = _sync_and_card_env_id()
    food, fun = "cat_13095ba70000", "cat_db5642ab0000"
    for key, amount in ((food, 100), (fun, 50)):
        planning.apply_budget_change(
            {"type": "assign", "month": "2026-06", "envelope_id": _env(key), "amount": amount}
        )
    env.set_txns(
        [
            _expense("2026-06-02", food, 80, from_account_id="amex"),
            _expense("2026-06-03", fun, 70, from_account_id="amex"),
            _expense("2026-06-10", food, 40),
            {
                "kind": "income",
                "date": "2026-06-12",
                "category": food,
                "to_account_id": "amex",
                "to_amount": 10,
                "to_currency": "SGD",
                "base_amount": 10,
            },
            _transfer("2026-06-20", "bank", "amex", 200),
        ]
    )
    june = planning.budget_month("2026-06")
    assert _cat(june, food)["available"] == -10
    assert _cat(june, fun)["available"] == -20
    pay = _row_by_env(june, card_env_id)
    assert pay["available"] == -90
    assert pay["owed"] == 240  # 300 + 80 + 70 − 10 − 200
    assert june["card_spend_not_covered"] == 30
    assert june["ready_to_assign"] == 850

    july = planning.budget_month("2026-07")
    assert _cat(july, food)["available"] == 0
    assert _cat(july, fun)["available"] == 0
    assert _row_by_env(july, card_env_id)["available"] == 0
    assert july["ready_to_assign"] == 760


def _card_book_with_pre_budget_debt(env):
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0]},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -300.0},
        ]
    )
    return _sync_and_card_env_id()


def _target(key, amount):
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env(key), "target_type": "monthly", "amount": amount}
    )


def test_underfunded_auto_assign_funds_targets_then_card_debt_like_ynab(env):
    """YNAB's Auto-Assign → Underfunded proposed exactly the targets plus every card's payment
    shortfall (side by side, 2026-09-28). Fun's target 50 and the card's 300 of debt from before
    the budget began: 350, cards last, and the apply lands exactly the preview."""
    card_env_id = _card_book_with_pre_budget_debt(env)
    fun = "cat_db5642ab0000"
    _target(fun, 50)
    before = planning.budget_month("2026-06")["ready_to_assign"]

    preview = planning.auto_assign_preview("2026-06", "underfunded")
    assert [(s["envelope_id"], s["delta"]) for s in preview["steps"]] == [
        (_env(fun), 50.0),
        (card_env_id, 300.0),
    ]
    planning.apply_budget_change({"type": "auto_assign", "month": "2026-06", "mode": "underfunded"})
    june = planning.budget_month("2026-06")
    pay = _row_by_env(june, card_env_id)
    assert pay["available"] == 300 and pay["underfunded"] == 0
    assert june["ready_to_assign"] == round(before - 350, 2)


def test_underfunded_proposes_no_card_step_when_it_funds_a_card_overspent_row(env):
    """Funding a row overspent on the card moves coverage into the card's payment envelope, so a
    card figure computed beforehand would double-fund it (the 2026-09-08 HIGH). That preview
    leaves the cards out, and its apply still equals it."""
    card_env_id = _card_book_with_pre_budget_debt(env)
    food = "cat_13095ba70000"
    _target(food, 100)
    env.set_txns([_expense("2026-06-02", food, 130, from_account_id="amex")])

    preview = planning.auto_assign_preview("2026-06", "underfunded")
    assert card_env_id not in {s["envelope_id"] for s in preview["steps"]}
    planning.apply_budget_change({"type": "auto_assign", "month": "2026-06", "mode": "underfunded"})
    june = planning.budget_month("2026-06")
    assert _cat(june, food)["assigned"] == preview["steps"][0]["new_assigned"]
    assert _row_by_env(june, card_env_id)["available"] <= _row_by_env(june, card_env_id)["owed"]


def test_average_modes_count_only_months_since_the_budget_began(env):
    """YNAB averaged a one-month-old budget's assignments as that month's figure (2026-09-28);
    ONW divided by 12, counting every month before the budget's start as zero."""
    food = "cat_13095ba70000"
    for month, amount in (("2026-06", 100), ("2026-07", 200)):
        planning.apply_budget_change(
            {"type": "assign", "month": month, "envelope_id": _env(food), "amount": amount}
        )
    july = planning.auto_assign_preview("2026-07", "average_assigned")["steps"]
    august = planning.auto_assign_preview("2026-08", "average_assigned")["steps"]
    assert {s["envelope_id"]: s["new_assigned"] for s in august}[_env(food)] == 150.0
    assert {s["envelope_id"]: s["new_assigned"] for s in july}[_env(food)] == 100.0
    assert planning.auto_assign_preview("2026-06", "average_assigned")["steps"] == []
