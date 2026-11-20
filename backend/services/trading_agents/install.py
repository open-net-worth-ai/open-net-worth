"""The TradingAgents environment: a venv of its own under the data folder, built from ONE pinned commit.

TradingAgents is third-party code with a heavy dependency tree (pandas, LangChain, LangGraph), so
it never shares ONW's interpreter: it lives in ``<data dir>/integrations/trading_agents/venv`` and a
run is a subprocess of that interpreter (``ai/trading_agents/runner``). Installing is always a
click by the owner on the connector page — never at boot, never from a tool — and is refused in a
public demo and in a QA process (tests inject a fake worker instead).

Layout under ``root()``: ``venv/`` (the interpreter), ``cache/`` (market-data cache, shared) and
``runs/<run id>/`` (one run's own home, results folder and decision memory log). A run's folder holds
text derived from the portfolio context, so it is deleted when the run ends or its row is deleted,
and ``remove_environment`` deletes the whole tree. Run rows are kept (the owner deletes them from
the UI).

The install is three steps, and a missing ``worker/requirements.lock`` FAILS it (it never falls
back to unpinned dependencies): every dependency from that hash-locked file (``--require-hashes
--no-deps``, wheels only), then the pinned commit with ``--no-deps``, then a ``pip check``. The
installer and the worker's self-test run in the worker's redirected environment (HOME and every
XDG/AppData folder a scratch folder, which is also their working directory). Building the pinned
commit's own sdist still fetches its build backend unpinned — an accepted residual.

An install writes its progress to the connector's app-written ``state`` (``install_status``,
``installed_version``, ``install_error``); a failure's log tail is redacted before it is stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from backend.core import data_events, paths, platform_fs, process_tree
from backend.core.child_env import scrubbed_environ
from backend.core.config import get_settings
from backend.core.errors import ConflictError, ExternalServiceError, FeatureDisabledError
from backend.core.redact_error import redact_error
from backend.db.trading_agents_config_store import TradingAgentsConfigStore

log = logging.getLogger(__name__)

# The ONE pinned TradingAgents commit (v0.7.1, 2026-11-04). A bump is a code review plus a release
# note: re-read docs/specs/trading-agents-pinned-findings.md against the new commit first.
TA_COMMIT = "9fa1196fffbcfd9de5146d2dd2869df84ab99838"
TA_PIN = f"git+https://github.com/TauricResearch/TradingAgents@{TA_COMMIT}"

INSTALL_TIMEOUT_SECONDS = 15 * 60
SELFTEST_TIMEOUT_SECONDS = 120
DOMAIN = "trading_agents"

ABSENT, INSTALLING, READY, FAILED = "absent", "installing", "ready", "failed"

_TAIL_CHARS = 4000
_MAX_ERROR_CHARS = 1000
_READ_CHUNK = 4096
_STOP_WAIT_SECONDS = 5.0
_tasks: set[asyncio.Task] = set()


class Scratch(NamedTuple):
    """One run's folders (and one file) TradingAgents is told to use instead of ``~``. ``cache`` is
    the only one shared between runs."""

    home: Path
    results: Path
    cache: Path
    memory_log: Path


_RUN_ID = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# Where a library looks for per-user config and caches: for the worker and the installer, every
# one of them is a scratch folder, never the owner's real home.
HOME_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
)


def _no_runs() -> int:
    return 0


_busy: Callable[[], int] = _no_runs


# ── paths ────────────────────────────────────────────────────────────────────────────────


def root() -> Path:
    """``<data dir>/integrations/trading_agents`` — follows ``ONW_ROOT`` / ``DATA_DIR``."""
    return paths.DATA_DIR / "integrations" / "trading_agents"


def venv_dir() -> Path:
    return root() / "venv"


def venv_python() -> Path:
    if process_tree.IS_WINDOWS:
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def worker_script() -> Path:
    """The worker shipped beside this module (a data file of the wheel, never imported)."""
    return Path(__file__).parent / "worker" / "ta_worker.py"


def lock_file() -> Path:
    """The hash-locked dependency list shipped beside the worker (a data file of the wheel)."""
    return worker_script().parent / "requirements.lock"


def run_dir(run_id: str) -> Path:
    """One run's folder. A run id is a path segment here, so anything else is refused."""
    if not _RUN_ID.match(run_id or ""):
        raise ValueError("not a TradingAgents run id")
    return root() / "runs" / run_id


