"""
The RTA identity, as a PROPERTY over randomly generated books (ADR
2026-09-09-ready-to-assign-follows-ynab).

The identity must CLOSE EXACTLY on EVERY month of EVERY book, whatever the mix of assignments,
overspends, uncategorized spending, off-budget transfers, card spending and card payments:

    ready_to_assign == on_budget_cash + card_credit − pre_budget_baseline − held_in_envelopes
                                                    − held_outside_envelopes
                                                    − card_spend_not_covered

`card_credit` (ADR 2026-11-02-a-card-in-credit-is-budget-money) is every card's balance above
zero: a card in credit holds budget money, so a spend paid from that credit holds in its bucket
like cash spend, and a refund landing past zero is new money like a cash refund.

`held_in_envelopes` is SIGNED (Σ available over the envelope rows, a current-month overspend
counted negative), so `RTA ≤ cash − baseline` is deliberately NOT asserted here: ADR 2026-09-09
retired that bound. An envelope HOLDS its own overspend until month-end, which can genuinely put
Ready to Assign above cash on hand until the clamped carry resets the hole at the roll. What
replaces the bound is this closure — the popover's lines account for every dollar of on-budget
cash, with no residual term to explain away.

`held_outside_envelopes` (ADR 2026-09-10) is the no-bucket term — uncategorized CASH spend and
CASH spend on a category linked to no envelope, each holding its own hole under that same
clamped carry. It is ≤ 0 by construction, so it too can push RTA above cash.

THE CLOSURE ABOVE CANNOT POLICE THAT TERM, and an earlier version of this file claimed it could
("a regression that let card spend into the no-bucket hold would break the closure"). It cannot:
`_ready_to_assign` DEFINES RTA as a function of `held_outside_envelopes`, so the identity closes
tautologically no matter which dollars were misclassified into it. The 2026-09-10 security
review proved exactly that — three shapes where a CREDIT-account leg was folded into the hold,
inflating RTA by the full amount, every one of them closing this identity to the cent.

So a third property runs beside the closure, and it is deliberately NON-CIRCULAR:
`_no_bucket_holds_from_the_raw_book` re-derives the term straight off the generated
transaction rows — a second implementation that shares no code with the engine — gating on
`type == "cash"` rather than on anything the engine computed. It is asserted per month AND as a
month-over-month movement, and the accounts it touched are asserted to be a subset of
`ctx["cash_ids"]`, the exact set `_cash_as_of` sums: a dollar the hold reserves must be a dollar
the cash side counted. The generator reaches the shapes that broke this — an ARCHIVED credit
card (whose payment envelope is retired, so its spend takes the cash fold) and a card-funded
categorized transfer to an off-budget account (the card's own outflow, which the transfer branch
does not recognise as a card movement) — alongside the plain cash ones.

The 2026-09-10 review round added two more, both under a label the generator ALSO spends real
cash on in the same month, since both bugs were invisible until the two collided:

  * a categorized transfer landing AT the archived card (`transfer_into_archived_card`). It
    folds POSITIVE into the FULL activity map and nothing into the cash one, so a same-label
    cash expense could be cancelled to ~0 on the full fold alone — and `_unbudgeted_keys`, which
    filtered on that fold, dropped the key, its hold and its display row together.
  * an INFLOW on the unlinked category (`unlinked_refund`). The engine skipped it entirely, so a
    refund could not cancel the hold its own spend opened; the raw walk below skipped it for the
    same reason and would have gone on agreeing with the bug forever. Both now fold it — into
    the no-bucket accumulator ONLY, never into Activity.

The differential-fuzz round that followed added two more, and they are about the AMOUNT rather
than the leg set — the first shapes here where the engine and this file's own oracle could
disagree on a leg BOTH of them agreed was cash:

  * a CROSS-CURRENCY stamped transfer (`xccy_stamped_transfer`), settled at the bank's rate
    rather than the book's. `ledger._transfer_base_value` stamps `base_amount` from whichever
    leg was already in base currency, which here is always the OFF-BUDGET one — so the stamp is
    neither the moving leg nor today's rate, while `_cash_as_of` is both.
  * an UNEQUAL-LEG transfer (`fee_transfer`): same currency both sides, less arriving than left,
    a wire fee eaten in transit. The stamp takes the FROM leg — again the off-budget one — so
    the arrival "cancelled" more cash hole than actually arrived.

Every transfer this generator minted before was either UNSTAMPED or stamped from the very leg
that moved, so the stamp and the cash figure could not disagree — which is exactly why four
prior review rounds of this property found nothing. Both are minted under `_UNLINKED`, the label
the generator also spends real cash on, and both are counted only when the gap is material.

Both forms are checked: the engine's own base figure, and the WIRE figure (which subtracts the
`_display_rta` clamp for money assigned to later months — a display rule orthogonal to the
identity, so the payload closes only with that term included).

A second property runs beside it, on the same books: the CARRY recurrence, month over month,
per envelope row, read entirely off the wire —

    available(m+1, env) == max(0, available(m, env)) + assigned(m+1, env) + activity(m+1, env)

The closed identity alone cannot see this: it is asserted per month, so an engine that carried
a hole forward as debt (the 2026-09-08 rule) would still close every month it was checked on.
The `max(0, ·)` is exactly the half the restoration turns on — an envelope holds its overspend
in-month and RESETS at the roll — and the counters below prove the generated books actually
reach a red row that then resets, on a card-linked book, rather than passing the recurrence
vacuously on rows that were never negative.

Seeded `random` (no Hypothesis dependency), so a failure is reproducible from the seed alone.
Reuses tests/test_rta_balance_identity.py's isolated `env` fixture and row helpers.
"""

