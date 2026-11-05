"""What a YNAB user reads, pinned byte for byte in ``tests/snapshots/ynab_wording.json``: the sync's
run summary, ``Unsupported`` messages and review reasons, the migration's report and run log, and
every refusal and row label on the way. The word "YNAB" in these strings comes from
``budget_app.current().name``, and this file proves that YNAB users cannot see the difference.

To pin a new string on purpose, run this file with ``UPDATE_WORDING_SNAPSHOT=1``; a refactor never
needs to. All figures and names are invented."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.errors import ExternalServiceError, OpenNetWorthError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import import_runs
from backend.services import ledger as ledger_service
from backend.services import profile as profile_service
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_migration import (
    apply,
    category_apply,
    category_links,
    category_make,
    category_match,
    job,
    links,
    mapping,
    report,
    source,
    verify,
)
from backend.services.ynab_sync import (
    category_new,
    conflicts,
    payloads,
    pull,
    push,
    runner,
    starts,
    txn_sync,
    units,
)
from backend.services.ynab_sync.units import Unit
from tests import ynab_stub
from tests.test_ynab_migration_service import run

pytestmark = [pytest.mark.flags(ynab_import_and_sync=True), pytest.mark.usefixtures("db_isolated")]

SNAPSHOT = Path(__file__).parent / "snapshots" / "ynab_wording.json"
_DATE = re.compile(r"\d{4}-\d{2}(-\d{2})?")
_BACKUP = re.compile(r"Backup taken: .+?\. Restore")
CHECKING = f"{1:08d}-0000-0000-0000-000000000000"


def pin(section: str, lines: list[str]) -> None:
    lines = [_BACKUP.sub("Backup taken: <name>. Restore", _DATE.sub("<date>", s)) for s in lines]
    lines = list(dict.fromkeys(lines))
    if os.environ.get("UPDATE_WORDING_SNAPSHOT"):
        pinned = json.loads(SNAPSHOT.read_text("utf-8")) if SNAPSHOT.exists() else {}
        SNAPSHOT.parent.mkdir(exist_ok=True)
        text = json.dumps({**pinned, section: lines}, indent=1, sort_keys=True, ensure_ascii=False)
        SNAPSHOT.write_text(text + "\n", "utf-8")
        return
    assert lines == json.loads(SNAPSHOT.read_text("utf-8"))[section]


def said(fn, *args, **kwargs) -> str:
    """What ``fn`` refuses with, in the words the person reads."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        return getattr(exc, "message", None) or str(exc)
    raise AssertionError(f"{fn.__name__} did not refuse")


def refuse(*_args, **_kwargs):
    raise ExternalServiceError("YNAB answered HTTP 400")


def boom(*_args, **_kwargs):
    raise OpenNetWorthError("ACME refused")


@pytest.fixture
def filed(monkeypatch):
    """The reason of each review item the code under test files."""
    reasons: list[str] = []
    monkeypatch.setattr(
        conflicts, "file", lambda kind, key, title, reason, *a, **k: reasons.append(reason)
    )
    monkeypatch.setattr(conflicts, "open_keys", dict)
    monkeypatch.setattr(conflicts, "close", lambda key: None)
    return reasons


@pytest.fixture
def ynab(monkeypatch):
    server, base_url, _calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    yield server
    server.shutdown()


# ── the sync: run summary, Unsupported, review reasons ──


def test_run_summary():
    tallies = [
        (Counter(), 0),
        (Counter(sent=2), 0),
        (Counter(pulled=3), 0),
        (Counter(categories=1), 0),
        (Counter(categories=2), 0),
        (Counter(), 1),
        (Counter(), 2),
        (Counter(deferred=4), 0),
        (Counter(sent=1, pulled=1, categories=1, deferred=1), 1),
    ]
    pin("run_summary", [runner._summary(tally, n) for tally, n in tallies])