def scratch(run_id: str) -> Scratch:
    """Create (owner-only) and return one run's scratch folders."""
    base = run_dir(run_id)
    folders = Scratch(
        base / "home", base / "results", root() / "cache", base / "memory" / "memory_log.md"
    )
    for folder in (folders.home, folders.results, folders.cache, folders.memory_log.parent):
        folder.mkdir(parents=True, exist_ok=True)
        platform_fs.restrict_to_owner(folder)
    return folders


def redirected_environ(home: Path) -> dict[str, str]:
    """The scrubbed environment with every per-user folder pointed at ``home`` and ``.env``
    loading switched off — what third-party code runs under here."""
    env = scrubbed_environ()
    env.update(dict.fromkeys(HOME_VARIABLES, str(home)))
    for name in ("HOMEDRIVE", "HOMEPATH"):
        env.pop(name, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    return env


def install_scratch() -> Path:
    """The folder the installer and the self-test run in (owner-only), as their home and cwd."""
    folder = root() / "install"
    folder.mkdir(parents=True, exist_ok=True)
    platform_fs.restrict_to_owner(folder)
    return folder


def remove_run(run_id: str) -> None:
    """Delete one run's folder (its logs and memory hold text derived from the portfolio context)."""
    shutil.rmtree(run_dir(run_id), ignore_errors=True)


def remove_all_runs() -> None:
    """Boot: nothing is running, so no run's folder is still wanted."""
    shutil.rmtree(root() / "runs", ignore_errors=True)


def set_busy_probe(probe: Callable[[], int]) -> None:
    """The runner says how many runs are waiting or going (``services`` cannot import it)."""
    global _busy
    _busy = probe


def refuse_if_busy() -> None:
    """Rebuilding or removing the venv under a run would pull the interpreter out from under it."""
    if _busy() > 0:
        raise ConflictError(
            "A TradingAgents run is waiting or going. Wait for it to finish or cancel it first.",
            status="runs_pending",
        )


# ── refusal + state ──────────────────────────────────────────────────────────────────────


def _refusal() -> Optional[str]:
    settings = get_settings()
    if settings.demo_mode:
        return "TradingAgents cannot be installed on a public demo."
    if settings.onw_qa_mode:
        return "TradingAgents is not installed in a QA process; tests inject a fake worker."
    return None


def refuse_if_not_allowed() -> None:
    reason = _refusal()
    if reason is not None:
        raise FeatureDisabledError(reason, status="install_refused")


def _set_state(**changes: object) -> None:
    TradingAgentsConfigStore().merge_state(changes)
    data_events.publish([DOMAIN])


def _failure_text(tail: str) -> str:
    return redact_error(" ".join(tail.split()))[-_MAX_ERROR_CHARS:]


def recover_interrupted() -> None:
    """Boot: an install that was running when the app stopped is over — say so, instead of
    leaving the connector on "installing" forever."""
    if TradingAgentsConfigStore().read()["state"].get("install_status") == INSTALLING:
        _set_state(
            install_status=FAILED, install_error="The install was interrupted. Install it again."
        )


# ── running a command ────────────────────────────────────────────────────────────────────


def _stop(process: asyncio.subprocess.Process) -> None:
    try:
        process_tree.kill_tree(process.pid)
    except OSError:
        pass  # already gone


async def _run(command: list[str], *, cwd: Optional[Path] = None) -> tuple[int, str]:
    """Run ``command`` in its own process tree, in the redirected environment and (unless ``cwd``
    says otherwise) the install scratch folder, so no ``.env`` or config of the server's own folder
    or the owner's home is in reach; ``(exit code, bounded output tail)``. Cancelling (a timeout
    above) kills the whole tree."""
    scratch_dir = install_scratch()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd or scratch_dir,
        env=redirected_environ(scratch_dir),
        **process_tree.spawn_kwargs(),
    )
    tail = ""
    try:
        assert process.stdout is not None
        while chunk := await process.stdout.read(_READ_CHUNK):
            tail = (tail + chunk.decode("utf-8", errors="replace"))[-_TAIL_CHARS:]
        return await process.wait(), tail.replace("\r", "\n")
    except BaseException:
        _stop(process)
        await asyncio.wait_for(process.wait(), _STOP_WAIT_SECONDS)
        raise


async def _checked(command: list[str], what: str) -> str:
    code, tail = await _run(command)
    if code != 0:
        raise ExternalServiceError(f"{what} failed: {_failure_text(tail)}")
    return tail


# ── the install ──────────────────────────────────────────────────────────────────────────


def _installer() -> Optional[str]:
    return shutil.which("uv")


