"""The two-way sync for Actual: the real engine over the real ``ActualClient`` and a made-up server
(``actual_sync_stub``), starting from a real import. Every name and number is invented."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import scheduler
from backend.api.actual_budget_api import router
from backend.core import audit_context
from backend.core.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import (
    ActualBaselineStore,
    ActualConflictStore,
    ActualMirrorStore,
    ActualSyncStateStore,
)
from backend.db.payee_store import PayeeStore
from backend.db.ynab_config_store import YnabConfigStore
from backend.db.ynab_sync_store import YnabSyncStateStore
from backend.services import budget_app, import_runs, planning, ynab_sync
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import shape, writes
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import YNAB, using
from backend.services.ynab_migration import job
from backend.services.ynab_sync import (
    baselines,
    delta,
    pull,
    push,
    resolve,
    runner,
    units,
)
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync import state as sync_state
from tests.actual_sync_stub import (
    BUDGET,
    BUDGET_B,
    HOST,
    PASSWORD,
    URL,
    FakeServer,
    down,
    seed,
    seed_other_budget,
)
from tests.flags import flags

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

TODAY = user_today().isoformat()
KEY = TODAY[:7]
PAST = "2020-01-01"
FUTURE = f"{int(TODAY[:4]) + 1}-03-01"
TYPES = {"a1": "cash", "a2": "investment", "a3": "credit"}
WAIT = 30


def _import(**kw) -> dict:
    actual_app.start_migration("import", BUDGET, account_types=TYPES, **kw)
    deadline = time.time() + 60
    while time.time() < deadline:
        record = import_runs.latest("actual_budget", job.KINDS)
        if record["status"] != "running":
            assert record["status"] == "done", record["error"]
            return record
        time.sleep(0.02)
    raise AssertionError("the import did not finish")


def _prepared(tmp_path, monkeypatch) -> FakeServer:
    server = FakeServer(tmp_path)
    server.install(monkeypatch)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    ActualBudgetConfigStore().update({"server_url": URL, "password": PASSWORD})
    return server


@pytest.fixture
def world(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    _import()
    server.calls.clear()
    return server


def enable(direction="both", interval="1h") -> dict:
    return actual_app.sync_settings(True, interval, BUDGET, direction)


def _run(trigger: str = "manual") -> dict:
    with using(ACTUAL):
        runner._locked_run(trigger)
        return sync_state.load()["last_run"]


def sync() -> dict:
    run = _run()
    assert run["ok"], run["error"]
    return run


def writes_seen(server: FakeServer) -> list[str]:
    return [c for c in server.calls if c == "commit"]


def row(description: str) -> dict:
    (found,) = [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == description and not t.get("deleted")
    ]
    return found


def as_person(call, *args, **kw):
    token = audit_context.set_audit_scope("user", "ui")
    try:
        return call(*args, **kw)
    finally:
        audit_context.clear_audit_scope(token)


def edit_here(txn_id: str, **changes) -> dict:
    return as_person(ledger_service.update_transaction, txn_id, changes)


def category_here(label: str) -> dict:
    return next(c for c in categories_service.all_category_rows() if c["label"] == label)


def account_here(name: str) -> dict:
    return next(a for a in ledger_service.accounts.query_accounts() if a["name"] == name)


def envelope_here(name: str) -> dict:
    return next(e for e in envelopes_service.all_envelopes() if e["name"] == name)


def new_row(**over) -> dict:
    account = account_here("ACME Checking")
    return as_person(
        ledger_service.create_transaction,
        {
            "kind": "expense",
            "date": TODAY,
            "description": "ACME Coffee",
            "memo": "",
            "from_account_id": account["id"],
            "from_amount": 12.34,
            "from_currency": "USD",
            "cleared": True,
            "needs_review": False,
            **over,
        },
    )


def open_items() -> list[dict]:
    return actual_app.sync_status()["conflicts"]


# ── the first run ──


def test_the_first_run_after_an_import_changes_nothing(world):
    enable()
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(world) == [] and open_items() == []
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(world) == []


def test_each_store_is_actuals_own(world):
    enable()
    sync()
    assert ActualSyncStateStore().doc("state")["bootstrapped"] is True
    assert ActualMirrorStore().by_id().keys() >= {"t-market"}
    assert ActualBaselineStore().split()[0]
    assert ActualConflictStore().open_items() == []
    assert YnabSyncStateStore().all() == []


def test_a_renamed_starting_balance_is_not_pushed_back(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    server.edit("UPDATE categories SET name = 'Opening Money' WHERE id = 'c-sb'")
    server.edit("UPDATE payees SET name = 'Old Bank' WHERE id = 'p-sb'")
    server.queue.clear()
    _import()
    server.calls.clear()
    before = server.row("transactions", "t-open")
    enable()
    assert sync()["summary"] == "Nothing changed"
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(server) == []
    assert server.row("transactions", "t-open") == before
    assert before["category"] == "c-sb"


def test_an_opening_filed_under_salary_reads_no_change_on_any_run(
    db_isolated, tmp_path, monkeypatch
):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    server.edit("UPDATE transactions SET category = 'c-pay' WHERE id = 't-open'")
    server.queue.clear()
    _import()
    server.calls.clear()
    enable()
    assert sync()["summary"] == "Nothing changed"
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(server) == []
    assert server.row("transactions", "t-open")["category"] == "c-pay"


def test_the_mirror_reads_no_phantom_change_for_an_opening(db_isolated, tmp_path):
    server = FakeServer(tmp_path)
    seed(server, KEY, PAST, TODAY)
    server.edit("UPDATE transactions SET category = 'c-pay' WHERE id = 't-open'")
    mirror: dict = {}
    meta = {"accounts": [], "groups": [], "categories": [], "payees": {}, "months": []}
    plan = server.plan()
    delta.apply_read({"plan": plan, "server_knowledge": 1}, mirror, meta, True)
    assert mirror["t-open"]["category_id"] is None
    again = shape.delta_against(server.plan(), mirror, meta)
    assert again["transactions"] == [] and again["subtransactions"] == []
    assert again["payees"] == []


# ── what differs from YNAB ──


def test_future_date_sent(world):
    enable()
    sync()
    new_row(date=FUTURE, category=category_here("Food")["id"])
    assert sync()["summary"] == "1 sent to Actual"
    (made,) = world.live("date = ?", int(FUTURE.replace("-", "")))
    assert made["amount"] == -1_234 and made["category"] == "c-food"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_split_edit_sent(world):
    enable()
    sync()
    split = next(t for t in ledger_service.transactions.all() if t.get("splits"))
    food, fun = category_here("Food")["id"], category_here("Fun")["id"]
    edit_here(
        split["id"],
        date=PAST[:4] + "-06-15",
        from_amount=70.0,
        splits=[
            {"category": food, "amount": 30.0, "memo": "one"},
            {"category": fun, "amount": 25.0, "memo": "two"},
            {"category": fun, "amount": 15.0, "memo": "three"},
        ],
    )
    assert sync()["summary"] == "1 sent to Actual"
    parent = world.row("transactions", "t-split")
    assert parent["amount"] == -7_000 and parent["date"] == int(PAST[:4] + "0615")
    lines = world.live("parent_id = 't-split'")
    assert sorted(r["amount"] for r in lines) == [-3_000, -2_500, -1_500]
    assert all(r["date"] == parent["date"] for r in lines)
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    refreshed = ledger_service.transactions.find_by_id(split["id"])
    assert len([e for e in refreshed["external_refs"] if e["source"] == "actual_budget"]) == 4


def test_a_split_made_here_can_be_edited_and_deleted_after(world):
    enable()
    sync()
    food, fun = category_here("Food")["id"], category_here("Fun")["id"]
    made = new_row(
        description="ACME Shop",
        from_amount=30.0,
        splits=[
            {"category": food, "amount": 20.0, "memo": "a"},
            {"category": fun, "amount": 10.0, "memo": "b"},
        ],
    )
    assert sync()["summary"] == "1 sent to Actual"
    edit_here(
        made["id"],
        from_amount=33.0,
        splits=[
            {"category": fun, "amount": 25.0, "memo": "x"},
            {"category": food, "amount": 8.0, "memo": "y"},
        ],
    )
    assert sync()["summary"] == "1 sent to Actual"
    assert open_items() == []
    assert sorted(r["amount"] for r in world.live("notes IN ('x', 'y')")) == [-2_500, -800]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    as_person(ledger_service.bulk_delete_transactions, [made["id"]])
    assert sync()["summary"] == "1 sent to Actual"
    assert not world.live("notes IN ('x', 'y') OR amount = -3300")


def test_splitting_an_existing_row_is_sent(world):
    enable()
    sync()
    market = next(
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "Corner Market" and not t.get("splits")
    )
    food, fun = category_here("Food")["id"], category_here("Fun")["id"]
    edit_here(
        market["id"],
        category=None,
        splits=[
            {"category": food, "amount": 20.0, "memo": ""},
            {"category": fun, "amount": 5.0, "memo": ""},
        ],
    )
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-market")["isParent"] == 1
    assert sorted(r["amount"] for r in world.live("parent_id = 't-market'")) == [-2_000, -500]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_piece_of_a_larger_split_stays_in_review(world):
    enable()
    sync()
    # A plain line beside a transfer line: the two lines here are separate rows of one parent.
    world.edit(
        "UPDATE transactions SET description = 'pt-a2', transferred_id = 't-l2b', category = NULL WHERE id = 't-l2'"
    )
    world.queue.clear()
    world.txn("t-l2b", "a2", 2_000, TODAY, description="pt-a1", transferred_id="t-l2")
    sync()
    split = next(
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and any(e["ref"] == "t-split" for e in t.get("external_refs") or [])
    )
    edit_here(split["id"], date=PAST[:4] + "-06-15")
    world.calls.clear()
    sync()
    assert writes_seen(world) == []
    assert any("larger" in c["reason"] for c in open_items())
    assert len(world.live("parent_id = 't-split'")) == 2


def test_hide_not_parked(world):
    enable()
    sync()
    categories_service.set_category_hidden(category_here("Fun")["id"], True)
    groups = len(world.rows("category_groups"))
    assert "1 category change" in sync()["summary"]
    row_ = world.row("categories", "c-fun")
    assert row_["hidden"] == 1 and row_["cat_group"] == "g1"
    assert len(world.rows("category_groups")) == groups
    categories_service.set_category_hidden(category_here("Fun")["id"], False)
    sync()
    assert world.row("categories", "c-fun")["hidden"] == 0
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_delete_in_actual_hides_here(world):
    enable()
    sync()
    world.edit("UPDATE categories SET tombstone = 1 WHERE id = 'c-fun'")
    world.queue.clear()
    sync()
    assert category_here("Fun")["hidden"] is True
    assert writes_seen(world) == []


def test_never_deletes_actual_category(world):
    enable()
    sync()
    planning.apply_budget_change(
        {"type": "category_delete", "key": category_here("Fun")["id"], "reassign": True}
    )
    sync()
    sync()
    row_ = world.row("categories", "c-fun")
    assert row_["tombstone"] == 0 and row_["hidden"] == 1


def test_a_new_category_here_reaches_actual_in_its_group_and_income_in_the_income_group(world):
    enable()
    sync()
    made = planning.apply_budget_change(
        {"type": "category_create", "label": "Pets", "kind": "expense"}
    )
    planning.apply_budget_change(
        {
            "type": "envelope_add_category",
            "envelope_id": envelope_here("Food")["id"],
            "category_id": made["id"],
        }
    )
    income = planning.apply_budget_change(
        {"type": "category_create", "label": "Rental", "kind": "income"}
    )
    sync()
    pets = world.rows("categories", "name = 'Pets'")[0]
    rental = world.rows("categories", "name = 'Rental'")[0]
    assert pets["cat_group"] == "g1" and pets["is_income"] == 0
    assert rental["cat_group"] == "gi" and rental["is_income"] == 1
    assert income["id"]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_income_row_round_trips(world):
    world.category("c-gift", "Gifts", "gi", income=1)
    enable()
    sync()
    account = row("ACME Corp")["to_account_id"]
    salary = category_here("Salary")["id"]
    new_row(
        kind="income",
        description="ACME Corp Bonus",
        from_account_id=None,
        from_amount=None,
        from_currency=None,
        to_account_id=account,
        to_amount=50.0,
        to_currency="USD",
        category=salary,
    )
    assert sync()["summary"] == "1 sent to Actual"
    (made,) = world.live("amount = 5000")
    assert made["category"] == "c-pay"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    world.edit("UPDATE transactions SET category = 'c-gift' WHERE id = ?", made["id"])
    world.queue.clear()
    assert sync()["summary"] == "1 pulled"
    assert row("ACME Corp Bonus")["category"] == category_here("Gifts")["id"]
    assert category_here("Gifts")["kind"] == "income"


def test_a_blank_income_row_is_reported_once_for_its_payee(world):
    enable()
    sync()
    account = row("ACME Corp")["to_account_id"]
    for amount in (10.0, 20.0):
        new_row(
            kind="income",
            description="ACME Refund",
            from_account_id=None,
            from_amount=None,
            from_currency=None,
            to_account_id=account,
            to_amount=amount,
            to_currency="USD",
            category=None,
        )
    with using(ACTUAL):
        runner._locked_run("manual")
        run = import_runs.get(sync_state.load()["last_run"]["run_id"])
    told = [n for n in run["notes"] if n["item"] == "ACME Refund"]
    assert len(told) == 1 and "To Budget" in told[0]["reason"]
    assert all(
        r["category"] is None
        for r in world.live("description IS NOT NULL AND amount IN (1000, 2000)")
    )


def test_card_envelope_assigned_never_moved(world):
    enable()
    sync()
    card_env = next(e for e in envelopes_service.all_envelopes() if "ACME Card" in e["name"])
    planning.apply_budget_change(
        {"type": "assign", "month": KEY, "envelope_id": card_env["id"], "amount": 77.0}
    )
    cells = world.rows("zero_budgets")
    cats = world.rows("categories")
    world.calls.clear()
    sync()
    assert world.rows("zero_budgets") == cells and world.rows("categories") == cats
    assert writes_seen(world) == []
    assert planning.assignments_store.month_map(KEY)[card_env["id"]] == 77.0


def test_no_flag_sent(world, monkeypatch):
    sent: list[dict] = []
    real = writes.ActualClient.save_transactions

    def spy(self, method, rows):
        sent.extend(rows)
        return real(self, method, rows)

    monkeypatch.setattr(writes.ActualClient, "save_transactions", spy)
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="bonus")
    new_row(category=category_here("Food")["id"])
    sync()
    assert len(sent) == 2 and all("flag_color" not in op for op in sent)


def test_an_assigned_change_reaches_actual_with_its_note_and_back(world):
    enable()
    sync()
    food = envelope_here("Food")["id"]
    planning.apply_budget_change(
        {"type": "assign", "month": KEY, "envelope_id": food, "amount": 450.0}
    )
    assert sync()["summary"] == "1 sent to Actual"
    (cell,) = world.rows("zero_budgets", "category = 'c-food'")
    assert cell["amount"] == 45_000
    assert world.row("notes", "c-food")["note"] == f"ONW {KEY}: assigned 450.00 (was 400.00)"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    world.edit("UPDATE zero_budgets SET amount = 50000 WHERE category = 'c-food'")
    world.queue.clear()
    assert sync()["summary"] == "1 pulled"
    assert planning.assignments_store.month_map(KEY)[food] == 500.0


# ── tracking ──


def test_tracking_skips_assigned(world):
    enable()
    sync()
    food = envelope_here("Food")["id"]
    world.mode = "tracking"
    world.insert(
        "reflect_budgets",
        id="r1",
        month=int(KEY.replace("-", "")),
        category="c-food",
        amount=99_900,
        carryover=0,
    )
    planning.apply_budget_change(
        {"type": "assign", "month": KEY, "envelope_id": food, "amount": 123.0}
    )
    edit_here(row("ACME Corp")["id"], memo="still syncs")
    cells = world.rows("zero_budgets"), world.rows("reflect_budgets")
    with using(ACTUAL):
        runner._locked_run("manual")
        run = import_runs.get(sync_state.load()["last_run"]["run_id"])
    assert (world.rows("zero_budgets"), world.rows("reflect_budgets")) == cells
    assert planning.assignments_store.month_map(KEY)[food] == 123.0
    assert world.row("transactions", "t-pay")["notes"] == "still syncs"
    assert open_items() == []
    assert any("tracking" in n["reason"] for n in run["notes"])
    status = actual_app.sync_status()
    assert status["budget_mode"] == "tracking"
    assert any("tracking" in n["reason"] for n in status["notes"])
    with using(ACTUAL):  # said once, not on every run
        runner._locked_run("manual")
        run = import_runs.get(sync_state.load()["last_run"]["run_id"])
    assert not any("tracking" in n["reason"] for n in run["notes"])


def test_tracking_to_envelope_goes_fresh(world):
    enable()
    sync()
    food = envelope_here("Food")["id"]
    world.mode = "tracking"
    sync()
    world.mode = "envelope"
    planning.apply_budget_change(
        {"type": "assign", "month": KEY, "envelope_id": food, "amount": 123.0}
    )
    world.calls.clear()
    sync()
    (cell,) = world.rows("zero_budgets", "category = 'c-food'")
    assert cell["amount"] == 40_000 and writes_seen(world) == []
    assert planning.assignments_store.month_map(KEY)[food] == 123.0
    (item,) = [c for c in open_items() if c["entity"] == "assigned"]
    assert "differs" in item["reason"] and item["fresh"] is True


def test_envelope_to_tracking_says_so(world):
    enable()
    sync()
    world.mode = "tracking"
    with using(ACTUAL):
        runner._locked_run("manual")
        run = import_runs.get(sync_state.load()["last_run"]["run_id"])
    assert [n for n in run["notes"] if "tracking" in n["reason"]]


# ── directions and review ──


def test_one_way_both_directions(world):
    enable("from_ynab")
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    world.calls.clear()
    assert sync()["summary"] == "1 pulled"
    assert writes_seen(world) == []
    assert world.row("transactions", "t-pay")["notes"] is None

    enable("to_ynab")
    world.edit("UPDATE transactions SET notes = 'there too' WHERE id = 't-market'")
    world.queue.clear()
    assert sync()["summary"] == "1 sent to Actual"  # what From held back goes now
    assert world.row("transactions", "t-pay")["notes"] == "here"
    assert row("ACME Corp")["memo"] == "here"
    market = next(
        t for t in ledger_service.transactions.all()
        if any(e["ref"] == "t-market" for e in t.get("external_refs") or [])
    )  # fmt: skip
    assert market["memo"] == "there"


def test_conflict_goes_to_review(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    world.calls.clear()
    sync()
    assert writes_seen(world) == []
    (item,) = open_items()
    assert item["kind"] == "conflict"
    assert world.row("transactions", "t-pay")["notes"] == "there"
    assert row("ACME Corp")["memo"] == "here"
    after = actual_app.resolve_sync_item(item["id"], "ynab")
    assert after["conflicts"] == [] and row("ACME Corp")["memo"] == "there"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_keeping_ours_in_review_writes_actual(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "onw")
    assert world.row("transactions", "t-pay")["notes"] == "here"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_row_edited_mid_run_goes_to_review_and_is_not_written(world, monkeypatch):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    real = writes.ActualClient.save_transactions

    def edited_first(self, method, rows):
        world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
        return real(self, method, rows)

    monkeypatch.setattr(writes.ActualClient, "save_transactions", edited_first)
    run = sync()
    assert run["sent"] == 0
    assert world.row("transactions", "t-pay")["notes"] == "there"
    (item,) = open_items()
    assert item["kind"] == "conflict" and "while the sync was running" in item["reason"]


def test_a_split_that_does_not_add_up_is_filed_not_written(world, monkeypatch):
    enable()
    sync()
    real = push.create_payload

    def broken(ctx, n, currencies):
        return {**real(ctx, n, currencies), "category_id": None, "subtransactions": [
            {"amount": -1_000, "category_id": "c-food", "memo": None}
        ]}  # fmt: skip

    monkeypatch.setattr(push, "create_payload", broken)
    new_row(category=category_here("Food")["id"])
    world.calls.clear()
    assert sync()["sent"] == 0
    assert writes_seen(world) == []
    (item,) = open_items()
    assert item["kind"] == "failed" and "add up" in item["reason"]


# ── failure ──


def test_commit_error_ends_run(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    baseline = ActualBaselineStore().split()[0]
    world.commit_error = (down(), False)
    run = _run()
    assert run["ok"] is False
    assert HOST not in run["error"] and PASSWORD not in run["error"]
    assert ActualBaselineStore().split()[0] == baseline
    assert world.row("transactions", "t-pay")["notes"] is None
    # The next run works it out afresh: the edit is still owed, and goes.
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-pay")["notes"] == "here"


def test_lost_commit_answer_claimed_no_duplicate(world):
    enable()
    sync()
    made = new_row(category=category_here("Food")["id"])
    world.commit_error = (httpx.ReadTimeout(f"{URL} timed out"), True)
    run = _run()
    assert run["ok"] is False and HOST not in run["error"]
    assert len(world.live("amount = -1234")) == 1  # the server took it

    run = sync()
    assert len(world.live("amount = -1234")) == 1
    (booked,) = [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "ACME Coffee" and not t.get("deleted")
    ]
    assert booked["id"] == made["id"]
    (remote,) = world.live("amount = -1234")
    assert {"source": "actual_budget", "ref": remote["id"]} in booked["external_refs"]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_lost_answer_on_a_split_and_a_transfer_is_claimed_too(world):
    enable()
    sync()
    food, fun = category_here("Food")["id"], category_here("Fun")["id"]
    checking = account_here("ACME Checking")["id"]
    new_row(
        description="ACME Shop",
        memo="whole",
        from_amount=30.0,
        splits=[
            {"category": food, "amount": 20.0, "memo": "a"},
            {"category": fun, "amount": 10.0, "memo": "b"},
        ],
    )
    new_row(
        kind="transfer",
        description="",
        memo="moved",
        from_account_id=checking,
        from_amount=40.0,
        to_account_id=account_here("ACME Card")["id"],
        to_amount=40.0,
        to_currency="USD",
    )
    world.commit_error = (httpx.ReadTimeout("timed out"), True)
    assert _run()["ok"] is False
    assert sync()["sent"] == 0  # both were taken: nothing goes a second time
    assert len(world.live("notes = 'whole' AND isParent = 1")) == 1
    assert len(world.live("notes = 'moved'")) == 2
    here = [t for t in ledger_service.transactions.all() if t.get("memo") in ("whole", "moved")]
    assert len([t for t in here if not t.get("deleted")]) == 2
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_lost_answer_on_a_new_category_pairs_it_with_no_second_one(world):
    enable()
    sync()
    made = planning.apply_budget_change(
        {"type": "category_create", "label": "Pets", "kind": "expense"}
    )
    planning.apply_budget_change(
        {
            "type": "envelope_add_category",
            "envelope_id": envelope_here("Food")["id"],
            "category_id": made["id"],
        }
    )
    world.commit_error = (httpx.ReadTimeout("timed out"), True)
    assert _run()["ok"] is False
    sync()
    assert len(world.rows("categories", "name = 'Pets'")) == 1


def test_deleted_row_not_recreated(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-pay'")
    world.queue.clear()
    assert sync()["summary"] == "1 pulled"
    assert not [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "ACME Corp" and not t.get("deleted")
    ]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    assert world.row("transactions", "t-pay")["tombstone"] == 1


def test_deleted_here_is_deleted_there_and_not_pulled_back(world):
    enable()
    sync()
    as_person(ledger_service.bulk_delete_transactions, [row("ACME Corp")["id"]])
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-pay")["tombstone"] == 1
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_row_already_gone_from_actual_counts_as_deleted(world, monkeypatch):
    enable()
    sync()
    as_person(ledger_service.bulk_delete_transactions, [row("ACME Corp")["id"]])
    real = writes.ActualClient.delete_transaction

    def gone_first(self, txn_id):
        world.edit("UPDATE transactions SET tombstone = 1 WHERE id = ?", txn_id)
        return real(self, txn_id)

    monkeypatch.setattr(writes.ActualClient, "delete_transaction", gone_first)
    run = sync()
    assert run["sent"] == 1 and open_items() == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(lambda server: setattr(server, "sync_error", down()), id="down-mid-run"),
        pytest.param(
            lambda server: setattr(
                server,
                "sync_error",
                httpx.HTTPStatusError(
                    f"{URL} refused {PASSWORD}",
                    request=httpx.Request("POST", URL),
                    response=httpx.Response(401, request=httpx.Request("POST", URL)),
                ),
            ),
            id="wrong-password-mid-run",
        ),
        pytest.param(
            lambda server: setattr(
                server,
                "open_error",
                ExternalServiceError("That budget is no longer on the Actual server."),
            ),
            id="budget-gone-at-open",
        ),
    ],
)
def test_server_down_leaves_baselines(world, failure):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    baselines = ActualBaselineStore().all()
    mirror = ActualMirrorStore().all()
    failure(world)
    run = _run()
    assert run["ok"] is False
    assert HOST not in run["error"] and PASSWORD not in run["error"]
    assert ActualBaselineStore().all() == baselines
    assert ActualMirrorStore().all() == mirror


# ── a failed run loses nothing ──


def by_ref(ref: str) -> dict:
    return next(
        t
        for t in ledger_service.transactions.all()
        if any(e["ref"] == ref for e in t.get("external_refs") or [])
    )


def test_to_actual_a_failed_run_keeps_an_edit_made_there(world):
    enable("to_ynab")
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.commit_error = (down(), False)
    assert _run()["ok"] is False
    assert "t-market" in sync_state_of()["held_ynab"]
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-pay")["notes"] == "here"
    assert by_ref("t-market").get("memo") != "there"  # To Actual doesn't change the book
    enable("both")
    assert sync()["summary"] == "1 pulled"
    assert by_ref("t-market")["memo"] == "there"
    assert open_items() == []


@pytest.mark.parametrize("broken", ["pull", "build"])
def test_an_unexpected_error_mid_run_loses_no_pull(world, monkeypatch, broken):
    enable()
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    target, name = (pull, "apply") if broken == "pull" else (units, "build")
    real = getattr(target, name)

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(target, name, boom)
    assert _run()["ok"] is False
    monkeypatch.setattr(target, name, real)
    assert sync()["summary"] == "1 pulled"
    assert by_ref("t-market")["memo"] == "there"
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_ledger_refusal_recording_an_agreed_row_loses_no_pull(world, monkeypatch):
    """A claim's link is written while recording what both sides agree on, outside any catch."""
    enable()
    sync()
    made = new_row(description="ACME Lost", from_amount=7.77, category=category_here("Food")["id"])
    world.commit_error = (httpx.ReadTimeout("timed out"), True)
    assert _run()["ok"] is False
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()  # read only by the run that is refused
    real = ledger_service.update_transaction

    def refuse(txn_id, changes, **kw):
        if "external_refs" in changes and txn_id == made["id"]:
            raise ValidationError("the ledger refused the link")
        return real(txn_id, changes, **kw)

    monkeypatch.setattr(ledger_service, "update_transaction", refuse)
    assert _run()["ok"] is False
    assert "t-market" in sync_state_of()["held_ynab"]
    monkeypatch.setattr(ledger_service, "update_transaction", real)
    sync()
    assert by_ref("t-market")["memo"] == "there"
    assert len(world.live("amount = -777")) == 1 and open_items() == []


