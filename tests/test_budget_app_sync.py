"""The two-way sync works for ``budget_app.current()``. A second app with in-memory stores and a stub
client goes through the real engine here, and YNAB's own stores, lock and server are never touched.
All names and figures are invented."""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import scheduler
from backend.core import audit_context
from backend.core.errors import ConflictError, ExternalServiceError
from backend.db.actual_sync_store import ActualBaselineStore
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.db.ynab_sync_store import (
    SyncBaselineStore,
    SyncConflictStore,
    SyncMirrorStore,
    SyncStateStore,
    YnabBaselineStore,
    YnabConflictStore,
    YnabMirrorStore,
    YnabSyncStateStore,
)
from backend.services import accounts as accounts_service
from backend.services import budget_app, import_runs, ynab_sync
from backend.services import ledger as ledger_service
from backend.services import profile as profile_service
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import YNAB, BudgetApp, SyncStores, current, using
from backend.services.ynab_migration import job
from backend.services.ynab_sync import (
    category_base,
    client,
    conflicts,
    resolve,
    runner,
    units,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.client import Budget, RateCapReached
from tests.flags import flags

pytestmark = [pytest.mark.flags(ynab_import_and_sync=True), pytest.mark.usefixtures("db_isolated")]

SYNC_DIR = Path(client.__file__).parent
DONE = 30  # seconds a worker gets to finish before a test gives up on it


class _InMemory:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def all(self) -> list[dict]:
        return list(self.rows.values())

    def find_by_id(self, doc_id: str) -> dict | None:
        return self.rows.get(doc_id)

    def upsert(self, doc: dict) -> dict:
        self.rows[doc["id"]] = doc
        return doc

    def delete(self, doc_id: str) -> None:
        self.rows.pop(doc_id, None)

    def replace_all(self, docs: list[dict]) -> None:
        self.rows = {d["id"]: d for d in docs}


class _State(_InMemory, SyncStateStore):
    pass


class _Mirror(_InMemory, SyncMirrorStore):
    pass


class _Baselines(_InMemory, SyncBaselineStore):
    pass


class _Conflicts(_InMemory, SyncConflictStore):
    pass


def _plan(*txns: dict) -> dict:
    return {
        "plan": {
            "accounts": [
                {
                    "id": "ya1",
                    "name": "ACME Checking",
                    "type": "checking",
                    "on_budget": True,
                    "closed": False,
                    "deleted": False,
                    "balance": 0,
                    "cleared_balance": 0,
                    "transfer_payee_id": "pa1",
                }
            ],
            "payees": [{"id": "p1", "name": "ACME Coffee"}],
            "category_groups": [],
            "categories": [],
            "months": [],
            "transactions": list(txns),
            "subtransactions": [],
        },
        "server_knowledge": 7,
    }


def _coffee() -> dict:
    return {
        "id": "yt1",
        "date": "2026-01-05",
        "amount": -12_500,
        "memo": None,
        "cleared": "cleared",
        "approved": True,
        "account_id": "ya1",
        "payee_id": "p1",
        "category_id": None,
        "transfer_account_id": None,
        "transfer_transaction_id": None,
        "deleted": False,
    }


class _Client:
    """Speaks the engine's internal format; what a read returns is set per test."""

    def __init__(self, budget: Budget, plan: dict):
        self.budget, self.plan, self.writes = budget, plan, []

    def read_plan(self, knowledge):
        self.budget.spend()
        return self.plan if knowledge is None else _plan()

    def save_transactions(self, method, rows):
        self.budget.spend()
        self.writes.append((method, rows))
        return [{**self.plan["plan"]["transactions"][0], **r} for r in rows]


@dataclasses.dataclass(frozen=True)
class _Acme(BudgetApp):
    opened: list = dataclasses.field(default_factory=list)  # every client it opened


def _app(key: str = "acme", plan: dict | None = None, **over) -> _Acme:
    state, mirror, baselines, reviews = _State(), _Mirror(), _Baselines(), _Conflicts()
    opened: list[_Client] = []

    @contextmanager
    def open_client(_plan_id, budget):
        made = _Client(budget, plan or _plan())
        opened.append(made)
        yield made

    fields = {f.name: getattr(YNAB, f.name) for f in dataclasses.fields(BudgetApp)}
    return _Acme(
        **{
            **fields,
            "key": key,
            "name": "Acme Budget",
            "source": f"{key}_budget",
            "surface": f"{key}_sync",
            "config_store": lambda: SimpleNamespace(is_configured=lambda: True),
            "stores": SyncStores(
                state=lambda: state,
                mirror=lambda: mirror,
                baselines=lambda: baselines,
                conflicts=lambda: reviews,
            ),
            "open_client": open_client,
            "lock": threading.Lock(),
            "is_on": lambda: True,
            "opened": opened,
            **over,
        }
    )


def _on(app: BudgetApp, **over) -> None:
    """Enabled, due, and pointed at a plan, one way so nothing here asks to be sent."""
    with using(app):
        sync_state.save(
            {
                **sync_state.blank(),
                "plan_id": "plan",
                "plan_name": "ACME",
                "currency": "USD",
                "enabled": True,
                "direction": "from_ynab",
                **over,
            }
        )


def _state(app: BudgetApp) -> dict:
    with using(app):
        return sync_state.load()


def _wait_free(app: BudgetApp) -> None:
    deadline = time.time() + DONE
    while app.lock.locked() and time.time() < deadline:
        time.sleep(0.01)
    assert not app.lock.locked()


def _ynab_untouched() -> None:
    assert not YNAB.lock.locked()
    for store in (
        YnabSyncStateStore(),
        YnabMirrorStore(),
        YnabBaselineStore(),
        YnabConflictStore(),
    ):
        assert store.all() == []
    assert import_runs.list_runs("ynab", include_archived=True) == []


def _account(app: BudgetApp) -> dict:
    profile = profile_service.read_profile()
    profile["base_currency"] = "USD"
    profile_service.write_profile(profile)
    return accounts_service.create_account(
        {
            "name": "ACME Checking",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0.0,
            "initial_balance_date": "2000-01-01",
            "external_ids": [{"source": app.source, "ref": "ya1", "name": "ACME Checking"}],
        }
    )


# ── what stays YNAB's, as it was ──


def test_ynab_keeps_its_stored_values():
    assert (YNAB.key, YNAB.source, YNAB.surface) == ("ynab", "ynab", "ynab_sync")
    assert ynab_sync.SURFACE == "ynab_sync"
    assert sync_state.request_cap() == 40
    assert YNAB.caps.category_request_cap == 20
    tables = {
        s()._table.name
        for s in (
            YNAB.stores.state,
            YNAB.stores.mirror,
            YNAB.stores.baselines,
            YNAB.stores.conflicts,
        )
    }
    assert tables == {
        "ynab_sync_state",
        "ynab_sync_mirror",
        "ynab_sync_baselines",
        "ynab_sync_conflicts",
    }


def test_each_app_stores_in_its_own_table():
    assert ActualBaselineStore()._table.name == "actual_sync_baselines"
    assert isinstance(ActualBaselineStore(), SyncBaselineStore)
    assert not isinstance(ActualBaselineStore(), YnabBaselineStore)
    ActualBaselineStore().upsert({"id": "c:y1", "plan": "p"})
    assert ActualBaselineStore().pairs("p")[0].keys() == {"y1"}
    assert YnabBaselineStore().all() == []


def test_a_narrowed_request_cap_is_ynabs_alone(monkeypatch):
    monkeypatch.setattr(sync_state, "REQUEST_CAP", 3)
    assert sync_state.request_cap() == 3
    other = _app(caps=dataclasses.replace(YNAB.caps, request_cap=999))
    with using(other):
        assert sync_state.request_cap() == 999


def test_the_category_cap_is_the_apps():
    base = category_base.Base.__new__(category_base.Base)
    base.client = SimpleNamespace(budget=SimpleNamespace(used=19))
    assert base.send(lambda: "sent") == "sent"
    base.client.budget.used = 20
    with pytest.raises(RateCapReached):
        base.send(lambda: "sent")
    with using(_app(caps=dataclasses.replace(YNAB.caps, category_request_cap=21))):
        assert base.send(lambda: "sent") == "sent"


# ── read at call time ──


def test_stores_and_wording_follow_the_app_at_each_call():
    app = _app()
    _on(app)
    with using(app):
        item = conflicts.file(conflicts.CONFLICT, "t1", "ACME", "Why", None, None, entity="txn")
        assert conflicts.get(item["id"])["title"] == "ACME"
        assert sync_state.load()["plan_name"] == "ACME"
        assert runner._summary(Counter(sent=1), 0) == "1 sent to Acme Budget"
        assert str(RateCapReached()).startswith("Acme Budget request limit")
    assert str(RateCapReached()).startswith("YNAB request limit")
    assert conflicts.get(item["id"]) is None
    assert sync_state.load()["plan_id"] is None
    _ynab_untouched()


def _module_level_calls(tree: ast.Module) -> list[int]:
    """Lines where ``current()`` runs at import, or in a default argument."""
    found = []

    def calls_current(node: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Call) and getattr(n.func, "id", "") == "current"
            for n in ast.walk(node)
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            found += [d.lineno for d in node.args.defaults + node.args.kw_defaults if d and calls_current(d)]  # fmt: skip
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)) and calls_current(node):
            found.append(node.lineno)
    return found