def _txn(**over) -> dict:
    base = {
        "kind": "expense",
        "date": "2020-01-02",
        "description": "ACME",
        "memo": "",
        "cleared": True,
        "category": None,
        "splits": None,
        "from": ["a1", 1000],
        "to": None,
    }
    return {**base, **over}


def _sync_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        ynab_acct={"a1": "y1", "a2": "y2"},
        ynab_cat={},
        cat_ids={},
        income=frozenset(),
        unsent=frozenset({"c2"}),
        said=set(),
        names={"categories": {"c1": "Groceries", "c2": "Pets"}, "accounts": {}},
        currency="USD",
        meta={"accounts": [{"id": "y2"}], "categories": [{"id": "yc", "name": "Pets"}]},
        mirror={
            "m1": {"id": "m1", "account_id": "y1", "amount": -1000},
            "m2": {"id": "m2", "account_id": "y2", "amount": 1000},
            "s1": {"id": "s1", "account_id": "y1", "amount": -1000, "subtransactions": [{}]},
        },
        sub_parent={},
    )


def test_unsupported(monkeypatch):
    ctx = _sync_ctx()
    transfer = _txn(kind="transfer", to=["a2", 1000])
    lines = [
        said(payloads._check_plain_shape, ctx, _txn(kind="adjustment")),
        said(payloads._check_plain_shape, ctx, _txn(date="2999-01-02")),
        said(payloads._check_plain_shape, ctx, _txn(**{"from": ["a9", 1000]})),
        said(payloads.create_payload, ctx, _txn(), {"EUR", "GBP"}),
        said(payloads.create_payload, ctx, transfer, {"USD"}),
        said(payloads._plain_fields, ctx, {}, _txn(), transfer, ["kind"]),
        said(payloads._plain_fields, ctx, {}, _txn(), _txn(splits=[["c1", 1000, ""]]), []),
        said(payloads._transfer_ops, ctx, ["m1", "m2"], transfer, transfer, ["category"]),
        said(payloads.update_payloads, ctx, ["m1", "m2"], _txn(), _txn(memo="new")),
        said(payloads.update_payloads, ctx, ["s1"], _txn(), _txn(date="2020-01-03")),
        said(payloads._category_id, ctx, "c1"),
    ]
    notes: list[str] = []
    monkeypatch.setattr(payloads.recorder, "note", lambda item, line: notes.append(line))
    payloads._category_id(ctx, "c2")

    monkeypatch.setattr(units.context, "render", lambda *a: (_ for _ in ()).throw(KeyError()))
    lines.append(units._render(ctx, {"category": None, "splits": None})[1])
    monkeypatch.setattr(units.context, "render", lambda *a: {})
    lines.append(units._render(ctx, {"category": "yc", "splits": None})[1])

    out = push.Outcome()
    push.send_deletes(ctx, SimpleNamespace(), [Unit("t1", {}, ["gone"], None, None, None)], out)
    lines += [reason for _, reason in out.unsupported]
    monkeypatch.setattr(
        push,
        "create_payload",
        lambda *a: {"account_id": "y1", "date": "2020-01-02", "amount": -1000},
    )
    client = SimpleNamespace(
        save_transactions=lambda method, rows: [],
        budget=SimpleNamespace(left=lambda: 10, spend=lambda n=1: None),
    )
    out = push.Outcome()
    push.send_creates(ctx, client, [Unit("t1", {}, [], None, _txn(), None)], out)
    lines += [reason for _, reason in out.failed]
    pin("unsupported", lines + notes)


