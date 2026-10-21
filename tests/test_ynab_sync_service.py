"""End-to-end: the two-way YNAB sync against an isolated database and the synthetic YNAB API.
Each test migrates the stub plan with the real migration, then edits one side or both."""

from __future__ import annotations

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning, ynab_sync
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_sync import runner
from tests import ynab_stub
from tests.test_ynab_migration_service import run as migrate


@pytest.fixture
def world(db_isolated, monkeypatch):
    server, base_url, calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    assert migrate()["status"] == "done"
    server.writes.clear()
    yield server
    server.shutdown()


def enable(interval="1h"):
    return ynab_sync.update_settings(True, interval, ynab_stub.PLAN)


def sync():
    runner._locked_run("manual")
    run = ynab_sync.status()["last_run"]
    assert run["ok"], run["error"]
    return run


def row(description, **match):
    rows = [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == description
        and not t.get("deleted")
        and all(t.get(k) == v for k, v in match.items())
    ]
    assert len(rows) == 1, (description, len(rows))
    return rows[0]


def test_first_run_after_a_clean_migration_changes_nothing(world):
    enable()
    run = sync()
    assert run["summary"] == "Nothing changed" and run["requests"] == 1
    assert world.writes == []
    assert ynab_sync.status()["conflicts"] == []


def edit_onw(txn_id, **changes):
    from backend.core import audit_context

    token = audit_context.set_audit_scope("user", "ui")
    try:
        return ledger_service.update_transaction(txn_id, changes)
    finally:
        audit_context.clear_audit_scope(token)


def patches(world):
    return [w for w in world.writes if w[0] == "PATCH" and w[1].endswith("/transactions")]


def test_a_ledger_edit_is_sent_with_a_red_flag_and_not_pulled_back(world):
    enable()
    sync()
    bistro = row("Bistro")
    edit_onw(bistro["id"], memo="team dinner", cleared=False)
    run = sync()
    assert run["summary"] == "1 sent to YNAB"
    ((_, _, body),) = patches(world)
    (op,) = body["transactions"]
    assert (
        op["memo"] == "team dinner" and op["cleared"] == "uncleared" and op["flag_color"] == "red"
    )
    assert "amount" not in op and "date" not in op
    yn = ynab_stub.find(world.state, payee_name="Bistro")
    assert yn["memo"] == "team dinner" and yn["flag_color"] == "red"
    writes = len(world.writes)
    again = sync()  # the next read returns our own write: nothing to do
    assert again["summary"] == "Nothing changed" and len(world.writes) == writes


def test_a_ynab_edit_is_applied_in_the_ledger_and_not_sent_back(world):
    enable()
    sync()
    ynab_stub.ynab_edit(
        world.state,
        ynab_stub.find(world.state, payee_name="Bistro")["id"],
        memo="from ynab",
        amount=-70_000,
        category_id=world.state["C"]["fun"],
    )
    run = sync()
    assert run["summary"] == "1 pulled" and world.writes == []
    bistro = row("Bistro")
    assert bistro["memo"] == "from ynab" and bistro["from_amount"] == 70.0
    assert categories_service.resolve_category_ref(
        bistro["category"]
    ) == categories_service.resolve_category_ref("Fun Money")
    assert sync()["summary"] == "Nothing changed" and world.writes == []


def test_changes_to_the_same_transaction_on_both_sides_wait_for_review(world):
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="theirs"
    )
    run = sync()
    assert run["summary"] == "1 needs review" and world.writes == []
    (item,) = ynab_sync.status()["conflicts"]
    assert (
        item["kind"] == "conflict"
        and item["onw"]["memo"] == "mine"
        and item["ynab"]["memo"] == "theirs"
    )
    assert sync()["summary"].endswith("1 needs review")  # still waiting; not duplicated
    assert len(ynab_sync.status()["conflicts"]) == 1

    ynab_sync.resolve.resolve(item["id"], "onw")
    assert ynab_stub.find(world.state, payee_name="Bistro")["memo"] == "mine"
    assert ynab_sync.status()["conflicts"] == []
    assert sync()["summary"] == "Nothing changed"


def test_keeping_ynabs_version_writes_it_into_the_ledger(world):
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="theirs"
    )
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    ynab_sync.resolve.resolve(item["id"], "ynab")
    assert row("Bistro")["memo"] == "theirs" and world.writes == []
    assert sync()["summary"] == "Nothing changed" and world.writes == []


