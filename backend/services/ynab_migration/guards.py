"""What the migration may touch in a book that already has something in it.

Two decisions, both made from what the book HOLDS rather than from "has no accounts":

* the base currency is only adopted from the plan when the book has no money data at all;
* only the shipped starter budget items — untouched — are ever removed to make room for the
  plan's own categories. Anything the user made, renamed, funded or linked is kept.
"""

from __future__ import annotations

from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.commitments_store import CommitmentsStore
from backend.db.grant_store import GrantStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.models.category_defs import STARTER_CATEGORIES, STARTER_GROUPS
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning

_STARTER_GROUP_NAMES = {name for name, _ in STARTER_GROUPS}


def money_data() -> list[str]:
    """What in the book carries money. Empty means the base currency is safe to change."""
    found = []
    if ledger_service.accounts.query_accounts(include_archived=True):
        found.append("accounts")
    if any(not t.get("deleted") for t in ledger_service.transactions.all()):
        found.append("transactions")
    for label, store in (
        ("owned assets", OwnedAssetsStore()),
        ("equity grants", GrantStore()),
        ("income events", IncomeEventStore()),
        ("income streams", RecurringStreamStore()),
    ):
        if store.count():
            found.append(label)
    return found


def _referenced_categories() -> set[str]:
    used: set[str] = set()
    for t in ledger_service.transactions.all():
        if t.get("category"):
            used.add(t["category"])
        used |= {s.get("category") for s in t.get("splits") or []}
    return used


def starter_prune_plan(wanted_labels: set[str]) -> dict:
    """The untouched starter categories/envelopes/groups the plan does not reuse. ``wanted_labels``
    are the plan's category labels, lowercased. A starter item is listed only if it still looks
    exactly as seeded (label, 1:1 envelope in its starter group) and nothing was ever funded,
    targeted, linked to a commitment or spent against it."""
    seeded = {c["id"]: c for c in STARTER_CATEGORIES if c["id"] != "split"}
    rows = {c["id"]: c for c in categories_service.all_category_rows()}
    envs = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
    env_of = {cid: e for e in envs for cid in e.get("category_ids") or []}
    group_name = {g["id"]: g["name"] for g in planning.list_groups()}
    assigned = BudgetAssignmentsStore().all_map()
    targets = planning.read_category_targets()
    commitment_envs = {c.get("envelope_id") for c in CommitmentsStore().all()}
    spent = _referenced_categories()

    cats, env_ids = [], []
    for cid, seed in seeded.items():
        row = rows.get(cid)
        if not row or row["label"] != seed["label"] or row["label"].lower() in wanted_labels:
            continue
        if cid in spent:
            continue
        env = env_of.get(cid)
        if env is not None:
            keys = {env["id"], cid}
            funded = any(
                abs(v) > 0 for month in assigned.values() for k, v in month.items() if k in keys
            )
            as_seeded = (
                env["name"] == seed["label"]
                and env["category_ids"] == [cid]
                and group_name.get(env["group_id"]) in _STARTER_GROUP_NAMES
            )
            if funded or not as_seeded or keys & set(targets) or env["id"] in commitment_envs:
                continue
            env_ids.append(env["id"])
        cats.append({"id": cid, "label": seed["label"]})
    return {"categories": cats, "envelopes": env_ids}


def starter_groups_left_empty(prune: dict, kept_group_names: set[str]) -> list[dict]:
    """Starter groups that would hold nothing once ``prune`` is applied and the plan's own
    groups (``kept_group_names``, lowercased) are in place."""
    going = set(prune["envelopes"])
    still_used = {
        e["group_id"]
        for e in envelopes_service.all_envelopes()
        if not e.get("archived") and e["id"] not in going
    }
    return [
        g
        for g in planning.list_groups()
        if g["name"] in _STARTER_GROUP_NAMES
        and g["id"] not in still_used
        and g["name"].lower() not in kept_group_names
        and not g.get("is_others")
        and not g.get("synced")
    ]
