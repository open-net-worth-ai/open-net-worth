"""Fetching a YNAB plan only reads, so the two-way sync no longer blocks it. The block once came
back as ``connect_error`` and was stored as the connector's status: the card read "Error / Needs
attention" while the sync worked, and nothing could clear it. What the block guarded against is
handled where it belongs, in "is this row already booked": an imported plan's Starting Balance
rows, which the import folded into each account's opening, read as booked, not new."""

from __future__ import annotations

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.services import connector_fetch, import_runs
from tests import ynab_stub
from tests.test_ynab_sync_service import enable, sync, world  # noqa: F401

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)

EVER = "2000-01-01"


def _status() -> tuple[str, str | None]:
    cfg = YnabConfigStore().get()
    return cfg["last_status"], cfg.get("last_error")


def test_a_synced_plan_can_be_fetched_and_offers_nothing_already_booked(world):  # noqa: F811
    YnabConfigStore().update({"enabled": True})
    enable()
    sync()

    result = connector_fetch.fetch("ynab", plan_id=ynab_stub.PLAN, since=EVER)

    assert result["status"] == "ok"
    assert result["transactions"] == []
    assert result["counts"]["already_booked"] == result["counts"]["fetched"] > 0
    assert _status() == ("ok", None)
    assert world.writes == []


def test_an_imported_starting_balance_is_its_accounts_opening(world):  # noqa: F811
    from backend.services import ledger as ledger_service

    YnabConfigStore().update({"enabled": True})
    result = connector_fetch.fetch("ynab", plan_id=ynab_stub.PLAN, since=EVER, include_booked=True)

    openings = ledger_service.opening_txn_ids()
    starts = [r for r in result["transactions"] if r["description"] == "Starting Balance"]
    assert starts and all(r["booked_txn_id"] in openings for r in starts)
    assert all("is_opening" not in r for r in result["transactions"])


def test_a_plan_never_imported_keeps_its_starting_balance_rows(world, monkeypatch):  # noqa: F811
    YnabConfigStore().update({"enabled": True})
    monkeypatch.setattr(import_runs, "plan_imported", lambda *_: False)

    result = connector_fetch.fetch("ynab", plan_id=ynab_stub.PLAN, since=EVER)

    starts = [r for r in result["transactions"] if r["description"] == "Starting Balance"]
    assert starts


def test_a_successful_sync_clears_a_failure_the_old_block_stored(world):  # noqa: F811
    YnabConfigStore().update({"enabled": True})
    enable()
    YnabConfigStore().record_status("connect_error", error="The two-way YNAB sync is on ...")
    fetched_at = YnabConfigStore().get()["last_synced_at"]

    sync()

    cfg = YnabConfigStore().get()
    assert (cfg["last_status"], cfg.get("last_error")) == ("ok", None)
    assert cfg["last_synced_at"] == fetched_at  # no fetch ran: "Last fetched" doesn't move


def test_a_successful_sync_leaves_a_credentials_failure_for_the_user(world):  # noqa: F811
    enable()
    YnabConfigStore().record_status("auth_error", error="HTTP 401")

    sync()

    assert _status() == ("auth_error", "HTTP 401")