from __future__ import annotations

import random

from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning
from tests.test_rta_balance_identity import _BASE_ACCOUNTS, _FX, _expense, _income, _transfer
from tests.test_rta_balance_identity import env as _isolated_env

# Re-export that file's isolated planning fixture under its own name: pytest resolves a fixture
# by the module-level name it is bound to, and a bare `import env` reads to ruff as an import
# shadowed by each test's `env` parameter (F811).
env = _isolated_env

_SEED = 20260908
_BOOKS = 200
_MONTHS = ("2026-06", "2026-07", "2026-08")
# Four builtin expense categories, each 1:1 with a fixture-minted envelope (food, transport,
# shopping, entertainment) + the two shapes that have NO envelope to absorb them.
_CATS = ("cat_13095ba70000", "cat_db5642ab0000", "cat_6c4bc0170000", "cat_dfcf9f910000")
_UNSORTED = ""  # blank category — the "Uncategorized" bucket
_UNLINKED = "salary"  # a labeled category with no envelope — the "Unbudgeted" bucket

_USD_ACCOUNT = {
    "id": "bank_usd",
    "name": "Bank USD",
    "kind": "asset",
    "type": "cash",
    "currency": "USD",
    "initial_balance": 0.0,
}
# A closed card. `sync_card_payment_envelopes` retires an archived card's payment envelope and
# nulls its FK, so its spend stops taking the card branch — the exact shape that used to fall
# through into the no-bucket hold and fabricate Ready to Assign.
_ARCHIVED_CARD = {
    "id": "cc_old",
    "name": "Closed Amex",
    "kind": "liability",
    "type": "credit",
    "currency": "SGD",
    "archived": True,
    "initial_balance": 0.0,
}

# The account universe, split the way the ENGINE splits it — restated here as literals so the
# re-derivation below shares nothing with `_build_context`.
_CASH_IDS = frozenset({"bank", "bank_usd"})
_CARD_IDS = frozenset({"cc", "cc_old"})
_ON_BUDGET_IDS = frozenset({"bank", "bank_usd", "cc", "cc_old"})


def _prev(month: str) -> str:
    """The calendar month before ``month`` — the pairing key for the carry recurrence."""
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _amt(rng, lo, hi):
    return round(rng.uniform(lo, hi), 2)


def _day(rng, month):
    return f"{month}-{rng.randint(3, 27):02d}"


