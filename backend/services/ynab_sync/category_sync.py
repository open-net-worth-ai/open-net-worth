"""YNAB categories and category groups, both ways, each run (ADR
``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``, amendment 2026-11-03).

A YNAB category pairs with one of OUR CATEGORIES by id (``ynab_pairs``); its budget (Assigned)
syncs with the envelope that holds ONLY that category. A YNAB category group pairs with one of
our envelope groups. Per run, in order:

  1. paired groups: a rename on one side reaches the other;
  2. paired categories: a rename, a move to another group (the envelope's, when it holds only
     that category) or a hide reaches the other side; a category deleted here is hidden in YNAB
     (its API cannot delete), one deleted or hidden in YNAB is hidden here, never deleted;
  3. a category new in YNAB: matched with one of ours (``category_match``) or created here;
  4. a category new here: created in YNAB, in the group paired with its envelope's group, or
     in the YNAB group "Not budgeted" when it has no envelope of its own;
  5. the budget link: whether each category's Assigned still syncs, and an envelope for a
     category YNAB assigned money to.

The direction is honoured as for transactions: one way skips the other way's writes, and the same
field changed on BOTH sides goes to the review list. The state is re-derived from the two sides
and the last-agreed baselines each run, so what a direction held back, or the request allowance
deferred, is simply found again."""

from __future__ import annotations

from collections import Counter

from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.category_base import Resumed
from backend.services.ynab_sync.category_new import News
from backend.services.ynab_sync.client import YnabClient


def reconcile(
    client: YnabClient,
    base: Baselines,
    st: dict,
    meta: dict,
    tally: Counter,
    assigned_seen: dict,
    plan_cats: list[dict],
    full: bool = False,
) -> Resumed:
    """``plan_cats``: the YNAB categories worth mirroring (``mapping.plan_categories``); ``full``:
    ``assigned_seen`` is the whole plan's Assigned. Returns the categories whose Assigned has no
    agreed baseline (their envelope changed) with YNAB's figures, for the Assigned step to
    compare with this book's and send every difference to review."""
    run = News(client, base, st, meta, tally, assigned_seen, full)
    try:
        run.close_stale()
        run.sync_groups()
        run.sync_pairs()
        run.pull_new(plan_cats)
        run.push_new()
        return run.budgets()
    finally:
        run.finish()
