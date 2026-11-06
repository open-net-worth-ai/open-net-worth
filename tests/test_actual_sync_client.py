"""``ActualClient``: every batch is sync, re-check, apply, commit, on a real ``actualpy`` session over
a made-up budget (``actual_sync_stub``). Every name and number is invented."""

from __future__ import annotations

import time
from contextlib import contextmanager

import httpx
import pytest

from backend.core.errors import ExternalServiceError
from backend.core.user_time import user_today
from backend.services.actual_sync import writes
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync import assigned
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync.client import Budget
from tests.actual_sync_stub import BUDGET, HOST, PASSWORD, FakeServer, down, seed

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

TODAY = user_today().isoformat()
KEY = TODAY[:7]
PAST = "2020-01-01"


@pytest.fixture
def server(db_isolated, tmp_path, monkeypatch):
    made = FakeServer(tmp_path)
    seed(made, KEY, PAST, TODAY)
    made.install(monkeypatch)
    with using(ACTUAL):
        yield made


@contextmanager
def opened(server, read=True):
    with server.open_copy(None, BUDGET) as copy:
        client = writes.ActualClient(copy, Budget(1000))
        if read:
            client.read_plan(None)
        yield client


def body(**over) -> dict:
    return {
        "date": TODAY,
        "memo": None,
        "cleared": "cleared",
        "approved": True,
        "account_id": "a1",
        "amount": -12_340,
        "payee_name": "ACME Coffee",
        "category_id": "c-food",
        **over,
    }


def line(milli: int, category: str = "c-food", memo: str | None = None) -> dict:
    return {"amount": milli, "category_id": category, "memo": memo}


# ── the batch ──


def test_batch_order_sync_recheck_apply_commit(server):
    with opened(server) as client:
        server.edit("UPDATE transactions SET notes = 'in Actual' WHERE id = 't-market'")
        with pytest.raises(ynab_client.Moved):
            client.save_transactions("PATCH", [{"id": "t-market", "memo": "here"}])
        assert server.calls == ["sync"]  # refused after the sync, and nothing was sent
        assert server.row("transactions", "t-market")["notes"] == "in Actual"

        server.calls.clear()
        (saved,) = client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
        assert server.calls == ["sync", "commit"]
        assert saved["memo"] == "bonus"
        assert server.row("transactions", "t-pay")["notes"] == "bonus"


def test_a_later_batch_still_sees_a_move_an_earlier_one_synced_in(server):
    with opened(server) as client:
        server.edit("UPDATE transactions SET notes = 'in Actual' WHERE id = 't-market'")
        client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
        with pytest.raises(ynab_client.Moved):
            client.save_transactions("PATCH", [{"id": "t-market", "memo": "here"}])


def test_our_own_write_is_not_read_as_a_move(server):
    with opened(server) as client:
        client.save_transactions("PATCH", [{"id": "t-pay", "memo": "one"}])
        client.save_transactions("PATCH", [{"id": "t-pay", "memo": "two"}])
    assert server.row("transactions", "t-pay")["notes"] == "two"


def test_never_syncs_over_uncommitted(server):
    with opened(server) as client:
        client.copy.session.get(client.copy.db.Transactions, "t-pay").notes = "half done"
        with pytest.raises(ynab_client.Stopped):
            client.save_transactions("PATCH", [{"id": "t-market", "memo": "here"}])
        assert server.calls == [] and not server.synced_over_uncommitted


def test_a_commit_error_stops_every_later_write_with_no_sync(server):
    server.commit_error = (down(), False)
    with opened(server) as client:
        with pytest.raises(ynab_client.Stopped) as stopped:
            client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
        assert not ynab_client.rejected(stopped.value)
        server.calls.clear()
        with pytest.raises(ynab_client.Stopped):
            client.save_transactions("PATCH", [{"id": "t-market", "memo": "here"}])
        assert server.calls == []
    assert server.row("transactions", "t-pay")["notes"] is None


def test_an_answer_with_a_status_is_not_read_as_a_refusal_of_the_row(server):
    response = httpx.Response(400, request=httpx.Request("POST", f"http://{HOST}/sync"))
    server.sync_error = httpx.HTTPStatusError(
        f"{HOST} {PASSWORD}", request=response.request, response=response
    )
    with opened(server) as client:
        with pytest.raises(ynab_client.Stopped) as stopped:
            client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
    assert "HTTP 400" in stopped.value.message
    assert not ynab_client.rejected(stopped.value)
    assert not ynab_client.not_found(stopped.value)


def test_errors_carry_no_address_or_password(server):
    server.sync_error = down()
    with opened(server) as client:
        with pytest.raises(ExternalServiceError) as caught:
            client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
    error: BaseException | None = caught.value
    while error is not None:
        assert HOST not in str(error) and PASSWORD not in str(error)
        error = error.__cause__ or error.__context__