def _adjustment(date, account, amount):
    """A dateable balance correction with NO category — the shape a standing card debt (or a
    pre-existing cash balance) takes: it moves the account, never an envelope."""
    row = {"kind": "adjustment", "date": date, "category": None, "description": "adj"}
    if amount >= 0:
        row.update({"to_account_id": account, "to_amount": amount, "to_currency": "SGD"})
    else:
        row.update({"from_account_id": account, "from_amount": -amount, "from_currency": "SGD"})
    return row


def _unlinked_full_net(txns, month) -> float:
    """The FULL (not cash-only) activity the engine will fold onto `_UNLINKED` for `month`, over
    the rows generated SO FAR. Generator-side arithmetic only — its single job is to SIZE the
    cancelling transfer below, so the shape it targets is actually hit instead of relying on two
    random amounts colliding to the cent (they never do). Mirrors the three ways a generated row
    reaches that key: an expense drains it, a categorized transfer folds its ON-BUDGET leg
    (negative leaving, positive arriving), and an inflow reaches the cash fold only — so it is
    deliberately absent here."""
    net = 0.0
    for t in txns:
        if not (t.get("date") or "").startswith(month) or t.get("category") != _UNLINKED:
            continue
        if t["kind"] == "expense":
            net -= _to_base(t["from_amount"], t["from_currency"])
        elif t["kind"] == "transfer":
            # A STAMPED transfer's envelope figure is locked to its stamp (the historical-rate
            # lock), whichever leg that came from; only an unstamped row falls back to the
            # on-budget leg at today's rate. The CASH figure is always the moving leg — that
            # split is precisely what `_no_bucket_holds_from_the_raw_book` polices, so this
            # generator-side helper must mirror the ENVELOPE half or it sizes the cancelling
            # transfer against the wrong number.
            stamp = t.get("base_amount")
            arriving = t["to_account_id"] in _ON_BUDGET_IDS
            leg = "to" if arriving else "from"
            moved = abs(float(stamp)) if stamp is not None else _to_base(*_leg(t, leg))
            net += moved if arriving else -moved
    return round(net, 2)


def _leg(t, side):
    """``(native_amount, currency)`` for one side of a row — the pair `_to_base` takes."""
    return t.get(f"{side}_amount"), t.get(f"{side}_currency")


