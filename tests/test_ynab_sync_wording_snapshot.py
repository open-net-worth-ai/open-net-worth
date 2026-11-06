"""What a YNAB user reads of the two-way sync beyond ``test_ynab_wording_snapshot``: the run's trail
on the Imports & sync screen, its refusals and phases, and what settling a review item can answer.
Pinned in the same file, by the same rule: every line comes from the real code, and a refactor
never changes one. All figures and names are invented."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from backend.core.errors import ExternalServiceError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import import_runs
from backend.services.ynab_migration import source
from backend.services.ynab_sync import (
    category_new,
    category_pairs,
    category_resolve,
    client,
    conflicts,
    push,
    recorder,
    resolve,
    runner,
    txn_sync,
)
from backend.services.ynab_sync import state as sync_state
from backend.services.ynab_sync.client import RateCapReached
from backend.services.ynab_sync.units import Unit
from tests.test_ynab_wording_snapshot import (  # noqa: F401
    _categories_sync,
    _pair,
    filed,
    pin,
    said,
)

pytestmark = [pytest.mark.flags(ynab_import_and_sync=True), pytest.mark.usefixtures("db_isolated")]


@pytest.fixture
def trail():
    rec = recorder.Recorder()
    token = recorder.activate(rec)
    yield rec
    recorder.deactivate(token)


def _read(rec: recorder.Recorder) -> list[str]:
    return [
        *(c["text"] for c in rec.items["category_changes"]),
        *(n["reason"] for n in rec.items["notes"]),
        *(d["reason"] for d in rec.items["deferred"]),
    ]


def _sync() -> category_new.News:
    sync = _categories_sync()
    sync.meta = {}
    sync.base.told = lambda env_id: False
    sync.base.tell = lambda env_id: None
    sync.book.envelopes = []
    return sync


def _on(**over) -> dict:
    st = {**sync_state.blank(), "plan_id": "plan", "plan_name": "P", "enabled": True, **over}
    sync_state.save(st)
    return st


# ── the trail: what a run says it did ──


def test_trail_of_a_change_to_a_paired_category(trail, monkeypatch):
    sync = _sync()
    cat = sync.book.rows["c1"]
    y, o = sync.ystate(sync.yc["y1"]), sync.ostate(cat)
    sync.pull_name = lambda _cat, name: name
    monkeypatch.setattr(category_pairs.make, "set_hidden", lambda ids, hidden: None)
    sync.pull("y1", _pair(), cat, y, dict(o), ["name"])
    sync.pull("y1", _pair(), cat, {**y, "hidden": True}, dict(o), ["hidden"])
    sync.pull("y1", _pair(), cat, {**y, "hidden": False}, dict(o), ["hidden"])

    monkeypatch.setattr(category_pairs.planning, "apply_budget_change", lambda *a, **k: {})
    sync.book.solo_envelope = lambda cid: {"id": "e1", "name": "Fun jar", "group_id": "g1"}
    sync.book.groups.append({"id": "g2", "name": "Needs"})
    sync.groups.ensure = lambda book, ygid, name: "g2"
    sync.pull_group(cat, {"group": "yg2"}, o)

    sync.say_pushed(y, o, ["name"], {"name": "Fun"})
    sync.say_pushed(y, {**o, "hidden": True}, ["hidden"], {"category_group_id": "yh"})
    sync.say_pushed(y, o, ["hidden"], {"category_group_id": "yg1"})
    sync.say_pushed(y, o, ["group"], {"category_group_id": "yg2"})

    sync.client.update_category = lambda ycid, body: {"id": ycid, **body}
    sync.ours_gone("y1", _pair(), sync.yc["y1"])
    pin("sync_trail_paired", _read(trail))


def test_trail_of_groups(trail, monkeypatch):
    sync = _sync()
    gp = {"id": "g:yg1", "group": "g1", "onw": {"name": "Old"}, "ynab": {"name": "Old"}}
    monkeypatch.setattr(category_new.planning, "apply_budget_change", lambda *a, **k: {})
    sync.pull_group_name(gp, "There", "Here")

    sync.client.update_group = lambda ygid, name: {"id": ygid, "name": name}
    sync.push_group_name("yg1", gp, "There", "Here")

    sync.client.create_group = lambda name: {"id": "made", "name": name}
    sync.parking_group("ACME parking")

    sync.groups.ynab_id = lambda group_id: None
    sync.groups.pair = lambda *a: None
    sync.book.groups = [{"id": "g9", "name": "Brand new"}]
    sync.ynab_group_for("g9")
    pin("sync_trail_groups", _read(trail))


def test_trail_of_new_categories(trail, monkeypatch):
    sync = _sync()
    sync.book.solo_envelope = lambda cid: None
    sync.book.rows |= {
        "c3": {"id": "c3", "label": "Pets"},
        "c5": {"id": "c5", "label": "Dogs"},
        "c6": {"id": "c6", "label": "Cats"},
    }
    sync.yc |= {
        "y9": {"id": "y9", "name": "Pets", "category_group_id": "yg1"},
        "p1": {"id": "p1", "name": "Dogs", "category_group_id": "yg1"},
    }
    pets = {"id": "y9", "name": "Pets", "label": "Pets", "hidden": False}

    monkeypatch.setattr(category_new, "propose", lambda *a: SimpleNamespace(kind="new"))
    sync.create_here = lambda c: sync.book.rows["c3"]
    sync.new_from_ynab(pets, set())
    sync.pair_existing(pets, sync.book.rows["c3"], set())

    sync.create_in_ynab(sync.book.rows["c5"])
    sync.client.create_category = lambda group, name: {
        "id": "y7",
        "name": name,
        "category_group_id": group,
    }
    sync.create_in_ynab(sync.book.rows["c6"])

    monkeypatch.setattr(category_new.make, "give_envelope", lambda book, cat, gid: {"id": "e9"})
    sync.groups.ensure = lambda book, ygid, name: "g1"
    sync.give_envelope_now("y1", _pair(), sync.book.rows["c1"])
    pin("sync_trail_new", _read(trail))


def test_trail_of_what_the_person_should_know(trail):
    sync = _sync()
    sync.book.env_of = {}
    sync.say_paused({"id": "c1", "label": "Fun"})
    sync.note_shared({"id": "e1", "name": "Household"})
    sync.deferred = 1
    sync.finish()
    pin("sync_trail_notes", _read(trail))


def test_trail_of_what_waits_for_the_next_run(trail, filed, monkeypatch):  # noqa: F811
    item = SimpleNamespace(
        key="a:2026-03:c1", action="push", month="2026-03", cid="c1", onw=1, ynab=2, env="e"
    )
    monkeypatch.setattr(runner.assigned, "evaluate", lambda *a: [item])
    monkeypatch.setattr(runner.assigned, "push", lambda *a: ([], [item], []))
    monkeypatch.setattr(runner.planning, "assignments_store", SimpleNamespace(all_map=dict))
    runner._assigned_phase(
        SimpleNamespace(meta={"categories": [{"id": "c1", "name": "Fun"}]}),
        None,
        SimpleNamespace(assigned={}),
        SimpleNamespace(assigned={}),
        Counter(),
        False,
        {"direction": "both", "held_assigned": []},
    )

    unit = Unit("t1", {"description": "ACME"}, ["y1"], {"v": 1}, {"v": 2}, None)
    monkeypatch.setattr(txn_sync.push, "send_updates", lambda c, cl, u, out: out.deferred.extend(u))
    monkeypatch.setattr(txn_sync.push, "send_creates", lambda *a: None)
    monkeypatch.setattr(txn_sync.push, "send_deletes", lambda *a: None)
    txn_sync._push_all(SimpleNamespace(), None, SimpleNamespace(txns={}), [unit], Counter())
    pin("sync_trail_deferred", _read(trail))


# ── the run: its phase, and what the runner itself refuses ──


def test_the_run_says_what_it_is_reading(monkeypatch):
    phases: list[str] = []
    monkeypatch.setattr(recorder, "phase", phases.append)
    monkeypatch.setattr(runner, "_reconcile", lambda st, tally, c: None)
    runner._execute({"plan_id": "plan"}, Counter())
    pin("sync_phases", phases)


def test_runner_refusals(monkeypatch):
    lines = [said(runner.start_now)]
    st = _on()
    assert runner._lock.acquire(blocking=False)
    try:
        lines += [said(runner.start_now), said(runner.update_settings, True, None, None)]
    finally:
        runner._lock.release()

    with monkeypatch.context() as m:
        m.setattr(source, "list_plans", lambda store: [])
        lines.append(said(runner._adopt_plan, "other"))
    with monkeypatch.context() as m:
        m.setattr(source, "list_plans", lambda store: [{"id": "other", "name": "P"}])
        lines.append(said(runner._adopt_plan, "other"))

    with monkeypatch.context() as m:
        m.setattr(YnabConfigStore, "is_configured", lambda self: False)
        lines.append(said(runner._require_ready, st))
    with monkeypatch.context() as m:
        m.setattr(YnabConfigStore, "is_configured", lambda self: True)
        lines.append(said(runner._require_ready, {**st, "plan_id": None}))
        lines.append(said(runner._require_ready, st))
    pin("sync_runner_refusals", lines)


def test_sync_now_beside_an_import():
    _on()
    run_id = import_runs.start("ynab", "import", "plan")["id"]
    try:
        pin("sync_run_in_progress", [said(runner.start_now)])
    finally:
        import_runs.finish(run_id, "done")


# ── settling a review item ──


def test_resolve_refusals():
    lines = [said(resolve.resolve, "c_nothing", "onw")]
    _on()
    item = conflicts.file(conflicts.CONFLICT, "t1", "ACME", "Why", None, None, entity="txn")
    assert runner._lock.acquire(blocking=False)
    try:
        lines.append(said(resolve.resolve, item["id"], "onw"))
    finally:
        runner._lock.release()
    run_id = import_runs.start("ynab", "import", "plan")["id"]
    try:
        lines.append(said(resolve.resolve, item["id"], "onw"))
    finally:
        import_runs.finish(run_id, "done")
    pin("sync_resolve_refusals", lines)


def test_resolve_cannot_send_it(monkeypatch):
    monkeypatch.setattr(
        resolve.planning, "assignments_store", SimpleNamespace(month_map=lambda m: {})
    )
    monkeypatch.setattr(resolve.assigned, "push", lambda *a: ([], [], [(None, "ACME said no")]))
    figure = {"month": "2026-03", "category": "c1", "env": "e", "ynab_milli": 1, "onw_milli": 2}
    lines = [said(resolve._assigned, figure, "onw", None, None, {"categories": []})]

    canon = {"v": 1}
    unit = Unit("t1", {}, ["y1"], canon, canon, canon, None, "ACME blocked")
    monkeypatch.setattr(resolve, "_unit", lambda *a: unit)
    lines.append(said(resolve._transaction, None, {}, "ynab", None, None))

    txn = {"kind": conflicts.CONFLICT}
    monkeypatch.setattr(
        push, "send_updates", lambda c, cl, u, out: out.unsupported.append((u[0], "ACME said no"))
    )
    lines.append(said(resolve._transaction, None, txn, "onw", None, None))
    monkeypatch.setattr(push, "send_updates", lambda c, cl, u, out: out.deferred.extend(u))
    lines.append(said(resolve._transaction, None, txn, "onw", None, None))
    pin("sync_resolve_sending", lines)


def test_category_resolve_refusals(monkeypatch):
    sync = _sync()
    sync.deferred = 1
    lines = [said(category_resolve._check, sync)]
    sync.deferred = 0

    new = {"ynab_id": "y1", "kind": conflicts.CHOOSE, "options": [{"ref": "cat:c1"}]}
    sync.direction = "to_ynab"
    lines.append(said(category_resolve._choose, sync, new, "ynab", None))
    sync.direction = "both"
    sync.base.cats = {"y2": {"category": "c1"}}
    lines.append(said(category_resolve._choose, sync, new, "onw", "cat:c1"))

    sync.direction = "from_ynab"
    lines.append(said(category_resolve._new_here, sync, {"category": "c1"}, "onw"))

    sync.direction = "to_ynab"
    sync.base.cats = {"y1": _pair()}
    sync.book.env_of = {}
    lines.append(said(category_resolve._budget, sync, {"ynab_id": "y1"}, "ynab"))

    def out_of_requests(*_a):
        raise RateCapReached

    monkeypatch.setattr(category_resolve, "News", lambda *a: sync)
    monkeypatch.setattr(category_resolve, "_category", out_of_requests)
    lines.append(
        said(category_resolve.apply, {"entity": "category"}, "onw", None, {}, {}, None, None)
    )
    pin("sync_category_resolve_refusals", lines)


def test_category_resolve_trail(trail, monkeypatch):
    sync = _sync()
    sync.book.solo_envelope = lambda cid: None
    sync.book.env_of = {}
    sync.book.rows["c3"] = {"id": "c3", "label": "Pets"}
    sync.create_here = lambda c: sync.book.rows["c3"]
    new = {"ynab_id": "y1", "kind": conflicts.CHOOSE, "options": [{"ref": "env:e1"}]}
    category_resolve._choose(sync, new, "ynab", None)

    sync.book.envelopes = [{"id": "e1", "name": "Household"}]
    monkeypatch.setattr(category_resolve.make, "make_category_in", lambda *a: sync.book.rows["c3"])
    sync.base.cats = {}
    category_resolve._choose(sync, new, "onw", "env:e1")
    pin("sync_category_resolve_trail", _read(trail))


# ── the client ──


def test_client_wording(monkeypatch):
    def limited(*_a, **_k):
        raise ExternalServiceError("YNAB answered HTTP 429")

    monkeypatch.setattr(source, "call", limited)
    pin(
        "sync_client",
        [
            said(client.YnabClient, "not an id!", client.Budget(1)),
            said(client.Budget(0).spend),
            said(client.YnabClient("plan", client.Budget(1)).read_plan, None),
        ],
    )
