"""
Unit tests for SkillStore (skill registry — RFC P2-SK1).

Pure, offline, fully isolated: the store path is monkeypatched into a pytest
tmp_path so these never touch live backend/data (per the "no destructive tests on
live data" rule). No LLM, no network.

Run:
    .venv/bin/pytest tests/test_skill_store.py -v
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.core.errors import DataIntegrityError, ValidationError
from backend.db import skill_store
from backend.db.skill_state_store import SkillStateStore
from backend.db.skill_store import STARTER_SKILLS, SkillStore


def _increment_usage_in_process(
    skills_dir: str,
    state_path: str,
    sid: str,
    barrier,
    increments: int,
) -> None:
    """Spawn-safe worker that widens the real store's read/write race window."""
    from backend.db import skill_store as worker_store

    worker_store.SKILLS_DIR = Path(skills_dir)
    worker_store.SKILLS_STATE_JSON = Path(state_path)
    original_load_state = worker_store._load_state

    def delayed_load_state():
        state = original_load_state()
        time.sleep(0.02)
        return state

    worker_store._load_state = delayed_load_state
    store = worker_store.SkillStore()
    for _ in range(increments):
        barrier.wait(timeout=10)
        store.increment_invocation_count(sid)


def _race_state_update_in_process(
    skills_dir: str,
    state_path: str,
    sid: str,
    barrier,
    action: str,
) -> None:
    """Spawn-safe worker that aligns an ordinary state update with an increment."""
    from backend.db import skill_store as worker_store

    worker_store.SKILLS_DIR = Path(skills_dir)
    worker_store.SKILLS_STATE_JSON = Path(state_path)
    original_load_state = worker_store._load_state

    def delayed_load_state():
        state = original_load_state()
        time.sleep(0.05)
        return state

    worker_store._load_state = delayed_load_state
    barrier.wait(timeout=10)
    if action == "increment":
        worker_store.SkillStore().increment_invocation_count(sid)
    else:
        worker_store._update_state(sid, {"enabled": False})


def _race_delete_increment_in_process(
    skills_dir: str,
    state_path: str,
    sid: str,
    action: str,
    increment_admitted,
    delete_done,
    results,
) -> None:
    """Force delete to finish after increment admission but before its mutation."""
    from backend.db import skill_store as worker_store

    worker_store.SKILLS_DIR = Path(skills_dir)
    worker_store.SKILLS_STATE_JSON = Path(state_path)
    store = worker_store.SkillStore()
    if action == "increment":
        original_mutate_state = worker_store._mutate_state

        def gated_mutate_state(mutate):
            increment_admitted.set()
            if not delete_done.wait(timeout=10):
                raise TimeoutError("delete did not finish")
            return original_mutate_state(mutate)

        worker_store._mutate_state = gated_mutate_state
        result = store.increment_invocation_count(sid)
    else:
        if not increment_admitted.wait(timeout=10):
            raise TimeoutError("increment was not admitted")
        try:
            result = store.delete(sid)
        finally:
            delete_done.set()
    results.put((action, result))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_store, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    (tmp_path / "skills").mkdir()
    return tmp_path


# ── create: defaults, slug id, normalization, unknown-key drop ─────────────────
def test_normalize_skill_name_enforces_standard_slug():
    from backend.db.skill_store import normalize_skill_name

    assert normalize_skill_name("Monthly Close") == "monthly-close"
    assert (
        normalize_skill_name("Riverside Bank/PocketPay Import & Dedup")
        == "riverside-bank-pocketpay-import-dedup"
    )
    assert normalize_skill_name("  Trailing -- and  spaces  ") == "trailing-and-spaces"
    assert normalize_skill_name("already-a-slug") == "already-a-slug"
    assert normalize_skill_name("!!!") == ""  # nothing usable
    assert len(normalize_skill_name("x" * 200)) == 64  # capped at MAX_SKILL_NAME_LEN


def test_create_defaults_and_slug_id(isolated):
    s = SkillStore()
    sk = s.create({"name": "Monthly Close", "allowed_tools": ["get_ledger_summary"]})
    assert sk["id"] == "monthly-close"  # slugified from name
    assert sk["name"] == "monthly-close"  # name itself is normalized to the standard slug
    assert sk["author"] == "user"  # default for non-builtin create
    assert sk["enabled"] is True  # default
    assert "allowed_tools" not in sk  # not a skill field — dropped at create
    assert sk["created_at"] and sk["updated_at"]
    assert "trigger" not in sk and "guardrails" not in sk


def test_create_explicit_id_wins(isolated):
    s = SkillStore()
    sk = s.create({"id": "mc", "name": "Whatever", "allowed_tools": ["x"]})
    assert sk["id"] == "mc"