def test_a_run_past_its_deadline_stops_between_batches(server):
    with opened(server) as client:
        client._deadline = time.monotonic() - 1
        with pytest.raises(ynab_client.Stopped) as stopped:
            client.save_transactions("PATCH", [{"id": "t-pay", "memo": "bonus"}])
    assert "too long" in stopped.value.message
    assert server.calls == []


# ── reading back ──


def test_saved_rows_read_back_in_shape(server):
    with opened(server) as client:
        (saved,) = client.save_transactions("POST", [body(memo="latte")])
    assert saved["id"] and saved["deleted"] is False
    assert saved["account_id"] == "a1" and saved["amount"] == -12_340
    assert saved["memo"] == "latte" and saved["cleared"] == "cleared"
    assert saved["category_id"] == "c-food" and saved["payee_name"] == "ACME Coffee"
    assert saved["subtransactions"] == [] and saved["transfer_account_id"] is None
    row = server.row("transactions", saved["id"])
    assert (row["amount"], row["date"]) == (-1_234, int(TODAY.replace("-", "")))


def test_split_lines_read_back_in_the_order_they_were_written(server):
    rows = [line(-2_000, "c-food", "a"), line(-3_000, "c-fun", "b"), line(-7_340, "c-food", "c")]
    with opened(server) as client:
        (saved,) = client.save_transactions("POST", [body(category_id=None, subtransactions=rows)])
    assert [s["memo"] for s in saved["subtransactions"]] == ["a", "b", "c"]
    fresh = next(t for t in server.plan()["transactions"] if t["id"] == saved["id"])
    assert fresh["amount"] == -12_340


def test_split_sum_refused_before_write(server):
    rows = [line(-2_000), line(-3_000)]
    with opened(server) as client:
        with pytest.raises(ynab_client.Refused) as refused:
            client.save_transactions("POST", [body(category_id=None, subtransactions=rows)])
    assert "add up" in refused.value.message
    assert server.calls == []
    assert not server.live("amount = -1234")


def test_a_fraction_of_a_cent_is_refused_before_write(server):
    with opened(server) as client:
        with pytest.raises(ynab_client.Refused):
            client.save_transactions("POST", [body(amount=-12_345)])
    assert server.calls == []


def test_rules_not_run(server):
    server.insert(
        "rules",
        id="r1",
        stage="pre",
        conditions_op="and",
        conditions='[{"op":"is","field":"imported_payee","value":"ACME Coffee"}]',
        actions='[{"op":"set","field":"category","value":"c-fun"}]',
        tombstone=0,
    )
    with opened(server) as client:
        (saved,) = client.save_transactions("POST", [body()])
    assert server.row("transactions", saved["id"])["category"] == "c-food"


def test_transfer_uses_transfer_payee(server):
    op = {
        "date": TODAY,
        "memo": "move",
        "cleared": "cleared",
        "approved": True,
        "account_id": "a1",
        "payee_id": "pt-a3",
        "amount": -5_000,
    }
    with opened(server) as client:
        (saved,) = client.save_transactions("POST", [op])
    twin = server.row("transactions", saved["transfer_transaction_id"])
    mine = server.row("transactions", saved["id"])
    assert mine["description"] == "pt-a3" and twin["description"] == "pt-a1"
    assert mine["transferred_id"] == twin["id"] and twin["transferred_id"] == mine["id"]
    assert (mine["amount"], twin["amount"], twin["acct"]) == (-500, 500, "a3")
    assert mine["category"] is None and twin["category"] is None
    assert saved["transfer_account_id"] == "a3"


def test_a_categorized_transfer_keeps_it_on_its_budget_leg(server):
    op = {
        "date": TODAY,
        "memo": None,
        "cleared": "cleared",
        "account_id": "a1",
        "payee_id": "pt-a2",
        "amount": -5_000,
        "category_id": "c-fun",
    }
    with opened(server) as client:
        (saved,) = client.save_transactions("POST", [op])
    assert server.row("transactions", saved["id"])["category"] == "c-fun"
    assert server.row("transactions", saved["transfer_transaction_id"])["category"] is None
    assert saved["category_id"] == "c-fun"


def test_deleting_a_transfer_takes_both_legs_and_a_split_its_lines(server):
    with opened(server) as client:
        client.delete_transaction("t-out")
        client.delete_transaction("t-split")
    assert not server.live("id IN ('t-out', 't-in', 't-split', 't-l1', 't-l2')")


def test_deleting_a_row_already_gone_says_not_found(server):
    with opened(server) as client:
        server.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-pay'")
        with pytest.raises(ynab_client.Refused) as gone:
            client.delete_transaction("t-pay")
    assert ynab_client.not_found(gone.value)


