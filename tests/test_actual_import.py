"""The Actual import: the YNAB engine, running on a budget read through ``ACTUAL``'s reader.

The one thing faked is the door to the server (``client.open_copy`` / ``list_budgets``): rows are
built here and go through the real ``shape``, reader, mapping, job and ledger. Every name and
number is made up."""

from __future__ import annotations

import dataclasses
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.actual_budget_api import router
from backend.core.errors import ConflictError, ValidationError
from backend.core.user_time import user_today
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import ActualBaselineStore
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.ynab_config_store import YnabConfigStore
from backend.db.ynab_sync_store import YnabBaselineStore, YnabSyncStateStore
from backend.services import accounts as accounts_service
from backend.services import budget_app, import_runs, planning
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import client, shape
from backend.services.actual_sync import reader as actual_reader
from backend.services.actual_sync import rows as actual_rows
from backend.services.budget_app import YNAB, using
from backend.services.reports_rollup import trailing_saving
from backend.services.ynab_migration import job, links, plan_read, source
from backend.services.ynab_sync import runner
from tests.test_actual_shape import (
    acct,
    budget,
    cat,
    child,
    group,
    payee,
    txn,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

BUDGET = "budget-1"
TYPES = {"a1": "cash", "a2": "investment"}


def _month(offset: int = 0) -> int:
    today = user_today()
    index = today.year * 12 + today.month - 1 + offset
    return index // 12 * 100 + index % 12 + 1


def _key(offset: int = 0) -> str:
    month = _month(offset)
    return f"{month // 100:04d}-{month % 100:02d}"


def day(offset: int = 0, number: int = 3) -> int:
    return _month(offset) * 100 + number


def make_rows(**extra) -> shape.Rows:
    kw = dict(
        accounts=[acct("a1", "ACME Checking"), acct("a2", "ACME Savings", off=1)],
        payees=[
            payee("p1", "Corner Market"),
            payee("psb", "Starting Balance"),
            payee("pt1", None, transfer="a1"),
            payee("pt2", None, transfer="a2"),
        ],
        category_groups=[group("g1", "Usual"), group("gi", "Income", income=1)],
        categories=[
            cat("c_food", "Food", "g1"),
            cat("c_fun", "Fun", "g1"),
            cat("c_pay", "Salary", "gi", income=1),
            cat("c_sb", "Starting Balances", "gi", income=1),
        ],
    )
    kw.update(extra)
    rows = shape.Rows(**kw)
    rows.category_mapping = [{"id": c["id"], "transferId": c["id"]} for c in rows.categories]
    rows.payee_mapping = [{"id": p["id"], "targetId": p["id"]} for p in rows.payees]
    return rows


class Server:
    """What ``client`` would have read from a real server."""

    def __init__(self, monkeypatch):
        self.rows = make_rows()
        self.currency: str | None = "USD"
        self.notices: list[str] = []
        self.opened = 0
        monkeypatch.setattr(client, "open_copy", self._open)
        monkeypatch.setattr(
            client,
            "list_budgets",
            lambda store=None: [
                {"id": BUDGET, "name": "ACME Budget", "currency": None, "encrypted": False}
            ],
        )

    @contextmanager
    def _open(self, store, budget_id):
        self.opened += 1
        yield SimpleNamespace(
            plan=lambda: shape.plan_from_rows(self.rows),
            currency=self.currency,
            mode=self.rows.mode,
            notices=self.notices,
        )


@pytest.fixture
def server(db_isolated, monkeypatch):
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    ActualBudgetConfigStore().update({"server_url": "http://127.0.0.1:5006", "password": "pw-1"})
    return Server(monkeypatch)


def run(kind="import", **kw) -> dict:
    if kind == "import":
        kw.setdefault("account_types", TYPES)
    actual_app.start_migration(kind, BUDGET, **kw)
    deadline = time.time() + 60
    while time.time() < deadline:
        record = import_runs.latest("actual_budget", job.KINDS)
        if record["status"] != "running":
            return record
        time.sleep(0.02)
    raise AssertionError("the run did not finish")


def _set_base(code: str) -> None:
    from backend.services import profile as profile_service

    profile = profile_service.read_profile()
    profile["base_currency"] = code
    profile_service.write_profile(profile)


def _mine() -> None:
    _set_base("USD")
    accounts_service.create_account(
        {"name": "Mine", "type": "cash", "currency": "USD", "initial_balance": 1.0}
    )


def category_row(preview: dict, category_id: str) -> dict:
    rows = [c for g in preview["category_groups"] for c in g["categories"]]
    return next(c for c in rows if c["id"] == category_id)


def account_named(name: str) -> dict:
    accounts = ledger_service.accounts.query_accounts(include_archived=True)
    return next(a for a in accounts if a["name"] == name)


def live_txns() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def balance(name: str) -> float:
    row = ledger_service.account_with_balance(account_named(name), live_txns())
    return float(row["balance"])


def ready_to_assign(offset: int = 0) -> float:
    return float(planning.budget_month(_key(offset))["ready_to_assign"])


def checks(record: dict) -> list[dict]:
    return record["verification"]["matches"] + record["verification"]["mismatches"]


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


# ── what ACTUAL is ───────────────────────────────────────────────────────────


def test_the_description_of_actual():
    app = actual_app.ACTUAL
    assert (app.key, app.name, app.source, app.surface) == (
        "actual_budget",
        "Actual",
        "actual_budget",
        "actual_sync",
    )
    assert app.caps == budget_app.Capabilities(
        future_dates=True,
        edit_splits=True,
        hide_categories=True,
        income_categories=True,
        card_payment_categories=False,
        compares_ready_to_assign=False,
        zero_based_on_fresh_book=True,
        request_cap=1_000_000,
        category_request_cap=1_000_000,
        flag_trail=False,
    )
    assert app.stores.state().doc("state") == {"id": "state"}


def test_actual_is_registered_whatever_was_imported_first(tmp_path):
    code = (
        "from backend.services import budget_app;"
        "print(sorted(a.key for a in budget_app.all_apps()))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "ONW_ROOT": str(tmp_path),
            "ONW_QA_MODE": "1",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "['actual_budget', 'ynab']"


def test_the_reader_returns_the_shape_source_returns(server):
    server.rows.transactions = [txn("t1", "a1", -500, day(), category="c_food", description="p1")]
    data = actual_reader.ActualReader().fetch_plan(None, BUDGET, with_budget=True)
    assert {"accounts", "groups", "transactions", "months"} <= set(data)
    assert {c["id"] for g in data["groups"] for c in g["categories"]} >= {"c_food", "c_pay"}
    (row,) = data["transactions"]
    assert row["payee_name"] == "Corner Market" and row["subtransactions"] == []
    assert data["currency"] == "USD" and data["budget_mode"] == "envelope"


def test_the_ynab_read_carries_nothing_new(db_isolated, monkeypatch):
    monkeypatch.setattr(
        source,
        "list_plans",
        lambda store: [{"id": "p", "name": "ACME", "currency": "USD"}],
    )
    monkeypatch.setattr(
        source,
        "fetch_plan",
        lambda store, plan_id, *, with_budget, progress: {
            "accounts": [],
            "groups": [],
            "transactions": [],
            "months": [],
        },
    )
    record = job._Run(import_runs.start("ynab", "preview", "p")["id"])
    plan = plan_read.load_plan(record, YnabConfigStore(), "p", {}, "preview")
    assert not {"notices", "review", "tracking", "carryover", "known_problems"} & set(plan)
    import_runs.finish(record.id, "done")


# ── income and Starting Balances ─────────────────────────────────────────────


def test_salary_raises_ready_to_assign(server):
    server.rows.transactions = [
        txn("t1", "a1", 300_000, day(), category="c_pay", description="p1", cleared=1)
    ]
    record = run()
    assert record["status"] == "done", record["error"]
    salary = next(c for c in categories_service.all_category_rows() if c["label"] == "Salary")
    assert salary["kind"] == "income"
    held = {c for e in envelopes_service.all_envelopes() for c in e.get("category_ids") or []}
    assert salary["id"] not in held
    assert ready_to_assign() == pytest.approx(3000.0)
    (booked,) = [t for t in live_txns() if t.get("category") == salary["id"]]
    assert booked["kind"] == "income"
    assert booked["external_refs"][0]["source"] == "actual_budget"
    assert booked["source"] == "feed"


def test_income_matches_income_only(server):
    spend = categories_service.create_category("Salary", kind="expense")
    wages = categories_service.create_category("Wages", kind="income")
    server.rows.categories = [
        cat("c_pay", "Salary", "gi", income=1),
        cat("c_wages", "Wages", "gi", income=1),
        cat("c_food", "Wages", "g1"),
    ]
    server.rows.category_mapping = [
        {"id": c["id"], "transferId": c["id"]} for c in server.rows.categories
    ]
    preview = run("preview")["preview"]
    assert category_row(preview, "c_pay")["link"] is None
    assert category_row(preview, "c_wages")["link"]["ref"] == f"cat:{wages['id']}"
    assert category_row(preview, "c_food")["link"] is None
    assert [c["ref"] for c in preview["category_choices"]["income_categories"]] == [
        f"cat:{wages['id']}"
    ]
    refused = run("preview", category_links={"c_pay": f"cat:{spend['id']}"})
    assert refused["status"] == "failed" and "income" in refused["error"]
    refused = run("preview", category_links={"c_food": f"cat:{wages['id']}"})
    assert refused["status"] == "failed" and "income" in refused["error"]


def test_income_never_goes_in_an_envelope(server):
    env = planning.apply_budget_change({"type": "envelope_create", "name": "Pot", "group_id": ""})
    refused = run("preview", category_links={"c_pay": f"env:{env['envelope']['id']}"})
    assert refused["status"] == "failed" and "income" in refused["error"]


def test_budget_rows_on_income_never_make_an_envelope(server):
    server.rows.budgets = [budget(_month(), "c_pay", 50_000)]
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["preview"]["budget_months"] == 0
    salary = next(c for c in categories_service.all_category_rows() if c["label"] == "Salary")
    held = {c for e in envelopes_service.all_envelopes() for c in e.get("category_ids") or []}
    assert salary["id"] not in held


def test_starting_balance_is_the_opening(server):
    server.rows.transactions = [
        txn(
            "t1",
            "a1",
            123_400,
            day(-2),
            category="c_sb",
            description="psb",
            starting_balance_flag=1,
            cleared=1,
        ),
        txn("t2", "a2", 50_000, day(-2), description="psb", starting_balance_flag=1, cleared=1),
    ]
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["preview"]["skipped_categories"] == {"internal": 1}
    assert {c["label"] for c in categories_service.all_category_rows()}.isdisjoint(
        {"Starting Balances"}
    )
    checking, savings = account_named("ACME Checking"), account_named("ACME Savings")
    openings = ledger_service.opening_txn_ids()
    rows = live_txns()
    assert len(rows) == 2 and {t["id"] for t in rows} == set(openings)
    assert checking["opening_txn_id"] in openings and savings["opening_txn_id"] in openings
    assert balance("ACME Checking") == pytest.approx(1234.0)
    assert balance("ACME Savings") == pytest.approx(500.0)
    assert record["verification"]["ok"], record["verification"]["mismatches"]


def _opening_only(*extra_rows, category="c_sb") -> list[dict]:
    opening = txn(
        "t1",
        "a1",
        123_400,
        day(-3),
        category=category,
        description="psb",
        starting_balance_flag=1,
        cleared=1,
    )
    return [opening, *extra_rows]


def _opening_row() -> dict:
    opening_id = account_named("ACME Checking")["opening_txn_id"]
    return next(t for t in live_txns() if t["id"] == opening_id)


def _checking_rows() -> list[dict]:
    mine = account_named("ACME Checking")["id"]
    return [t for t in live_txns() if mine in (t.get("from_account_id"), t.get("to_account_id"))]


def test_a_renamed_starting_balances_is_still_the_opening(server):
    server.rows.categories = [
        cat("c_food", "Food", "g1"),
        cat("c_sb", "Opening Balances", "gi", income=1),
    ]
    server.rows.category_mapping = [
        {"id": c["id"], "transferId": c["id"]} for c in server.rows.categories
    ]
    server.rows.transactions = _opening_only()
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["preview"]["skipped_categories"] == {"internal": 1}
    assert "Opening Balances" not in {c["label"] for c in categories_service.all_category_rows()}
    assert [t["id"] for t in _checking_rows()] == [_opening_row()["id"]]
    assert _opening_row()["to_amount"] == pytest.approx(1234.0)
    assert balance("ACME Checking") == pytest.approx(1234.0)
    assert trailing_saving() is None
    assert record["verification"]["ok"], record["verification"]["mismatches"]


def test_an_opening_filed_under_salary_stays_an_opening(server):
    server.rows.transactions = _opening_only(
        txn("s1", "a1", 300_000, day(-2), category="c_pay", description="p1", cleared=1),
        txn("s2", "a1", 300_000, day(-1), category="c_pay", description="p1", cleared=1),
        category="c_pay",
    )
    record = run()
    assert record["status"] == "done", record["error"]
    salary = next(c for c in categories_service.all_category_rows() if c["label"] == "Salary")
    assert salary["kind"] == "income"
    assert _opening_row()["to_amount"] == pytest.approx(1234.0)
    assert _opening_row().get("category") in (None, "")
    paid = [t for t in live_txns() if t.get("category") == salary["id"]]
    assert sorted(t["to_amount"] for t in paid) == pytest.approx([3000.0, 3000.0])
    assert all(t["kind"] == "income" for t in paid)
    assert len(_checking_rows()) == 3
    assert balance("ACME Checking") == pytest.approx(7234.0)
    assert trailing_saving()["monthly"] == pytest.approx(3000.0)
    assert record["verification"]["ok"], record["verification"]["mismatches"]


def test_an_opening_split_into_income_stays_an_opening(server):
    server.rows.transactions = [
        txn("t1", "a1", 50_000, day(-3), isParent=1, description="psb", starting_balance_flag=1),
        child("l1", "t1", "a1", 50_000, day(-3), category="c_pay", description="psb"),
        txn("s1", "a1", 300_000, day(-1), category="c_pay", description="p1"),
    ]
    assert run()["status"] == "done"
    assert _opening_row()["to_amount"] == pytest.approx(500.0)
    assert len(_checking_rows()) == 2


def test_an_opening_in_a_spending_category_funds_it_as_before(server):
    server.rows.transactions = _opening_only(category="c_food")
    record = run()
    assert record["status"] == "done", record["error"]
    food = next(c for c in categories_service.all_category_rows() if c["label"] == "Food")
    (row,) = [t for t in live_txns() if t.get("category") == food["id"]]
    assert row["to_amount"] == pytest.approx(1234.0)
    assert _opening_row()["to_amount"] in (None, 0, 0.0)


def test_only_live_unflagged_rows_keep_an_income_category_real():
    def internal(*rows) -> bool:
        plan = shape.plan_from_rows(make_rows(transactions=list(rows)))
        return next(c for c in plan["categories"] if c["id"] == "c_pay")["internal"]

    opening = _opening_only(category="c_pay")[0]
    pay = txn("s1", "a1", 100, day(), category="c_pay")
    assert internal(opening) is True
    assert internal(opening, pay) is False
    assert internal(opening, {**pay, "tombstone": 1}) is True
    assert internal(pay) is False
    assert internal() is False


def test_a_merged_category_counts_as_the_one_it_became():
    rows = make_rows(
        categories=[cat("c_pay", "Salary", "gi", income=1), cat("c_old", "Old", "gi", income=1)],
        transactions=[
            _opening_only(category="c_old")[0],
            txn("s1", "a1", 100, day(), category="c_pay"),
        ],
    )
    rows.category_mapping = [
        {"id": "c_pay", "transferId": "c_pay"},
        {"id": "c_old", "transferId": "c_pay"},
    ]
    plan = shape.plan_from_rows(rows)
    assert next(c for c in plan["categories"] if c["id"] == "c_pay")["internal"] is False
    assert next(t for t in plan["transactions"] if t["id"] == "t1")["category_id"] is None


def _enveloped_salary() -> dict:
    made = categories_service.create_category("Salary", kind="income")
    env = planning.apply_budget_change({"type": "envelope_create", "name": "Pot", "group_id": ""})
    planning.apply_budget_change(
        {
            "type": "envelope_add_category",
            "envelope_id": env["envelope"]["id"],
            "category_id": made["id"],
        }
    )
    return made


def test_an_enveloped_income_category_is_not_matched_and_salary_reaches_ready_to_assign(server):
    enveloped = _enveloped_salary()
    server.rows.transactions = [
        txn("t1", "a1", 300_000, day(), category="c_pay", description="p1", cleared=1)
    ]
    preview = run("preview")["preview"]
    row = category_row(preview, "c_pay")
    assert row["link"] is None and row["note"].startswith("Income:")
    assert preview["category_choices"]["income_categories"] == []

    record = run()
    assert record["status"] == "done", record["error"]
    assert ready_to_assign() == pytest.approx(3000.0)
    (booked,) = [t for t in live_txns() if t["kind"] == "income"]
    assert booked["category"] != enveloped["id"]
    made = categories_service.all_category_rows()
    assert next(c for c in made if c["id"] == booked["category"])["label"] == "Salary (Actual)"


def test_picking_an_enveloped_income_category_is_refused_plainly(server):
    enveloped = _enveloped_salary()
    refused = run("preview", category_links={"c_pay": f"cat:{enveloped['id']}"})
    assert refused["status"] == "failed"
    assert "Pot" in refused["error"] and "Ready to Assign" in refused["error"]


def test_a_pair_later_put_in_an_envelope_says_where_the_money_stays(server):
    server.rows.transactions = [
        txn("t1", "a1", 300_000, day(), category="c_pay", description="p1", cleared=1)
    ]
    assert run()["status"] == "done"
    salary = next(c for c in categories_service.all_category_rows() if c["label"] == "Salary")
    env = planning.apply_budget_change({"type": "envelope_create", "name": "Pot", "group_id": ""})
    planning.apply_budget_change(
        {
            "type": "envelope_add_category",
            "envelope_id": env["envelope"]["id"],
            "category_id": salary["id"],
        }
    )
    note = category_row(run("preview")["preview"], "c_pay")["note"]
    assert "Pot" in note and "Ready to Assign" in note and not note.startswith("Income:")


# ── cards and account types ──────────────────────────────────────────────────


def test_group_named_credit_card_payments_is_ordinary(server):
    server.rows.category_groups = [group("gc", "Credit Card Payments")]
    server.rows.categories = [cat("c_visa", "ACME Visa", "gc")]
    server.rows.category_mapping = [{"id": "c_visa", "transferId": "c_visa"}]
    server.rows.budgets = [budget(_month(), "c_visa", 10_000)]
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["preview"]["skipped_categories"] == {}
    (grp,) = record["preview"]["category_groups"]
    assert grp["name"] == "Credit Card Payments (Actual)"
    envelope = next(e for e in envelopes_service.all_envelopes() if e["name"] == "ACME Visa")
    assert BudgetAssignmentsStore().all_map()[_key()][envelope["id"]] == pytest.approx(100.0)


def test_card_like_default(server):
    server.rows.accounts = [
        acct("card", "ACME Visa Card"),
        acct("flush", "ACME Visa Card 2"),
        acct("over", "ACME Checking"),
        acct("off", "ACME House", off=1),
    ]
    server.rows.transactions = [
        txn("t1", "card", -5_000, day()),
        txn("t2", "flush", 5_000, day()),
        txn("t3", "over", -5_000, day()),
        txn("t4", "off", 5_000, day()),
    ]
    shown = {a["id"]: a for a in run("preview")["preview"]["accounts"]}
    assert {k: v["type"] for k, v in shown.items()} == {
        "card": "credit",
        "flush": "cash",
        "over": "cash",
        "off": "investment",
    }
    assert shown["card"]["type_options"] == ["cash", "credit"]
    assert shown["off"]["type_options"] == [
        "fixed_deposit",
        "investment",
        "retirement",
        "receivable",
        "loan",
    ]


@pytest.mark.parametrize(
    "types, named",
    [
        ({"a1": "cash"}, "ACME Savings"),
        ({"a2": "investment"}, "ACME Checking"),
        ({"a1": "investment", "a2": "investment"}, "ACME Checking"),
        ({"a1": "loan", "a2": "investment"}, "ACME Checking"),
        ({"a1": "cash", "a2": "cash"}, "ACME Savings"),
        ({"a1": "cash", "a2": "credit"}, "ACME Savings"),
        ({"a1": "cash", "a2": "gold"}, "ACME Savings"),
    ],
)
def test_type_required_and_band_checked(server, types, named):
    record = run(account_types=types)
    assert record["status"] == "failed"
    assert named in record["error"]
    assert record["backup_name"] is None
    assert ledger_service.accounts.query_accounts(include_archived=True) == []


def test_an_unknown_account_in_the_types_fails(server):
    record = run(account_types={**TYPES, "nope": "cash"})
    assert record["status"] == "failed" and "Actual" in record["error"]


@pytest.mark.parametrize("chosen", links.OFF_BUDGET_TYPES)
def test_every_off_budget_type_can_be_created(server, chosen):
    record = run(account_types={"a1": "credit", "a2": chosen})
    assert record["status"] == "done", record["error"]
    assert account_named("ACME Checking")["type"] == "credit"
    assert account_named("ACME Savings")["type"] == chosen


def test_a_link_stays_in_the_band(server):
    _mine()
    mine = account_named("Mine")
    refused = run("preview", account_links={"a2": mine["id"]})
    assert refused["status"] == "failed" and "cannot be linked" in refused["error"]
    assert run("preview", account_links={"a1": mine["id"]})["status"] == "done"


def test_a_linked_account_needs_no_type(server):
    _mine()
    mine = account_named("Mine")
    record = run(
        account_types={"a2": "investment"},
        account_links={"a1": mine["id"]},
        confirm_nonempty=True,
    )
    assert record["status"] == "done", record["error"]
    assert account_named("Mine")["external_ids"][0]["source"] == "actual_budget"


def test_links_to_retirement_and_receivable_follow_the_asset_rule():
    pa = {"id": "x", "closed": False, "payload": {"type": "retirement", "name": "X"}}
    with using(actual_app.ACTUAL):
        account = {"id": "m", "type": "investment", "currency": "USD"}
        assert links._fits(pa, account, "USD")
        assert links._fits(
            {**pa, "payload": {**pa["payload"], "type": "receivable"}}, account, "USD"
        )
        assert not links._fits(pa, {**account, "type": "cash"}, "USD")


# ── currency ─────────────────────────────────────────────────────────────────


def test_currency_asked_when_absent(server):
    _set_base("EUR")
    server.currency = None
    preview = run("preview")["preview"]
    assert preview["currency_asked"] is True and preview["currency"] == "EUR"
    assert ledger_service.accounts.query_accounts(include_archived=True) == []

    refused = run()
    assert refused["status"] == "failed" and "currency" in refused["error"]
    assert refused["backup_name"] is None

    with pytest.raises(ValidationError):
        actual_app.start_migration("preview", BUDGET, currency="dollars")

    record = run(currency="gbp")
    assert record["status"] == "done", record["error"]
    assert account_named("ACME Checking")["currency"] == "GBP"


def test_the_budgets_own_currency_is_not_asked_and_not_overridden(server):
    assert run("preview")["preview"]["currency_asked"] is False
    assert run("preview", currency="USD")["status"] == "done"
    refused = run("preview", currency="EUR")
    assert refused["status"] == "failed" and "USD" in refused["error"]


# ── tracking mode ────────────────────────────────────────────────────────────


def _tracking(server) -> None:
    server.rows.mode = "tracking"
    server.rows.budgets = [budget(_month(), "c_food", 20_000)]
    server.rows.transactions = [
        txn("t1", "a1", 100_000, day(), description="p1", cleared=1),
        txn("t2", "a1", -5_000, day(), category="c_food", description="p1", cleared=1),
    ]


def test_tracking_import_writes_no_assigned(server):
    _tracking(server)
    preview = run("preview")["preview"]
    assert preview["budget_months"] == 0 and preview["budget_mode"] == "tracking"
    assert any("tracking" in w and "Ready to Assign" in w for w in preview["warnings"])

    record = run()
    assert record["status"] == "done", record["error"]
    assert not any(
        v for month in BudgetAssignmentsStore().all_map().values() for v in month.values()
    )
    assert record["counts"]["assigned"]["created"] == 0
    assert ready_to_assign() == pytest.approx(1000.0)
    assert any("tracking" in e["line"] for e in record["log"])
    assert record["verification"]["ok"], record["verification"]["mismatches"]
    assert not any(c["check"].endswith(_key()) for c in checks(record))
    assert any(c["check"].endswith(": balance") for c in checks(record))


def test_a_tracking_budget_on_a_fresh_book_still_sets_zero_based_and_says_why(server):
    _tracking(server)
    lines = [e["line"] for e in run()["log"]]
    assert not any("as in Actual" in line for line in lines)
    assert any(line.startswith("Budget method set to zero-based") for line in lines)


# ── verify ───────────────────────────────────────────────────────────────────


def _envelope_budget(server) -> None:
    server.rows.budgets = [
        budget(_month(), "c_food", 20_000),
        budget(_month(-1), "c_fun", 5_000),
    ]
    server.rows.transactions = [
        txn("t1", "a1", 300_000, day(-1), category="c_pay", description="p1", cleared=1),
        txn("t2", "a1", -5_000, day(), category="c_food", description="p1", cleared=1),
        txn("t3", "a1", -9_000, day(-1), category="c_fun", description="p1", cleared=1),
    ]


def test_verify_compares_balances_assigned_available(server):
    _envelope_budget(server)
    record = run()
    assert record["status"] == "done", record["error"]
    by_name = {c["check"]: c for c in checks(record)}
    assert by_name["ACME Checking: balance"]["ynab_value"] == pytest.approx(2860.0)
    assert by_name[f"Food: assigned {_key()}"]["ynab_value"] == pytest.approx(200.0)
    assert by_name[f"Food: available {_key()}"]["ynab_value"] == pytest.approx(150.0)
    assert by_name[f"Food: available {_key()}"]["onw_value"] == pytest.approx(150.0)


def test_no_ready_row_and_ok_true(server):
    _envelope_budget(server)
    record = run()
    assert record["verification"]["ok"], record["verification"]["mismatches"]
    assert not any("Ready to Assign" in c["check"] for c in checks(record))
    again = run("verify")
    assert again["verification"]["ok"], again["verification"]["mismatches"]
    assert not any("Ready to Assign" in c["check"] for c in checks(again))


def test_a_real_difference_still_fails_and_names_no_ready_row(server):
    _envelope_budget(server)
    run()
    server.rows.budgets = [budget(_month(), "c_food", 30_000), budget(_month(-1), "c_fun", 5_000)]
    record = run("verify")
    assert record["verification"]["ok"] is False
    names = [m["check"] for m in record["verification"]["mismatches"]]
    assert f"Food: assigned {_key()}" in names
    assert not any("Ready to Assign" in name for name in names)


def _carryover(server) -> None:
    server.rows.budgets = [
        budget(_month(-1), "c_fun", 0, carry=1),
        budget(_month(), "c_food", 1_000),
    ]
    server.rows.transactions = [
        txn("t0", "a1", 300_000, day(-1), category="c_pay", description="p1", cleared=1),
        txn("t1", "a1", -2_000, day(-1), category="c_fun", description="p1", cleared=1),
    ]


def test_carryover_row_is_known_difference(server):
    _carryover(server)
    preview = run("preview")["preview"]
    assert any("Carryover" in w and "Fun" in w for w in preview["warnings"])

    record = run()
    assert record["verification"]["ok"], record["verification"]["mismatches"]
    row = next(c for c in checks(record) if c["check"] == f"Fun: available {_key()}")
    assert row["ynab_value"] == pytest.approx(-20.0)
    assert row["onw_value"] == pytest.approx(0.0)
    assert "known difference" in row["note"]


def test_a_carryover_flag_with_nothing_overspent_says_nothing(server):
    server.rows.budgets = [budget(_month(-1), "c_fun", 5_000, carry=1)]
    preview = run("preview")["preview"]
    assert not any("Carryover" in w for w in preview["warnings"])


def test_carryover_stays_out_of_the_first_divergence(server):
    _carryover(server)
    run()
    server.rows.budgets = [
        budget(_month(-1), "c_fun", 0, carry=1),
        budget(_month(-1), "c_food", 1_000),
        budget(_month(), "c_food", 1_000),
    ]
    record = run("verify")
    assert record["verification"]["ok"] is False
    assert not any(m["check"].startswith("Fun:") for m in record["verification"]["mismatches"]), (
        record["verification"]["mismatches"]
    )


def test_held_money_disclosed(server):
    server.rows.held = [
        {"id": _key(), "buffered": 5_000},
        {"id": _key(-3), "buffered": 7_700},
        {"id": _key(1), "buffered": 0},
    ]
    warnings = run("preview")["preview"]["warnings"]
    (line,) = [w for w in warnings if "holding" in w]
    assert _key() in line and "50.00" in line and "Ready to Assign" in line
    assert not any("77.00" in w for w in warnings)


def test_held_money_reads_from_the_copy_and_tolerates_no_table():
    conn = sqlite3.connect(":memory:")
    assert actual_rows.read_held(conn) == []
    conn.execute("CREATE TABLE zero_budget_months (id TEXT PRIMARY KEY, buffered INTEGER)")
    conn.execute("INSERT INTO zero_budget_months VALUES ('2030-01', 4200)")
    conn.row_factory = sqlite3.Row
    assert actual_rows.read_held(conn) == [{"id": "2030-01", "buffered": 4200}]


def test_ready_to_assign_not_matching_is_said_once(server):
    warnings = run("preview")["preview"]["warnings"]
    assert sum("To Budget" in w for w in warnings) == 1


def test_a_plain_http_warning_reaches_the_review(server):
    server.notices = ["This Actual Budget server is reached over plain http."]
    assert server.notices[0] in run("preview")["preview"]["warnings"]


# ── Review Focus 1 and 5 ─────────────────────────────────────────────────────


def _bad_split(server) -> None:
    server.rows.transactions = [
        txn("t1", "a1", -10_000, day(), isParent=1, description="p1", cleared=1),
        child("l1", "t1", "a1", -6_000, day(), category="c_food", description="p1", cleared=1),
        child("l2", "t1", "a1", -3_000, day(), category="c_fun", description="p1", cleared=1),
    ]


def test_split_not_summing_is_reported(server):
    _bad_split(server)
    warnings = run("preview")["preview"]["warnings"]
    (line,) = [w for w in warnings if "split" in w.lower()]
    assert "-100.00" in line and "-90.00" in line and "ACME Checking" in line

    record = run()
    assert record["status"] == "done", record["error"]
    assert balance("ACME Checking") == pytest.approx(-90.0)
    assert record["verification"]["ok"], record["verification"]["mismatches"]
    (problem,) = record["problems"]
    assert problem["item"] == "t1" and "90.00" in problem["reason"]
    assert "Actual" in problem["hint"]
    assert any("90.00" in e["line"] for e in record["log"])


def _gaps(count: int) -> list[dict]:
    rows = []
    for n in range(count):
        rows.append(txn(f"t{n}", "a1", -1_000, day(), isParent=1, description="p1", cleared=1))
        rows.append(child(f"l{n}", f"t{n}", "a1", -500, day(), category="c_food", cleared=1))
    return rows


def test_real_refusals_come_first_and_the_overflow_is_counted(server, monkeypatch):
    server.rows.transactions = _gaps(105)
    refused = {"ref": "r1", "reason": "Refused.", "kind": "failed"}
    real = job.apply.send_transactions

    def send(*args, **kwargs):
        tally, problems = real(*args, **kwargs)
        return tally, [refused, *problems]

    monkeypatch.setattr(job.apply, "send_transactions", send)
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["problems"][0]["item"] == "r1"
    assert len(record["problems"]) == 100
    assert any(e["line"].startswith("6 more problems") for e in record["log"])
    notice = next(w for w in record["preview"]["warnings"] if "split" in w.lower())
    assert "100 more" in notice and "first 100" in notice


def test_a_split_that_sums_says_nothing(server):
    server.rows.transactions = [
        txn("t1", "a1", -9_000, day(), isParent=1, description="p1", cleared=1),
        child("l1", "t1", "a1", -6_000, day(), category="c_food", description="p1", cleared=1),
        child("l2", "t1", "a1", -3_000, day(), category="c_fun", description="p1", cleared=1),
    ]
    record = run()
    assert record["problems"] == []
    assert not any("split" in w.lower() for w in record["preview"]["warnings"])


def test_a_parent_with_no_lines_left_is_reported():
    rows = make_rows(transactions=[txn("t1", "a1", -10_000, day(), isParent=1)])
    (gap,) = shape.plan_from_rows(rows)["split_gaps"]
    assert (gap["id"], gap["parent"], gap["lines"]) == ("t1", -100_000, 0)


def test_split_line_transfer(server):
    server.rows.transactions = [
        txn("t1", "a1", -10_000, day(), isParent=1, description="p1", cleared=1),
        child("l1", "t1", "a1", -6_000, day(), category="c_food", description="p1", cleared=1),
        child("l2", "t1", "a1", -4_000, day(), description="pt2", transferred_id="m1", cleared=1),
        txn("m1", "a2", 4_000, day(), description="pt1", transferred_id="l2", cleared=1),
    ]
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["verification"]["ok"], record["verification"]["mismatches"]
    rows = [t for t in live_txns() if t["id"] not in ledger_service.opening_txn_ids()]
    assert sorted(t["kind"] for t in rows) == ["expense", "transfer"]
    (move,) = [t for t in rows if t["kind"] == "transfer"]
    assert move["to_amount"] == pytest.approx(40.0)
    assert {r["ref"] for r in move["external_refs"]} >= {"l2", "m1"}
    assert balance("ACME Checking") == pytest.approx(-100.0)
    assert balance("ACME Savings") == pytest.approx(40.0)


# ── one app at a time ────────────────────────────────────────────────────────


def test_refused_while_other_sync_on(server):
    YnabSyncStateStore().save({"id": "state", "enabled": True})
    with pytest.raises(ConflictError) as refused:
        actual_app.start_migration("import", BUDGET, account_types=TYPES)
    assert "YNAB" in refused.value.message and "Actual" in refused.value.message
    assert run("preview")["status"] == "done"


def _actual_with_sync_on() -> budget_app.BudgetApp:
    state = SimpleNamespace(doc=lambda doc_id: {"id": doc_id, "enabled": True})
    return dataclasses.replace(
        actual_app.ACTUAL,
        stores=dataclasses.replace(actual_app.ACTUAL.stores, state=lambda: state),
    )


def test_a_ynab_import_is_refused_while_actuals_sync_is_on(db_isolated, monkeypatch):
    monkeypatch.setattr(
        budget_app, "_apps", {"ynab": YNAB, "actual_budget": _actual_with_sync_on()}
    )
    YnabConfigStore().update({"access_token": "stub"})
    with pytest.raises(ConflictError) as refused:
        job.start("import", "plan-1")
    assert "Actual" in refused.value.message


@pytest.mark.flags(actual_budget_sync=False, ynab_import_and_sync=True)
def test_a_flagged_off_actual_blocks_nothing(db_isolated, monkeypatch):
    monkeypatch.setattr(
        budget_app, "_apps", {"ynab": YNAB, "actual_budget": _actual_with_sync_on()}
    )
    assert budget_app.other_sync_on() is None


@pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=False)
def test_a_flagged_off_ynab_blocks_nothing(db_isolated):
    YnabSyncStateStore().save({"id": "state", "enabled": True})
    with using(actual_app.ACTUAL):
        assert budget_app.other_sync_on() is None


def test_refused_while_other_running(server):
    run_id = import_runs.start("ynab", "sync", "plan-1")["id"]
    with pytest.raises(ConflictError) as refused:
        actual_app.start_migration("import", BUDGET, account_types=TYPES)
    assert "YNAB" in refused.value.message
    import_runs.finish(run_id, "done")
    assert run()["status"] == "done"


def test_a_ynab_import_is_refused_while_actual_runs(db_isolated):
    YnabConfigStore().update({"access_token": "stub"})
    run_id = import_runs.start("actual_budget", "import", BUDGET)["id"]
    with pytest.raises(ConflictError) as refused:
        job.start("import", "plan-1")
    assert "Actual" in refused.value.message
    import_runs.finish(run_id, "done")


def test_a_second_actual_run_says_its_name(server, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(job, "_execute", lambda *a: gate.wait(10))
    actual_app.start_migration("preview", BUDGET)
    with pytest.raises(ConflictError) as refused:
        actual_app.start_migration("preview", BUDGET)
    gate.set()
    assert refused.value.message == "An Actual run is already in progress."
    deadline = time.time() + 30
    while budget_app.import_runs.has_running("actual_budget") and time.time() < deadline:
        time.sleep(0.02)


# ── no sync yet ──────────────────────────────────────────────────────────────


def _boom(*_args, **_kwargs):
    raise AssertionError("reached YNAB's sync")


def test_a_sync_direction_is_refused_plainly(server, monkeypatch):
    monkeypatch.setattr(runner, "update_settings", _boom)
    for call in (
        lambda: actual_app.start_migration("import", BUDGET, sync_direction="both"),
        lambda: _start_as_actual(sync_direction="both"),
    ):
        with pytest.raises(ValidationError) as refused:
            call()
        assert "Actual" in refused.value.message and "sync" in refused.value.message
    assert import_runs.latest("actual_budget", job.KINDS) is None


def _start_as_actual(**kw):
    with using(actual_app.ACTUAL):
        return job.start("import", BUDGET, **kw)


def test_turning_on_the_sync_cannot_reach_ynab_from_an_actual_import(server, monkeypatch):
    monkeypatch.setattr(runner, "update_settings", _boom)
    before = YnabSyncStateStore().doc("state")
    baselines = YnabBaselineStore().all()
    lines: list[str] = []
    fake = SimpleNamespace(log=lines.append)
    with using(actual_app.ACTUAL):
        job._turn_on_sync(fake, BUDGET, "both")
    assert len(lines) == 1 and "YNAB" not in lines[0] and "not turned on" in lines[0]
    assert YnabSyncStateStore().doc("state") == before
    assert YnabBaselineStore().all() == baselines


def test_an_actual_import_writes_nothing_to_ynabs_stores(server):
    _envelope_budget(server)
    record = run()
    assert record["status"] == "done", record["error"]
    assert YnabBaselineStore().all() == []
    assert ActualBaselineStore().pairs(BUDGET)[0].keys() >= {"c_food", "c_fun", "c_pay"}
    assert YnabSyncStateStore().find_by_id("state") is None


# ── re-running, and wording ──────────────────────────────────────────────────


def test_import_idempotent(server):
    planning.apply_budget_change({"type": "envelope_create", "name": "Fun", "group_id": ""})
    pot = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Fun")
    for label in ("Fun one", "Fun two"):
        made = categories_service.create_category(label, kind="expense")
        planning.apply_budget_change(
            {"type": "envelope_add_category", "envelope_id": pot["id"], "category_id": made["id"]}
        )
    _envelope_budget(server)
    server.rows.transactions.append(
        txn("t9", "a1", -1_000, day(2), category="c_food", description="p1")
    )
    first = run(confirm_nonempty=True)
    assert first["status"] == "done", first["error"]
    assert first["verification"]["ok"], first["verification"]["mismatches"]
    labels = sorted(c["label"] for c in categories_service.all_category_rows())
    assert "Fun (Actual)" in labels
    pairs = ActualBaselineStore().pairs(BUDGET)
    rows = len(live_txns())
    accounts = len(ledger_service.accounts.query_accounts(include_archived=True))
    assigned = BudgetAssignmentsStore().all_map()

    second = run(confirm_nonempty=True)
    assert second["status"] == "done", second["error"]
    assert second["counts"]["transactions"]["created"] == 0
    assert second["counts"]["accounts"]["created"] == 0
    assert second["counts"]["categories"]["created"] == 0
    assert second["counts"]["assigned"]["created"] == 0
    assert sorted(c["label"] for c in categories_service.all_category_rows()) == labels
    assert len(live_txns()) == rows
    assert len(ledger_service.accounts.query_accounts(include_archived=True)) == accounts
    assert BudgetAssignmentsStore().all_map() == assigned
    assert ActualBaselineStore().pairs(BUDGET) == pairs


def test_a_rerun_needs_no_type_for_an_account_already_here(server):
    assert run()["status"] == "done"
    again = run(account_types={})
    assert again["status"] == "done", again["error"]
    assert again["book_state"] == "rerun"


def test_a_future_dated_row_is_booked(server):
    server.rows.transactions = [txn("t1", "a1", -1_000, day(3), description="p1")]
    record = run()
    assert record["status"] == "done", record["error"]
    assert record["counts"]["transactions"]["created"] == 1
    (row,) = [t for t in live_txns() if t["id"] not in ledger_service.opening_txn_ids()]
    assert row["date"] == f"{_key(3)}-03"


def test_nothing_a_person_reads_says_ynab(server):
    _bad_split(server)
    server.rows.category_groups.append(group("gc", "Credit Card Payments"))
    server.rows.categories.append(cat("c_visa", "ACME Visa", "gc"))
    server.rows.category_mapping.append({"id": "c_visa", "transferId": "c_visa"})
    server.rows.budgets = [budget(_month(), "c_visa", 1_000)]
    preview = run("preview")
    record = run()
    verify = run("verify")
    said = strings(
        [
            preview["preview"],
            preview["phase"],
            record["log"],
            record["problems"],
            record["warnings"],
            record["verification"],
            verify["verification"],
        ]
    )
    assert said and not [s for s in said if "YNAB" in s]


def test_refusals_say_actual_not_a_plan_or_a_token(server, monkeypatch):
    with pytest.raises(ValidationError) as refused:
        actual_app.start_migration("preview", "  ")
    assert refused.value.message == "pick the Actual budget to migrate"
    with monkeypatch.context() as m:
        m.setattr(client, "list_budgets", lambda store=None: [])
        failed = run("preview")
    assert failed["error"] == "That Actual budget is not visible to the saved sign-in."
    with monkeypatch.context() as m:
        m.setattr(ActualBudgetConfigStore, "is_configured", lambda self: False)
        with pytest.raises(ValidationError) as refused:
            actual_app.start_migration("preview", BUDGET)
    assert "Actual" in refused.value.message and "token" not in refused.value.message


def test_a_currency_refusal_says_the_budget_not_the_plan(server):
    _mine()
    server.currency = "EUR"
    record = run(confirm_nonempty=True)
    assert record["status"] == "failed"
    assert "the Actual budget is EUR" in record["error"]


def test_a_preview_read_is_not_reused_for_another_app(server):
    reads: list[str] = []

    class Reader(actual_reader.ActualReader):
        def fetch_plan(self, store, plan_id, *, with_budget, progress=lambda *_: None):
            reads.append(budget_app.current().key)
            return super().fetch_plan(store, plan_id, with_budget=with_budget, progress=progress)

    first = dataclasses.replace(actual_app.ACTUAL, reader=Reader())
    second = dataclasses.replace(first, key="acme")
    fake = SimpleNamespace(id="run-1", progress=lambda *_: None)
    with using(first):
        plan_read.read(fake, None, BUDGET, None)
        plan_read.read(SimpleNamespace(id="run-2", progress=lambda *_: None), None, BUDGET, "run-1")
    assert reads == ["actual_budget"]  # the same app re-checks from what it read
    with using(second):
        plan_read.read(SimpleNamespace(id="run-3", progress=lambda *_: None), None, BUDGET, "run-2")
    assert reads == ["actual_budget", "acme"]


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def api(server):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _settled(api) -> dict:
    deadline = time.time() + 60
    while time.time() < deadline:
        body = api.get("/api/actual-budget/migration").json()
        if body["job"] and body["job"]["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("the run did not finish")


def test_the_routes_run_a_preview_and_an_import(api):
    response = api.post("/api/actual-budget/migration/preview", json={"plan_id": BUDGET})
    assert response.status_code == 202, response.text
    body = _settled(api)
    assert body["token_saved"] is True and body["job"]["kind"] == "preview"

    response = api.post(
        "/api/actual-budget/migration/start",
        json={"plan_id": BUDGET, "account_types": TYPES, "currency": "USD"},
    )
    assert response.status_code == 202, response.text
    done = _settled(api)["job"]
    assert done["kind"] == "import" and done["status"] == "done", done["error"]

    response = api.post("/api/actual-budget/migration/verify", json={"plan_id": BUDGET})
    assert response.status_code == 202, response.text
    assert _settled(api)["job"]["kind"] == "verify"


def test_the_route_asks_for_confirmation_on_a_book_with_its_own_accounts(api):
    _mine()
    response = api.post(
        "/api/actual-budget/migration/start", json={"plan_id": BUDGET, "account_types": TYPES}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "confirm_nonempty"


def test_the_route_refuses_a_sync_direction(api, monkeypatch):
    monkeypatch.setattr(runner, "update_settings", _boom)
    response = api.post(
        "/api/actual-budget/migration/start",
        json={"plan_id": BUDGET, "account_types": TYPES, "sync_direction": "both"},
    )
    assert response.status_code == 422
    assert "sync" in response.json()["detail"]
    assert api.get("/api/actual-budget/migration").json()["job"] is None


@pytest.mark.flags(actual_budget_sync=False)
def test_flag_off_the_routes_and_the_seam_are_gone(db_isolated):
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    assert api.get("/api/actual-budget/migration").status_code == 404
    assert (
        api.post("/api/actual-budget/migration/preview", json={"plan_id": BUDGET}).status_code
        == 404
    )
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        actual_app.start_migration("preview", BUDGET)
    with pytest.raises(NotFoundError):
        actual_app.migration_status()


def test_behind_the_gateway_the_routes_and_the_seam_refuse(api, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config, "gateway_active", lambda: True)
    assert api.get("/api/actual-budget/migration").status_code == 409
    assert (
        api.post("/api/actual-budget/migration/preview", json={"plan_id": BUDGET}).status_code
        == 409
    )
    with pytest.raises(ConflictError):
        actual_app.start_migration("preview", BUDGET)
    with pytest.raises(ConflictError):
        actual_app.migration_status()


# ── the pairs' table ─────────────────────────────────────────────────────────


def test_a_reset_wipes_actuals_pairs(db_isolated):
    from backend.db import portable_bridge
    from backend.db.engine import get_engine

    store = ActualBaselineStore()
    store.upsert({"id": "c:x", "plan": "p", "category": "cat_1"})
    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "actual_sync.json")
    assert ActualBaselineStore().all() == []


def test_actuals_pairs_are_not_checkpointed():
    from backend.ai.checkpoint._policy import _EXCLUDED_TABLES

    assert "actual_sync_baselines" in _EXCLUDED_TABLES