def test_claude_and_openai_extensions_round_trip_through_native_files(isolated):
    store = SkillStore()
    created = store.create(
        {
            "name": "review-pr",
            "description": "Review a pull request.",
            "body": "Review $ARGUMENTS.",
            "claude": {
                "when_to_use": "When the user asks for a PR review.",
                "argument_hint": "[pr-number]",
                "arguments": ["pr"],
                "disable_model_invocation": True,
                "user_invocable": True,
                "disallowed_tools": ["Write", "Edit"],
                "model": "sonnet",
                "effort": "high",
                "context": "fork",
                "agent": "Explore",
                "background": False,
                "paths": ["backend/**"],
                "shell": "bash",
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "check"}]}]},
            },
            "openai": {
                "interface": {
                    "display_name": "Review PR",
                    "short_description": "Review one pull request",
                    "default_prompt": "Review PR 123",
                },
                "policy": {"allow_implicit_invocation": False},
                "dependencies": {"tools": [{"type": "mcp", "value": "github"}]},
            },
        }
    )

    assert created["claude"]["arguments"] == ["pr"]
    assert created["claude"]["context"] == "fork"
    assert "disallowed_tools" not in created["claude"]  # a skill carries no tool list
    assert created["openai"]["interface"]["display_name"] == "Review PR"
    skill_md = (isolated / "skills" / "review-pr" / "SKILL.md").read_text()
    openai_yaml = (isolated / "skills" / "review-pr" / "agents" / "openai.yaml").read_text()
    assert "disable-model-invocation: true" in skill_md
    assert "argument-hint: '[pr-number]'" in skill_md
    assert "disallowed-tools" not in skill_md
    assert "display_name: Review PR" in openai_yaml

    updated = store.update_skill(
        "review-pr",
        {
            "claude": {**created["claude"], "user_invocable": False},
            "openai": {**created["openai"], "policy": {"allow_implicit_invocation": True}},
        },
    )
    assert updated is not None
    assert updated["claude"]["user_invocable"] is False
    assert updated["openai"]["policy"]["allow_implicit_invocation"] is True


@pytest.mark.parametrize(
    ("relative_path", "contents", "expected_problem"),
    [
        (
            "SKILL.md",
            "---\nname: review-pr\ndescription: Review a pull request.\n"
            'disable-model-invocation: "true"\n---\nReview it.\n',
            "disable-model-invocation' must be a boolean",
        ),
        (
            "agents/openai.yaml",
            "policy:\n  allow_implicit_invocation: 'false'\n",
            "policy.allow_implicit_invocation' must be a boolean",
        ),
    ],
)
def test_direct_file_edits_with_malformed_invocation_controls_fail_closed(
    isolated, relative_path, contents, expected_problem
):
    store = SkillStore()
    store.create(
        {
            "name": "review-pr",
            "description": "Review a pull request.",
            "body": "Review it.",
        }
    )
    target = isolated / "skills" / "review-pr" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents)

    skill = store.find_by_id("review-pr")

    assert skill is not None
    assert skill["enabled"] is False
    assert any(expected_problem in problem for problem in skill["validation_errors"])


@pytest.mark.parametrize("sid", ["../escape", "nested/skill", ".", "UPPER", "bad_slug"])
def test_structured_skill_ids_must_be_canonical_slugs(isolated, sid):
    store = SkillStore()

    with pytest.raises(ValidationError, match="canonical slug"):
        store.find_by_id(sid)
    with pytest.raises(ValidationError, match="canonical slug"):
        store.create({"id": sid, "name": "valid-name"})


def test_symlinked_skill_folder_cannot_escape_store_root(isolated):
    outside = isolated / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: escaped\ndescription: escaped\n---\nbody\n")
    (isolated / "skills" / "escaped").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        SkillStore().find_by_id("escaped")

    with pytest.raises(ValidationError, match="symlink"):
        SkillStore().all()