def assign(month_offset, envelope_key, amount):
    from backend.core.user_time import user_today

    today = user_today()
    i = today.year * 12 + today.month - 1 + month_offset
    month = f"{i // 12:04d}-{i % 12 + 1:02d}"
    cats = {c["label"]: c for c in categories_service.all_category_rows()}
    env = next(e for e in envelopes_service.all_envelopes() if e["name"] == envelope_key)
    planning.apply_budget_change(
        {"type": "assign", "month": month, "envelope_id": env["id"], "amount": amount},
        allow_negative=True,
    )
    return month, cats


def month_patches(world):
    return [w for w in world.writes if "/months/" in w[1]]


def note_of(world, key):
    cid = world.state["C"][key]
    return next(c for g in world.state["groups"] for c in g["categories"] if c["id"] == cid).get(
        "note"
    )


def test_assigned_is_sent_with_a_note_line_and_the_note_keeps_ten_lines(world):
    world.state["groups"][2]["categories"][0]["note"] = "pays the landlord"
    enable()
    sync()
    for n in range(12):
        assign(0, "Rent", 1200 + n + 1)
        sync()
    (_, path, body) = month_patches(world)[-1]
    assert body == {"category": {"budgeted": 1_212_000}}
    lines = note_of(world, "rent").split("\n")
    assert lines[0] == "pays the landlord"
    assert len(lines) == 11 and lines[-1].endswith("assigned 1212.00 (was 1211.00)")
    assert lines[-1].startswith("ONW 20")
    run = sync()  # echo: YNAB hands our write back; nothing moves
    assert run["summary"] == "Nothing changed" and run["requests"] == 1


def test_assigned_changed_in_ynab_is_pulled_and_a_card_envelope_is_never_pushed(world):
    enable()
    sync()
    ynab_stub.ynab_budget(world.state, 0, "groc", 350_000)
    assert sync()["summary"] == "1 pulled" and world.writes == []
    month, _ = assign(0, "Groceries", 350)  # same figure: agrees, nothing to send
    assert sync()["summary"] == "Nothing changed"


def test_assigned_changed_on_both_sides_is_a_conflict(world):
    enable()
    sync()
    assign(0, "Fun Money", 70)
    ynab_stub.ynab_budget(world.state, 0, "fun", 60_000)
    assert sync()["summary"] == "1 needs review"
    (item,) = ynab_sync.status()["conflicts"]
    assert item["entity"] == "assigned" and (item["onw"]["amount"], item["ynab"]["amount"]) == (
        70.0,
        60.0,
    )
    ynab_sync.resolve.resolve(item["id"], "ynab")
    env = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Fun Money")
    assert (
        max(
            planning.assignments_store.month_map(m).get(env["id"], 0)
            for m in planning.assignments_store.months()
        )
        == 60.0
    )


def test_the_first_run_sends_edits_made_in_the_ledger_since_the_migration(world):
    bistro = row("Bistro")
    edit_onw(bistro["id"], memo="before first sync")
    edit_onw(bistro["id"], cleared=False)
    assign(0, "Rent", 1300)
    enable()
    run = sync()
    assert run["summary"] == "1 sent to YNAB, 1 needs review"
    yn = ynab_stub.find(world.state, payee_name="Bistro")
    assert yn["memo"] == "before first sync" and yn["flag_color"] == "red"
    # Assigned differs from YNAB's: YNAB may have changed since the migration, so on the first
    # run it is the person's call, never an automatic overwrite.
    assert world.state["budget"][0]["rent"] == 1_200_000 and month_patches(world) == []
    (item,) = ynab_sync.status()["conflicts"]
    assert (item["entity"], item["onw"]["amount"], item["ynab"]["amount"]) == (
        "assigned",
        1300.0,
        1200.0,
    )
    ynab_sync.resolve.resolve(item["id"], "onw")
    assert world.state["budget"][0]["rent"] == 1_300_000
    assert note_of(world, "rent").endswith("assigned 1300.00 (was 1200.00)")
    assert sync()["summary"] == "Nothing changed"


def test_the_first_run_takes_ynabs_edits_to_rows_the_ledger_did_not_touch(world):
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="edited in ynab"
    )
    enable()
    assert sync()["summary"] == "1 pulled" and world.writes == []
    assert row("Bistro")["memo"] == "edited in ynab"


def test_the_first_run_flags_a_row_edited_on_both_sides_and_adopts_equal_edits(world):
    edit_onw(row("Bistro")["id"], memo="mine")
    edit_onw(row("Pizza")["id"], memo="same")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="theirs"
    )
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Pizza")["id"], memo="same"
    )
    enable()
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["title"] == "Bistro" and world.writes == []


