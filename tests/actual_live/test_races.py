"""What happens around one batch on a real server: an answer that never arrives, and a person using
Actual at the same moment. The person is the official client, so their edits carry real timestamps."""

from __future__ import annotations

import threading
import time

import pytest
from actual.protobuf_models import SyncRequest

from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync import writes
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync.client import Budget, Moved
from tests.actual_live.world import (
    THIS_MONTH,
    TODAY,
    account_here,
    as_person,
    assigned_here,
    balances_here,
    balances_there,
    category_here,
    edit_here,
    envelope_here,
    new_row,
    open_items,
    quiet,
    row_here,
    rows_here,
    run,
    sync,
    there,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


@pytest.fixture
def lose_next_commit(actual_server):
    """The next request carrying messages reaches the server and is applied; its answer is lost."""
    armed = {"on": False, "lost": 0}

    def lose(path: str, body: bytes) -> bool:
        if not armed["on"] or path != "/sync/sync":
            return False
        if not SyncRequest.deserialize(body).messages:
            return False
        armed["on"], armed["lost"] = False, armed["lost"] + 1
        return True

    actual_server.lose_answer = lose
    yield armed
    actual_server.lose_answer = lambda _path, _body: False


def lost_then_settled(armed: dict) -> None:
    armed["on"], lost_before = True, armed["lost"]
    last = run()
    assert armed["lost"] == lost_before + 1, "the answer was to be lost"
    assert not last["ok"], "a commit with no answer ends the run"
    assert "127.0.0.1" not in last["error"], "the address never shows"
    sync()
    quiet()


def test_a_lost_answer_to_a_plain_create_leaves_one_row(world, lose_next_commit):
    budget = world.seed()
    world.imported(budget)
    quiet()
    new_row(memo="lost create", category=category_here("ACME Food")["id"])
    lost_then_settled(lose_next_commit)
    assert len(there(budget.view(), "lost create")) == 1
    assert len(rows_here(memo="lost create")) == 1
    assert balances_here() == balances_there(budget.view())
    edit_here(row_here(memo="lost create")["id"], memo="still linked")
    sync()
    assert len(there(budget.view(), "still linked")) == 1, "the row here owns the row there"


def test_a_lost_answer_to_a_split_create_leaves_one_row(world, lose_next_commit):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food, fun = category_here("ACME Food")["id"], category_here("ACME Fun")["id"]
    new_row(
        memo="lost split",
        from_amount=30.0,
        splits=[
            {"category": food, "amount": 20.0, "memo": "line a"},
            {"category": fun, "amount": 10.0, "memo": "line b"},
        ],
    )
    lost_then_settled(lose_next_commit)
    (split,) = there(budget.view(), "lost split")
    assert [s["amount"] for s in split["subs"]] == [-2000, -1000]
    assert len(rows_here(memo="lost split")) == 1
    assert balances_here() == balances_there(budget.view())


def test_a_lost_answer_to_a_transfer_create_leaves_one_transfer(world, lose_next_commit):
    budget = world.seed()
    world.imported(budget)
    quiet()
    new_row(
        kind="transfer",
        description="",
        memo="lost transfer",
        from_amount=40.0,
        to_account_id=account_here("ACME Card")["id"],
        to_amount=40.0,
        to_currency="USD",
    )
    lost_then_settled(lose_next_commit)
    assert len(there(budget.view(), "lost transfer")) == 2, "two legs of one transfer"
    assert len(rows_here(memo="lost transfer")) == 1
    assert balances_here() == balances_there(budget.view())


def test_a_lost_answer_to_an_edit_a_delete_and_an_assigned(world, lose_next_commit):
    budget = world.seed()
    world.imported(budget)
    quiet()

    edit_here(row_here(memo="plain")["id"], memo="lost edit", from_amount=31.0)
    lost_then_settled(lose_next_commit)
    assert [r["amount"] for r in there(budget.view(), "lost edit")] == [-3100]

    as_person(ledger_service.bulk_delete_transactions, [row_here(memo="other row")["id"]])
    lost_then_settled(lose_next_commit)
    assert there(budget.view(), "other row") == []
    assert rows_here(memo="other row") == []

    planning.apply_budget_change(
        {
            "type": "assign",
            "month": THIS_MONTH,
            "envelope_id": envelope_here("ACME Food")["id"],
            "amount": 321.0,
        }
    )
    lose_next_commit["on"] = True
    run()
    sync()
    quiet()
    view = budget.view(THIS_MONTH)
    assert view["budget"][THIS_MONTH]["categories"][budget.ids["food"]]["budgeted"] == 32100
    assert assigned_here(THIS_MONTH, "ACME Food") == 321.0
    assert open_items() == []


def test_a_lost_answer_to_a_new_category_makes_no_second_one(world, lose_next_commit):
    from backend.services import categories as categories_service

    budget = world.seed()
    world.imported(budget)
    quiet()
    as_person(categories_service.create_category, "ACME New Here", kind="expense")
    lose_next_commit["on"] = True
    run()
    assert lose_next_commit["lost"] == 1
    sync()
    quiet()
    named = [c for c in budget.view()["categories"] if c["name"] == "ACME New Here"]
    assert len(named) == 1, "a lost answer must not make the category twice"


def test_a_person_identical_row_is_claimed_rather_than_doubled(world):
    """``_claim_unowned`` can't tell a lost answer from a person typing the very same row in Actual
    before our create goes. It claims it: one row each side, the person's row now paired. Accepted
    in the ADR; this pins what it does."""
    budget = world.seed()
    world.imported(budget)
    quiet()
    food = budget.ids["food"]
    new_row(memo="twin", description="ACME Twin", category=category_here("ACME Food")["id"])
    budget.call(
        "addTransactions",
        budget.ids["checking"],
        [
            {
                "date": TODAY,
                "amount": -1234,
                "payee_name": "ACME Twin",
                "category": food,
                "notes": "twin",
                "cleared": True,
            }
        ],
    )
    sync()
    assert len(there(budget.view(), "twin")) == 1
    assert len(rows_here(memo="twin")) == 1
    quiet()


# ── a person's edit inside a batch ──


class Run:
    """One run's client, with a hook at the point between a batch's pull and its commit."""

    def __init__(self, client) -> None:
        self.client = client
        self.seconds: list[float] = []

    def write(self, row_id: str, memo: str, inside=None) -> None:
        copy = self.client.copy
        pull, commit = copy.pull, copy.commit
        started = {}

        def timed_pull():
            pull()
            started["at"] = time.perf_counter()

        def hooked_commit():
            if inside:
                inside()
                started["at"] = time.perf_counter()
            commit()
            self.seconds.append(time.perf_counter() - started["at"])

        copy.pull, copy.commit = timed_pull, hooked_commit
        try:
            self.client.save_transactions("PATCH", [{"id": row_id, "memo": memo}])
        finally:
            copy.pull, copy.commit = pull, commit


def in_a_run(budget, body) -> Run:
    with using(ACTUAL):
        with writes.open_sync_client(budget.file, Budget(1000)) as client:
            client.read_plan(None)
            run_ = Run(client)
            body(run_)
            return run_


def the_row(view: dict, notes: str) -> str:
    (row,) = there(view, notes)
    return row["id"]


def test_an_edit_inside_a_batch_is_seen_by_the_next_batch(world):
    """The row the person edited isn't in this batch, and the next batch writes it: that batch
    must find it moved, not write over the person."""
    budget = world.seed()
    view = budget.view()
    first, second = the_row(view, "plain"), the_row(view, "other row")

    def body(run_: Run) -> None:
        run_.write(
            first, "ours", inside=lambda: budget.steps(["edit", "other row", {"notes": "person"}])
        )
        with pytest.raises(Moved):
            run_.write(second, "ours too")

    in_a_run(budget, body)
    after = budget.view()
    assert there(after, "person"), "the person's edit stands"
    assert not there(after, "ours too")


def test_an_edit_between_batches_is_seen_by_the_next_batch(world):
    budget = world.seed()
    view = budget.view()
    first, second = the_row(view, "plain"), the_row(view, "other row")

    def body(run_: Run) -> None:
        run_.write(first, "ours")
        budget.steps(["edit", "other row", {"notes": "person"}])
        with pytest.raises(Moved):
            run_.write(second, "ours too")

    in_a_run(budget, body)
    assert there(budget.view(), "person")


def test_an_edit_from_a_window_left_open_with_a_clock_behind_is_seen_by_the_next_batch(
    world, tmp_path
):
    """A person's window, open since before our commit, on a device whose clock is 90s behind: its
    edit, made after our commit, carries an earlier time than anything of ours."""
    budget = world.seed()
    view = budget.view()
    first, second = the_row(view, "plain"), the_row(view, "other row")
    ready, go = tmp_path / "ready", tmp_path / "go"
    person = threading.Thread(
        target=budget.server.run_job,
        args=(
            [["pause", str(ready), str(go)], ["edit", "other row", {"notes": "behind"}]],
            budget.group,
        ),
        kwargs={"clock_skew_ms": -90_000},
    )

    def body(run_: Run) -> None:
        person.start()
        while not ready.exists():
            time.sleep(0.02)
        run_.write(first, "ours")
        go.write_text("go")
        person.join(timeout=60)
        with pytest.raises(Moved):
            run_.write(second, "ours too")

    in_a_run(budget, body)
    assert there(budget.view(), "behind")


def test_the_same_field_inside_its_own_batch_is_the_race_left(world):
    """Nothing can close this: no compare-and-set on the server. The later timestamp wins, here
    ours, with nothing filed. The time between a batch's pull and its commit is the true window,
    measured over a real write and printed for the ADR."""
    budget = world.seed()
    view = budget.view()
    row = the_row(view, "plain")
    plain_seconds: list[float] = []

    def body(run_: Run) -> None:
        for number in range(20):
            run_.write(row, f"timing {number}")
        plain_seconds.extend(run_.seconds)
        run_.write(
            row, "ours", inside=lambda: budget.steps(["edit", "timing 19", {"notes": "person"}])
        )

    in_a_run(budget, body)
    assert there(budget.view(), "ours"), "ours carries the later timestamp"
    assert not there(budget.view(), "person")
    window = sorted(plain_seconds)
    print(
        f"\n[window] pull to commit answered, {len(window)} one-row batches: "
        f"median {window[len(window) // 2] * 1000:.0f} ms, worst {window[-1] * 1000:.0f} ms"
    )


def test_another_field_inside_the_batch_keeps_both(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="memo here")
    commit = actual_client.Copy.commit
    done = {"person": False}

    def person_first(self):
        if not done["person"]:
            done["person"] = True
            budget.steps(["edit", "plain", {"amount": -4444}])
        commit(self)

    actual_client.Copy.commit = person_first
    try:
        sync()
    finally:
        actual_client.Copy.commit = commit
    (row,) = there(budget.view(), "memo here")
    assert row["amount"] == -4444, "the server merges by field"
    sync()
    assert float(row_here(memo="memo here")["from_amount"]) == 44.44
    quiet()
    assert balances_here() == balances_there(budget.view())


def test_a_different_row_edited_during_a_real_run_is_not_lost(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="sent")
    commit = actual_client.Copy.commit
    done = {"person": False}

    def person_first(self):
        if not done["person"]:
            done["person"] = True
            budget.steps(["edit", "other row", {"notes": "edited during the run"}])
        commit(self)

    actual_client.Copy.commit = person_first
    try:
        sync()
    finally:
        actual_client.Copy.commit = commit
    sync()
    assert rows_here(memo="edited during the run")
    quiet()
