"""One TradingAgents run, from the bridge starting to the run's terminal status.

A session starts the run's LLM bridge, spawns the worker (its own process tree, the scrubbed
environment plus ONLY the variables the worker needs — never a model key: the one credential it
gets is the bridge's per-run token), reads its JSON lines, and enforces the caps. Tokens and cost
come from the bridge's in-process callback, never from the worker, which is untrusted for
accounting; the worker's ``llm_call`` lines only say which role a call belongs to.
The queue around it is ``ai/trading_agents/runner``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from backend.ai.trading_agents import llm_bridge
from backend.ai.trading_agents.llm_bridge import LlmBridge, RunLimits
from backend.core import process_tree
from backend.core.child_env import scrubbed_environ
from backend.core.errors import ConflictError, OpenNetWorthError
from backend.db.trading_agents_config_store import TradingAgentsConfigStore
from backend.services.trading_agents import install, runs, settings
from backend.services.trading_agents.roles import ROLE_BY_NODE, role_for

log = logging.getLogger(__name__)

RUN_TIMEOUT_SECONDS = 30 * 60
STOP_CANCEL, STOP_BUDGET, STOP_CALLS, STOP_TIMEOUT = "cancel", "budget", "calls", "timeout"
_STOP_STATUS = {
    STOP_BUDGET: runs.STOPPED_BUDGET,
    STOP_CALLS: runs.STOPPED_CALLS,
    STOP_TIMEOUT: runs.TIMED_OUT,
}
_STOP_TEXT = {
    STOP_BUDGET: "The run reached its spend cap of ${cap:.2f} and was stopped.",
    STOP_CALLS: "The run reached its limit of {calls} model calls and was stopped.",
    STOP_TIMEOUT: "The run took longer than 30 minutes and was stopped.",
}
_MAX_LINE_BYTES = 2 * 1024 * 1024
_MAX_PROGRESS_WRITES = 500
_STDERR_TAIL_CHARS = 2000
_ERROR_DETAIL_CHARS = 300
_ENV_NO_PROXY = ("127.0.0.1", "localhost")
# Where a library looks for per-user config and caches: every one of them is the run's own folder.
_HOME_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
)


@dataclass(frozen=True)
class Job:
    """Everything a run needs, resolved when it was submitted."""

    run_id: str
    ticker: str
    trade_date: str
    analysts: list[str]
    portfolio: Optional[dict]
    cfg: settings.Settings
    models: settings.ResolvedModels
    cap_usd: float


def worker_env(token: str, sec_user_agent: str, run_id: str) -> dict[str, str]:
    """The worker's whole environment: the allowlisted base plus what a run needs. No LLM vendor
    key, ever — ``OPENAI_COMPATIBLE_API_KEY`` carries the bridge's per-run token. The worker is
    not sandboxed (it can still read what its own user can), but nothing it would look up by
    habit points at the owner's real home: HOME and every XDG/AppData variable are the run's own
    folder, and ``.env`` loading is switched off."""
    folders = install.scratch(run_id)
    env = scrubbed_environ()
    env.update(dict.fromkeys(_HOME_VARIABLES, str(folders.home)))
    for name in ("HOMEDRIVE", "HOMEPATH"):
        env.pop(name, None)
    env.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "OPENAI_COMPATIBLE_API_KEY": token,
            "TRADINGAGENTS_RESULTS_DIR": str(folders.results),
            "TRADINGAGENTS_CACHE_DIR": str(folders.cache),
            "TRADINGAGENTS_MEMORY_LOG_PATH": str(folders.memory_log),
            "TRADINGAGENTS_NO_UPDATE_CHECK": "1",
            "SEC_EDGAR_USER_AGENT": sec_user_agent,
            **settings.data_vendor_env(),
        }
    )
    # The bridge is on loopback: a configured proxy must never be asked to reach it.
    for name in ("NO_PROXY", "no_proxy"):
        if name == "NO_PROXY" or name in env:
            hosts = [h for h in env.get(name, "").split(",") if h.strip()]
            env[name] = ",".join([*hosts, *(h for h in _ENV_NO_PROXY if h not in hosts)])
    return env


def _reports(state: dict) -> dict:
    """The flat report fields a run keeps, out of the worker's whitelisted final state."""
    out = {k: state.get(k) for k in runs.REPORT_FIELDS if isinstance(state.get(k), str)}
    for section in ("investment_debate_state", "risk_debate_state"):
        nested = state.get(section)
        if isinstance(nested, dict):
            out.update({k: v for k, v in nested.items() if k in runs.REPORT_FIELDS})
    return out