def test_held_ids_are_on_disk_before_the_mirror_moves(world, monkeypatch):
    enable("to_ynab")
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    seen: list[list[str]] = []
    real = delta.save_changes

    def spy(*a, **k):
        seen.append(ActualSyncStateStore().doc("state").get("held_ynab"))
        return real(*a, **k)

    monkeypatch.setattr(delta, "save_changes", spy)
    sync()
    assert seen == [["t-market"]]


@pytest.mark.parametrize("direction", ["both", "to_ynab"])
def test_a_row_deleted_in_actual_during_a_failed_run_is_still_deleted_here(
    world, monkeypatch, direction
):
    enable(direction)
    sync()
    market = by_ref("t-market")["id"]
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-market'")
    world.queue.clear()
    if direction == "both":
        real = pull.apply

        def boom(*_a, **_k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(pull, "apply", boom)
        assert _run()["ok"] is False
        monkeypatch.setattr(pull, "apply", real)
    else:
        edit_here(row("ACME Corp")["id"], memo="here")
        world.commit_error = (down(), False)
        assert _run()["ok"] is False
        sync()
        enable("both")
    sync()
    assert ledger_service.transactions.find_by_id(market).get("deleted")
    assert open_items() == []


def test_a_run_that_fails_then_succeeds_leaves_both_sides_equal(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.txn(
        "t-new", "a1", -700, TODAY, description="p-market", category="c-fun", notes="brand new"
    )
    world.queue.clear()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.commit_error = (down(), False)
    assert _run()["ok"] is False
    run = sync()
    assert run["ok"] is True and open_items() == []
    assert world.row("transactions", "t-pay")["notes"] == "here"
    assert by_ref("t-market")["memo"] == "there"
    assert by_ref("t-new")["memo"] == "brand new"
    assert len([t for t in ledger_service.transactions.all() if t.get("memo") == "brand new"]) == 1
    assert sync_state_of()["held_ynab"] == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_replaying_settled_rows_changes_nothing(world):
    enable()
    sync()
    with using(ACTUAL):
        st = sync_state.load()
        st["held_ynab"] = sorted(ActualMirrorStore().by_id())
        sync_state.save(st)
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(world) == [] and open_items() == []


# ── what Actual does to text ──


@pytest.mark.parametrize(
    "description",
    ["corner market", "ACME " + "x" * 250],
    ids=["a payee in another case", "a payee past the request's length"],
)
def test_a_lost_answer_is_claimed_when_actual_normalised_the_row(world, description):
    enable()
    sync()
    made = new_row(description=description, from_amount=7.77, category=category_here("Food")["id"])
    world.commit_error = (httpx.ReadTimeout("timed out"), True)
    assert _run()["ok"] is False
    sync()
    assert len(world.live("amount = -777")) == 1
    (booked,) = [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and abs((t.get("from_amount") or 0) - 7.77) < 0.001
    ]
    assert booked["id"] == made["id"] and open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


@pytest.mark.parametrize(
    "description", ["corner market", "ACME " + "x" * 250], ids=["case", "length"]
)
def test_a_later_edit_of_a_normalised_row_is_no_false_conflict(world, description):
    enable()
    sync()
    made = new_row(description=description, from_amount=7.77, category=category_here("Food")["id"])
    assert sync()["summary"] == "1 sent to Actual"
    edit_here(made["id"], memo="later")
    assert sync()["summary"] == "1 sent to Actual"
    assert open_items() == []
    (remote,) = world.live("amount = -777")
    assert remote["notes"] == "later"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_real_rename_in_actual_still_comes_across(world):
    enable()
    sync()
    world.edit("UPDATE payees SET name = 'Corner Shop' WHERE id = 'p-market'")
    world.queue.clear()
    sync()
    assert by_ref("t-market")["description"] == "Corner Shop"


# ── a category cleared in Actual ──


def test_a_category_cleared_in_actual_stays_cleared_here(world):
    enable()
    sync()
    market = by_ref("t-market")
    food = category_here("Food")["id"]
    PayeeStore().set_default_category_for_kind(market["payee_id"], "expense", food)
    world.edit("UPDATE transactions SET category = NULL WHERE id = 't-market'")
    world.queue.clear()
    assert sync()["summary"] == "1 pulled"
    assert by_ref("t-market").get("category") is None
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    edit_here(by_ref("t-market")["id"], memo="note here")
    assert open_items() == []
    # The edit here runs the normal path, where a payee default may fill a blank: that is the
    # person's own edit, and it reaches Actual as one.
    sync()
    assert open_items() == []
    assert world.row("transactions", "t-market")["notes"] == "note here"


def test_every_other_caller_of_the_ledger_choke_still_fills_a_blank(world):
    market = by_ref("t-market")
    food = category_here("Food")["id"]
    PayeeStore().set_default_category_for_kind(market["payee_id"], "expense", food)
    updated = as_person(ledger_service.update_transaction, market["id"], {"category": None})
    assert updated["category"] == food
    kept = as_person(
        ledger_service.update_transaction, market["id"], {"category": None}, labels_as_given=True
    )
    assert kept.get("category") is None


# ── a process killed mid-run ──


def _killed_run(monkeypatch) -> None:
    """What's on disk when the process dies with the run's work done and nothing saved at its end."""
    with monkeypatch.context() as killed:
        killed.setattr(baselines.Baselines, "flush", lambda self: None)
        killed.setattr(delta, "save_changes", lambda *a, **k: None)
        killed.setattr(sync_state, "save", lambda st: None)
        killed.setattr(sync_state, "save_meta", lambda meta: None)
        with using(ACTUAL):
            runner._locked_run("manual")


def test_a_kill_after_a_create_is_linked_is_not_stuck(world, monkeypatch):
    enable()
    sync()
    made = new_row(category=category_here("Food")["id"])
    _killed_run(monkeypatch)
    assert made["id"] not in ActualBaselineStore().split()[0]
    assert ledger_service.transactions.find_by_id(made["id"])["external_refs"]
    run = sync()
    assert run["ok"] is True and open_items() == []
    assert made["id"] in ActualBaselineStore().split()[0]
    assert len(world.live("amount = -1234")) == 1
    edit_here(made["id"], memo="later")
    assert sync()["summary"] == "1 sent to Actual"
    (remote,) = world.live("amount = -1234")
    assert remote["notes"] == "later"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def _killed_create(monkeypatch) -> dict:
    enable()
    sync()
    made = new_row(category=category_here("Food")["id"])
    _killed_run(monkeypatch)
    return made


def _killed_pull(world, monkeypatch) -> dict:
    enable()
    sync()
    world.txn(
        "t-new", "a1", -700, TODAY, description="p-market", category="c-fun", notes="brand new"
    )
    world.queue.clear()
    _killed_run(monkeypatch)
    return by_ref("t-new")


@pytest.mark.parametrize("keep, memo", [("onw", "edited here"), ("ynab", "")])
def test_a_conflict_on_a_killed_create_can_be_answered_either_way(world, monkeypatch, keep, memo):
    made = _killed_create(monkeypatch)
    edit_here(made["id"], memo="edited here")
    sync()
    (item,) = open_items()
    assert "both" in item["reason"]
    actual_app.resolve_sync_item(item["id"], keep)
    assert open_items() == []
    (remote,) = world.live("amount = -1234")
    assert (remote["notes"] or "") == memo
    assert (ledger_service.transactions.find_by_id(made["id"]).get("memo") or "") == memo
    assert made["id"] in ActualBaselineStore().split()[0]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


@pytest.mark.parametrize("keep, memo", [("onw", "edited here"), ("ynab", "edited there")])
def test_a_conflict_on_a_killed_pull_can_be_answered_either_way(world, monkeypatch, keep, memo):
    made = _killed_pull(world, monkeypatch)
    world.edit("UPDATE transactions SET notes = 'edited there' WHERE id = 't-new'")
    world.queue.clear()
    edit_here(made["id"], memo="edited here")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], keep)
    assert open_items() == []
    assert world.row("transactions", "t-new")["notes"] == memo
    assert by_ref("t-new")["memo"] == memo
    assert made["id"] in ActualBaselineStore().split()[0]
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def _reads_as_missing(item: dict) -> None:
    assert item["kind"] == "conflict"
    assert "no longer in Actual" in item["reason"] and "larger" not in item["reason"]


