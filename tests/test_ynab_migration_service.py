"""End-to-end: the YNAB migration job against an isolated database and a synthetic YNAB API."""

from __future__ import annotations

import json
import threading
import time

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning, ynab_migration
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_migration import job as job_module
from tests import ynab_stub


@pytest.fixture
def ynab(db_isolated, monkeypatch):
    server, base_url, calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()  # what boot does
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    yield calls
    server.shutdown()


def run(mode="import", **kw):
    ynab_migration.start(mode, ynab_stub.PLAN, **kw)
    deadline = time.time() + 90
    while time.time() < deadline:
        job = ynab_migration.status()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("migration job did not finish")


def ynab_ids():
    return {
        r["external_refs"][0]["ref"]
        for r in ledger_service.transactions.all()
        if r.get("external_refs") and not r.get("deleted")
    }


def test_preview_reads_and_writes_nothing(ynab):
    job = run("preview")
    assert job["status"] == "done" and job["verification"] is None
    preview = job["preview"]
    assert (preview["transactions"], preview["categories"], preview["budget_months"]) == (19, 6, 4)
    assert [a["name"] for a in preview["accounts"]][:3] == ["Checking", "Savings", "Visa"]
    assert preview["book_state"] == "fresh"
    assert ledger_service.accounts.query_accounts(include_archived=True) == []


def test_import_verifies_clean_and_a_second_run_books_nothing(ynab):
    job = run()
    assert job["status"] == "done", job["error"]
    assert job["verification"]["ok"], job["verification"]["mismatches"]
    assert job["counts"]["transactions"] == {
        "created": 19,
        "existing": 0,
        "refused": 0,
        "failed": 0,
    }
    assert job["counts"]["accounts"]["created"] == 5 and job["book_state"] == "fresh"
    lines = [e["line"] for e in job["log"]]
    assert "Cleared set on 0 transaction(s)." in lines  # booked cleared; no per-row pass
    from backend.services.budget_engines import service as budget_engines_service

    engines = budget_engines_service.list_engines()
    assert engines["home_engine_id"] == "zero_based"
    assert next(e for e in engines["engines"] if e["id"] == "zero_based")["configured"]

    accounts = {a["name"]: a for a in ledger_service.accounts.query_accounts(include_archived=True)}
    assert accounts["Old Account"]["archived"] is True
    assert accounts["Visa"]["type"] == "credit" and accounts["Brokerage"]["type"] == "investment"
    assert accounts["Visa"]["external_ids"][0]["source"] == "ynab"
    assert planning.budget_month("2000-01")["base_currency"] == "USD"
    before = len(ynab_ids())

    again = run()
    assert again["status"] == "done" and again["verification"]["ok"]
    assert again["counts"]["transactions"] == {
        "created": 0,
        "existing": 19,
        "refused": 0,
        "failed": 0,
    }
    assert again["book_state"] == "rerun"
    assert len(ynab_ids()) == before
    assert len(ledger_service.accounts.query_accounts(include_archived=True)) == 5


def test_split_with_a_transfer_line_and_a_transfer_pair_are_single_transactions(ynab):
    run()
    rows = [t for t in ledger_service.transactions.all() if t.get("external_refs")]
    transfers = [t for t in rows if t["kind"] == "transfer"]
    assert len(transfers) == 4 and all(len(t["external_refs"]) == 2 for t in transfers)
    (split,) = [t for t in rows if t.get("splits")]
    assert sorted(s["amount"] for s in split["splits"]) == [30.0, 100.0]


def test_verify_mode_rechecks_without_writing(ynab):
    run()
    before = len(ynab_ids())
    job = run("verify")
    assert job["status"] == "done" and job["verification"]["ok"] and len(ynab_ids()) == before


