"""The TradingAgents environment: where it lives, how it is built and when it is refused.

No network and no real venv: the command runner is replaced, except two tests that run a real
throwaway interpreter to prove the tree is killed on a timeout.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from backend.core import paths, process_tree
from backend.core.errors import ConflictError, FeatureDisabledError
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.services import connector_catalog
from backend.services.trading_agents import install

pytestmark = pytest.mark.flags(trading_agents=True)


@pytest.fixture(autouse=True)
def isolated(db_isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    process = SimpleNamespace(demo_mode=False, onw_qa_mode=False)
    monkeypatch.setattr(install, "get_settings", lambda: process)
    return process


def _state() -> dict:
    return TradingAgentsConfigStore().read()["state"]


SELFTEST_OK = json.dumps({"ok": True, "ta_version": "0.7.1", "ta_commit": install.TA_COMMIT}) + "\n"


class Commands:
    """Records the commands an install runs and answers each from a script."""

    def __init__(self, fail_on: str | None = None, tail: str = "ok"):
        self.seen: list[list[str]] = []
        self.fail_on = fail_on
        self.tail = tail

    async def run(self, command, cwd=None):
        self.seen.append(list(command))
        if command[-1] == "--selftest":
            return 0, SELFTEST_OK
        if self.fail_on and self.fail_on in command:
            return 1, self.tail
        return 0, self.tail


def _install_with(monkeypatch, commands: Commands, uv: str | None = "/bin/uv"):
    monkeypatch.setattr(install, "_run", commands.run)
    monkeypatch.setattr(install, "_installer", lambda: uv)


# ── paths ────────────────────────────────────────────────────────────────────────────────


def test_the_environment_lives_under_the_data_dir(tmp_path):
    assert install.root() == tmp_path / "data" / "integrations" / "trading_agents"
    assert install.venv_python() == install.root() / "venv" / "bin" / "python"


def test_windows_uses_the_scripts_python(monkeypatch):
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    assert install.venv_python().parts[-2:] == ("Scripts", "python.exe")


def test_scratch_folders_are_created_under_the_root():
    folders = install.scratch("tar_0123456789ab")
    for folder in (folders.home, folders.results, folders.cache, folders.memory_log.parent):
        assert folder.is_dir() and install.root() in folder.parents


def test_each_run_has_its_own_home_results_and_memory_but_shares_the_cache():
    first, second = install.scratch("tar_aaaaaaaaaaaa"), install.scratch("tar_bbbbbbbbbbbb")
    assert first.cache == second.cache
    for name in ("home", "results", "memory_log"):
        assert getattr(first, name) != getattr(second, name)
        assert install.run_dir("tar_aaaaaaaaaaaa") in getattr(first, name).parents


@pytest.mark.parametrize("run_id", ["", "..", "../etc", "tar/x", "tar_x\\y", "a" * 65])
def test_a_run_id_that_is_not_a_plain_segment_is_refused(run_id):
    with pytest.raises(ValueError):
        install.run_dir(run_id)


def test_removing_a_run_deletes_only_its_folder_and_boot_removes_them_all():
    keep, drop = install.scratch("tar_aaaaaaaaaaaa"), install.scratch("tar_bbbbbbbbbbbb")
    drop.memory_log.write_text("a past decision for ACME")
    install.remove_run("tar_bbbbbbbbbbbb")
    assert not install.run_dir("tar_bbbbbbbbbbbb").exists() and keep.home.is_dir()
    install.remove_all_runs()
    assert not install.run_dir("tar_aaaaaaaaaaaa").exists() and install.root().exists()


def test_the_pin_is_one_full_commit():
    assert install.TA_PIN.endswith("@" + install.TA_COMMIT)
    assert len(install.TA_COMMIT) == 40
    assert install.worker_script().is_file()


# ── refusal ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("attribute", ["demo_mode", "onw_qa_mode"])
async def test_an_install_is_refused_in_a_demo_and_a_qa_process(isolated, attribute):
    setattr(isolated, attribute, True)
    with pytest.raises(FeatureDisabledError):
        await install.install()
    with pytest.raises(FeatureDisabledError):
        install.start_install()
    assert _state().get("install_status") is None


# ── building it ──────────────────────────────────────────────────────────────────────────


async def test_with_uv_it_makes_a_venv_installs_the_pin_and_runs_the_selftest(monkeypatch):
    commands = Commands()
    _install_with(monkeypatch, commands)
    await install.install()
    venv, deps, pin, pip_check, check = commands.seen
    assert venv[:3] == ["/bin/uv", "venv", str(install.venv_dir())]
    assert deps[:3] == ["/bin/uv", "pip", "install"]
    assert deps[deps.index("--python") + 1] == str(install.venv_python())
    assert deps[-5:] == [
        "--require-hashes",
        "--no-deps",
        "--no-build",
        "-r",
        str(install.lock_file()),
    ]
    assert pin[:3] == ["/bin/uv", "pip", "install"] and pin[-1] == install.TA_PIN
    assert "--no-deps" in pin and "--require-hashes" not in pin
    assert pip_check == ["/bin/uv", "pip", "check", "--python", str(install.venv_python())]
    assert check[:2] == [str(install.venv_python()), "-I"] and check[-1] == "--selftest"
    assert _state()["install_status"] == "ready"
    assert _state()["installed_version"] == install.TA_COMMIT
    assert "install_error" not in _state()


async def test_without_uv_it_falls_back_to_venv_and_pip(monkeypatch):
    commands = Commands()
    _install_with(monkeypatch, commands, uv=None)
    await install.install()
    assert commands.seen[0][1:3] == ["-m", "venv"]
    assert commands.seen[1][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in commands.seen[1] and "--no-deps" in commands.seen[1]
    assert commands.seen[1][-6:-1] == [
        "--require-hashes",
        "--no-deps",
        "--only-binary",
        ":all:",
        "-r",
    ]
    assert commands.seen[2][1:4] == ["-m", "pip", "install"]
    assert commands.seen[2][-1] == install.TA_PIN
    assert commands.seen[3][1:] == ["-m", "pip", "check"]
    assert _state()["install_status"] == "ready"


def test_the_shipped_lock_is_fully_hashed_and_leaves_out_the_pin_itself():
    requirements: dict[str, list[str]] = {}
    for line in install.lock_file().read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith(" "):
            current = requirements.setdefault(line.split()[0], [])
        current.append(line)
    assert requirements and not any(
        name.lower().startswith("tradingagents") for name in requirements
    )
    assert all("==" in name for name in requirements)
    assert all(any("--hash=sha256:" in ln for ln in lines) for lines in requirements.values())


async def test_with_no_lock_file_the_install_fails_naming_it_and_installs_nothing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(install, "lock_file", lambda: tmp_path / "absent.lock")
    commands = Commands()
    _install_with(monkeypatch, commands)
    await install.install()
    assert commands.seen == []
    assert _state()["install_status"] == "failed"
    assert "absent.lock" in _state()["install_error"] and "unpinned" in _state()["install_error"]
    assert not install.venv_dir().exists()


async def test_a_dependency_problem_found_by_pip_check_fails_the_install(monkeypatch):
    commands = Commands(
        fail_on="check", tail="langgraph 1.2 requires langchain-core, not installed"
    )
    _install_with(monkeypatch, commands)
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "Checking the installed dependencies failed" in _state()["install_error"]
    assert "langchain-core" in _state()["install_error"]
    assert "check" in commands.seen[-1]  # the self-test never ran


async def test_an_install_with_no_recorded_commit_fails_instead_of_warning(monkeypatch):
    async def run(command, cwd=None):
        if command[-1] == "--selftest":
            return 0, '{"ok": true, "ta_version": "0.7.1"}'
        return 0, ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "could not read which commit" in _state()["install_error"]
    assert _state().get("installed_version") is None


async def test_an_install_that_is_not_the_pinned_commit_fails_the_check(monkeypatch):
    async def run(command, cwd=None):
        if command[-1] == "--selftest":
            return 0, '{"ok": true, "ta_version": "0.7.1", "ta_commit": "' + "0" * 40 + '"}'
        return 0, ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "not the pinned" in _state()["install_error"]
    assert _state().get("installed_version") is None


async def test_an_install_at_the_pinned_commit_passes_the_check(monkeypatch):
    async def run(command, cwd=None):
        if command[-1] == "--selftest":
            report = {"ok": True, "ta_version": "0.7.1", "ta_commit": install.TA_COMMIT}
            return 0, json.dumps(report)
        return 0, ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    await install.install()
    assert _state()["install_status"] == "ready"


async def test_a_failed_step_is_recorded_with_a_redacted_log_tail(monkeypatch):
    secret = "sk-ant-api03-" + "A" * 40
    commands = Commands(fail_on="install", tail=f"ERROR: could not fetch ?api_key={secret} 404")
    _install_with(monkeypatch, commands)
    await install.install()
    state = _state()
    assert state["install_status"] == "failed"
    assert "failed" in state["install_error"]
    assert secret not in state["install_error"]
    assert not install.venv_dir().exists()


async def test_a_failed_selftest_leaves_the_install_failed(monkeypatch):
    async def run(command, cwd=None):
        if command[-1] == "--selftest":
            return 1, '{"ok": false, "error": "ImportError: no module"}'
        return 0, ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "install check failed" in _state()["install_error"]


async def test_an_install_past_the_timeout_is_stopped_and_failed(monkeypatch):
    async def forever(command, cwd=None):
        await asyncio.sleep(3600)

    monkeypatch.setattr(install, "_run", forever)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    monkeypatch.setattr(install, "INSTALL_TIMEOUT_SECONDS", 0.05)
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "15 minutes" in _state()["install_error"]


async def test_start_install_marks_it_installing_at_once_and_refuses_a_second(monkeypatch):
    gate = asyncio.Event()

    async def slow(command, cwd=None):
        await gate.wait()
        return 0, SELFTEST_OK

    monkeypatch.setattr(install, "_run", slow)
    monkeypatch.setattr(install, "_installer", lambda: "/bin/uv")
    install.start_install()
    assert _state()["install_status"] == "installing"
    with pytest.raises(ConflictError):
        install.start_install()
    gate.set()
    await asyncio.gather(*install._tasks)
    assert _state()["install_status"] == "ready"


def test_an_install_cut_off_by_a_restart_is_failed_not_installing_forever():
    TradingAgentsConfigStore().merge_state({"install_status": "installing"})
    install.recover_interrupted()
    assert _state()["install_status"] == "failed"
    assert "interrupted" in _state()["install_error"]


def test_a_secret_in_install_error_is_redacted_by_the_connector_store():
    secret = "sk-ant-api03-" + "B" * 40
    TradingAgentsConfigStore().merge_state({"install_error": f"clone failed ?token={secret}"})
    assert secret not in _state()["install_error"]


# ── the real runner ──────────────────────────────────────────────────────────────────────


async def test_run_returns_the_exit_code_and_the_tail():
    code, tail = await install._run([sys.executable, "-c", "print('hello from a child')"])
    assert code == 0 and "hello from a child" in tail


async def test_a_command_past_its_timeout_is_killed_with_its_tree(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = f"import os,time;open({str(pid_file)!r},'w').write(str(os.getpid()));time.sleep(3600)"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(install._run([sys.executable, "-c", script]), 1.0)
    pid = int(pid_file.read_text())
    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_command_that_cannot_start_is_an_error_the_install_reports(monkeypatch):
    async def missing(command, cwd=None):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(install, "_run", missing)
    await install.install()
    assert _state()["install_status"] == "failed"
    assert "could not run" in _state()["install_error"]


# ── removal ──────────────────────────────────────────────────────────────────────────────


def test_removing_the_connector_deletes_the_environment_and_every_scratch_folder():
    folders = install.scratch("tar_0123456789ab")
    install.venv_dir().mkdir(parents=True)
    folders.memory_log.write_text("a past decision for ACME")
    connector_catalog.kind_of("trading_agents").remove("trading_agents")
    assert not install.root().exists()


def test_removal_with_nothing_installed_is_a_no_op():
    install.remove_environment()
    assert not install.root().exists()


async def test_the_installer_and_selftest_run_in_the_redirected_environment(monkeypatch, tmp_path):
    """The child sees the install scratch folder as its home and its working directory, never the
    server's own folder or the owner's home, and no ``.env`` loading."""
    monkeypatch.setenv("HOME", "/real/owner/home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/real/owner/config")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SERVER_FOLDER_SECRET=1\n")
    probe = (
        "import json,os;print(json.dumps({'cwd': os.getcwd(), 'home': os.environ['HOME'], "
        "'xdg': os.environ['XDG_CONFIG_HOME'], 'dotenv': os.environ['PYTHON_DOTENV_DISABLED'], "
        "'env_file': os.path.exists('.env')}))"
    )
    code, tail = await install._run([sys.executable, "-c", probe])
    seen = json.loads(tail)
    scratch = str(install.root() / "install")
    assert code == 0 and not seen["env_file"]
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(scratch)
    assert seen["home"] == seen["xdg"] == scratch and seen["dotenv"] == "1"