def test_nothing_freezes_the_app_at_import():
    for path in SYNC_DIR.glob("*.py"):
        assert _module_level_calls(ast.parse(path.read_text("utf-8"))) == [], path.name


def _people_read(path: Path) -> list[str]:
    """Every string but a docstring or a log line with the app's name spelled out in it."""
    tree = ast.parse(path.read_text("utf-8"))
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
    }

    def logged(node) -> bool:
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("info", "exception"):  # fmt: skip
                return True
        return False

    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "YNAB" in n.value
        and id(n) not in docs
        and not logged(n)
    ]


def test_the_only_spelled_out_name_is_in_ynabs_own_client():
    spelled = {p.name: _people_read(p) for p in SYNC_DIR.glob("*.py")}
    assert {k: v for k, v in spelled.items() if v} == {
        "client.py": [
            "refused to send a malformed id to YNAB",
            "YNAB rate limit hit; the rest waits for the next run",
        ]
    }


# ── the run, through the real engine ──


def test_a_run_books_and_skips_under_the_apps_own_source_and_surface():
    app = _app(plan=_plan(_coffee()))
    _on(app)
    _account(app)
    with using(app):
        runner._locked_run("manual")
    assert _state(app)["last_run"]["ok"], _state(app)["last_run"]["error"]
    assert _state(app)["last_run"]["summary"] == "1 pulled"

    (row,) = [t for t in ledger_service.transactions.all() if t["description"] == "ACME Coffee"]
    assert row["external_refs"] == [{"source": "acme_budget", "ref": "yt1"}]
    assert row["source"] == "acme_budget"
    audit = TransactionAuditStore()
    upto = audit.max_seq()
    events = audit.since(-1, upto=upto, not_surface="nothing")
    assert [e["surface"] for e in events if e["txn_id"] == row["id"]] == ["acme_sync"]

    with using(app):
        assert row["id"] not in units.audit_touched(
            -1, upto
        )  # its own write isn't one to send back
    assert row["id"] in units.audit_touched(-1, upto)  # to YNAB, it is someone else's edit
    assert app.stores.mirror().rows.keys() == {"yt1"}
    assert app.stores.baselines().rows["t:" + row["id"]]["refs"] == ["yt1"]
    assert YnabMirrorStore().all() == []

    runs = import_runs.list_runs("acme", include_archived=True)
    assert [(r["kind"], r["trigger"]) for r in runs] == [("sync", "manual")]
    assert import_runs.list_runs("ynab", include_archived=True) == []

    with using(app):
        runner._locked_run("manual")
    assert _state(app)["last_run"]["summary"] == "Nothing changed"
    assert all(c.writes == [] for c in app.opened)