def test_a_row_edited_in_actual_is_not_deleted(server):
    with opened(server) as client:
        server.edit("UPDATE transactions SET notes = 'keep me' WHERE id = 't-pay'")
        with pytest.raises(ynab_client.Moved):
            client.delete_transaction("t-pay")
    assert server.row("transactions", "t-pay")["tombstone"] == 0


def test_a_split_edit_rewrites_every_line_and_keeps_their_ids(server):
    op = {
        "id": "t-split",
        "date": TODAY,
        "amount": -70_000,
        "subtransactions": [
            line(-40_000, "c-fun", "x"),
            line(-20_000, "c-food", "y"),
            line(-10_000, "c-food", "z"),
        ],
        "category_id": None,
    }
    with opened(server) as client:
        (saved,) = client.save_transactions("PATCH", [op])
    assert saved["amount"] == -70_000
    assert [s["memo"] for s in saved["subtransactions"]] == ["x", "y", "z"]
    live = server.live("parent_id = 't-split'")
    assert {r["id"] for r in live} >= {"t-l1", "t-l2"} and len(live) == 3
    assert server.row("transactions", "t-l1")["category"] == "c-fun"


def test_a_split_with_fewer_lines_tombstones_the_rest(server):
    op = {
        "id": "t-split",
        "amount": -50_000,
        "subtransactions": [line(-50_000, "c-fun", "all")],
        "category_id": None,
    }
    with opened(server) as client:
        client.save_transactions("PATCH", [op])
    assert [r["id"] for r in server.live("parent_id = 't-split'")] == ["t-l1"]
    assert server.row("transactions", "t-l2")["tombstone"] == 1


def test_splitting_a_plain_row_and_unsplitting_it(server):
    split = {
        "id": "t-market",
        "amount": -25_000,
        "subtransactions": [line(-20_000, "c-fun"), line(-5_000, "c-food")],
        "category_id": None,
    }
    with opened(server) as client:
        (saved,) = client.save_transactions("PATCH", [split])
        assert len(saved["subtransactions"]) == 2 and saved["category_id"] is None
        assert server.row("transactions", "t-market")["isParent"] == 1
        plain = {"id": "t-market", "amount": -25_000, "subtransactions": [], "category_id": "c-fun"}
        (saved,) = client.save_transactions("PATCH", [plain])
    assert saved["subtransactions"] == [] and saved["category_id"] == "c-fun"
    row = server.row("transactions", "t-market")
    assert row["isParent"] == 0 and not server.live("parent_id = 't-market'")


def test_a_blank_category_is_never_written_to_an_opening_row(server):
    with opened(server) as client:
        client.save_transactions("PATCH", [{"id": "t-open", "category_id": None, "memo": "note"}])
    row = server.row("transactions", "t-open")
    assert row["category"] == "c-sb" and row["notes"] == "note"


def test_a_reconciled_row_cannot_be_uncleared_from_here(server):
    server.edit("UPDATE transactions SET reconciled = 1 WHERE id = 't-pay'")
    server.queue.clear()
    with opened(server) as client:
        with pytest.raises(ynab_client.Refused) as refused:
            client.save_transactions("PATCH", [{"id": "t-pay", "cleared": "uncleared"}])
    assert "reconciled" in refused.value.message


def test_a_gone_category_is_refused_not_written(server):
    with opened(server) as client:
        server.edit("UPDATE categories SET tombstone = 1 WHERE id = 'c-fun'")
        with pytest.raises(ynab_client.Refused):
            client.save_transactions("POST", [body(category_id="c-fun")])
    assert not server.live("amount = -1234")


def test_a_payee_is_found_whatever_its_case(server):
    with opened(server) as client:
        client.save_transactions("POST", [body(payee_name="corner market")])
    assert len(server.rows("payees", "lower(name) = 'corner market'")) == 1


# ── Assigned ──


def test_assigned_writes_the_cell_in_cents(server):
    with opened(server) as client:
        client.set_assigned(KEY, "c-food", 123_450)
        client.set_assigned(KEY, "c-pay", 0)
        new_month = f"{int(KEY[:4]) + 1}-01"
        client.set_assigned(new_month, "c-fun", 5_000)
    month = int(KEY.replace("-", ""))
    (cell,) = server.rows("zero_budgets", "month = ? AND category = 'c-food'", month)
    assert cell["amount"] == 12_345
    (added,) = server.rows(
        "zero_budgets", "month = ? AND category = 'c-fun'", int(new_month.replace("-", ""))
    )
    assert added["id"] == f"{new_month.replace('-', '')}-c-fun" and added["amount"] == 500