def test_transaction_review_reasons(filed, monkeypatch):
    ctx = _sync_ctx()
    monkeypatch.setattr(txn_sync, "view", lambda c, names: None)
    base = SimpleNamespace(txns={})
    unit = Unit("t1", {"description": "ACME"}, ["y1"], None, None, None)

    monkeypatch.setattr(
        txn_sync.pull, "apply", lambda *a: (_ for _ in ()).throw(pull.LedgerChanged("moved"))
    )
    txn_sync._apply_pull(ctx, unit, base, Counter())
    monkeypatch.setattr(txn_sync.pull, "apply", boom)
    txn_sync._apply_pull(ctx, unit, base, Counter())

    monkeypatch.setattr(txn_sync, "_rows", lambda *a: ({}, {}, {}, set()))
    clash = Unit("t3", {}, ["y3"], {"v": 1}, {"v": 2}, {"v": 3})
    blocked = Unit(None, None, ["y4"], None, None, {"v": 1}, None, "The ACME reason.")
    monkeypatch.setattr(txn_sync.units, "build", lambda *a: [clash, blocked])
    txn_sync.settle(ctx, None, base, SimpleNamespace(touched=set()), False, set(), Counter())

    same = {"v": 1}
    many = [Unit(f"t{i}", {}, [f"y{i}"], same, same, None) for i in range(30)]
    txn_sync._hold_removals(ctx, many, {})

    def rejected(_ctx, _client, jobs, out):
        out.unsupported.append((unit, "The ACME reason."))
        out.failed.append((unit, "ACME said no"))

    monkeypatch.setattr(txn_sync.push, "send_updates", rejected)
    monkeypatch.setattr(txn_sync.push, "send_creates", lambda *a: None)
    monkeypatch.setattr(txn_sync.push, "send_deletes", lambda *a: None)
    deletes = [Unit(f"t{i}", {}, [f"y{i}"], same, None, None) for i in range(26)]
    txn_sync._push_all(ctx, None, base, deletes, Counter())

    monkeypatch.setattr(txn_sync, "_record_sent", boom)
    monkeypatch.setattr(txn_sync.recorder, "txn_change", lambda *a: None)
    txn_sync._recorded(ctx, unit, push.Outcome(), base)
    pin("review_transactions", filed)


def test_assigned_review_reasons(filed, monkeypatch):
    def item(action, cid="c1"):
        return SimpleNamespace(
            key=f"{action}:{cid}",
            action=action,
            month="2026-03",
            cid=cid,
            onw=1000,
            ynab=2000,
            env="e",
        )

    pulled, known, unknown, fresh = (
        item("pull"),
        item("conflict"),
        item("pull", "gone"),
        item("conflict", "c2"),
    )
    monkeypatch.setattr(runner.assigned, "evaluate", lambda *a: [pulled, unknown, known, fresh])
    monkeypatch.setattr(runner.assigned, "pull", boom)
    monkeypatch.setattr(runner.assigned, "push", lambda *a: ([], [], [(known, "ACME said no")]))
    monkeypatch.setattr(runner.planning, "assignments_store", SimpleNamespace(all_map=dict))
    ctx = SimpleNamespace(
        meta={"categories": [{"id": "c1", "name": "Fun"}, {"id": "c2", "name": "Pets"}]}
    )
    runner._assigned_phase(
        ctx,
        None,
        SimpleNamespace(assigned={}),
        SimpleNamespace(assigned={}),
        Counter(),
        False,
        {"direction": "both", "held_assigned": []},
        {"c2"},
    )
    pin("review_assigned", filed)


class _NothingOpen(dict):
    """Every review item is filed afresh, so a second reason under one key is heard too."""

    def __setitem__(self, key, value):
        pass


def _categories_sync() -> category_new.News:
    sync = category_new.News.__new__(category_new.News)
    sync.__dict__.update(
        client=SimpleNamespace(
            budget=SimpleNamespace(used=0),
            update_category=refuse,
            update_group=refuse,
            create_category=refuse,
        ),
        base=SimpleNamespace(
            cats={}, groups={}, declined=set(), set_cat=lambda p: None, set_group=lambda g: None
        ),
        book=SimpleNamespace(
            rows={"c1": {"id": "c1", "label": "Fun here"}},
            env_of={"c1": {"group_id": "g1"}},
            groups=[{"id": "g1", "name": "Wants"}],
            solo_envelope=lambda cid: None,
            refresh=lambda: None,
        ),
        groups=SimpleNamespace(our_id=lambda y: None),
        tally=Counter(),
        direction="both",
        plan_id="plan",
        open=_NothingOpen(),
        yc={"y1": {"id": "y1", "name": "Fun there", "category_group_id": "yg1"}},
        yg={
            "yg1": {"id": "yg1", "name": "Wants"},
            "yg2": {"id": "yg2", "name": "Needs"},
            "yh": {"id": "yh", "name": category_new.ynab_pairs.HIDDEN_HERE},
            "yn": {"id": "yn", "name": category_new.ynab_pairs.NOT_BUDGETED},
        },
        held_names=set(),
        noted=set(),
        deferred=0,
        quiet=False,
        errors=[],
        fresh=set(),
        ynab_assigned={"y1"},
    )
    return sync