def test_a_deleted_row_is_not_booked_again(ynab):
    run()
    victim = next(t for t in ledger_service.transactions.all() if t.get("description") == "Mystery")
    ledger_service.bulk_delete_transactions([victim["id"]])
    job = run()
    assert job["counts"]["transactions"]["refused"] == 1
    assert "deleted this transaction" in job["problems"][0]["hint"]
    assert job["verification"]["ok"] is False  # the missing row is surfaced, not hidden


def test_a_book_with_other_accounts_needs_confirmation(ynab):
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {"name": "Manual", "type": "cash", "currency": "SGD", "initial_balance": 10}
    )
    with pytest.raises(ynab_migration.ConfirmationRequired):
        ynab_migration.start("import", ynab_stub.PLAN)
    job = run(confirm_nonempty=True)
    assert job["status"] == "failed" and "base currency" in job["error"]


def test_a_second_start_while_one_runs_conflicts(ynab, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(job_module, "_execute", lambda *a: gate.wait(10))
    ynab_migration.start("preview", ynab_stub.PLAN)
    with pytest.raises(ConflictError):
        ynab_migration.start("preview", ynab_stub.PLAN)
    gate.set()


def test_the_plan_must_be_picked_and_visible(ynab):
    with pytest.raises(ValidationError):
        ynab_migration.start("preview", "  ")
    ynab_migration.start("preview", "no-such-plan")
    deadline = time.time() + 30
    while ynab_migration.status()["job"]["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert "not visible" in ynab_migration.status()["job"]["error"]


def test_start_needs_a_saved_token(db_isolated):
    with pytest.raises(ValidationError):
        ynab_migration.start("preview", ynab_stub.PLAN)


def test_the_token_never_appears_in_status(ynab):
    run()
    assert ynab_stub.TOKEN not in json.dumps(ynab_migration.status())


# ── safety: what an import may touch in a book that already holds something ──────────


def _labels():
    return {c["label"] for c in categories_service.all_category_rows()}


def test_prune_keeps_everything_the_user_made_funded_or_targeted(ynab):
    mine = planning.apply_budget_change({"type": "category_create", "label": "Pet Food"})
    env = planning.apply_budget_change({"type": "envelope_create", "name": "Pet Food"})["envelope"]
    planning.apply_budget_change(
        {"type": "envelope_add_category", "envelope_id": env["id"], "category_id": mine["id"]}
    )
    funded = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Vacation")
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-01", "envelope_id": funded["id"], "amount": 50}
    )
    targeted = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Entertainment")
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": targeted["id"],
            "target_type": "monthly",
            "amount": 20,
        }
    )
    job = run()
    assert job["status"] == "done", job["error"]
    kept = _labels()
    assert {"Pet Food", "Vacation", "Entertainment"} <= kept
    assert "Medical expenses" not in kept  # untouched starter: removed
    assert (
        job["preview"]["prune"]["categories"]
        and "Vacation" not in job["preview"]["prune"]["categories"]
    )


def test_preview_lists_the_prune_and_the_currency_change(ynab):
    preview = run("preview")["preview"]
    assert preview["currency_change"] is True and preview["base_currency"] == "SGD"
    assert "Medical expenses" in preview["prune"]["categories"]
    assert "Groceries" not in preview["prune"]["categories"]  # the plan reuses it


def test_a_book_with_money_data_but_no_accounts_keeps_its_currency_and_budget(ynab):
    from backend.db.owned_assets_store import OwnedAssetsStore

    OwnedAssetsStore().insert({"id": "oa_house", "name": "House"})
    preview = run("preview")["preview"]
    assert preview["currency_blocked"] is True and preview["money_data"] == ["owned assets"]
    job = run()
    assert job["status"] == "failed" and "owned assets" in job["error"]
    assert ledger_service.accounts.query_accounts(include_archived=True) == []
    assert "Medical expenses" in _labels()


def test_an_import_takes_a_backup_first_and_names_it(ynab):
    job = run()
    assert job["backup_name"] and job["backup_name"] in " ".join(e["line"] for e in job["log"])


