"""Actual keeps tags in a note as ``#name``: they become the ledger row's tags at the import, when
a note changes in Actual, and once for rows imported before. Every name is made up."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services.budget_app import YNAB
from backend.services.memo_tags import tags_in, tags_of
from tests.actual_sync_stub import seed
from tests.test_actual_sync_engine import (  # noqa: F401, F811 — the fixture
    KEY,
    PAST,
    TODAY,
    _import,
    _prepared,
    edit_here,
    enable,
    sync,
    world,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def by_ref(ref: str) -> dict:
    return next(
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and any(e["ref"] == ref for e in t.get("external_refs") or [])
    )


@pytest.mark.parametrize(
    "note,tags",
    [
        ("#groceries weekly shop #acme-trip", ["groceries", "acme-trip"]),
        ("#a#b", ["a"]),
        ("price ##1 and #ok", ["ok"]),
        ("see https://acme.test/#top", []),
        ("#dup #dup", ["dup"]),
        ("", []),
        (None, []),
    ],
)
def test_tags_read_as_actual_reads_them(note, tags):
    assert tags_in(note) == tags


def test_split_lines_carry_tags_too():
    row = {"memo": "#home", "splits": [{"memo": "#garden"}, {"memo": "#home x"}]}
    assert tags_of(row) == ["home", "garden"]


def test_only_actual_reads_tags_from_notes():
    assert YNAB.caps.memo_tags is False


def test_the_import_tags_rows_from_their_notes(db_isolated, tmp_path, monkeypatch):
    server = _prepared(tmp_path, monkeypatch)
    seed(server, KEY, PAST, TODAY)
    server.edit("UPDATE transactions SET notes = '#groceries for #acme-trip' WHERE id = 't-market'")
    _import()
    market = by_ref("t-market")
    assert market["tags"] == ["groceries", "acme-trip"]
    assert market["memo"] == "#groceries for #acme-trip"


def test_a_note_changed_in_actual_adds_its_tags_and_keeps_ours(world):  # noqa: F811
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], tags=["mine"])
    world.edit("UPDATE transactions SET notes = 'weekly #groceries' WHERE id = 't-market'")
    sync()
    assert by_ref("t-market")["tags"] == ["mine", "groceries"]
    assert world.row("transactions", "t-market")["notes"] == "weekly #groceries"


def test_rows_imported_before_gain_their_tags_once(world):  # noqa: F811
    market = by_ref("t-market")
    edit_here(market["id"], memo="#groceries")
    ledger_service.update_transaction(market["id"], {"tags": []})
    enable("from_ynab")
    sync()
    assert by_ref("t-market")["tags"] == ["groceries"]

    edit_here(market["id"], tags=[])
    sync()
    assert by_ref("t-market")["tags"] == [], "a tag removed here stays removed"
