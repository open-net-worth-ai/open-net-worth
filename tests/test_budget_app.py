"""``budget_app``: which budget app the engine is working for. The default is YNAB, so everything
written before a second app existed keeps its meaning, and ``using`` follows a job onto its worker
thread. All names are invented."""

from __future__ import annotations

import contextvars
import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.db.ynab_sync_store import YnabBaselineStore, YnabSyncStateStore
from backend.services import budget_app, import_runs, ynab_pairs
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services.budget_app import YNAB, current, using
from backend.services.ynab_migration import (
    apply,
    category_make,
    category_match,
    job,
    links,
    mapping,
    report,
    source,
    verify,
)
from backend.services.ynab_sync.client import Budget, YnabClient

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


def other_app(**over) -> budget_app.BudgetApp:
    config = SimpleNamespace(is_configured=lambda: True)
    state = SimpleNamespace(doc=lambda doc_id: {"id": doc_id, "enabled": False})
    return dataclasses.replace(
        YNAB,
        key="acme",
        name="Acme Budget",
        source="acme_budget",
        config_store=lambda: config,
        stores=dataclasses.replace(YNAB.stores, state=lambda: state),
        **over,
    )


def test_the_default_is_ynab_as_it_always_was():
    app = current()
    assert app is YNAB
    assert (app.key, app.name, app.source, app.surface) == ("ynab", "YNAB", "ynab", "ynab_sync")
    assert app.caps == budget_app.Capabilities(
        future_dates=False,
        edit_splits=False,
        hide_categories=False,
        income_categories=False,
        card_payment_categories=True,
        compares_ready_to_assign=True,
        zero_based_on_fresh_book=True,
        request_cap=40,
        category_request_cap=20,
        flag_trail=True,
    )
    assert app.config_store is YnabConfigStore
    assert mapping.SOURCE == "ynab"
    assert isinstance(app.stores.baselines(), YnabBaselineStore)


def test_using_nests_and_restores():
    first, second = other_app(), dataclasses.replace(other_app(), key="second")
    with using(first):
        with using(second) as inner:
            assert current() is second and inner is second
        assert current() is first
    assert current() is YNAB


def test_using_restores_when_the_work_raises():
    with pytest.raises(ValueError):
        with using(other_app()):
            raise ValueError
    assert current() is YNAB


def test_the_app_crosses_a_thread_only_with_its_context():
    with ThreadPoolExecutor(max_workers=1) as pool, using(other_app()):
        bare = pool.submit(lambda: current().key).result()
        carried = pool.submit(contextvars.copy_context().run, lambda: current().key).result()
    assert (bare, carried) == ("ynab", "acme")