def test_a_ynab_run_stamps_what_its_scan_skips(monkeypatch):
    stamped, skipped = [], []
    monkeypatch.setattr(
        runner,
        "_execute",
        lambda st, tally: stamped.append(audit_context.get_audit_scope().surface),
    )
    runner._locked_run("scheduled")
    monkeypatch.setattr(
        TransactionAuditStore,
        "since",
        lambda self, seq, *, upto, not_surface: skipped.append(not_surface) or [],
    )
    monkeypatch.setattr(
        TransactionAuditStore,
        "edits_of",
        lambda self, ids, *, not_surface: skipped.append(not_surface) or {},
    )
    units.audit_touched(-1, 0)
    units.first_run_bases([])
    assert stamped == ["ynab_sync"] and skipped == ["ynab_sync", "ynab_sync"]


# ── every thread hop ──


def test_sync_now_runs_on_the_pool_for_the_app_that_started_it():
    app = _app(plan=_plan(_coffee()))
    _on(app)
    _account(app)
    with using(app):
        started = runner.start_now()
    assert started["run_id"] and started["running"]
    _wait_free(app)

    assert _state(app)["last_run"]["summary"] == "1 pulled"
    assert import_runs.get(started["run_id"])["status"] == "done"
    with using(app):
        assert runner.status()["run_id"] is None
    assert len(app.opened) == 1
    _ynab_untouched()