def test_no_backup_means_nothing_is_written(ynab, monkeypatch):
    from backend.services import backup

    monkeypatch.setattr(backup, "run_backup", lambda **kw: {"ok": False, "error": "disk full"})
    job = run()
    assert job["status"] == "failed" and "backup" in job["error"]
    assert ledger_service.accounts.query_accounts(include_archived=True) == []


def test_an_import_spends_a_handful_of_ynab_requests(ynab):
    # YNAB allows 200 requests an hour: one export plus the verify reads, never one per
    # account or per month (a long-lived plan's run used ~140 and the next one hit HTTP 429).
    run()
    # plan list + the export, then verification's accounts + this month
    assert [c.rsplit("/", 1)[-1] for c in ynab] == [
        "plans",
        ynab_stub.PLAN,
        "accounts",
        ynab[-1].rsplit("/", 1)[-1],
    ]
    assert "/months/" in ynab[-1]


def test_an_invalid_plan_currency_is_refused(ynab, monkeypatch):
    from backend.services.ynab_migration import source

    monkeypatch.setattr(
        source,
        "list_plans",
        lambda store: [{"id": ynab_stub.PLAN, "name": "P", "currency": "us$"}],
    )
    job = run("preview")
    assert job["status"] == "failed" and "currency" in job["error"]


def test_a_finished_jobs_late_writes_never_land_on_the_next_job(ynab, monkeypatch):
    first = run("preview")
    gate = threading.Event()
    monkeypatch.setattr(job_module, "_execute", lambda *a: gate.wait(10))
    ynab_migration.start("preview", ynab_stub.PLAN)
    job_module._Run(first["id"]).update(error="stale", status="failed")
    job_module._Run(first["id"]).log("stale line")
    current = ynab_migration.status()["job"]
    assert current["error"] is None and current["status"] == "running" and current["log"] == []
    gate.set()


def test_a_spend_matching_a_transfer_leg_still_books(db_isolated):
    from collections import Counter

    from backend.services import accounts as accounts_service
    from backend.services.ynab_migration import apply

    categories_service.ensure_builtins_seeded()
    cur = ledger_service.base_currency()
    a, b = (
        accounts_service.create_account(
            {"name": n, "type": "cash", "currency": cur, "initial_balance": 0}
        )["id"]
        for n in ("A", "B")
    )

    def twin(ref):
        return {
            "date": "2024-03-01",
            "kind": "transfer",
            "description": "Transfer : B",
            "from_account_id": a,
            "from_amount": 50.0,
            "from_currency": cur,
            "to_account_id": b,
            "to_amount": 50.0,
            "to_currency": cur,
            "external_refs": [{"source": "ynab", "ref": ref}],
        }

    spend = {
        "date": "2024-03-01",
        "kind": "expense",
        "description": "Cash withdrawal",
        "from_account_id": a,
        "from_amount": 50.0,
        "from_currency": cur,
        "external_refs": [{"source": "ynab", "ref": "t2"}],
    }
    tally, problems = Counter(), []
    apply._book_chunk([twin("t1")], "b1", tally, problems)
    apply._book_chunk([spend, twin("t1")], "b2", tally, problems)
    assert not problems, problems
    assert (tally["created"], tally["already_present"]) == (2, 1)


def test_a_budget_mismatch_names_the_first_month_it_appears(ynab):
    assert run()["status"] == "done"
    from backend.core.user_time import user_today

    t = user_today()
    i = t.year * 12 + t.month - 1 - 2  # the stub's oldest month
    month = f"{i // 12:04d}-{i % 12 + 1:02d}"
    env = next(e for e in envelopes_service.all_envelopes() if e.get("category_ids"))
    current = planning.budget_month(month)
    row = next(
        r for g in current["groups"] for r in g["categories"] if r.get("envelope_id") == env["id"]
    )
    planning.apply_budget_change(
        {"type": "assign", "month": month, "envelope_id": env["id"], "amount": row["assigned"] + 7}
    )

    job = run("verify")
    history = job["verification"]["mismatches"]
    assert any(r["check"].endswith(f"first differs {month}") for r in history), history