@pytest.mark.parametrize("keep", ["onw", "ynab"])
def test_a_killed_create_then_deleted_in_actual_asks_which_side(world, monkeypatch, keep):
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = ?", remote["id"])
    world.queue.clear()
    world.calls.clear()
    run = sync()
    assert run["sent"] == 0 and writes_seen(world) == []  # not recreated
    assert not world.live("amount = -1234")
    assert not ledger_service.transactions.find_by_id(made["id"]).get("deleted")  # not deleted
    (item,) = open_items()
    _reads_as_missing(item)
    sync()  # it waits, and isn't filed again
    assert [i["id"] for i in open_items()] == [item["id"]]
    actual_app.resolve_sync_item(item["id"], keep)
    assert open_items() == []
    if keep == "onw":
        (again,) = world.live("amount = -1234")
        assert again["id"] != remote["id"]
        assert made["id"] in ActualBaselineStore().split()[0]
        refs = ledger_service.transactions.find_by_id(made["id"])["external_refs"]
        assert [e["ref"] for e in refs] == [again["id"]]
    else:
        assert not world.live("amount = -1234")
        assert ledger_service.transactions.find_by_id(made["id"]) is None or (
            ledger_service.transactions.find_by_id(made["id"]).get("deleted")
        )
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    assert open_items() == []