def test_symlinked_skill_folder_alias_inside_store_is_rejected_for_read_write_delete(isolated):
    store = SkillStore()
    victim = store.create({"name": "victim", "body": "keep me"})
    alias = isolated / "skills" / "alias"
    alias.symlink_to(isolated / "skills" / victim["id"], target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        store.find_by_id("alias")
    with pytest.raises(ValidationError, match="symlink"):
        store.update_skill("alias", {"body": "overwrite"})
    with pytest.raises(ValidationError, match="symlink"):
        store.delete("alias")

    assert store.find_by_id(victim["id"])["body"].strip() == "keep me"


def test_symlinked_skill_file_cannot_escape_its_skill_folder(isolated):
    outside = isolated / "outside.md"
    outside.write_text("---\nname: escaped\ndescription: escaped\n---\nbody\n")
    folder = isolated / "skills" / "escaped"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(outside)

    with pytest.raises(ValidationError, match="regular file"):
        SkillStore().find_by_id("escaped")

    with pytest.raises(ValidationError, match="regular file"):
        SkillStore().increment_invocation_count("escaped")


def _seed_raw_state_row(sid: str, payload) -> None:
    """Insert one row directly into the ``skills_state`` table, bypassing
    ``SkillStateStore``'s own validation — the DB-era way to plant a malformed row (a
    hand-edited row, or one left behind by a botched migration). Mirrors
    ``tests/db/test_money_tables.py``'s raw-insert-via-the-engine pattern.

    A DB-backed store has no on-disk-corruption failure mode of its own — a read either
    succeeds or the database itself is broken, which ``db.migrate.ensure_migrated()``'s
    boot gate owns instead (see ``skill_store.py``'s own ``_load_state`` docstring). So
    writing truncated/unparseable BYTES, the pre-migration way to simulate corruption, no
    longer reaches any code path at all — this is how "bad data got into the row some
    other way" (a hand edit, an incomplete migration) is simulated now."""
    from sqlalchemy import insert as sa_insert

    from backend.db import engine as eng
    from backend.db.tables import skills_state

    with eng.get_engine().begin() as conn:
        conn.execute(sa_insert(skills_state).values(seq=0, id=sid, payload=payload))


def test_malformed_state_row_fails_closed_across_every_read_entry_point(isolated):
    """A malformed skills_state row must fail EVERY read entry point closed — never
    silently drop/skip the bad row or default-enable/create a skill around it. Formerly
    this drove the same "fails closed everywhere" property by writing truncated BYTES to
    skills_state.json (quarantining a copy and refusing every entry point); a DB-backed
    table has no byte-corruption failure mode any more (see _seed_raw_state_row), so a
    malformed ROW — one missing every required field — is the DB-era way to reach the
    same fail-closed contract. The quarantine-FILE side effect is gone by design; the
    fail-closed-on-bad-data contract itself is unchanged and still pinned here."""
    _seed_raw_state_row("demo", {})  # missing every required field
    store = SkillStore()

    for action in (
        store.all,
        lambda: store.find_by_id("demo"),
        lambda: store.create({"name": "new-skill"}),
        lambda: store.increment_invocation_count("demo"),
    ):
        with pytest.raises(DataIntegrityError, match="skills_state"):
            action()

    assert not (isolated / "skills" / "new-skill").exists()


def test_structurally_invalid_state_row_fails_closed(isolated):
    """A state row that isn't even an object (e.g. a hand-edited or half-migrated value)
    must fail closed on read rather than crash trying to treat a string as a dict."""
    _seed_raw_state_row("demo", "not-an-object")

    with pytest.raises(DataIntegrityError, match="skills_state"):
        SkillStore().all()


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"enabled": "false"},
        {
            "enabled": True,
            "created_at": "2026-08-09",
            "updated_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "user",
            "updated_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "attacker",
            "created_at": "2026-08-09",
            "updated_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": "2026-08-09",
            "updated_at": "2026-08-09",
            "invocation_count": True,
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": "2026-08-09",
            "updated_at": "2026-08-09",
            "invocation_count": -1,
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": 20260809,
            "updated_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": None,
            "updated_at": "2026-08-09",
        },
        {
            "enabled": True,
            "author": "user",
            "created_at": "2026-08-09",
            "updated_at": "not-a-date",
        },
    ],
)
def test_invalid_skill_state_fields_fail_closed(isolated, row):
    _seed_raw_state_row("demo", row)

    for _attempt in range(2):  # must fail EVERY read, not just the first (no self-healing)
        with pytest.raises(DataIntegrityError, match="skills_state"):
            SkillStore().all()


def test_invalid_skill_state_id_fails_closed(isolated):
    """A skill id key that isn't a canonical slug (e.g. a path-escape attempt) must fail
    closed on read, never get silently skipped or treated as a valid skill id."""
    _seed_raw_state_row("../victim", {"enabled": False})

    with pytest.raises(DataIntegrityError, match="skills_state"):
        SkillStore().invocation_counts()


def test_missing_state_row_is_disabled_and_usage_writer_persists_complete_row(isolated):
    store = SkillStore()
    skill = store.create({"name": "legacy-state-gap", "body": "safe body"})
    SkillStateStore().replace_all_keyed({})  # simulate the state row going missing

    assert store.find_by_id(skill["id"])["enabled"] is False
    assert store.increment_invocation_count(skill["id"]) == 1

    row = SkillStateStore().all_as_dict()[skill["id"]]
    assert row == {
        "enabled": False,
        "author": "user",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "invocation_count": 1,
    }


def test_create_dedupes_id(isolated):
    s = SkillStore()
    a = s.create({"name": "Audit", "allowed_tools": ["x"]})
    b = s.create({"name": "Audit", "allowed_tools": ["x"]})
    assert a["id"] == "audit"
    assert b["id"] == "audit-2"
    assert len(s.all()) == 2


def test_create_content_failure_leaves_durable_disabled_tombstone(isolated, monkeypatch):
    store = SkillStore()

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(skill_store, "_write_skill_folder", fail_write)

    with pytest.raises(OSError, match="disk full"):
        store.create({"name": "half-created", "enabled": True})

    state = SkillStateStore().all_as_dict()
    assert state["half-created"]["enabled"] is False
    assert store.find_by_id("half-created") is None


def test_create_rejects_folder_appearing_after_disabled_state_save(isolated, monkeypatch):
    store = SkillStore()
    lexical = isolated / "skills" / "create-race"
    original_save = skill_store._save_state
    injected = False

    def save_then_create_attacker_folder(state):
        nonlocal injected
        original_save(state)
        if not injected and state.get("create-race", {}).get("enabled") is False:
            lexical.mkdir()
            (lexical / "SKILL.md").write_text("ATTACKER CONTENT", encoding="utf-8")
            injected = True

    monkeypatch.setattr(skill_store, "_save_state", save_then_create_attacker_folder)

    with pytest.raises(ValidationError, match="appeared during create"):
        store.create({"name": "create-race", "body": "replacement", "enabled": True})

    assert (lexical / "SKILL.md").read_text(encoding="utf-8") == "ATTACKER CONTENT"
    assert SkillStateStore().all_as_dict()["create-race"]["enabled"] is False