def _pair(**over) -> dict:
    side = {"name": "Fun", "hidden": False, "group": "g1"}
    return {
        "category": "c1",
        "gone": False,
        "had_env": True,
        "env": None,
        "onw": dict(side),
        "ynab": {**side, "group": "yg1"},
        **over,
    }


def test_category_and_group_review_reasons(filed, monkeypatch):
    sync = _categories_sync()
    sync.sync_pair("y1", _pair())

    moved = _categories_sync()
    moved.book.rows["c1"]["label"] = "Fun"
    moved.yc["y1"].update(name="Fun", category_group_id="yg2")
    moved.book.env_of["c1"] = {"group_id": "g2"}
    moved.sync_pair("y1", _pair())

    y, o = sync.ystate(sync.yc["y1"]), sync.ostate(sync.book.rows["c1"])
    sync.pull_name = boom
    sync.pull("y1", _pair(), sync.book.rows["c1"], y, o, ["name"])
    sync.refuse("y1", "Fun", ["name"], y, o)
    sync.ours_gone("y1", _pair(), sync.yc["y1"])

    gp = {"id": "g:yg1", "group": "g1", "onw": {"name": "Old"}, "ynab": {"name": "Old"}}
    sync.sync_group("yg1", gp, "There", "Here")
    monkeypatch.setattr(category_new.planning, "apply_budget_change", boom)
    sync.pull_group_name(gp, "There", "Here")
    sync.push_group_name("yg1", gp, "There", "Here")

    sync.new_from_ynab = boom
    sync.pull_new([{"id": "y9", "name": "Pets"}])
    sync.ask({"id": "y9", "name": "Pets"}, SimpleNamespace(kind="ambiguous", options=[]))
    envelope = {"id": "e1", "name": "Household"}
    sync.ask({"id": "y9", "name": "Pets"}, SimpleNamespace(kind="offer", envelope=envelope))

    sync.create_in_ynab({"id": "c3", "label": "Pets"})
    sync.yc |= {
        "p1": {"id": "p1", "name": "Pets", "category_group_id": "yg1"},
        "p2": {"id": "p2", "name": "Pets", "category_group_id": "yg2"},
    }
    sync.create_in_ynab({"id": "c3", "label": "Pets"})
    sync.envelope_for_money("y1", _pair(), {"id": "c1", "label": "Fun"})
    pin("review_categories", filed)


def test_start_review_reason(monkeypatch):
    monkeypatch.setattr(
        starts.ledger_service,
        "account_balance",
        lambda acct, txns, at: 100.0 if at == "2026-03-01" else 75.5,
    )
    acct = {"name": "ACME Checking", "currency": "USD"}
    rows = [{"id": "t1", "date": "2026-01-15"}, {"id": "t2", "date": "2026-02-15"}]
    pin(
        "review_start",
        [starts._item(acct, "2026-03-01", some, [])["reason"] for some in (rows[:1], rows)],
    )


# ── the migration: its report ──


