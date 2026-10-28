"""The demo's Automations — the assistant actually doing standing work.

Modelled on the shape a real instance takes: a mix of EVENT rules that fire the moment a
transaction lands, and SCHEDULED routines that run on their own clock. A demo with an empty
Automations tab shows a feature that exists; this shows one that is being used.

Both trigger kinds are represented deliberately, because they answer different questions:

* an ``event`` rule ("anything matching /coffee/ is Food & Dining") is how the ledger keeps
  itself tidy without anybody opening the app;
* a ``schedule`` routine ("every morning, tell me what needs attention") is how the app
  reaches OUT instead of waiting to be visited.

Statuses are mixed on purpose. A board where everything is green says nothing about what a
paused automation looks like, and a real instance always has a few switched off.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _rule(name: str, regex: str, category_key: str, *, status: str = "active") -> dict:
    """A categorisation rule: an event trigger + the instruction that acts on it.

    The category is named in the instructions rather than baked into the trigger because the
    trigger only MATCHES — the write goes through ``update_transactions`` like any other
    change, so it lands in the audit trail attributed to this automation.
    """
    return {
        "name": name,
        "description": "Categorises matching transactions as they arrive.",
        "status": status,
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_regex": regex, "operation": "create"},
            "limit": "each",
            "sequence": {"group": "transaction-categorization", "priority": 100, "stop": True},
        },
        "execution": {"kind": "lucius"},
        "permission_mode": "auto",
        "effort": "low",
        "instructions": (
            "This Automation handles the transaction event supplied with the run. Re-check the "
            f"match condition (/{regex}/) against that transaction and take no action if it "
            f"does not match. Otherwise call update_transactions to set its category to "
            f"'{category_key}'. Do not touch any other row."
        ),
    }


def _routine(
    name: str,
    description: str,
    instructions: str,
    trigger: dict,
    *,
    status: str = "active",
    permission_mode: str = "auto",
    effort: str = "medium",
) -> dict:
    return {
        "name": name,
        "description": description,
        "status": status,
        "trigger": trigger,
        "execution": {"kind": "lucius"},
        "permission_mode": permission_mode,
        "effort": effort,
        "instructions": instructions,
    }


def _daily(time: str) -> dict:
    return {"kind": "schedule", "type": "daily", "time": time}


def _weekly(day: str, time: str) -> dict:
    return {"kind": "schedule", "type": "weekly", "day": day, "time": time}


def AUTOMATIONS(categories: dict) -> list[dict]:
    """Every automation the demo book carries."""
    return [
        # ── Event rules: the ledger keeping itself tidy ───────────────────────
        _rule("Coffee shops → Food & Dining", r"coffee|espresso|roasters", categories["food"]),
        _rule("Rideshare & transit → Transport", r"hopp rides|cityline", categories["transport"]),
        _rule(
            "Streaming & apps → Subscriptions",
            r"nimbus|auralis|cloudshelf|forge ide",
            categories["subscriptions"],
        ),
        _rule("Vet & pet supplies → Pet Care", r"paws & claws|kibble", categories["pets"]),
        _rule(
            "Utilities → Home & Utilities",
            r"lumen energy|cascade water|fiberline",
            categories["home"],
            status="paused",
        ),
        # ── Scheduled routines: the app reaching out ──────────────────────────
        _routine(
            "Morning briefing",
            "What changed overnight and what needs attention today.",
            "Summarise what moved since yesterday: net worth, any holding that moved more than "
            "3%, transactions that landed overnight, and anything due in the next 7 days. Lead "
            "with what needs a decision. Keep it under 200 words.",
            _daily("07:30"),
        ),
        _routine(
            "Weekly spending review",
            "Where the money went, against the budget.",
            "Compare this week's spending to the same week last month, by envelope. Call out "
            "any envelope that is on track to overspend before month end, and say by how much. "
            "Do not repeat categories that are behaving.",
            _weekly("sun", "18:00"),
        ),
        _routine(
            "Vest reminder",
            "Warns before each RSU tranche vests.",
            "Check upcoming vests. If a tranche vests within 14 days, report the date, the "
            "units, and the indicative value at today's price. Say nothing if none are due.",
            # It looks 14 days ahead, so a weekly run catches every tranche; daily repeated itself.
            _weekly("mon", "09:00"),
        ),
        _routine(
            "Card payment due",
            "Reminds before the Sapphire Card statement is due.",
            "Check the credit card's statement balance and due date. If payment is due within "
            "7 days and no payment transaction exists for this cycle, say what is owed and by "
            "when.",
            # A 7-day window checked weekly never misses a due date.
            _weekly("mon", "08:00"),
        ),
        _routine(
            "Unusual spending watch",
            "Flags transactions that do not look like the usual pattern.",
            "Look at transactions from the last 24 hours. Flag anything more than 3x the "
            "typical amount for its payee, any payee never seen before with an amount over "
            "$200, and any duplicate-looking pair. Report nothing if all of it looks ordinary.",
            # It reads the last 24 hours, so hourly re-read the same day 24 times — each run a
            # Lucius turn charged to the demo's token ceiling.
            _daily("21:00"),
            permission_mode="manual",
        ),
        _routine(
            "Portfolio drift check",
            "Watches the allocation against its targets.",
            "Compare the current allocation to the target allocation on the profile. If any "
            "band is more than 5 percentage points from its target, say which, by how much, "
            "and roughly what trade would close the gap. Do not place any trade.",
            _weekly("mon", "08:30"),
            permission_mode="manual",
        ),
        _routine(
            "Commitment funding check",
            "Makes sure the next instalment is actually funded.",
            "For every active commitment, check whether the funding sources dated before the "
            "next instalment cover it. Report any shortfall with the date it bites and the gap "
            "in base currency.",
            _weekly("wed", "09:00"),
        ),
        _routine(
            "Subscription creep",
            "Catches price rises on recurring charges.",
            "Compare each recurring subscription charge to what it cost three months ago. "
            "Report any that went up, with the old and new amounts. Say nothing if none did.",
            {"kind": "schedule", "type": "monthly", "day": 1, "time": "10:00"},
            status="paused",
        ),
        _routine(
            "Uncategorised sweep",
            "Keeps the Uncategorized pile from growing.",
            "Find transactions with no category from the last 7 days. For each, suggest a "
            "category based on its payee and similar past rows, and apply it where you are "
            "confident. List anything you were not confident about rather than guessing.",
            _daily("22:00"),
            status="paused",
            effort="low",
        ),
    ]


def _pause_system_vest_reminder() -> None:
    """Recording the grant provisions the app's own "Vest calendar reminder" (system_key
    ``vest_reminder``), a second vest check beside the seeded "Vest reminder". Paused, as a
    user tidying the tab would; a later boot re-seed patches only the fields it sets, never
    ``status``."""
    from backend.db.automation_store import AutomationStore
    from backend.services.automations.service import apply_automation_change

    for row in AutomationStore().all():
        if (row.get("provenance") or {}).get("system_key") == "vest_reminder" and row.get(
            "status"
        ) == "active":
            apply_automation_change(
                {"op": "update", "id": row["id"], "changes": {"status": "paused"}}
            )


def seed_automations(categories: dict) -> dict:
    """Create every automation. Returns counts by status."""
    from backend.services.automations.service import apply_automation_change

    _pause_system_vest_reminder()

    created = {"active": 0, "paused": 0, "failed": 0}
    for spec in AUTOMATIONS(categories):
        try:
            apply_automation_change({"op": "create", "automation": spec})
            created[spec["status"]] += 1
        except Exception as exc:  # noqa: BLE001 — one bad spec must not fail the whole build
            log.warning("demo automation %r skipped: %s", spec["name"], exc)
            created["failed"] += 1
    return created
