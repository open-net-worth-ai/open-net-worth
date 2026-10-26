"""The Singapore chapter's plan and assistant side: envelopes, goals, Outlook scenarios, a
research watchlist, one skill and three paused Automations.

Split from ``singapore.py`` (the money) because none of it moves any: the envelopes budget cash
that is already in the book, the goals only EARMARK it ("never add them to net worth again",
as the household plan puts it), and the Automations are paused rehearsals a visitor runs by
hand. Run after the ledger and the generic Plan seed, because the last step re-balances Ready
to Assign over the finished book.
"""

from __future__ import annotations

import logging
from datetime import date

from backend.scripts.demo import planning
from backend.scripts.demo._timeline import Timeline, add_months, month_key

log = logging.getLogger(__name__)

GROUP = "Singapore"
RESERVES = ("Singapore Reserves & Goals", "#14b8a6")
# Category handles that get an envelope of their own, named after the category.
BUDGETED = ("bills", "dining", "transport", "software")

# Goals earmark cash that already sits in Singapore Runway: unlinked on purpose (a linked
# account's whole live balance would override the earmark), and not an extra asset.
GOALS: list[dict] = [
    {
        "name": "Singapore home deposit",
        "target_amount": 80000.0,
        "current_amount": 22500.0,
        "currency": "SGD",
        "months_ahead": 24,
        "priority": 1,
        "note": "Synthetic goal; earmarked from Singapore Runway, not an extra asset.",
    },
    {
        "name": "Japan sabbatical",
        "target_amount": 14000.0,
        "current_amount": 5600.0,
        "currency": "SGD",
        "months_ahead": 9,
        "priority": 1,
        "note": "Synthetic goal; earmarked from Singapore Runway, not an extra asset.",
    },
]

_STREAM = {"label": "Consulting after full-time work", "amount": 18737.4, "age": 52, "endAge": 58}
_EVENT = {"id": "demo-sabbatical", "label": "Japan sabbatical", "amount": 10930.15, "age": 37}
_LEVERS = {"ret": 6.5, "vol": 15, "swr": 3.8, "lifeExp": 92, "monthlyRetirementSpending": 4840.49}
SCENARIOS: list[dict] = [
    {"name": "Singapore base case", "retireAge": 52, "saving": 1483.38},
    {"name": "Six-month sabbatical", "retireAge": 54, "saving": 1171.09},
    {"name": "Retire two years earlier", "retireAge": 50, "saving": 2107.96},
]

RESEARCH = ("Asia & global research", "Synthetic shortlist: global ETFs and Singapore exposure.")
RESEARCH_ITEMS = [
    ("D05.SI", "DBS Group"),
    ("CSPX.L", "iShares S&P 500 UCITS ETF"),
    ("VWRA.L", "Vanguard FTSE All-World UCITS ETF"),
]
WATCH_ONLY = "Watch only. No purchase recommendation or auto-trading authority."

SKILL = {
    "name": "mail-receipt-rehearsal",
    "description": "Practice bank-email transaction logging from a synthetic file.",
    "body": (
        "This is an OFFLINE EMAIL REHEARSAL. Read the attached bank-notification.txt; never "
        "claim an inbox was contacted. Resolve the account and category and check duplicate "
        "identities. Show a proposal for the SGD 27.90 payment, then request approval through "
        "normal gated tools. Use the stable Message-ID and emitter if booking. Report the "
        "actual write result. An existing SGD 12.80 transaction is different. Do not infer "
        "success from a proposal."
    ),
}

_WEEKLY_SUNDAY = {"kind": "schedule", "type": "weekly", "day": "sun", "time": "18:00"}
AUTOMATIONS: list[dict] = [
    {
        "name": "Founder weekly wealth review",
        "instructions": (
            "Read get_net_worth, get_budget_month for the current month, get_portfolio_xray "
            "and get_goals. Return at most 140 words. Lead with ONE practical check supported "
            "by the current book. Then give four short bullets: total net worth and "
            "owned/unvested distinction; cash on hand and Ready to Assign; the next goal's "
            "funding gap; largest reported holding concentration. Read debt from liability "
            "accounts, not the optional profile list. Use tool-provided amounts and "
            "percentages, state the reporting currency, and cite the relevant account, "
            "envelope or goal. Distinguish manually confirmed historical installments from "
            "ledger-verified payments. If a figure is unavailable, say so. No trades, tax "
            "strategy, money writes, external messages or extended reconciliation."
        ),
    },
    {
        "name": "Asia watchlist thesis check",
        "instructions": (
            "Review Asia & global research watchlist. State which thesis changed and show "
            "current evidence. Distinguish illustrative manual marks from live quotes. If no "
            "new evidence is available, say so. Never invent news or trade."
        ),
    },
    {
        "name": "Offline bank-email rehearsal",
        "instructions": (
            "Run mail-receipt-rehearsal using bank-notification.txt from Documents. This is a "
            "paused recording fixture, not a live mailbox. Preview the SGD 27.90 Kopi Lane "
            "debit with duplicates checked. Require normal write approval. Never say IMAP "
            "succeeded or seed fake automation history."
        ),
    },
]