def test_create_hides_partial_content_and_keeps_disabled_state_until_commit(isolated, monkeypatch):
    store = SkillStore()
    write_started = threading.Event()
    release_write = threading.Event()
    read_done = threading.Event()
    original_write = skill_store._write_skill_folder

    def gated_write(folder, record, **kwargs):
        write_started.set()
        assert release_write.wait(timeout=5)
        original_write(folder, record, **kwargs)

    monkeypatch.setattr(skill_store, "_write_skill_folder", gated_write)
    created: list[dict] = []
    creator = threading.Thread(
        target=lambda: created.append(store.create({"name": "atomic-create", "enabled": True}))
    )
    creator.start()
    assert write_started.wait(timeout=5)
    state = SkillStateStore().all_as_dict()
    assert state["atomic-create"]["enabled"] is False

    reader = threading.Thread(
        target=lambda: (SkillStore().find_by_id("atomic-create"), read_done.set())
    )
    reader.start()
    assert not read_done.wait(timeout=0.2)
    release_write.set()
    creator.join(timeout=5)
    reader.join(timeout=5)

    assert created[0]["enabled"] is True
    assert read_done.is_set()


def test_create_final_state_failure_leaves_written_content_non_invocable(isolated, monkeypatch):
    store = SkillStore()
    original_save = skill_store._save_state
    calls = 0

    def fail_final_save(state):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("final state failed")
        original_save(state)

    monkeypatch.setattr(skill_store, "_save_state", fail_final_save)

    with pytest.raises(OSError, match="final state failed"):
        store.create({"name": "final-create-failure", "enabled": True})

    state = SkillStateStore().all_as_dict()
    assert state["final-create-failure"]["enabled"] is False
    assert store.find_by_id("final-create-failure")["enabled"] is False


def test_create_drops_unknown_top_level_keys(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["x"], "evil": "rm -rf", "exec": "os.system"})
    assert "evil" not in sk and "exec" not in sk


def test_create_drops_nonstandard_trigger_and_bad_author(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["x"], "trigger": "cron", "author": "hacker"})
    assert "trigger" not in sk
    assert sk["author"] == "user"


def test_create_drops_nonstandard_parameters(isolated):
    s = SkillStore()
    sk = s.create(
        {
            "name": "X",
            "allowed_tools": ["x"],
            "parameters": [
                {"name": "month", "required": True, "junk": 1},
                "not-a-dict",  # ignored
            ],
        }
    )
    assert "parameters" not in sk


def test_create_drops_nonstandard_guardrails(isolated):
    s = SkillStore()
    sk = s.create(
        {
            "name": "X",
            "allowed_tools": ["x"],
            "guardrails": {"tier_overrides": {"x": "approve"}, "junk": True},
        }
    )
    assert "guardrails" not in sk


# ── update_skill: whitelist, normalize, immutability, timestamp ────────────────
def test_update_skill_patches_editable_and_stamps(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["a"]})
    original_created = sk["created_at"]
    updated = s.update_skill(
        sk["id"],
        {
            "name": "X2",
            "allowed_tools": ["a", "b"],  # not a skill field → dropped
            "id": "hacked",  # immutable → dropped
            "author": "hacker",  # immutable → dropped
            "created_at": "hacked",  # immutable → dropped
        },
    )
    assert updated["name"] == "x2"  # normalized to the standard lowercase-hyphenated slug
    assert "allowed_tools" not in updated
    assert updated["id"] == sk["id"]  # immutable
    assert updated["author"] == "user"  # immutable
    assert updated["created_at"] == original_created
    assert "updated_at" in updated


def test_update_skill_drops_nonstandard_trigger(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["a"]})
    updated = s.update_skill(sk["id"], {"trigger": "cron"})
    assert "trigger" not in updated


def test_update_skill_missing_returns_none(isolated):
    s = SkillStore()
    assert s.update_skill("ghost", {"name": "x"}) is None


def test_update_content_failure_leaves_existing_skill_disabled(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "update-failure", "body": "old", "enabled": True})

    monkeypatch.setattr(
        skill_store,
        "_write_skill_folder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(OSError, match="write failed"):
        store.update_skill(skill["id"], {"body": "new"})

    assert store.find_by_id(skill["id"])["enabled"] is False


def test_update_folder_swap_after_disable_never_overwrites_swapped_victim(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "swap-update", "body": "original", "enabled": True})
    lexical = isolated / "skills" / skill["id"]
    moved_original = isolated / "moved-original"
    victim = isolated / "victim"
    victim.mkdir()
    victim_skill_md = victim / "SKILL.md"
    victim_skill_md.write_text("---\nname: victim\ndescription: victim\n---\nDO NOT OVERWRITE\n")
    original_save = skill_store._save_state
    swapped = False

    def save_then_swap(state):
        nonlocal swapped
        original_save(state)
        if not swapped and state[skill["id"]]["enabled"] is False:
            lexical.rename(moved_original)
            victim.rename(lexical)
            swapped = True

    monkeypatch.setattr(skill_store, "_save_state", save_then_swap)

    with pytest.raises(ValidationError, match="identity changed"):
        store.update_skill(skill["id"], {"body": "new body"})

    assert "DO NOT OVERWRITE" in (lexical / "SKILL.md").read_text()
    assert "original" in (moved_original / "SKILL.md").read_text()
    assert SkillStateStore().all_as_dict()[skill["id"]]["enabled"] is False


