"""Exporting memory as Markdown: one file per section and topic, unsorted facts in one file,
retired and expired facts left out. Synthetic facts only."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.db import MemoryStore
from backend.services import memory_export


def _fact(fid, content, section="money", topic="Housing", **extra):
    return {
        "id": fid,
        "content": content,
        "source": "explicit",
        "state": "live",
        "section": section,
        "topic": topic,
        "expires_at": None,
        "created_at": "2026-09-14T08:00:00+00:00",
        "updated_at": "2026-09-14T08:00:00+00:00",
        **extra,
    }


def _files(blob: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.testzip() is None
        return {n: z.read(n).decode() for n in z.namelist()}


def test_export_zip_has_one_file_per_topic_and_no_retired_facts(db_isolated):
    MemoryStore().bulk_insert(
        [
            _fact("1", "Rents a flat"),
            _fact("2", "Rent is due on the 1st", topic=" housing"),
            _fact("3", "Holds ETFs", topic="Investing", created_at="2026-02-03T00:00:00+00:00"),
            _fact("4", "Prefers short answers", section="you", topic="Preferences"),
            _fact(
                "5",
                "Send the tax letter",
                section="open_items",
                topic=None,
                expires_at="2099-01-01",
            ),
            _fact("6", "Old landlord", state="retired", retired_reason="stale"),
            _fact("7", "Gone long ago", section="open_items", topic=None, expires_at="2000-01-01"),
            _fact("8", "Legacy fact", section=None, topic=None, state="unsorted"),
        ]
    )

    files = _files(memory_export.build_memory_export())

    assert sorted(files) == [
        "money-housing.md",
        "money-investing.md",
        "not-sorted-yet.md",
        "open-items.md",
        "you-preferences.md",
    ]
    assert files["money-housing.md"].splitlines() == [
        "# Money · Housing",
        "",
        "- Rents a flat (Sep 2026)",
        "- Rent is due on the 1st (Sep 2026)",
    ]
    assert "- Holds ETFs (Feb 2026)" in files["money-investing.md"]
    assert "- Send the tax letter (Sep 2026)" in files["open-items.md"]
    assert files["open-items.md"].startswith("# Open items")
    assert "- Legacy fact (Sep 2026)" in files["not-sorted-yet.md"]
    assert not any("Old landlord" in text or "Gone long ago" in text for text in files.values())


def test_export_always_carries_open_items_and_skips_empty_groups(db_isolated):
    files = _files(memory_export.build_memory_export())

    assert list(files) == ["open-items.md"]


def test_export_filenames_are_safe_and_distinct(db_isolated):
    MemoryStore().bulk_insert(
        [
            _fact("1", "a", topic="../../etc/passwd"),
            _fact("2", "b", topic="Cards & payments"),
            _fact("3", "c", topic="Cards / payments"),
            _fact("4", "d", topic="日本"),
            _fact("5", "e", section="other", topic=None),
        ]
    )

    names = list(_files(memory_export.build_memory_export()))

    assert len(names) == len(set(names)) == 6
    for name in names:
        assert "/" not in name and "\\" not in name and ".." not in name
        assert name.endswith(".md")


@pytest.fixture
def client(monkeypatch, db_isolated):
    import backend.core.config as cfg
    from backend.main import app

    monkeypatch.setenv("AUTH_DISABLED", "true")
    cfg._settings = None
    yield TestClient(app, raise_server_exceptions=True)
    cfg._settings = None


def test_export_route_streams_a_dated_zip(client, monkeypatch):
    import backend.api.memory as api

    monkeypatch.setattr(api, "user_today", lambda: date(2026, 10, 27))
    MemoryStore().insert(_fact("1", "Rents a flat"))

    r = client.get("/api/memory/export")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="memory-2026-10-27.zip"' in r.headers["content-disposition"]
    assert "money-housing.md" in _files(r.content)
