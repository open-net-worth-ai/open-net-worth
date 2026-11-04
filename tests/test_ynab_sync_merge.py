"""A merge moves a synced row's YNAB ids onto the surviving row. The sync must follow them: the
tombstone is not a deletion, so nothing is deleted in YNAB, and the survivor is the row compared
with YNAB from then on. Undoing the merge moves the link back."""

from __future__ import annotations

import pytest

from backend.core import audit_context
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services import match_review, ynab_sync
from backend.services.ynab_sync import units
from tests import ynab_stub
from tests.test_ynab_sync_service import (  # noqa: F401
    edit_onw,
    enable,
    patches,
    posts,
    row,
    sync,
    world,
)

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


def book_twin(bistro, description="Bistro Downtown", **kw):
    """A hand-booked duplicate of the synced Bistro row: same day, account and amount."""
    token = audit_context.set_audit_scope("user", "ui")
    try:
        return ledger_service.create_transaction(
            {
                "needs_review": False,
                "kind": "expense",
                "date": bistro["date"],
                "description": description,
                "from_account_id": bistro["from_account_id"],
                "from_amount": bistro["from_amount"],
                "from_currency": bistro["from_currency"],
                **kw,
            },
            allow_duplicate=True,
        )
    finally:
        audit_context.clear_audit_scope(token)


def merge(canonical_id, absorbed_id):
    token = audit_context.set_audit_scope("user", "ui")
    try:
        return match_review.merge_transactions_preserving_fields(canonical_id, absorbed_id)[0]
    finally:
        audit_context.clear_audit_scope(token)


def deletes(world):  # noqa: F811
    return [w for w in world.writes if w[0] == "DELETE"]


def ynab_row(world, ynab_id):  # noqa: F811
    return next(
        (t for t in world.state["txns"] if t["id"] == ynab_id and not t.get("deleted")), None
    )


def test_a_synced_row_merged_into_another_stays_in_ynab_and_the_survivor_is_paired(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, memo="booked by hand")
    merge(twin["id"], bistro["id"])

    sync()
    assert deletes(world) == []
    assert ynab_row(world, refs[0])["memo"] == "booked by hand"  # the survivor's version went out
    assert ynab_row(world, refs[0])["payee_name"] == "Bistro Downtown"
    assert posts(world) == []
    assert units.ynab_refs(row("Bistro Downtown")) == refs

    writes = len(world.writes)
    assert sync()["summary"] == "Nothing changed" and len(world.writes) == writes

    edit_onw(row("Bistro Downtown")["id"], memo="edited after the merge")
    assert sync()["summary"] == "1 sent to YNAB"  # the survivor is paired for good
    assert ynab_row(world, refs[0])["memo"] == "edited after the merge"
    assert deletes(world) == []


def test_a_hand_booked_row_merged_into_a_synced_one_stays_in_ynab(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, memo="booked by hand")
    merge(bistro["id"], twin["id"])

    sync()
    assert deletes(world) == [] and posts(world) == []
    assert ynab_row(world, refs[0])["memo"] == "booked by hand"  # adopted by the merge
    assert ynab_row(world, refs[0])["payee_name"] == "Bistro"
    assert sync()["summary"] == "Nothing changed"


def test_a_ynab_edit_after_the_merge_lands_on_the_survivor(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, description="Bistro")  # identical, so the merge changes nothing
    merge(twin["id"], bistro["id"])
    sync()

    ynab_stub.ynab_edit(world.state, refs[0], memo="from ynab")
    assert sync()["summary"] == "1 pulled"
    assert row("Bistro")["id"] == twin["id"] and row("Bistro")["memo"] == "from ynab"
    assert deletes(world) == []


def test_a_ynab_delete_after_the_merge_deletes_the_survivor(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, description="Bistro")
    merge(twin["id"], bistro["id"])
    sync()

    ynab_stub.ynab_delete(world.state, refs[0])
    assert sync()["summary"] == "1 pulled"
    assert not [
        t
        for t in ledger_service.transactions.all()
        if t["description"] == "Bistro" and not t.get("deleted")
    ]


def test_a_merge_before_the_first_run_deletes_nothing_in_ynab(world):  # noqa: F811
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, memo="booked by hand")
    merge(twin["id"], bistro["id"])

    enable()
    sync()
    assert deletes(world) == [] and posts(world) == []
    assert ynab_row(world, refs[0])["memo"] == "booked by hand"  # compared with what was booked
    assert ynab_row(world, refs[0])["payee_name"] == "Bistro Downtown"
    assert sync()["summary"] == "Nothing changed"