def test_a_cell_that_moved_is_refused(server):
    with opened(server) as client:
        server.edit("UPDATE zero_budgets SET amount = 1 WHERE category = 'c-food'")
        with pytest.raises(ynab_client.Moved):
            client.set_assigned(KEY, "c-food", 123_450)
    (cell,) = server.rows("zero_budgets", "category = 'c-food'")
    assert cell["amount"] == 1


def test_no_assigned_goes_to_a_tracking_budget(server):
    server.mode = "tracking"
    with opened(server) as client:
        with pytest.raises(ynab_client.Refused):
            client.set_assigned(KEY, "c-food", 123_450)
    assert server.calls == []


# ── categories and notes ──


def test_note_line_written_and_capped(server):
    server.insert("notes", id="c-food", note="mine\n" + "\n".join(
        f"ONW 2026-{m:02d}: assigned 1.00 (was 0.00)" for m in range(1, 11)
    ))  # fmt: skip
    with opened(server) as client:
        category = next(
            c for c in client.read_plan(None)["plan"]["categories"] if c["id"] == "c-food"
        )
        item = assigned.Item(KEY, "c-food", "env", 400_000, 500_000, 400_000, "push", False)
        sent, _, rejected = assigned.push(client, [item], {"c-food": category})
    assert sent == [item] and rejected == []
    note = server.row("notes", "c-food")["note"].split("\n")
    assert note[0] == "mine"
    assert len(note) == 11 and note[-1] == f"ONW {KEY}: assigned 500.00 (was 400.00)"
    assert "2026-01" not in note[1]


def test_a_note_is_written_where_there_was_none(server):
    with opened(server) as client:
        assert client.set_note("c-fun", "line")["note"] == "line"
    assert server.row("notes", "c-fun")["note"] == "line"


def test_a_note_changed_in_actual_is_left_alone(server):
    server.insert("notes", id="c-food", note="mine")
    with opened(server) as client:
        server.edit("UPDATE notes SET note = 'edited in Actual' WHERE id = 'c-food'")
        assert client.set_note("c-food", "mine\nONW line")["note"] == "edited in Actual"
    assert server.row("notes", "c-food")["note"] == "edited in Actual"


def test_a_note_too_long_to_read_whole_is_left_alone(server):
    long = "x" * 600
    server.insert("notes", id="c-food", note=long)
    with opened(server) as client:
        client.set_note("c-food", "x" * 500 + "\nONW line")
    assert server.row("notes", "c-food")["note"] == long


def test_hidden_written(server):
    with opened(server) as client:
        saved = client.update_category("c-fun", {"hidden": True})
    assert saved["hidden"] is True
    row = server.row("categories", "c-fun")
    assert row["hidden"] == 1 and row["tombstone"] == 0 and row["cat_group"] == "g1"


def test_renaming_and_moving_a_category(server):
    with opened(server) as client:
        made = client.create_group("ACME Group")
        saved = client.update_category("c-fun", {"name": "Joy", "category_group_id": made["id"]})
    assert saved["name"] == "Joy" and saved["category_group_id"] == made["id"]
    assert server.row("category_groups", made["id"])["is_income"] == 0


def test_a_new_category_takes_its_kind_from_its_group(server):
    with opened(server) as client:
        spending = client.create_category("g1", "Pets")
        income = client.create_category("gi", "Rental")
    assert spending["is_income"] is False and income["is_income"] is True
    assert server.row("categories", income["id"])["is_income"] == 1
    assert server.row("category_mapping", income["id"])["transferId"] == income["id"]


def test_a_category_that_moved_is_refused(server):
    with opened(server) as client:
        server.edit("UPDATE categories SET name = 'Fun Fun' WHERE id = 'c-fun'")
        with pytest.raises(ynab_client.Moved):
            client.update_category("c-fun", {"name": "Joy"})
    assert server.row("categories", "c-fun")["name"] == "Fun Fun"


def test_renaming_a_group(server):
    with opened(server) as client:
        assert client.update_group("g1", "Everyday")["name"] == "Everyday"
    assert server.row("category_groups", "g1")["name"] == "Everyday"


# ── a read ──


def test_a_later_read_says_only_what_changed(server):
    with opened(server, read=False) as client:
        whole = client.read_plan(None)["plan"]
    assert len(whole["transactions"]) == 6
    from backend.services.ynab_sync import delta

    mirror: dict = {}
    meta = {"accounts": [], "groups": [], "categories": [], "payees": {}, "months": []}
    delta.apply_read({"plan": whole, "server_knowledge": 1}, mirror, meta, True)
    ACTUAL.stores.mirror().replace_all(list(mirror.values()))
    ACTUAL.stores.state().save({**meta, "id": "meta"})
    with opened(server, read=False) as client:
        again = client.read_plan(1)
    assert again["plan"]["transactions"] == [] and again["plan"]["subtransactions"] == []
    assert again["server_knowledge"] == writes.KNOWLEDGE