async def _create_environment() -> None:
    if not lock_file().is_file():
        raise ExternalServiceError(
            f"The dependency lock {lock_file().name} is missing from this Open Net Worth "
            "installation, so TradingAgents will not install unpinned dependencies. Reinstall "
            "Open Net Worth."
        )
    shutil.rmtree(venv_dir(), ignore_errors=True)
    root().mkdir(parents=True, exist_ok=True)
    uv = _installer()
    python = str(venv_python())
    if uv is not None:
        await _checked(
            [uv, "venv", str(venv_dir()), "--python", sys.executable], "Creating the environment"
        )
        pip = [uv, "pip", "install", "--python", python]
        wheels_only, check = ["--no-build"], [uv, "pip", "check", "--python", python]
    else:
        await _checked([sys.executable, "-m", "venv", str(venv_dir())], "Creating the environment")
        pip = [python, "-m", "pip", "install", "--no-input"]
        wheels_only, check = ["--only-binary", ":all:"], [python, "-m", "pip", "check"]
    await _checked(
        [*pip, "--require-hashes", "--no-deps", *wheels_only, "-r", str(lock_file())],
        "Installing the locked dependencies",
    )
    await _checked([*pip, "--no-deps", TA_PIN], "Installing")
    await _checked(check, "Checking the installed dependencies")


async def _selftest() -> str:
    """Run the worker's ``--selftest`` in the new venv; the TradingAgents version it reports."""
    command = [str(venv_python()), "-I", str(worker_script()), "--selftest"]
    try:
        code, tail = await asyncio.wait_for(_run(command), SELFTEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise ExternalServiceError("The install check timed out.") from exc
    report = _last_json(tail)
    if code != 0 or not report.get("ok"):
        raise ExternalServiceError(f"The install check failed: {_failure_text(tail)}")
    commit = str(report.get("ta_commit") or "")
    if not commit:
        raise ExternalServiceError(
            "The install check could not read which commit TradingAgents was installed from, so "
            "it cannot be verified against the pin."
        )
    if commit != TA_COMMIT:
        raise ExternalServiceError(
            f"The install check found TradingAgents at {commit[:12]}, not the pinned {TA_COMMIT[:12]}."
        )
    return str(report.get("ta_version") or "")


def _last_json(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


async def install() -> None:
    """Build the venv from ``TA_PIN`` and verify it; ``state`` ends ``ready`` or ``failed``. Never
    raises for an install failure (the reason is in ``state.install_error``)."""
    refuse_if_not_allowed()
    _set_state(install_status=INSTALLING, install_error=None)
    try:
        await asyncio.wait_for(_create_environment(), INSTALL_TIMEOUT_SECONDS)
        version = await _selftest()
    except asyncio.TimeoutError:
        _fail("The install took longer than 15 minutes and was stopped.")
    except ExternalServiceError as exc:
        _fail(exc.message or str(exc))
    except OSError as exc:
        _fail(f"The install could not run: {exc.strerror or type(exc).__name__}")
    else:
        log.info("trading_agents_installed commit=%s version=%s", TA_COMMIT[:8], version)
        _set_state(install_status=READY, installed_version=TA_COMMIT, install_error=None)
    finally:
        shutil.rmtree(root() / "install", ignore_errors=True)


def _fail(reason: str) -> None:
    log.warning("trading_agents_install_failed")
    shutil.rmtree(venv_dir(), ignore_errors=True)
    _set_state(install_status=FAILED, installed_version=None, install_error=reason)


def start_install() -> None:
    """Begin (or redo) the install in the background and return at once: the status shows
    ``installing`` immediately, and a data event follows each change."""
    refuse_if_not_allowed()
    if TradingAgentsConfigStore().read()["state"].get("install_status") == INSTALLING:
        raise ConflictError("TradingAgents is already installing.", status=INSTALLING)
    refuse_if_busy()
    _set_state(install_status=INSTALLING, install_error=None)
    task = asyncio.get_running_loop().create_task(install(), name="trading_agents_install")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── removal ──────────────────────────────────────────────────────────────────────────────


def remove_environment() -> None:
    """Delete the venv and every scratch folder (the connector's ``remove`` hook). Run rows stay."""

    refuse_if_busy()

    def _warn(_func: object, path: str, exc: BaseException) -> None:
        log.warning("trading_agents_remove_failed path=%s error=%s", path, type(exc).__name__)

    if root().exists():
        shutil.rmtree(root(), onexc=_warn)


def environment_present() -> bool:
    return venv_python().exists()
