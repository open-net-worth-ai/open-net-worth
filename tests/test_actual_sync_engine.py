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
from backend.core.errors import ConflictError, ExternalServiceError, NotFoundError
from backend.core.user_time import user_today
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.actual_sync_store import (
    ActualBaselineStore,
    ActualConflictStore,
    ActualMirrorStore,
    ActualSyncStateStore,
)
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
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync import delta, push, resolve, runner
from backend.services.ynab_sync import state as sync_state
from tests.actual_sync_stub import BUDGET, HOST, PASSWORD, URL, FakeServer, down, seed
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
    assert all(not path.exists() for path in world.copies)


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
    # A Reset sync gives the budget a new group and snapshot: the file and row ids stay.
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed"
    assert writes_seen(world) == []
    assert open_items() == []
    assert ActualBaselineStore().all() == baselines
    assert all(
        not t.get("deleted")
        for t in ledger_service.transactions.all()
        if t["description"] == "ACME Corp"
    )
    assert sync_state_of()["plan_id"] == BUDGET


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
    run_id = import_runs.start("actual_budget", "preview", BUDGET)["id"]
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

    _ynab_on()
    assert ACTUAL.pool is not YNAB.pool
    assert ynab_sync.start_now()["running"] is True
    _free(YNAB)
    assert sync_state.load()["last_run"]["ok"] is True

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