@pytest.mark.parametrize("keep", ["onw", "ynab"])
def test_a_killed_pull_then_deleted_in_actual_asks_which_side(world, monkeypatch, keep):
    made = _killed_pull(world, monkeypatch)
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-new'")
    world.queue.clear()
    assert sync()["sent"] == 0
    (item,) = open_items()
    _reads_as_missing(item)
    assert not ledger_service.transactions.find_by_id(made["id"]).get("deleted")
    actual_app.resolve_sync_item(item["id"], keep)
    assert open_items() == []
    live_new = world.live("notes = 'brand new'")
    gone_here = ledger_service.transactions.find_by_id(made["id"])
    if keep == "onw":
        assert len(live_new) == 1 and live_new[0]["id"] != "t-new"
        assert not gone_here.get("deleted")
    else:
        assert live_new == [] and (gone_here is None or gone_here.get("deleted"))
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


@pytest.mark.parametrize("keep", ["onw", "ynab"])
def test_a_row_with_no_baseline_that_the_read_says_was_deleted_asks_which_side(world, keep):
    """The mirror held it and the read reports it gone. It used to read as part of a larger split."""
    enable()
    sync()
    market = by_ref("t-market")
    ActualBaselineStore().delete("t:" + market["id"])
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-market'")
    world.queue.clear()
    assert _run()["ok"] is True
    (item,) = open_items()
    _reads_as_missing(item)
    actual_app.resolve_sync_item(item["id"], keep)
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_row_that_comes_back_before_the_answer_closes_the_question(world, monkeypatch):
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = ?", remote["id"])
    world.queue.clear()
    sync()
    assert len(open_items()) == 1
    world.edit("UPDATE transactions SET tombstone = 0 WHERE id = ?", remote["id"])
    world.queue.clear()
    sync()
    assert open_items() == [] and made["id"] in ActualBaselineStore().split()[0]


def test_one_way_a_kept_side_waits_for_both_ways(world, monkeypatch):
    # From Actual: keeping ours can't write to Actual yet.
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = ?", remote["id"])
    world.queue.clear()
    enable("from_ynab")
    sync()
    (item,) = open_items()
    world.calls.clear()
    actual_app.resolve_sync_item(item["id"], "onw")
    assert writes_seen(world) == [] and not world.live("amount = -1234")
    assert sync()["sent"] == 0 and open_items() == []
    enable("both")
    assert sync()["summary"] == "1 sent to Actual"
    assert len(world.live("amount = -1234")) == 1 and open_items() == []
    assert not ledger_service.transactions.find_by_id(made["id"]).get("deleted")