def _random_book(rng, env_ids):
    """One small book: 2 cash accounts, 1 credit card, 4 envelopes, 3 months of random
    assignments, spends, transfers, card spends and card payments."""
    txns = [_adjustment("2026-06-01", "bank", _amt(rng, 0, 4000))]
    if rng.random() < 0.4:  # standing card debt, carried in from before the book starts
        txns.append(_adjustment("2026-06-01", "cc", -_amt(rng, 50, 2000)))
    assigns: dict[str, dict[str, float]] = {}
    for month in _MONTHS:
        if rng.random() < 0.9:
            txns.append(_income(f"{month}-01", _amt(rng, 200, 3000)))
        if rng.random() < 0.3:
            txns.append(_income(f"{month}-02", _amt(rng, 50, 500), to="bank_usd", currency="USD"))
        month_assigns = {}
        for env_id in env_ids:
            if rng.random() < 0.55:
                month_assigns[env_id] = _amt(rng, 0, 800)
        if month_assigns:
            assigns[month] = month_assigns
        for _ in range(rng.randint(0, 6)):
            op = rng.choice(
                [
                    "cash_spend",
                    "cash_spend",
                    "unsorted_spend",
                    "unlinked_spend",
                    "card_spend",
                    "card_spend",
                    "card_payment",
                    "archived_card_spend",
                    "card_funded_transfer",
                    "transfer_into_archived_card",
                    "unlinked_refund",
                    "off_budget_transfer",
                    "on_budget_transfer",
                    "xccy_stamped_transfer",
                    "fee_transfer",
                    "refund",
                ]
            )
            day = _day(rng, month)
            cat = rng.choice(_CATS)
            if op == "cash_spend":
                account = "bank" if rng.random() < 0.8 else "bank_usd"
                ccy = "SGD" if account == "bank" else "USD"
                txns.append(_expense(day, cat, _amt(rng, 5, 600), frm=account, currency=ccy))
            elif op == "unsorted_spend":
                txns.append(_expense(day, _UNSORTED, _amt(rng, 5, 300)))
            elif op == "unlinked_spend":
                txns.append(_expense(day, _UNLINKED, _amt(rng, 5, 300)))
            elif op == "card_spend":
                spend_cat = cat if rng.random() < 0.75 else _UNSORTED
                txns.append(_expense(day, spend_cat, _amt(rng, 5, 700), frm="cc"))
            elif op == "card_payment":
                txns.append(_transfer(day, _amt(rng, 20, 900), "bank", "cc"))
            elif op == "archived_card_spend":
                # A swipe on a CLOSED card: still a `credit` account, still on-budget, but with
                # no payment envelope left to auto-move into. It moves no cash, so it must hold
                # nothing — restricted to the no-bucket categories, which is the half this file
                # can police (a LINKED category on a retired card is a separate, pre-existing
                # question about the coverage fold, not about this term).
                txns.append(
                    _expense(
                        day, rng.choice([_UNSORTED, _UNLINKED]), _amt(rng, 5, 300), frm="cc_old"
                    )
                )
            elif op == "card_funded_transfer":
                # The card's OWN outflow to an off-budget account, labeled. The transfer branch
                # only recognises money arriving AT a card, so this falls into the categorized-
                # transfer fold — where it must still be seen as a CARD movement, not cash.
                row = _transfer(day, _amt(rng, 20, 600), "cc", "vault")
                row["category"] = _UNLINKED
                txns.append(row)
            elif op == "transfer_into_archived_card":
                # Money arriving AT the closed card from off-budget, under the SAME label the
                # `unlinked_spend` op spends cash on. The XOR rule folds the on-budget (card)
                # leg POSITIVE, so the FULL map can net a real cash hole to ~0 while the cash
                # map still holds every cent of it.
                row = _transfer(day, _amt(rng, 20, 400), "vault", "cc_old")
                row["category"] = _UNLINKED
                txns.append(row)
            elif op == "unlinked_refund":
                # An inflow on the unlinked category: it must CANCEL that key's own hold (it
                # really did put cash back) without ever becoming envelope Activity.
                txns.append(
                    {
                        "kind": "income",
                        "date": day,
                        "category": _UNLINKED,
                        "to_account_id": "bank" if rng.random() < 0.8 else "cc",
                        "to_amount": _amt(rng, 5, 300),
                        "to_currency": "SGD",
                    }
                )
            elif op == "off_budget_transfer":
                row = _transfer(day, _amt(rng, 20, 900), "bank", "vault")
                if rng.random() < 0.5:
                    row["category"] = rng.choice([cat, _UNLINKED])
                txns.append(row)
            elif op == "xccy_stamped_transfer":
                # A CROSS-CURRENCY transfer between the USD cash account and off-budget, settled
                # at the BANK's rate rather than the book's, and STAMPED the way
                # `ledger._transfer_base_value` stamps one: from whichever leg was already in
                # base currency — which here is always the OFF-BUDGET SGD side, never the moving
                # on-budget USD one. Folding that stamp into the cash accumulator reserved a
                # number `_cash_as_of` never saw. The jitter is bounded AWAY from 1.0 so the
                # divergence is material on every row, not just on the lucky ones.
                usd = _amt(rng, 20, 400)
                sgd = round(
                    usd * _FX["USD"] * (1 + rng.choice((-1, 1)) * rng.uniform(0.008, 0.04)), 2
                )
                row = (
                    _transfer(
                        day,
                        usd,
                        "bank_usd",
                        "vault",
                        currency="USD",
                        to_amount=sgd,
                        to_currency="SGD",
                    )
                    if rng.random() < 0.5
                    else _transfer(day, sgd, "vault", "bank_usd", to_amount=usd, to_currency="USD")
                )
                row.update({"category": _UNLINKED, "base_amount": sgd, "base_currency": "SGD"})
                txns.append(row)
            elif op == "fee_transfer":
                # No FX at all: S$X leaves an OFF-BUDGET account and LESS arrives on-budget, a
                # wire fee eaten in transit. Both legs are in base, so the stamp takes the FROM
                # leg — the off-budget one — while only what ARRIVED is cash.
                sent = _amt(rng, 50, 600)
                row = _transfer(
                    day, sent, "vault", "bank", to_amount=round(sent - _amt(rng, 1, 25), 2)
                )
                row.update({"category": _UNLINKED, "base_amount": sent, "base_currency": "SGD"})
                txns.append(row)
            elif op == "on_budget_transfer":
                sgd = _amt(rng, 20, 500)
                txns.append(
                    _transfer(
                        day,
                        sgd,
                        "bank",
                        "bank_usd",
                        to_amount=round(sgd / 1.35, 2),
                        to_currency="USD",
                    )
                )
            else:  # refund into an envelope
                txns.append(
                    {
                        "kind": "income",
                        "date": day,
                        "category": cat,
                        "to_account_id": "bank",
                        "to_amount": _amt(rng, 5, 200),
                        "to_currency": "SGD",
                    }
                )
        # THE EXACT-CANCELLATION SHAPE (review, 2026-09-10). Everything above is independently
        # random, so the unlinked key's FULL fold lands on 0.00 essentially never — and 0.00 is
        # precisely where `_unbudgeted_keys`' old filter dropped the key, its hold and its
        # display row together. Land a categorized transfer AT the archived card for exactly the
        # month's outstanding unlinked net and the full fold goes to zero to the cent, while the
        # cash fold keeps every cent that really left a cash account. WHICH month, HOW much and
        # WHAT else is in it all stay fuzzed; only the collision is made certain.
        residual = _unlinked_full_net(txns, month)
        if residual < -0.005 and rng.random() < 0.5:
            cancel = _transfer(_day(rng, month), round(-residual, 2), "vault", "cc_old")
            cancel["category"] = _UNLINKED
            txns.append(cancel)
    baseline = _amt(rng, 0, 1500) if rng.random() < 0.25 else 0.0
    return txns, assigns, baseline