def test_report_lines():
    hints = ("failed", "refused_duplicate", "previously_deleted", "assigned", "other")
    made = [
        Counter(),
        Counter(categories=1),
        Counter(categories=2, envelopes=1),
        Counter(categories=2, envelopes=2, groups=1),
    ]
    tallies = [
        Counter(created=1),
        Counter(created=2, already_present=1),
        Counter(created=2, matched=2),
        Counter(created=0, refused_duplicate=1),
        Counter(created=0, previously_deleted=1, refused_duplicate=1),
        Counter(created=1, failed=1),
        Counter(created=1, failed=2),
    ]
    ver = {"checks_matching": 3, "checks_total": 4}
    count = {"accounts": {"created": 2}, "transactions": {"created": 7}}
    runs = [
        ("verify", {"verification": ver, "preview": None, "counts": {}}),
        ("preview", {"verification": None, "preview": None, "counts": {}}),
        (
            "preview",
            {"verification": None, "preview": {"accounts": [1], "transactions": 7}, "counts": {}},
        ),
        ("import", {"verification": ver, "preview": None, "counts": count}),
        ("import", {"verification": None, "preview": None, "counts": count}),
    ]
    pin(
        "report",
        [report.problem({"ref": "r", "kind": k})["hint"] for k in hints]
        + [report.transactions_line(t) for t in tallies]
        + [report.categories_line(m) for m in made]
        + [report.starter_line(m) for m in made]
        + [report.outcome(kind, r) for kind, r in runs],
    )


# ── the migration: the job against the synthetic YNAB ──


def _log(run_record: dict) -> list[str]:
    return [e["line"] for e in run_record["log"]]


def _checks(run_record: dict) -> list[str]:
    ver = run_record["verification"]
    return [f"{c['check']} | {c['note']}" for c in [*ver["mismatches"], *ver["matches"]]]


def test_import_log_and_verification(ynab):
    profile = profile_service.read_profile()
    profile["base_currency"] = "EUR"
    profile_service.write_profile(profile)
    preview = run("preview")
    fresh = run()
    assert fresh["status"] == "done", fresh["error"]
    again = run()
    checked = run("verify")
    pin("import_log_fresh", _log(fresh))
    pin("import_log_rerun", _log(again))
    pin("verify_rows", _checks(fresh) + _checks(checked))
    pin("outcomes", [r["outcome"] for r in (preview, fresh, again, checked)])


def test_import_log_with_a_linked_account(ynab):
    profile = profile_service.read_profile()
    profile["base_currency"] = "USD"
    profile_service.write_profile(profile)
    everyday = accounts_service.create_account(
        {
            "name": "Everyday",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
            "initial_balance_date": "2000-01-01",
        }
    )
    linked = run(account_links={CHECKING: everyday["id"]}, confirm_nonempty=True)
    assert linked["status"] == "done", linked["error"]
    pin("import_log_linked", _log(linked))


def test_turning_the_sync_on_is_logged(monkeypatch):
    lines: list[str] = []
    fake = SimpleNamespace(log=lines.append)
    monkeypatch.setattr(runner, "update_settings", lambda **k: None)
    for direction in ("both", "from_ynab", "to_ynab"):
        job._turn_on_sync(fake, "plan", direction)
    monkeypatch.setattr(runner, "update_settings", lambda **k: boom())
    job._turn_on_sync(fake, "plan", "both")
    pin("sync_turned_on", lines)


def test_job_refusals(ynab, monkeypatch):
    plan = ynab_stub.PLAN
    lines = [
        said(job.start, "nope", plan),
        said(job.start, "verify", plan, account_links={"a": "b"}),
        said(job.start, "import", plan, recheck_of="r"),
        said(job.start, "preview", plan, sync_direction="both"),
        said(job.start, "import", plan, sync_direction="sideways"),
        said(job.start, "preview", "  "),
    ]
    with monkeypatch.context() as m:
        m.setattr(YnabConfigStore, "is_configured", lambda self: False)
        lines.append(said(job.start, "preview", plan))
    with monkeypatch.context() as m:
        m.setattr(runner, "is_running", lambda: True)
        lines.append(said(job.start, "preview", plan))
    pin("job_refusals", lines)


