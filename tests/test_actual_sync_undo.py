"""Undo the last sync here: what it changed in this book is put back and the sync is switched
off. Same harness as ``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.actual_sync import app as actual_app
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import by_ref, edit_here, enable, row, sync

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _live(ref: str) -> bool:
    found = [
        t
        for t in ledger_service.transactions.all()
        if any(e["ref"] == ref for e in t.get("external_refs") or [])
    ]
    return bool(found) and not found[0].get("deleted")


def test_undo_puts_back_an_edit_a_new_row_and_a_deletion_and_switches_off(world):
    enable()
    sync()
    before_memo = by_ref("t-market")["memo"]
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.edit("UPDATE transactions SET tombstone = 1 WHERE id = 't-pay'")
    world.txn(
        "t-new",
        "a1",
        -700,
        engine.TODAY,
        description="p-market",
        category="c-fun",
        notes="brand new",
    )
    world.queue.clear()
    sync()
    assert by_ref("t-market")["memo"] == "there"
    assert not _live("t-pay") and _live("t-new")

    done = actual_app.undo_last_sync()
    assert done["put_back"] == 3 and done["skipped"] == [] and done["sync_off"]
    assert by_ref("t-market")["memo"] == before_memo
    assert _live("t-pay") and not _live("t-new")
    assert done["status"]["enabled"] is False
    with pytest.raises(ValidationError):
        actual_app.undo_last_sync()


def test_a_row_edited_here_since_is_left_alone_and_named(world):
    enable()
    sync()
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    edit_here(by_ref("t-market")["id"], memo="mine now")
    done = actual_app.undo_last_sync()
    assert done["put_back"] == 0 and len(done["skipped"]) == 1
    assert by_ref("t-market")["memo"] == "mine now"


def test_nothing_to_undo_says_so(world):
    enable()
    sync()
    with pytest.raises(ValidationError, match="No sync has changed this book"):
        actual_app.undo_last_sync()
    assert row("ACME Corp")
