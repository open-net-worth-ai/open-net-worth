"""One made-up book and the budgets it syncs with, for a live test."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from backend.core import audit_context
from backend.core.user_time import user_today
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import import_runs, planning
from backend.services import ledger as ledger_service
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_migration import job
from backend.services.ynab_sync import runner
from backend.services.ynab_sync import state as sync_state
from tests.actual_live.harness import PASSWORD, Budget, Server, seed_budget

TODAY = user_today().isoformat()
THIS_MONTH = TODAY[:7]
LAST_DAY = (user_today().replace(day=1) - timedelta(days=11)).isoformat()
LAST_MONTH = LAST_DAY[:7]
NEXT_YEAR = f"{int(TODAY[:4]) + 1}-03-01"
# Three months before a seeded budget's first row. A fresh download of the official client reads this
# month's Assigned as 0 whoever wrote it, though its own table holds the figure (test_money pins it).
_FIRST_SHEET = date.fromisoformat(LAST_DAY).year * 12 + date.fromisoformat(LAST_DAY).month - 1 - 3
FIRST_SHEET_MONTH = f"{_FIRST_SHEET // 12:04d}-{_FIRST_SHEET % 12 + 1:02d}"
IMPORT_SECONDS = 180


def milli_to_cents(amount: float) -> int:
    return round(amount * 100)


def as_person(call, *args, **kw):
    token = audit_context.set_audit_scope("user", "ui")
    try:
        return call(*args, **kw)
    finally:
        audit_context.clear_audit_scope(token)


def live_rows() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def rows_here(**fields) -> list[dict]:
    return [t for t in live_rows() if all(t.get(k) == v for k, v in fields.items())]


def row_here(**fields) -> dict:
    (found,) = rows_here(**fields)
    return found


def edit_here(txn_id: str, **changes) -> dict:
    return as_person(ledger_service.update_transaction, txn_id, changes)


def account_here(name: str) -> dict:
    return next(a for a in ledger_service.accounts.query_accounts() if a["name"] == name)


def category_here(label: str) -> dict:
    return next(c for c in categories_service.all_category_rows() if c["label"] == label)


def envelope_here(name: str) -> dict:
    return next(e for e in envelopes_service.all_envelopes() if e["name"] == name)


def assigned_here(month: str, name: str) -> float:
    return float(planning.assignments_store.month_map(month).get(envelope_here(name)["id"]) or 0)


def ready_to_assign(month: str = THIS_MONTH) -> float:
    return float(planning.budget_month(month)["ready_to_assign"])


def new_row(account: str = "ACME Checking", **over) -> dict:
    return as_person(
        ledger_service.create_transaction,
        {
            "kind": "expense",
            "date": TODAY,
            "description": "ACME Coffee",
            "memo": "",
            "from_account_id": account_here(account)["id"],
            "from_amount": 12.34,
            "from_currency": "USD",
            "cleared": True,
            "needs_review": False,
            **over,
        },
    )


def balances_here() -> dict[str, int]:
    rows = live_rows()
    return {
        a["name"]: milli_to_cents(float(ledger_service.account_with_balance(a, rows)["balance"]))
        for a in ledger_service.accounts.query_accounts()
    }


def balances_there(view: dict) -> dict[str, int]:
    return {a["name"]: a["balance"] for a in view["accounts"]}


def there(view: dict, notes: str) -> list[dict]:
    return [r for r in view["rows"] if r["notes"] == notes]


def run() -> dict:
    with using(ACTUAL):
        runner._locked_run("manual")
        return sync_state.load()["last_run"]


def sync() -> dict:
    last = run()
    assert last["ok"], last["error"]
    return last


def quiet() -> None:
    """Nothing is left to do: a run writes nothing, either way."""
    last = sync()
    assert last["summary"] == "Nothing changed", last["summary"]


def open_items() -> list[dict]:
    return actual_app.sync_status()["conflicts"]


def warnings() -> dict[str, str]:
    return {w["kind"]: w["text"] for w in actual_app.sync_status()["warnings"]}


@dataclass
class World:
    server: Server
    budgets: list[Budget] = field(default_factory=list)

    @classmethod
    def prepared(cls, server: Server) -> "World":
        categories_service.ensure_builtins_seeded()
        envelopes_service.ensure_envelopes_migrated()
        ActualBudgetConfigStore().update({"server_url": server.url, "password": PASSWORD})
        return cls(server)

    def seed(self, *, tracking: bool = False, encryption: str | None = None) -> Budget:
        name = f"ACME {uuid.uuid4().hex[:8]}"
        budget = seed_budget(
            self.server, name, THIS_MONTH, TODAY, LAST_DAY, tracking=tracking, encryption=encryption
        )
        self.budgets.append(budget)
        return budget

    def types(self, budget: Budget) -> dict[str, str]:
        ids = budget.ids
        return {ids["checking"]: "cash", ids["card"]: "credit", ids["savings"]: "investment"}

    def finish(self) -> dict:
        deadline = time.monotonic() + IMPORT_SECONDS
        while time.monotonic() < deadline:
            record = import_runs.latest("actual_budget", job.KINDS)
            if record["status"] != "running":
                assert record["status"] == "done", record["error"]
                return record
            time.sleep(0.05)
        pytest.fail("the import did not finish")

    def imported(self, budget: Budget, direction: str | None = "both", **kw) -> dict:
        if direction:
            kw["sync_direction"] = direction
        actual_app.start_migration(
            "import", budget.file, account_types=self.types(budget), currency="USD", **kw
        )
        return self.finish()

    def verify(self, budget: Budget) -> dict:
        actual_app.start_migration("verify", budget.file)
        return self.finish()["verification"]