def test_two_merges_in_a_row_are_followed_to_the_last_survivor(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    middle = book_twin(bistro, description="Bistro Middle")
    last = book_twin(bistro, description="Bistro Last")
    merge(middle["id"], bistro["id"])
    merge(last["id"], middle["id"])

    sync()
    assert deletes(world) == [] and posts(world) == []
    assert ynab_row(world, refs[0])["payee_name"] == "Bistro Last"
    assert sync()["summary"] == "Nothing changed"


def test_the_survivor_deleted_before_the_next_run_is_a_real_deletion(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro)
    merge(twin["id"], bistro["id"])
    ledger_service.bulk_delete_transactions([twin["id"]])

    sync()
    assert len(deletes(world)) == 1 and ynab_row(world, refs[0]) is None
    assert sync()["summary"] == "Nothing changed"


def test_undoing_a_merge_puts_the_link_back_and_sends_the_other_row_as_new(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro, memo="booked by hand")
    merge(twin["id"], bistro["id"])
    sync()

    ledger_service.unmerge_transactions(bistro["id"])
    sync()
    assert deletes(world) == []
    assert units.ynab_refs(row("Bistro")) == refs
    assert ynab_row(world, refs[0])["payee_name"] == "Bistro"  # the restored row's version
    assert ynab_row(world, refs[0])["memo"] != "booked by hand"
    assert len(posts(world)) == 1  # the hand-booked row is now a row of its own in YNAB
    new_refs = units.ynab_refs(row("Bistro Downtown"))
    assert new_refs and new_refs != refs
    assert sync()["summary"] == "Nothing changed"


def test_undoing_a_merge_the_sync_never_saw_changes_nothing_about_the_link(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    twin = book_twin(bistro)
    merge(twin["id"], bistro["id"])
    ledger_service.unmerge_transactions(bistro["id"])

    sync()
    assert deletes(world) == [] and patches(world) == []
    assert units.ynab_refs(row("Bistro")) == refs
    assert len(posts(world)) == 1


def test_an_open_review_item_follows_the_merge_to_the_survivor(world):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    edit_onw(bistro["id"], memo="mine")
    ynab_stub.ynab_edit(world.state, refs[0], memo="theirs")
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    assert item["onw_txn_id"] == bistro["id"]

    twin = book_twin(row("Bistro"), description="Bistro")
    merge(twin["id"], bistro["id"])
    sync()
    (item,) = ynab_sync.status()["conflicts"]  # asked again, about the row that holds the link
    assert item["onw_txn_id"] == twin["id"] and item["ynab"]["memo"] == "theirs"
    assert deletes(world) == []

    ynab_sync.resolve.resolve(item["id"], "ynab")
    assert row("Bistro")["id"] == twin["id"] and row("Bistro")["memo"] == "theirs"
    assert deletes(world) == [] and sync()["summary"] == "Nothing changed"


@pytest.mark.parametrize("keep", ["onw", "ynab"])
def test_resolving_a_stale_item_after_a_merge_never_deletes_in_ynab(world, keep):  # noqa: F811
    enable()
    sync()
    bistro = row("Bistro")
    refs = units.ynab_refs(bistro)
    edit_onw(bistro["id"], memo="mine")
    ynab_stub.ynab_edit(world.state, refs[0], memo="theirs")
    sync()
    (item,) = ynab_sync.status()["conflicts"]

    twin = book_twin(row("Bistro"), description="Bistro", memo="mine")
    merge(twin["id"], bistro["id"])
    ynab_sync.resolve.resolve(item["id"], keep)  # the sync has not run since the merge

    assert deletes(world) == []
    want = "mine" if keep == "onw" else "theirs"
    assert ynab_row(world, refs[0])["memo"] == want
    assert row("Bistro")["id"] == twin["id"] and row("Bistro")["memo"] == want
    assert ynab_sync.status()["conflicts"] == []
    assert sync()["summary"] == "Nothing changed"


def test_two_rows_ynab_holds_separately_cannot_be_merged(world):  # noqa: F811
    enable()
    sync()
    with pytest.raises(ValidationError):
        merge(row("Bistro")["id"], row("Pizza")["id"])
    assert sync()["summary"] == "Nothing changed" and world.writes == []


def test_a_forced_merge_of_two_synced_rows_still_deletes_nothing_in_ynab(world):  # noqa: F811
    enable()
    sync()
    bistro, pizza = row("Bistro"), row("Pizza")
    token = audit_context.set_audit_scope("user", "ui")
    try:
        ledger_service.merge_transactions(pizza["id"], bistro["id"], override=True)
    finally:
        audit_context.clear_audit_scope(token)

    sync()
    assert deletes(world) == []
    for ynab_id in units.ynab_refs(bistro) + units.ynab_refs(pizza):
        assert ynab_row(world, ynab_id) is not None
