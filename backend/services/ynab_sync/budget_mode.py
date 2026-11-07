"""What an app's budget says about whether its Assigned can sync at all. YNAB's read says nothing,
so none of this applies to it.

Tracking budgets hold no envelopes, so while a budget is in tracking mode everything but Assigned
syncs. When it comes back there is no figure both sides agreed on: every category compares afresh,
so a difference goes to review and no figure moves."""

from __future__ import annotations

from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services.budget_app import current
from backend.services.ynab_sync import recorder
from backend.services.ynab_sync.baselines import Baselines

TRACKING = "tracking"
MODE_ITEM = "Budget mode"


def tracking_line(name: str) -> str:
    return f"This budget is in tracking mode in {name}, so its Assigned amounts aren't synced."


def settle(data: dict, meta: dict, base: Baselines) -> bool:
    """Whether Assigned syncs on this run, from what the read says. A change of mode is told once."""
    mode = (data.get("plan") or {}).get("budget_mode")
    if mode is None:
        return True
    before, name = meta.get("budget_mode"), current().name
    meta["budget_mode"] = mode
    if mode == TRACKING:
        if before != TRACKING:
            recorder.note(MODE_ITEM, tracking_line(name))
        return False
    if before == TRACKING:
        for pair in base.cats.values():
            pair["resume"] = True
            base.set_cat(pair)
        recorder.note(
            MODE_ITEM,
            f"This budget is back in envelope mode in {name}. Assigned amounts sync again; any "
            "that differ wait in the review list.",
        )
    return True


def income_in_envelopes(meta: dict, pairs: dict[str, dict]) -> list[dict]:
    """Income categories of the app whose category here has since been put in an envelope: what
    they receive stays there, away from Ready to Assign."""
    if not current().caps.income_categories:
        return []
    income = {c["id"] for c in meta["categories"] if c.get("is_income") and not c.get("deleted")}
    envs = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
    held = {cid: e for e in envs for cid in e.get("category_ids") or []}
    labels = {c["id"]: c["label"] for c in categories_service.all_category_rows()}
    return [
        {
            "category": p["category"],
            "item": labels[p["category"]],
            "reason": (
                f"{labels[p['category']]} is an income category in {current().name}, and sits in "
                f"the envelope {held[p['category']]['name']} here. What it receives stays in that "
                "envelope instead of reaching Ready to Assign. Take it out of the envelope to "
                "change that."
            ),
        }
        for y, p in pairs.items()
        if y in income and not p.get("gone") and p["category"] in held and p["category"] in labels
    ]


def warn(meta: dict, base: Baselines) -> None:
    """In the run's report once per category; the page keeps showing it for as long as it holds."""
    for found in income_in_envelopes(meta, base.cats):
        key = f"income-in-envelope:{found['category']}"
        if not base.told(key):
            base.tell(key)
            recorder.note(found["item"], found["reason"])