def test_the_pool_is_shared_and_each_run_holds_only_its_own_lock():
    first, second = _app("acme"), _app("zenith")
    _on(first)
    _on(second)
    entered, leave = threading.Event(), threading.Event()
    open_first = first.open_client

    @contextmanager
    def held_open(plan_id, budget):
        entered.set()
        assert leave.wait(DONE)
        with open_first(plan_id, budget) as made:
            yield made

    held = dataclasses.replace(first, open_client=held_open)
    with using(held):
        run_id = runner.start_now()["run_id"]
    assert entered.wait(DONE)

    with using(second):
        queued = runner.start_now()  # behind the first on the one pool, on its own lock
    assert queued["running"] and queued["run_id"] != run_id
    assert not YNAB.lock.locked() and not runner.is_running()
    assert runner.status()["run_id"] is None
    with using(held):
        assert runner.is_running() and runner.status()["run_id"] == run_id
        assert runner._lock.locked()

    assert second.lock.locked()  # waiting its turn, it reads as running
    assert not second.opened
    leave.set()
    _wait_free(held)
    _wait_free(second)
    assert _state(held)["last_run"]["ok"] and _state(second)["last_run"]["ok"]
    _ynab_untouched()


def test_the_proxy_answers_for_the_app_of_the_moment():
    app = _app()
    with using(app):
        assert runner._lock.acquire(blocking=False)
        assert runner._lock.locked() and not YNAB.lock.locked()
    assert not runner._lock.locked()
    assert runner._lock.acquire(blocking=False)
    assert YNAB.lock.locked() and app.lock.locked()
    runner._lock.release()
    assert not YNAB.lock.locked() and app.lock.locked()
    with using(app):
        runner._lock.release()
    assert not app.lock.locked()


def test_one_apps_running_sync_holds_up_only_that_apps_controls():
    app = _app()
    _on(app)
    assert app.lock.acquire(blocking=False)
    try:
        with using(app):
            with pytest.raises(ConflictError):
                runner.update_settings(False, None, None)
            with pytest.raises(ConflictError):
                runner.start_now()
        assert runner.update_settings(False, None, None)["running"] is False
    finally:
        app.lock.release()
    assert YNAB.lock.acquire(blocking=False)
    try:
        with using(app):
            assert runner.update_settings(False, None, None)["running"] is False
    finally:
        YNAB.lock.release()


def test_turning_the_sync_on_from_an_import_writes_that_apps_state():
    app = _app(reader=SimpleNamespace(list_plans=lambda store: [
        {"id": "plan", "name": "ACME", "currency": "usd"}
    ]))  # fmt: skip
    _account(app)
    lines: list[str] = []
    with using(app):
        job._turn_on_sync(SimpleNamespace(log=lines.append), "plan", "both")
    assert lines == ["Sync turned on: both ways."]
    st = _state(app)
    assert (st["enabled"], st["plan_id"], st["currency"]) == (True, "plan", "USD")
    _ynab_untouched()


# ── the scheduler ──


def _tick() -> None:
    asyncio.run(scheduler._run_ynab_sync())


def test_the_schedulers_tick_runs_each_app_with_no_context_to_go_on(monkeypatch):
    app = _app(plan=_plan(_coffee()))
    _on(app)
    _account(app)
    monkeypatch.setitem(budget_app._apps, app.key, app)
    assert current() is YNAB
    _tick()
    assert current() is YNAB

    last = _state(app)["last_run"]
    assert (last["trigger"], last["summary"]) == ("scheduled", "1 pulled")
    assert not app.lock.locked()
    _ynab_untouched()


def test_a_tick_runs_only_the_apps_that_are_due_and_on(monkeypatch):
    def never():
        raise AssertionError("an app that is off or doesn't sync is never read")

    off = _app("off", is_on=lambda: False)
    off = dataclasses.replace(off, stores=dataclasses.replace(off.stores, state=never))
    no_sync = _app("nosync", syncs=False)
    no_sync = dataclasses.replace(no_sync, stores=dataclasses.replace(no_sync.stores, state=never))
    idle = _app("idle")  # its schedule is manual
    _on(idle, interval="manual")
    for app in (off, no_sync, idle):
        monkeypatch.setitem(budget_app._apps, app.key, app)
    assert ynab_sync.run_if_due() is False
    assert idle.opened == []
    _ynab_untouched()


@pytest.mark.flags(actual_budget_sync=True)
def test_actual_is_in_the_tick_beside_ynab():
    assert ACTUAL.syncs is True
    assert ACTUAL in budget_app.syncing_apps()
    assert YNAB in budget_app.syncing_apps()


