"""
planning_service — YNAB envelope-budgeting math + the apply_budget_change choke point (PLAN).

ALL the Planning math lives here (the services tier): per-(month, category) Assigned /
Activity / Available with rollover, Ready-to-Assign, per-category target "needed this
month", the full ``budget_month`` table, and month-navigation bounds. Plus the SINGLE write
entrypoint ``apply_budget_change(change)`` (mirrors ``positions.apply_position_change``):
assign · move_money · set_target · group create/rename/reorder/delete/move-category — every
op validated BEFORE any write and routed through a checkpoint-covered store.

Money rules (computed-not-stored, base-currency-correct) — READY TO ASSIGN FOLLOWS YNAB
(ADR 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category, restoring the
YNAB-faithful ADR 2026-07-02 + payment-envelope ADR 2026-07-09 one day after
2026-09-08-ready-to-assign-is-unassigned-cash shipped and was found to match neither YNAB nor
Actual Budget, and to leave no room for paying a credit card off over time):

  available(m, cat) = max(0, available(m-1, cat)) + assigned(m, cat) + activity(m, cat)
      CLAMPED carry — a negative Available does NOT roll forward as debt. It resets at the
      month roll, and the deficit is charged to next month's Ready to Assign exactly once (the
      cash side already dropped for a cash overspend; the carry stops offsetting it). An
      overspend is HELD in-month — see ``ready_to_assign`` below — never charged twice.
      ``rollover_start`` (ctx["floor"]) still bounds the walk below.
  ready_to_assign(m) = cash(m) − pre_budget_baseline − held_in_envelopes(m)
                       − held_outside_envelopes(m) − card_spend_not_covered(m)
      held_in_envelopes(m)      = Σ over envelope ROWS  available(m, env)   — SIGNED, current
                                   month's own overspend counts NEGATIVE (the max(0,·) clamp
                                   lives only in the month-to-month CARRY above, never here)
      held_outside_envelopes(m) = Uncategorized + every unlinked category, each holding its own CASH
                                   hole under the SAME clamped carry (``_no_bucket_held``, ADR
                                   2026-09-10) — ≤ 0, so it only hands RTA room BACK
      card_spend_not_covered(m) = this month's uncovered card-spend ACTIVITY only
                                   (``_CARD_UNCOVERED_KEY`` in the activity map) — NOT a running
                                   card-debt balance; a prior month's uncovered swipe never
                                   reappears here once its month ends (the debt lives on the
                                   card's own balance forever, surfaced via the payment row's
                                   ``underfunded``/``owed`` fields, never re-subtracted from RTA)
      An envelope HOLDS its own overspend until month-end — RTA does not move when a category
      goes red, cash or card. If still red at the roll, the CARRY resets it to 0 and the hole is
      charged to next month's RTA (falls straight out of the identity, no separate charge step).
      COVERING an overspent envelope COSTS RTA, dollar for dollar up to the hole's size —
      assigning money there is what finally pays for it (``_auto_cover_overspending`` is capped
      at the displayed RTA accordingly, symmetric with ``_auto_underfunded``). CASH spending with
      NO envelope at all — uncategorized, or a category linked to none — HOLDS THE SAME WAY, in a
      pseudo-envelope of its own (``_no_bucket_held`` / ``held_outside_envelopes``, ADR
      2026-09-10 amending 2026-09-09, which drained it immediately on indirect research about
      YNAB; direct observation showed YNAB holds it too, in its own uncategorized bucket). Same
      clamped carry, ``assigned`` ≡ 0: RTA does not move at the spend, the hole resets at the
      roll and is charged to next month exactly once, and labelling the transaction before then
      makes the hold evaporate — the mirror of covering an overspent envelope. "No bucket" only
      ever matters for CASH. A CARD spend never had cash to lose, so its debt is never RTA's
      business — linked-but-unfunded and unlinked-entirely are equally RTA-neutral (see the
      credit-card treatment below), and the only way card debt moves RTA is by actually being
      FUNDED: assigning into that card's payment envelope, like any other assignment. The
      consequences fall out with no per-kind rules: income→cash raises RTA; a FUNDED spend (cash
      or card) is neutral; an UNFUNDED cash spend holds now, charges at the roll; an UNFUNDED
      card spend is neutral now AND forever (the debt lives on the card); a transfer to a
      tracking account removes it; a CATEGORIZED transfer with exactly ONE leg on-budget drains
      its envelope (owner decision 2026-07-03, docs/adr/2026-07-03-categorized-transfers.md) and
      holds the same way a spend does while that envelope stays in the black; a transfer with
      BOTH legs on-budget is neutral either way (the cash nets to zero on its own).
      RTA is NOT bounded above by cash − baseline (that 2026-09-08 invariant is retired) — a
      held overspend can put RTA above cash on hand until the roll resolves it. The identity
      instead CLOSES EXACTLY: RTA + held_in_envelopes + held_outside_envelopes +
      card_spend_not_covered + baseline == cash, always
      (tests/test_rta_identity_property.py pins the closed form on 200 books).

      CREDIT-CARD TREATMENT (ADR 2026-07-09-ynab-engine-credit-card-payment-envelopes.md,
      restored 2026-09-09, still the ONLY engine — the two-mode ``credit_card_mode`` flag was
      removed 2026-07-10 and never came back): a payment-tracked card LEAVES the cash sum
      (``cashflow.is_cash_account`` excludes it) and a real per-card "Credit Card Payments"
      envelope (``sync_card_payment_envelopes``) is where money is set aside to pay it — the
      ONLY funding mechanism, and the feature that lets a user pay a card off over time by
      assigning toward its payment envelope on their own schedule (a pre-existing/starting
      balance does NOT reduce RTA — the payment envelope starts at 0, so flipping onto this
      engine or adding an indebted card is a one-time jump, not a recurring drain).

          COVERAGE-CAPPED AUTO-MOVE (unchanged mechanically): a card spend on a category LINKED
          to an envelope auto-moves ``min(spend, max(0, headroom))`` from the spending envelope's
          row into the card's payment envelope — the cap is resolved at the ENVELOPE, not the raw
          category (envelope⊥category — a merged N>1 envelope shares ONE pool; every member
          category's card spend draws from the SAME headroom, computed on the CLAMPED prior
          carry, matching the carry rule above). A COVERED spend relocates cash (spending
          envelope −X available, payment envelope +X), leaving ``held_in_envelopes`` flat. An
          UNCOVERED spend's shortfall does NOT move to the payment envelope (it doesn't fund it)
          — it is instead yielded as ``_CARD_UNCOVERED_KEY``, which cancels the SPENDING ROW'S
          own drain in the identity, so a card spend linked to an envelope that partially or
          fully lacks headroom is RTA-neutral in the very month it happens (the envelope's own
          drop and the offsetting credit net to zero). A spend on a category with NO linked
          envelope at all (unlinked, or blank/Uncategorized) has no row to cancel against — so
          ``_CARD_UNCOVERED_KEY`` yields NOTHING for it, and nothing in the identity reflects
          that spend at all: RTA-neutral too, reached by ABSENCE instead of by cancellation.
          Card neutrality is therefore UNIFORM — there is no linked-vs-unlinked distinction on
          the card side, and no card spend is ever "one rule for no bucket" (that rule is
          CASH-only). The debt never touches RTA at all: the term is recomputed fresh per month
          from that month's own transactions and never carried, so a card's real, standing debt
          lives permanently on its balance, surfaced via the payment row's own ``underfunded``
          figure, never by subtracting from RTA — the ONE way it moves RTA is being FUNDED
          (assigning into the payment envelope). Paying the card (a transfer whose to-leg is a
          payment-tracked card) drains the payment envelope by the payment's own to-leg native
          amount, uncapped, and MAY drive it negative ("paid more than you set aside" — never
          clamped); it is RTA-neutral because
          cash and debt fall together, and the CLAMPED carry means a payment envelope driven
          negative this month resets to 0 next month rather than accumulating. A REFUND landing
          on the card under a linked spending category drains the payment envelope by the same
          amount (the debt shrank) while crediting the category — RTA-neutral, as in YNAB
          (ADR 2026-07-09 amendment, 2026-09-28).
  activity(m, cat)  = Σ income-refund(base) − Σ expense(base) for that (month, category),
      ALWAYS via ``ledger.txn_base_amount`` (recomputed from the native leg), so a base
      change re-values prior months with no stale ``base_amount`` leak. Only legs on a
      PLAN-SOURCE account (``cashflow.is_plan_source`` — identical to on-budget) fill
      envelopes — an expense paid from a tracking account never touches the plan (guard: it
      would leak RTA upward under the identity).

Inflow classification (PRD §8 decision): an income txn whose category is a SPENDING category
is a refund into that envelope (positive Activity); any other income (blank / no category /
income-kind category / ``ready_to_assign``) simply lands in on-budget cash and is therefore
RTA inflow BY THE IDENTITY — there is no inflow TERM to compute. An unclassified income row
carries ``category=None`` and never folds into the "" bucket at all, so the no-bucket hold above
cannot absorb it — it is on-budget cash the instant it lands. That asymmetry is the point: a
no-bucket bucket holds HOLES, never surpluses (``_no_bucket_held``'s upper clamp says the same
thing for the paths that CAN fold positive — a categorized transfer arriving on-budget, an
investment sell).

Assigned is stored in base currency and is NOT re-derived (it is authored intent in base).

The recursive walks are bounded below by ``budget_config.rollover_start`` / the first month
with any data (no infinite past walk) and memoized within one ``budget_month`` call.

See docs/prd/ynab-planning.md.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timezone

from sqlalchemy import Connection

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.txn_sources import display_source
from backend.core.user_time import user_today
from backend.core.write_receipt import record_write
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.engine import locked_unit_of_work
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.plan_state_store import PlanStateStore
from backend.db.tables import budget_assignments as budget_assignments_table
from backend.db.tables import envelope_groups as envelope_groups_table
from backend.db.tables import envelopes as envelopes_table
from backend.db.tables import plan_state as plan_state_table
from backend.db.tables import profile as profile_table
from backend.services import budgets as budgets_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services.cashflow import is_cash_account, is_payment_tracked_card, is_plan_source
from backend.services.fx_cache import load_fx_cache

logger = logging.getLogger(__name__)

assignments_store = BudgetAssignmentsStore()
groups_store = EnvelopeGroupsStore()
# Holds the ONE pending fresh-start undo before-image (see ``fresh_start``). A module singleton
# like the two above, for the same reason: `_eng()` resolves the engine per call, so a
# `db_isolated` redirect is honoured.
plan_state_store = PlanStateStore()

# Income txns carrying one of these (or no) categories are inflow-to-assign (Ready-to-Assign),
# never an envelope refund. (A spending category on income → a refund into that envelope.)
# "" catches an unclassified row (category=None; all reads coerce via `or ""`). There is no
# "uncategorized" key any more — the boot migration clears every legacy row to None.
_INFLOW_CATEGORIES = {"", "ready_to_assign"}


# Internal-only sentinel key (never a real category or envelope id — opaque ids are always
# `cat_xxx`/`env_xxx`) for the coverage-capped credit-card auto-move's uncovered-remainder term
# (ADR 2026-07-09-ynab-engine-credit-card-payment-envelopes.md, ``_capped_card_credits``).
# Read ONLY by ``_ready_to_assign`` (and echoed on the wire as `card_spend_not_covered`) and
# explicitly excluded from ``_unbudgeted_lines`` — it is never a row, never shown, never summed
# into ``budget_month``'s totals.
_CARD_UNCOVERED_KEY = "__card_uncovered__"


# ── Month helpers ────────────────────────────────────────────────────────────────


def _valid_month(month: str) -> str:
    """Validate a YYYY-MM string. Raises ValidationError on a bad shape."""
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise ValidationError(f"month must be YYYY-MM, got {month!r}")
    try:
        int(month[:4]), int(month[5:7])
    except ValueError:
        raise ValidationError(f"month must be YYYY-MM, got {month!r}")
    if not 1 <= int(month[5:7]) <= 12:
        raise ValidationError(f"month out of range: {month!r}")
    return month


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _this_month() -> str:
    t = user_today()
    return f"{t.year:04d}-{t.month:02d}"


# ── Envelope-id-native storage (docs/adr/2026-07-05-envelope-id-native.md) ────────
# Assigned amounts, targets, and notes are ALL stored keyed by ENVELOPE id — the row identity
# IS the envelope id, full stop. A category is a pure transaction label with no money of its
# own (envelope⊥category); an envelope is the thing that holds money. There is no category-
# keyed representation to fall back to and no indirection to resolve. `_resolve_envelope` is
# the ONE validator every write path uses to turn a caller-supplied ref into a confirmed-live
# envelope.


def _assigned_amt(month: str, envelope_id: str) -> float:
    """Assigned for (month, envelope_id) — a direct read of the envelope-keyed slot."""
    return assignments_store.month_map(month).get(envelope_id, 0.0)


def _set_assigned_amt(
    month: str, envelope_id: str, amount: float, *, conn: Connection | None = None
) -> None:
    """Write assigned for (month, envelope_id). ``conn`` composes the write into a caller's
    open transaction (``fresh_start``'s seam) — ``None`` opens its own, lock-held one."""
    assignments_store.set_assigned(month, envelope_id, amount, conn=conn)


def _assigned_to_date_map(month: str) -> dict[str, float]:
    """Cumulative NET assigned per envelope from the beginning of the book THROUGH ``month``
    (inclusive) — i.e. total contributions made toward each envelope so far, net of any
    releases (a release is a negative assign). Drives the "set aside $X by [date]" goal, whose
    progress is measured by what you've CONTRIBUTED (immune to spending), unlike the balance
    ``by_date`` goal that measures Available. Single file load (``all_map``)."""
    totals: dict[str, float] = {}
    for m, cats in assignments_store.all_map().items():
        if m <= month:
            for env_id, amt in cats.items():
                totals[env_id] = totals.get(env_id, 0.0) + amt
    return {k: round(v, 2) for k, v in totals.items()}


def _assigned_to_date(month: str, envelope_id: str) -> float:
    """Cumulative net assigned for one envelope through ``month`` (see ``_assigned_to_date_map``)."""
    return _assigned_to_date_map(month).get(envelope_id, 0.0)


def _target_for(envelope_id: str, targets: dict) -> dict | None:
    """Read the stored target for a budget row (envelope-keyed)."""
    return targets.get(envelope_id)


def _set_target_for(envelope_id: str, spec: dict | None, targets: dict) -> None:
    """Write (or clear) the target for a budget row (envelope-keyed)."""
    if spec is None:
        targets.pop(envelope_id, None)
    else:
        targets[envelope_id] = spec


def _resolve_envelope(ref: str) -> dict:
    """Resolve ``ref`` — an envelope id — to its live (non-archived) envelope record. Raises
    NotFoundError otherwise: an archived or unknown id is not a budget row."""
    envelope = envelopes_service.envelope_store.find_by_id(ref)
    if envelope is None or envelope.get("archived"):
        raise NotFoundError(f"'{ref}' is not a live envelope")
    return envelope


# ── Spending-category universe ───────────────────────────────────────────────────


def _spending_categories() -> set[str]:
    """The budgetable (expense) category universe, plus any category that already carries an
    assignment or a target (so a custom category the user budgets shows up even before it is
    in the built-in/budget sets). Used for category-manager-adjacent validation (e.g. seeding
    a new group's category list) — NOT for row rendering or write validation, which key on
    envelope LINKAGE instead (``envelopes_service.env_of()``) since the full split (RFC
    envelope-category-full-split.md §Backend 3)."""
    cats = set(budgets_service.expense_categories(budgets_service.read_budgets()))
    env_index = {e["id"]: e for e in envelopes_service.all_envelopes()}
    for month in assignments_store.months():
        for k in assignments_store.month_map(month):
            env = env_index.get(k)
            if env is not None:
                cats.update(env.get("category_ids") or [])
            else:
                cats.add(k)
    cats.update(read_category_targets().keys())
    return cats


# ── Envelope row grouping ──────────────────────────────────────────────────────────
# `budget_month` renders one row per NON-ARCHIVED envelope, full stop — including a
# zero-category envelope (assigned/target/available work; activity is always 0). An unlinked
# category has no budget row — its activity surfaces in the synthetic "Unbudgeted" group
# instead (`_unbudgeted_lines`). `_budget_rows()` is the single source of truth for this
# grouping; every recursive-walk function below folds through its `envelope_id` (the row's ONE
# identity) so assigned is read ONCE per envelope and activity is a genuine sum across members.


def _budget_rows() -> list[dict]:
    """One row-spec per non-archived envelope. ``envelope_id`` is the row's identity — the ONE
    key ``_assigned_amt``/``_available`` read through. ``key`` mirrors ``envelope_id`` (kept as
    a separate field so every existing reader/wire consumer that names it ``key`` — the table
    row's own `key`, the frontend's row identity, the AI tools — needs no further change)."""
    from backend.db.category_store import CategoryStore

    color_map = {r["id"]: (r.get("color") or "") for r in CategoryStore().all()}

    rows: list[dict] = []
    for envelope in envelopes_service.all_envelopes():
        if envelope.get("archived"):
            continue

        env_color = envelope.get("color") or ""
        cat_ids = list(envelope.get("category_ids") or [])
        if not env_color and cat_ids:
            env_color = color_map.get(cat_ids[0]) or ""

        rows.append(
            {
                "envelope_id": envelope["id"],
                "category_ids": cat_ids,
                "key": envelope["id"],
                "name": envelope.get("name") or "",
                "group_id": envelope.get("group_id"),
                "order": envelope.get("order") or 0,
                "color": env_color,
            }
        )
    return rows


def _row_label(spec: dict) -> str:
    """Display label for a budget row-spec: the envelope's OWN name whenever it is non-empty
    (envelope-first display, ENV-RES-08 fix 2026-07-04) — a 1:1 row used to always show the
    category's own label, silently discarding a name the user typed over a single pick in
    NewEnvelopeModal; falls back to the primary category's live label, then the row's own key
    (a zero-category envelope can have neither). On the byte-identical 1:1 migration book every
    auto-minted envelope's name equals its category's label (and ``on_category_relabeled``
    keeps them in sync going forward), so this is a structural no-op there — the pinned
    migration test stays green."""
    if spec["name"]:
        return spec["name"]
    cats = spec["category_ids"]
    return _category_label(cats[0]) if cats else spec["envelope_id"]


# ── Per-envelope targets (profile.category_targets) ───────────────────────────────


def read_category_targets() -> dict:
    """The stored ``profile.category_targets`` map: {envelope_id: {type, amount, date?}}."""
    raw = budgets_service._read_profile_raw().get("category_targets") or {}
    return raw if isinstance(raw, dict) else {}


def write_category_targets(targets: dict) -> None:
    budgets_service._write_profile_key("category_targets", targets)


# ── Per-envelope notes (profile.category_notes) ───────────────────────────────────


def read_category_notes() -> dict:
    """The stored ``profile.category_notes`` map: {envelope_id: note_text}."""
    raw = budgets_service._read_profile_raw().get("category_notes") or {}
    return raw if isinstance(raw, dict) else {}


def write_category_note(envelope_id: str, note: str) -> None:
    """Set or clear the note for a budget row, keyed by envelope id. An empty/blank note
    removes the key. ``envelope_id`` must resolve to a live (non-archived) envelope —
    NotFoundError otherwise (an unlinked category has no row, so a note on it would never
    surface anywhere)."""
    envelope_id = _resolve_envelope((envelope_id or "").strip())["id"]
    notes = read_category_notes()
    if note.strip():
        notes[envelope_id] = note.strip()
    else:
        notes.pop(envelope_id, None)
    budgets_service._write_profile_key("category_notes", notes)


# ── Activity (from the ledger, base-recomputed) ──────────────────────────────────


def plan_leg_ok(t: dict, leg: str, plan_source_ids, cutoff_by_id: dict) -> bool:
    """Does this txn's ``leg`` account participate in the PLAN (envelope activity)?

    Balance-based RTA requires envelope activity to be matched by a plan-source cash movement,
    so a leg fills an envelope ONLY when it is on a KNOWN plan-source account
    (``cashflow.is_plan_source`` — identical to on-budget in both credit-card modes; verify-lens
    fix: a missing/nonexistent account id used to slip through and phantom-move RTA — spending
    money RAISED it). A leg dated before its account's tracking start is also out:
    that movement is already baked into the asserted opening balance, so counting it would
    fill an envelope with cash the balance side has absorbed.

    PUBLIC (no leading underscore) because 50/30/20 reuses this EXACT rule
    (``budget_engines.proportional_fold``, ADR 2026-09-25) so the two methods' envelope
    activity can never silently diverge on which legs even count — see that module's own
    docstring for how it threads ``plan_source_ids``/``cutoff_by_id`` through its ``ctx``."""
    acct = t.get(leg)
    if acct not in plan_source_ids:
        return False
    if svc.is_before_tracking_start(t.get("date"), cutoff_by_id.get(acct)):
        return False
    return True


def _card_payment_env_id(
    t: dict, leg: str, card_pay_env_of: dict, cutoff_by_id: dict
) -> str | None:
    """The live payment envelope id for ``t``'s ``leg`` account, if it is a payment-tracked
    card with a live FK (``credit_card_mode()=="payment_envelope"`` — ``card_pay_env_of`` is
    built empty otherwise, see ``_build_context``) and the leg isn't dated before that card's
    own tracking-start anchor (mirrors ``plan_leg_ok``'s cutoff, applied directly
    since a payment-tracked card is never gated by ``plan_source_ids`` membership here)."""
    acct = t.get(leg)
    env_id = card_pay_env_of.get(acct)
    if not env_id:
        return None
    if svc.is_before_tracking_start(t.get("date"), cutoff_by_id.get(acct)):
        return None
    return env_id


def _iter_category_contributions(
    txns: list[dict],
    month: str,
    base: str,
    rates: dict,
    plan_source_ids: frozenset[str] | set[str],
    cutoff_by_id: dict,
    card_pay_env_of: dict[str, str] | None = None,
    ctx: dict | None = None,
    cash_out: dict[str, float] | None = None,
):
    """Yield ``(category, delta, txn, split_or_None)`` for every leg/split that actually moves
    an envelope's Activity for ``month`` — the ONE fold both ``_activity_by_category`` (sums by
    category) and ``activity_transactions`` (the Activity-popover's per-transaction detail feed)
    share, so the two can never silently disagree on eligibility/sign/amount. See
    ``_activity_by_category``'s docstring for the full per-kind rules this implements.

    Card membership (``ctx["card_ids"]``, read straight off ``ctx`` rather than taking its own
    parameter — a separate caller-supplied set here would be one more thing a direct caller could
    forget to thread, silently disabling coverage for a LIVE card too, not just an archived one)
    decides whether an outflow is CARD SPEND at all; ``card_pay_env_of``
    (``{account_id: payment_envelope_id}``, only a LIVE FK — empty for an archived or
    just-dropped-out card) decides only where its coverage credit lands, if anywhere. The two are
    DELIBERATELY separate (ADR 2026-09-28-iterative-available-walk-and-uniform-card-coverage):
    every card-funded outflow — an EXPENSE, a categorized TRANSFER's on-budget/moving leg, or an
    investment BUY — is a candidate for the coverage-capped auto-move whether or not that card
    currently has a live payment envelope to credit, because a card's debt is RTA-neutral
    UNCONDITIONALLY (CLAUDE.md: "a card never had cash to lose"), not only while its payment
    envelope happens to resolve. (a) an EXPENSE whose from-account is a card ALWAYS drains its
    spending category/split line by the FULL native→base amount (unchanged — capping never
    shrinks the spend itself), and its coverage-capped portion is credited to the card's payment
    envelope when one resolves — see ``_capped_card_credits`` for the v2 cap (this needs ``ctx``
    to resolve each envelope's Available-before-this-month's-card-spends baseline; card-spend
    lines are collected during this pass and resolved in one batch AFTER the main loop, so a
    candidate's credit is deferred, never yielded inline); (b) a TRANSFER whose TO-account is a
    LIVE payment-tracked card is ALWAYS a card payment — detected BEFORE the categorized-transfer
    XOR block below and handled exclusively by it (``continue``s past the XOR), draining the
    payment envelope by the TO-leg's own native amount converted to base (never
    ``txn_base_amount``, which prefers the from-leg), UNCAPPED (a payment may drive the payment
    envelope negative — YNAB-correct, never clamped) — this fires regardless of the transfer's
    own ``category`` (paying a card down is always a payment, whatever a stray label says) and
    regardless of the from-leg's plan-source status (mirrors YNAB: a card-directed transfer
    always spends the Payment category, even when funded from an account outside the plan); a
    transfer whose FROM-account is a card instead (money leaving it for an off-budget account,
    categorized) is card spend exactly like (a) — same coverage-capped treatment, same drain
    magnitude taken from the ENVELOPE figure (``delta``), not the cash figure (``moved_now``);
    (c) a categorized investment BUY funded from a card is card spend too, mirroring (a) exactly
    — the pre-fix code never consulted card membership here at all, so this leg's drain was
    always a bare, uncancelled subtraction.

    ``cash_out``, when supplied, is FILLED with the GENUINELY-CASH fold — every contribution
    above whose MOVING leg sits on an account in ``ctx["cash_ids"]``, the exact set
    ``_cash_as_of`` sums, PLUS the two contributions that reach it and nothing else (an inflow on
    an unlinked category, and a cash-account expense the card branch routed away — both via
    ``fold_no_bucket_only``). It is neither a subset nor a superset of the yielded stream, and it
    is a strictly narrower set than the coverage cap's
    ``cash_activity_by_key`` (which admits every plan-source leg except a payment-tracked card's
    own outflow — expense, transfer-out or investment buy, LIVE or ARCHIVED alike, ADR
    2026-09-28: every one of them is now a card-spend candidate and so excluded from this
    accumulator, never just a live card's). What it STILL admits that ``cash_out`` never does is
    the INBOUND side of a card leg — an income refund landing on a card, or a categorized
    transfer ARRIVING at one — since those credit a category rather than draining it and are
    outside this fix's scope. The two READERS want genuinely different predicates and each gets
    its own accumulator: the cap asks "everything except a card's own outflow", the no-bucket
    hold (``_no_bucket_held``) asks "what actually left a cash account". Gating the hold on
    ``cash_ids`` is what makes the two sides of the RTA identity provably draw from ONE account
    universe — a leg the hold reserves against is a leg ``_cash_as_of`` counted. Conflating them
    fabricated Ready to Assign (security review, 2026-09-10).

    They differ in the AMOUNT too, not only in the leg set: the cap's pool mirrors ENVELOPE math
    (each row's stamped, historical-rate-locked figure — it is compared against an envelope's
    Available), while ``cash_out`` mirrors CASH math (the moving leg's own native amount at
    ``ctx["rates"]``, the arithmetic ``_cash_as_of`` does on that same account). The two agree on
    every expense/income/investment line and can disagree on a transfer — see that branch. A
    generator fills it as it is CONSUMED: a caller must exhaust the iterator before reading it."""
    if cash_out is not None and ctx is None:
        raise ValidationError(
            "_iter_category_contributions: ctx is required to fill cash_out — the no-bucket "
            "hold is gated on ctx['cash_ids']"
        )
    linked = envelopes_service.env_of()
    card_pay_env_of = card_pay_env_of or {}
    # (spending_env_id, card_env_id, amt, txn, split_or_None) — spending_env_id is the pool the
    # coverage cap is resolved against; card_env_id is the row the CAPPED credit is yielded to,
    # or None when this card has no LIVE payment envelope (archived / just dropped out) — see
    # _capped_card_credits, which then treats the whole amount as uncovered without touching the
    # pool. Two different cards spending against the SAME spending envelope share ONE headroom
    # pool — always distinct ids, never conflated.
    card_spend_candidates: list[tuple[str | None, str | None, float, dict, dict | None]] = []
    cash_activity_by_key: dict[str, float] = {}  # the coverage cap's pool — see the docstring
    cash_ids = ctx["cash_ids"] if ctx else frozenset()
    card_ids = ctx["card_ids"] if ctx else frozenset()

    def fold(
        key: str, delta: float, moving_account_id: str | None, cash_delta: float | None = None
    ) -> None:
        """Book against ``key`` in BOTH accumulators — the coverage cap's pool takes ``delta``
        (the ENVELOPE's figure, historical-rate-locked); the cash-only one takes ``cash_delta``
        (defaulting to ``delta``) and only when the leg that actually moved sits on a ``cash``
        account.

        The two are the same number everywhere the stamped envelope figure IS the moving leg's
        own value at today's rate — every expense/income/investment line. They differ on a
        TRANSFER, whose stamp is taken from whichever leg was already in base currency and so
        may be neither the moving leg nor today's rate: see that branch."""
        cash_activity_by_key[key] = cash_activity_by_key.get(key, 0.0) + delta
        fold_no_bucket_only(key, delta if cash_delta is None else cash_delta, moving_account_id)

    def fold_no_bucket_only(key: str, delta: float, moving_account_id: str | None) -> None:
        """Book ``delta`` against the NO-BUCKET accumulator ALONE — not the coverage cap's pool,
        and (for the inflow caller, which also declines to ``yield``) not the activity map either.

        Two contributions belong here. An INFLOW on a LABELED category linked to no envelope has
        to be able to cancel that key's own hold — otherwise a $100 spend and its matching $100
        refund left the hold at −100 while the refund independently raised RTA through cash,
        counting the same $100 twice (review, 2026-09-10). But it must NOT become Activity (there
        is no envelope for it to be Activity ON — an inflow under an unlinked label is RTA money,
        which is precisely why it is not yielded), and it must not widen the card coverage cap's
        headroom (it is not coverage).

        The other is an EXPENSE routed down the card branch by a ``payment_envelope_id`` FK while
        its account's ``type`` is already ``cash`` — ``update_account`` writes the type before the
        sync that clears the FK, and the two share no transaction, so a slow or failed sync leaves
        that window open. The FK routes the coverage CREDIT; ``cash_ids`` decides the HOLD, and
        the two answer independently (money really did leave a cash account). Its drain still
        stays out of the cap's pool, which exists precisely to exclude card-spend drains."""
        if cash_out is not None and moving_account_id in cash_ids:
            cash_out[key] = cash_out.get(key, 0.0) + delta

    for t in txns:
        if t.get("deleted") or not (t.get("date") or "").startswith(month):
            continue
        kind = t.get("kind")
        splits = t.get("splits")
        if kind == "expense":
            if not plan_leg_ok(t, "from_account_id", plan_source_ids, cutoff_by_id):
                continue  # not plan money (tracking / unknown account / pre-anchor)
            from_acct = t.get("from_account_id")
            # A card-spend candidate whenever the account is payment-tracked AT ALL (ADR
            # 2026-09-28) — LIVE or ARCHIVED alike, never gated on whether a payment envelope
            # currently resolves. `card_env_id` (possibly None — an archived/retired card) is
            # resolved separately: `_capped_card_credits` credits it only when live, else the
            # FULL amount is uncovered — a card never had cash to lose, whether or not it still
            # has somewhere to book the coverage.
            is_card_spend = from_acct in card_ids
            card_env_id = card_pay_env_of.get(from_acct)
            if splits:
                for s in splits:
                    amt = svc.txn_split_base_amount(t, s, base, rates)
                    cat = s["category"]
                    yield (cat, -amt, t, s)  # full drain always happens, capped or not
                    if is_card_spend:  # candidate for the coverage-capped auto-move — ALWAYS
                        # added, even when unlinked/blank (spend_env_id=None → 0 headroom, see
                        # _capped_card_credits): the uncovered remainder still needs tracking so
                        # it can offset the drain above and keep the RTA identity exact.
                        spend_env_id = linked.get(cat or "")
                        card_spend_candidates.append((spend_env_id, card_env_id, amt, t, s))
                        # ...and the HOLD is decided by `cash_ids`, never by the FK — see
                        # fold_no_bucket_only (a no-op unless this really is a cash account).
                        fold_no_bucket_only(cat, -amt, from_acct)
                    else:
                        fold(cat, -amt, from_acct)
            else:
                cat = t.get("category") or ""
                amt = svc.txn_base_amount(t, base, rates)
                yield cat, -amt, t, None  # full drain always happens, capped or not
                if is_card_spend:
                    spend_env_id = linked.get(cat)
                    card_spend_candidates.append((spend_env_id, card_env_id, amt, t, None))
                    fold_no_bucket_only(cat, -amt, from_acct)
                else:
                    fold(cat, -amt, from_acct)
        elif kind == "income":
            if not plan_leg_ok(t, "to_account_id", plan_source_ids, cutoff_by_id):
                continue  # not plan money (tracking / unknown account / pre-anchor)
            # A LINKED, non-inflow category takes the refund fold (envelope Activity). An
            # UNLINKED one takes the no-bucket-only fold instead — it is still RTA money and
            # still not Activity, but it must be able to cancel that key's own hold. An INFLOW
            # category (blank, "ready_to_assign") takes neither: it is inflow-to-assign by
            # definition, and netting it into the Uncategorized bucket would wipe that bucket's hold
            # for anyone whose salary lands uncategorized.
            # A refund landing on a payment-tracked card also DRAINS that card's payment
            # envelope by the same amount — you now owe less, so less is set aside (YNAB parity,
            # 2026-09-28: a card refund was held twice, pushing RTA low by its amount). The refund still
            # widens the category's coverage headroom via `fold`, so a same-month spend+refund
            # nets exactly like YNAB's net card spend.
            refund_env = _card_payment_env_id(t, "to_account_id", card_pay_env_of, cutoff_by_id)
            if splits:
                for s in splits:
                    sc = s.get("category") or ""
                    if sc in _INFLOW_CATEGORIES:
                        continue
                    delta = svc.txn_split_base_amount(t, s, base, rates)
                    if sc in linked:
                        fold(sc, delta, t.get("to_account_id"))
                        yield sc, delta, t, s
                        if refund_env:
                            yield refund_env, -delta, t, s
                    else:
                        fold_no_bucket_only(sc, delta, t.get("to_account_id"))
            else:
                cat = t.get("category") or ""
                if cat in _INFLOW_CATEGORIES:
                    continue
                delta = svc.txn_base_amount(t, base, rates)
                if cat in linked:
                    fold(cat, delta, t.get("to_account_id"))
                    yield cat, delta, t, None
                    if refund_env:
                        yield refund_env, -delta, t, None
                else:
                    fold_no_bucket_only(cat, delta, t.get("to_account_id"))
        elif kind == "investment":
            cat = t.get("category") or ""
            if not cat:
                continue  # uncategorized → drains/credits RTA directly via cash; no envelope
            if t.get("from_account_id"):  # a buy drains the envelope, like a budgeted expense
                from_acct = t.get("from_account_id")
                if plan_leg_ok(t, "from_account_id", plan_source_ids, cutoff_by_id):
                    amt = svc.txn_base_amount(t, base, rates)
                    delta = -amt
                    yield cat, delta, t, None  # full drain always happens, capped or not
                    if from_acct in card_ids:  # a card-funded buy is card spend too (ADR
                        # 2026-09-28) — same coverage-capped treatment as a card expense; the
                        # pre-fix code never checked card membership here at all.
                        card_env_id = card_pay_env_of.get(from_acct)
                        spend_env_id = linked.get(cat)
                        card_spend_candidates.append((spend_env_id, card_env_id, amt, t, None))
                        fold_no_bucket_only(cat, delta, from_acct)
                    else:
                        fold(cat, delta, from_acct)
            elif t.get("to_account_id"):  # a sell credits the envelope, like an income refund
                if plan_leg_ok(t, "to_account_id", plan_source_ids, cutoff_by_id):
                    delta = svc.txn_base_amount(t, base, rates)
                    fold(cat, delta, t.get("to_account_id"))
                    yield cat, delta, t, None
        elif kind == "transfer":
            card_env_id = _card_payment_env_id(t, "to_account_id", card_pay_env_of, cutoff_by_id)
            if card_env_id:  # paying down a payment-tracked card — always the Payment envelope,
                # UNCAPPED (rule 3: may go negative — never resolved via card_spend_candidates)
                paid = svc.to_base(
                    float(t.get("to_amount") or 0),
                    t.get("to_currency") or base,
                    base,
                    rates=rates,
                )
                yield (card_env_id, -paid, t, None)
                continue  # this transfer's own `category` (if any) is inert — see docstring
            cat = t.get("category") or ""
            if not cat:
                continue  # uncategorized → drains/credits RTA directly via cash; no envelope
            from_ok = plan_leg_ok(t, "from_account_id", plan_source_ids, cutoff_by_id)
            to_ok = plan_leg_ok(t, "to_account_id", plan_source_ids, cutoff_by_id)
            if from_ok == to_ok:
                continue  # both on-budget (net-zero — label inert) or neither (not plan money)
            # The MOVING leg is whichever side is on-budget (they differ by the XOR above): its
            # account decides whether any real cash moved, and its OWN native amount at TODAY's
            # rate is EXACTLY what `_cash_as_of` sees leave/arrive on that account (it revalues
            # each account's native balance at ctx["rates"]). That is the no-bucket hold's figure,
            # signed by direction — leaving drains, arriving credits.
            leg = "from" if from_ok else "to"
            moved_now = svc.to_base(
                float(t.get(f"{leg}_amount") or 0),
                t.get(f"{leg}_currency") or base,
                base,
                rates=rates,
            )
            if from_ok:
                moved_now = -moved_now
            # The ENVELOPE's figure is the STAMPED base value instead (the moved value, at the
            # transaction-date rate) when present — it keeps a categorized FOREIGN transfer's
            # envelope contribution from drifting with the market, exactly like an expense/income
            # (ADR 2026-07-09). Trusted only while the row's `base_currency` still matches the
            # CURRENT base: a base-currency switch clears the FX cache and deliberately does NOT
            # re-stamp existing rows, so a stale stamp is an old-base number that means nothing
            # here (same rule as `core.fx.txn_base_amount`, which this branch cannot call — for a
            # transfer it prefers the FROM leg, while the leg that matters here is the ON-BUDGET
            # one). Stale or unstamped falls back to the moving leg at today's rate; categorizing
            # a legacy transfer (the moment it starts touching the budget) re-stamps it.
            ba = t.get("base_amount") if (t.get("base_currency") or "").upper() == base else None
            delta = moved_now if ba is None else (-1.0 if from_ok else 1.0) * abs(float(ba))
            # ...and the two are NOT interchangeable: `_transfer_base_value` stamps whichever leg
            # was already in base currency, which on a cross-currency transfer is routinely the
            # OTHER leg, at the BANK's rate rather than the book's — and even same-currency legs
            # differ when a fee is eaten in transit. Folding the stamp into the cash accumulator
            # reserved a number `_cash_as_of` never counted, fabricating RTA in one direction and
            # draining real cash in the other (differential fuzz, 2026-09-10).
            moving_acct = t.get(f"{leg}_account_id")
            if leg == "from" and moving_acct in card_ids:
                # A card-funded categorized transfer OUT (e.g. credit card → brokerage) is card
                # spend too, not a bare drain (ADR 2026-09-28) — the envelope still absorbs the
                # STAMPED `delta` in full below; the coverage-capped auto-move (or, absent a live
                # payment envelope, `_CARD_UNCOVERED_KEY`) matches it dollar for dollar, exactly
                # like a card expense. The candidate's magnitude is `-delta` (delta is negative
                # here — the moving leg is the FROM leg), mirroring the expense branch's `amt`.
                card_env_id = card_pay_env_of.get(moving_acct)
                spend_env_id = linked.get(cat)
                card_spend_candidates.append((spend_env_id, card_env_id, -delta, t, None))
                fold_no_bucket_only(cat, delta, moving_acct)
            else:
                fold(cat, delta, moving_acct, cash_delta=moved_now)
            yield (cat, delta, t, None)

    if card_spend_candidates:
        yield from _capped_card_credits(card_spend_candidates, cash_activity_by_key, month, ctx)


def _capped_card_credits(
    candidates: list[tuple[str | None, str | None, float, dict, dict | None]],
    cash_activity_by_key: dict[str, float],
    month: str,
    ctx: dict | None,
):
    """Resolve the v2 COVERAGE-CAPPED auto-move credit for every card-spend candidate collected
    by ``_iter_category_contributions``'s main pass. The cap pool is the RESOLVED SPENDING
    ENVELOPE (never the raw category — envelope⊥category, RFC
    envelope-category-full-split.md: a merged N>1 envelope shares ONE Available pool, so every
    member category's card spend draws from the SAME headroom, not its own); the CREDIT lands on
    each spend's OWN card's payment envelope — two different cards spending against the same
    spending envelope in the same month share ONE headroom pool but credit DIFFERENT payment
    envelopes (whichever card each transaction is actually on), so candidates are grouped by
    ``spend_env_id`` for the cap computation but yielded keyed by their own ``card_env_id``. A
    candidate whose category has NO linked envelope carries ``spend_env_id=None`` — it is grouped
    on its own with headroom forced to 0 (no pool, no coverage possible).

    Per spending envelope: ``moved = min(Σ card spend this month, max(0, headroom))`` where
    ``headroom = max(0, available(prev_month)) + assigned(month) + cash-only activity(month)`` —
    the envelope's Available *before* any of this month's card spending against it, on the
    CLAMPED prior carry (ADR 2026-09-09: a hole resets at the roll, so last month's deficit
    never eats this month's coverage). "Cash-only"
    is every OTHER contribution already folded for this envelope's member categories during the
    SAME pass (income refunds, non-card expenses, transfers, investment lines —
    ``cash_activity_by_key``, accumulated by the caller; card-spend DRAIN lines are deliberately
    excluded from it, since they're exactly the activity being capped here). "Cash-only" here
    means "everything except a payment-tracked card's OWN outflow" and NOT "``type == cash``":
    a card's expense, transfer-out or investment buy is EXCLUDED from ``cash_activity_by_key``
    whether that card is LIVE or ARCHIVED (ADR 2026-09-28-iterative-available-walk-and-uniform-
    card-coverage — before it, an archived card's spend fell through to the plain fold and DID
    shrink this headroom, silently stealing coverage capacity from a live card sharing the same
    spending envelope with nothing to show for it, since nothing credited the archived card back
    either). An archived/uncreditable candidate still arrives here (``card_env_id is None`` below
    resolves it as 100% uncovered without touching ``headroom`` at all — see the loop), so it can
    never consume coverage meant for a card that actually has somewhere to receive it. That is a
    DIFFERENT predicate from the no-bucket hold's, which asks whether real cash moved
    (``cash_out`` — see ``_iter_category_contributions``); the two accumulators are deliberately
    separate.

    MONTH-AGGREGATE, not per-transaction-chronological, and this is PROVABLY equivalent for the
    identity + every spec'd test case: for spends s1..sn summing to S against one envelope with
    headroom A, sequential coverage (each txn draws down the SAME running headroom) yields a
    TOTAL moved of ``min(S, max(0,A))`` regardless of processing order — coverage is monotonic,
    so reordering the spends can only change WHICH single line (and, when two different cards
    share the pool, which CARD) absorbs a partial-coverage remainder, never the total moved. The
    total is therefore order-invariant; only the per-transaction breakdown
    (``activity_transactions``) needs a deterministic order, so candidates are still walked in
    (date, created_at, id) order to keep that detail view stable and reproducible.

    WHAT HAPPENS TO THE UNCOVERED REMAINDER (why this function ALSO yields
    ``_CARD_UNCOVERED_KEY``, not just the capped credit — ADR 2026-09-09, restoring 2026-07-09):
    the spending category's own drain is yielded unconditionally by the caller, in FULL, before
    this function runs — required, so an overspent row shows its real negative Available, and
    that drain fully participates in ``_ready_to_assign``'s subtracted sum. But a card is
    EXCLUDED from cash entirely, so unlike a cash overspend (where the cash leg and the
    envelope's drain move together and net to zero) a card spend's cash side never moves at all.
    Left unmatched, the spending row's negative would make RTA *increase* by the uncovered
    amount — an unfunded charge manufacturing assignable money out of nothing. So exactly
    ``amt − covered`` is yielded as a POSITIVE delta under ``_CARD_UNCOVERED_KEY`` — a dedicated,
    non-row, ``_ready_to_assign``-only term cancelling the spending row's drain for the part no
    envelope reserved — but ONLY when ``spend_env_id is not None``, i.e. only when there IS a
    spending row with a drain to cancel.

    For ``spend_env_id is None`` (no linked envelope at all — an unlinked category, or
    blank/Uncategorized) NOTHING is yielded and nothing in the identity reflects the spend:
    ``_ready_to_assign`` sums ``held_in_envelopes`` over envelope ROWS only, and there is no row,
    so there was never a drain to offset. That is exactly as RTA-neutral as the
    linked-but-unfunded case — by ABSENCE rather than by cancellation. Yielding the term
    unconditionally (the bug fixed 2026-09-09, found by that day's security review) made an
    unlinked card spend a BARE, uncancelled subtraction: RTA dropped by the whole swipe and then
    silently un-dropped at the next month's roll, since the term is per-month and never carried —
    a drain with nothing to show for either half. Net effect either way: an uncovered card spend
    is RTA-NEUTRAL, this month and every month after — a card never had cash to lose, so its debt
    is never RTA's business, linked or not. It surfaces through the payment row's own
    ``underfunded`` figure (``_payment_row_fields``), and touches RTA only by actually being
    funded (an assignment into the payment envelope).

    ``ctx`` is required to resolve ``available(prev_month, envelope_id)`` — every real call site
    (``_activity_map``, ``activity_transactions``) supplies it. A caller with card spend
    candidates but no ``ctx`` (a direct unit-test call bypassing the normal ``_build_context``
    path) is a caller error — this raises loudly rather than silently under/over-crediting."""
    if ctx is None:
        raise ValidationError(
            "_iter_category_contributions: ctx is required to resolve the coverage cap "
            "for a payment-tracked card spend"
        )
    by_spend_env: dict[str | None, list[tuple[str | None, float, dict, dict | None]]] = {}
    for spend_env_id, card_env_id, amt, t, s in candidates:
        by_spend_env.setdefault(spend_env_id, []).append((card_env_id, amt, t, s))

    for spend_env_id, items in by_spend_env.items():
        if spend_env_id is None:
            headroom = 0.0  # no linked envelope → no pool → no coverage possible
        else:
            members = ctx["_row_of_cat"].get(spend_env_id, [spend_env_id])
            cash_activity = round(sum(cash_activity_by_key.get(m, 0.0) for m in members), 2)
            assigned = _ctx_assigned(ctx, month, spend_env_id)
            if month <= ctx["floor"]:
                prev_carry = 0.0
            else:
                prev_carry = max(0.0, _available(ctx, _prev_month(month), spend_env_id))
            headroom = max(0.0, round(prev_carry + assigned + cash_activity, 2))
        items.sort(
            key=lambda item: (
                item[2].get("date") or "",
                item[2].get("created_at") or "",
                item[2].get("id") or "",
                (item[3] or {}).get("category") or "",
            )
        )
        for card_env_id, amt, t, s in items:
            if card_env_id is not None:
                covered = round(min(amt, headroom), 2)
                headroom = round(headroom - covered, 2)
            else:
                # No LIVE payment envelope to move money into (archived / just dropped out) —
                # 100% uncovered by construction, and deliberately NOT drawn from `headroom`: an
                # uncreditable card must never consume coverage capacity a live card sharing the
                # same spending envelope could actually use (ADR 2026-09-28).
                covered = 0.0
            if covered > 0:
                yield (card_env_id, covered, t, s)
            uncovered = round(amt - covered, 2)
            # ONLY when there is a spending ROW whose drain needs cancelling — a
            # `spend_env_id is None` candidate never landed one, so yielding here would be a
            # bare subtraction that evaporates at the next roll. See the docstring above.
            if uncovered > 0 and spend_env_id is not None:
                yield (_CARD_UNCOVERED_KEY, uncovered, t, s)


def _activity_by_category(
    txns: list[dict],
    month: str,
    base: str,
    rates: dict,
    plan_source_ids: frozenset[str] | set[str] = frozenset(),
    cutoff_by_id: dict | None = None,
    card_pay_env_of: dict[str, str] | None = None,
    ctx: dict | None = None,
    cash_out: dict[str, float] | None = None,
) -> dict:
    """Net Activity per category for one month, base currency. Expense is NEGATIVE (money
    leaves the envelope); an income txn categorized to a SPENDING category is a positive
    refund into that envelope. Income that is inflow-to-assign is excluded (it feeds RTA,
    not an envelope). Adjustment never touches an envelope (no category, ever).

    PLAN-SOURCE GUARD (balance-based RTA): a leg fills an envelope ONLY when it is on a KNOWN
    plan-source account and not before that account's tracking start — see
    ``plan_leg_ok``. Under the identity RTA = cash − baseline − Σ available, envelope activity
    with no matching plan-source cash movement leaks RTA (an off-budget/ghost/account-less
    expense would RAISE it; an account-less refund would depress it permanently).

    PAYMENT-ENVELOPE AUTO-MOVE (``card_pay_env_of``, ADR
    2026-07-09-ynab-engine-credit-card-payment-envelopes.md, empty under credit_card_mode
    "signed") is additive to every rule below — see ``_iter_category_contributions``'s and
    ``_capped_card_credits``'s docstrings for the exact v2 coverage-capped mechanics (an expense
    funded by a payment-tracked card drains its spending envelope in FULL but credits its
    payment envelope only up to that envelope's own coverage; a transfer paying down a
    payment-tracked card drains it uncapped, pre-empting the categorized-transfer XOR rule for
    that transaction). ``ctx`` is required whenever ``card_pay_env_of`` is non-empty — it
    resolves each spending envelope's prior-month Available for the cap; omitted by every
    caller that never populates ``card_pay_env_of`` (e.g. split-transaction tests), so it stays
    an optional, additive parameter.

    A CATEGORIZED investment leg folds exactly like a budgeted expense/income refund: a buy
    (from_account_id set) is NEGATIVE — it drains its envelope, e.g. a synced Commitment DCA
    purchase; a sell (to_account_id set) is POSITIVE — it credits its envelope, e.g. selling a
    holding earmarked for a specific goal. An UNCATEGORIZED investment leg is excluded
    entirely (not even the blank-category Uncategorized bucket expense/income use) — it already
    drains/credits Ready-to-Assign directly through the cash side of the identity, so folding
    it into "" would double-count the same money against Σ available. Splits can't exist on an
    investment txn (``kind`` is restricted to expense/income at the create choke point).

    A CATEGORIZED transfer folds by an XOR rule on which leg is on-budget (owner decision
    2026-07-03, retiring the old "transfers carry category=None" invariant — see
    docs/adr/2026-07-03-categorized-transfers.md): exactly ONE leg on-budget → the category
    absorbs that leg's own amount (from-leg on-budget → NEGATIVE, like a budgeted expense —
    e.g. a loan payment leaving on-budget cash to an off-budget lender; to-leg on-budget →
    POSITIVE, like an income refund — e.g. a reimbursement arriving from an off-budget
    counterparty). BOTH legs on-budget → folds NOTHING — the cash side already nets to zero
    moving money between two on-budget accounts, so the category is a pure label with no
    money to absorb (this read-time XOR rule, not any write-time restriction, is the entire
    safety mechanism the old invariant used to provide by force). NEITHER leg on-budget →
    folds nothing (not plan money). An UNCATEGORIZED transfer is excluded entirely — exactly
    today's behavior, unchanged: the cash side alone moves RTA, with no envelope to absorb it.
    The absorbed amount is the ON-BUDGET leg's OWN native amount converted to base (never
    ``txn_base_amount``, which always prefers the from-leg and would pick the WRONG leg when
    the to-leg is the on-budget one). Splits can't exist on a transfer (``kind`` is restricted
    to expense/income at the create choke point).

    A SPLIT txn (P3-SP) distributes each line to its OWN category via ``txn_split_base_amount``
    (a pure fraction of the leg base — the lines sum EXACTLY to the leg total, so no envelope
    double-counts). For a split EXPENSE every line subtracts; for a split INCOME a line whose
    category is LINKED to a live envelope adds (a refund), a line that is inflow-to-assign is
    skipped — mirroring the single-category behavior. A non-split txn folds its single leg as
    before. Refund classification keys on envelope LINKAGE (``env_of()``), not the broader
    "spending category" universe (RFC full-split §Backend model) — an income row categorized
    to an UNLINKED category is RTA inflow, never absorbed into a reserve that doesn't exist.
    It is excluded from THIS map for exactly that reason, but it does still reach ``cash_out``,
    where it cancels its own key's no-bucket hold — see ``fold_no_bucket_only``.

    ``cash_out`` is an out-parameter forwarded verbatim to ``_iter_category_contributions`` — see
    there: the GENUINELY-CASH half of this same fold (moving leg in ``ctx["cash_ids"]``), which
    the no-bucket hold reads. Filled by the time this returns (the loop below exhausts the
    generator)."""
    cutoff_by_id = cutoff_by_id or {}
    out: dict[str, float] = {}
    for cat, delta, _t, _s in _iter_category_contributions(
        txns,
        month,
        base,
        rates,
        plan_source_ids,
        cutoff_by_id,
        card_pay_env_of,
        ctx,
        cash_out,
    ):
        out[cat] = out.get(cat, 0.0) + delta
    return {c: round(v, 2) for c, v in out.items()}


def _card_leg_amount(row: dict, card_id: str, base: str, rates: dict) -> dict:
    """YNAB's sign convention for a payment-envelope popover row: the CARD ACCOUNT'S OWN leg
    amount, never the envelope's (capped/uncapped) contribution — a payment/return is
    ``+to_amount`` (base), a card spend is ``−from_amount`` (base). Returns ``{}`` (no-op) when
    ``card_id`` doesn't match either leg (an ordinary category/envelope row). ``row["base_amount"]``
    is already the from-leg's base value for a non-transfer row (set by the caller just above),
    so the spend branch reuses it instead of re-converting."""
    if row.get("to_account_id") == card_id:
        amt = svc.to_base(
            float(row.get("to_amount") or 0), row.get("to_currency") or base, base, rates=rates
        )
        return {"card_leg_amount": round(amt, 2), "txn_type": "payment"}
    if row.get("from_account_id") == card_id:
        return {"card_leg_amount": -row["base_amount"], "txn_type": "spend"}
    return {}


def _card_activity_transactions(
    ctx: dict, month: str, card: dict, env_id: str, kind: str | None
) -> dict:
    """CARD-CENTRIC Activity list behind a payment-envelope popover (owner 2026-07-10): EVERY
    non-deleted transaction on the card this month (from OR to leg, past the card's tracking-start
    cutoff), signed by the card's OWN leg (``_card_leg_amount`` — a payment/refund +, a spend −).
    This is "all transactions made with the card" — a SUPERSET of the payment envelope's own
    contributions: it also lists the card's spends (keyed to their SPENDING envelopes, not this
    one) and their merchant payees, matching the popover's left "Spending" column. The same
    two-column ``breakdown`` box rides along."""
    card_id = card["id"]
    cutoff = ctx["cutoff_by_id"].get(card_id)
    opening_ids = ctx["opening_ids"]
    rows: list[dict] = []
    for t in ctx["txns"]:
        if t.get("deleted") or not (t.get("date") or "").startswith(month):
            continue
        if kind and t.get("kind") != kind:
            continue
        if card_id not in (t.get("from_account_id"), t.get("to_account_id")):
            continue
        if svc.is_before_tracking_start(t.get("date"), cutoff):
            continue
        row = dict(t)
        if row.get("kind") != "transfer":
            row["base_amount"] = svc.txn_base_amount(row, ctx["base"], ctx["rates"])
            row["base_currency"] = ctx["base"]
        row["needs_review"] = bool(row.get("needs_review", False))
        row["source"] = display_source(
            row.get("source"), kind=row.get("kind"), is_opening=row.get("id") in opening_ids
        )
        row["activity_memo"] = row.get("memo") or ""
        leg = _card_leg_amount(row, card_id, ctx["base"], ctx["rates"])
        row.update(leg)
        row["activity_amount"] = leg.get("card_leg_amount", 0.0)
        rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    result: dict = {"transactions": rows, "excluded_count": 0}
    breakdown = payment_activity_breakdown(env_id, month, ctx)
    if breakdown is not None:
        result["breakdown"] = breakdown
    return result


def activity_transactions(category_ids: list[str], month: str, kind: str | None = None) -> dict:
    """Per-transaction breakdown behind one Activity-popover cell for ``month`` — reuses the
    SAME ``_iter_category_contributions`` fold ``_activity_by_category`` aggregates (on-budget
    guard, split fractions, categorized-transfer XOR, investment fold), so this list can never
    silently disagree with the row's own Activity total the way a plain
    ``GET /api/transactions?category=`` fetch (no plan-eligibility awareness at all) used to.
    Pass ``[""]`` for the blank-category (uncategorized) bucket, optionally narrowed
    by ``kind`` to isolate uncategorized income from Uncategorized expense (both share the "" key).

    Each returned transaction is the ledger dict re-based the SAME way ``GET /api/transactions``
    re-bases its rows (fresh ``base_amount``/``base_currency`` for non-transfers — the stored
    value is a stale snapshot; ``needs_review`` defaulted; ``source`` mapped onto the display
    vocab) — so opening one from the popover's detail drawer shows the same numbers opening it
    from the register would. Plus ``activity_amount`` (the SIGNED, category-scoped contribution
    — a split's own fraction, never the full leg) and ``activity_memo`` (the split line's own
    memo when the contribution came from one, else the transaction's). A split transaction contributing to two DIFFERENT
    requested categories (a merged envelope's members) appears once per contributing line, by
    design — mirrors how a merged row's Activity is itself a genuine sum across members.

    When a requested id resolves to a LIVE payment envelope, each row ALSO carries
    ``card_leg_amount``/``txn_type`` (``_card_leg_amount`` — the YNAB-display sign, see its
    docstring) and the response carries a top-level ``breakdown`` (``payment_activity_breakdown``
    — the two-column Spending/Returns vs Funded Spending/Payments & Returns box). Both are
    display-only additions; an ordinary category/envelope fetch is byte-identical to before.

    ``excluded_count`` is how many transactions matched ``category_ids``+``month`` but
    contributed nothing (e.g. dated before the account's tracking-start anchor, on an
    off-budget account, or a categorized transfer whose legs are both/neither on-budget) — so
    the UI can say "N more this month didn't count toward Activity" instead of just going quiet
    about the gap between the list and the total."""
    _valid_month(month)
    ctx = _build_context(month)
    # `_CARD_UNCOVERED_KEY` is an internal RTA-identity-only term, never a real category — a
    # caller asking for it by name gets nothing rather than a feed of rows filed under a key no
    # category picker can produce (same exclusion `_unbudgeted_lines` makes).
    wanted = set(category_ids) - {_CARD_UNCOVERED_KEY}
    payment_accounts = _payment_env_accounts(ctx)
    # Payment-envelope Activity is CARD-CENTRIC (owner 2026-07-10): list EVERY transaction on the
    # card this month (spends −, payments +, refunds +) — "all transactions made with the card" —
    # not just this envelope's own contributions, so a card spend (keyed to its spending envelope)
    # and its merchant payee show up too, matching the popover's left "Spending" column. Ordinary
    # category/envelope queries fall through unchanged.
    card_env = next(((c, payment_accounts[c]) for c in wanted if c in payment_accounts), None)
    if card_env is not None:
        return _card_activity_transactions(ctx, month, card_env[1], card_env[0], kind)
    rows: list[dict] = []
    counted_ids: set[str] = set()
    opening_ids = ctx["opening_ids"]
    for cat, delta, t, split in _iter_category_contributions(
        ctx["txns"],
        month,
        ctx["base"],
        ctx["rates"],
        ctx["plan_source_ids"],
        ctx["cutoff_by_id"],
        ctx["card_pay_env_of"],
        ctx,
    ):
        if cat not in wanted or (kind and t.get("kind") != kind):
            continue
        counted_ids.add(t["id"])
        row = dict(t)
        if row.get("kind") != "transfer":
            row["base_amount"] = svc.txn_base_amount(row, ctx["base"], ctx["rates"])
            row["base_currency"] = ctx["base"]
        row["needs_review"] = bool(row.get("needs_review", False))
        row["source"] = display_source(
            row.get("source"), kind=row.get("kind"), is_opening=row.get("id") in opening_ids
        )
        row["activity_amount"] = round(delta, 2)
        # The Activity popover's Memo column: the split line's own memo (named ``note`` until
        # 2026-08-02 — older rows still carry it), else the transaction's; never the bank's
        # description string (owner, 2026-09-25).
        split_memo = ((split.get("memo") or split.get("note")) if split else None) or ""
        row["activity_memo"] = split_memo or row.get("memo") or ""
        card = payment_accounts.get(cat)
        if card is not None:
            row.update(_card_leg_amount(row, card["id"], ctx["base"], ctx["rates"]))
        rows.append(row)

    def _matches_scope(t: dict) -> bool:
        if kind and t.get("kind") != kind:
            return False
        if (t.get("category") or "") in wanted:
            return True
        return any((s.get("category") or "") in wanted for s in (t.get("splits") or []))

    excluded_count = sum(
        1
        for t in ctx["txns"]
        if not t.get("deleted")
        and (t.get("date") or "").startswith(month)
        and t["id"] not in counted_ids
        and _matches_scope(t)
    )
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    result = {"transactions": rows, "excluded_count": excluded_count}
    if len(wanted) == 1:
        breakdown = payment_activity_breakdown(next(iter(wanted)), month, ctx)
        if breakdown is not None:
            result["breakdown"] = breakdown
    return result


# ── Bounds: how far back the rollover walk runs ──────────────────────────────────


def _earliest_data_month(txns: list[dict]) -> str | None:
    """Earliest month that carries any data (a txn or an assignment). None on an empty book."""
    months: list[str] = []
    cfg_start = budgets_service.read_budget_config().get("rollover_start")
    if cfg_start:
        months.append(cfg_start)
    months.extend(assignments_store.months())
    for t in txns:
        d = t.get("date") or ""
        if not t.get("deleted") and len(d) >= 7:
            months.append(d[:7])
    return min(months) if months else None


def _floor_month(txns: list[dict], floor_override: str | None = None) -> str:
    """The month at/after which the recursive walk runs. ``rollover_start`` wins when set
    (mirrors ``services.budgets``); else the first month with data; else this month.

    ``floor_override`` outranks BOTH — the seam ``fresh_start_preview`` runs the real fold at
    a hypothetical floor without writing ``rollover_start`` first. Read-only by construction:
    the override is a call argument, never persisted."""
    cfg_start = floor_override or budgets_service.read_budget_config().get("rollover_start")
    if cfg_start:
        return cfg_start
    return _earliest_data_month(txns) or _this_month()


# ── Available + Ready-to-Assign (recursive, memoized) ────────────────────────────


def _build_context(
    month: str, floor_override: str | None = None, zero_month_assignments: bool = False
) -> dict:
    """Resolve the inputs the recursive walks share once (txns, FX, floor, caches).

    ``floor_override`` (YYYY-MM) computes the whole table at a HYPOTHETICAL carry floor —
    ``fresh_start_preview``'s seam. Default None = the stored ``rollover_start``.

    ``zero_month_assignments`` drops ``month``'s assignment map from the loaded snapshot, so the
    whole table folds as if nothing had been assigned that month — the OTHER half of the same
    preview (a fresh start now clears the month's assignments too). Applied to the in-memory
    snapshot only, before any walk memoizes: nothing is written, and every other month keeps its
    real assignments so the carry into ``month`` is still the true one."""
    _valid_month(month)
    base = svc.base_currency()
    rates = load_fx_cache()
    txns = svc.transactions.all()
    floor = _floor_month(txns, floor_override)
    assigned_by_month = assignments_store.all_map()
    if zero_month_assignments:
        assigned_by_month.pop(month, None)
    # Balance-based RTA: the account universe, split by ROLE (ADR
    # 2026-07-09-ynab-engine-credit-card-payment-envelopes.md) — cash_ids feeds the cash side of
    # the identity (archived INCLUDED — a closed account's balance is 0 going forward but real
    # in past months, so history stays correct); plan_source_ids gates envelope activity (a leg
    # must be on a KNOWN plan-source account to fill an envelope — see plan_leg_ok). A
    # payment-tracked card is in plan_source_ids but NOT cash_ids (its spending fills a category
    # while its balance leaves the cash sum — ADR 2026-07-09). Per-account tracking-start
    # anchors gate both sides symmetrically.
    accounts = svc.accounts.all()
    cash_ids = frozenset(a["id"] for a in accounts if is_cash_account(a))
    plan_source_ids = frozenset(a["id"] for a in accounts if is_plan_source(a))
    # Every payment-tracked card account (any `type=="credit"` row — is_payment_tracked_card is
    # type-only, so this INCLUDES archived ones, unlike card_pay_env_of below) — the coverage-cap
    # membership test in _iter_category_contributions (ADR
    # 2026-09-28-iterative-available-walk-and-uniform-card-coverage): whether an outflow is "card
    # spend" at all must not depend on whether a LIVE payment envelope currently resolves for it.
    card_ids = frozenset(a["id"] for a in accounts if is_payment_tracked_card(a))
    # {account_id: payment_envelope_id} for every card with a LIVE payment-envelope FK (boot syncs
    # one per on-budget card) — drives the auto-move fold in _iter_category_contributions.
    card_pay_env_of: dict[str, str] = {}
    for a in accounts:
        fk = a.get("payment_envelope_id")
        if fk:
            card_pay_env_of[a["id"]] = fk
    cutoff_by_id = {aid: cut for aid, cut in svc.cutoffs_by_account(accounts, txns).items() if cut}
    opening_ids = svc.opening_txn_ids()
    cfg = budgets_service.read_budget_config()
    baseline = cfg.get("pre_budget_baseline") or 0.0
    rows = _budget_rows()
    row_of_cat: dict[str, list[str]] = {}
    for spec in rows:
        row_of_cat[spec["envelope_id"]] = spec["category_ids"] or [spec["envelope_id"]]
    return {
        "base": base,
        "rates": rates,
        "txns": txns,
        "floor": floor,
        "accounts": accounts,
        "cash_ids": cash_ids,
        "plan_source_ids": plan_source_ids,
        "card_ids": card_ids,
        "card_pay_env_of": card_pay_env_of,
        "cutoff_by_id": cutoff_by_id,
        # The accounts' Starting Balance row ids — the ONE opening marker, resolved once per
        # context for the RTA bucket split and the Activity display projection.
        "opening_ids": opening_ids,
        "baseline": float(baseline),
        # The STORED carry floor, echoed for display ("Fresh start · since …"). Distinct from
        # "floor" above, which is the EFFECTIVE floor (falls back to the first data month, and
        # is what a preview overrides).
        "rollover_start": cfg.get("rollover_start"),
        "_rows": rows,  # envelope row-specs — one per non-archived envelope
        "_row_of_cat": row_of_cat,  # envelope_id → its row's category_ids (activity fold); a
        # zero-category envelope folds against its own id — harmless for a Commitments envelope
        # (nothing ever posts there), load-bearing for a payment envelope (the auto-move fold
        # posts directly against its own id).
        "_activity": {},  # month → {cat: net}
        # month → {cat: net} over legs that genuinely moved CASH (moving account in cash_ids —
        # the same set _cash_as_of sums) — the no-bucket hold's input, filled by the same pass as
        # "_activity". NOT the coverage cap's pool, which admits credit legs: see
        # _iter_category_contributions.
        "_cash_activity": {},
        # month → {category_or_"": held} for every key with NO envelope — see _no_bucket_held.
        "_no_bucket": {},
        "_available": {},  # (month, envelope_id) → available
        "_rta": {},  # month → ready_to_assign
        "_cash": {},  # month → cash (base, CURRENT rate) as of month end
        "_txns_le": {},  # month → the dated-in-or-before-month txn slice both balance folds share
        "_acct_bal": {},  # (month, account_id) → that account's signed base balance at month end
        # Every assignment row, loaded ONCE per context: month → {envelope_id: amount}.
        # _available's carry walk reads assigned at every (month × envelope) node; through
        # _assigned_amt each node was its own month_map() SELECT — 661 of /api/planning/
        # summary's 727 statements re-fetched ~20 distinct month maps (live attribution,
        # 2026-08-12). Same shape as "txns"/"rates" above: one read threaded through the
        # request, never a cross-request cache.
        "_assigned_by_month": assigned_by_month,
    }


def _ctx_assigned(ctx: dict, month: str, envelope_id: str) -> float:
    """Assigned for (month, envelope_id) off the context's one-shot load — the in-context
    equivalent of ``_assigned_amt`` (which stays the direct store read for write paths,
    where a fresh read at the choke is the point)."""
    return ctx["_assigned_by_month"].get(month, {}).get(envelope_id, 0.0)


def _activity_map(ctx: dict, month: str) -> dict:
    """Net activity per category for ``month``. Also fills ``ctx["_cash_activity"][month]`` — the
    GENUINELY-CASH half of the SAME fold (see ``_iter_category_contributions``'s ``cash_out``) —
    in the same pass, so ``_cash_activity_map`` never walks the ledger a second time."""
    cache = ctx["_activity"]
    if month not in cache:
        cash_out: dict[str, float] = {}
        cache[month] = _activity_by_category(
            ctx["txns"],
            month,
            ctx["base"],
            ctx["rates"],
            ctx["plan_source_ids"],
            ctx["cutoff_by_id"],
            ctx["card_pay_env_of"],
            ctx,
            cash_out,
        )
        ctx["_cash_activity"][month] = {c: round(v, 2) for c, v in cash_out.items()}
    return cache[month]


def _cash_activity_map(ctx: dict, month: str) -> dict:
    """``_activity_map``'s CASH-ONLY twin: only contributions whose MOVING leg sits on an account
    in ``ctx["cash_ids"]`` — the exact set ``_cash_as_of`` sums, so a dollar this map reserves is
    a dollar that side counted. The no-bucket hold (``_no_bucket_held``) reads this and never the
    full map: a spend on ANY ``credit`` account has no cash to hold back (its debt lives on the
    card, surfaced by the payment row's ``underfunded``), so holding it here would charge the same
    swipe on both sides — or, for a card outside the payment-envelope fold (archived, just
    retyped, or paying an off-budget account by transfer), invent Ready to Assign outright.

    It is NOT a plain subset of the full map: an inflow on an unlinked category is here and not
    there (``fold_no_bucket_only``), so a hold can be cancelled by its own refund without that
    refund becoming Activity on an envelope that doesn't exist."""
    _activity_map(ctx, month)  # fills the cash twin as a side effect of that one pass
    return ctx["_cash_activity"][month]


def _row_activity(ctx: dict, month: str, category_ids: list[str]) -> float:
    """Net activity (base ccy) folded across every category in ONE budget row (an envelope's
    members, or a single no-envelope category) for ``month`` — the ONE fold every row-level
    reader (``_available``, ``budget_month``, auto-assign's history modes) shares, so a
    merged envelope's Activity/history is computed identically everywhere. A category
    belongs to at most one row, so this never double-counts."""
    return round(sum(_activity_map(ctx, month).get(c, 0.0) for c in category_ids), 2)


def _unsorted_activity(ctx: dict, month: str) -> float:
    """Plan activity carrying NO category at all (the blank/uncategorized bucket) — money the
    user never labeled, shown as the long-standing "Uncategorized" display row. The DISPLAY figure:
    cash and card spend together, which is what the row's Activity column means. What that
    bucket HOLDS is a different (cash-only) number — ``_no_bucket_held``."""
    return round(_activity_map(ctx, month).get("", 0.0), 2)


def _no_bucket_held(cash_activity: float) -> float:
    """What a NO-BUCKET pseudo-envelope holds for one month: uncategorized spend,
    or spend on a labeled category linked to no envelope ("Unbudgeted"). ADR
    2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end.

        held(m) = min(0, max(0, held(m−1)) + cash_activity(m))

    The same CLAMPED CARRY every envelope gets in ``_available`` (``max(0, prev) + assigned +
    activity``) with ``assigned`` ≡ 0 — nothing can be assigned to a bucket that isn't an
    envelope. So a hole is HELD in-month (Ready to Assign does not move when a spend has no
    home) and RESET at the roll, charging it to the next month's RTA exactly once, identical to
    an overspent envelope. Categorizing the spend before month-end makes the hold evaporate,
    identical to covering that envelope.

    The one clamp an envelope does NOT have is the upper one, and it is what collapses the
    recursion: a no-bucket key can never hold a SURPLUS. Nothing can be assigned to it, so a
    positive cash fold there (a categorized transfer arriving on-budget, an investment sell) is
    unassigned cash and belongs to RTA — the same rule ``_activity_by_category`` already applies
    to income landing on an unlinked category. With the value pinned ≤ 0, ``max(0, held(m−1))``
    is provably 0 for every month, which is why this takes one month's activity and no prior
    walk: the reset IS the whole carry.

    ``cash_activity`` must come from ``_cash_activity_map`` — every leg on a non-``cash`` account
    is excluded, because a card never had cash to lose (see that function). Not merely a
    payment-TRACKED card's: an archived card, or one whose payment envelope has not been synced
    yet, is just as much "not cash", and reserving against it fabricated RTA (security review,
    2026-09-10)."""
    return min(0.0, round(cash_activity, 2))


def _unsorted_available(ctx: dict, month: str) -> float:
    """What the Uncategorized (blank-category) bucket HOLDS this month — ``_no_bucket_held`` over its
    cash-only activity. ≤ 0 always: only spending lands here (uncategorized INCOME is
    inflow-to-assign and never folds into the "" key at all)."""
    return _no_bucket_held(_cash_activity_map(ctx, month).get("", 0.0))


def _unbudgeted_keys(ctx: dict, month: str) -> list[str]:
    """Every LABELED category with month activity and NO linked envelope (RFC full-split
    §Backend 6) — the key set behind both ``_unbudgeted_lines`` (display) and
    ``_unbudgeted_available_map`` (the held term), so the two can't diverge. Excludes the blank
    key (Uncategorized's own territory), any live envelope's OWN row id (a zero-category envelope,
    e.g. a payment envelope, can receive a DIRECT fold keyed by its own id — see
    ``_iter_category_contributions`` — which is by definition already budgeted via that row,
    never "unbudgeted"), ``_CARD_UNCOVERED_KEY`` (an internal RTA-identity-only term, never a
    real category — see ``_capped_card_credits``), and any key with neither activity to show nor
    a hole to hold.

    BOTH folds decide inclusion, because the row set serves two masters that DISAGREE. Full
    activity is the DISPLAY test: a card-only spend on an unlinked category is real and must
    still be listed (it holds nothing — Available reads 0, the debt lives on the card). Cash-only
    activity is the HELD test, and it is deliberately ONE-SIDED: ``_no_bucket_held`` clamps a
    surplus to 0, so only a NEGATIVE cash fold can hold anything, and a lone inflow under an
    unlinked label still mints no row (exactly as before).

    Filtering on full activity ALONE was a money bug (review, 2026-09-10). The claim it rested on
    — "card spend is never positive, so a key with negative cash activity always clears this
    filter too" — is FALSE, because an EXPENSE is not the only thing that lands on a category: a
    categorized TRANSFER arriving at an on-budget account folds POSITIVE (the XOR rule in
    ``_iter_category_contributions``), and so does a categorized investment SELL. Land either at a
    ``credit`` account under the same label as a real cash expense and the full fold nets to ~0
    while the cash fold still holds the whole expense — so the key was dropped from the map AND
    from the display, and Ready to Assign was drained by exactly what should have been held, with
    nothing on screen to show for it.

    Inclusion only widens WHICH keys get asked; the held VALUE still comes from the cash-only map
    alone (``_unbudgeted_available_map``)."""
    linked = envelopes_service.env_of()
    row_ids = {spec["envelope_id"] for spec in ctx["_rows"]}
    activity = _activity_map(ctx, month)
    cash = _cash_activity_map(ctx, month)
    return [
        cat
        for cat in dict.fromkeys([*activity, *cash])
        if cat
        and cat != _CARD_UNCOVERED_KEY
        and cat not in linked
        and cat not in row_ids
        and (abs(activity.get(cat, 0.0)) >= 0.005 or cash.get(cat, 0.0) <= -0.005)
    ]


def _unbudgeted_available_map(ctx: dict, month: str) -> dict[str, float]:
    """``{category: held}`` for every LABELED category with NO linked envelope — one
    ``_no_bucket_held`` per category, never one over their sum: a category refunded into the
    black must not cancel another's still-open hole. Keyed identically to ``_unbudgeted_lines``
    (same exclusions), so every displayed row has a held figure and vice versa. The VALUE is
    read from ``_cash_activity_map`` and nowhere else — ``_unbudgeted_keys`` admits a key on
    EITHER fold, but a key admitted on its full activity alone still holds only what left a cash
    account. Memoized."""
    cache = ctx["_no_bucket"]
    if month not in cache:
        cash = _cash_activity_map(ctx, month)
        cache[month] = {
            cat: _no_bucket_held(cash.get(cat, 0.0)) for cat in _unbudgeted_keys(ctx, month)
        }
    return cache[month]


def _held_outside_envelopes(ctx: dict, month: str) -> float:
    """The identity's no-bucket term: Uncategorized + every unlinked category, each holding its own
    hole until the roll. SIGNED and ≤ 0 — it only ever hands Ready to Assign room BACK."""
    return round(
        _unsorted_available(ctx, month) + sum(_unbudgeted_available_map(ctx, month).values()), 2
    )


def _unbudgeted_lines(ctx: dict, month: str) -> list[dict]:
    """Per-category Activity + Available for LABELED categories with NO linked envelope: "labeled
    but unbudgeted" — surfaced so it's visible and actionable. ``available`` is what the category
    HOLDS (``_no_bucket_held``), not its activity: the two differ whenever the spend was on a
    card (held 0 — the debt is the card's) or partly refunded."""
    label_map = _category_label_map()
    held = _unbudgeted_available_map(ctx, month)
    activity = _activity_map(ctx, month)
    lines = [
        {
            "key": cat,
            "label": _category_label(cat, label_map),
            "activity": round(activity.get(cat, 0.0), 2),
            "available": held.get(cat, 0.0),
        }
        for cat in _unbudgeted_keys(ctx, month)
    ]
    return sorted(lines, key=lambda ln: ln["label"])


def _txns_as_of(ctx: dict, month: str) -> list[dict]:
    """Every DATED transaction in/before ``month``, sliced ONCE per month per context. A
    dateless row is data corruption and is excluded from BOTH sides of the identity. Shared by
    the cash fold and the per-account fold — rebuilding it per account walked the whole ledger
    once per card on every payment-row pass."""
    cache = ctx["_txns_le"]
    if month not in cache:
        cache[month] = [
            t for t in ctx["txns"] if t.get("date") and (t.get("date") or "")[:7] <= month
        ]
    return cache[month]


def _cash_as_of(ctx: dict, month: str) -> float:
    """Σ SIGNED balances (base ccy) of every CASH account (``cash_ids``) as of the END of
    ``month`` — the cash side of the RTA identity. Only DATED txns in/before ``month`` count (a
    dateless row is data corruption — excluded from BOTH sides of the identity), and an account
    whose tracking-start anchor lies in a LATER month contributes nothing yet (its
    opening balance must not back-date into history — verify-lens fix).

    A payment-tracked card is excluded from ``cash_ids`` entirely (its debt is funded by a real
    payment envelope, never by this cash sum). This is the
    only credit-card engine; the old "signed" mode, where card debt netted against cash, was
    removed 2026-07-10.

    Converted at the CURRENT rate (``ctx['rates']``). Since the historical-rate lock (ADR
    2026-07-09) envelope ACTIVITY is locked at each txn's transaction-date rate while this cash
    side still floats, a foreign-cash FX gain flows into RTA — current-value budgeting, by owner
    decision: an appreciated held balance is immediately assignable. The budget just NOTES this
    can happen when foreign cash is held (``has_foreign_cash``); RTA itself is unchanged.
    Memoized."""
    cache = ctx["_cash"]
    if month not in cache:
        txns_le = _txns_as_of(ctx, month)
        total = 0.0
        for a in ctx["accounts"]:
            if a["id"] not in ctx["cash_ids"]:
                continue
            cutoff = ctx["cutoff_by_id"].get(a["id"])
            if cutoff and cutoff[:7] > month:
                continue  # the account's asserted opening balance doesn't exist yet
            bal = svc.account_balance(a, txns_le, cutoff)
            total += svc.to_base(
                bal, a.get("currency") or ctx["base"], ctx["base"], rates=ctx["rates"]
            )
        cache[month] = round(total, 2)
    return cache[month]


def _account_balance_base_as_of(ctx: dict, month: str, account: dict) -> float:
    """One account's SIGNED balance (base ccy) as of month-end — the single-account version of
    ``_cash_as_of``'s per-account fold, used by the payment-envelope row's ``underfunded``
    figure (rule 4/5, ADR 2026-07-09-ynab-engine-credit-card-payment-envelopes.md): the card's
    TRUE current debt, independent of whether it ever posted through the plan (this is what lets
    a pre-existing/starting-balance adjustment — invisible to the envelope auto-move, by design —
    still surface correctly via ``underfunded`` instead of silently diverging). An account whose
    tracking-start anchor lies in a LATER month contributes 0 (its opening balance
    doesn't exist yet).

    Memoized per (month, account) — ``_payment_row_fields`` runs this over every payment-tracked
    card, and both ``_ready_to_assign`` and ``budget_month``'s payload ask for the same figure."""
    key = (month, account["id"])
    cache = ctx["_acct_bal"]
    if key in cache:
        return cache[key]
    cutoff = ctx["cutoff_by_id"].get(account["id"])
    if cutoff and cutoff[:7] > month:
        cache[key] = 0.0
        return 0.0
    bal = svc.account_balance(account, _txns_as_of(ctx, month), cutoff)
    val = round(
        svc.to_base(bal, account.get("currency") or ctx["base"], ctx["base"], rates=ctx["rates"]),
        2,
    )
    cache[key] = val
    return val


def _has_foreign_cash(ctx: dict) -> bool:
    """True if any on-budget cash account holds a non-base currency — the ONLY signal the budget
    needs to note that Ready-to-Assign can shift as exchange rates move (foreign cash floats at
    the current rate). Deliberately NOT a computed drift amount: for a personal budget the amount
    is sub-dollar noise, was fragile to compute (a running-balance cost basis blew up to nonsense
    on a churned account), and is display-only. A plain qualitative note is enough. ADR
    2026-07-09-foreign-currency-historical-rate-lock."""
    base = ctx["base"]
    return any(
        (a.get("currency") or base).upper() != base
        for a in ctx.get("accounts", [])
        if a["id"] in ctx["cash_ids"]
    )


def _available(ctx: dict, month: str, envelope_id: str) -> float:
    """available(m, env) = max(0, available(m-1, env)) + assigned(m, env) + activity(m, env),
    where activity is the GENUINE SUM over every category folded into ``env``'s row — a category
    belongs to at most one envelope, so folding never double-counts — and assigned is the ONE
    envelope-keyed read.

    The prior month's carry is CLAMPED at 0 (ADR 2026-09-09, restoring ADR 2026-07-02): a
    negative Available does NOT roll forward as debt — it resets, and the deficit is charged to
    next month's Ready to Assign as the cash side's own drop stops being offset by the hole. An
    overspend is held IN-MONTH (see ``_ready_to_assign``) and charged exactly once, at the roll.

    Bounded below by ``ctx['floor']``; memoized per (month, envelope_id). Walked ITERATIVELY, not
    via Python recursion (ADR 2026-09-28-iterative-available-walk-and-uniform-card-coverage): one
    stack frame per month walked back used to blow the interpreter's own recursion limit long
    before any of this module's own caps did — a `rollover_start` set to "1900-01" is ~1,500
    months of real recursion against a default limit of 1,000 — and `_capped_card_credits`
    re-entering this function for a DIFFERENT envelope mid-walk (to resolve a card's coverage
    cap) stacked MORE frames on top of whatever depth the outer walk was already at, so the worst
    case was never simply "however many months." An explicit list stands in for the call stack
    instead: total work is still O(months walked), but bounded by memory, never by CPython's
    recursion ceiling — so an arbitrarily old floor is correct, not just uncapped-but-crashy."""
    key = (month, envelope_id)
    cache = ctx["_available"]
    if key in cache:
        return cache[key]
    # Walk backward from `month`, collecting every not-yet-memoized month until one is cached OR
    # at/before the floor (`m = None` marks that base case — no earlier month to read at all).
    # Then fold FORWARD from the oldest, seeding `prev_carry` from whichever base case applied.
    pending: list[str] = []
    m: str | None = month
    while (m, envelope_id) not in cache:
        pending.append(m)
        if m <= ctx["floor"]:
            m = None
            break
        m = _prev_month(m)
    prev_carry = 0.0 if m is None else max(0.0, cache[(m, envelope_id)])
    members = ctx["_row_of_cat"].get(envelope_id, [envelope_id])
    for cur in reversed(pending):
        assigned = _ctx_assigned(ctx, cur, envelope_id)
        activity = _row_activity(ctx, cur, members)
        val = round(prev_carry + assigned + activity, 2)
        cache[(cur, envelope_id)] = val
        prev_carry = max(0.0, val)
    return cache[key]


def _ready_to_assign(ctx: dict, month: str) -> float:
    """Ready to Assign follows YNAB (ADR 2026-09-09, restoring ADR 2026-07-02 + 2026-07-09):

        ready_to_assign(m) = cash_as_of(m) − pre_budget_baseline − Σ_env available(m, env)
                                                                   − held_outside_envelopes(m)
                                                                   − card_uncovered(m)

    ``Σ_env available`` is the TRUE per-envelope value for ``m`` — SIGNED, so a current-month
    overspend counts NEGATIVE (the ``max(0, ·)`` clamp lives only in the month-to-month CARRY, in
    ``_available``, never here). ``card_uncovered(m)`` is this month's uncovered card-spend total
    (``_CARD_UNCOVERED_KEY`` in the activity map) — see ``_capped_card_credits`` for why it must
    cancel the spending row's own drain rather than being left unmatched.
    ``held_outside_envelopes(m)`` is the no-bucket term (``_held_outside_envelopes``): CASH spend
    with no envelope to land in — uncategorized, or a category linked to none — holding its own
    hole exactly like an envelope does, ≤ 0 always.

    An envelope HOLDS its own overspend until month-end: RTA does not move when a category goes
    red, cash or card. At the roll, ``_available``'s clamped carry resets a still-negative
    envelope to 0 — the hole is charged to next month's RTA (it falls straight out of the
    identity: for a cash overspend, cash already dropped and the carry no longer offsets it; a
    card overspend's debt keeps living on the card via its own payment envelope, never
    re-charged).

    CASH SPEND WITH NO BUCKET HOLDS THE SAME WAY (ADR 2026-09-10, amending 2026-09-09's one
    remaining divergence): uncategorized spend and spend on a category linked to no
    envelope are held by ``_no_bucket_held`` under the SAME clamped-carry rule, then reset and
    charged at the roll if still unresolved. 2026-09-09 drained them immediately on indirect
    research about YNAB; direct observation showed YNAB holds them, via its own uncategorized
    pseudo-bucket. "No bucket" is therefore not a third treatment — it is an envelope-shaped
    holder with nothing assignable to it.

    Consequences:
      * income (any account-bound inflow) raises RTA — no kind filter;
      * a FUNDED spend is RTA-neutral (cash and its envelope's available drop together; a card
        swipe auto-moves the covered amount into its payment envelope — see
        ``_capped_card_credits``);
      * an UNFUNDED cash spend holds (RTA unmoved now, charged at the roll if still red) —
        whether the hold is an envelope's own negative row or a no-bucket bucket's;
      * an UNFUNDED card spend is RTA-neutral now AND at the roll — the debt lives on the card,
        never charged to RTA (``card_uncovered`` cancels the spending row's own drain here; the
        real gap surfaces via the payment row's own ``underfunded`` figure);
      * COVERING an overspent envelope COSTS RTA, up to the hole's size — assigning money there
        is what resolves the hold;
      * an UNCATEGORIZED transfer to a tracking account, or an uncategorized investment buy/sell,
        moves RTA by the cash — nothing absorbs it (both fold no category at all). Categorize
        it and it behaves like any other spend: a LINKED category's envelope absorbs it, and
        since ADR 2026-09-10 an UNLINKED one holds it too, so RTA no longer moves at the
        transaction either way;
      * standing/legacy card debt does NOT reduce RTA (a card's payment envelope starts at 0) —
        the user pays it down by assigning toward the payment envelope over time.

    RTA is NOT bounded above by cash − baseline (ADR 2026-09-09 retires that invariant) — a held
    overspend can put RTA above cash on hand until the roll resolves it. The identity instead
    closes EXACTLY: RTA + Σ_env available(signed) + held_outside_envelopes + card_uncovered +
    baseline == cash_as_of, always (pinned by tests/test_rta_identity_property.py).

    Months strictly BEFORE the floor return 0 — the budget doesn't exist yet, and returning the
    raw opening balances there would let any pre-history month claim the whole bank as
    assignable (matches the "month before any data returns all-zeros" contract + the UI's
    can_go_back nav gate). Memoized per month."""
    cache = ctx["_rta"]
    if month in cache:
        return cache[month]
    if month < ctx["floor"]:
        cache[month] = 0.0
        return 0.0
    # Sum over ROWS (one envelope, one term), NEVER over every member — `_available` already
    # folds a row's members, so summing per raw category here would double-count a merged
    # envelope's assigned money the same number of times as it has members.
    reserved = sum(_available(ctx, month, spec["envelope_id"]) for spec in ctx["_rows"])
    reserved += _activity_map(ctx, month).get(_CARD_UNCOVERED_KEY, 0.0)
    reserved += _held_outside_envelopes(ctx, month)
    val = round(_cash_as_of(ctx, month) - ctx["baseline"] - round(reserved, 2), 2)
    cache[month] = val
    return val


def _cash_overspending(ctx: dict, month: str) -> float:
    """Ov(m) − C(m) − Ho(m) — this month's holes, still open right now, that will be FORGIVEN
    at the roll and charged to NEXT month's Ready to Assign if they are still unresolved then.
    ``Ov(m)`` (Σ over envelope rows of the pre-carry overspend) contains BOTH cash and card
    overspending; ``−C(m)`` (this month's uncovered card-spend activity) and ``−Ho(m)`` (the
    no-bucket hold, itself ≤ 0) are what make the result CASH-only. Provably ≥ 0 on every book
    (design doc §2.4: an envelope's card-caused overspend cancels exactly against `C(m)`) — that
    theorem is what lets this be labelled "Cash Overspending" the way YNAB's own does. Called
    twice per request at most (this month + previous month, from `_flow_terms`); not memoized,
    the same policy `_cash_as_of` itself would use if its callers were this infrequent."""
    ov = sum(max(0.0, -_available(ctx, month, spec["envelope_id"])) for spec in ctx["_rows"])
    card = _activity_map(ctx, month).get(_CARD_UNCOVERED_KEY, 0.0)
    held_outside = _held_outside_envelopes(ctx, month)
    return round(ov - card - held_outside, 2)


def _flow_terms(ctx: dict, month: str) -> dict:
    """The stock identity (`_ready_to_assign`), reframed as YNAB's own flow terms — an exact
    algebraic rearrangement (design doc §2.3), never a second independent computation:

        RTA(m) = carried_in + inflow − assigned(m) − cash_overspending_prev − assigned_in_future(m)

    ``carried_in`` is last month's Ready to Assign BEFORE its own future-assigned clamp — the
    same quantity `_ready_to_assign` always returns. ``inflow`` is net cash movement this month
    that no bucket absorbed: on-budget cash's own change, minus this month's envelope activity,
    minus this month's no-bucket hold, minus this month's uncovered card spend. It is computed
    from its OWN four inputs and must NEVER be back-solved from `rta − everything_else` — that
    would make every check built on top of this function vacuous.

    AT the floor month (``_available`` forces the carry to start at 0 there — there is no real
    "previous month" for the walk to read): ``carried_in`` and ``cash_overspending_prev`` are
    genuinely 0, not missing, and ``inflow`` folds ``baseline`` in directly — real YNAB shows
    the SAME flow framing for its own first month (owner-supplied screenshot, 2026-09-14: no
    distinct "first month" vocabulary exists), so this is not a fallback state, it is month 1
    of the walk. Every OTHER month, ``baseline`` cancels inside ``carried_in`` and never
    reappears (design doc §7); AT the floor there is no ``carried_in`` for it to cancel into,
    so it has to land in ``inflow`` instead — the one place its absence would otherwise break
    the closure. Called for every month `>= ctx["floor"]`; a month strictly BEFORE the floor
    has no budget at all (`_ready_to_assign` returns 0 there) and never reaches this function."""
    row_activity_total = round(
        sum(
            _row_activity(
                ctx, month, ctx["_row_of_cat"].get(spec["envelope_id"], [spec["envelope_id"]])
            )
            for spec in ctx["_rows"]
        ),
        2,
    )
    held_outside = _held_outside_envelopes(ctx, month)
    card_uncovered = _activity_map(ctx, month).get(_CARD_UNCOVERED_KEY, 0.0)
    prev = _prev_month(month)
    if month <= ctx["floor"]:
        cash_now = _cash_as_of(ctx, month)
        inflow = round(
            cash_now - ctx["baseline"] - row_activity_total - held_outside - card_uncovered, 2
        )
        return {
            "prev_month": prev,
            "carried_in": 0.0,
            "inflow": inflow,
            "cash_overspending_prev": 0.0,
        }
    carried_in = _ready_to_assign(ctx, prev)
    delta_cash = round(_cash_as_of(ctx, month) - _cash_as_of(ctx, prev), 2)
    inflow = round(delta_cash - row_activity_total - held_outside - card_uncovered, 2)
    return {
        "prev_month": prev,
        "carried_in": carried_in,
        "inflow": inflow,
        "cash_overspending_prev": _cash_overspending(ctx, prev),
    }


# ── Per-category target → "needed this month" ────────────────────────────────────


TARGET_TYPES = ("monthly", "weekly", "yearly", "by_date")


def needed_this_month(
    envelope_id: str,
    available_now: float,
    assigned_now: float,
    target: dict | None,
    month: str,
    assigned_to_date: float = 0.0,
    activity: float = 0.0,
) -> dict:
    """The funding status for a budget row given its target + this month's Assigned / Available.
    ``envelope_id`` is unused (the math is target/assigned/available only) — kept for a
    consistent call signature with the row facades.

    Returns ``{target, needed_this_month, status}`` where status ∈
    {none, funded, underfunded, snoozed}. ``needed`` is what to ADD this month to satisfy the
    target. A recurring "set aside X" target (``needs_whole_amount=True``) measures what was
    **assigned this month** — once you've assigned the target it's funded, even after you SPEND it
    (a rent bill you fund $2,150 and pay $2,150 is funded, not "still $2,000 short"). A recurring
    "fill up to X" target (``needs_whole_amount=False``) measures what you **funded this month**
    (rollover + assigned = ``available − activity``), so spending it down doesn't re-open it
    mid-month — it refills next month. Only the savings-balance ``by_date`` target measures the
    running Available (spending a savings pot sets you back).
    Both Assigned and Available are base-currency-correct (Available is recomputed from native legs):
      - monthly: max(0, amount − assigned_now)  — set aside X this month. An optional
                 ``due_day`` (1-28, or -1 = last day of the month) is stored + echoed for
                 display only ("By the 15th") — no effect on the math.
      - weekly:  max(0, amount × weeks_touching(month) − assigned_now). The week count is the
                 number of days matching the target's ``due_weekday`` (0=Monday..6=Sunday;
                 unset → Monday, byte-identical to the original Monday-only count) that fall
                 within the budget month — a deterministic 4-or-5 per month, independent of
                 timezone/locale (target deadlines, handoff delta #13).
      - yearly:  max(0, amount/12 − assigned_now) — an even 1/12 spread, set aside this month.
                 An optional ``date`` (the annual deadline, e.g. "By 15 Mar") is stored +
                 echoed for display only — no effect on the math.
      - by_date: max(0, (amount − progressed) / months_remaining), months floored at 1 —
                 build to ``amount`` by the date. ``needs_whole_amount=False`` ("have a balance
                 of") measures Available (a savings balance you don't spend down);
                 ``needs_whole_amount=True`` ("set aside by [date]") measures ``assigned_to_date``
                 — cumulative contributions, so spending never sets you back (YNAB-exact).
    A ``snooze_until`` (YYYY-MM) on the spec suspends funding through that month: while
    ``snooze_until >= month`` the target needs 0 this month (status 'snoozed', NOT underfunded)
    and resumes the following month.
    A non-positive target amount or unknown type → no target (status 'none')."""
    if not target or not isinstance(target, dict):
        return {"target": None, "needed_this_month": 0.0, "status": "none"}
    ttype = target.get("type")
    amount = float(target.get("amount") or 0.0)
    if ttype not in TARGET_TYPES or amount <= 0:
        return {"target": None, "needed_this_month": 0.0, "status": "none"}

    snooze_until = target.get("snooze_until") or None
    # needs_whole_amount=True (default): set aside the full amount, measured against contributions
    # (Assigned).  needs_whole_amount=False: measured against Available (refill / savings balance).
    needs_whole_amount = target.get("needs_whole_amount", True)
    spec: dict = {
        "type": ttype,
        "amount": round(amount, 2),
        "date": target.get("date"),
        "due_weekday": target.get("due_weekday"),
        "due_day": target.get("due_day"),
        "needs_whole_amount": needs_whole_amount,
    }
    if snooze_until:
        spec["snooze_until"] = snooze_until

    # Snoozed: this month (and any earlier) needs nothing — the target resumes after.
    if snooze_until and month <= snooze_until:
        return {"target": spec, "needed_this_month": 0.0, "status": "snoozed"}

    if ttype == "monthly":
        base = amount
    elif ttype == "weekly":
        due_weekday = target.get("due_weekday")
        weekday = due_weekday if isinstance(due_weekday, int) and 0 <= due_weekday <= 6 else 0
        base = amount * _weeks_in_month(month, weekday)
    elif ttype == "yearly":
        base = amount / 12.0
    else:  # by_date — a dated goal, spread over the months remaining (floored at 1).
        months_left = _months_until(month, target.get("date"))
        # needs_whole_amount=True → "Set aside $X by [date]": progress is your cumulative
        # CONTRIBUTIONS (assigned_to_date), so spending never sets you back (YNAB-exact).
        # needs_whole_amount=False → "Have a balance of $X by [date]": progress is the current
        # Available balance (a pure savings goal you don't spend down).
        progressed = assigned_to_date if needs_whole_amount else available_now
        needed = max(0.0, (amount - progressed) / max(1, months_left))
        needed = round(needed, 2)
        status = "funded" if needed <= 0 else "underfunded"
        return {"target": spec, "needed_this_month": needed, "status": status}

    # For recurring types, apply the set-aside vs. refill branch.
    if needs_whole_amount:
        needed = max(0.0, base - assigned_now)
    else:
        # "Fill up to X": measure what you FUNDED this month (rollover + assigned), NOT the
        # balance after spending — spending it down doesn't re-open the target mid-month, it
        # refills next month. available_now already nets out this month's spend (activity ≤ 0),
        # so add it back to recover the funded-this-month figure. Matches the client (planData
        # statusOf) so the row, the health summary, and the AI all agree.
        funded_this_month = available_now - activity
        needed = max(0.0, base - funded_this_month)
    needed = round(needed, 2)
    status = "funded" if needed <= 0 else "underfunded"
    return {"target": spec, "needed_this_month": needed, "status": status}


def _weeks_in_month(month: str, weekday: int = 0) -> int:
    """Number of days matching ``weekday`` (0=Monday..6=Sunday, default Monday) that fall
    within ``month`` (YYYY-MM).

    Deterministic 4-or-5 — the count from the 1st through the last day, inclusive. Used as the
    weekly-target multiplier so a 5-occurrence month funds 5× the weekly amount; a target's
    ``due_weekday`` picks which weekday counts (handoff delta #13; unset → Monday, matching the
    original Monday-only behavior byte-for-byte)."""
    from calendar import monthrange
    from datetime import date

    try:
        y, m = int(month[:4]), int(month[5:7])
    except (ValueError, IndexError):
        return 4
    last = monthrange(y, m)[1]
    return sum(1 for d in range(1, last + 1) if date(y, m, d).weekday() == weekday)


def _months_until(month: str, target_date: str | None) -> int:
    """Whole months from the START of ``month`` to ``target_date`` (>=0). The current month
    counts, so a deadline in the displayed month → 1 (fund it all now)."""
    if not target_date:
        return 1
    try:
        ty, tm = int(target_date[:4]), int(target_date[5:7])
    except (ValueError, IndexError):
        return 1
    my, mm = int(month[:4]), int(month[5:7])
    months = (ty - my) * 12 + (tm - mm) + 1  # inclusive of the current month
    return max(1, months)


# ── Uncategorized income signal ──────────────────────────────────────────────────


def _uncategorized_income(txns: list[dict], month: str, base: str, rates: dict) -> float:
    """Positive income that arrived in ``month`` with no category (None/blank).

    None-category income IS already counted in Ready to Assign (it is RTA inflow) — the
    signal is purely informational: income the user never gave a real label (it may deserve
    one for reports/tax, or a spending category if it is an envelope refund).
    Returns the absolute base amount so the UI can surface a nudge; 0 when there is none."""
    total = 0.0
    for t in txns:
        if t.get("deleted") or t.get("kind") != "income":
            continue
        if not (t.get("date") or "").startswith(month):
            continue
        cat = t.get("category") or ""
        if not cat:
            total += svc.txn_base_amount(t, base, rates)
    return round(total, 2)


# ── Payment-envelope row fields (credit_card_mode="payment_envelope") ─────────────


def _payment_env_accounts(ctx: dict) -> dict[str, dict]:
    """``{payment_envelope_id: card_account}`` for every card with a LIVE payment-envelope FK
    (empty under credit_card_mode "signed" — mirrors ``ctx["card_pay_env_of"]``, just inverted
    and hydrated to the account row). Shared by ``budget_month`` (row attach) and
    ``payment_activity_breakdown``/``activity_transactions`` (popover detail) so the two never
    resolve "is this envelope a card payment row" differently."""
    out: dict[str, dict] = {}
    if ctx["card_pay_env_of"]:
        accounts_by_id = {a["id"]: a for a in ctx["accounts"]}
        for acct_id, env_id in ctx["card_pay_env_of"].items():
            acct = accounts_by_id.get(acct_id)
            if acct is not None:
                out[env_id] = acct
    return out


def payment_activity_breakdown(env_id: str, month: str, ctx: dict | None = None) -> dict | None:
    """The YNAB-reference two-column Activity-popover box for a Credit-Card-Payments envelope:
    ``{spending, returns, total_spending}`` (left column — what actually posted to the CARD
    ACCOUNT this month, uncapped by envelope coverage) vs ``{funded_spending, payments_returns,
    total_activity}`` (right column — the envelope's own Activity, i.e. the auto-move fold).
    Returns ``None`` when ``env_id`` isn't a LIVE payment envelope (credit_card_mode !=
    "payment_envelope", or no card currently FKs to it) — display-only, never a money path.

    ``total_activity`` (``funded_spending + payments_returns``) is PROVABLY the row's own
    ``budget_month`` Activity figure: both are a Σ over the SAME ``_iter_category_contributions``
    fold restricted to ``cat == env_id`` (funded_spending sums the positive/capped-credit
    entries, payments_returns the negative/payment-drain entries — see
    ``_capped_card_credits``/the transfer branch) — this function reuses that exact fold rather
    than re-deriving the total, so the two can never silently disagree.

    ``spending``/``returns``/``total_spending`` are a SEPARATE, account-level fold: every
    charge/refund actually posted to the card this month, independent of whether the spending
    envelope covered it (mirrors ``card_statements._cycle_charges``'s charge/refund detection —
    a charge is ``kind in (expense, investment)`` with ``from_account_id == card`` per the
    payment-envelope spec's inclusion of investment buys on a card; a refund is
    ``kind=income`` with ``to_account_id == card`` — the same convention
    ``card_statements.py`` already uses for credits), gated by the card's own tracking-start
    cutoff like every other payment-envelope read. In the fully-covered case (the common one)
    ``spending`` and ``funded_spending`` share the same magnitude with opposite sign; under
    partial/no coverage they diverge — that divergence IS the "underfunded" gap ``_payment_row_
    fields`` already surfaces on the row itself."""
    ctx = ctx or _build_context(month)
    account = _payment_env_accounts(ctx).get(env_id)
    if account is None:
        return None
    card_id = account["id"]
    base, rates = ctx["base"], ctx["rates"]
    cutoff = ctx["cutoff_by_id"].get(card_id)

    spending = 0.0
    returns = 0.0
    for t in ctx["txns"]:
        if t.get("deleted") or not (t.get("date") or "").startswith(month):
            continue
        if svc.is_before_tracking_start(t.get("date"), cutoff):
            continue
        tkind = t.get("kind")
        if tkind in ("expense", "investment") and t.get("from_account_id") == card_id:
            spending -= svc.txn_base_amount(t, base, rates)
        elif tkind == "income" and t.get("to_account_id") == card_id:
            returns += svc.txn_base_amount(t, base, rates)

    funded_spending = 0.0
    payments_returns = 0.0
    for cat, delta, _t, _s in _iter_category_contributions(
        ctx["txns"],
        month,
        base,
        rates,
        ctx["plan_source_ids"],
        ctx["cutoff_by_id"],
        ctx["card_pay_env_of"],
        ctx,
    ):
        if cat != env_id:
            continue
        if delta >= 0:
            funded_spending += delta
        else:
            payments_returns += delta

    return {
        "spending": round(spending, 2),
        "returns": round(returns, 2),
        "total_spending": round(spending + returns, 2),
        "funded_spending": round(funded_spending, 2),
        "payments_returns": round(payments_returns, 2),
        "total_activity": round(funded_spending + payments_returns, 2),
    }


def _payment_row_fields(ctx: dict, month: str, account: dict, available: float) -> dict:
    """``owed`` + ``available_for_payment`` + ``underfunded`` for a payment-envelope row (rule 5,
    ADR 2026-07-09-ynab-engine-credit-card-payment-envelopes.md).

    ``available_for_payment`` is the row's own raw, SIGNED ``available`` — never clamped (rule
    3: paying the card with unbudgeted money legitimately drives it negative, "you sent more
    than you set aside", and that must show through here exactly, not get floored at 0).

    ``underfunded = max(0, owed − max(available_for_payment, 0))`` where ``owed`` is the card's
    TRUE current debt (``max(0, -balance)``, independent of whether that debt ever posted
    through the plan — this is what correctly surfaces a pre-existing/starting-balance
    adjustment, invisible to the envelope auto-move by design, as an Underfunded figure instead
    of silently diverging: rule 4's "starting debt" case is exactly ``owed=balance,
    available=0`` → ``underfunded=owed``).

    The inner clamp is what makes this figure mean "how much MORE would have to be assigned
    here to cover the card", which is the only thing it is for. Without it the number lied
    exactly where it matters: a payment envelope driven NEGATIVE
    (rule 3 — "you sent the card more than you'd set aside") counted its own overdraw as debt
    ON TOP of the balance that payment already knocked down, so the row read 300 where the
    book owed 180. An overdrawn payment envelope funds nothing; it does not owe extra. Its
    own deficit still shows, unclamped, in ``available_for_payment``.

    ``owed`` goes on the wire in its own right (ADR 2026-09-08) rather than leaving the client
    to invert the formula as ``underfunded + max(available, 0)``: that inversion is wrong for a
    card funded BEYOND its debt (a paid-off card whose envelope still holds money reads as owing
    that money), and the Budget tab's optimistic Ready to Assign needs the real figure to know
    that moving money OUT of such an envelope releases it."""
    owed = round(max(0.0, -_account_balance_base_as_of(ctx, month, account)), 2)
    underfunded = round(max(0.0, owed - max(available, 0.0)), 2)
    return {"owed": owed, "available_for_payment": available, "underfunded": underfunded}


def card_payment_coverage(month: str) -> list[dict]:
    """Each payment-tracked card's debt set against what its payment envelope holds, in base
    currency, as the Budget's Credit Card Payments rows show it: ``{account_id, name, owed,
    held, underfunded}``. ``held`` is the envelope's positive available (an overdrawn envelope
    funds nothing). Empty under credit_card_mode "signed", where no card has such an envelope.
    The one read for anything that must say a card is covered, so no caller recomputes it."""
    ctx = _build_context(month)
    out = []
    for env_id, account in _payment_env_accounts(ctx).items():
        available = _available(ctx, month, env_id)
        fields = _payment_row_fields(ctx, month, account, available)
        out.append(
            {
                "account_id": account["id"],
                "name": account.get("name") or "",
                "owed": fields["owed"],
                "held": round(max(available, 0.0), 2),
                "underfunded": fields["underfunded"],
            }
        )
    return out


# ── The full table the UI / AI consume ───────────────────────────────────────────


def budget_month(month: str) -> dict:
    """The complete Planning table for ``month``: RTA header + groups → categories with
    assigned/activity/available + target status + totals, all base currency.

    Empty/young book → zeros + ``status: 'unbudgeted'`` per category (never a fabricated
    Assigned/Available). A month before any data returns all-zeros (no negative carry).

    Extra signal: ``uncategorized_income_base`` — positive income for this month still
    unclassified (``category=None``). It IS already counted as RTA inflow; the nudge is purely
    informational (give it a real label for reports/tax, or a spending category if it is
    actually an envelope refund)."""
    import time as _time

    _perf_log = __import__("logging").getLogger(__name__)
    _t0 = _time.perf_counter()
    ctx = _build_context(month)
    _t1 = _time.perf_counter()
    base = ctx["base"]
    targets = read_category_targets()

    # Payment-envelope rows (credit_card_mode="payment_envelope" only — empty dict under
    # "signed"): env_id -> the card account it pays, so the row loop below can attach
    # `available_for_payment`/`underfunded`/`is_payment_envelope` (rule 5, ADR
    # 2026-07-09-ynab-engine-credit-card-payment-envelopes.md).
    payment_env_accounts = _payment_env_accounts(ctx)

    # Cumulative contributions per envelope through this month — drives the "set aside $X by
    # [date]" goal (spending-immune). One file load, shared across every row.
    assigned_to_date = _assigned_to_date_map(month)

    # One row per non-archived envelope, full stop. `env_id` is the row's identity — money
    # reads/writes and target lookups key off it directly (no indirection).
    rows: dict[str, dict] = {}
    for spec in ctx["_rows"]:
        cats = spec["category_ids"]
        env_id = spec["envelope_id"]
        assigned = _ctx_assigned(ctx, month, env_id)
        # A zero-category envelope (e.g. a payment envelope) folds activity against its OWN id
        # (ctx["_row_of_cat"]'s fallback — mirrors _available below); `cats` alone would always
        # read 0 for it even when the auto-move fold posted real activity there.
        activity = _row_activity(ctx, month, ctx["_row_of_cat"].get(env_id, cats or [env_id]))
        available = _available(ctx, month, env_id)
        contributed = assigned_to_date.get(env_id, 0.0)
        tinfo = needed_this_month(
            env_id, available, assigned, _target_for(env_id, targets), month, contributed, activity
        )
        unbudgeted = assigned == 0 and activity == 0 and available == 0 and not tinfo["target"]
        row = {
            "key": env_id,
            "label": _row_label(spec),
            "envelope_id": env_id,
            "category_ids": cats,
            "color": spec["color"],
            "assigned": assigned,
            "activity": activity,
            "available": available,
            "assigned_to_date": contributed,
            "target": tinfo["target"],
            "needed_this_month": tinfo["needed_this_month"],
            "status": "unbudgeted" if unbudgeted else tinfo["status"],
            "is_payment_envelope": env_id in payment_env_accounts,
            "owed": None,
            "available_for_payment": None,
            "underfunded": None,
        }
        if env_id in payment_env_accounts:
            row.update(_payment_row_fields(ctx, month, payment_env_accounts[env_id], available))
        rows[env_id] = row

    _t2 = _time.perf_counter()
    _perf_log.info(
        "PERF budget_month(%s) build_context=%.1fms row_loop=%.1fms (n_rows=%d)",
        month,
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        len(ctx["_rows"]),
    )
    groups_out, assigned_cats = _grouped_rows(rows, ctx["_rows"])

    # Derived "Uncategorized" group — synthetic, non-stored: expense transactions with no category
    # (None) land in the "" bucket of _activity_by_category.  Make them visible so the user
    # can see and act on them, without creating a real "uncategorized" category.
    unsorted_activity = _unsorted_activity(ctx, month)
    # Gated on FULL activity alone, unlike `_unbudgeted_keys` — and provably safe here: the blank
    # key is reachable ONLY from an expense leg (income on "" is inflow-to-assign per
    # `_INFLOW_CATEGORIES`; an uncategorized transfer/investment folds nothing), so its cash-only
    # fold is a NEGATIVE subset of its full fold and can never hold what this filter hides. The
    # held term itself never passes through here anyway — `_held_outside_envelopes` adds
    # `_unsorted_available` unconditionally, display row or not.
    if abs(unsorted_activity) >= 0.005:
        # `available` is what the bucket HOLDS (cash-only, ≤ 0 — ADR 2026-09-10), never its
        # Activity: a card swipe with no category shows here but holds nothing.
        unsorted_available = _unsorted_available(ctx, month)
        groups_out.append(
            {
                "id": "__unsorted__",
                "name": "Uncategorized",
                "collapsed": False,
                "synced": False,
                "unsorted": True,
                "categories": [
                    {
                        "key": None,
                        "label": "Uncategorized",
                        "assigned": 0.0,
                        "activity": unsorted_activity,
                        "available": unsorted_available,
                        "target": None,
                        "needed_this_month": 0.0,
                        "status": "unsorted",
                    }
                ],
                "subtotal": {
                    "assigned": 0.0,
                    "activity": unsorted_activity,
                    "available": unsorted_available,
                },
            }
        )

    # Derived "Unbudgeted" group (RFC full-split §Backend 6) — LABELED categories with no
    # linked envelope: real spend, visible, actionable ("Add to envelope…"), but no assigned
    # column (there's no envelope to have assigned anything). Omitted when empty.
    unbudgeted_lines = _unbudgeted_lines(ctx, month)
    unbudgeted_total = 0.0
    if unbudgeted_lines:
        unbudgeted_total = round(sum(ln["activity"] for ln in unbudgeted_lines), 2)
        unbudgeted_held = round(sum(ln["available"] for ln in unbudgeted_lines), 2)
        groups_out.append(
            {
                "id": "__unbudgeted__",
                "name": "Unbudgeted",
                "collapsed": False,
                "synced": False,
                "unbudgeted": True,
                "categories": [
                    {
                        "key": ln["key"],
                        "label": ln["label"],
                        "assigned": 0.0,
                        "activity": ln["activity"],
                        "available": ln["available"],
                        "target": None,
                        "needed_this_month": 0.0,
                        "status": "unlinked",
                    }
                    for ln in unbudgeted_lines
                ],
                "subtotal": {
                    "assigned": 0.0,
                    "activity": unbudgeted_total,
                    "available": unbudgeted_held,
                },
            }
        )

    # Owner decisions (2026-07-02 + the 2026-07-03 clamp — see _display_rta): money assigned
    # in FUTURE months is already spoken for, but it can only consume THIS month's positive
    # remainder — never push the month negative. The identity base (cash − baseline − held in
    # envelopes − uncovered card spend) stays the internal memoized quantity;
    # `assigned_in_future_months` carries the DEDUCTED amount so the breakdown popover's lines
    # sum to the displayed RTA.
    rta, future_assigned = _display_rta(ctx, month)
    uncat_income = _uncategorized_income(ctx["txns"], month, base, ctx["rates"])
    # The identity's terms, on the wire (ADR 2026-09-09). The RTA popover renders exactly these
    # — there is no residual to explain away. `held_in_envelopes` reuses the `_available` memo
    # the row loop already warmed; `card_spend_not_covered` is one lookup in the month's activity
    # map, itself already built by that same loop.
    # ALL FIVE are gated at the floor together, exactly like `_ready_to_assign` itself: a month
    # BEFORE the budget exists has no Ready to Assign (it returns 0 there), so it has no terms to
    # make one out of either. Gating only the subtracted terms left the identity open below
    # the floor — a pre-floor payload published the real bank balance beside a hard RTA of 0 and
    # invited the reader to subtract quantities that were never in the same identity.
    pre_floor = month < ctx["floor"]
    held_in_envelopes = (
        0.0
        if pre_floor
        else round(sum(_available(ctx, month, spec["envelope_id"]) for spec in ctx["_rows"]), 2)
    )
    held_outside_envelopes = 0.0 if pre_floor else _held_outside_envelopes(ctx, month)
    card_spend_not_covered = (
        0.0 if pre_floor else round(_activity_map(ctx, month).get(_CARD_UNCOVERED_KEY, 0.0), 2)
    )
    on_budget_cash = 0.0 if pre_floor else _cash_as_of(ctx, month)
    baseline_out = 0.0 if pre_floor else ctx["baseline"]
    flow = None if pre_floor else _flow_terms(ctx, month)
    cash_overspending = 0.0 if pre_floor else _cash_overspending(ctx, month)

    # totals.activity/available are a PLAIN Σ over the ENVELOPE ROWS — nothing else (ADR
    # 2026-09-08, superseding the BE-TOTALS-UNBUDGETED-CONTRACT fold of 2026-07-04, and kept by
    # ADR 2026-09-09). The Uncategorized and Unbudgeted DISPLAY groups still render, but neither
    # reserves anything, so folding their activity into "what the envelopes hold" made the
    # header disagree with the `held_in_envelopes` sitting right beside it — the identity's own
    # term is exactly this same Σ.
    total_assigned = round(sum(r["assigned"] for r in rows.values()), 2)
    total_activity = round(sum(r["activity"] for r in rows.values()), 2)
    total_available = round(sum(r["available"] for r in rows.values()), 2)
    underfunded = round(
        sum(r["needed_this_month"] for r in rows.values() if r["status"] == "underfunded"), 2
    )

    return {
        "month": month,
        "base_currency": base,
        "ready_to_assign": rta,
        # Balance-identity transparency: RTA = on_budget_cash − pre_budget_baseline
        # − held_in_envelopes − held_outside_envelopes − card_spend_not_covered
        # − assigned_in_future_months. The last term
        # is the CLAMPED deduction (_display_rta), so this identity holds exactly on every
        # month — past, current, or future — and every line of the popover is a payload key. It
        # holds BELOW the floor because every term above is zeroed there together, not because
        # a pre-floor month happens to have nothing in it. RTA is NOT bounded above by cash here
        # (ADR 2026-09-09): `held_in_envelopes` is SIGNED, so a month whose envelopes are net
        # overspent publishes an RTA ABOVE its own cash until the hole resets at the roll.
        "on_budget_cash": on_budget_cash,
        # Display-only flag: do any on-budget cash accounts hold a foreign currency? If so the
        # budget notes that RTA can shift as exchange rates move (foreign cash floats at the
        # current rate). A plain note, not a computed amount. ADR 2026-07-09.
        "has_foreign_cash": _has_foreign_cash(ctx),
        "pre_budget_baseline": baseline_out,
        # Read-only echo of the stored carry floor (null = carrying all history) so the UI can
        # show "Fresh start · since <month>" without a second fetch. Written only via
        # ``set_rollover_start`` (the ``set_spending_limit_rollover_start`` tool that also wrote it was
        # retired 2026-09-26).
        "rollover_start": ctx["rollover_start"],
        # Σ available over the envelope ROWS, SIGNED — negative when envelopes are net
        # overspent this month, because an envelope holds its own overspend until the roll.
        "held_in_envelopes": held_in_envelopes,
        # The no-bucket term (ADR 2026-09-10) — Uncategorized + every unlinked category, each holding
        # its own CASH hole until the roll. SIGNED and ≤ 0: it only hands RTA room back. Kept a
        # SEPARATE term rather than folded into `held_in_envelopes`, which stays exactly the Σ
        # over envelope rows that `totals.available` renders — one number, two names, no drift.
        "held_outside_envelopes": held_outside_envelopes,
        # THIS MONTH's uncovered card-spend activity only — never the card's standing debt.
        "card_spend_not_covered": card_spend_not_covered,
        "assigned_in_future_months": future_assigned,
        "assigned_in_future_by_month": _future_assigned_by_month(month),
        "flow": flow,
        "cash_overspending": cash_overspending,
        "uncategorized_income_base": uncat_income,
        "groups": groups_out,
        "totals": {
            "assigned": total_assigned,
            "activity": total_activity,
            "available": total_available,
            "underfunded": underfunded,
        },
        "nav": _nav(ctx, month),
        "assigned_categories": sorted(assigned_cats),
    }


def _income_category_keys() -> set[str]:
    """Ids of every income-kind category (shown in the Income view, never budgetable)."""
    from backend.services.categories import get_all_categories

    return {c["id"] for c in get_all_categories(include_hidden=True) if c.get("kind") == "income"}


def list_groups() -> list[dict]:
    """Every envelope group in display order (``GET /api/planning/groups``), flagged ``synced``
    for the two the app keeps — Commitments and Credit Card Payments — which a spending envelope
    may never be moved into. A client offering "move to group" (the 50/30/20 envelope editor,
    Zero-Based's kebab) filters on it; ``_op_move_category`` refuses the same regardless, so no
    caller — Lucius included — can put a row where it can't come back from."""
    return [
        {**g, "synced": g.get("name", "") in _SYNCED_GROUP_NAMES}
        for g in groups_store.list_ordered()
    ]


def _envelope_group_buckets(rows: dict[str, dict], row_specs: list[dict]) -> dict[str, list[str]]:
    """Envelope rows, bucketed by their resolved group id, each bucket ordered by
    ``envelope.order`` (RFC full-split §Backend 5: an envelope row places by
    ``envelope.group_id`` directly — merge/split/move relocate it across groups without any
    other write). A ``group_id`` that no longer resolves to a real
    group (its group was deleted) falls back to Others so the row is never orphaned."""
    groups = groups_store.all()
    real_group_ids = {g["id"] for g in groups}
    others = next((g for g in groups if g.get("is_others")), None) or groups_store.ensure_others()
    envelope_specs = sorted(
        (s for s in row_specs if s["envelope_id"] in rows),
        key=lambda s: s["order"] or 0,
    )
    by_group: dict[str, list[str]] = {}
    for spec in envelope_specs:
        gid = spec["group_id"] if spec["group_id"] in real_group_ids else others["id"]
        by_group.setdefault(gid, []).append(spec["envelope_id"])
    return by_group


def _grouped_rows(rows: dict[str, dict], row_specs: list[dict]) -> tuple[list[dict], set[str]]:
    """Project the per-row table into the user's groups. EVERY row is a real envelope now
    (RFC full-split §Backend 3/5 — the pseudo-row branch is gone), placed ONLY by
    ``envelope.group_id``/``order`` (``_envelope_group_buckets``). On a 1:1-only book this is byte-identical to the pre-split table. Returns
    (groups, set of placed row keys)."""
    # A no-longer-resolving (or never-stamped) envelope.group_id falls back to Others
    # (_envelope_group_buckets) — lazily ensure the real entity exists so that fallback
    # actually has somewhere to land. Pre-split this fell out of the pseudo-row path
    # (_ensure_categories_grouped); with pseudo-rows gone it must be explicit here.
    groups_store.ensure_others()
    envelope_buckets = _envelope_group_buckets(rows, row_specs)

    groups = groups_store.list_ordered()
    out: list[dict] = []
    placed: set[str] = set()
    for g in groups:
        cats = envelope_buckets.get(g["id"], [])
        # Hide an empty Others group; a user group always renders (so it can be filled).
        if not cats and g.get("is_others"):
            continue
        placed.update(cats)
        out.append(
            {
                "id": g["id"],
                "name": g.get("name", ""),
                "collapsed": bool(g.get("collapsed")),
                # Every auto-managed group (Commitments, Credit Card Payments) is pinned to the
                # top + locked (read-only, "Synced") in the UI. Flag it by the canonical name so
                # the client never has to guess a magic id (its id is a generated grp-<hash>,
                # not a fixed sentinel).
                "synced": g.get("name", "") in _SYNCED_GROUP_NAMES,
                "categories": [rows[c] for c in cats],
                "subtotal": _subtotal([rows[c] for c in cats]),
            }
        )
    return out, placed


def _subtotal(cat_rows: list[dict]) -> dict:
    return {
        "assigned": round(sum(r["assigned"] for r in cat_rows), 2),
        "activity": round(sum(r["activity"] for r in cat_rows), 2),
        "available": round(sum(r["available"] for r in cat_rows), 2),
    }


def _future_assigned(month: str) -> float:
    """Σ Assigned across all months strictly AFTER ``month``. Raw sum — the displayed
    deduction is CLAMPED in ``_display_rta`` (owner decision 2026-07-03): future months
    can only consume what ``month`` actually has left."""
    total = 0.0
    for m in assignments_store.months():
        if m > month:
            total += sum(assignments_store.month_map(m).values())
    return round(total, 2)


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"


def _future_assigned_by_month(month: str) -> list[dict]:
    """Per-month breakdown of Σ Assigned for every month strictly AFTER ``month`` — the raw
    per-month figures ``_future_assigned`` sums into one total (owner ask, 2026-09-15,
    matching real YNAB's "Assigned in Future Months" list). Display-only: the RTA deduction
    itself still uses ``_display_rta``'s clamped aggregate; this is never fed back into the
    identity, only read alongside it.

    The immediate NEXT month always appears, even at $0.00 with no assignment data at all —
    real YNAB always previews it unconditionally, and ``assignments_store.months()`` only
    lists months that already have a write, so without this the panel was hiding entirely on
    every book that had never touched next month yet (the common case)."""
    months = {m for m in assignments_store.months() if m > month}
    months.add(_next_month(month))
    return [
        {"month": m, "amount": round(sum(assignments_store.month_map(m).values()), 2)}
        for m in sorted(months)
    ]


def _display_rta(ctx: dict, month: str) -> tuple[float, float]:
    """The displayed Ready-to-Assign for ``month`` + the future-assigned deduction that
    was actually applied.

    Owner decision (2026-07-03, amends the raw subtraction of 2026-07-02): a dollar
    assigned to a FUTURE month still reduces ``month``'s displayed RTA (never promise the
    same dollar twice) — but the deduction is capped at ``month``'s own positive remainder:

        deducted = min(future_assigned, max(0, identity(month)))

    Consequences:
      * a month is NEVER pushed negative by promises later income paid for — browsing past
        months used to show every one deep-red because the raw sum deducted ALL later
        months' assignments from a historical cash frame (frame-mixing);
      * the popover's "Assigned in future months" line carries the DEDUCTED amount, so it
        renders only when this month genuinely had leftover the future consumed (the
        frontend hides the line at 0);
      * a genuine over-promise still shows red where it lives — the future month's own
        identity (what's set aside there exceeds the cash backing it);
      * a genuinely overspent month (identity < 0) keeps its true negative untouched.
    """
    base = _ready_to_assign(ctx, month)
    deducted = round(min(_future_assigned(month), max(0.0, base)), 2)
    return round(base - deducted, 2), deducted


def _nav(ctx: dict, month: str) -> dict:
    """Month-navigation bounds: forward unbounded; backward bounded by the floor."""
    return {
        "current": month,
        "prev": _prev_month(month),
        "earliest": ctx["floor"],
        "can_go_back": month > ctx["floor"],
    }


# ── Fresh start: moving the carry floor (ADR 2026-09-08) ─────────────────────────
# A "fresh start" moves ONE field — ``budget_config.rollover_start``. No transaction, no
# A "fresh start" is a BLANK SLATE for ``month`` (owner decision 2026-09-08, amending the
# original "moves the floor, never the data"): it moves ``budget_config.rollover_start`` AND
# clears every assignment in that month. Both halves land in ONE transaction, and both are
# snapshotted first, so Undo puts the book back exactly — the floor via the same single writer
# (``services.budgets.write_budget_config``) and the assignments back through the money choke
# ``apply_budget_change``, which is now very much involved: clearing an assignment IS a money
# write and must be checkpoint-covered like any other.
#
# Scope, deliberately: the ZERO-BASED lens' assignment table only. The proportional engine's
# config is that engine's own state, not this one's — a fresh start in the zero-based budget
# must not silently rewrite a lens the user isn't looking at.


def budget_config() -> dict:
    """``{rollover_start, budget_start, pre_budget_baseline}`` — the stored budget config."""
    return budgets_service.read_budget_config()


def set_rollover_start(rollover_start: str | None) -> dict:
    """Move (or clear, with ``None``) the carry-walk floor; returns the resulting config.

    The ONLY field this entrypoint sets — a merge write, so a captured ``pre_budget_baseline``
    survives untouched.

    The month is validated by ``budgets.write_budget_config`` (the single writer), NOT here:
    the same refusal bound the (retired 2026-09-26) ``set_spending_limit_rollover_start`` tool,
    which wrote there directly. It raises ValidationError on a non-YYYY-MM month and on a FUTURE
    one — the floor is also ``_ready_to_assign``'s zero-line, so a floor ahead of today blanks
    Ready to Assign and every envelope in every month the user can reach. A floor BEFORE the
    first month with data stays legal: that is simply a budget that carries everything."""
    budgets_service.write_budget_config({"rollover_start": rollover_start})
    return budgets_service.read_budget_config()


def fresh_start_preview(month: str) -> dict:
    """What "start fresh from ``month``" would do — computed, never written.

    Runs the REAL fold against a context whose carry floor is ``month`` AND whose ``month``
    assignments are zeroed (``_build_context(..., floor_override=, zero_month_assignments=True)``)
    — the two halves the write actually performs — so the number the confirm dialog promises is
    the number the user gets. Nothing is persisted and no store is touched: a preview that
    wrote-then-reverted would leave the book wrong for the width of the request and would
    checkpoint a phantom edit.

    Under the restored YNAB identity (ADR 2026-09-09) the ledger is the SAME identity the RTA
    popover renders, exactly — no residual term, ever:

        after = cash_now − baseline − held_in_envelopes − held_outside_envelopes
                − card_spend_not_covered − assigned_in_future

    ``cash_now`` is on-budget cash as of the END of ``month`` ITSELF (``_cash_as_of`` — the same
    fold ``budget_month`` reports as ``on_budget_cash``), not the previous month's cash: the
    identity is measured at the cash, this month's own activity included. The two subtracted
    terms are read against the SAME hypothetical context, so a card spend or a refund made THIS
    month prices in exactly as it would post-write. ``baseline`` is the pre-budget cutover
    capture (``ctx["baseline"]``), echoed rather than re-derived.

    ``held_in_envelopes`` is normally 0 at the hypothetical floor — the write clears the month's
    assignments, so a plain spend leaves nothing held back — but it is NOT always 0: a refund
    landing in a spending envelope this same month, or a covered card swipe this same month
    (the coverage cap crediting the payment envelope), both leave a row available non-zero even
    with assigned zeroed and no carry. Earlier drafts omitted the term on the theory that it was
    always 0 on the books they tested — a real gap. Including it always is what makes the
    identity exact BY CONSTRUCTION rather than "on every book this file happens to try."

    ``held_outside_envelopes`` (ADR 2026-09-10) is unaffected by a fresh start in either half —
    it depends only on this month's own uncategorized/unlinked CASH activity, which the write
    neither clears nor re-floors — but it is a term of the identity, so the dialog's ledger
    cannot close without it (the same reasoning as the paragraph above).

    A future month's OWN assignments can still clamp the displayed figure (``_display_rta``'s
    owner-decided rule: a dollar already promised to a later month can't be promised twice to this
    one). When that clamp is live, ``ready_to_assign_after`` is the CLAMPED figure and
    ``assigned_in_future`` carries the deduction so the dialog can name it — the key is present
    only then; the other four are always present, hidden by the FRONTEND when zero.

    ``month`` is validated by the SAME two rules the write applies (``_valid_month`` +
    ``budgets._validate_anchor_month``, ``fresh_start``'s own pair) — a preview that answered a
    future or malformed month with a full figure the confirm then refused was a dialog promising
    a number it could never deliver."""
    _valid_month(month)
    budgets_service._validate_anchor_month("rollover_start", month)
    after = _build_context(month, floor_override=month, zero_month_assignments=True)
    cash_now = _cash_as_of(after, month)
    baseline = after["baseline"]
    held_in_envelopes = round(
        sum(_available(after, month, spec["envelope_id"]) for spec in after["_rows"]), 2
    )
    held_outside_envelopes = _held_outside_envelopes(after, month)
    card_spend_not_covered = round(_activity_map(after, month).get(_CARD_UNCOVERED_KEY, 0.0), 2)
    rta_after, deducted = _display_rta(after, month)
    result = {
        "month": month,
        "ready_to_assign_after": rta_after,
        "cash_now": cash_now,
        "held_in_envelopes": held_in_envelopes,
        "held_outside_envelopes": held_outside_envelopes,
        "card_spend_not_covered": card_spend_not_covered,
        "baseline": baseline,
    }
    if deducted:
        result["assigned_in_future"] = deducted
    return result


def fresh_start(month: str) -> dict:
    """Start fresh from ``month``: clear that month's assignments AND move the carry floor to it,
    as ONE transaction. Returns ``{token, month}`` — the handle Undo needs, NOT the data.

    Both halves are one unit on purpose. Half-applied is a book that says "fresh start · since
    September" while September's envelopes are still funded, or the reverse — either way a state
    no user asked for and no single Undo can name. ``locked_unit_of_work`` holds all three tables'
    locks in global order for the whole write, so a failure anywhere rolls back everything.

    **The before-image never leaves the server.** It is snapshotted INSIDE the same transaction
    (so a concurrent assign can't be zeroed-but-unsnapshotted, or created-but-uncleared) and
    stored under a one-shot token via ``PlanStateStore.set_fresh_start_undo``. Returning it to the
    client and taking it back made the undo route an unauthenticated bulk assign that smuggled
    negative amounts past ``_op_assign``'s guard — found in pre-merge review, and the reason the
    wire now carries a token only.

    ``month`` is validated UP FRONT (``budgets._validate_anchor_month``, the same rule the single
    writer applies at the end) rather than only when the floor is finally written: the refusal is
    identical either way thanks to the transaction, but failing before the lock is taken is
    cheaper and makes the guard legible where the op starts.

    Every write inside goes through its existing choke — ``apply_budget_change`` for each
    assignment (checkpoint-covered, one write-hook before-image per row) and
    ``budgets.write_budget_config`` for the floor."""
    _valid_month(month)
    budgets_service._validate_anchor_month("rollover_start", month)
    token = new_id("fsu")
    with locked_unit_of_work(budget_assignments_table, plan_state_table, profile_table) as conn:
        # Read INSIDE the lock: between a read outside it and the writes below, a concurrent
        # assign could land in the month and be cleared without ever entering the snapshot.
        snapshot = {
            "token": token,
            "month": month,
            "rollover_start": budgets_service.read_budget_config().get("rollover_start"),
            "assignments": dict(assignments_store.month_map(month)),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for env_id in snapshot["assignments"]:
            apply_budget_change(
                {"type": "assign", "month": month, "envelope_id": env_id, "amount": 0.0},
                conn=conn,
            )
        budgets_service.write_budget_config({"rollover_start": month}, conn=conn)
        plan_state_store.set_fresh_start_undo(snapshot, conn=conn)
    logger.info(
        "fresh_start %s: cleared %d assignment(s), floor %s → %s (undo token %s)",
        month,
        len(snapshot["assignments"]),
        snapshot["rollover_start"],
        month,
        token,
    )
    return {"token": token, "month": month}


def undo_fresh_start(token: str) -> None:
    """Undo the pending fresh start named by ``token`` — the floor AND every cleared assignment,
    in one transaction, from the SERVER-HELD before-image.

    One-shot: the snapshot is deleted in the same transaction that replays it, so a second undo
    (or a stale token from another tab) raises ``ConflictError`` rather than re-writing stale
    amounts over whatever the user has done since.

    ``allow_negative`` is passed here — only over values this function read back out of
    ``plan_state``, amounts the server itself wrote at ``fresh_start`` — and by the YNAB
    migration (``ynab_migration.apply.apply_budget``), over YNAB's own month figures. A negative
    assigned slot is a real state (``move_money`` out of a row produces one), so a byte-identical
    restore needs it; what it must never be is a door a caller can push its own numbers through.

    Accepted limitation: an assignment edited in the 7-second undo window is DISCARDED by Undo,
    which replays the pre-fresh-start amounts wholesale."""
    if not isinstance(token, str) or not token.strip():
        raise ValidationError("token is required")
    with locked_unit_of_work(budget_assignments_table, plan_state_table, profile_table) as conn:
        snapshot = plan_state_store.get_fresh_start_undo()
        if not snapshot:
            raise ConflictError("There is nothing to undo — this fresh start was already undone")
        if snapshot.get("token") != token:
            raise ConflictError("This undo is no longer the most recent fresh start")
        month = _valid_month(snapshot.get("month"))
        assignments = snapshot.get("assignments") or {}
        for env_id, amount in assignments.items():
            apply_budget_change(
                {"type": "assign", "month": month, "envelope_id": env_id, "amount": amount},
                conn=conn,
                allow_negative=True,
            )
        budgets_service.write_budget_config(
            {"rollover_start": snapshot.get("rollover_start")}, conn=conn
        )
        plan_state_store.set_fresh_start_undo(None, conn=conn)
    logger.info("undo_fresh_start %s: restored %d assignment(s)", month, len(assignments))


def _category_label(cat: str, label_map: dict[str, str] | None = None) -> str:
    """Human label for a category key — read from the CategoryStore, the single source of
    truth since the categories-as-pure-data re-arch. A genuinely unrecognized key (should
    never happen on a populated store) title-cases as a last resort.

    Pass a pre-built ``{id: label}`` map (``_category_label_map()``) when labelling many
    rows in a loop — mirrors the identical fix in ``services.cashflow`` (2026-07-27 perf
    investigation: this exact per-call ``CategoryStore()`` pattern was measured causing
    hundreds of redundant full-store reads there)."""
    if label_map is not None:
        label = label_map.get(cat)
    else:
        from backend.db.category_store import CategoryStore

        row = CategoryStore().get(cat)
        label = row.get("label") if row else None
    if label and str(label).strip():
        return str(label)
    return cat.replace("_", " ").title()


def _category_label_map() -> dict[str, str]:
    """``{category_id: label}`` for every category, loaded ONCE — the batch counterpart to
    ``_category_label``'s per-call store lookup."""
    from backend.db.category_store import CategoryStore

    return {r["id"]: r.get("label") for r in CategoryStore().all()}


# ── Age of Money (the rolling cash-age metric) ───────────────────────────────────

# How many of the most-recent outflows the rolling age is taken over (YNAB samples the last
# ~10 spends). Fewer datable outflows than this → low confidence; below the min we refuse.
_AGE_WINDOW = 10
# Below this many DATABLE outflows we don't fabricate a number ("not enough history").
_AGE_MIN_SAMPLES = 3


def _parse_day(d: str) -> date | None:
    """YYYY-MM-DD → date, or None on a bad/empty value."""
    try:
        return date(int(d[:4]), int(d[5:7]), int(d[8:10]))
    except (ValueError, IndexError, TypeError):
        return None


def age_of_money(txns: list[dict] | None = None, *, today: date | None = None) -> dict:
    """The rolling Age of Money — the median number of days a unit of cash sits between
    arriving (an inflow) and being spent (an outflow).

    Computed FIFO, base-currency-correct (every leg via ``ledger.txn_base_amount`` so a base
    change re-values it): inflows are queued oldest-first; each outflow consumes inflow cash
    oldest-first, and the consumed cash's age = (outflow date − inflow date). The metric is
    the integer MEDIAN of the per-outflow ages over the last ``_AGE_WINDOW`` outflows (a
    quantity-weighted age WITHIN each outflow, so a big spend draining old + new cash is aged
    fairly). An outflow that can't be funded from any datable inflow cash is skipped (we
    can't honestly date its cash) — so a young book with fewer than ``_AGE_MIN_SAMPLES``
    datable outflows returns ``forecastable: False`` and NO fabricated number.

    Returns ``{age_days, forecastable, samples, reason}``."""
    today = today or user_today()
    rows = txns if txns is not None else svc.transactions.all()
    base = svc.base_currency()
    rates = load_fx_cache()

    # Build dated inflow / outflow streams (base amounts, oldest first).
    inflows: list[tuple[date, float]] = []
    outflows: list[tuple[date, float]] = []
    for t in rows:
        if t.get("deleted"):
            continue
        kind = t.get("kind")
        if kind not in ("income", "expense"):
            continue  # transfers/adjustments don't create or spend net cash
        day = _parse_day(t.get("date") or "")
        if day is None or day > today:
            continue
        amt = svc.txn_base_amount(t, base, rates)
        if amt <= 0:
            continue
        (inflows if kind == "income" else outflows).append((day, amt))

    inflows.sort(key=lambda x: x[0])
    outflows.sort(key=lambda x: x[0])

    if not inflows or len(outflows) < _AGE_MIN_SAMPLES:
        return _age_insufficient(0)

    # FIFO match: walk outflows oldest→newest, consuming inflow cash oldest-first. Each
    # outflow's age = quantity-weighted (Σ age·portion / Σ portion) over the cash it drew.
    queue: list[list] = [[d, a] for d, a in inflows]  # mutable [date, remaining]
    qi = 0
    ages: list[float] = []
    for od, oamt in outflows:
        need = oamt
        weighted_days = 0.0
        consumed = 0.0
        while need > 1e-9 and qi < len(queue):
            idate, iremain = queue[qi]
            take = min(need, iremain)
            weighted_days += max(0, (od - idate).days) * take
            consumed += take
            queue[qi][1] -= take
            need -= take
            if queue[qi][1] <= 1e-9:
                qi += 1
        if consumed <= 1e-9:
            continue  # no datable inflow cash funded this spend → can't age it honestly
        ages.append(weighted_days / consumed)

    if len(ages) < _AGE_MIN_SAMPLES:
        return _age_insufficient(len(ages))

    window = sorted(ages[-_AGE_WINDOW:])
    n = len(window)
    mid = n // 2
    median = window[mid] if n % 2 else (window[mid - 1] + window[mid]) / 2.0
    return {
        "age_days": int(round(median)),
        "forecastable": True,
        "samples": n,
        "reason": "ok",
    }


def _age_insufficient(samples: int) -> dict:
    return {
        "age_days": None,
        "forecastable": False,
        "samples": samples,
        "reason": "not_enough_history",
    }


# ── Budget health (a current-month summary composed from budget_month) ───────────


def budget_health(month: str | None = None, table: dict | None = None) -> dict:
    """A one-glance health summary for ``month``, composed PURELY from ``budget_month`` (no
    new money math — every figure is read off the already-computed table). Pass an
    already-built ``table`` to reuse it (the health route does, so the table is built once).

    Returns ``{month, base_currency, rta, rta_status, underfunded_count,
    underfunded_amount, overspent_count, total_available, total_assigned}`` where
    ``rta_status`` ∈ {to_assign, all_assigned, overassigned}: positive RTA = cash nobody has
    given a job yet; ~0 = every dollar assigned; NEGATIVE = more has been assigned across
    envelopes (or is owed toward uncovered card debt) than the cash on hand can back —
    OVER-ASSIGNMENT, not a red envelope (ADR 2026-09-09): an in-month overspend is HELD by its
    own envelope and does not move RTA until the month rolls, so a negative RTA here means the
    ASSIGNED side outran cash, never that a category went red.

    ``overspent_count`` counts every row whose Available is below −0.005 THIS MONTH ONLY — the
    clamped carry (ADR 2026-09-09) resets a still-negative envelope to 0 at the roll (charging
    the hole to next month's Ready to Assign instead), so this is never a running total across
    months. It is a to-do list, not free tidying: covering an overspent envelope COSTS Ready to
    Assign, up to the hole's size — that assignment is what pays for it.

    The ~0 band is ±0.05: multi-currency books accrue cent-level drift between per-txn
    activity rounding and the once-per-account balance conversion (verify-lens finding), and
    a fully-assigned book must not flip to 'to_assign' over a phantom cent."""
    if table is None:
        table = budget_month(month or _this_month())
    rta = table.get("ready_to_assign", 0.0)
    totals = table.get("totals", {})

    underfunded_count = 0
    overspent_count = 0
    for g in table.get("groups", []):
        for c in g.get("categories", []):
            if c.get("status") == "underfunded":
                underfunded_count += 1
            if (c.get("available") or 0.0) < -0.005:
                overspent_count += 1

    if rta > 0.05:
        rta_status = "to_assign"
    elif rta < -0.05:
        rta_status = "overassigned"
    else:
        rta_status = "all_assigned"

    return {
        "month": table.get("month"),
        "base_currency": table.get("base_currency"),
        "rta": rta,
        "rta_status": rta_status,
        "underfunded_count": underfunded_count,
        "underfunded_amount": totals.get("underfunded", 0.0),
        "overspent_count": overspent_count,
        "total_available": totals.get("available", 0.0),
        "total_assigned": totals.get("assigned", 0.0),
    }


def planning_health(month: str | None = None) -> dict:
    """The combined read for the Planning health strip: budget health for ``month`` + the
    book-wide Age of Money. One call so the route/UI fetch both together. The budget table is
    built ONCE and reused for the health derivation (no double compute)."""
    table = budget_month(month or _this_month())
    return {
        **budget_health(table=table),
        "age_of_money": age_of_money(),
    }


# ── Income view (Budget tab "Inflow categories") ─────────────────────────────


def history_activity_by_category(month: str) -> dict[str, float]:
    """Activity for ``month`` the budget leaves out ONLY because it is dated before its
    account's Starting Balance: history the opening balance already holds. The budget must
    never count it (it would spend money twice); a report of where money went must (a
    statement imported at onboarding read "No spending recorded in Sep", persona QA
    2026-10-13). The SAME fold as the budget's own Activity, run once with the cutoffs and once
    without, so the two can never disagree on what a leg is, only on whether history counts."""
    ctx = _build_context(month)
    if not ctx["cutoff_by_id"]:
        return {}
    args = (ctx["txns"], month, ctx["base"], ctx["rates"], ctx["plan_source_ids"])
    tracked = _activity_by_category(*args, ctx["cutoff_by_id"], ctx["card_pay_env_of"], ctx)
    everything = _activity_by_category(*args, {}, ctx["card_pay_env_of"], ctx)
    out = {}
    for cat in set(tracked) | set(everything):
        delta = round(everything.get(cat, 0.0) - tracked.get(cat, 0.0), 2)
        if abs(delta) >= 0.005:
            out[cat] = delta
    return out


def _income_received_ytd(
    txns: list[dict], month: str, base: str, rates: dict
) -> dict[str, tuple[float, float]]:
    """Sum received (this month) and ytd (Jan 1 of the year through month-end) per
    category for kind=income transactions. Only income-kind category keys need to be
    summed, but we accumulate ALL categories here so the caller can filter."""
    ytd_start = month[:4] + "-01"
    acc: dict[str, list[float]] = {}  # cat → [received, ytd]
    for t in txns:
        if t.get("deleted") or t.get("kind") != "income":
            continue
        date_str = (t.get("date") or "")[:7]
        if not (ytd_start <= date_str <= month):
            continue
        cat = t.get("category") or ""
        entry = acc.setdefault(cat, [0.0, 0.0])
        base_amt = svc.txn_base_amount(t, base, rates)
        entry[1] += base_amt  # ytd always
        if date_str == month:
            entry[0] += base_amt  # this month only
    return {k: (round(v[0], 2), round(v[1], 2)) for k, v in acc.items()}


def income_view(month: str) -> dict:
    """Income-view payload for month YYYY-MM: the income-kind categories as one flat list (income
    belongs to no group), with this-month received + YTD per category and in total.

    Amounts are sourced from kind=income transactions only — never the income-events
    store — so a cash dividend booked via apply_income_change is counted exactly once.
    The income-events store is the investment-only lens; the ledger is the single source
    of truth for the budget income view. An empty book returns zeros, never a 500."""
    ctx = _build_context(month)
    base = ctx["base"]

    from backend.services.categories import get_all_categories

    income_cats = [c for c in get_all_categories(include_hidden=True) if c.get("kind") == "income"]
    sums = _income_received_ytd(ctx["txns"], month, base, ctx["rates"])

    cats_out: dict[str, dict] = {}
    for cat in income_cats:
        key = cat["id"]
        received, ytd = sums.get(key, (0.0, 0.0))
        cats_out[key] = {
            "key": key,
            "label": cat.get("label") or key,
            "color": cat.get("color") or "",
            "received": received,
            "ytd": ytd,
        }

    all_cats = list(cats_out.values())
    return {
        "month": month,
        "base_currency": base,
        "categories": all_cats,
        "total_received": round(sum(c["received"] for c in all_cats), 2),
        "total_ytd": round(sum(c["ytd"] for c in all_cats), 2),
    }


# ── The single write choke point ─────────────────────────────────────────────────


_AUTO_ASSIGN_MODES = frozenset(
    [
        "underfunded",
        "assigned_last_month",
        "spent_last_month",
        "average_assigned",
        "average_spent",
        "cover_overspending",
        "reset_assigned",
    ]
)

# The modes whose steps COUPLE — one step's write moves a later step's correct figure, so
# ``_op_auto_assign`` re-derives each of theirs against the freshly-written book (paying a
# context rebuild per step for it) while every other mode applies its previewed steps against
# the single frozen preview. Membership is a READ/WRITE-SET argument about ``month``, never a
# judgement about how a mode "feels":
#
#   An apply writes exactly ONE thing — ``assigned(month, env)`` — for the rows in its own step
#   list. A mode couples iff some step's figure READS a value another step's write can move.
#   Reading ``assigned(month, own row)`` (the ``current`` every builder compares against before
#   emitting a step) does NOT couple: there is one row per envelope, so no other step writes
#   that slot.
#
#   * ``cover_overspending`` — reads ``_available(month, env)``, whose activity term for a
#     card-linked row runs through ``_capped_card_credits``, which reads ``assigned(month, ·)``
#     of the SPENDING envelope and credits the card's PAYMENT envelope. Payment envelopes left
#     the step universe with the 2026-09-08 HIGH, so that path can no longer land on a step —
#     membership is defence in depth for the N non-atomic writes below. COUPLED.
#   * ``underfunded`` — reads ``_available(month, env)`` AND carries a running ``remaining_rta``
#     seeded from ``_ready_to_assign(month)``, which every step's write moves. COUPLED.
#   * ``reset_assigned`` — writes the constant 0; reads only its own row's ``current``. NOT
#     coupled.
#   * ``assigned_last_month`` — reads ``assigned(_prev_month(month), env)`` plus its own
#     ``current``; the apply writes ``month`` only. NOT coupled.
#   * ``spent_last_month`` / ``average_assigned`` / ``average_spent`` — read activity/assigned
#     for months STRICTLY BEFORE ``month`` (``_prev_month`` / ``_auto_history_months``) plus
#     their own ``current``; the apply never writes an earlier month. NOT coupled.
#
# An uncoupled mode's previewed steps ARE its end state, so applying them frozen is exact rather
# than merely close enough. Adding a mode here costs only time; leaving a genuinely coupled one
# out is the defect — so a new mode that reads ANYTHING at ``month`` belongs in this set until
# the same read/write-set argument says otherwise.
_COUPLED_AUTO_ASSIGN_MODES = frozenset(["cover_overspending", "underfunded"])


def _auto_reset(month: str, rows: list[dict]) -> list[dict]:
    steps = []
    for spec in rows:
        env_id = spec["envelope_id"]
        current = _assigned_amt(month, env_id)
        if current != 0.0:
            steps.append(
                {
                    "envelope_id": env_id,
                    "label": _row_label(spec),
                    "current_assigned": current,
                    "new_assigned": 0.0,
                }
            )
    return steps


def _auto_underfunded(ctx: dict, month: str, rows: list[dict]) -> list[dict]:
    """Fund every row's target shortfall, then every card's payment shortfall (YNAB's own
    Underfunded does both — its total was exactly the targets plus each card's underfunded,
    checked side by side 2026-09-28).

    Cards go LAST and only when no target step lands on an overspent row. A row with
    Available ≥ 0 already has every card swipe against it covered (coverage is
    ``min(card spend, headroom)`` and Available is ``headroom − card spend``), so funding it
    moves nothing into a payment envelope and the card figures below stay exact. Funding an
    overspent row CAN move coverage into a card — the 2026-09-08 HIGH's double-fund — so that
    preview proposes no card steps at all rather than a card figure its own earlier steps shrink.
    """
    # Cap at the DISPLAYED RTA (future-assigned already spoken for, clamped — _display_rta).
    remaining_rta = _display_rta(ctx, month)[0]
    targets = read_category_targets()
    assigned_to_date = _assigned_to_date_map(month)
    payment_accounts = _payment_env_accounts(ctx)
    funds_overspent_row = False
    steps = []
    for spec in rows:
        if remaining_rta <= 0:
            break
        env_id = spec["envelope_id"]
        if env_id in payment_accounts:
            continue
        assigned = _assigned_amt(month, env_id)
        available = _available(ctx, month, env_id)
        activity = _row_activity(
            ctx, month, ctx["_row_of_cat"].get(env_id, spec["category_ids"] or [env_id])
        )
        tinfo = needed_this_month(
            env_id,
            available,
            assigned,
            _target_for(env_id, targets),
            month,
            assigned_to_date.get(env_id, 0.0),
            activity,
        )
        needed = tinfo["needed_this_month"]
        if needed <= 0:
            continue
        add = round(min(needed, remaining_rta), 2)
        if add <= 0:
            continue
        new_assigned = round(assigned + add, 2)
        remaining_rta = round(remaining_rta - add, 2)
        funds_overspent_row = funds_overspent_row or available < 0
        steps.append(
            {
                "envelope_id": env_id,
                "label": _row_label(spec),
                "current_assigned": assigned,
                "new_assigned": new_assigned,
            }
        )
    if not funds_overspent_row:
        steps += _auto_card_shortfalls(ctx, month, rows, payment_accounts, remaining_rta)
    return steps


def _auto_card_shortfalls(
    ctx: dict, month: str, rows: list[dict], payment_accounts: dict, remaining_rta: float
) -> list[dict]:
    """Each card's payment ``underfunded`` (``_payment_row_fields``), in row order, capped at
    what Ready to Assign has left."""
    steps = []
    for spec in rows:
        if remaining_rta <= 0:
            break
        env_id = spec["envelope_id"]
        account = payment_accounts.get(env_id)
        if account is None:
            continue
        available = _available(ctx, month, env_id)
        short = _payment_row_fields(ctx, month, account, available)["underfunded"]
        add = round(min(short, remaining_rta), 2)
        if add <= 0:
            continue
        assigned = _assigned_amt(month, env_id)
        remaining_rta = round(remaining_rta - add, 2)
        steps.append(
            {
                "envelope_id": env_id,
                "label": _row_label(spec),
                "current_assigned": assigned,
                "new_assigned": round(assigned + add, 2),
            }
        )
    return steps


def _auto_cover_overspending(ctx: dict, month: str, rows: list[dict]) -> list[dict]:
    """Top up every overspent SPENDING row to exactly 0. Payment envelopes are excluded.

    A card's payment envelope is not a hole to fill: it is funded by COVERAGE, not by an
    assignment. ``_capped_card_credits`` moves a swipe's covered part into it the moment the
    spending envelope the swipe drew on is itself funded — so a step sized against the payment
    row's pre-coverage Available lands on top of a credit that did not exist when the step was
    computed, and the money sits there as real set-aside for a card that may owe nothing. That
    is the security review's HIGH (2026-09-08): a S$900 swipe paid off in-month proposed 900 to
    the spending row AND 900 to the payment row, and Ready to Assign fell 100 → −800. The Budget
    tab has always excluded them (``mapCat`` forces ``status='none'`` on a payment row); this is
    the same universe, so the tab and the tool now propose the same work.

    CAPPED at the DISPLAYED Ready to Assign again (ADR 2026-09-09, restoring B4 — symmetric with
    ``_auto_underfunded``). Under the restored identity an envelope HOLDS its overspend, so
    covering one genuinely COSTS Ready to Assign, dollar for dollar up to the hole's size: the
    money has to come from somewhere, and auto-assign must never propose funding money that
    doesn't exist (money-conservation, B1/B2). The 2026-09-08 uncapped version rested on
    "covering never changes RTA", which the restored formula makes false. Same greedy,
    row-order allocation as ``_auto_underfunded``: fund each overspent row's FULL deficit before
    moving to the next, until RTA runs out."""
    payment_env_ids = set(_payment_env_accounts(ctx))
    remaining_rta = _display_rta(ctx, month)[0]
    steps = []
    for spec in rows:
        if remaining_rta <= 0:
            break
        env_id = spec["envelope_id"]
        if env_id in payment_env_ids:
            continue
        available = _available(ctx, month, env_id)
        if available >= 0:
            continue
        assigned = _assigned_amt(month, env_id)
        add = round(min(-available, remaining_rta), 2)
        if add <= 0:
            continue
        remaining_rta = round(remaining_rta - add, 2)
        steps.append(
            {
                "envelope_id": env_id,
                "label": _row_label(spec),
                "current_assigned": assigned,
                "new_assigned": round(assigned + add, 2),
            }
        )
    return steps


def _auto_assigned_last_month(month: str, rows: list[dict]) -> list[dict]:
    prev = _prev_month(month)
    steps = []
    for spec in rows:
        env_id = spec["envelope_id"]
        new_assigned = round(_assigned_amt(prev, env_id), 2)
        current = _assigned_amt(month, env_id)
        if new_assigned != current:
            steps.append(
                {
                    "envelope_id": env_id,
                    "label": _row_label(spec),
                    "current_assigned": current,
                    "new_assigned": new_assigned,
                }
            )
    return steps


def _auto_spent_last_month(ctx: dict, month: str, rows: list[dict]) -> list[dict]:
    prev = _prev_month(month)
    steps = []
    for spec in rows:
        env_id = spec["envelope_id"]
        # Net activity is negative for a spend; folded across every member THEN made positive
        # (matches the row's own Activity column — folding first, not summing member abs()s,
        # is the only way a same-month refund on one member nets correctly against the rest).
        spent = round(abs(_row_activity(ctx, prev, spec["category_ids"])), 2)
        current = _assigned_amt(month, env_id)
        if spent != current:
            steps.append(
                {
                    "envelope_id": env_id,
                    "label": _row_label(spec),
                    "current_assigned": current,
                    "new_assigned": spent,
                }
            )
    return steps


def _auto_history_months(month: str, floor: str) -> list[str]:
    """Up to 12 months before ``month``, none before the budget's first month (``floor``).

    YNAB averages only the months the budget has existed — a one-month-old budget's Average
    Assigned is that month's figure (checked side by side, 2026-09-28). Dividing by a fixed 12
    counted every month before a fresh start as a zero, so the average came out a fraction of
    what the user actually budgets."""
    history_months: list[str] = []
    m = _prev_month(month)
    while len(history_months) < 12 and m >= floor:
        history_months.append(m)
        m = _prev_month(m)
    return history_months


def _auto_average(ctx: dict, month: str, rows: list[dict], mode: str) -> list[dict]:
    history_months = _auto_history_months(month, ctx["floor"])
    n = len(history_months)
    if not n:
        return []
    steps = []
    for spec in rows:
        env_id = spec["envelope_id"]
        if mode == "average_assigned":
            total = sum(_assigned_amt(hm, env_id) for hm in history_months)
        else:
            total = sum(abs(_row_activity(ctx, hm, spec["category_ids"])) for hm in history_months)
        new_assigned = round(total / n, 2)
        current = _assigned_amt(month, env_id)
        if new_assigned != current:
            steps.append(
                {
                    "envelope_id": env_id,
                    "label": _row_label(spec),
                    "current_assigned": current,
                    "new_assigned": new_assigned,
                }
            )
    return steps


def auto_assign_preview(month: str, mode: str) -> dict:
    """Preview what auto-assign would do for ``month`` under ``mode``.

    Returns ``{steps: [{envelope_id, label, current_assigned, new_assigned}], total_delta}``.
    ONE step per BUDGET ROW (an envelope's member categories folded) — NEVER per raw category.
    Iterating raw categories would generate an independent step per member of a merged (N>1)
    envelope, all targeting the SAME stored slot: applying them sequentially would let the
    last-processed member's write clobber the others, and the preview's own `total_delta` would
    sum the same envelope's delta once per member. ``envelope_id`` in each step is the ONE key
    ``_set_assigned_amt`` needs.

    Row-level semantics (mirrors how ``budget_month`` renders a row):
      * assigned/available — the row's ONE value (the existing envelope facade / the folded
        ``_available``), never summed per member.
      * target (``underfunded``) — the row's OWN (envelope-keyed) target ONLY, matching what
        the table itself displays for that row; member categories carry no target of their
        own to fold (a merged envelope shows a single target).
      * history (``spent_last_month``/``average_spent``) — net activity folded across every
        member for that month, THEN made absolute — matches the row's own Activity column.

    Modes:
    - ``underfunded`` — fund all underfunded targets up to their ``needed_this_month``, then
      every card's payment shortfall (``_auto_underfunded``), capped at the current
      Ready-to-Assign so we never over-assign.
    - ``assigned_last_month`` — repeat exactly what was assigned in the previous month.
    - ``spent_last_month`` — assign the absolute activity (spending) from the previous month.
    - ``average_assigned`` — average assigned per category over up to 12 months, none before
      the budget's first month (``_auto_history_months``).
    - ``average_spent`` — the same window's average absolute spending per category.
    - ``cover_overspending`` — top up every overspent SPENDING envelope to exactly 0 (a card's
      payment envelope is never a step — it is funded by coverage, see
      ``_auto_cover_overspending``), capped at the current Ready-to-Assign so we never
      over-assign (B4, restored 2026-09-09 and symmetric with ``underfunded``): covering a hole
      costs Ready to Assign, up to the hole's size. Run on a LATER month it writes future
      assignments, which reduce this month's displayed figure like any other future assignment.
    - ``reset_assigned`` — release all assigned amounts back to 0 (returns money to RTA).

    THE PREVIEW IS THE CONTRACT. For a mode in ``_COUPLED_AUTO_ASSIGN_MODES`` (this one and
    ``underfunded``) ``_op_auto_assign`` re-derives every step against the book as it stands
    after the previous write and clamps it to the figure below, so the confirm card and the end
    state cannot disagree; the rest have no read at ``month`` for another step's write to move,
    so their previewed steps already ARE the end state. Excluding payment envelopes is what
    makes cover AGREE rather than merely converge — a spending step's coverage credit is the
    only cross-row coupling this mode has, and no remaining step reads it."""
    month = _valid_month(month)
    mode = (mode or "").strip()
    if mode not in _AUTO_ASSIGN_MODES:
        raise ValidationError(
            f"unknown auto-assign mode: {mode!r}; valid: {sorted(_AUTO_ASSIGN_MODES)}"
        )

    ctx = _build_context(month)
    rows = ctx["_rows"]

    if mode == "reset_assigned":
        steps = _auto_reset(month, rows)
    elif mode == "underfunded":
        steps = _auto_underfunded(ctx, month, rows)
    elif mode == "cover_overspending":
        steps = _auto_cover_overspending(ctx, month, rows)
    elif mode == "assigned_last_month":
        steps = _auto_assigned_last_month(month, rows)
    elif mode == "spent_last_month":
        steps = _auto_spent_last_month(ctx, month, rows)
    else:  # average_assigned / average_spent
        steps = _auto_average(ctx, month, rows, mode)

    # Add an explicit delta to each step for callers that want it directly.
    for s in steps:
        s["delta"] = round(s["new_assigned"] - s["current_assigned"], 2)
    total_delta = round(sum(s["delta"] for s in steps), 2)
    return {"steps": steps, "total_delta": total_delta}


_COMMITMENTS_GROUP_NAME = "Commitments"


def sync_commitment_envelopes() -> dict:
    """Ensure every active commitment has a dedicated envelope in the pinned 'Commitments' group.

    Idempotent — safe to call on every page load. For each active commitment with ≥1 unpaid
    installment, ensures a dedicated envelope exists (created if absent), linked via
    ``commitment.envelope_id`` — a real foreign key (category-opaque-ids migration, decision #4).

    Sets NO target (owner 2026-07-10): the old "next installment ÷ months-until-due" auto-target
    was a naive proxy for the user's monthly set-aside and is REMOVED — the funding target is the
    user's / Lucius's to set. An envelope that has a LINKED CATEGORY is user-owned (migrated /
    adopted, ADR 2026-07-10) and is left entirely alone — never re-targeted, never auto-retired.
    A still-zero-category commitment that drops out (paid off / cancelled) has its envelope retired
    (``envelopes.force_delete_commitment_envelope`` — zeroed + archived) and its FK cleared. The
    Synced badge (the FK) stays either way. Returns ``{group_id, synced, removed}``.
    """
    from backend.db.commitments_store import CommitmentsStore
    from backend.services import commitments as commitments_service

    # ── 1. Active commitments = those with ≥1 unpaid installment ──
    # NO monthly-target math here (owner 2026-07-10): the old "next installment ÷ months-until-due"
    # auto-target was a naive proxy — it is NOT the user's real monthly set-aside (that lives in the
    # funding plan / is set explicitly). REMOVED. The funding target is now the user's / Lucius's to
    # set; the sync never derives one.
    active: dict[str, str] = {}  # commitment_id → name
    for c in CommitmentsStore().query_commitments(status="active"):
        if any(not i.get("paid_txn_id") and i.get("due_date") for i in c.get("installments", [])):
            active[c["id"]] = c["name"]

    # ── 2. Ensure 'Commitments' group exists ──
    all_groups = groups_store.list_ordered()
    c_group = next((g for g in all_groups if g.get("name") == _COMMITMENTS_GROUP_NAME), None)
    if c_group is None:
        c_group = groups_store.create({"name": _COMMITMENTS_GROUP_NAME, "order": 0})
    group_id = c_group["id"]

    # ── 3. Ensure each active commitment has a live envelope + current target ──
    synced = 0

    for cid, name in active.items():
        commitment = commitments_service.get_commitment(cid)
        env_id = commitment.get("envelope_id") if commitment else None
        envelope = envelopes_service.envelope_store.find_by_id(env_id) if env_id else None
        if envelope is None or envelope.get("archived"):
            # Bot-managed envelope: identity is the commitment.envelope_id FK, not the name.
            # This sync runs on every page load and must never throw, so it bypasses the
            # name-uniqueness guard (two commitments could share a name, or a name could
            # collide with a user envelope — neither should break the idempotent sync).
            envelope = envelopes_service.create_envelope(
                name, group_id=group_id, enforce_unique=False
            )
            env_id = envelope["id"]
            commitments_service.update_commitment(cid, {"envelope_id": env_id})
        elif envelope.get("group_id") != group_id:
            # Self-heal a drifted group placement (EXTRA A, 2026-07-04 owner incident): a
            # Lucius reorg once moved a commitment envelope out of the pinned group because
            # `_op_move_category` didn't share the `is_commitments_synced` guard merge/split/
            # delete already enforce (fixed there — this repairs an already-drifted book).
            envelopes_service.envelope_store.update_envelope(env_id, {"group_id": group_id})
        # The sync sets NO target (owner 2026-07-10 — the naive auto-target is gone); the funding
        # target is the user's / Lucius's to set. A categorized envelope is also user-owned and is
        # never auto-retired below. The FK/Synced badge stays either way.
        synced += 1

    # ── 4. Retire envelopes for commitments that dropped out ──
    removed = 0
    for c in CommitmentsStore().all():
        env_id = c.get("envelope_id")
        if not env_id or c["id"] in active:
            continue
        envelope = envelopes_service.envelope_store.find_by_id(env_id)
        if envelope is not None and envelope.get("category_ids"):
            continue  # user-owned (has a linked category) — never auto-retire; keep the FK/badge
        if envelope is not None and not envelope.get("archived"):
            # Self-contained (2026-09-26): force_delete_commitment_envelope now clears its own
            # target/note/cap as part of hard-deleting the row — no separate batched
            # target-popping pass needed here any more.
            envelopes_service.force_delete_commitment_envelope(env_id)
            removed += 1
        commitments_service.update_commitment(c["id"], {"envelope_id": None})

    logger.info(
        "sync_commitment_envelopes group=%s synced=%d removed=%d", group_id, synced, removed
    )
    return {"group_id": group_id, "synced": synced, "removed": removed}


_CARD_PAYMENTS_GROUP_NAME = "Credit Card Payments"
_SENTINEL_NAME_RE = re.compile(r"^__.+__$")
_SYNCED_GROUP_NAMES = frozenset({_COMMITMENTS_GROUP_NAME, _CARD_PAYMENTS_GROUP_NAME})


def sync_card_payment_envelopes() -> dict:
    """Sync a zero-category "payment envelope" for every non-archived, on-budget credit-card
    account — the YNAB-engine credit-card mode (ADR
    2026-07-09-ynab-engine-credit-card-payment-envelopes.md). Reuses the Commitments sync as
    its template (real envelope, pinned group, account FK, force-delete on drop-out) — see
    ``sync_commitment_envelopes``.

    Boot-ordered in ``main.py`` right after ``ensure_envelopes_migrated``. Payment envelopes are the
    ONLY credit-card engine (the "signed" mode was removed 2026-07-10), so this always runs.

    Idempotent — safe to call on every boot. For each account with
    ``cashflow.is_payment_tracked_card(a)`` (every ``credit`` account) that is non-archived
    and on-budget (``cashflow.is_on_budget`` — TYPE-ONLY since ADR
    2026-09-07-seven-account-types-one-axis: every ``credit`` account is on-budget and there is
    no per-account override to opt one out), ensures a
    dedicated envelope exists in the pinned 'Credit Card Payments' group (created if absent),
    linked via ``account.payment_envelope_id`` — a real foreign key, mirroring
    ``commitment.envelope_id``. NO auto-target (unlike commitments) — a payment envelope starts
    at whatever the auto-move fold + the user's own assigns land it at; nothing pre-funds it.

    RETIRE step: unlike a commitment (never hard-deleted), an account CAN be — so the "kept"
    set is diffed against every LIVE envelope currently sitting in the pinned group (not
    against the FK on each account row, which a hard-deleted account no longer has) — any such
    envelope that isn't in the fresh "kept" set (whatever the reason: archived, off-budget,
    retyped, or the account row is simply gone) is retired via
    ``envelopes.force_delete_card_payment_envelope`` and any account still carrying a now-stale
    FK has it cleared.

    Returns ``{group_id, synced, removed, retired}``. ``retired`` carries each retirement's own
    ``{envelope_id, name, zeroed_assignments, account_id}`` — WHAT was destroyed, not just how
    many. It was dropped on the floor until 2026-09-10: retiring zeroes the envelope's assigned
    amount in EVERY month, and since ``update_account`` started running this sync on a type flip
    that happens the instant a card is retyped to ``cash``, with nothing surfaced. The account
    PATCH choke forwards this to its own caller so the loss is at least disclosed.

    ``account_id`` is the account whose (now-stale) ``payment_envelope_id`` FK actually pointed
    at this envelope — read from the accounts snapshot BEFORE the FK-clear step below, so an
    archived card that still carries the pointer resolves to itself, not to nothing. This is a
    GLOBAL, idempotent reconciliation — it retires every stale envelope in the book on one pass,
    not just the account that happened to trigger the call — so a caller MUST filter ``retired``
    by this field before attributing a retirement to "the account I just wrote" (review
    2026-09-10: a later, unrelated edit that merely triggers this same sync was getting credited
    with — or, worse, silently dropping — an archived card's stale retirement)."""
    from backend.services.cashflow import is_on_budget

    all_groups = groups_store.list_ordered()
    c_group = next((g for g in all_groups if g.get("name") == _CARD_PAYMENTS_GROUP_NAME), None)
    if c_group is None:
        c_group = groups_store.create({"name": _CARD_PAYMENTS_GROUP_NAME, "order": 0})
    group_id = c_group["id"]

    cards = [
        a
        for a in svc.accounts.all()
        if not a.get("archived") and is_payment_tracked_card(a) and is_on_budget(a)
    ]

    synced = 0
    kept_env_ids: set[str] = set()
    for account in cards:
        env_id = account.get("payment_envelope_id")
        envelope = envelopes_service.envelope_store.find_by_id(env_id) if env_id else None
        if envelope is None or envelope.get("archived"):
            # Bot-managed envelope: identity is the account.payment_envelope_id FK, not the
            # name — two cards could share a display name, so bypass the uniqueness guard
            # exactly like the commitment sync does.
            envelope = envelopes_service.create_envelope(
                account.get("name") or "Card", group_id=group_id, enforce_unique=False
            )
            env_id = envelope["id"]
            svc.accounts.update_by_id(account["id"], {"payment_envelope_id": env_id})
        elif envelope.get("group_id") != group_id:
            # Self-heal a drifted group placement (mirrors sync_commitment_envelopes).
            envelopes_service.envelope_store.update_envelope(env_id, {"group_id": group_id})
        synced += 1
        kept_env_ids.add(env_id)

    # ── Retire envelopes for cards that dropped out (archived / off-budget / retyped / the
    # account row itself is gone) — diffed against the pinned group's LIVE membership, not the
    # accounts list, so a hard-deleted account's orphaned envelope is still caught.
    #
    # Owner snapshot taken BEFORE the retire loop (and well before the FK-clear loop below):
    # an archived/off-budget card's `payment_envelope_id` is untouched by archiving itself, so
    # it still names the stale envelope here — the only place a caller can later attribute the
    # retirement to the RIGHT account instead of whichever edit happened to trigger this pass.
    owner_by_envelope_id = {
        a["payment_envelope_id"]: a["id"]
        for a in svc.accounts.all()
        if a.get("payment_envelope_id")
    }
    retired: list[dict] = []
    for envelope in envelopes_service.envelope_store.all():
        if envelope.get("archived") or envelope.get("group_id") != group_id:
            continue
        if envelope["id"] in kept_env_ids:
            continue
        result = envelopes_service.force_delete_card_payment_envelope(envelope["id"])
        retired.append(
            {
                "envelope_id": result["envelope_id"],
                "name": result["name"],
                "zeroed_assignments": result["zeroed_assignments"],
                "account_id": owner_by_envelope_id.get(envelope["id"]),
            }
        )
    removed = len(retired)

    # Clear the FK on any (still-live) account that points at a now-retired envelope.
    for account in svc.accounts.all():
        env_id = account.get("payment_envelope_id")
        if env_id and env_id not in kept_env_ids:
            svc.accounts.update_by_id(account["id"], {"payment_envelope_id": None})

    logger.info(
        "sync_card_payment_envelopes group=%s synced=%d removed=%d zeroed=%s",
        group_id,
        synced,
        removed,
        [(r["envelope_id"], sorted(r["zeroed_assignments"])) for r in retired],
    )
    return {"group_id": group_id, "synced": synced, "removed": removed, "retired": retired}


def apply_budget_change(
    change: dict, *, conn: Connection | None = None, allow_negative: bool = False
) -> dict:
    """Apply ONE Planning mutation. Mirrors ``positions.apply_position_change``: validate
    BEFORE any write, route every op through a checkpoint-covered store, return a structured
    result. ``type`` ∈ {envelope_create, envelope_add_category, envelope_merge,
    envelope_split_category, envelope_rename, envelope_note, envelope_delete,
    assign,
    move_money, set_target, group_create, group_rename, group_reorder, group_delete,
    move_category, reorder_categories, category_create, category_rename, category_delete,
    auto_assign}. Category create/rename/delete delegate to ``services.categories`` (the
    atomic cross-store re-key); the envelope ops delegate to ``services.envelopes`` (RFC
    full-split §Backend 1 + contract addendum; P4 spec §4.2). ``envelope_delete`` hard-deletes
    with a server-held Undo token — there is no ``envelope_restore`` op any more (ADR
    2026-09-26-deleting-an-envelope-deletes-it; its Undo is
    ``services.envelopes.undo_delete_envelope``, a separate REST-only route, mirroring
    ``fresh_start``/``undo_fresh_start``). Raises ValidationError/NotFoundError/ConflictError
    (mapped to HTTP 422/404/409 by the route).

    ``conn``/``allow_negative`` are the ``assign`` op's composition seam ONLY (``fresh_start``
    zeroes a month's assignments through this choke inside its own transaction) — passing
    ``conn`` with any other op RAISES rather than being silently ignored, since a caller that
    believes its write joined a transaction it didn't is the bug this guard exists to prevent."""
    ctype = change.get("type")
    if conn is not None and ctype != "assign":
        raise ValidationError(f"conn= composes the 'assign' op only, not {ctype!r}")
    if ctype == "assign":
        return _op_assign(change, conn=conn, allow_negative=allow_negative)
    if ctype == "envelope_create":
        return _op_envelope_create(change)
    if ctype == "envelope_add_category":
        return _op_envelope_add_category(change)
    if ctype == "envelope_merge":
        return _op_envelope_merge(change)
    if ctype == "envelope_split_category":
        return _op_envelope_split_category(change)
    if ctype == "envelope_rename":
        return _op_envelope_rename(change)
    if ctype == "envelope_note":
        return _op_envelope_note(change)
    if ctype == "envelope_delete":
        return _op_envelope_delete(change)
    if ctype == "move_money":
        return _op_move_money(change)
    if ctype == "set_target":
        return _op_set_target(change)
    if ctype == "group_create":
        return _op_group_create(change)
    if ctype == "group_rename":
        return _op_group_rename(change)
    if ctype == "group_reorder":
        return _op_group_reorder(change)
    if ctype == "group_delete":
        return _op_group_delete(change)
    if ctype == "move_category":
        return _op_move_category(change)
    if ctype == "reorder_categories":
        return _op_reorder_categories(change)
    if ctype == "category_create":
        return _op_category_create(change)
    if ctype == "category_rename":
        return _op_category_rename(change)
    if ctype == "category_delete":
        return _op_category_delete(change)
    if ctype == "auto_assign":
        return _op_auto_assign(change)
    raise ValidationError(f"unknown budget change type: {ctype!r}")


def add_to_assigned(
    envelope_id: str, amount: float, *, month: str | None = None, skip_if_negative: bool = False
) -> float | None:
    """Add ``amount`` to what ``envelope_id`` already has assigned in ``month`` (default: the
    current month), through the ``assign`` op — the same choke an Assigned-cell edit uses, so
    the write is validated, receipt-recorded and checkpoint-covered exactly like one.

    ``assign`` sets an ABSOLUTE figure; the read-add-write around it lives here because the
    current figure is planning's to read, and a caller doing that itself would be re-deriving
    envelope-keyed storage from outside this module. Returns the new assigned figure.

    ``amount`` must be positive. A negative or zero delta has no caller and no meaning here:
    a NEGATIVE assigned slot is a ``move_money`` pull-back artifact, not money to carry
    anywhere, and ``assign`` refuses a negative result by invariant.

    That same invariant can refuse the ADDITION, on the DESTINATION's state rather than on
    anything the caller got wrong: a slot already pulled back below zero stays below zero
    unless the whole of ``amount`` covers the hole. ``skip_if_negative`` is for the caller
    that has somewhere else to leave the money and must not fail — the write is skipped and
    ``None`` returned instead of raising. All-or-nothing by arithmetic, not by policy: any
    PARTIAL addition small enough to fit is one the full amount would have fitted too."""
    if amount <= 0:
        raise ValidationError("amount must be greater than 0")
    month = _valid_month(month or _this_month())
    envelope = _resolve_envelope(envelope_id)
    new_amount = round(_assigned_amt(month, envelope["id"]) + amount, 2)
    if new_amount < 0 and skip_if_negative:
        logger.info(
            "add_to_assigned skipped +%s on %s/%s — would leave assigned at %s",
            amount,
            month,
            envelope["id"],
            new_amount,
        )
        return None
    apply_budget_change(
        {
            "type": "assign",
            "month": month,
            "envelope_id": envelope["id"],
            "amount": new_amount,
        }
    )
    return new_amount


# ── Choke-seam sweep (plan 4a task 5) ────────────────────────────────────────────────
# Every ``_op_*`` above was examined for a write to ≥2 wave tables (categories/envelope_groups/
# envelopes/budget_assignments/…) done as separate, non-atomic writes. Composed (this file):
# ``_op_group_delete`` (envelope_groups + envelopes — see the locked_unit_of_work block inside
# it). Composed (services.envelopes/services.categories, called from here):
# envelope merge/delete/force-delete/add_category and category delete/merge — see those
# modules' own locked_unit_of_work blocks; this dispatcher's own surface is unchanged either
# way. Explicitly NOT composed:
#   * ``apply_income_change`` (services/income.py) — ``income_events`` is single-table, then
#     the ledger choke (services/ledger.py) opens its OWN separate transaction for the txn it
#     creates; that boundary is deliberate (two different entities, two different chokes).
#   * ``apply_automation_change`` (services/automations.py) — ``automations`` only, and it is
#     still a JsonStore file this wave (the hybrid period, spec D7) — nothing to compose it
#     WITH yet.
#   * ``assign``/``move_money`` (``_op_assign``/``_op_move_money`` above) — same-table,
#     multi-ROW writes to ``budget_assignments`` (not a multi-TABLE write); out of this task's
#     "genuinely write ≥2 wave tables" binding.
#   * ``group_create``/``group_rename``/``group_reorder`` — ``envelope_groups`` only.
#   * ``reorder_categories`` — ``envelopes`` only (multiple rows, one table).


# ── write-receipt reporting ────────────────────────────────────────────────────────
#
# Every id-bearing Planning op reports onto the turn's receipt so the chip can say "1
# envelope edited" beside the transactions the same turn wrote. Four deliberate calls:
#
#   * Reported INSIDE each ``_op_*``, never in a shared wrapper around the dispatcher.
#     ``services.domain_introspect_ast`` DERIVES each op's field set from the dispatcher's
#     source, so a wrapper reading ``change`` on the path every branch returns through
#     would be attributed to all 21 ops and make ``describe`` report fields they never
#     read. Each call below uses only values its own op already resolved.
#   * The composite (month, envelope_id) key is NOT faked into an entity id. The entity
#     WRITTEN by an assign / move / target is the ENVELOPE; the month is context and rides
#     in ``meta``, where the chip's sublabel renders it.
#   * ``move_money`` is TWO update records — two envelopes really did change. The ``__rta``
#     sentinel end is not an entity and records nothing.
#   * The GROUP ops (create/rename/reorder) record NOTHING, and that is correct rather than a
#     gap: none of them writes an envelope row — they touch ``envelope_groups`` only. A row
#     for an entity that was not written would be a lie, and there is no ``group`` mention
#     type to render one with anyway. ``group_delete`` DOES move the group's envelopes into
#     Others, so it records those envelope updates (and nothing for the group itself).


def _rec(op: str, entity_id, entity_type: str, verb: str, month: str = "") -> None:
    """Best-effort receipt record. Never raises — a mechanism that only DESCRIBES a write
    must not be able to fail it."""
    try:
        meta = {"verb": verb}
        if month:
            meta["month"] = month
        record_write(op, str(entity_id or ""), entity_type=entity_type, meta=meta)
    except Exception:  # noqa: BLE001
        pass


def _envelope_id_or_blank(ref) -> str:
    """The envelope a category ref resolves to, or "" (which ``record_write`` drops)."""
    try:
        return _resolve_envelope(str(ref or ""))["id"]
    except Exception:  # noqa: BLE001
        return ""


def _op_envelope_create(change: dict) -> dict:
    """Create a standalone envelope — NO category attached (RFC full-split §Backend 1).
    ``color`` (handoff delta #5) is optional — hex-validated, defaults to ""."""
    name = (change.get("name") or "").strip()
    group_id = (change.get("group_id") or "").strip() or None
    color = (change.get("color") or "").strip() or None
    envelope = envelopes_service.create_envelope(name, group_id, color)
    _rec("create", envelope.get("id"), "envelope", "envelope_create")
    return {"ok": True, "envelope": envelope}


def _op_envelope_add_category(change: dict) -> dict:
    """Link an UNLINKED category to an existing envelope (contract addendum — not composable
    from split+merge, since an unlinked category has no source envelope)."""
    envelope_id = (change.get("envelope_id") or "").strip()
    category_id = (change.get("category_id") or "").strip()
    if not envelope_id or not category_id:
        raise ValidationError("envelope_id and category_id are required")
    result = envelopes_service.add_category(envelope_id, category_id)
    _rec("update", envelope_id, "envelope", "envelope_add_category")
    return result


def _op_envelope_merge(change: dict) -> dict:
    """Fold one envelope into another (P4 spec §4.2) — validation + the actual store writes
    live in ``services.envelopes`` (co-located with the entity it mutates)."""
    source_id = (change.get("source_envelope_id") or "").strip()
    target_id = (change.get("target_envelope_id") or "").strip()
    if not source_id or not target_id:
        raise ValidationError("source_envelope_id and target_envelope_id are required")
    result = envelopes_service.merge_envelope(source_id, target_id)
    # A merge writes BOTH envelopes: the target absorbs, the source stops existing.
    _rec("update", target_id, "envelope", "envelope_merge")
    _rec("delete", source_id, "envelope", "envelope_merge")
    return result


def _op_envelope_split_category(change: dict) -> dict:
    """Unlink one category from an envelope — it becomes envelope-less (→ Unbudgeted), no
    envelope is minted (budget-envelopes handoff port, delta #1)."""
    envelope_id = (change.get("envelope_id") or "").strip()
    category_id = (change.get("category_id") or "").strip()
    if not envelope_id or not category_id:
        raise ValidationError("envelope_id and category_id are required")
    result = envelopes_service.split_category(envelope_id, category_id)
    _rec("update", envelope_id, "envelope", "envelope_split_category")
    return result


def _op_envelope_rename(change: dict) -> dict:
    """Rename an envelope directly (only reachable for N>1 rows in the UI — a 1:1 row still
    renames via its category, RFC P4 §2.3/§2.4) and/or set its display colour (handoff delta
    #5 widens this from name-only to name?/color?). A key that's simply ABSENT from the
    change dict means "leave unchanged" — only an explicit blank ``name`` is refused."""
    envelope_id = (change.get("envelope_id") or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    result = envelopes_service.rename_envelope(envelope_id, change.get("name"), change.get("color"))
    _rec("update", envelope_id, "envelope", "envelope_rename")
    return result


def _op_envelope_note(change: dict) -> dict:
    """Set or clear (``note=''``) an envelope's note — through this choke so it rides the same
    confirm card and checkpoint as the envelope's other edits (``update_envelopes``)."""
    envelope_id = (change.get("envelope_id") or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    note = change.get("note") or ""
    write_category_note(envelope_id, note)
    _rec("update", envelope_id, "envelope", "envelope_note")
    return {"envelope_id": envelope_id, "note": note.strip()}


def _envelope_delete_delta(ctx: dict, month: str, envelope: dict) -> dict:
    """The ΔRTA a delete of ``envelope`` realizes for ``month``, read (never written) against
    ``ctx`` — shared by ``envelope_delete_preview`` (the confirm dialog, read against the LIVE
    context) and ``_op_envelope_delete`` (the realized figure, read an instant before the write).

    docs/INVARIANTS.md / ``tests/test_delete_envelope_never_lowers_rta.py`` state the identity as

        ΔRTA(m) = max(0, prev_carry) + assigned(m, E) + Σ_c max(0, cash_c) + Σ_c non_cash_c

    which is the SAME number as ``available(m, E) − Σ_c min(0, cash_activity(m, c))`` (expand
    ``available = max(0, prev_carry) + assigned + Σ_c(cash_c + non_cash_c)`` and use
    ``x − min(0, x) = max(0, x)``) — the form computed here, since it costs one ``_available``
    call instead of re-deriving the carry walk. The sum is PER MEMBER CATEGORY, never over the
    envelope's own summed activity: a merged envelope's members hold separately once detached
    (``test_a_merged_envelopes_members_hold_separately``), so netting a spend against a sibling's
    refund before the ``min(0, ·)`` clamp would cancel a hole that doesn't actually close.

    A LOWER BOUND, not always the exact realized figure, when a member carries LIVE
    (non-archived) payment-tracked card spend this ``_available`` call already priced in as
    covered: ``delete_envelope`` unlinking the category forces that spend's coverage headroom to
    0 (``_capped_card_credits``: ``spend_env_id is None`` ⇒ ``headroom = 0``), so whatever this
    envelope had ALREADY credited to the card's payment envelope stops being credited — a SECOND
    envelope's ``available`` (and, via ITS own carry, every later month's) rises by exactly the
    withdrawn credit, on top of this function's own figure. That credit was always ``≥ 0``
    (``covered = min(amt, headroom)``, never negative), so the omission only ever makes the
    REALIZED change ≥ what this returns — never less: this function never OVER-promises, it can
    only under-count a live card's freed-up coverage (verified as a property over random books,
    ``tests/test_delete_envelope_never_lowers_rta.py``). Exact whenever the envelope's members
    carry no such spend — true for every plain cash scenario, including the launch-board repro
    this function was written to fix.

    Returns ``{month, envelope_id, available, delta_rta}`` — ``available`` is what the
    delete-confirm dialog's "unresolved overspend keeps holding" bullet reads, ``delta_rta`` is
    what actually returns to Ready to Assign (CLAUDE.md: "Deleting an envelope never LOWERS
    Ready to Assign" — the one pinned, deferred exception aside)."""
    envelope_id = envelope["id"]
    available = _available(ctx, month, envelope_id)
    cash = _cash_activity_map(ctx, month)
    held = sum(min(0.0, cash.get(c, 0.0)) for c in (envelope.get("category_ids") or []))
    return {
        "month": month,
        "envelope_id": envelope_id,
        "available": round(available, 2),
        "delta_rta": round(available - held, 2),
    }


def envelope_delete_preview(envelope_id: str, month: str) -> dict:
    """What deleting ``envelope_id`` would do to Ready to Assign for ``month`` — computed, never
    written; the number the delete-confirm dialog promises (Zero-Based's DeleteEnvelopeModal and
    50/30/20's own copy of it alike). Mirrors ``fresh_start_preview``'s contract exactly: nothing
    is persisted, no store is touched, and the fold runs against the REAL, current context — a
    write landing between this read and the confirm click can move the number, same as every
    other preview in this file.

    Applies the SAME guard ``delete_envelope`` itself does (``envelopes_service.
    assert_deletable``), so a preview is never offered for a row the write would refuse anyway —
    see ``_envelope_delete_delta`` for the figure's derivation."""
    _valid_month(month)
    envelope_id = (envelope_id or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    envelope = envelopes_service.envelope_store.find_by_id(envelope_id)
    if envelope is None:
        raise NotFoundError(f"envelope {envelope_id} not found")
    envelopes_service.assert_deletable(envelope)
    return _envelope_delete_delta(_build_context(month), month, envelope)


def _op_envelope_delete(change: dict) -> dict:
    """Delete an envelope FOR REAL (ADR 2026-09-26-deleting-an-envelope-deletes-it) — detach
    members, release its reserve to RTA, hard-delete the row. The result carries an
    ``undo_token`` a caller can replay via ``services.envelopes.undo_delete_envelope`` /
    ``POST /api/planning/envelopes/undo-delete``.

    ``change["month"]`` (optional — the REST route always sends it, defaulted to this month)
    names which month's realized ΔRTA to echo back into the result (``month``, ``available``,
    ``delta_rta`` — ``envelope_delete_preview``'s own shape), read an instant BEFORE the write so
    it is the same figure the confirm dialog just showed, not re-derived after the row and its
    assignments are already gone. Omitted entirely for a caller that doesn't send one (e.g. an
    older Lucius write) — the result then carries no delta, exactly as before this field existed."""
    envelope_id = (change.get("envelope_id") or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    month = change.get("month")
    delta = envelope_delete_preview(envelope_id, month) if month else None
    result = envelopes_service.delete_envelope(envelope_id)
    _rec("delete", envelope_id, "envelope", "envelope_delete")
    return {**result, **delta} if delta is not None else result


def _live_step_amount(month: str, mode: str, step: dict) -> float | None:
    """This step's figure re-derived against the book as it stands NOW, bounded above by the
    apply-time preview's own figure for the row. ``None`` = the step no longer applies — its row
    dropped out of the fresh preview because an earlier step already closed the hole."""
    for live in auto_assign_preview(month, mode).get("steps", []):
        if live["envelope_id"] == step["envelope_id"]:
            return round(min(live["new_assigned"], step["new_assigned"]), 2)
    return None


def _op_auto_assign(change: dict) -> dict:
    """Apply an auto-assign mode so the end state is exactly what the preview promised.

    The preview is both the CONTRACT and the upper bound; for a COUPLED mode a live
    re-derivation per step is what makes the end state its own promise. Computing N steps against
    ONE frozen context and
    then writing them one at a time was the defect (security review 2026-09-08, HIGH): a card's
    payment envelope is credited by ``_capped_card_credits`` the moment the spending row it
    draws on is funded, so a later step sized against the pre-write book landed on top of a
    credit that did not exist when it was computed. A coupled mode's steps therefore each re-run
    the preview against the freshly-committed book and take their OWN row's figure, clamped to
    the apply-time preview's figure for that row (never above it, whatever the live book now
    says — the confirm card's amounts are never replayed) and skipped entirely when the row has
    dropped out.

    NOT one transaction, deliberately: a re-derivation has to READ what the previous step WROTE,
    and ``_build_context`` reads through its own connection — composing the steps into one open
    transaction would re-derive every one of them against the same pre-write snapshot, which is
    exactly the bug this closes. Each step keeps its own lock-held write, as before.

    The rebuild is paid ONLY by the modes in ``_COUPLED_AUTO_ASSIGN_MODES``, and that set is a
    read/write-set proof about ``month`` (see its comment), not an intuition about which modes
    look risky: an apply writes only ``assigned(month, env)``, and every other mode's figure
    reads either a constant or a month STRICTLY BEFORE ``month``, so its previewed steps ARE its
    end state. A coupled N-step apply costs N previews (the first step needs no rebuild — nothing
    has been written yet), measured at ~50ms/step on the 3k-transaction demo book; an uncoupled
    one costs the single preview it always did.

    ``total_delta`` reports what was ACTUALLY written, not what the preview proposed — the two
    are equal on every book where no step moved, and where one did the honest figure is the one
    the write receipt should carry."""
    month = _valid_month(change.get("month") or _this_month())
    mode = (change.get("mode") or "").strip()
    if not mode:
        raise ValidationError("mode is required")
    steps = auto_assign_preview(month, mode).get("steps", [])
    rederive = mode in _COUPLED_AUTO_ASSIGN_MODES
    applied = 0
    total_delta = 0.0
    for index, step in enumerate(steps):
        env_id = step["envelope_id"]
        amount = (
            _live_step_amount(month, mode, step)
            if rederive and index
            else round(step["new_assigned"], 2)
        )
        if amount is None:
            continue
        current = _assigned_amt(month, env_id)
        _set_assigned_amt(month, env_id, amount)
        # Reported PER ROW here rather than once for the whole op: auto-assign really does
        # write N envelopes, and a single aggregate record would make the chip claim one
        # write where N happened.
        _rec("update", env_id, "envelope", "auto_assign", month)
        applied += 1
        total_delta = round(total_delta + amount - current, 2)
    logger.info("apply_budget_change auto_assign %s/%s applied=%d", month, mode, applied)
    return {"applied": applied, "total_delta": total_delta}


def _require_envelope_id(ref: str) -> str:
    """Validate ``ref`` is a non-blank id that resolves to a live envelope. ValidationError
    (never NotFoundError) so every write-path caller (assign/move_money/set_target) gets a
    uniform 422 on a bad ref."""
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("envelope_id is required")
    try:
        return _resolve_envelope(ref)["id"]
    except NotFoundError:
        raise ValidationError(
            f"'{ref}' is not a known envelope — call list_envelopes for valid ids"
        )


def _op_assign(
    change: dict, *, conn: Connection | None = None, allow_negative: bool = False
) -> dict:
    """``allow_negative`` relaxes the "cannot be negative" guard, which exists to stop a USER
    typing a negative into the Assigned cell — not because the value is invalid: ``move_money``
    out of a row legitimately drives assigned negative (its docstring), so a negative slot is a
    real state a book can be in. ``undo_fresh_start`` therefore needs it to put a snapshot back
    byte-identically. Keyword-only and never set from a wire payload — but the guarantee is the
    SOURCE of the values, not the kwarg's un-nameability: its callers are ``undo_fresh_start``,
    replaying a before-image the server itself wrote, and the YNAB migration, writing YNAB's figures."""
    month = _valid_month(change.get("month"))
    env_id = _require_envelope_id(change.get("envelope_id"))
    try:
        amount = round(float(change.get("amount")), 2)
    except (TypeError, ValueError):
        raise ValidationError("amount must be a number")
    if not math.isfinite(amount):
        raise ValidationError("amount must be a finite number")
    if amount < 0 and not allow_negative:
        raise ValidationError("assigned amount cannot be negative (use move_money to pull back)")
    _set_assigned_amt(month, env_id, amount, conn=conn)
    _rec("update", env_id, "envelope", "assign", month)
    logger.info("apply_budget_change assign %s/%s = %s", month, env_id, amount)
    return {"ok": True, "month": month, "envelope_id": env_id, "assigned": amount}


_RTA_SENTINEL = "__rta"  # Ready-to-Assign as a move endpoint (handoff delta #9)


def _op_move_money(change: dict) -> dict:
    """Move assigned money between two envelope rows within a month — OR, per the handoff's
    "RTA in the Move/Cover picker" delta, between an envelope row and Ready-to-Assign itself
    via the ``__rta`` sentinel on exactly one end (both ends ``__rta`` is refused — nothing to
    move). ``__rta`` is a one-delta move, matching ``assign``'s own math: releasing TO RTA
    (``to_envelope_id="__rta"``) decrements the source's assigned; funding FROM RTA
    (``from_envelope_id="__rta"``) increments the destination's assigned with NO RTA cap —
    exactly like a fresh ``assign`` call, RTA is simply allowed to go negative (the
    over-assigned case already has its own coach copy).

    The cap on moving money OUT of a row (envelope→envelope, or envelope→``__rta``) is its
    AVAILABLE for THIS month, not this-month Assigned (FEB-02 fix, 2026-07-04 — YNAB-correct:
    carried-over money from a prior month is movable, not just what was freshly assigned).
    Assigned is therefore allowed to go NEGATIVE — the availability identity
    ``prev + assigned + activity`` already absorbs it exactly like an in-month overspend does,
    and ``BudgetAssignmentsStore.set_assigned`` stores a negative amount as-is (only an EXACT
    zero pops the entry)."""
    month = _valid_month(change.get("month"))
    from_ref = (change.get("from_envelope_id") or "").strip()
    to_ref = (change.get("to_envelope_id") or "").strip()
    from_is_rta = from_ref == _RTA_SENTINEL
    to_is_rta = to_ref == _RTA_SENTINEL
    if from_is_rta and to_is_rta:
        raise ValidationError("from_envelope_id and to_envelope_id cannot both be Ready to Assign")
    try:
        amount = round(float(change.get("amount")), 2)
    except (TypeError, ValueError):
        raise ValidationError("amount must be a number")
    if amount <= 0:
        raise ValidationError("move amount must be greater than 0")

    ctx = _build_context(month)

    if from_is_rta or to_is_rta:
        return _op_move_money_rta(
            ctx, month, from_is_rta, to_ref if from_is_rta else from_ref, amount
        )

    src = _require_envelope_id(from_ref)
    dst = _require_envelope_id(to_ref)
    if src == dst:
        raise ValidationError("from_envelope_id and to_envelope_id must differ")
    _check_move_cap(ctx, month, src, amount)
    new_src = round(_assigned_amt(month, src) - amount, 2)
    new_dst = round(_assigned_amt(month, dst) + amount, 2)
    _set_assigned_amt(month, src, new_src)
    _set_assigned_amt(month, dst, new_dst)
    _rec("update", src, "envelope", "move_money", month)
    _rec("update", dst, "envelope", "move_money", month)
    logger.info("apply_budget_change move %s %s→%s %s", month, src, dst, amount)
    return {
        "ok": True,
        "month": month,
        "from_envelope_id": src,
        "to_envelope_id": dst,
        "from_assigned": new_src,
        "to_assigned": new_dst,
    }


def _check_move_cap(ctx: dict, month: str, src: str, amount: float) -> None:
    """Refuse moving more than ``src``'s row has AVAILABLE this month (FEB-02 fix — see
    ``_op_move_money``'s docstring)."""
    available_src = _available(ctx, month, src)
    if amount > available_src:
        raise ValidationError(
            f"cannot move more than '{src}' has available this month ({available_src:.2f})"
        )


def _op_move_money_rta(
    ctx: dict, month: str, from_is_rta: bool, other_ref: str, amount: float
) -> dict:
    """The ``__rta``-sentinel half of ``_op_move_money`` — see its docstring for the math."""
    if from_is_rta:
        dst = _require_envelope_id(other_ref)
        new_dst = round(_assigned_amt(month, dst) + amount, 2)  # no RTA cap — matches assign
        _set_assigned_amt(month, dst, new_dst)
        _rec("update", dst, "envelope", "move_money", month)
        logger.info("apply_budget_change move %s __rta→%s %s", month, dst, amount)
        return {
            "ok": True,
            "month": month,
            "from_envelope_id": _RTA_SENTINEL,
            "to_envelope_id": dst,
            "to_assigned": new_dst,
        }
    src = _require_envelope_id(other_ref)
    _check_move_cap(ctx, month, src, amount)  # release-to-RTA keeps the same available cap
    new_src = round(_assigned_amt(month, src) - amount, 2)
    _set_assigned_amt(month, src, new_src)
    _rec("update", src, "envelope", "move_money", month)
    logger.info("apply_budget_change move %s %s→__rta %s", month, src, amount)
    return {
        "ok": True,
        "month": month,
        "from_envelope_id": src,
        "to_envelope_id": _RTA_SENTINEL,
        "from_assigned": new_src,
    }


def _op_set_target(change: dict) -> dict:
    env_id = _require_envelope_id(change.get("envelope_id"))
    # NB: never fall back to change["type"] here — that is the op discriminator ("set_target"),
    # not the target kind. Only "target_type" carries the target kind (None/"" = clear).
    ttype = (change.get("target_type") or "").strip() or None
    snooze_until = (change.get("snooze_until") or "").strip() or None
    if snooze_until and len(snooze_until) < 7:
        raise ValidationError("snooze_until must be a month (YYYY-MM)")
    targets = read_category_targets()

    # Snooze-only: no new type given but a snooze intent is → suspend (or resume) the EXISTING
    # target, preserving its kind/amount/date — so the "Snooze this month" affordance never
    # erases the underlying target. We take this path when EITHER a snooze month is given
    # (snooze) OR the saved target is currently snoozed and we're clearing the flag (un-snooze).
    # A plain type='' with no snooze on a non-snoozed target still CLEARS the whole target.
    if ttype is None:
        existing = _target_for(env_id, targets)
        existing_snoozed = bool(existing and existing.get("snooze_until"))
        if existing and (snooze_until or existing_snoozed):
            spec = {k: v for k, v in existing.items() if k != "snooze_until"}
            if snooze_until:
                spec["snooze_until"] = snooze_until
            _set_target_for(env_id, spec, targets)
            write_category_targets(targets)
            _rec("update", env_id, "envelope", "set_target")
            logger.info("apply_budget_change set_target(snooze) %s = %s", env_id, spec)
            return {"ok": True, "envelope_id": env_id, "target": spec}
        _set_target_for(env_id, None, targets)  # clear
        write_category_targets(targets)
        _rec("update", env_id, "envelope", "set_target")
        return {"ok": True, "envelope_id": env_id, "target": None}

    if ttype not in TARGET_TYPES:
        raise ValidationError("target type must be one of: " + ", ".join(TARGET_TYPES))
    try:
        amount = round(float(change.get("amount")), 2)
    except (TypeError, ValueError):
        raise ValidationError("amount must be a number")
    if amount <= 0:
        raise ValidationError("target amount must be greater than 0 (omit type to clear)")
    spec: dict = {"type": ttype, "amount": amount}
    # needs_whole_amount defaults True; only write False to keep stored data lean.
    needs_whole_amount = change.get("needs_whole_amount", True)
    if not needs_whole_amount:
        spec["needs_whole_amount"] = False
    if ttype == "by_date":
        tdate = (change.get("date") or "").strip()
        if len(tdate) < 7:
            raise ValidationError("by_date target requires a 'date' (YYYY-MM-DD)")
        spec["date"] = tdate
    _apply_target_deadline_fields(change, ttype, spec)
    if snooze_until:
        spec["snooze_until"] = snooze_until
    _set_target_for(env_id, spec, targets)
    write_category_targets(targets)
    _rec("update", env_id, "envelope", "set_target")
    logger.info("apply_budget_change set_target %s = %s", env_id, spec)
    return {"ok": True, "envelope_id": env_id, "target": spec}


def _apply_target_deadline_fields(change: dict, ttype: str, spec: dict) -> None:
    """Validate + attach the optional target-deadline fields (handoff delta #13):
    ``due_weekday`` (weekly only, 0-6), ``due_day`` (monthly only, 1-28 or -1 = last day),
    ``date`` (yearly only here — ``by_date``'s own required ``date`` is handled by the
    caller). Refuses a field paired with the wrong target type — mismatched pairs are a
    ValidationError, never silently dropped."""
    due_weekday = change.get("due_weekday")
    if due_weekday is not None:
        if ttype != "weekly":
            raise ValidationError("due_weekday is only valid for a weekly target")
        spec["due_weekday"] = _valid_due_weekday(due_weekday)

    due_day = change.get("due_day")
    if due_day is not None:
        if ttype != "monthly":
            raise ValidationError("due_day is only valid for a monthly target")
        spec["due_day"] = _valid_due_day(due_day)

    tdate = (change.get("date") or "").strip()
    if not tdate:
        return
    if ttype == "yearly":
        spec["date"] = tdate
    elif ttype != "by_date":
        raise ValidationError("date is only valid for a yearly or by_date target")


def _valid_due_weekday(value) -> int:
    try:
        weekday = int(value)
    except (TypeError, ValueError):
        raise ValidationError("due_weekday must be an integer 0-6 (Monday=0)")
    if not 0 <= weekday <= 6:
        raise ValidationError("due_weekday must be 0-6 (Monday=0)")
    return weekday


def _valid_due_day(value) -> int:
    try:
        day = int(value)
    except (TypeError, ValueError):
        raise ValidationError("due_day must be an integer 1-28, or -1 for the last day")
    if day != -1 and not 1 <= day <= 28:
        raise ValidationError("due_day must be 1-28, or -1 for the last day of the month")
    return day


def _valid_group_name(raw) -> str:
    """A group's display name: non-blank, never one of the app's own groups (they are recognised
    BY NAME and locked, so a user group sharing one would lock whatever lands in it — and moving
    rows in is refused anyway, ``_op_move_category``), and never a ``__x__`` sentinel (those are
    the budget's own synthetic ids — ``__unsorted__``, ``__unbudgeted__`` — and must not be
    mistaken for a real group)."""
    name = (raw or "").strip()
    if not name:
        raise ValidationError("group name is required")
    if name in _SYNCED_GROUP_NAMES:
        raise ValidationError(f"'{name}' is a group the app keeps — pick another name")
    if _SENTINEL_NAME_RE.match(name):
        raise ValidationError(f"'{name}' is reserved for the app — pick another name")
    return name


def _op_group_create(change: dict) -> dict:
    """Create an empty envelope group — validated before anything is written. Envelopes are
    placed into it afterwards (``move_category``); a group never lists categories."""
    group = groups_store.create({"name": _valid_group_name(change.get("name"))})
    return {"ok": True, "group": group}


def _op_group_rename(change: dict) -> dict:
    gid = change.get("group_id") or ""
    patch = {}
    if change.get("name") is not None:
        patch["name"] = _valid_group_name(change.get("name"))
    if change.get("collapsed") is not None:
        patch["collapsed"] = bool(change.get("collapsed"))
    updated = groups_store.update_group(gid, patch)
    if updated is None:
        raise NotFoundError(f"group {gid} not found")
    return {"ok": True, "group": updated}


def _op_group_reorder(change: dict) -> dict:
    order = change.get("order") or []
    if not isinstance(order, list):
        raise ValidationError("order must be a list of group ids")
    groups = groups_store.reorder([str(g) for g in order])
    return {"ok": True, "groups": groups}


def _op_group_delete(change: dict) -> dict:
    """Delete a group; its envelopes are NOT lost — they move into the persisted "Others"
    catch-all so no envelope is ever orphaned (PRD AC-11). The Others group itself is the
    default catch-all and cannot be deleted (deleting it would orphan everything)."""
    gid = change.get("group_id") or ""
    group = groups_store.find_by_id(gid)
    if group is None:
        raise NotFoundError(f"group {gid} not found")
    if group.get("is_others"):
        raise ValidationError("the 'Others' group is the default catch-all and cannot be deleted")
    others = groups_store.ensure_others()
    envelope_store = envelopes_service.envelope_store
    envelopes = envelopes_service.all_envelopes()
    members = sorted(
        (e for e in envelopes if e.get("group_id") == gid), key=lambda e: e.get("order") or 0
    )
    tail = max(
        (e.get("order") or 0 for e in envelopes if e.get("group_id") == others["id"]), default=-1
    )
    with locked_unit_of_work(envelope_groups_table, envelopes_table) as conn:
        for offset, e in enumerate(members, start=1):
            patch = {"group_id": others["id"], "order": tail + offset}
            envelope_store.update_envelope(e["id"], patch, conn=conn)
        groups_store.delete(gid, conn=conn)
    for e in members:
        _rec("update", e["id"], "envelope", "group_delete")
    return {"ok": True, "deleted": gid}


def _op_move_category(change: dict) -> dict:
    """Move a budget ROW — an envelope, referenced by its own id — into the destination group.
    ``envelope.group_id``/``order`` is the ONLY placement authority ``budget_month`` reads, and
    this op writes nothing else. A category (a pure label) belongs to no group, so an
    INCOME-kind category ref is refused: envelopes are a spending-axis concept (never minted for
    income) and income has no group to move into.

    Optional ``index``: insert the row at this position AMONG the destination group's OTHER
    rendered rows (clamped to [0, len]); omitted/None → append at the end.

    A Commitments-synced envelope is REFUSED (EXTRA A, 2026-07-04 owner incident: a Lucius
    reorg moved commitment envelopes out of the pinned Commitments group because this was the
    one write path that lacked the ``is_commitments_synced`` guard merge/split/delete already
    enforce) — ``sync_commitment_envelopes`` self-heals an already-drifted book on its next
    run."""
    ref = (change.get("category") or "").strip()
    if not ref:
        raise ValidationError("category is required")
    dest_id = change.get("group_id") or ""
    dest = groups_store.find_by_id(dest_id)
    if dest is None:
        raise NotFoundError(f"group {dest_id} not found")
    # The destination half of the synced-group guard below: the app keeps these groups, and a
    # row moved in could never be moved back out (the table locks them).
    if dest.get("name", "") in _SYNCED_GROUP_NAMES:
        raise ValidationError(
            f"'{dest.get('name')}' is kept in sync by the app — nothing can be moved into it"
        )
    raw_index = change.get("index")
    index = None
    if raw_index is not None:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            raise ValidationError("index must be an integer")

    if ref in _income_category_keys():
        raise ValidationError(
            "income categories have no envelope and belong to no group — only a spending "
            "envelope can be moved into a group"
        )

    envelope = _resolve_envelope(ref)
    if envelopes_service.is_commitments_synced(envelope):
        raise ValidationError("Commitments envelopes cannot be moved to another group")
    if envelopes_service.is_card_payment_synced(envelope):
        raise ValidationError("Credit Card Payment envelopes cannot be moved to another group")
    cats = list(envelope.get("category_ids") or [])
    if len(cats) > 1:
        raise ValidationError(
            f"'{ref}' is part of the merged envelope '{envelope.get('name') or ref}' — "
            "split it out first"
        )

    # `index` picks the insert position among the destination group's OTHER envelope rows;
    # siblings renumber so nothing collides and the move never silently lands at the bottom
    # regardless of the requested index.
    envelope_store = envelopes_service.envelope_store
    siblings = sorted(
        (
            e
            for e in envelopes_service.all_envelopes()
            if not e.get("archived") and e.get("group_id") == dest_id and e["id"] != envelope["id"]
        ),
        key=lambda e: e.get("order") or 0,
    )
    pos = len(siblings) if index is None else max(0, min(index, len(siblings)))
    siblings.insert(pos, envelope)

    with locked_unit_of_work(envelopes_table) as conn:
        for order, e in enumerate(siblings):
            patch: dict = {}
            if e["id"] == envelope["id"]:
                patch = {"group_id": dest_id, "order": order}
            elif (e.get("order") or 0) != order:
                patch = {"order": order}
            if patch:
                envelope_store.update_envelope(e["id"], patch, conn=conn)
    _rec("update", _envelope_id_or_blank(ref), "envelope", "move_category")
    return {"ok": True, "category": ref, "group_id": dest_id}


def _op_reorder_categories(change: dict) -> dict:
    """Reorder the budget ROWS (envelopes) placed in ONE group — persists the new relative
    ``envelope.order``. ``group_id`` + ``categories`` = the full ordered list of that group's
    CURRENTLY RENDERED row ids (envelope ids), de-duped, preserving the requested order.
    Validates the request is exactly a reordering of the group's current members (no
    adds/drops here — ``move_category`` handles membership). Idempotent + checkpoint-covered."""
    gid = (change.get("group_id") or "").strip()
    group = groups_store.find_by_id(gid)
    if group is None:
        raise NotFoundError(f"group {gid} not found")
    requested = [str(c).strip() for c in (change.get("categories") or []) if str(c).strip()]

    envelope_store = envelopes_service.envelope_store
    group_envelopes = sorted(
        (
            e
            for e in envelopes_service.all_envelopes()
            if not e.get("archived") and e.get("group_id") == gid
        ),
        key=lambda e: e.get("order") or 0,
    )
    by_key = {e["id"]: e for e in group_envelopes}

    resolved: list[str] = []
    seen: set[str] = set()
    for key in requested:
        env_id = _resolve_envelope(key)["id"]
        if env_id not in seen:
            seen.add(env_id)
            resolved.append(env_id)

    if sorted(resolved) != sorted(by_key):
        raise ValidationError(
            "categories must be exactly the group's current members reordered "
            "(use move_category to add/remove a category)"
        )

    # Renumber this group's envelopes to the requested relative order, reusing the group's
    # OWN existing order values.
    order_values = sorted(e.get("order") or 0 for e in group_envelopes)
    for slot, key in zip(order_values, resolved):
        envelope = by_key[key]
        if (envelope.get("order") or 0) != slot:
            envelope_store.update_envelope(envelope["id"], {"order": slot})

    logger.info("apply_budget_change reorder_categories %s = %s", gid, resolved)
    for ref in resolved:
        _rec("update", _envelope_id_or_blank(ref), "envelope", "reorder_categories")
    return {"ok": True, "group_id": gid, "categories": resolved}


# ── Category lifecycle (delegated to services.categories — atomic cross-store re-key) ──


def _op_category_create(change: dict) -> dict:
    """Create a custom category (an opaque id is minted — no caller-supplied key). A category is
    a pure label: it joins no group and mints no envelope (RFC full-split §Backend 2) — to make
    it budgetable, create/link an envelope (``envelope_add_category``). A ``group_id`` is
    therefore refused rather than silently ignored."""
    from backend.services import categories as categories_service

    if change.get("group_id"):
        raise ValidationError(
            "a category belongs to no group — groups hold envelopes; link the category to an "
            "envelope instead"
        )
    result = categories_service.create_category(
        label=change.get("label"),
        kind=change.get("kind") or "expense",
        color=change.get("color") or "",
    )
    _rec("create", result.get("id"), "category", "category_create")
    return result


def _op_category_rename(change: dict) -> dict:
    """Relabel a category, or merge it into another (atomic, value-preserving) — see
    ``categories.rename_category``."""
    from backend.services import categories as categories_service

    result = categories_service.rename_category(
        category_id=change.get("key"),
        new_label=change.get("new_label"),
        merge_into=change.get("merge_into"),
    )
    _rec("update", result.get("category_id"), "category", "category_rename")
    return result


def _op_category_delete(change: dict) -> dict:
    """Delete a custom category (in-use safe: refuse, or reassign txns to None/unsorted)."""
    from backend.services import categories as categories_service

    result = categories_service.delete_category(
        category_id=change.get("key"),
        reassign=bool(change.get("reassign", False)),
        reassign_to=change.get("reassign_to"),
    )
    _rec("delete", result.get("category_id"), "category", "category_delete")
    return result