def test_the_job_worker_runs_as_the_app_that_started_it(db_isolated, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(job, "_execute", lambda *a: seen.append(current().key))
    with using(other_app()):
        started = job.start("preview", "plan-1")
    deadline = time.time() + 30
    while import_runs.get(started["id"])["status"] == "running" and time.time() < deadline:
        time.sleep(0.02)
    assert seen == ["acme"]
    assert import_runs.get(started["id"])["provider"] == "acme"


def test_the_job_files_its_status_under_the_current_app(db_isolated):
    run_id = import_runs.start("acme", "preview", "plan-1")["id"]
    with using(other_app()):
        assert job.status() == {"token_saved": True, "job": import_runs.get(run_id)}
    assert job.status()["job"] is None


def test_the_ynab_reader_is_the_source_module_so_its_patches_hold(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(source, "list_plans", lambda store: calls.append(("plans", store)))
    monkeypatch.setattr(
        source, "fetch_accounts", lambda store, plan: calls.append(("accounts", store, plan))
    )
    monkeypatch.setattr(
        source, "fetch_month", lambda store, plan, month: calls.append(("month", plan, month))
    )

    def fetch_plan(store, plan, *, with_budget, progress):
        calls.append(("plan", plan, with_budget, progress))

    monkeypatch.setattr(source, "fetch_plan", fetch_plan)
    reader = YNAB.reader
    tick = object()
    reader.list_plans("s")
    reader.fetch_accounts("s", "p")
    reader.fetch_month("s", "p", "2026-01")
    reader.fetch_plan("s", "p", with_budget=True, progress=tick)
    assert calls == [
        ("plans", "s"),
        ("accounts", "s", "p"),
        ("month", "p", "2026-01"),
        ("plan", "p", True, tick),
    ]


def test_opening_a_ynab_client_is_only_creating_one(db_isolated):
    budget = Budget(3)
    with YNAB.open_client("plan-1", budget) as client:
        assert isinstance(client, YnabClient)
        assert client.plan == "plan-1" and client.budget is budget


def test_the_pairs_live_in_the_current_apps_store():
    saved: list[dict] = []
    baselines = SimpleNamespace(
        pairs=lambda plan: ({plan: {}}, {}),
        declined=lambda plan: {plan},
        pair_documents=lambda: ["doc"],
        upsert=saved.append,
    )
    app = other_app()
    app = dataclasses.replace(
        app, stores=dataclasses.replace(app.stores, baselines=lambda: baselines)
    )
    with using(app):
        assert ynab_pairs.load("p") == ({"p": {}}, {})
        assert ynab_pairs.declined("p") == {"p"}
        assert ynab_pairs.keep_for() == ["doc"]
        ynab_pairs.save([{"id": "c:1"}])
    assert saved == [{"id": "c:1"}]


def test_the_pairs_of_ynab_stay_in_its_store(db_isolated):
    doc = ynab_pairs.group_doc("p", "yg", "g", "Needs", "Needs")
    ynab_pairs.save([doc])
    assert YnabBaselineStore().find_by_id("g:yg")["group"] == "g"
    assert ynab_pairs.load("p")[1]["yg"]["group"] == "g"


def test_the_engine_says_the_name_of_the_current_app():
    book = category_match.Book.__new__(category_match.Book)
    book.labels, book.names = {"fun"}, {"bills"}
    with using(other_app()):
        lines = [
            report.problem({"ref": "r", "kind": "failed"})["hint"],
            category_make.our_group_name("Commitments"),
            book.free_label("Fun"),
            book.free_envelope_name("Bills"),
            verify._count_row(
                "Checking", {"ynab_rows": 3, "expected_rows": 1}, "a1", [], frozenset()
            )["note"],
        ]
    assert lines == [
        "Open Net Worth refused this row. Fix the cause in Acme Budget, or add the transaction by "
        "hand.",
        "Commitments (Acme Budget)",
        "Fun (Acme Budget)",
        "Bills (Acme Budget)",
        "Acme Budget has 3",
    ]


def test_ids_are_tagged_and_read_under_the_current_apps_source():
    plan = {
        "id": "y1",
        "name": "Everyday",
        "type": "checking",
        "on_budget": True,
        "closed": False,
        "deleted": False,
    }
    with using(other_app()):
        built = mapping.build_plan("P", "USD", [plan], [], [], [])
        (account,) = built["accounts"]
        assert account["payload"]["external_ids"][0]["source"] == "acme_budget"
        assert links.ynab_ref({"external_ids": [{"source": "acme_budget", "ref": "y1"}]}) == "y1"
        assert links.ynab_ref({"external_ids": [{"source": "ynab", "ref": "y1"}]}) is None
    assert links.ynab_ref({"external_ids": [{"source": "ynab", "ref": "y1"}]}) == "y1"


def test_every_registered_app_is_listed_once(monkeypatch):
    monkeypatch.setattr(budget_app, "_apps", {"ynab": YNAB})
    other = other_app()
    budget_app.register(other)
    budget_app.register(other)
    assert budget_app.all_apps() == [YNAB, other]


def test_another_apps_sync_being_on_is_found(db_isolated, monkeypatch):
    monkeypatch.setattr(budget_app, "_apps", {"ynab": YNAB})
    other = other_app()
    budget_app.register(other)
    assert budget_app.other_sync_on() is None
    with using(other):
        assert budget_app.other_sync_on() is None
    YnabSyncStateStore().save({"id": "state", "enabled": True})
    assert budget_app.other_sync_on() is None  # YNAB's own sync is not another app's
    with using(other):
        assert budget_app.other_sync_on() is YNAB


def test_another_apps_run_is_found(db_isolated, monkeypatch):
    monkeypatch.setattr(budget_app, "_apps", {"ynab": YNAB})
    other = other_app()
    budget_app.register(other)
    assert budget_app.other_running() is False
    run_id = import_runs.start("ynab", "preview", "plan-1")["id"]
    assert budget_app.other_running() is False  # YNAB's own run is not another app's
    with using(other):
        assert budget_app.other_running() is True
    import_runs.finish(run_id, "done")
    with using(other):
        assert budget_app.other_running() is False


class _FakeReader:
    """One small budget in the engine's format, and a note of every read."""

    def __init__(self):
        self.calls: list[str] = []
        self.account = {
            "id": "y1",
            "name": "Everyday",
            "type": "checking",
            "on_budget": True,
            "closed": False,
            "deleted": False,
            "balance": -12_000,
            "cleared_balance": -12_000,
        }
        self.category = {"id": "c1", "budgeted": 50_000, "activity": -12_000, "balance": 38_000}

    def _month(self) -> str:
        return verify.user_today().strftime("%Y-%m")

    def list_plans(self, store):
        self.calls.append("list_plans")
        return [{"id": "plan-1", "name": "ACME Plan", "currency": "USD"}]

    def fetch_plan(self, store, plan_id, *, with_budget, progress=lambda *_: None):
        self.calls.append("fetch_plan")
        when = f"{self._month()}-02"
        return {
            "accounts": [self.account],
            "groups": [
                {"id": "g1", "name": "Usual", "categories": [{"id": "c1", "name": "Food"}]},
            ],
            "transactions": [
                {
                    "id": "t1",
                    "account_id": "y1",
                    "date": when,
                    "amount": -12_000,
                    "cleared": "cleared",
                    "category_id": "c1",
                    "payee_name": "Corner Market",
                    "subtransactions": [],
                }
            ],
            "months": [{"month": f"{self._month()}-01", "categories": [self.category]}],
        }

    def fetch_accounts(self, store, plan_id):
        self.calls.append("fetch_accounts")
        return [self.account]

    def fetch_month(self, store, plan_id, month):
        self.calls.append("fetch_month")
        return {"categories": [self.category], "to_be_budgeted": -50_000}


@pytest.fixture
def source_is_never_read(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("read YNAB's source under another app")

    for name in ("list_plans", "fetch_plan", "fetch_accounts", "fetch_month", "call"):
        monkeypatch.setattr(source, name, boom)


def test_the_import_and_verify_read_through_the_current_reader(db_isolated, source_is_never_read):
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    reader = _FakeReader()
    app = other_app(reader=reader)
    with using(app):
        record = job._Run(import_runs.start(app.key, "import", "plan-1")["id"])
        job._execute(record, "import", "plan-1", True, {})
        import_runs.finish(record.id, "done")
    run = import_runs.get(record.id)
    assert run["verification"]["ok"], run["verification"]["mismatches"]
    assert reader.calls == ["list_plans", "fetch_plan", "fetch_accounts", "fetch_month"]
    assert run["log"][0]["line"].startswith("Backup taken")

    (account,) = ledger_service.accounts.query_accounts(include_archived=True)
    assert account["external_ids"][0]["source"] == "acme_budget"
    (row,) = [t for t in ledger_service.transactions.all() if t.get("external_refs")]
    assert row["external_refs"][0]["source"] == "acme_budget"

    reader.calls.clear()
    with using(app):
        plan = mapping.build_plan(
            "ACME Plan",
            "USD",
            [reader.account],
            reader.fetch_plan(None, "plan-1", with_budget=True)["groups"],
            reader.fetch_plan(None, "plan-1", with_budget=True)["transactions"],
            [],
        )
        checked = verify.verify(app.config_store(), "plan-1", plan, {}, False)
        assert apply.book_state(plan)[0] == "rerun"
    assert checked["ok"], checked["rows"]
    assert "fetch_accounts" in reader.calls and "fetch_month" not in reader.calls
    assert apply.book_state(plan)[0] == "foreign"  # to YNAB, that account is someone else's


def test_a_rerun_finds_its_accounts_and_rows_under_the_current_source(
    db_isolated, source_is_never_read
):
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    app = other_app(reader=_FakeReader())
    with using(app):
        for _ in range(2):
            record = job._Run(import_runs.start(app.key, "import", "plan-1")["id"])
            job._execute(record, "import", "plan-1", True, {})
            import_runs.finish(record.id, "done")
    run = import_runs.get(record.id)
    assert run["book_state"] == "rerun"
    assert run["counts"]["accounts"]["created"] == 0
    assert run["counts"]["transactions"] == report.count(existing=1)
    assert run["counts"]["categories"]["created"] == 0
