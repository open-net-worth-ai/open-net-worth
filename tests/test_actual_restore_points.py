"""Actual's budget as it was before a sync wrote to it. Same harness as ``test_actual_sync_engine``;
nothing real."""

from __future__ import annotations

import zipfile

import pytest

from backend.core.errors import NotFoundError
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import restore_points
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import edit_here, enable, row, sync

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


@pytest.fixture(autouse=True)
def _own_folder(tmp_path, monkeypatch):
    """The session's backups folder is shared between tests; each test gets its own."""
    folder = tmp_path / "restore-points"
    folder.mkdir()
    monkeypatch.setattr(restore_points, "_folder", lambda: folder)


def _points() -> list[dict]:
    return actual_app.sync_status()["restore_points"]


def test_a_run_that_writes_keeps_actuals_budget_from_before_it(world):
    enable()
    sync()
    sync()
    before = len(_points())
    edit_here(row("ACME Corp")["id"], memo="here")
    assert sync()["summary"].startswith("1 sent")
    assert len(_points()) == before + 1
    point = _points()[0]
    path = restore_points.path_of(point["id"])
    with zipfile.ZipFile(path) as archive:
        assert "db.sqlite" in archive.namelist()
    assert point["bytes"] > 0 and point["created_at"].endswith("Z")
    assert point["budget"] == "acme-budget"
    assert sync()["summary"] == "Nothing changed"
    assert len(_points()) == before + 1, "a quiet run adds none"


def test_a_from_actual_run_keeps_none(world):
    enable("from_ynab")
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    sync()
    assert _points() == []


def test_only_the_newest_five_are_kept(world, monkeypatch):
    enable()
    sync()
    stamps = iter(f"20261121T0000{i:02d}Z" for i in range(10))

    class Clock:
        @staticmethod
        def now(tz=None):
            from datetime import datetime

            return datetime.strptime(next(stamps), "%Y%m%dT%H%M%SZ")

    monkeypatch.setattr(
        restore_points,
        "datetime",
        type("D", (), {"now": Clock.now, "strptime": __import__("datetime").datetime.strptime}),
    )
    for i in range(7):
        edit_here(row("ACME Corp")["id"], memo=f"edit {i}")
        sync()
    assert len(_points()) == restore_points.KEEP


@pytest.mark.parametrize("bad", ["../../etc/passwd", "", "20261121T000000Z-x-abc123/../y", "nope"])
def test_an_id_never_reaches_outside_the_folder(world, bad):
    with pytest.raises(NotFoundError):
        restore_points.path_of(bad)
