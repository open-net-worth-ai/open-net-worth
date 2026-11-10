"""The YNAB data sync's direction: From YNAB, To YNAB, or Both ways.

One way only skips the other way's sends or pulls; a row (or a month's Assigned) changed on BOTH
sides still goes to the review list, whatever the direction. Resolving it never writes to the side
the direction protects: it is accepted where it stands, and a later change on both sides asks
again. Switching back to Both ways sends or pulls what was held."""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import envelopes as envelopes_service
from backend.services import planning, ynab_sync
from tests import ynab_stub
from tests.test_ynab_sync_service import assign, edit_onw, row, sync, world  # noqa: F401

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


def enable(direction):
    return ynab_sync.update_settings(True, "1h", ynab_stub.PLAN, direction)


def _bistro(world):  # noqa: F811
    return ynab_stub.find(world.state, payee_name="Bistro")


def _fun_assigned():
    env = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Fun Money")
    return max(
        planning.assignments_store.month_map(m).get(env["id"], 0)
        for m in planning.assignments_store.months()
    )


def test_both_ways_is_the_default_and_a_bad_direction_is_refused(world):  # noqa: F811
    status = ynab_sync.update_settings(True, "1h", ynab_stub.PLAN)
    assert status["direction"] == "both"
    assert status["directions"] == ["both", "from_ynab", "to_ynab"]
    with pytest.raises(ValidationError):
        ynab_sync.update_settings(None, None, None, "sideways")


def test_from_ynab_pulls_and_never_sends(world):  # noqa: F811
    enable("from_ynab")
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Pizza")["id"], memo="theirs"
    )
    assign(0, "Fun Money", 70)
    run = sync()
    assert run["summary"] == "1 pulled" and world.writes == []
    assert row("Pizza")["memo"] == "theirs"
    assert row("Bistro")["memo"] == "mine" and _bistro(world).get("memo") != "mine"


def test_to_ynab_sends_and_never_pulls(world):  # noqa: F811
    enable("to_ynab")
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Pizza")["id"], memo="theirs"
    )
    ynab_stub.ynab_budget(world.state, 0, "fun", 60_000)
    before = _fun_assigned()
    run = sync()
    assert run["summary"] == "1 sent to YNAB"
    assert _bistro(world)["memo"] == "mine"
    assert row("Pizza")["memo"] != "theirs" and _fun_assigned() == before


def test_one_way_still_asks_when_both_sides_changed_and_keeping_the_held_side_writes_nothing(
    world,  # noqa: F811
):
    enable("from_ynab")
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(world.state, _bistro(world)["id"], memo="theirs")
    assert sync()["summary"] == "1 needs review"
    (item,) = ynab_sync.status()["conflicts"]
    ynab_sync.resolve.resolve(item["id"], "onw")  # keep this book's: YNAB is not written
    assert world.writes == [] and _bistro(world)["memo"] == "theirs"
    assert row("Bistro")["memo"] == "mine"
    assert sync()["summary"] == "Nothing changed" and world.writes == []
    ynab_stub.ynab_edit(world.state, _bistro(world)["id"], memo="theirs again")
    assert sync()["summary"] == "1 needs review"  # both sides differ again: asked again


def test_switching_back_to_both_ways_sends_what_was_held(world):  # noqa: F811
    enable("from_ynab")
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    sync()
    assert world.writes == []
    enable("both")
    assert sync()["summary"] == "1 sent to YNAB"
    assert _bistro(world)["memo"] == "mine"