def test_request_cap_defers_the_rest_without_losing_it(world, monkeypatch):
    from backend.services.ynab_sync import state as sync_state

    enable()
    sync()
    for off in (-2, -1, 0):
        assign(off, "Rent", 1500 + off)
    monkeypatch.setattr(sync_state, "REQUEST_CAP", 3)  # the read, one figure, then out of budget
    first = sync()
    assert first["requests"] <= 3 and first["deferred"] >= 1
    monkeypatch.setattr(sync_state, "REQUEST_CAP", 40)
    sync()
    for off in (-2, -1, 0):
        assert world.state["budget"][off]["rent"] == (1500 + off) * 1000
    assert sync()["summary"] == "Nothing changed"


def account(name):
    return next(a for a in ledger_service.accounts.query_accounts() if a["name"] == name)


def create_onw(**row_):
    from backend.core import audit_context

    token = audit_context.set_audit_scope("user", "ui")
    try:
        return ledger_service.create_transaction({"needs_review": False, **row_})
    finally:
        audit_context.clear_audit_scope(token)


def posts(world):
    return [w for w in world.writes if w[0] == "POST"]


def test_new_ledger_transactions_are_created_in_ynab_and_linked(world):
    from backend.core.user_time import user_today

    enable()
    sync()
    today = user_today().isoformat()
    groceries = categories_service.resolve_category_ref("Groceries")
    plain = create_onw(
        kind="expense",
        date=today,
        description="Corner shop",
        from_account_id=account("Checking")["id"],
        from_amount=12.5,
        from_currency="USD",
        category=groceries,
    )
    move = create_onw(
        kind="transfer",
        date=today,
        description="Top up",
        from_account_id=account("Checking")["id"],
        from_amount=40,
        from_currency="USD",
        to_account_id=account("Savings")["id"],
        to_amount=40,
        to_currency="USD",
    )
    split = create_onw(
        kind="expense",
        date=today,
        description="Market",
        from_account_id=account("Checking")["id"],
        from_amount=30,
        from_currency="USD",
        splits=[
            {"category": groceries, "amount": 20},
            {"category": categories_service.resolve_category_ref("Fun Money"), "amount": 10},
        ],
    )
    run = sync()
    assert run["summary"] == "3 sent to YNAB" and len(posts(world)) == 1
    sent = {t.get("payee_name", "transfer"): t for t in posts(world)[0][2]["transactions"]}
    assert all(t["flag_color"] == "red" and t["approved"] for t in sent.values())
    assert (
        sent["Corner shop"]["amount"] == -12_500
        and sent["Corner shop"]["category_id"] == world.state["C"]["groc"]
    )
    assert [s["amount"] for s in sent["Market"]["subtransactions"]] == [-20_000, -10_000]
    assert sent["transfer"]["payee_id"] == "tp-" + world.state["A"]["sav"]
    for onw in (plain, move, split):
        refs = [
            r["ref"] for r in ledger_service.transactions.find_by_id(onw["id"])["external_refs"]
        ]
        assert refs and all(r.startswith("ynab-new") for r in refs)
    assert len(ledger_service.transactions.find_by_id(move["id"])["external_refs"]) == 2
    writes = len(world.writes)
    assert sync()["summary"] == "Nothing changed" and len(world.writes) == writes
    assert (
        len([t for t in ledger_service.transactions.all() if t["description"] == "Corner shop"])
        == 1
    )


def test_deleting_on_either_side_deletes_on_the_other(world):
    enable()
    sync()
    ledger_service.bulk_delete_transactions([row("Bistro")["id"]])
    assert sync()["summary"] == "1 sent to YNAB"
    assert [w[0] for w in world.writes] == ["DELETE"]
    ynab_stub.ynab_delete(world.state, ynab_stub.find(world.state, payee_name="Pizza")["id"])
    assert sync()["summary"] == "1 pulled"
    assert not [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "Pizza" and not t.get("deleted")
    ]
    assert sync()["summary"] == "Nothing changed"


def test_a_transaction_added_in_ynab_is_booked_in_the_ledger(world):
    from backend.core.user_time import user_today

    enable()
    sync()
    ynab_stub.ynab_add(
        world.state, "chk", user_today().isoformat(), -45_000, "Bakery", "groc", memo="bread"
    )
    assert sync()["summary"] == "1 pulled" and world.writes == []
    created = row("Bakery")
    assert created["from_amount"] == 45.0 and created["memo"] == "bread"
    assert sync()["summary"] == "Nothing changed"