def test_one_way_keeping_actuals_side_waits_for_both_ways(world, monkeypatch):
    # To Actual: keeping Actual's can't change the book yet.
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = ?", remote["id"])
    world.queue.clear()
    enable("to_ynab")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "ynab")
    assert not ledger_service.transactions.find_by_id(made["id"]).get("deleted")
    sync()
    assert open_items() == [] and not world.live("amount = -1234")
    assert not ledger_service.transactions.find_by_id(made["id"]).get("deleted")
    enable("both")
    sync()
    found = ledger_service.transactions.find_by_id(made["id"])
    assert (found is None or found.get("deleted")) and open_items() == []
    assert not world.live("amount = -1234")


def _gone_there(world, ref: str, gone: int = 1) -> None:
    world.edit("UPDATE transactions SET tombstone = ? WHERE id = ?", gone, ref)
    world.queue.clear()


def test_from_actual_keeping_ours_on_a_row_gone_there_is_not_asked_again(world):
    enable("from_ynab")
    sync()
    market = by_ref("t-market")
    edit_here(market["id"], memo="mine")
    _gone_there(world, "t-market")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "onw")
    assert sync()["sent"] == 0 and open_items() == []
    assert actual_app.sync_status()["waiting"] == 1
    enable("both")
    assert sync()["summary"] == "1 sent to Actual"
    assert len(world.live("notes = 'mine'")) == 1 and open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_from_actual_a_mass_hold_is_answered_once_per_row(world):
    enable("from_ynab")
    sync()
    market = by_ref("t-market")
    world.edit("UPDATE transactions SET tombstone = 1")
    world.queue.clear()
    sync()
    item = next(c for c in open_items() if c.get("onw_txn_id") == market["id"])
    actual_app.resolve_sync_item(item["id"], "onw")
    sync()
    assert not [c for c in open_items() if c.get("onw_txn_id") == market["id"]]


def test_to_actual_keeping_actuals_side_of_an_edited_row_is_not_lost(world):
    enable("to_ynab")
    sync()
    market = by_ref("t-market")
    edit_here(market["id"], memo="mine")
    _gone_there(world, "t-market")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "ynab")
    sync()
    assert open_items() == []
    assert not ledger_service.transactions.find_by_id(market["id"]).get("deleted")
    enable("both")
    sync()
    found = ledger_service.transactions.find_by_id(market["id"])
    assert found is None or found.get("deleted")
    assert open_items() == [] and world.row("transactions", "t-market")["tombstone"] == 1


def test_a_waiting_answer_survives_an_edit_and_a_second_answer(world, monkeypatch):
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    _gone_there(world, remote["id"])
    enable("to_ynab")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "ynab")
    edit_here(made["id"], memo="edited while waiting")
    sync()
    (item,) = open_items()
    actual_app.resolve_sync_item(item["id"], "ynab")
    assert open_items() == []
    sync()
    enable("both")
    sync()
    found = ledger_service.transactions.find_by_id(made["id"])
    assert (found is None or found.get("deleted")) and open_items() == []
    assert not world.live("amount = -1234")


@pytest.mark.parametrize(
    "direction, promise, waits",
    [
        ("both", "to remove it here.", None),
        ("from_ynab", "when the sync goes both ways", "onw"),
        ("to_ynab", "until the sync goes both ways", "ynab"),
    ],
)
def test_the_question_and_the_status_say_what_an_answer_does(
    world, monkeypatch, direction, promise, waits
):
    _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    _gone_there(world, remote["id"])
    enable(direction)
    sync()
    (item,) = open_items()
    assert promise in item["reason"] and "no longer in Actual" in item["reason"]
    if waits is None:
        assert "both ways" not in item["reason"]
        return
    after = actual_app.resolve_sync_item(item["id"], waits)
    assert after["waiting"] == 1
    (warning,) = [w for w in after["warnings"] if w["kind"] == "waiting_for_both_ways"]
    assert "both ways" in warning["text"]
    if direction == "to_ynab":
        assert "stays here" in warning["text"] and "net worth" in warning["text"]


def test_the_question_follows_a_change_of_direction(world, monkeypatch):
    _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    _gone_there(world, remote["id"])
    enable("to_ynab")
    sync()
    assert "until the sync goes both ways" in open_items()[0]["reason"]
    enable("both")
    sync()
    (item,) = open_items()
    assert "both ways" not in item["reason"]


def test_a_both_ways_sync_has_nothing_waiting(world):
    enable()
    sync()
    status = actual_app.sync_status()
    assert status["waiting"] == 0 and status["warnings"] == []


def test_a_gone_row_that_returns_changed_is_asked_afresh(world, monkeypatch):
    made = _killed_create(monkeypatch)
    (remote,) = world.live("amount = -1234")
    _gone_there(world, remote["id"])
    sync()
    assert "no longer" in open_items()[0]["reason"]
    world.edit(
        "UPDATE transactions SET tombstone = 0, notes = 'changed there' WHERE id = ?", remote["id"]
    )
    world.queue.clear()
    sync()
    (item,) = open_items()
    assert "no longer" not in item["reason"] and item["ynab"]["memo"] == "changed there"
    actual_app.resolve_sync_item(item["id"], "ynab")
    found = ledger_service.transactions.find_by_id(made["id"])
    assert not found.get("deleted") and found["memo"] == "changed there"  # a version, no deletion
    assert open_items() == []


def test_a_conflict_whose_row_then_goes_is_asked_afresh(world, monkeypatch):
    made = _killed_create(monkeypatch)
    edit_here(made["id"], memo="edited here")
    sync()
    (item,) = open_items()
    assert "both" in item["reason"] and item["ynab"] is not None
    (remote,) = world.live("amount = -1234")
    _gone_there(world, remote["id"])
    sync()
    (item,) = open_items()
    assert "no longer in Actual" in item["reason"] and item["ynab"] is None
    actual_app.resolve_sync_item(item["id"], "onw")
    assert len(world.live("notes = 'edited here'")) == 1 and open_items() == []


def test_a_kill_after_a_pull_is_not_stuck(world, monkeypatch):
    enable()
    sync()
    world.txn(
        "t-new", "a1", -700, TODAY, description="p-market", category="c-fun", notes="brand new"
    )
    world.queue.clear()
    _killed_run(monkeypatch)
    assert "t-new" in by_ref("t-new")["external_refs"][0]["ref"]
    run = sync()
    assert run["ok"] is True and open_items() == []
    assert len([t for t in ledger_service.transactions.all() if t.get("memo") == "brand new"]) == 1
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


# ── an empty read ──


def test_an_empty_copy_of_a_small_book_deletes_nothing(world):
    enable()
    sync()
    rows_here = len([t for t in ledger_service.transactions.all() if not t.get("deleted")])
    world.edit("UPDATE transactions SET tombstone = 1")
    world.queue.clear()
    sync()
    assert any("at once" in c["reason"] for c in open_items())
    assert len([t for t in ledger_service.transactions.all() if not t.get("deleted")]) == rows_here
    world.edit("UPDATE transactions SET tombstone = 0")
    world.queue.clear()
    assert sync()["ok"] is True
    assert open_items() == []  # back as it was: nothing's left to ask
    assert len([t for t in ledger_service.transactions.all() if not t.get("deleted")]) == rows_here
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_an_empty_read_holds_a_row_differing_only_cosmetically(world):
    enable()
    sync()
    market = by_ref("t-market")
    edit_here(market["id"], description=market["description"].upper())
    world.edit("UPDATE transactions SET tombstone = 1")
    world.queue.clear()
    run = sync()
    assert run["pulled"] == 0
    assert not ledger_service.transactions.find_by_id(market["id"]).get("deleted")
    assert any(c.get("onw_txn_id") == market["id"] for c in open_items())
    world.edit("UPDATE transactions SET tombstone = 0")
    world.queue.clear()
    sync()
    assert open_items() == []
    assert not ledger_service.transactions.find_by_id(market["id"]).get("deleted")
    assert world.row("transactions", "t-market")["tombstone"] == 0
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_the_mass_delete_guard_counts_a_row_differing_only_cosmetically(world):
    enable()
    sync()
    for n in range(30):
        world.txn(f"t-more-{n}", "a1", -100 - n, TODAY, description="p-market", category="c-food")
    world.queue.clear()
    sync()
    market = by_ref("t-market")
    edit_here(market["id"], description=market["description"].upper())
    # Most of the rows go, so the read isn't empty and only the threshold can hold them.
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id LIKE 't-more-%' OR id = 't-market'")
    world.queue.clear()
    run = sync()
    assert run["pulled"] == 0
    assert not ledger_service.transactions.find_by_id(market["id"]).get("deleted")
    assert any(
        c.get("onw_txn_id") == market["id"] and "at once" in c["reason"] for c in open_items()
    )


def test_missing_rows_dont_raise_the_mass_delete_limit(world, monkeypatch):
    enable()
    for n in range(30):
        world.txn(f"t-more-{n}", "a1", -100 - n, TODAY, description="p-market", category="c-food")
    world.queue.clear()
    sync()
    # A hundred linked rows with no baseline, gone from Actual.
    with monkeypatch.context() as killed:
        killed.setattr(baselines.Baselines, "flush", lambda self: None)
        killed.setattr(delta, "save_changes", lambda *a, **k: None)
        killed.setattr(sync_state, "save", lambda st: None)
        killed.setattr(sync_state, "save_meta", lambda meta: None)
        for n in range(100):
            new_row(description=f"ACME Loose {n}", from_amount=1000.0 + n, date=PAST)
        with using(ACTUAL):
            runner._locked_run("manual")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE amount <= -100000")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id LIKE 't-more-%'")
    world.queue.clear()
    assert sync()["pulled"] == 0
    held = [c for c in open_items() if "at once" in c["reason"]]
    assert len(held) == 30
    assert len(world.rows("transactions", "tombstone = 1 AND id LIKE 't-more-%'")) == 30