def test_delete_waits_for_inflight_update_and_cannot_be_resurrected(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "update-delete-race", "body": "old"})
    write_started = threading.Event()
    release_write = threading.Event()
    delete_done = threading.Event()
    original_write = skill_store._write_skill_folder

    def gated_write(folder, record, **kwargs):
        if record.get("body") == "new":
            write_started.set()
            assert release_write.wait(timeout=5)
        original_write(folder, record, **kwargs)

    monkeypatch.setattr(skill_store, "_write_skill_folder", gated_write)
    updater = threading.Thread(target=lambda: store.update_skill(skill["id"], {"body": "new"}))
    updater.start()
    assert write_started.wait(timeout=5)
    deleter = threading.Thread(target=lambda: (store.delete(skill["id"]), delete_done.set()))
    deleter.start()
    assert not delete_done.wait(timeout=0.2)

    release_write.set()
    updater.join(timeout=5)
    deleter.join(timeout=5)

    assert delete_done.is_set()
    assert store.find_by_id(skill["id"]) is None
    assert skill["id"] not in SkillStateStore().all_as_dict()


def test_delete_folder_swap_after_disable_never_removes_swapped_victim(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "swap-delete", "body": "original", "enabled": True})
    lexical = isolated / "skills" / skill["id"]
    moved_original = isolated / "moved-delete-original"
    victim = isolated / "delete-victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("---\nname: victim\ndescription: victim\n---\nKEEP\n")
    original_save = skill_store._save_state
    swapped = False

    def save_then_swap(state):
        nonlocal swapped
        original_save(state)
        if not swapped and state[skill["id"]]["enabled"] is False:
            lexical.rename(moved_original)
            victim.rename(lexical)
            swapped = True

    monkeypatch.setattr(skill_store, "_save_state", save_then_swap)

    with pytest.raises(ValidationError, match="identity changed"):
        store.delete(skill["id"])

    assert "KEEP" in (lexical / "SKILL.md").read_text()
    assert SkillStateStore().all_as_dict()[skill["id"]]["enabled"] is False


def test_delete_restores_wrong_target_swapped_between_verify_and_rename(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "rename-race", "body": "original", "enabled": True})
    lexical = isolated / "skills" / skill["id"]
    moved_original = isolated / "moved-rename-original"
    victim = isolated / "rename-victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("---\nname: victim\ndescription: victim\n---\nKEEP\n")
    original_rename = skill_store.os.rename
    swapped = False

    def swap_during_rename(src, dst, *args, **kwargs):
        nonlocal swapped
        # POSIX passes bare names (dir_fd); Windows passes full paths.
        if (
            not swapped
            and os.path.basename(str(src)) == skill["id"]
            and os.path.basename(str(dst)).startswith(".deleting-")
        ):
            lexical.rename(moved_original)
            victim.rename(lexical)
            swapped = True
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(skill_store.os, "rename", swap_during_rename)

    with pytest.raises(ValidationError, match="identity changed"):
        store.delete(skill["id"])

    assert "KEEP" in (lexical / "SKILL.md").read_text()
    assert SkillStateStore().all_as_dict()[skill["id"]]["enabled"] is False


# ── queries ────────────────────────────────────────────────────────────────────
def test_query_skills_filters(isolated):
    s = SkillStore()
    s.create({"name": "A", "allowed_tools": ["x"]})
    s.create({"name": "B", "allowed_tools": ["x"], "enabled": False})
    assert len(s.query_skills(enabled=True)) == 1
    assert len(s.query_skills(enabled=False)) == 1


def test_invocation_counts_survive_a_fresh_store_instance(isolated):
    first = SkillStore()
    skill = first.create({"name": "Persistent Usage", "allowed_tools": []})

    assert first.increment_invocation_count(skill["id"]) == 1
    assert first.increment_invocation_count(skill["id"]) == 2

    fresh = SkillStore()
    assert fresh.invocation_counts() == {skill["id"]: 2}


def test_increment_invocation_count_is_atomic(isolated):
    store = SkillStore()
    skill = store.create({"name": "Concurrent Usage", "allowed_tools": []})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _index: SkillStore().increment_invocation_count(skill["id"]),
                range(40),
            )
        )

    assert SkillStore().invocation_counts() == {skill["id"]: 40}


def test_increment_invocation_count_is_atomic_across_processes(isolated):
    store = SkillStore()
    skill = store.create({"name": "Multiprocess Usage", "allowed_tools": []})
    process_count = 4
    increments = 3
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(process_count)
    processes = [
        context.Process(
            target=_increment_usage_in_process,
            args=(
                str(skill_store.SKILLS_DIR),
                str(skill_store.SKILLS_STATE_JSON),
                skill["id"],
                barrier,
                increments,
            ),
        )
        for _ in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0] * process_count
    assert SkillStore().invocation_counts() == {
        skill["id"]: process_count * increments,
    }


def test_state_update_and_usage_increment_do_not_overwrite_each_other(isolated):
    store = SkillStore()
    skill = store.create({"name": "Mixed State Writes", "allowed_tools": []})
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_race_state_update_in_process,
            args=(
                str(skill_store.SKILLS_DIR),
                str(skill_store.SKILLS_STATE_JSON),
                skill["id"],
                barrier,
                action,
            ),
        )
        for action in ("increment", "update")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0, 0]
    assert SkillStore().find_by_id(skill["id"])["enabled"] is False
    assert SkillStore().invocation_counts() == {skill["id"]: 1}