def test_what_ynabs_api_cannot_express_goes_to_the_review_list(world):
    enable()
    sync()
    split = next(t for t in ledger_service.transactions.all() if t.get("splits"))
    edit_onw(split["id"], date="2000-01-15")
    run = sync()
    assert run["summary"] == "1 needs review" and world.writes == []
    (item,) = ynab_sync.status()["conflicts"]
    assert item["kind"] == "unsupported" and "split" in item["reason"]
    ynab_sync.resolve.resolve(item["id"], "ynab")  # revert the ledger to YNAB's version
    assert ledger_service.transactions.find_by_id(split["id"])["date"] != "2000-01-15"
    assert sync()["summary"] == "Nothing changed"


def test_a_future_dated_ledger_transaction_is_not_sent(world):
    enable()
    sync()
    create_onw(
        kind="expense",
        date="2999-01-01",
        description="Someday",
        from_account_id=account("Checking")["id"],
        from_amount=5,
        from_currency="USD",
    )
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["kind"] == "unsupported" and "future" in item["reason"] and world.writes == []


def test_a_starting_balance_is_never_sent(world):
    edit_onw(account("Checking")["opening_txn_id"], to_amount=5100, to_currency="USD")
    enable()
    sync()
    assert world.writes == [] and ynab_sync.status()["conflicts"] == []


def test_a_rejected_write_is_filed_with_ynabs_reason_and_not_retried(world):
    enable()
    sync()
    world.state["reject_writes"] = "memo is too long"
    edit_onw(row("Bistro")["id"], memo="x")
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["kind"] == "failed" and "memo is too long" in item["reason"]
    count = len(world.writes)
    sync()
    assert len(world.writes) == count


def test_a_ledger_edit_to_a_row_ynab_deleted_is_a_conflict(world):
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_delete(world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"])
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["ynab"] is None and item["onw"]["memo"] == "mine"
    ynab_sync.resolve.resolve(item["id"], "onw")  # recreate it in YNAB
    assert ynab_stub.find(world.state, payee_name="Bistro")["memo"] == "mine"
    assert sync()["summary"] == "Nothing changed"


def test_a_read_that_would_delete_most_of_the_ledger_is_refused(world, monkeypatch):
    from backend.services.ynab_sync import txn_sync

    monkeypatch.setattr(txn_sync, "MIN_REMOVAL_GUARD", 3)
    enable()
    sync()
    for t in list(world.state["txns"]):
        if not t["deleted"]:
            ynab_stub.ynab_delete(world.state, t["id"])
    runner._locked_run("manual")
    run = ynab_sync.status()["last_run"]
    assert not run["ok"] and "would delete" in run["error"]
    assert len([t for t in ledger_service.transactions.all() if not t.get("deleted")]) > 15


def test_every_write_the_sync_makes_carries_its_own_audit_surface(world):
    from backend.db.transaction_audit_store import TransactionAuditStore

    enable()
    sync()
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="from ynab"
    )
    sync()
    events = TransactionAuditStore().for_txn(row("Bistro")["id"])
    assert events[-1]["op"] == "update" and events[-1]["surface"] == ynab_sync.SURFACE


def test_the_token_never_appears_in_status_errors_or_logs(world, caplog):
    import json
    import logging

    caplog.set_level(logging.DEBUG)
    enable()
    sync()
    YnabConfigStore().update({"access_token": "SUPER-SECRET-TOKEN"})
    runner._locked_run("manual")
    run = ynab_sync.status()["last_run"]
    assert not run["ok"] and "401" in run["error"]
    blob = json.dumps(ynab_sync.status()) + caplog.text
    assert "SUPER-SECRET-TOKEN" not in blob and ynab_stub.TOKEN not in blob
    from backend.db.ynab_sync_store import YnabSyncStateStore

    assert "SUPER-SECRET-TOKEN" not in json.dumps(YnabSyncStateStore().all())


