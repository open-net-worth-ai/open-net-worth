"""The TradingAgents environment: a venv of its own under the data folder, built from ONE pinned commit.

TradingAgents is third-party code with a heavy dependency tree (pandas, LangChain, LangGraph), so
it never shares ONW's interpreter: it lives in ``<data dir>/integrations/trading_agents/venv`` and a
run is a subprocess of that interpreter (``ai/trading_agents/runner``). Installing is always a
click by the owner on the connector page — never at boot, never from a tool — and is refused in a
public demo and in a QA process (tests inject a fake worker instead).

Layout under ``root()``: ``venv/`` (the interpreter), ``results/`` (the worker's working folder and
TradingAgents' own logs), ``cache/`` (market-data cache), ``memory/`` (its decision memory log).
The logs and the memory hold text derived from the portfolio context, so ``remove_environment``
deletes the whole folder. Run rows are kept (the owner deletes them from the UI).

An install writes its progress to the connector's app-written ``state`` (``install_status``,
``installed_version``, ``install_error``); a failure's log tail is redacted before it is stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import NamedTuple, Optional

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
    """The three folders (and one file) TradingAgents is told to use instead of ``~/.tradingagents``."""

    results: Path
    cache: Path
    memory_log: Path


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


def scratch() -> Scratch:
    """Create (owner-only) and return the worker's scratch folders."""
    base = root()
    folders = Scratch(base / "results", base / "cache", base / "memory" / "memory_log.md")
    for folder in (folders.results, folders.cache, folders.memory_log.parent):
        folder.mkdir(parents=True, exist_ok=True)
        platform_fs.restrict_to_owner(folder)
    return folders


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
    """Run ``command`` in its own process tree with the scrubbed environment; ``(exit code,
    bounded output tail)``. Cancelling (a timeout above) kills the whole tree."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=scrubbed_environ(),
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
    shutil.rmtree(venv_dir(), ignore_errors=True)
    root().mkdir(parents=True, exist_ok=True)
    uv = _installer()
    python = str(venv_python())
    if uv is not None:
        await _checked(
            [uv, "venv", str(venv_dir()), "--python", sys.executable], "Creating the environment"
        )
        await _checked([uv, "pip", "install", "--python", python, TA_PIN], "Installing")
        return
    await _checked([sys.executable, "-m", "venv", str(venv_dir())], "Creating the environment")
    await _checked([python, "-m", "pip", "install", "--no-input", TA_PIN], "Installing")


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
    _set_state(install_status=INSTALLING, install_error=None)
    task = asyncio.get_running_loop().create_task(install(), name="trading_agents_install")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── removal ──────────────────────────────────────────────────────────────────────────────


def remove_environment() -> None:
    """Delete the venv and every scratch folder (the connector's ``remove`` hook). Run rows stay."""

    def _warn(_func: object, path: str, exc: BaseException) -> None:
        log.warning("trading_agents_remove_failed path=%s error=%s", path, type(exc).__name__)

    if root().exists():
        shutil.rmtree(root(), onexc=_warn)


def environment_present() -> bool:
    return venv_python().exists()
