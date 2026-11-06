"""The real server's own request limit. This test talks to the server directly, so the limit of 500
requests a minute from one address is in play."""

from __future__ import annotations

import time

import pytest

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import planning
from backend.services.envelopes import all_envelopes
from tests.actual_live.world import (
    FIRST_SHEET_MONTH,
    PASSWORD,
    THIS_MONTH,
    open_items,
    quiet,
    run,
    sync,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

MONTHS = 40


def _months_back(count: int) -> list[str]:
    year, month = int(THIS_MONTH[:4]), int(THIS_MONTH[5:])
    out = []
    for _ in range(count):
        month -= 1
        if month < 1:
            year, month = year - 1, 12
        out.append(f"{year:04d}-{month:02d}")
    return out


@pytest.mark.timeout(400)
def test_a_large_run_stays_under_the_limit_and_a_rushed_one_stops_cleanly(world):
    """Each Assigned sent is two batches, its figure and the trail in its note. A figure per month
    over years meets the cap, and a second run in the same minute meets the server's limit."""
    budget = world.seed()
    months = _months_back(MONTHS)
    budget.call(
        "addTransactions",
        budget.ids["checking"],
        [
            {
                "date": f"{months[-1]}-15",
                "amount": -100,
                "payee_name": "ACME Old",
                "notes": "long ago",
            }
        ],
    )
    world.imported(budget)
    quiet()
    ActualBudgetConfigStore().update({"server_url": world.server.direct_url, "password": PASSWORD})
    envelopes = [e for e in all_envelopes() if not e.get("archived")]
    cells = 0
    for index, month in enumerate(months):
        for envelope in envelopes:
            planning.apply_budget_change(
                {
                    "type": "assign",
                    "month": month,
                    "envelope_id": envelope["id"],
                    "amount": 10.0 + index,
                }
            )
            cells += 1
    assert cells >= 200

    first = run()
    assert first["ok"], first["error"]
    print(f"\n[limit] first run: {first['summary']}")
    assert "waiting for the next run" in first["summary"]

    rushed = run()
    print(f"[limit] same minute: ok={rushed['ok']} {rushed['summary']!r} {rushed['error']!r}")
    if not rushed["ok"]:
        assert "429" in rushed["error"]
        assert "127.0.0.1" not in rushed["error"]

    time.sleep(61)
    while "waiting for the next run" in sync()["summary"]:
        time.sleep(61)
    quiet()
    ActualBudgetConfigStore().update({"server_url": world.server.url, "password": PASSWORD})
    food = budget.ids["food"]
    view = budget.view(*months)
    shown = {m: view["budget"][m]["categories"][food]["budgeted"] for m in months}
    expected = {m: (10 + index) * 100 for index, m in enumerate(months)}
    del shown[FIRST_SHEET_MONTH], expected[FIRST_SHEET_MONTH]  # the official client's blind spot
    assert shown == expected
    assert open_items() == []