def _failed_run(confirm: bool = True) -> str:
    record = job._Run(import_runs.start("ynab", "import", ynab_stub.PLAN)["id"])
    try:
        return said(job._execute, record, "import", ynab_stub.PLAN, confirm, {})
    finally:
        import_runs.finish(record.id, "failed")


def test_run_failures(ynab, monkeypatch):
    lines = []
    with monkeypatch.context() as m:
        m.setattr(source, "list_plans", lambda store: [])
        lines.append(_failed_run())
    with monkeypatch.context() as m:
        m.setattr(
            source,
            "list_plans",
            lambda store: [{"id": ynab_stub.PLAN, "name": "P", "currency": "us$"}],
        )
        lines.append(_failed_run())
    with monkeypatch.context() as m:
        m.setattr(job.category_links, "unresolved", lambda plan, decisions: ["Fun Money"])
        lines.append(_failed_run())

    accounts_service.create_account(
        {"name": "Everyday", "type": "cash", "currency": "USD", "initial_balance": 1.0}
    )
    lines.append(said(job.start, "import", ynab_stub.PLAN))
    lines.append(_failed_run(confirm=False))
    pin("run_failures", lines)


# ── the migration: the wording of its parts ──


def _plan_account(**over) -> dict:
    payload = {"name": "Checking", "type": "cash"}
    return {
        "id": "y1",
        "closed": False,
        "payload": payload,
        "ynab_rows": 3,
        "expected_rows": 4,
        "cleared_adjust": 0.0,
        **over,
    }


def test_links_refusals():
    plan = {"accounts": [_plan_account(), _plan_account(id="y2")], "currency": "USD"}
    here = {"id": "a1", "name": "Everyday", "type": "cash", "currency": "USD"}
    card = {"id": "a2", "name": "ACME Card", "type": "credit", "currency": "USD"}
    held = {**here, "id": "a3", "external_ids": [{"source": "ynab", "ref": "y1"}]}
    pin(
        "links_refusals",
        [
            said(links.resolve, plan, {"nope": "a1"}, [here]),
            said(links.resolve, plan, {"y1": "nope"}, [here]),
            said(links.resolve, plan, {"y1": "a1"}, [here, held]),
            said(links.resolve, plan, {"y1": "a2"}, [card]),
            said(links.resolve, plan, {"y1": "a1", "y2": "a1"}, [here]),
        ],
    )


def test_mapping_lines():
    accounts = [{"id": "y1", "balance": 1500, "cleared_balance": 1000}]
    pieces: list[dict] = []
    txns = [{"id": "t", "account_id": "y1", "date": "2026-01-02", "amount": 0}]
    mapping.balance_gaps(accounts, txns, pieces, Counter())
    stats = {"subcent_amounts": 1, "unknown_category": 2, "transfers_one_sided": 3}
    plan = {
        "name": "P",
        "currency": "USD",
        "accounts": [],
        "categories": [],
        "skipped_categories": [],
        "pieces": [],
        "assigned": {},
        "stats": stats,
    }
    groups = [
        {
            "id": "g",
            "name": "Commitments",
            "deleted": False,
            "hidden": False,
            "internal": False,
            "categories": [
                {"id": "c", "name": "Rent", "deleted": False, "hidden": False, "internal": False}
            ],
        }
    ]
    txn = {"category_id": "c"}
    pin(
        "mapping",
        [p["description"] for p in pieces]
        + [p["memo"] for p in pieces]
        + mapping.preview_summary(plan)["warnings"]
        + [said(mapping.validate_amounts, accounts, [{"amount": 1.5}], [])]
        + [c["group"] for c in mapping.plan_categories(groups, [txn], [])["categories"]],
    )


def test_names_made_to_fit_the_book():
    book = category_match.Book.__new__(category_match.Book)
    book.labels = {"fun", "fun (ynab)", "pets"}
    book.names = {"bills", "bills (ynab)", "rent"}
    pin(
        "names",
        [
            book.free_label("Fun"),
            book.free_label("Pets"),
            book.free_label("Dining"),
            book.free_envelope_name("Bills"),
            book.free_envelope_name("Rent"),
            book.free_envelope_name("Dining"),
            category_make.our_group_name("Commitments"),
            category_make.our_group_name("Needs"),
            category_apply.shared_note("ACME Bills"),
            said(category_apply._category_for, {"name": "Fun"}, {"kind": "ambiguous"}, 0, 0, 0),
        ],
    )