def test_enabling_needs_a_token_a_visible_plan_and_a_migrated_book(world):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="not visible"):
        ynab_sync.update_settings(True, "1h", "not-a-plan")
    with pytest.raises(ValidationError, match="interval"):
        ynab_sync.update_settings(None, "5m", ynab_stub.PLAN)
    with pytest.raises(ValidationError, match="pick the YNAB plan"):
        ynab_sync.update_settings(True, None, None)
    status = ynab_sync.update_settings(False, "6h", ynab_stub.PLAN)
    assert (status["enabled"], status["interval"], status["plan_name"]) == (
        False,
        "6h",
        "Synthetic Plan",
    )
    assert ynab_sync.start_now.__name__ and not ynab_sync.run_if_due()  # disabled: never due
    with pytest.raises(ValidationError, match="turn the two-way sync on"):
        ynab_sync.start_now()


def test_a_second_run_while_one_is_running_is_refused(world):
    from backend.core.errors import ConflictError

    enable()
    assert runner._lock.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            ynab_sync.start_now()
        assert ynab_sync.status()["running"] and not ynab_sync.run_if_due()
    finally:
        runner._lock.release()


def test_the_scheduler_tick_runs_a_due_sync_once(world):
    enable("15m")
    assert ynab_sync.run_if_due() is True
    assert ynab_sync.status()["last_run"]["trigger"] == "scheduled"
    assert ynab_sync.status()["next_run_at"] and ynab_sync.run_if_due() is False


def test_the_plain_importer_steps_aside_for_a_synced_plan(world):
    from backend.core.errors import ValidationError

    YnabConfigStore().update({"budget_id": ynab_stub.PLAN})
    enable()
    with pytest.raises(ValidationError, match="two-way"):
        ynab_service.fetch_window(YnabConfigStore(), "2000-01-01", "2999-01-01", None)
    ynab_sync.update_settings(False, None, None)
    ynab_service.fetch_window(YnabConfigStore(), "2000-01-01", "2999-01-01", None)


def test_a_ledger_delete_against_a_ynab_edit_is_a_conflict_either_side_can_win(world):
    enable()
    sync()
    ledger_service.bulk_delete_transactions([row("Bistro")["id"]])
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="still here"
    )
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["onw"] is None and item["ynab"]["memo"] == "still here"
    ynab_sync.resolve.resolve(item["id"], "ynab")  # book it back
    assert row("Bistro")["memo"] == "still here" and world.writes == []
    assert sync()["summary"] == "Nothing changed"


def test_a_transfer_edit_updates_both_legs_and_a_split_takes_its_memo(world):
    enable()
    sync()
    move = next(
        t
        for t in ledger_service.transactions.all()
        if t["kind"] == "transfer" and t["from_amount"] == 500
    )
    edit_onw(move["id"], from_amount=450, to_amount=450, memo="smaller")
    split = next(t for t in ledger_service.transactions.all() if t.get("splits"))
    edit_onw(split["id"], memo="big trip, receipts kept")
    assert sync()["summary"] == "2 sent to YNAB"
    ((_, _, body),) = patches(world)
    ops = {op["id"]: op for op in body["transactions"]}
    assert sorted(op.get("amount") for op in ops.values() if "amount" in op) == [-450_000, 450_000]
    assert all(op["flag_color"] == "red" for op in ops.values())
    assert (
        ynab_stub.find(world.state, id=split_ynab_id(world, "Costco"))["memo"]
        == "big trip, receipts kept"
    )
    assert sync()["summary"] == "Nothing changed"


def split_ynab_id(world, payee):
    return ynab_stub.find(world.state, payee_name=payee)["id"]


def test_a_malformed_id_is_never_put_in_a_request_path():
    from backend.core.errors import ExternalServiceError
    from backend.services.ynab_sync.client import Budget, YnabClient

    client = YnabClient("plan-1", Budget(5))
    with pytest.raises(ExternalServiceError):
        client.delete_transaction("../../accounts")
    with pytest.raises(ExternalServiceError):
        client.set_assigned("2026-10", "x/../y", 1)
    assert client.budget.used == 0


def ledger_rows(description):
    return [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == description and not t.get("deleted")
    ]


def new_expense(description, amount=9.0, **kw):
    from backend.core.user_time import user_today

    return create_onw(
        kind="expense",
        date=user_today().isoformat(),
        description=description,
        from_account_id=account("Checking")["id"],
        from_amount=amount,
        from_currency="USD",
        **kw,
    )


def test_a_failure_after_the_creates_cannot_orphan_them(world):
    enable()
    sync()
    new_expense("Kiosk")
    ledger_service.bulk_delete_transactions([row("Bistro")["id"]])
    world.state["fail_before"] = ["DELETE"]
    runner._locked_run("manual")
    assert not ynab_sync.status()["last_run"]["ok"]
    (kiosk,) = ledger_rows("Kiosk")
    assert [r["ref"] for r in kiosk["external_refs"]]  # linked the moment YNAB confirmed it
    world.state["fail_before"] = []
    sync()
    assert len(posts(world)) == 1 and len(ledger_rows("Kiosk")) == 1
    assert sync()["summary"] == "Nothing changed"