def test_a_real_deletion_of_a_few_rows_needs_no_confirming(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-pay'")
    world.queue.clear()
    assert sync()["summary"] == "1 pulled" and open_items() == []


def test_a_commit_error_keeps_what_earlier_batches_confirmed(world, monkeypatch):
    enable()
    sync()
    monkeypatch.setattr(push, "BATCH", 1)
    edit_here(row("ACME Corp")["id"], memo="first")
    second = next(
        t for t in ledger_service.transactions.all()
        if any(e["ref"] == "t-market" for e in t.get("external_refs") or [])
    )  # fmt: skip
    edit_here(second["id"], memo="second")
    calls = {"n": 0}
    real = writes.ActualClient.save_transactions

    def fail_on_second(self, method, rows):
        calls["n"] += 1
        if calls["n"] == 2:
            world.commit_error = (down(), False)
        return real(self, method, rows)

    monkeypatch.setattr(writes.ActualClient, "save_transactions", fail_on_second)
    assert _run()["ok"] is False
    monkeypatch.setattr(writes.ActualClient, "save_transactions", real)
    agreed = [
        d
        for d in ActualBaselineStore().split()[0].values()
        if d["canon"]["memo"] in ("first", "second")
    ]
    assert len(agreed) == 1
    world.calls.clear()
    assert sync()["sent"] == 1
    assert {world.row("transactions", i)["notes"] for i in ("t-pay", "t-market")} == {
        "first",
        "second",
    }


# ── identity ──


def test_sync_reset_keeps_pairing_no_mass_delete(world):
    enable()
    sync()
    baselines = ActualBaselineStore().all()
    mirror = ActualMirrorStore().all()
    before = world.group_id
    world.reset_sync()
    assert world.group_id != before
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(world) == [] and open_items() == []
    assert ActualBaselineStore().all() == baselines and ActualMirrorStore().all() == mirror
    assert sync_state_of()["plan_id"] == BUDGET
    edit_here(row("ACME Corp")["id"], memo="after the reset")
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-pay")["notes"] == "after the reset"


def test_a_reset_during_a_run_stops_it_and_the_next_run_carries_on(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    baselines = ActualBaselineStore().all()
    real = writes.ActualClient.save_transactions

    def reset_first(self, method, rows):
        world.reset_sync()
        return real(self, method, rows)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(writes.ActualClient, "save_transactions", reset_first)
    try:
        run = _run()
    finally:
        monkeypatch.undo()
    assert run["ok"] is False and ActualBaselineStore().all() == baselines
    assert sync()["summary"] == "1 sent to Actual"
    assert world.row("transactions", "t-pay")["notes"] == "here"


def sync_state_of() -> dict:
    with using(ACTUAL):
        return sync_state.load()


def test_a_whole_budget_vanishing_reads_as_a_held_mass_delete(world):
    """What a broken read would look like: nothing is deleted without being confirmed."""
    enable()
    sync()
    world.mode = "envelope"
    # 26 more rows, then every one of them gone at once.
    for n in range(30):
        world.txn(f"t-more-{n}", "a1", -100 - n, TODAY, description="p-market", category="c-food")
    world.queue.clear()
    sync()
    world.edit("UPDATE transactions SET tombstone = 1")
    world.queue.clear()
    sync()
    assert any("at once" in c["reason"] for c in open_items())
    assert [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "Corner Market" and not t.get("deleted")
    ]


# ── another budget ──


def _live_here() -> int:
    return len([t for t in ledger_service.transactions.all() if not t.get("deleted")])


def _to_other_budget(direction: str = "both") -> None:
    return actual_app.sync_settings(True, "1h", BUDGET_B, direction)


@pytest.mark.parametrize("extra", [0, 40], ids=["under the mass-delete limit", "over it"])
def test_switching_budgets_deletes_nothing_here(world, extra):
    enable()
    for n in range(extra):
        world.txn(f"t-more-{n}", "a1", -100 - n, TODAY, description="p-market", category="c-food")
    world.queue.clear()
    sync()
    rows_here = _live_here()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    a_before = world.rows("transactions")
    _to_other_budget()
    assert sync()["ok"] is True
    assert _live_here() == rows_here  # not one row went
    assert world.rows("transactions") == a_before  # and the old budget wasn't written
    assert all(not c.get("onw_txn_id") for c in open_items())  # no item per old row
    assert sync()["ok"] is True
    assert _live_here() == rows_here
    assert all(not c.get("onw_txn_id") for c in open_items())
    (warning,) = [
        w for w in actual_app.sync_status()["warnings"] if w["kind"] == "not_in_this_budget"
    ]
    assert str(rows_here) in warning["text"]
    assert f"ACME {BUDGET_B}" in warning["text"] and "stay here" in warning["text"]


def _import_other_budget() -> None:
    actual_app.start_migration(
        "import", BUDGET_B, account_types={"b1": "cash"}, confirm_nonempty=True
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        record = import_runs.latest("actual_budget", job.KINDS)
        if record["status"] != "running":
            assert record["status"] == "done", record["error"]
            return
        time.sleep(0.02)
    raise AssertionError("the import did not finish")


def test_moving_to_an_imported_budget_and_back(world):
    enable()
    sync()
    rows_a = _live_here()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    actual_app.sync_settings(False, None, None, None)
    _import_other_budget()
    rows_both = _live_here()
    assert rows_both > rows_a
    a_before = world.rows("transactions")
    _to_other_budget()
    assert sync()["ok"] is True
    assert _live_here() == rows_both and open_items() == []
    assert world.rows("transactions") == a_before
    with world.on(BUDGET_B):  # the first budget's categories and groups stay out of this one
        assert {r["name"] for r in world.rows("categories")} == {"Zenith Food", "Starting Balances"}
        assert {r["name"] for r in world.rows("category_groups")} == {"Zenith Group", "Income"}
        cells = world.rows("zero_budgets")
    assert cells == []

    # This budget's rows sync; the other's are left alone.
    edit_here(by_ref("z-1")["id"], memo="zen edited")
    edit_here(by_ref("t-pay")["id"], memo="old budget, edited here")
    assert sync()["sent"] == 1
    with world.on(BUDGET_B):
        assert world.row("transactions", "z-1")["notes"] == "zen edited"
    assert world.rows("transactions") == a_before
    assert open_items() == []

    # And back: the other budget's rows stay, and this one's are linked again.
    actual_app.sync_settings(True, "1h", BUDGET, "both")
    assert sync()["ok"] is True
    assert _live_here() == rows_both
    assert by_ref("z-1")["memo"] == "zen edited"
    assert all(not c.get("onw_txn_id") or "no longer" not in c["reason"] for c in open_items())
    assert world.row("transactions", "t-pay")["notes"] == "old budget, edited here"


def test_two_budgets_cannot_share_an_account_here(world):
    """Why a row's account can say which budget it's in: the import refuses the link."""
    enable()
    sync()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    actual_app.sync_settings(False, None, None, None)
    actual_app.start_migration(
        "import",
        BUDGET_B,
        account_links={"b1": account_here("ACME Checking")["id"]},
        confirm_nonempty=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        record = import_runs.latest("actual_budget", job.KINDS)
        if record["status"] != "running":
            break
        time.sleep(0.02)
    assert record["status"] == "failed" and "cannot be linked" in record["error"]


def test_a_first_read_that_comes_back_empty_deletes_nothing(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    _import()
    rows_here = _live_here()
    enable()
    server.edit("UPDATE transactions SET tombstone = 1")
    server.queue.clear()
    assert sync()["ok"] is True
    assert _live_here() == rows_here
    server.edit("UPDATE transactions SET tombstone = 0")
    server.queue.clear()
    assert sync()["ok"] is True
    assert _live_here() == rows_here and open_items() == []
    server.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    server.queue.clear()
    assert sync()["summary"] == "1 pulled"
    assert by_ref("t-pay")["memo"] == "there"


def test_an_old_budgets_account_appears_nowhere_in_what_is_sent(world):
    enable()
    sync()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    _to_other_budget()
    sync()
    new_row()  # on the old budget's account, with no link at all
    assert sync()["sent"] == 0 and all(not c.get("onw_txn_id") for c in open_items())
    with world.on(BUDGET_B):
        assert not world.live("amount = -1234")
    assert not world.live("amount = -1234")


def test_importing_the_budget_switched_to_closes_its_rows_items(world):
    enable()
    sync()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    _to_other_budget()
    sync()
    assert [c for c in open_items() if c["key"].startswith("y:")]  # no account here for its rows
    actual_app.sync_settings(False, None, None, None)
    _import_other_budget()
    actual_app.sync_settings(True, None, None, None)
    sync()
    assert open_items() == []


def test_a_row_given_another_budgets_category_goes_uncategorized_and_says_so(world):
    enable()
    sync()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    actual_app.sync_settings(False, None, None, None)
    _import_other_budget()
    _to_other_budget()
    sync()
    edit_here(by_ref("z-1")["id"], category=category_here("Food")["id"])
    with using(ACTUAL):
        runner._locked_run("manual")
        st = sync_state.load()
        run = import_runs.get(st["last_run"]["run_id"])
    assert st["last_run"]["sent"] == 1 and st["last_run"]["deferred"] == 0
    with world.on(BUDGET_B):
        assert world.row("transactions", "z-1")["category"] is None
    assert any("Food" in n["item"] for n in run["notes"])
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    assert by_ref("z-1")["category"] == category_here("Food")["id"]


def test_importing_another_budget_is_refused_while_the_sync_is_on(world):
    enable()
    world.add_budget(BUDGET_B)
    with world.on(BUDGET_B):
        seed_other_budget(world, PAST, TODAY)
    rows_here = _live_here()
    with pytest.raises(ConflictError) as refused:
        actual_app.start_migration(
            "import", BUDGET_B, account_types={"b1": "cash"}, confirm_nonempty=True
        )
    assert "Turn it off" in refused.value.message and "Actual" in refused.value.message
    assert _live_here() == rows_here
    actual_app.start_migration("preview", BUDGET_B)  # looking is still fine
    deadline = time.time() + 30
    while time.time() < deadline:
        if import_runs.latest("actual_budget", job.KINDS)["status"] != "running":
            break
        time.sleep(0.02)
    _import()  # and so is importing the one being synced, again


# ── one app at a time ──


def _ynab_on(**over) -> None:
    YnabSyncStateStore().save(
        {
            **sync_state.blank(),
            "plan_id": "plan-y",
            "plan_name": "ACME",
            "currency": "USD",
            "enabled": True,
            **over,
        }
    )


def test_enable_refused_while_other_on(world):
    # Actual, with YNAB's sync on.
    _ynab_on()
    with pytest.raises(ConflictError) as refused:
        enable()
    assert "YNAB sync is on" in refused.value.message
    assert sync_state_of()["enabled"] is False
    YnabSyncStateStore().replace_all([])
    # YNAB, with Actual's sync on.
    enable()
    YnabConfigStore().update({"access_token": "stub-token"})
    YnabSyncStateStore().save({**sync_state.blank(), "plan_id": "plan-y", "currency": "USD"})
    with pytest.raises(ConflictError) as refused:
        ynab_sync.update_settings(True, None, None)
    assert "Actual sync is on" in refused.value.message
    assert sync_state.load()["enabled"] is False


def test_refused_before_the_other_apps_state_is_forgotten(world):
    enable()
    sync()
    _ynab_on()
    with using(ACTUAL):
        st = sync_state.load()
        st["enabled"] = False
        sync_state.save(st)
    with pytest.raises(ConflictError):
        actual_app.sync_settings(True, None, "another-budget", None)
    assert sync_state_of()["bootstrapped"] is True  # a refused switch reset nothing


def test_enable_refused_while_other_running(world):
    run_id = import_runs.start("ynab", "import", "plan-y")["id"]
    with pytest.raises(ConflictError) as refused:
        enable()
    assert "YNAB" in refused.value.message and "running" in refused.value.message
    import_runs.finish(run_id, "done")
    enable()

    with using(ACTUAL):
        st = sync_state.load()
        st["enabled"] = False
        sync_state.save(st)
    run_id = import_runs.start("actual_budget", "import", BUDGET)["id"]
    YnabConfigStore().update({"access_token": "stub-token"})
    YnabSyncStateStore().save({**sync_state.blank(), "plan_id": "plan-y", "currency": "USD"})
    with pytest.raises(ConflictError) as refused:
        ynab_sync.update_settings(True, None, None)
    assert "Actual" in refused.value.message and "running" in refused.value.message
    import_runs.finish(run_id, "done")


def test_sync_now_refused_while_other_running_both_ways(world, monkeypatch):
    enable()
    run_id = import_runs.start("ynab", "import", "plan-y")["id"]
    with pytest.raises(ConflictError) as refused:
        actual_app.start_sync()
    assert "YNAB" in refused.value.message
    assert not ACTUAL.lock.locked()
    import_runs.finish(run_id, "done")

    _ynab_on()
    run_id = import_runs.start("actual_budget", "import", BUDGET)["id"]
    with pytest.raises(ConflictError) as refused:
        ynab_sync.start_now()
    assert "Actual" in refused.value.message
    assert not YNAB.lock.locked()
    import_runs.finish(run_id, "done")


def test_the_tick_waits_for_the_other_apps_run_both_ways(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(
        runner, "_locked_run", lambda trigger, run_id=None: ran.append(budget_app.current().key)
    )
    enable()
    run_id = import_runs.start("ynab", "import", "plan-y")["id"]
    assert ynab_sync.run_if_due() is False and ran == []
    import_runs.finish(run_id, "done")

    with using(ACTUAL):
        st = sync_state.load()
        st["enabled"] = False
        sync_state.save(st)
    _ynab_on()
    run_id = import_runs.start("actual_budget", "import", BUDGET)["id"]
    assert ynab_sync.run_if_due() is False and ran == []
    import_runs.finish(run_id, "done")
    assert ynab_sync.run_if_due() is True and ran == ["ynab"]


def test_a_preview_or_a_verify_stands_in_nobodys_way(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(runner, "_execute", lambda st, tally: ran.append(budget_app.current().key))
    _ynab_on()
    run_id = import_runs.start("actual_budget", "preview", BUDGET)["id"]
    assert ynab_sync.run_if_due() is True and ran == ["ynab"]
    ynab_sync.start_now()
    _free(YNAB)
    assert ran == ["ynab", "ynab"]
    import_runs.finish(run_id, "done")
    run_id = import_runs.start("actual_budget", "verify", BUDGET)["id"]
    with using(YNAB):
        assert budget_app.other_running() is False
    import_runs.finish(run_id, "done")


def test_settling_a_review_item_checks_inside_the_guard(world, monkeypatch):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    sync()
    (item,) = open_items()
    held: list[bool] = []
    real = resolve.refuse_while_others_run

    def spy(then):
        held.append(budget_app._starting.locked())
        return real(then)

    monkeypatch.setattr(resolve, "refuse_while_others_run", spy)
    actual_app.resolve_sync_item(item["id"], "onw")
    assert held == [True]


def test_settling_a_review_item_waits_for_the_other_app(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    sync()
    (item,) = open_items()
    run_id = import_runs.start("ynab", "import", "plan-y")["id"]
    with pytest.raises(ConflictError) as refused:
        actual_app.resolve_sync_item(item["id"], "onw")
    assert "YNAB" in refused.value.message and not ACTUAL.lock.locked()
    import_runs.finish(run_id, "done")


def test_check_and_start_are_one_step_at_both_ends(world, monkeypatch):
    held: list[bool] = []
    real = import_runs.start

    def spy(*a, **kw):
        held.append(budget_app._starting.locked())
        return real(*a, **kw)

    monkeypatch.setattr(import_runs, "start", spy)
    monkeypatch.setattr(job._POOL, "submit", lambda *a, **kw: None)
    enable()
    actual_app.start_migration("preview", BUDGET)
    import_runs.finish(import_runs.latest("actual_budget", job.KINDS)["id"], "done")
    with using(ACTUAL):
        monkeypatch.setattr(ACTUAL.pool, "submit", lambda *a, **kw: None)
        started = runner.start_now()
    ACTUAL.lock.release()
    runner._manual_runs.pop("actual_budget", None)
    import_runs.finish(started["run_id"], "done")
    assert held == [True, True]


def test_a_race_sees_one_winner(world, monkeypatch):
    """Two ends starting together: exactly one gets through."""
    monkeypatch.setattr(job._POOL, "submit", lambda *a, **kw: None)
    YnabConfigStore().update({"access_token": "stub-token"})
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def actual_import():
        barrier.wait(WAIT)
        try:
            actual_app.start_migration("import", BUDGET, account_types=TYPES, confirm_nonempty=True)
            outcomes.append("actual")
        except ConflictError:
            outcomes.append("refused")

    def ynab_import():
        barrier.wait(WAIT)
        try:
            job.start("import", "plan-y", confirm_nonempty=True)
            outcomes.append("ynab")
        except ConflictError:
            outcomes.append("refused")

    threads = [threading.Thread(target=actual_import), threading.Thread(target=ynab_import)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
    assert sorted(outcomes) in (["actual", "refused"], ["refused", "ynab"])


def test_actual_waits_when_a_book_ended_up_with_both_on(world, monkeypatch):
    """Reachable only by toggling a flag: YNAB keeps running, Actual holds off and says why."""
    enable()
    _ynab_on(next_run_at="2999-01-01T00:00:00Z")
    opened = world.opens
    assert ynab_sync.run_if_due() is False
    assert world.opens == opened
    assert any("YNAB" in n["reason"] for n in actual_app.sync_status()["notes"])
    with pytest.raises(ConflictError):
        actual_app.start_sync()


def test_a_flagged_off_actual_holds_nothing_up(world):
    enable()
    with flags(actual_budget_sync=False):
        with using(YNAB):
            assert budget_app.other_sync_on() is None


# ── the tick ──


def _tick() -> None:
    asyncio.run(scheduler._run_ynab_sync())


def _free(app) -> None:
    deadline = time.time() + WAIT
    while app.lock.locked() and time.time() < deadline:
        time.sleep(0.01)
    assert not app.lock.locked()


def test_tick_runs_each_app_separately(world, monkeypatch):
    ran: list[tuple[str, str]] = []

    def execute(st, tally):
        ran.append((budget_app.current().key, st["plan_id"]))

    monkeypatch.setattr(runner, "_execute", execute)
    enable()
    assert ynab_sync.run_if_due() is True
    _free(ACTUAL)
    assert ran == [("actual_budget", BUDGET)]
    with using(ACTUAL):
        sync_state.save({**sync_state.load(), "enabled": False})
    _ynab_on()
    assert ynab_sync.run_if_due() is True
    assert ran == [("actual_budget", BUDGET), ("ynab", "plan-y")]
    assert budget_app.current() is YNAB


def test_an_app_that_cannot_load_stops_no_other_app(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(runner, "_execute", lambda st, tally: ran.append(budget_app.current().key))
    boom = ImportError("no Actual here")

    def broken():
        raise boom

    monkeypatch.setitem(budget_app._apps, "actual_budget", broken)
    _ynab_on()
    with pytest.raises(ImportError):
        ynab_sync.run_if_due()
    assert ran == ["ynab"]


def test_an_is_on_that_raises_stops_no_other_app(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(runner, "_execute", lambda st, tally: ran.append(budget_app.current().key))
    boom = RuntimeError("the Actual flag can't be read")

    def broken():
        raise boom

    monkeypatch.setitem(
        budget_app._apps, "actual_budget", dataclasses.replace(ACTUAL, is_on=broken)
    )
    _ynab_on()
    with pytest.raises(RuntimeError) as caught:
        ynab_sync.run_if_due()
    assert caught.value is boom
    assert ran == ["ynab"]


def _hung_actual(monkeypatch) -> tuple[threading.Event, threading.Event]:
    entered, leave = threading.Event(), threading.Event()
    ran: list[str] = []

    def execute(st, tally):
        ran.append(budget_app.current().key)
        if budget_app.current() is ACTUAL:
            entered.set()
            assert leave.wait(WAIT)

    monkeypatch.setattr(runner, "_execute", execute)
    return entered, leave


def test_a_hung_actual_run_holds_up_no_tick(world, monkeypatch):
    entered, leave = _hung_actual(monkeypatch)
    enable()
    started = time.monotonic()
    _tick()  # hands the run off and comes straight back
    assert entered.wait(WAIT)
    assert time.monotonic() - started < WAIT
    assert ACTUAL.lock.locked()

    _ynab_on()
    _tick()  # the next tick isn't held, and runs YNAB
    assert sync_state.load()["last_run"]["ok"] is True
    assert ACTUAL.lock.locked()  # still hung

    leave.set()
    _free(ACTUAL)


def test_a_hung_actual_run_starves_no_manual_ynab_run(world, monkeypatch):
    entered, leave = _hung_actual(monkeypatch)
    enable()
    _tick()
    assert entered.wait(WAIT)

    try:
        _ynab_on()
        assert ACTUAL.pool is not YNAB.pool
        ynab_sync.start_now()
        _free(YNAB)
        assert sync_state.load()["last_run"]["ok"] is True
    finally:
        leave.set()
    _free(ACTUAL)


def test_a_manual_actual_run_goes_on_actuals_own_pool(world, monkeypatch):
    entered, leave = _hung_actual(monkeypatch)
    enable()
    started = actual_app.start_sync()
    assert started["running"] is True and started["run_id"]
    assert entered.wait(WAIT)
    assert not YNAB.lock.locked()
    leave.set()
    _free(ACTUAL)
    assert import_runs.get(started["run_id"])["status"] == "done"


def test_a_handed_off_run_that_crashes_frees_the_lock(world, monkeypatch):
    enable()

    def crash(trigger, run_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_locked_run", crash)
    assert ynab_sync.run_if_due() is True
    _free(ACTUAL)


def test_the_tick_does_not_run_behind_the_gateway(world, monkeypatch):
    enable()
    opened = world.opens
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)
    assert ACTUAL.is_on() is False
    assert ynab_sync.run_if_due() is False
    assert world.opens == opened
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: False)
    assert ynab_sync.run_if_due() is True
    _free(ACTUAL)


def test_a_scheduled_run_writes_under_the_sync_surface_and_is_not_sent_back(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    world.calls.clear()
    assert ynab_sync.run_if_due() is False  # due an hour from now
    run = _run("scheduled")
    assert run["summary"] == "1 pulled"
    from backend.db.transaction_audit_store import TransactionAuditStore

    audit = TransactionAuditStore()
    events = audit.since(-1, upto=audit.max_seq(), not_surface="nothing")
    assert events[-1]["surface"] == "actual_sync"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_turning_on_from_an_import_writes_actuals_settings_and_not_ynabs(
    db_isolated, tmp_path, monkeypatch
):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    record = _import(sync_direction="from_ynab")
    assert any("Sync turned on: from Actual." == line["line"] for line in record["log"])
    st = sync_state_of()
    assert (st["enabled"], st["direction"], st["plan_id"], st["currency"]) == (
        True,
        "from_ynab",
        BUDGET,
        "USD",
    )
    assert YnabSyncStateStore().all() == []


def test_a_budget_stating_no_currency_syncs_in_the_one_it_imported_in(
    db_isolated, tmp_path, monkeypatch
):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    server.currency = None
    _import(currency="EUR")
    assert enable()["enabled"] is True
    assert sync_state_of()["currency"] == "EUR"


# ── warnings ──


def test_income_pair_put_in_an_envelope_warns(world):
    enable()
    sync()
    salary = category_here("Salary")["id"]
    env = planning.apply_budget_change(
        {"type": "envelope_create", "name": "Wages", "group_id": ""}
    )["envelope"]
    planning.apply_budget_change(
        {"type": "envelope_add_category", "envelope_id": env["id"], "category_id": salary}
    )
    planning.apply_budget_change(
        {"type": "assign", "month": KEY, "envelope_id": env["id"], "amount": 5.0}
    )
    cells = world.rows("zero_budgets")
    with using(ACTUAL):
        runner._locked_run("manual")
        st = sync_state.load()
        run = import_runs.get(st["last_run"]["run_id"])
    assert st["last_run"]["ok"] is True  # not refused
    assert any("Salary" in n["item"] and "Ready to Assign" in n["reason"] for n in run["notes"])
    (warning,) = actual_app.sync_status()["warnings"]
    assert warning["kind"] == "income_in_envelope"
    assert "Salary" in warning["text"] and "Wages" in warning["text"]
    assert "Ready to Assign" in warning["text"]
    assert HOST not in warning["text"] and PASSWORD not in warning["text"]
    assert world.rows("zero_budgets") == cells  # its Assigned stays here
    with using(ACTUAL):  # the run says it once; the page keeps showing it
        runner._locked_run("manual")
        run = import_runs.get(sync_state.load()["last_run"]["run_id"])
    assert not any("Salary" in n["item"] for n in run["notes"])
    assert len(actual_app.sync_status()["warnings"]) == 1


def test_no_warning_while_the_income_pair_has_no_envelope(world):
    enable()
    sync()
    status = actual_app.sync_status()
    assert status["notes"] == [] and status["warnings"] == []


# ── wording, and YNAB's ──


def test_a_running_sync_says_an_actual_and_ynabs_stays(world):
    enable()
    assert ACTUAL.lock.acquire(blocking=False)
    try:
        with using(ACTUAL), pytest.raises(ConflictError) as refused:
            job.start("preview", BUDGET)
        assert refused.value.message == (
            "An Actual sync is running; start the import when it finishes."
        )
        with pytest.raises(ConflictError) as refused:
            actual_app.start_sync()
        assert refused.value.message == "An Actual sync is already running."
    finally:
        ACTUAL.lock.release()
    assert YNAB.lock.acquire(blocking=False)
    try:
        YnabConfigStore().update({"access_token": "stub-token"})
        with pytest.raises(ConflictError) as refused:
            job.start("preview", "plan-y")
        assert refused.value.message == "A YNAB sync is running; start the import when it finishes."
    finally:
        YNAB.lock.release()


def test_refused_and_not_found_read_as_before_for_ynab():
    for text, refused, gone in (
        ("YNAB said HTTP 400", True, False),
        ("YNAB said HTTP 404", True, True),
        ("YNAB said HTTP 409", True, False),
        ("YNAB said HTTP 401", False, False),
        ("YNAB said HTTP 500", False, False),
    ):
        error = ExternalServiceError(text)
        assert ynab_client.rejected(error) is refused
        assert ynab_client.not_found(error) is gone
    assert ynab_client.rejected(ynab_client.Refused("no")) is True
    assert ynab_client.not_found(ynab_client.NotThere("gone")) is True
    assert ynab_client.moved(ynab_client.Moved("moved")) is True
    assert ynab_client.rejected(ynab_client.Stopped("HTTP 400")) is False
    assert ynab_client.not_found(ynab_client.Stopped("HTTP 404")) is False


def test_a_resolution_gets_each_apps_own_allowance(world):
    assert YNAB.caps.resolve_request_cap == 6
    assert ACTUAL.caps.resolve_request_cap > 6
    assert not hasattr(resolve, "RESOLVE_REQUESTS")


# ── routes ──


@pytest.fixture
def api(world):
    made = FastAPI()
    made.include_router(router)
    return TestClient(made)


def test_the_status_keeps_the_shape_the_page_reads(api):
    body = api.get("/api/actual-budget/sync").json()
    with using(YNAB):
        ynab_keys = set(runner.status())
    assert ynab_keys <= set(body)
    assert body["available"] is True and body["budget_mode"] is None
    assert body["notes"] == [] and body["warnings"] == [] and body["conflicts_count"] == 0
    assert body["direction"] == "both" and body["last_run"] is None
    assert "password" not in str(body).lower() and HOST not in str(body)


def test_the_routes_turn_on_run_and_settle(api, world):
    on = api.put(
        "/api/actual-budget/sync/settings",
        json={"enabled": True, "interval": "1h", "plan_id": BUDGET, "direction": "both"},
    )
    assert on.status_code == 200 and on.json()["enabled"] is True
    started = api.post("/api/actual-budget/sync/run")
    assert started.status_code == 202
    _free(ACTUAL)
    body = api.get("/api/actual-budget/sync").json()
    assert body["last_run"]["ok"] is True
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    assert api.post("/api/actual-budget/sync/run").status_code == 202
    _free(ACTUAL)
    (item,) = api.get("/api/actual-budget/sync").json()["conflicts"]
    settled = api.post(
        f"/api/actual-budget/sync/conflicts/{item['id']}/resolve", json={"keep": "onw"}
    )
    assert settled.status_code == 200 and settled.json()["conflicts_count"] == 0
    assert world.row("transactions", "t-pay")["notes"] == "here"


def test_the_routes_refuse_behind_the_gateway(api, monkeypatch):
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)
    for response in (
        api.get("/api/actual-budget/sync"),
        api.put("/api/actual-budget/sync/settings", json={"enabled": True}),
        api.post("/api/actual-budget/sync/run"),
        api.post("/api/actual-budget/sync/conflicts/c_x/resolve", json={"keep": "onw"}),
    ):
        assert response.status_code == 409


def test_the_routes_are_gone_with_the_flag_off(api):
    with flags(actual_budget_sync=False):
        assert api.get("/api/actual-budget/sync").status_code == 404
        assert api.post("/api/actual-budget/sync/run").status_code == 404
        with pytest.raises(NotFoundError):
            actual_app.sync_status()


def test_an_import_route_can_turn_the_sync_on(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    made = FastAPI()
    made.include_router(router)
    client = TestClient(made)
    response = client.post(
        "/api/actual-budget/migration/start",
        json={"plan_id": BUDGET, "account_types": TYPES, "sync_direction": "both"},
    )
    assert response.status_code == 202
    deadline = time.time() + 60
    while (
        time.time() < deadline
        and import_runs.latest("actual_budget", job.KINDS)["status"] == "running"
    ):
        time.sleep(0.02)
    assert client.get("/api/actual-budget/sync").json()["enabled"] is True