def _to_base(amount, currency) -> float:
    """SGD value of `amount`, off the FIXTURE's rate table (which is what
    `planning.load_fx_cache` is monkeypatched to return) — never `svc.to_base`, so the
    re-derivation below cannot inherit a conversion bug from the code it is checking."""
    return round(float(amount or 0) * _FX[(currency or "SGD").upper()], 2)


def _no_bucket_holds_from_the_raw_book(txns, month, linked):
    """`held_outside_envelopes(month)`, re-derived from the RAW transaction rows.

    A deliberately independent second implementation: it walks the stored book, decides
    "is this money?" from the account id alone (`_CASH_IDS` — literally `type == "cash"`, the
    same set `_cash_as_of` sums — and, for a card, only the part of a leg that moves its balance
    above zero), clamps each key at ≤ 0, and sums. It calls nothing in `planning`, so it cannot
    agree with the engine by construction — which is the whole point, since the identity closure
    asserts a formula against its own inputs and would pass on every misclassification.

    `txns` are the STORED rows (ids and `created_at` included): a card's balance is followed in
    the engine's own (date, created_at, id) order, since how much of a card leg is credit depends
    on what came before it.

    Returns `(held, accounts_touched)`. Three shapes reach a no-bucket key:

      * an EXPENSE whose category has no envelope — the moving leg is `from_account_id`;
      * a categorized TRANSFER with exactly ONE on-budget leg — the moving leg is whichever
        side is on-budget. Both-on-budget nets to zero (a card payment lands here, and is
        handled by the engine's payment branch instead); neither-on-budget is not plan money.
      * an INCOME on a LABELED category with no envelope — the moving leg is `to_account_id`.
        It is inflow-to-assign as far as ENVELOPE Activity goes (no envelope exists to be a
        refund into), but it genuinely put cash back on the key its own spend took it from, so
        it cancels that key's hold. This clause was missing here for as long as it was missing
        from the engine, and the two agreed with each other on the wrong answer (review,
        2026-09-10) — a second implementation only proves something when it is written from the
        rule rather than from the code.

    On a CARD (ADR 2026-11-02-a-card-in-credit-is-budget-money) only money counts: a spend holds
    the part paid from credit, `min(amount, max(0, balance))`, and a refund cancels the part that
    lands past zero, `amount − min(amount, owed)`. The rest is debt, which is never RTA's. Money
    arriving at a card by a categorized TRANSFER holds nothing (the engine treats it as Activity,
    not as a refund).

    Nothing else can: income on a BLANK category is inflow-to-assign proper (`""` is in the
    engine's `_INFLOW_CATEGORIES`, alongside `"ready_to_assign"`) and folds nowhere at all —
    netting salary into the Uncategorized bucket would wipe that bucket's hold for every book that
    has income; the generator mints no `investment` rows; and an `adjustment` carries no
    category. The generator also asserts an empty `cutoff_by_id`, so tracking-start anchors are
    provably not in play for this walk."""
    by_key: dict[str, float] = {}
    touched: set[str] = set()
    card_balance = dict.fromkeys(_CARD_IDS, 0.0)
    rows = sorted(
        (t for t in txns if not t.get("deleted") and (t.get("date") or "")[:7] <= month),
        key=lambda t: (t.get("date") or "", t.get("created_at") or "", t.get("id") or ""),
    )
    for t in rows:
        frm, to = t.get("from_account_id"), t.get("to_account_id")
        in_month = (t.get("date") or "").startswith(month)
        cat = t.get("category") or ""
        if in_month and cat not in linked:
            for moving, delta in _no_bucket_legs(t, cat):
                if moving in _CASH_IDS:
                    pass
                elif moving in _CARD_IDS and delta < 0:
                    delta = -min(-delta, max(0.0, card_balance[moving]))
                elif moving in _CARD_IDS and t.get("kind") == "income":
                    delta = delta - min(delta, max(0.0, -card_balance[moving]))
                else:
                    continue  # debt, or a transfer into a card — nothing to hold
                if abs(delta) > 0.005:
                    touched.add(moving)
                by_key[cat] = by_key.get(cat, 0.0) + delta
        if to in _CARD_IDS:
            card_balance[to] += _to_base(*_leg(t, "to"))
        if frm in _CARD_IDS:
            card_balance[frm] -= _to_base(*_leg(t, "from"))
    return round(sum(min(0.0, round(v, 2)) for v in by_key.values()), 2), touched