def test_category_review_wording():
    book = SimpleNamespace(
        matchable=[{"id": "c1", "label": "Fun"}],
        envelopes=[{"id": "e1", "name": "Bills"}],
        env_of={},
    )
    c = {"name": "Pets"}
    pin(
        "category_review",
        [
            said(category_links._explicit, c, "cat:c1", book, {"c1"}, set()),
            said(category_links._explicit, c, "env:e1", book, set(), {"e1"}),
            category_links.note(book, {"kind": "category", "category": {"id": "c1"}}, c, True),
        ],
    )


def test_apply_wording(monkeypatch):
    monkeypatch.setattr(apply.ledger_service, "base_currency", lambda: "EUR")
    reasons: list[str] = []
    monkeypatch.setattr(
        apply.ledger_service,
        "attach_external_refs",
        lambda txn_id, refs, reason: reasons.append(reason),
    )
    monkeypatch.setattr(apply.match_review, "adopt_if_blank", lambda txn, row: None)
    apply._attach({"external_refs": [{"ref": "y1"}]}, {"id": "t1"}, Counter(), [])
    pin("apply", [said(apply.ensure_base_currency, "USD", ["accounts"]), *reasons])


def test_verification_notes(monkeypatch):
    onw = {"id": "a1", "external_ids": [{"source": "ynab", "ref": "y1"}]}
    foreign = {"id": "a2", "external_ids": []}
    monkeypatch.setattr(
        ledger_service.accounts, "query_accounts", lambda include_archived=False: [onw, foreign]
    )
    monkeypatch.setattr(ledger_service.transactions, "all", lambda: [])
    monkeypatch.setattr(ledger_service, "opening_txn_ids", frozenset)
    monkeypatch.setattr(
        ledger_service,
        "account_with_balance",
        lambda a, txns: {"id": a["id"], "balance": 1.0, "cleared_balance": 1.0},
    )
    plan = {"accounts": [_plan_account(ynab_sum=2.0, cleared_adjust=0.5)]}
    live = [{"id": "y1", "balance": 1000, "cleared_balance": 500}]
    rows = verify.verify_accounts(plan, live)

    own = [
        {"id": "t1", "from_account_id": "a1", "external_refs": [{"source": "ynab", "ref": "y"}]},
        {"id": "t2", "from_account_id": "a1"},
    ]
    rows.append(verify._count_row("Checking", _plan_account(), "a1", own, frozenset()))

    ycats = {
        "u": {"name": "Uncategorized", "balance": 0, "activity": 2000, "budgeted": 1000},
        "z": {"name": "ACME", "balance": 3000, "activity": 0, "budgeted": 0},
    }
    table = {"held_outside_envelopes": 0.0, "ready_to_assign": 9.0}
    rows += verify._special_rows({"categories": []}, ycats, {}, table, "2026-03")

    env = {"c1": {"envelope": "e1", "category": "cat1"}}
    monkeypatch.setattr(
        apply,
        "month_rows",
        lambda month: (table, {"e1": {"activity": -1.0, "available": 1.0, "assigned": 0.0}}),
    )
    history = {
        "categories": [{"id": "c1", "label": "Fun"}],
        "history": {"2026-02": {"cats": {"c1": (-5000, 7000)}, "rta": 1000}},
        "lines": {"2026-02|c1": {"abcdefghij": -5000}},
    }
    rows += verify.first_divergence(history, env, "2026-03")
    pin(
        "verify_notes",
        [f"{r['label']} | {r['note']}" for r in rows]
        + [verify._ready_note(names) for names in ([], ["A", "B"], ["A", "B", "C", "D"])],
    )