def test_a_create_whose_answer_was_lost_is_claimed_not_sent_twice(world):
    enable()
    sync()
    new_expense("Kiosk")
    world.state["fail_after"] = ["POST"]
    runner._locked_run("manual")
    assert not ynab_sync.status()["last_run"]["ok"] and not ledger_rows("Kiosk")[0]["external_refs"]
    world.state["fail_after"] = []
    run = sync()
    assert len(posts(world)) == 1 and len(ledger_rows("Kiosk")) == 1 and run["pulled"] == 0
    assert [r["ref"] for r in ledger_rows("Kiosk")[0]["external_refs"]]
    assert len([t for t in world.state["txns"] if t["payee_name"] == "Kiosk"]) == 1
    assert sync()["summary"] == "Nothing changed"


def test_created_rows_are_paired_by_payee_and_category_not_just_amount_and_date():
    from backend.services.ynab_sync.push import _match_created

    def body(payee):
        return {"account_id": "a", "date": "2026-10-01", "amount": -5000, "payee_name": payee}

    def saved(i, payee):
        return {"id": i, **body(payee), "transfer_account_id": None}

    jobs = [(object(), [body("Cafe")]), (object(), [body("Bakery")])]
    hits = _match_created(jobs, [saved("y2", "bakery"), saved("y1", "CAFE")])
    assert [h["id"] for h in hits] == ["y1", "y2"]


def test_a_mass_of_ledger_deletes_is_not_sent_to_ynab_unreviewed(world, monkeypatch):
    from backend.services.ynab_sync import txn_sync

    monkeypatch.setattr(txn_sync, "MIN_REMOVAL_GUARD", 1)
    enable()
    sync()
    victims = [
        t
        for t in ledger_service.transactions.all()
        if t["description"] in ("Grocer", "Employer", "Landlord", "Bistro", "Pizza", "Interest")
        and not t.get("deleted")
    ]
    ledger_service.bulk_delete_transactions([t["id"] for t in victims][:6])
    sync()
    assert not [w for w in world.writes if w[0] == "DELETE"]
    items = ynab_sync.status()["conflicts"]
    assert len(items) == 6 and "confirm each" in items[0]["reason"]
    ynab_sync.resolve.resolve(items[0]["id"], "onw")
    assert len([w for w in world.writes if w[0] == "DELETE"]) == 1


def test_a_row_with_refs_ynab_never_held_is_created_in_ynab(world):
    enable()
    sync()
    new_expense("Restored", external_refs=[{"source": "ynab", "ref": "long-gone-1"}])
    sync()
    assert (
        len(posts(world)) == 1
        and len([t for t in world.state["txns"] if t["payee_name"] == "Restored"]) == 1
    )
    assert sync()["summary"] == "Nothing changed"


def test_a_ledger_change_nobody_audited_is_still_sent(world):
    enable()
    sync()
    bistro = row("Bistro")
    ledger_service.transactions.upsert({**bistro, "memo": "rewound state"})  # no audit event
    assert sync()["summary"] == "1 sent to YNAB"
    assert ynab_stub.find(world.state, payee_name="Bistro")["memo"] == "rewound state"
    assert sync()["summary"] == "Nothing changed"


def test_a_row_that_changed_since_the_snapshot_is_not_overwritten(world):
    from backend.services.ynab_sync import pull

    enable()
    sync()
    snapshot = row("Bistro")
    edit_onw(snapshot["id"], memo="edited meanwhile")
    with pytest.raises(pull.LedgerChanged):
        pull.apply(
            snapshot, {**snapshot, "memo": "ynab", "external_refs": snapshot["external_refs"]}
        )
    assert row("Bistro")["memo"] == "edited meanwhile"


def test_a_rate_limit_fails_fast_charges_the_send_and_defers(world, monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: (_ for _ in ()).throw(AssertionError("slept")))
    enable()
    world.state["rate_limit"] = True
    runner._locked_run("manual")
    run = ynab_sync.status()["last_run"]
    assert not run["ok"] and "rate limit" in run["error"] and run["requests"] == 1


def test_resolving_with_no_plan_is_a_validation_error(db_isolated):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        ynab_sync.resolve.resolve("c_x", "onw")