class RunSession:
    def __init__(self, job: Job, command: list[str]) -> None:
        self.job = job
        self.stop_reason: Optional[str] = None
        self.finished = asyncio.Event()
        self._command = command
        self._bridge: Optional[LlmBridge] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pending: dict[str, llm_bridge.CallRecord] = {}
        self._last_progress: Optional[tuple] = None
        self._progress_writes = 0
        self._metered = 0.0
        self._result: Optional[dict] = None
        self._signal: Optional[str] = None
        self._worker_error: Optional[str] = None
        self._stderr = ""

    # ── lifecycle ────────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Run to a terminal status. Never raises except ``CancelledError`` (the app is stopping)."""
        try:
            runs.apply_run_change("start", run_id=self.job.run_id)
        except ConflictError:
            self.finished.set()  # cancelled while it waited
            return
        TradingAgentsConfigStore().merge_state(
            {"last_run_at": datetime.now(timezone.utc).isoformat()}
        )
        try:
            await self._execute()
        except asyncio.CancelledError:
            self._kill()
            self._end(runs.INTERRUPTED, "The app stopped while the run was going.")
            raise
        except Exception as exc:  # noqa: BLE001 — a run always ends in a terminal status
            log.exception("trading_agents_run_failed run=%s", self.job.run_id)
            message = exc.message if isinstance(exc, OpenNetWorthError) else "The run failed."
            self._end(runs.FAILED, message or "The run failed.")
        else:
            self._conclude()
            await self._notify_finished()
        finally:
            self.finished.set()

    def stop(self, reason: str) -> None:
        """Stop the run for ``reason`` (the first reason wins): later bridge calls are refused and
        the worker's whole tree is killed."""
        if self.stop_reason is None:
            self.stop_reason = reason
        if self._bridge is not None and reason != STOP_CANCEL:
            self._bridge.refuse(self._stop_text(reason))
        self._kill()

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                process_tree.kill_tree(proc.pid)
            except OSError:
                pass  # already gone

    # ── the process ──────────────────────────────────────────────────────────────────────

    async def _execute(self) -> None:
        job = self.job
        models = llm_bridge.resolve_bridge_models(job.models)
        limits = RunLimits(max_calls=job.cfg.max_llm_calls, max_metered_usd=job.cap_usd)
        concurrency = settings.bridge_concurrency(job.cfg, job.models)
        bridge = LlmBridge(
            run_id=job.run_id,
            models=models,
            concurrency=concurrency,
            on_call=self._on_call,
            limits=limits,
        )
        async with bridge:
            self._bridge = bridge
            if self.stop_reason is not None:
                return
            await self._converse(bridge)

    def _request(self, bridge: LlmBridge) -> bytes:
        job, cfg = self.job, self.job.cfg
        body: dict[str, Any] = {
            "run_id": job.run_id,
            "ticker": job.ticker,
            "trade_date": job.trade_date,
            "analysts": job.analysts,
            "portfolio": job.portfolio,
            "config": {
                "backend_url": bridge.base_url,
                "deep_think_llm": llm_bridge.ALIAS_DEEP,
                "quick_think_llm": llm_bridge.ALIAS_QUICK,
                "llm_max_retries": 1,
                "max_debate_rounds": cfg.max_debate_rounds,
                "max_risk_rounds": cfg.max_risk_rounds,
                "max_tool_rounds": cfg.max_tool_rounds,
                "output_language": cfg.output_language,
            },
        }
        return json.dumps(body, allow_nan=False).encode()

    async def _converse(self, bridge: LlmBridge) -> None:
        env = worker_env(bridge.token, self.job.cfg.sec_edgar_user_agent, self.job.run_id)
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=install.scratch(self.job.run_id).results,
            env=env,
            limit=_MAX_LINE_BYTES,
            **process_tree.spawn_kwargs(),
        )
        self._proc = proc
        drain = asyncio.create_task(self._drain_stderr(proc))
        try:
            await asyncio.wait_for(self._talk(proc, self._request(bridge)), RUN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.stop(STOP_TIMEOUT)
        finally:
            self._kill()
            await proc.wait()
            await asyncio.gather(drain, return_exceptions=True)

    async def _talk(self, proc: asyncio.subprocess.Process, request: bytes) -> None:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(request)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the worker died before reading; its exit explains it
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:
                self._worker_error = "The worker sent a line that was too large."
                self._kill()
                return
            if not line:
                break
            self._handle(line)
        await proc.wait()

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(4096):
            self._stderr = (self._stderr + chunk.decode("utf-8", errors="replace"))[
                -_STDERR_TAIL_CHARS:
            ]

    # ── the worker's lines ───────────────────────────────────────────────────────────────

    def _handle(self, raw: bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "progress":
            self._progress(message)
        elif kind == "llm_call":
            self._attribute(message)
        elif kind == "result" and isinstance(message.get("final_state"), dict):
            self._result = message
        elif kind == "error":
            self._worker_error = str(message.get("message") or "The worker failed.")

    def _progress(self, message: dict) -> None:
        """A stage change, only for a node TradingAgents is known to have and only when it
        differs from the last one written: the worker is untrusted, and each write is a row
        update and a refresh of every open screen."""
        stage, status = message.get("stage"), message.get("status")
        if (
            stage not in ROLE_BY_NODE
            or (stage, status) == self._last_progress
            or self._progress_writes >= _MAX_PROGRESS_WRITES
        ):
            return
        self._last_progress = (stage, status)
        self._progress_writes += 1
        try:
            runs.apply_run_change("progress", run_id=self.job.run_id, stage=stage, status=status)
        except OpenNetWorthError:
            log.debug("trading_agents_progress_dropped run=%s", self.job.run_id)

    def _attribute(self, message: dict) -> None:
        """Say which role a call the bridge already served belongs to. The bridge reports every
        call before it answers, so an id that is not waiting here was never issued: ignored."""
        call_id = message.get("call_id")
        record = self._pending.pop(call_id, None) if isinstance(call_id, str) else None
        if record is not None:
            self._charge(record, role_for(message.get("node")))

    # ── cost, in-process from the bridge ─────────────────────────────────────────────────

    def _on_call(self, record: llm_bridge.CallRecord) -> None:
        self._metered += record.metered_usd
        if self._metered > self.job.cap_usd:
            self.stop(STOP_BUDGET)
        self._pending[record.call_id] = record

    def _charge(self, record: llm_bridge.CallRecord, role: str) -> None:
        plan = record.cost_basis == llm_bridge.PLAN_INCLUDED
        runs.apply_run_change(
            "usage",
            run_id=self.job.run_id,
            role=role,
            model=record.model_ref,
            llm_calls=1,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_tokens=record.cache_read_tokens,
            cache_creation_tokens=record.cache_creation_tokens,
            cost_usd=record.metered_usd,
            plan_included_calls=int(plan),
            unpriced_calls=int(not plan and record.cost_usd is None),
        )

    def _flush_unattributed(self) -> None:
        """Calls whose ``llm_call`` line never arrived are charged to ``other``."""
        for record in list(self._pending.values()):
            self._charge(record, runs.ROLE_OTHER)
        self._pending.clear()

    # ── the end ──────────────────────────────────────────────────────────────────────────

    def _stop_text(self, reason: str) -> str:
        return _STOP_TEXT[reason].format(cap=self.job.cap_usd, calls=self.job.cfg.max_llm_calls)

    def _end(self, status: str, message: str) -> None:
        """Record a terminal status other than ``done``: usage still pending first."""
        try:
            self._flush_unattributed()
            if status == runs.CANCELLED:
                runs.apply_run_change("cancel", run_id=self.job.run_id)
            else:
                runs.apply_run_change("fail", run_id=self.job.run_id, status=status, error=message)
        except OpenNetWorthError:
            log.warning("trading_agents_end_not_recorded run=%s status=%s", self.job.run_id, status)

    def _conclude(self) -> None:
        if self._result is not None and self.stop_reason != STOP_CANCEL:
            self._finish(self._result)
        elif self.stop_reason == STOP_CANCEL:
            self._end(runs.CANCELLED, "")
        elif self.stop_reason is not None:
            self._end(_STOP_STATUS[self.stop_reason], self._stop_text(self.stop_reason))
        else:
            self._end(*self._unfinished())

    def _unfinished(self) -> tuple[str, str]:
        """How a run ends when the worker stopped without a result: a cap the bridge enforced
        (the worker saw a refused call and failed), else the worker's own error."""
        bridge = self._bridge
        if bridge is not None and bridge.refused_calls:
            reason = STOP_BUDGET if self._metered >= self.job.cap_usd else STOP_CALLS
            return _STOP_STATUS[reason], self._stop_text(reason)
        if self._worker_error:
            return runs.FAILED, self._worker_error
        code = self._proc.returncode if self._proc is not None else None
        detail = " ".join(self._stderr.split())[-_ERROR_DETAIL_CHARS:]
        return runs.FAILED, f"The worker stopped without a result (exit {code}). {detail}".strip()

    def _finish(self, message: dict) -> None:
        state = message["final_state"]
        signal = message.get("signal")
        signal = signal if signal in runs.SIGNALS else "REVIEW"
        try:
            self._flush_unattributed()
            runs.apply_run_change(
                "finish",
                run_id=self.job.run_id,
                signal=signal,
                decision_text=state.get("final_trade_decision"),
                reports=_reports(state),
            )
            self._signal = signal
        except OpenNetWorthError as exc:
            self._end(runs.FAILED, f"The result could not be saved: {exc.message}")

    async def _notify_finished(self) -> None:
        """A finished run says so in the app (the in-app feed only: a research rating is not pushed
        to a phone). Best effort — a failed notice never changes the run."""
        if self._signal is None:
            return
        from backend.services.notify import notify

        try:
            await notify(
                f"TradingAgents finished {self.job.ticker}: {self._signal}",
                channels=["in_app"],
                title="TradingAgents",
            )
        except Exception:  # noqa: BLE001
            log.warning("trading_agents_notify_failed run=%s", self.job.run_id)


def worker_command() -> list[str]:
    return [str(install.venv_python()), "-I", str(install.worker_script())]
