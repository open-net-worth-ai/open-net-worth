"""A starter shipped after an install was seeded reaches it exactly once.

Boot seeds starters only into an empty library; ``install_added_starters`` covers a starter
added later (``daily-briefing``), and the offer record keeps a deleted copy deleted.
"""

from __future__ import annotations

import pytest

from backend.db import skill_store
from backend.db.skill_store import STARTER_SKILLS, SkillStore


class _Offers:
    def __init__(self, ids: set[str] | None = None):
        self.ids = set(ids or ())

    def offered(self) -> set[str]:
        return set(self.ids)

    def record(self, ids) -> None:
        self.ids = set(ids)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_store, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    (tmp_path / "skills").mkdir()
    return tmp_path


ADDED = [
    "daily-briefing",
    "log-transactions-from-email",
    "balance-mismatch-tracer",
    "email-finance-digest",
]


def test_the_added_starters_ship():
    assert set(ADDED) <= {s["id"] for s in STARTER_SKILLS}
    assert list(skill_store._ADDED_STARTERS) == ADDED


def test_an_existing_install_gets_a_new_starter_once(isolated):
    s, offers = SkillStore(), _Offers()
    assert s.install_added_starters(offers) == ADDED
    installed = s.find_by_id("daily-briefing")
    assert installed is not None and installed["author"] == "builtin"
    assert s.install_added_starters(offers) == []


def test_a_deleted_new_starter_stays_deleted(isolated):
    s, offers = SkillStore(), _Offers()
    s.install_added_starters(offers)
    assert s.delete("daily-briefing")
    assert s.install_added_starters(offers) == []
    assert s.find_by_id("daily-briefing") is None


def test_a_fresh_install_seeded_with_it_is_only_recorded(isolated):
    s, offers = SkillStore(), _Offers()
    s.seed_starters()
    assert s.install_added_starters(offers) == []
    assert set(ADDED) <= offers.ids


def test_the_first_run_never_brings_back_an_older_starter_the_owner_deleted(isolated):
    s, offers = SkillStore(), _Offers()
    s.install_added_starters(offers)
    assert s.find_by_id("catch-up") is None
    assert "catch-up" in offers.ids