def _seed_envelopes(C: dict) -> tuple[list[dict], str]:
    """The Singapore group, one envelope per budgeted category, and the reserves pot."""
    from backend.services import categories as categories_service
    from backend.services import envelopes as envelopes_service
    from backend.services import planning as planning_service

    group = planning_service.apply_budget_change({"type": "group_create", "name": GROUP})["group"]
    labels = {c["id"]: c["label"] for c in categories_service.all_category_rows()}
    made = []
    for handle in BUDGETED:
        envelope = planning_service.apply_budget_change(
            {"type": "envelope_create", "name": labels[C[handle]], "group_id": group["id"]}
        )["envelope"]
        planning_service.apply_budget_change(
            {
                "type": "envelope_add_category",
                "envelope_id": envelope["id"],
                "category_id": C[handle],
            }
        )
        made.append(envelope["id"])
    name, color = RESERVES
    reserves = planning_service.apply_budget_change(
        {"type": "envelope_create", "name": name, "group_id": group["id"], "color": color}
    )["envelope"]
    linked = [e for e in envelopes_service.all_envelopes() if e["id"] in made]
    return linked, reserves["id"]


def _seed_budget(tl: Timeline, C: dict, first_month: date) -> dict:
    """Target each envelope off its own median month, budget every month the chapter spans,
    then park what is left in the reserves pot so Ready to Assign stays in the book's band."""
    from backend.services import planning as planning_service

    envelopes, reserves_id = _seed_envelopes(C)
    _spend, targets = planning._envelope_months(tl, envelopes)
    for env_id, (amount, ttype) in targets.items():
        planning_service.apply_budget_change(
            {"type": "set_target", "envelope_id": env_id, "target_type": ttype, "amount": amount}
        )
    months = [month_key(m) for m in tl.months]
    chapter = [month_key(m) for m in tl.months if m >= first_month]
    assigned = 0
    for month in chapter:
        for env_id, (amount, _ttype) in targets.items():
            planning_service.apply_budget_change(
                {"type": "assign", "month": month, "envelope_id": env_id, "amount": amount}
            )
            assigned += 1
    parked = planning._give_every_dollar_a_job(months, reserves_id)
    return {"envelopes": len(envelopes) + 1, "assignments": assigned, "parked": parked}


def _seed_goals(tl: Timeline) -> int:
    from backend.db.targets_store import TargetsStore

    store = TargetsStore()
    for goal in GOALS:
        spec = {k: v for k, v in goal.items() if k != "months_ahead"}
        spec["target_date"] = add_months(tl.today, goal["months_ahead"]).isoformat()
        store.create(spec)
    return len(GOALS)


def _seed_scenarios() -> int:
    from backend.services import plan_state

    for scenario in SCENARIOS:
        plan_state.add_scenario(
            {**scenario, **_LEVERS, "streams": [dict(_STREAM)], "events": [dict(_EVENT)]}
        )
    return len(SCENARIOS)


def _seed_watchlist() -> int:
    from backend.core.ids import new_id
    from backend.db.watchlist_store import WatchlistStore

    store = WatchlistStore()
    watchlist = store.create_watchlist(*RESEARCH)
    for symbol, label in RESEARCH_ITEMS:
        store.insert(
            {
                "id": new_id("wli"),
                "watchlist_ids": [watchlist["id"]],
                "type": "ticker",
                "value": symbol,
                "label": label,
                "asset_class": "equity",
                "note": WATCH_ONLY,
            }
        )
    return len(RESEARCH_ITEMS)


def _seed_skill() -> int:
    from backend.db.skill_store import SkillStore
    from backend.services import skills as skills_service

    problems = skills_service.validate(SKILL, require_standard_fields=True)
    if problems:
        raise ValueError("; ".join(problems))
    SkillStore().create({**SKILL, "author": "user"})
    return 1


def _seed_automations() -> int:
    from backend.services.automations.service import apply_automation_change

    for spec in AUTOMATIONS:
        apply_automation_change(
            {
                "op": "create",
                "automation": {
                    **spec,
                    "description": "Synthetic demo routine; run manually for rehearsal.",
                    "status": "paused",
                    "trigger": dict(_WEEKLY_SUNDAY),
                    "execution": {"kind": "lucius"},
                    "permission_mode": "manual",
                    "effort": "low",
                    "provenance": {"source": "demo-seed", "synthetic": True},
                },
            }
        )
    return len(AUTOMATIONS)


def seed(tl: Timeline, C: dict, first_month: date) -> dict:
    """Everything but the money. ``first_month`` is the chapter's opening month. Returns counts
    for the build report."""
    return {
        "plan": _seed_budget(tl, C, first_month),
        "goals": _seed_goals(tl),
        "scenarios": _seed_scenarios(),
        "watchlist": _seed_watchlist(),
        "skills": _seed_skill(),
        "automations": _seed_automations(),
    }