def test_ynab_stays_out_of_the_tick_with_its_flag_off():
    with flags(ynab_import_and_sync=False):
        assert YNAB not in budget_app.syncing_apps()
        assert ynab_sync.run_if_due() is False


def test_one_app_failing_its_tick_leaves_the_next_to_run_and_is_still_raised(monkeypatch):
    boom = RuntimeError("ACME is down")

    def broken():
        raise boom

    bad = _app("bad")
    bad = dataclasses.replace(bad, stores=dataclasses.replace(bad.stores, state=broken))
    good = _app("good")
    _on(good)
    monkeypatch.setitem(budget_app._apps, bad.key, bad)
    monkeypatch.setitem(budget_app._apps, good.key, good)
    with pytest.raises(RuntimeError) as caught:
        ynab_sync.run_if_due()
    assert caught.value is boom
    assert _state(good)["last_run"]["ok"]
    assert current() is YNAB


# ── the client's lifetime ──


def test_a_run_opens_its_client_once_and_a_failure_comes_through_unchanged(monkeypatch):
    boom = RuntimeError("ACME is down")
    closed: list[bool] = []
    used: list[Budget] = []

    @contextmanager
    def open_client(_plan_id, budget):
        used.append(budget)
        try:
            yield SimpleNamespace(budget=budget)
        finally:
            closed.append(True)

    def fail(_st, _tally, _client):
        _client.budget.spend(2)
        raise boom

    monkeypatch.setattr(runner, "_reconcile", fail)
    tally: Counter = Counter()
    with using(dataclasses.replace(_app(), open_client=open_client)):
        with pytest.raises(RuntimeError) as caught:
            runner._execute({"plan_id": "plan"}, tally)
    assert caught.value is boom
    assert closed == [True] and len(used) == 1
    assert tally["requests"] == 2  # counted before the client closes


def test_ynabs_client_is_built_once_and_is_not_closed_by_anything():
    budget = Budget(40)
    with YNAB.open_client("plan", budget) as made:
        assert isinstance(made, client.YnabClient) and made.budget is budget
    boom = RuntimeError("down")
    with pytest.raises(RuntimeError) as caught:
        with YNAB.open_client("plan", budget):
            raise boom
    assert caught.value is boom


def test_a_malformed_plan_stops_a_ynab_run_before_it_reads():
    with pytest.raises(ExternalServiceError):
        runner._execute({"plan_id": "not an id!"}, Counter())


def test_settling_a_review_item_uses_the_apps_client_lock_and_surface(monkeypatch):
    app = _app()
    _on(app, direction="both")
    seen: dict = {}

    def settle(_ctx, _item, _keep, _client, _base):
        seen.update(lock=app.lock.locked(), ynab=YNAB.lock.locked(), client=_client)
        seen["surface"] = audit_context.get_audit_scope().surface
        _client.budget.spend()

    monkeypatch.setattr(resolve, "_transaction", settle)
    with using(app):
        item = conflicts.file(conflicts.CONFLICT, "t1", "ACME", "Why", None, None, entity="txn")
        after = resolve.resolve(item["id"], "onw")
    assert after["conflicts"] == []
    assert seen["lock"] is True and seen["ynab"] is False and seen["surface"] == "acme_sync"
    assert app.opened == [seen["client"]]
    assert seen["client"].budget.used == 1
    assert not app.lock.locked()
    _ynab_untouched()


def test_a_failure_settling_an_item_releases_the_apps_lock_and_comes_through(monkeypatch):
    app = _app()
    _on(app, direction="both")
    boom = RuntimeError("ACME is down")

    def fail(*_a):
        raise boom

    monkeypatch.setattr(resolve, "_transaction", fail)
    with using(app):
        item = conflicts.file(conflicts.CONFLICT, "t1", "ACME", "Why", None, None, entity="txn")
        with pytest.raises(RuntimeError) as caught:
            resolve.resolve(item["id"], "onw")
    assert caught.value is boom
    assert not app.lock.locked()


# ── YNAB's questions stay YNAB's ──


def test_whether_a_ynab_plan_is_in_sync_is_asked_of_ynab_whatever_the_app():
    _on(YNAB, plan_id="plan-y")
    other = _app()
    _on(other, plan_id="plan-o")
    with using(other):
        assert ynab_sync.plan_in_sync("plan-y")
        assert not ynab_sync.plan_in_sync("plan-o")