def test_delete_wins_after_increment_admission_without_orphan_state(isolated):
    store = SkillStore()
    skill = store.create({"name": "Delete Race", "allowed_tools": []})
    context = multiprocessing.get_context("spawn")
    increment_admitted = context.Event()
    delete_done = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_delete_increment_in_process,
            args=(
                str(skill_store.SKILLS_DIR),
                str(skill_store.SKILLS_STATE_JSON),
                skill["id"],
                action,
                increment_admitted,
                delete_done,
                results,
            ),
        )
        for action in ("increment", "delete")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    outcomes = dict(results.get(timeout=2) for _ in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    assert outcomes == {"increment": 0, "delete": True}
    assert store.find_by_id(skill["id"]) is None
    assert store.invocation_counts() == {}


def test_increment_unknown_skill_does_not_write_state(isolated):
    assert SkillStore().increment_invocation_count("missing") == 0
    assert not skill_store.SKILLS_STATE_JSON.exists()


# ── starter seeding (idempotent) ───────────────────────────────────────────────
def test_seed_starters_inserts_all_and_is_idempotent(isolated):
    s = SkillStore()
    n = s.seed_starters()
    assert n == len(STARTER_SKILLS)
    ids = {sk["id"] for sk in s.all()}
    assert {
        "skill-creator",
        "onboarding-new-user",
        "catch-up",
        "budget-review",
        "weekly-report",
        "monthly-report",
        "subscription-audit",
        "can-i-afford-it",
        "investment-checkup",
        "on-track",
    } <= ids
    # All starters are author=builtin and enabled.
    assert all(sk["author"] == "builtin" for sk in s.all())
    # Running again inserts nothing (idempotent).
    assert s.seed_starters() == 0
    assert len(s.all()) == len(STARTER_SKILLS)


# ── superseded starter bodies (refreshed only when the owner never edited them) ─
# The budget-review body installs carried until 2026-09-26: it read a profile field that doesn't
# exist and wrote through a retired tool, and a live chat followed it into nonsense.
_OLD_BUDGET_REVIEW = (
    "Review the user's budgets against reality.\n"
    "1. Call get_profile to read profile.monthly_budgets (the current per-category caps).\n"
    "2. Call get_spending_summary / get_ledger_summary for the last 3 months to get actual "
    "spend per category, in the base currency the tools report (do not hardcode a currency).\n"
    "3. For each category compare the 3-month average actual vs its cap. Highlight chronic "
    "overruns and unused headroom.\n"
    "4. Propose a revised set of caps via set_spending_limits — this is an approve-tier "
    "change, so present it and let the user confirm before it is written. Do not auto-apply."
)


def _install_old_budget_review(store: SkillStore, body: str = _OLD_BUDGET_REVIEW) -> None:
    store.create(
        {
            "id": "budget-review",
            "name": "budget-review",
            "description": "Compare recent spending per category against the user's monthly "
            "budgets and propose adjusted caps.",
            "author": "builtin",
            "body": body,
            "allowed_tools": ["get_profile", "set_spending_limits"],
        }
    )


def test_an_unedited_superseded_starter_is_refreshed_once(isolated):
    s = SkillStore()
    _install_old_budget_review(s)
    assert s.refresh_superseded_starters() == ["budget-review"]
    sk = s.find_by_id("budget-review")
    current = next(x for x in STARTER_SKILLS if x["id"] == "budget-review")
    assert sk["body"].strip() == current["body"].strip()
    assert sk["description"] == current["description"]
    # Old starters listed tools, which a model took as its whole toolset.
    assert not sk.get("allowed_tools")
    skill_md = (skill_store.SKILLS_DIR / "budget-review" / "SKILL.md").read_text()
    assert "allowed-tools" not in skill_md
    assert "copy_targets_to_caps" in sk["body"] and "set_spending_limits" not in sk["body"]
    assert s.refresh_superseded_starters() == []  # idempotent


def test_an_edited_starter_is_never_overwritten(isolated):
    s = SkillStore()
    _install_old_budget_review(s, body=_OLD_BUDGET_REVIEW + "\n5. Always mention my rent.")
    assert s.refresh_superseded_starters() == []
    assert s.find_by_id("budget-review")["body"].rstrip().endswith("Always mention my rent.")


def test_superseded_hashes_are_old_bodies_never_the_current_one():
    import hashlib

    digest = lambda b: hashlib.sha256(b.strip().encode()).hexdigest()  # noqa: E731
    assert digest(_OLD_BUDGET_REVIEW) in skill_store._SUPERSEDED_STARTER_BODIES["budget-review"]
    current = {x["id"]: digest(x["body"]) for x in STARTER_SKILLS}
    for sid, stale in skill_store._SUPERSEDED_STARTER_BODIES.items():
        assert sid in current, sid
        assert current[sid] not in stale, f"{sid}: the current body is marked superseded"


# ── skill-creator meta starter ─────────────────────────────────────────────────
def test_skill_creator_seeded_and_builtin(isolated):
    s = SkillStore()
    s.seed_starters()
    sk = s.find_by_id("skill-creator")
    assert sk is not None
    assert sk["author"] == "builtin"
    assert sk["enabled"] is True
    # Skills carry instructions, not a private tool offer or permission list.
    assert "allowed_tools" not in sk
    assert "ordinary tool discovery" in sk["body"]
    assert "parameters" not in sk


# ── email-transaction-logger stamps the email external_refs pair (harness audit 2026-07-19) ─
# ── retired starters (dropped from the shipped set) ───────────────────────────
def test_retired_starters_no_longer_seed(isolated):
    s = SkillStore()
    s.seed_starters()
    ids = {sk["id"] for sk in s.all()}
    assert not ids & skill_store.retired_starter_ids()


def _install_retired(store: SkillStore, sid: str, body: str) -> None:
    store.create({"id": sid, "name": sid, "description": "old", "author": "builtin", "body": body})


_OLD_FEE_ANALYZER = (
    "Analyze investment fees.\n"
    "1. Call get_fees_summary to retrieve the total acquisition and disposal fees paid, "
    "denominated in the base currency the tool reports (never invent a currency symbol).\n"
    "2. Call get_portfolio to get current holding values.\n"
    "3. For each holding, compute the fee-to-value ratio where data allows, and rank holdings "
    "from highest to lowest fee drag.\n"
    "4. Highlight any holding whose fees look disproportionate relative to its current value or "
    "position size, and explain why it stands out.\n"
    "5. Summarize the total fees paid to date and note any patterns (e.g. frequent small trades "
    "accumulating drag). Take NO write action."
)


def test_an_unedited_retired_starter_is_deleted_once(isolated):
    s = SkillStore()
    _install_retired(s, "fee-analyzer", _OLD_FEE_ANALYZER)
    assert s.retire_dropped_starters() == ["fee-analyzer"]
    assert s.find_by_id("fee-analyzer") is None
    assert s.retire_dropped_starters() == []  # idempotent


def test_a_retired_starter_hands_over_to_its_replacement(isolated):
    s = SkillStore()
    _install_retired(s, "fee-analyzer", _OLD_FEE_ANALYZER)
    s.create(
        {"id": "runway", "name": "runway", "description": "old", "author": "builtin", "body": "x"}
    )
    original = skill_store._RETIRED_STARTER_BODIES
    skill_store._RETIRED_STARTER_BODIES = {
        **original,
        "runway": original["runway"] | {hashlib.sha256(b"x").hexdigest()},
    }
    try:
        assert sorted(s.retire_dropped_starters()) == ["fee-analyzer", "runway"]
    finally:
        skill_store._RETIRED_STARTER_BODIES = original
    successor = s.find_by_id("can-i-afford-it")
    assert successor is not None and successor["author"] == "builtin"
    assert successor["enabled"] is True
    # fee-analyzer's job lives on in investment-checkup, which this store never had — only an
    # explicit replacement is installed, never a guess.
    assert s.find_by_id("investment-checkup") is None


def test_an_edited_retired_starter_is_kept(isolated):
    s = SkillStore()
    _install_retired(s, "fee-analyzer", _OLD_FEE_ANALYZER + "\n6. Mention my broker.")
    assert s.retire_dropped_starters() == []
    assert s.find_by_id("fee-analyzer") is not None


def test_a_retired_starter_an_automation_names_is_kept(isolated):
    s = SkillStore()
    _install_retired(s, "fee-analyzer", _OLD_FEE_ANALYZER)
    assert s.retire_dropped_starters(keep=frozenset({"fee-analyzer"})) == []
    assert s.find_by_id("fee-analyzer") is not None


def test_retired_hashes_name_only_dropped_starters():
    shipped = {x["id"] for x in STARTER_SKILLS}
    assert not skill_store.retired_starter_ids() & shipped
    import hashlib

    assert (
        hashlib.sha256(_OLD_FEE_ANALYZER.encode()).hexdigest()
        in skill_store._RETIRED_STARTER_BODIES["fee-analyzer"]
    )


# ── house authoring guide (skill-creator/references/authoring-guide.md §2, §7) ──
# The description carries the trigger and names the near-misses; the body leads with the
# Gotchas — at most one framing paragraph before them — and stays inside the spec's size
# limit. Every starter except
# skill-creator (it IS the guide) is held to it.
_HOUSE_STANDARD = [
    "balance-mismatch-tracer",
    "book-transactions",
    "budget-review",
    "can-i-afford-it",
    "catch-up",
    "daily-briefing",
    "email-finance-digest",
    "investment-checkup",
    "log-transactions-from-email",
    "monthly-report",
    "on-track",
    "onboarding-new-user",
    "subscription-audit",
    "weekly-report",
]


@pytest.mark.parametrize("sid", _HOUSE_STANDARD)
def test_starter_follows_the_house_authoring_guide(sid):
    starter = next(x for x in STARTER_SKILLS if x["id"] == sid)
    description = starter["description"]
    assert description.startswith("Use this skill") and len(description) <= 1024
    assert "Not for" in description  # the near-misses are named
    body = starter["body"]
    assert "## Gotchas" in body
    assert body.lstrip().split("## Gotchas")[0].count("\n\n") <= 1  # Gotchas lead
    # The Agent Skills limit (500 lines / ~5,000 tokens). The guide's ~100-line target is a
    # writing aim, not a gate: the reports' page rules run longer by design (ea1a792f1).
    assert len(body.splitlines()) <= 500 and len(body) <= 20_000


def test_every_starter_but_the_guide_is_held_to_the_house_standard():
    # A new starter joins _HOUSE_STANDARD, or this names it.
    assert {x["id"] for x in STARTER_SKILLS} - {"skill-creator"} == set(_HOUSE_STANDARD)


# ── superseded descriptions (body unchanged) ──────────────────────────────────
def test_an_unedited_superseded_description_is_refreshed(isolated):
    import hashlib

    s = SkillStore()
    current = next(x for x in STARTER_SKILLS if x["id"] == "weekly-report")
    s.create({**current, "author": "builtin"})
    old = "Weekly money report — delivered to Telegram."
    s.update_skill("weekly-report", {"description": old})
    digest = hashlib.sha256(old.encode()).hexdigest()
    stale = skill_store._SUPERSEDED_STARTER_DESCRIPTIONS["weekly-report"] | {digest}
    original = skill_store._SUPERSEDED_STARTER_DESCRIPTIONS
    skill_store._SUPERSEDED_STARTER_DESCRIPTIONS = {**original, "weekly-report": stale}
    try:
        assert s.refresh_superseded_starters() == ["weekly-report"]
        assert s.find_by_id("weekly-report")["description"] == current["description"]
        s.update_skill("weekly-report", {"description": "Mine, edited."})
        assert s.refresh_superseded_starters() == []
        assert s.find_by_id("weekly-report")["description"] == "Mine, edited."
    finally:
        skill_store._SUPERSEDED_STARTER_DESCRIPTIONS = original


def test_superseded_descriptions_are_never_the_current_one():
    import hashlib

    current = {
        x["id"]: hashlib.sha256(x["description"].strip().encode()).hexdigest()
        for x in STARTER_SKILLS
    }
    for sid, stale in skill_store._SUPERSEDED_STARTER_DESCRIPTIONS.items():
        assert sid in current, sid
        assert current[sid] not in stale, sid


def test_seed_starters_is_idempotent_by_id(isolated):
    # seed_starters() refills any missing starter id; the "don't resurrect a deleted
    # starter" guarantee is the CALLER's empty-store gate (lifespan in main.py), not
    # this primitive. So after deleting one and re-seeding, it is refilled — and a
    # second re-seed with everything present inserts nothing.
    s = SkillStore()
    s.seed_starters()
    s.delete("catch-up")
    assert s.seed_starters() == 1  # the one missing id is refilled
    assert s.find_by_id("catch-up") is not None
    assert s.seed_starters() == 0  # now everything present → no-op


# ── base CRUD still works ──────────────────────────────────────────────────────
def test_delete_and_find(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["a"]})
    assert s.find_by_id(sk["id"]) is not None
    assert s.delete(sk["id"]) is True
    assert s.find_by_id(sk["id"]) is None
    assert s.all() == []