def test_a_negative_month_assignment_is_written_as_is(db_isolated, monkeypatch):
    server, base_url, _ = ynab_stub.serve(negative_assignment=True)
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    try:
        job = run()
        assert job["status"] == "done", job["error"]
        from backend.core.user_time import user_today

        t = user_today()
        i = t.year * 12 + t.month - 1 - 1  # the stub's negative month
        month = f"{i // 12:04d}-{i % 12 + 1:02d}"
        rows = [r for g in planning.budget_month(month)["groups"] for r in g["categories"]]
        assert next(r for r in rows if r["label"] == "Fun Money")["assigned"] == -10.0
        assert job["verification"]["ok"], job["verification"]["mismatches"]
    finally:
        server.shutdown()


@pytest.mark.parametrize("as_given,expected", [(True, None), (False, "learned")])
def test_a_blank_category_stays_blank_when_labels_are_given(db_isolated, as_given, expected):
    # A YNAB Ready-to-Assign inflow is uncategorized on purpose; the payee default learned
    # from the same payee's categorized row must not fill it (it inflated envelopes by 100k+).
    from backend.services import accounts as accounts_service

    categories_service.ensure_builtins_seeded()
    cur = ledger_service.base_currency()
    a = accounts_service.create_account(
        {"name": "A", "type": "cash", "currency": cur, "initial_balance": 0}
    )["id"]
    cat_id = planning.apply_budget_change(
        {"type": "category_create", "label": "Gifts In", "kind": "expense"}
    )["id"]

    def inflow(day, category):
        return {"date": f"2024-03-0{day}", "kind": "income", "description": "Friend",
                "to_account_id": a, "to_amount": 20.0 + day, "to_currency": cur,
                "category": category}  # fmt: skip

    created, _ = ledger_service.bulk_create_transactions(
        [inflow(1, cat_id)], force=True, labels_as_given=as_given
    )
    created, _ = ledger_service.bulk_create_transactions(
        [inflow(2, None)], force=True, labels_as_given=as_given
    )
    got = ledger_service.transactions.find_by_id(created[0]["id"]).get("category")
    assert got == (cat_id if expected == "learned" else None)


def test_a_card_payment_category_maps_to_the_cards_payment_envelope(ynab):
    from backend.services.ynab_migration import apply

    assert run()["status"] == "done"
    plan = {"card_categories": {"ynab-cat": "Visa"}}
    visa = next(
        a
        for a in ledger_service.accounts.query_accounts(include_archived=True)
        if a["name"] == "Visa"
    )
    assert apply.card_envelopes(plan) == {
        "ynab-cat": {"category": None, "envelope": visa["payment_envelope_id"]}
    }


def test_a_run_that_cannot_be_queued_is_failed_not_left_running(ynab, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("pool is shut down")

    monkeypatch.setattr(job_module._POOL, "submit", refuse)
    with pytest.raises(RuntimeError):
        ynab_migration.start("preview", ynab_stub.PLAN)
    assert ynab_migration.status()["job"]["status"] == "failed"
    monkeypatch.undo()
    ynab_migration.start("preview", ynab_stub.PLAN)  # not blocked by a stuck "running" row


def test_a_failure_to_record_the_finish_is_contained(ynab, monkeypatch):
    from backend.services import import_runs

    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(import_runs, "finish", boom)
    job_module._run(
        import_runs.start("ynab", "preview", ynab_stub.PLAN)["id"], "preview", "x", False
    )


def test_an_unsummarizable_report_does_not_fail_a_completed_run(ynab, monkeypatch):
    monkeypatch.setattr(job_module.report, "outcome", lambda *a: 1 / 0)
    job = run("preview")
    assert job["status"] == "done" and job["outcome"] == ""
