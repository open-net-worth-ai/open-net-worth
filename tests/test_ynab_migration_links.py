"""Importing a YNAB plan into a book that already holds some of its accounts: a YNAB account can
be linked to an account here instead of creating one. The rows both sides hold are matched by
the ledger's own engine, so nothing is booked twice, and the two-way sync pairs with the linked
account. Against an isolated database and the synthetic YNAB API (tests/ynab_stub.py)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.core.errors import ValidationError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import profile as profile_service
from backend.services import ynab_migration, ynab_sync
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_migration.mapping import SOURCE
from backend.services.ynab_sync import runner
from tests import ynab_stub
from tests.test_ynab_migration_service import run

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)

CHECKING = f"{1:08d}-0000-0000-0000-000000000000"
VISA = f"{3:08d}-0000-0000-0000-000000000000"


@pytest.fixture
def ynab(db_isolated, monkeypatch):
    server, base_url, calls = ynab_stub.serve()
    server.calls = calls
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    profile = profile_service.read_profile()
    profile["base_currency"] = "USD"
    profile_service.write_profile(profile)
    yield server
    server.shutdown()


def day(month_offset: int, day_: int) -> str:
    today = date.today()
    i = today.year * 12 + today.month - 1 + month_offset
    return f"{i // 12:04d}-{i % 12 + 1:02d}-{day_:02d}"


def account(name="Everyday", type_="cash", currency="USD", opening=5000.0) -> dict:
    return accounts_service.create_account(
        {
            "name": name,
            "type": type_,
            "currency": currency,
            "initial_balance": opening,
            "initial_balance_date": day(-2, 1),
        }
    )


def book(acct: dict, when: str, amount: float, description: str) -> dict:
    leg = "to" if amount > 0 else "from"
    return ledger_service.create_transaction(
        {
            "kind": "income" if amount > 0 else "expense",
            "date": when,
            "description": description,
            f"{leg}_account_id": acct["id"],
            f"{leg}_amount": abs(amount),
            f"{leg}_currency": "USD",
            "cleared": True,
        }
    )


def everyday_with_history() -> tuple[dict, list[dict]]:
    """The account as the person kept it by hand: three rows YNAB also has (one of YNAB's two
    identical grocery runs among them)."""
    acct = account()
    rows = [
        book(acct, day(-2, 2), 3000, "Employer"),
        book(acct, day(-2, 3), -1200, "Landlord"),
        book(acct, day(-2, 5), -80.5, "Grocer"),
    ]
    return acct, rows


def on(acct_id: str) -> list[dict]:
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and acct_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def preview_account(job: dict, name: str) -> dict:
    return next(a for a in job["preview"]["accounts"] if a["name"] == name)


def test_the_review_offers_only_open_accounts_of_a_matching_type_and_currency(ynab):
    everyday = account()
    account("Card", "credit", opening=0)
    account("Euro cash", currency="EUR", opening=0)
    account("Term deposit", "fixed_deposit", opening=0)
    job = run("preview")
    assert job["status"] == "done", job["error"]
    offered = {a["name"]: [c["name"] for c in a["candidates"]] for a in job["preview"]["accounts"]}
    assert offered["Checking"] == ["Everyday"] and offered["Savings"] == ["Everyday"]
    assert offered["Visa"] == ["Card"]
    assert offered["Brokerage"] == ["Term deposit"]  # a tracking asset: any off-budget asset
    assert offered["Old Account"] == []  # closed in YNAB
    checking = preview_account(job, "Checking")
    assert checking["link"] is None and checking["match"] is None
    assert checking["id"] == CHECKING
    assert everyday["id"] in {c["id"] for c in preview_account(job, "Savings")["candidates"]}


def test_the_preview_says_what_a_linked_account_would_match_and_add(ynab):
    everyday, _ = everyday_with_history()
    book(everyday, day(-1, 20), -4.5, "Corner cafe")  # a row of its own, not in YNAB
    job = run("preview", account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    assert job["status"] == "done", job["error"]
    checking = preview_account(job, "Checking")
    assert checking["link"] == {"id": everyday["id"], "name": "Everyday"} and checking["chosen"]
    match = checking["match"]
    assert (match["matched"], match["to_check"], match["own_only"]) == (3, 0, 1)
    assert match["matched"] + match["added"] == checking["transactions"]
    assert ledger_service.accounts.find_by_id(everyday["id"]).get("external_ids") == []


def test_a_linked_account_imports_with_no_duplicates_and_verifies_clean(ynab):
    everyday, mine = everyday_with_history()
    job = run(account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    assert job["status"] == "done", job["error"]
    assert job["verification"]["ok"], job["verification"]["mismatches"]
    assert job["counts"]["accounts"]["created"] == 4
    linked = ledger_service.accounts.find_by_id(everyday["id"])
    assert [(e["source"], e["ref"]) for e in linked["external_ids"]] == [(SOURCE, CHECKING)]
    assert not any(a["name"] == "Checking" for a in ledger_service.accounts.query_accounts())
    rows = on(everyday["id"])
    landlord = [t for t in rows if t["description"] == "Landlord" and t["date"] == day(-2, 3)]
    assert [t["id"] for t in landlord] == [mine[1]["id"]]  # matched, not booked again
    assert len([t for t in rows if t["description"] == "Grocer" and t["date"] == day(-2, 5)]) == 2
    for t in mine:
        refs = ledger_service.transactions.find_by_id(t["id"])["external_refs"]
        assert any(r["source"] == SOURCE for r in refs)
    assert job["counts"]["transactions"]["existing"] == 3


def test_importing_again_books_nothing_more(ynab):
    everyday, _ = everyday_with_history()
    assert run(account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)["status"] == "done"
    before = len(ledger_service.transactions.all())
    again = run(confirm_nonempty=True)
    assert again["status"] == "done", again["error"]
    assert again["counts"]["transactions"]["created"] == 0
    assert again["counts"]["accounts"]["created"] == 0
    assert len(ledger_service.transactions.all()) == before
    assert preview_account(again, "Checking")["link"]["id"] == everyday["id"]


def test_the_sync_pairs_with_the_linked_account_and_sends_only_its_own_rows(ynab):
    everyday, _ = everyday_with_history()
    cafe = book(everyday, day(0, 1), -4.5, "Corner cafe")
    job = run(account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    assert job["status"] == "done", job["error"]
    ynab.writes.clear()
    ynab_sync.update_settings(True, "1h", ynab_stub.PLAN)
    runner._locked_run("manual")
    last = ynab_sync.status()["last_run"]
    assert last["ok"], last["error"]
    creates = [
        t
        for w in ynab.writes
        if w[0] == "POST" and w[1].endswith("/transactions")
        for t in w[2].get("transactions") or [w[2].get("transaction")]
    ]
    assert [(t["payee_name"], t["amount"], t["account_id"]) for t in creates] == [
        ("Corner cafe", -4500, CHECKING)
    ]
    assert any(
        r["source"] == SOURCE
        for r in ledger_service.transactions.find_by_id(cafe["id"])["external_refs"]
    )
    assert ynab_sync.status()["conflicts"] == []


def test_a_possible_match_is_added_unless_the_person_says_it_is_the_same(ynab):
    everyday = account()
    # YNAB's rent on the same day, worded differently: the engine is not sure.
    rent = book(everyday, day(-2, 3), -1200, "Rent payment")
    preview = run("preview", account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    match = preview_account(preview, "Checking")["match"]
    assert match["to_check"] == 1, match
    (pair,) = match["pairs"]
    assert pair["existing"]["id"] == rent["id"] and pair["amount"] == -1200
    job = run(
        account_links={CHECKING: everyday["id"]},
        same_as={pair["ref"]: rent["id"]},
        confirm_nonempty=True,
    )
    assert job["status"] == "done", job["error"]
    refs = ledger_service.transactions.find_by_id(rent["id"])["external_refs"]
    assert [r["ref"] for r in refs if r["source"] == SOURCE] == [pair["ref"]]
    assert not [
        t for t in on(everyday["id"]) if t["description"] == "Landlord" and t["date"] == day(-2, 3)
    ]


def test_an_account_that_does_not_fit_is_refused(ynab):
    card = account("Card", "credit", opening=0)
    job = run("preview", account_links={CHECKING: card["id"]}, confirm_nonempty=True)
    assert job["status"] == "failed" and "cannot be linked" in job["error"]
    everyday = account()
    job = run(
        "preview",
        account_links={
            CHECKING: everyday["id"],
            f"{2:08d}-0000-0000-0000-000000000000": everyday["id"],
        },
    )
    assert job["status"] == "failed" and "chosen for two" in job["error"]
    assert ledger_service.accounts.find_by_id(everyday["id"]).get("external_ids") == []


def test_a_verify_run_takes_no_links(ynab):
    with pytest.raises(ValidationError):
        ynab_migration.start("verify", ynab_stub.PLAN, account_links={CHECKING: "x"})


def test_the_start_route_carries_the_links(ynab):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.ynab_api import router

    everyday, _ = everyday_with_history()
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(
        "/api/ynab/migration/preview",
        json={"plan_id": ynab_stub.PLAN, "account_links": {CHECKING: everyday["id"]}},
    )
    assert r.status_code == 202, r.text
    import time

    deadline = time.time() + 90
    while ynab_migration.status()["job"]["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    job = ynab_migration.status()["job"]
    assert preview_account(job, "Checking")["match"]["matched"] == 3


def test_an_unconfirmed_possible_match_is_booked_beside_the_row_here(ynab):
    everyday = account()
    rent = book(everyday, day(-2, 3), -1200, "Rent payment")
    job = run(account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    assert job["status"] == "done", job["error"]
    assert not any(
        r["source"] == SOURCE
        for r in ledger_service.transactions.find_by_id(rent["id"])["external_refs"] or []
    )
    same_day = sorted(t["description"] for t in on(everyday["id"]) if t["date"] == day(-2, 3))
    assert same_day == ["Landlord", "Rent payment"]
    assert any("1 possible match(es) were added" in e["line"] for e in job["log"])


def test_a_recheck_reuses_its_previews_read_and_a_new_review_reads_ynab_again(ynab):
    everyday, _ = everyday_with_history()
    first = run("preview")
    reads = len(ynab.calls)
    again = run("preview", account_links={CHECKING: everyday["id"]}, recheck_of=first["id"])
    assert again["status"] == "done" and len(ynab.calls) == reads
    assert preview_account(again, "Checking")["match"]["matched"] == 3
    run("preview")
    assert len(ynab.calls) > reads