def _no_bucket_legs(t, cat):
    """``[(moving_account, signed_base_amount)]`` for one row that can reach a no-bucket key."""
    if t.get("kind") == "expense":
        return [(t.get("from_account_id"), -_to_base(t.get("from_amount"), t.get("from_currency")))]
    if t.get("kind") == "income" and cat:
        return [(t.get("to_account_id"), _to_base(t.get("to_amount"), t.get("to_currency")))]
    if t.get("kind") == "transfer" and cat:
        frm, to = t.get("from_account_id"), t.get("to_account_id")
        from_ok, to_ok = frm in _ON_BUDGET_IDS, to in _ON_BUDGET_IDS
        if from_ok == to_ok:
            return []
        if from_ok:
            return [(frm, -_to_base(t.get("from_amount"), t.get("from_currency")))]
        return [(to, _to_base(t.get("to_amount"), t.get("to_currency")))]
    return []


def test_the_identity_closes_exactly_on_every_generated_book(env):
    rng = random.Random(_SEED)
    env.set_accounts([dict(a) for a in _BASE_ACCOUNTS] + [dict(_USD_ACCOUNT), dict(_ARCHIVED_CARD)])
    env_ids = [envelopes_service.env_of()[c] for c in _CATS]
    checked = 0
    seen = {
        "held": 0,
        "net_overspent": 0,
        "card_uncovered": 0,
        "in_month_hole": 0,
        "held_outside": 0,
        "baseline": 0,
    }
    carry = {"pairs": 0, "reset_from_red": 0, "carried_positive": 0}
    # The shapes the no-bucket hold has to get right that a cash-only book never reaches,
    # counted so the re-derivation below is provably exercised against them. The first two are
    # CARD legs it must IGNORE; `transfer_into_archived_card` and `unlinked_refund` are the
    # 2026-09-10 pair, each of which only bites when it shares a month and a label with real
    # cash spending — so `unlinked_cash_spend` is counted beside them.
    card_shapes = {
        "archived_card_spend": 0,
        "card_funded_transfer": 0,
        "transfer_into_archived_card": 0,
        "unlinked_refund": 0,
        "unlinked_cash_spend": 0,
    }
    # Months where the unlinked key's FULL fold was cancelled to ~0 while its CASH fold still
    # held a real hole — the collision `_random_book` engineers. Counted per MONTH (not per row)
    # and only when a hole actually survived: without this the widened `_unbudgeted_keys` filter
    # would be under no test at all here.
    cancelled_but_still_holding = 0
    # The 2026-09-10 differential-fuzz pair: a transfer whose STAMPED base value is not the
    # moving leg's own current-rate value. Counted only when the gap is MATERIAL (> 1¢), so the
    # count is evidence the divergence was actually reachable rather than that the op fired.
    stamped_shapes = {"xccy_stamped_transfer": 0, "fee_transfer": 0}
    linked_cats = frozenset(envelopes_service.env_of())

    for book in range(_BOOKS):
        txns, assigns, baseline = _random_book(rng, env_ids)
        for t in txns:
            if t.get("kind") == "transfer" and t.get("base_amount") is not None:
                on_budget_leg = "to" if t["to_account_id"] in _ON_BUDGET_IDS else "from"
                gap = abs(float(t["base_amount"])) - _to_base(*_leg(t, on_budget_leg))
                if abs(gap) > 0.01:
                    shape = (
                        "xccy_stamped_transfer"
                        if t["from_currency"] != t["to_currency"]
                        else "fee_transfer"
                    )
                    stamped_shapes[shape] += 1
            if t.get("from_account_id") == "cc_old":
                card_shapes["archived_card_spend"] += 1
            elif t.get("kind") == "transfer" and t.get("to_account_id") == "cc_old":
                card_shapes["transfer_into_archived_card"] += 1
            elif (
                t.get("kind") == "transfer"
                and t.get("from_account_id") == "cc"
                and t.get("category")
            ):
                card_shapes["card_funded_transfer"] += 1
            elif t.get("kind") == "income" and t.get("category") == _UNLINKED:
                card_shapes["unlinked_refund"] += 1
            elif t.get("kind") == "expense" and t.get("category") == _UNLINKED:
                card_shapes["unlinked_cash_spend"] += 1
        env.set_profile(budget_config={"pre_budget_baseline": baseline})
        env.set_assignments(assigns)
        env.set_txns(txns)
        stored = svc.transactions.all()

        prev_rows: dict[str, dict] | None = None
        prev_month: str | None = None
        prev_raw: float | None = None
        prev_held_outside = 0.0
        for month in _MONTHS:
            table = planning.budget_month(month)
            ctx = planning._build_context(month)
            if month < ctx["floor"]:
                continue
            rows = {
                c["envelope_id"]: c
                for g in table["groups"]
                for c in g["categories"]
                if c.get("envelope_id")
            }
            # The CARRY recurrence, off the wire only — the clamped half of the restored
            # identity, which the per-month closure above cannot see. Skipped for a month AT or
            # BELOW the floor, where the walk starts from 0 by definition rather than from the
            # previous month.
            if prev_rows is not None and prev_month == _prev(month) and month > ctx["floor"]:
                for env_id, row in rows.items():
                    before = prev_rows.get(env_id)
                    if before is None:
                        continue
                    expected = round(
                        max(0.0, before["available"]) + row["assigned"] + row["activity"], 2
                    )
                    assert abs(row["available"] - expected) < 0.01, (
                        f"book={book} {prev_month}->{month} env={env_id} seed={_SEED}: "
                        f"available={row['available']} expected={expected} "
                        f"(prev={before['available']} assigned={row['assigned']} "
                        f"activity={row['activity']})"
                    )
                    carry["pairs"] += 1
                    carry["reset_from_red"] += before["available"] < -0.005
                    carry["carried_positive"] += before["available"] > 0.005
            prev_rows, prev_month = rows, month
            cash = table["on_budget_cash"] + table["card_credit"]
            held = table["held_in_envelopes"]
            held_outside = table["held_outside_envelopes"]
            card_uncovered = table["card_spend_not_covered"]
            rta = planning._ready_to_assign(ctx, month)
            where = f"book={book} month={month} seed={_SEED}"

            # 1. The engine's own figure closes the identity exactly.
            assert abs(rta - (cash - baseline - held - held_outside - card_uncovered)) < 0.01, (
                f"{where}: rta={rta} cash={cash} held={held} held_outside={held_outside} "
                f"card_uncovered={card_uncovered} baseline={baseline}"
            )
            # 2. So does the WIRE figure, once the display clamp for money already promised to a
            #    later month is included — every line of the popover is a payload key.
            assert (
                abs(
                    table["ready_to_assign"]
                    - (
                        cash
                        - table["pre_budget_baseline"]
                        - held
                        - held_outside
                        - card_uncovered
                        - table["assigned_in_future_months"]
                    )
                )
                < 0.01
            ), where
            # 3. The no-bucket term only ever HOLDS — it can never hoard a surplus, or an
            #    inflow labeled with an unlinked category would silently stop raising RTA.
            assert held_outside <= 0.005, f"{where}: held_outside={held_outside}"
            # 4. ...and it holds exactly what the RAW BOOK says left a CASH account with no
            #    bucket to land in. Independent of the engine (see the helper), so unlike the
            #    closure above this one actually fails when a credit-account leg sneaks in.
            assert not ctx["cutoff_by_id"], f"{where}: the raw walk assumes no tracking anchors"
            raw, touched = _no_bucket_holds_from_the_raw_book(stored, month, linked_cats)
            if (
                abs(planning._activity_map(ctx, month).get(_UNLINKED, 0.0))
                < 0.005
                < abs(planning._cash_activity_map(ctx, month).get(_UNLINKED, 0.0))
            ):
                cancelled_but_still_holding += 1
            assert abs(held_outside - raw) < 0.01, (
                f"{where}: held_outside={held_outside} but the raw book says {raw}"
            )
            # The two sides of the identity must draw from ONE account universe: every account
            # the hold reserved against is an account the money side summed — cash, or a card's
            # credit (`_card_credit_as_of`).
            money_ids = ctx["cash_ids"] | ctx["card_ids"]
            assert touched <= money_ids, f"{where}: reserved against non-money {touched}"
            # The MOVEMENT reconciles too, month over month — implied by the per-month equality
            # above, asserted separately because the term's job is to appear when cash leaves
            # with no home and to disappear at the roll.
            if prev_raw is not None:
                assert abs((held_outside - prev_held_outside) - (raw - prev_raw)) < 0.01, where
            prev_raw, prev_held_outside = raw, held_outside
            checked += 1
            seen["held"] += held > 0
            seen["net_overspent"] += held < 0
            seen["card_uncovered"] += card_uncovered > 0
            seen["held_outside"] += held_outside < -0.005
            seen["baseline"] += baseline > 0
            seen["in_month_hole"] += any(
                c["available"] < 0 for g in table["groups"] for c in g["categories"]
            )

    assert checked >= _BOOKS * len(_MONTHS) - _BOOKS  # every book contributed real months
    # The generator must actually reach the shapes the rule is about — a book set that never
    # overspends, never nets an envelope red or never leaves card spend uncovered would pass the
    # identity trivially.
    assert all(count > _BOOKS // 10 for count in seen.values()), seen
    # ...and the carry recurrence must be exercised on rows that were genuinely RED at the roll
    # (the reset half) as well as genuinely POSITIVE (the carry half), or the `max(0, ·)` is
    # never actually under test.
    assert carry["pairs"] > _BOOKS * len(env_ids), carry
    assert carry["reset_from_red"] > _BOOKS // 10, carry
    assert carry["carried_positive"] > _BOOKS // 10, carry
    # ...and every shape above is genuinely present, or check 4 would only ever have seen books
    # the pre-fix engines also got right.
    assert all(count > _BOOKS // 10 for count in card_shapes.values()), card_shapes
    assert cancelled_but_still_holding > _BOOKS // 10, cancelled_but_still_holding
    # ...including the two the generator was BLIND to until 2026-09-10 — four review rounds of
    # this property found nothing because every transfer it minted was either unstamped, or
    # stamped from the very leg that moved, so the stamp and the cash figure could not disagree.
    assert all(count > _BOOKS // 10 for count in stamped_shapes.values()), stamped_shapes
