"""The ``ynab_import_and_sync`` flag. Off is main's YNAB connector exactly: no plan import, no
two-way sync, and the connector fetches the plan as it always did. On → off → on is safe: the
sync's state is kept, only never acted on while the flag is off."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ynab_api import router
from backend.core import feature_flags
from backend.core.errors import NotFoundError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import ynab_migration, ynab_sync
from backend.services.aggregation import ynab as ynab_service
from tests import ynab_stub
from tests.flags import flags
from tests.test_ynab_sync_service import enable, world  # noqa: F401

# The world is a migrated plan, which needs the flag on; each test turns it off where it says.
pytestmark = pytest.mark.flags(ynab_import_and_sync=True)

GATED = [
    ("GET", "/api/ynab/migration", None),
    ("POST", "/api/ynab/migration/preview", {"plan_id": ynab_stub.PLAN}),
    ("POST", "/api/ynab/migration/start", {"plan_id": ynab_stub.PLAN}),
    ("POST", "/api/ynab/migration/verify", {"plan_id": ynab_stub.PLAN}),
    ("GET", "/api/ynab/sync", None),
    ("PUT", "/api/ynab/sync/settings", {"enabled": True}),
    ("POST", "/api/ynab/sync/run", None),
    ("POST", "/api/ynab/sync/conflicts/any/resolve", {"keep": "onw"}),
]


def test_the_flag_ships_off():
    flag = feature_flags.get("ynab_import_and_sync")
    assert flag.default is False and flag.kind == "release"


def test_off_the_import_and_sync_routes_are_not_found_and_the_connector_stays(world):  # noqa: F811
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with flags(ynab_import_and_sync=False):
        for method, path, body in GATED:
            assert client.request(method, path, json=body).status_code == 404, path
        assert client.get("/api/ynab/status").status_code == 200
        with pytest.raises(NotFoundError):
            ynab_migration.start("preview", ynab_stub.PLAN)


def test_off_a_sync_that_was_on_never_runs_and_the_connector_fetches_again(world):  # noqa: F811
    enable()
    assert ynab_sync.plan_in_sync(ynab_stub.PLAN)
    with flags(ynab_import_and_sync=False):
        assert ynab_sync.run_if_due() is False
        assert not ynab_sync.plan_in_sync(ynab_stub.PLAN)
        ynab_service.fetch_window(YnabConfigStore(), "2000-01-01", "2999-01-01", None)
    assert world.writes == []
    assert ynab_sync.plan_in_sync(ynab_stub.PLAN)
    assert ynab_sync.run_if_due() is True  # back on, it carries on from the state it kept
    assert ynab_sync.status()["last_run"]["ok"]
