"""Tests for the run_script tool (script-bundling-skills S3+S4).

Tests are OFFLINE + ISOLATED — no LLM, no network. All file I/O is redirected
to tmp_path. Tests cover:

  1. tool unreachable outside a script-skill scope (Gate 1)
  2. tool blocked when the instance kill switch is off (Gate 2)
  3. an Automation run takes the same chain as a chat (no unattended switch any more)
  4. trivial echo bundle runs end-to-end when all gates are on
  5. sha mismatch is refused by the sandbox before exec (when sha is supplied)

Run:
    .venv/bin/pytest tests/test_run_script_tool.py -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from backend.ai.tools import _skill_scope, execute_tool
from backend.db import skill_store as _skill_store_mod
from backend.db.skill_store import SkillStore
from backend.services import skill_bundles as _sb_mod

# ── Helpers ───────────────────────────────────────────────────────────────────


def call(tool: str, **inp: Any):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def err(result: dict) -> str:
    return result.get("error", "")


def _make_script_skill(skills_dir: Path | None = None) -> dict:
    """Create a minimal script skill record with an actual scripts/ dir."""
    store = SkillStore()
    skill = store.create(
        {
            "name": "Test Script Skill",
            "description": "Use to test a bundled script.",
            "body": "Run the echo script.",
            "allowed_tools": [],
        }
    )
    sid = skill["id"]
    if skills_dir is None:
        skills_dir = _skill_store_mod.SKILLS_DIR
    scripts_dir = skills_dir / sid / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / ".keep").write_text("", encoding="utf-8")
    return store.find_by_id(sid)


def _write_echo_bundle(bundle_dir: Path, *, break_sha: bool = False) -> str:
    """Write a trivial echo bundle to ``bundle_dir``; return the content-addressed sha."""
    from backend.services.skill_bundles import compute_bundle_sha

    bundle_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = bundle_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    script_content = (
        "import json, os, sys\n"
        "data = json.loads(open(os.environ['SKILL_INPUT']).read())\n"
        "print(json.dumps({'echo': data}))\n"
    )
    (scripts_dir / "echo.py").write_text(script_content, encoding="utf-8")

    sha = compute_bundle_sha(bundle_dir)

    if break_sha:
        (scripts_dir / "echo.py").write_text("# tampered\n", encoding="utf-8")

    return sha


def _bundle_ctx(skill: dict, bundle_dir: Path, sha: str = "", manifest_override=None) -> dict:
    manifest = manifest_override or {
        "scripts": [
            {
                "id": "echo",
                "interpreter": "python",
                "entry": "scripts/echo.py",
                "timeout_s": 30,
                "network": False,
            }
        ],
    }
    return {
        "skill_id": skill["id"],
        "manifest": manifest,
        "bundle_dir": bundle_dir,
        "bundle_sha": sha,
        "allowed_script_ids": {"echo"},
    }


def _set_verified_bundle(skill: dict, manifest: dict, bundle_dir: Path, sha: str) -> None:
    with SkillStore().pinned_snapshot(skill["id"]) as (fresh, pinned):
        assert fresh is not None and pinned is not None
        _skill_scope.set_active_bundle(
            {
                "skill_id": skill["id"],
                "manifest": manifest,
                "bundle_dir": bundle_dir,
                "bundle_sha": sha,
                "folder_identity": pinned.identity,
                "allowed_script_ids": {"echo"},
            }
        )


def _set_instance_switch(isolated: Path, *, enabled: bool) -> None:
    # app_config is DB-backed now (Wave 5D2a, JSON->DB) — conftest's autouse Tier-2 table
    # clear already isolates it per test, no path kwarg needed. `isolated` kept as the
    # param for call-site compatibility.
    del isolated
    from backend.db.app_config_store import SCRIPT_SKILLS_FLAG, AppConfigStore

    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, enabled)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect all stores to tmp_path; reset scope ContextVars around each test."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(_skill_store_mod, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(_skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    # app_config/skills_state are DB-backed now (Wave 5) and isolated per test by
    # conftest's autouse Tier-2 table clear — no per-store monkeypatch needed any more.
    monkeypatch.setattr(_sb_mod, "SKILLS_DIR", skills_dir)
    _skill_scope.clear_scope()
    yield tmp_path
    _skill_scope.clear_scope()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestGate1_ScopeRequired:
    """Gate 1: run_script is unreachable outside a script-skill turn."""

    def test_refused_when_no_active_bundle(self, isolated):
        _set_instance_switch(isolated, enabled=True)  # an off switch is refused earlier (Gate 2)
        result, proposed = call("run_script", script_id="echo", input={})
        assert proposed is None
        assert "not available outside a script-bundling skill turn" in err(result)

    def test_also_refused_after_non_script_skill_scope(self, isolated):
        _set_instance_switch(isolated, enabled=True)
        _skill_scope.set_offer_scope(["get_portfolio", "run_script"])
        result, _ = call("run_script", script_id="echo", input={})
        assert "not available outside a script-bundling skill turn" in err(result)


class TestGate2_InstanceKillSwitch:
    """Gate 2: instance kill switch must be on."""

    def test_refused_when_kill_switch_off_by_default(self, isolated):
        skill = _make_script_skill()
        _skill_scope.set_active_bundle(_bundle_ctx(skill, Path("/tmp/nonexistent")))
        _skill_scope.set_offer_scope(["run_script"])
        result, _ = call("run_script", script_id="echo", input={})
        assert "Script-bundling skills are disabled" in err(result)

    def test_refused_when_kill_switch_explicitly_off(self, isolated):
        _set_instance_switch(isolated, enabled=False)
        skill = _make_script_skill()
        _skill_scope.set_active_bundle(_bundle_ctx(skill, Path("/tmp/nonexistent")))
        _skill_scope.set_offer_scope(["run_script"])
        result, _ = call("run_script", script_id="echo", input={})
        assert "Script-bundling skills are disabled" in err(result)


class TestEndToEnd:
    """End-to-end: trivial echo bundle runs through the sandbox when all gates on."""

    def _setup_gates(self, isolated: Path) -> None:
        _set_instance_switch(isolated, enabled=True)

    def test_echo_bundle_runs(self, isolated):
        """All gates on + a real echo script → sandbox runs and returns JSON result."""
        self._setup_gates(isolated)
        skill = _make_script_skill()

        bundle_dir = _skill_store_mod.SKILLS_DIR / skill["id"]
        sha = _write_echo_bundle(bundle_dir)
        from backend.services.skill_bundles import _derive_manifest_from_folder

        manifest = _derive_manifest_from_folder(bundle_dir)

        _set_verified_bundle(skill, manifest, bundle_dir, sha)
        _skill_scope.set_offer_scope(["run_script"])

        result, proposed = call("run_script", script_id="echo", input={"hello": "world"})
        assert proposed is None
        assert result.get("ok") is True, f"Expected ok=True, got: {result}"
        assert result.get("data") == {"echo": {"hello": "world"}}

    def test_sha_mismatch_refused_when_sha_supplied(self, isolated):
        """A tampered bundle is refused when the caller supplies a sha pin (T7)."""
        self._setup_gates(isolated)
        skill = _make_script_skill()

        bundle_dir = _skill_store_mod.SKILLS_DIR / skill["id"]
        sha = _write_echo_bundle(bundle_dir, break_sha=True)
        manifest = {
            "version": "v1",
            "scripts": [{"id": "echo", "entry": "scripts/echo.py", "timeout_s": 10}],
        }

        _set_verified_bundle(skill, manifest, bundle_dir, sha)
        _skill_scope.set_offer_scope(["run_script"])

        result, _ = call("run_script", script_id="echo", input={})
        assert result.get("ok") is not True
        assert err(result), "expected an error message"
        error_text = err(result).lower()
        assert "refused" in error_text or "mismatch" in error_text or "changed" in error_text

    def test_unknown_script_id_refused(self, isolated):
        """A script_id not in scripts/ is refused before the sandbox."""
        self._setup_gates(isolated)
        skill = _make_script_skill()

        bundle_dir = _skill_store_mod.SKILLS_DIR / skill["id"]
        sha = _write_echo_bundle(bundle_dir)
        from backend.services.skill_bundles import _derive_manifest_from_folder

        manifest = _derive_manifest_from_folder(bundle_dir)

        _skill_scope.set_active_bundle(
            {
                "skill_id": skill["id"],
                "manifest": manifest,
                "bundle_dir": bundle_dir,
                "bundle_sha": sha,
                "allowed_script_ids": {"echo"},
            }
        )
        _skill_scope.set_offer_scope(["run_script"])

        result, _ = call("run_script", script_id="nonexistent_id", input={})
        assert "not declared in the skill manifest" in err(result)
        assert "echo" in err(result)


class TestToolRegistered:
    """Smoke-test that run_script appears in the registry."""

    def test_run_script_in_tool_schemas(self):
        from backend.ai.tools import TOOL_SCHEMAS

        names = {t["name"] for t in TOOL_SCHEMAS}
        assert "run_script" in names


class TestAutomationRunsTakeTheSameChain:
    """There is no unattended switch: whether a script asks first is the tool's permission value
    and the approval mode, the same in a chat and in an Automation run. Only the instance switch
    (Gate 2) decides whether scripts exist at all."""

    def test_a_run_with_nobody_watching_is_not_refused_for_being_unattended(self, isolated):
        _set_instance_switch(isolated, enabled=True)
        skill = _make_script_skill()
        _skill_scope.set_active_bundle(_bundle_ctx(skill, Path("/tmp/nonexistent")))
        _skill_scope.set_offer_scope(["run_script"])
        res, _ = call("run_script", script_id="no-such-script", input={})
        assert "unattended" not in err(res).lower()

    def test_the_switch_and_its_reader_are_gone(self):
        from backend.db import app_config_store

        assert not hasattr(app_config_store, "script_skills_unattended_enabled")
        assert not hasattr(app_config_store, "SCRIPT_SKILLS_UNATTENDED_FLAG")
        with pytest.raises(ImportError):
            import backend.core.unattended  # noqa: F401