def test_delete_failure_is_loud_and_leaves_skill_durably_disabled(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "delete-failure", "body": "keep", "enabled": True})
    monkeypatch.setattr(
        skill_store,
        "_remove_pinned_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    with pytest.raises(OSError, match="busy"):
        store.delete(skill["id"])

    remaining = store.find_by_id(skill["id"])
    assert remaining is not None
    assert remaining["enabled"] is False


def test_delete_final_state_failure_leaves_disabled_tombstone_and_no_folder(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "delete-final-failure", "enabled": True})
    original_save = skill_store._save_state
    calls = 0

    def fail_final_save(state):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("final state failed")
        original_save(state)

    monkeypatch.setattr(skill_store, "_save_state", fail_final_save)

    with pytest.raises(OSError, match="final state failed"):
        store.delete(skill["id"])

    state = SkillStateStore().all_as_dict()
    assert state[skill["id"]]["enabled"] is False
    assert not (isolated / "skills" / skill["id"]).exists()


# ── a skill never promises the app's approval card ─────────────────────────────
# Whether a write raises a confirm card is the user's permission setting, never the skill's: the
# same write asks on one install and runs straight away on another (seen 2026-10-15 — a skill said
# "each shows its confirm card", and with the book's permissions the fix applied at once). A skill
# asks before writing; it never promises, or relies on, a card.
@pytest.mark.parametrize("sid", sorted(x["id"] for x in STARTER_SKILLS))
def test_no_starter_promises_a_confirm_card(sid):
    import re

    starter = next(x for x in STARTER_SKILLS if x["id"] == sid)
    texts = {"SKILL.md": starter["body"], **(starter.get("files") or {})}
    for path, text in texts.items():
        if sid == "skill-creator" and path in ("SKILL.md", "references/authoring-guide.md"):
            # The guide quotes the phrase to forbid it.
            text = text.replace('"shows a confirm card"', "")
        assert not re.search(r"confirm(ation)? card|approve-tier", text, re.I), f"{sid}/{path}"


def test_the_guide_says_ask_first_and_never_promise_a_card():
    guide = next(x for x in STARTER_SKILLS if x["id"] == "skill-creator")
    text = guide["files"]["references/authoring-guide.md"]
    assert "it proposes\nthe change and asks first" in text
    assert 'never write that a step "shows a confirm card"' in text
