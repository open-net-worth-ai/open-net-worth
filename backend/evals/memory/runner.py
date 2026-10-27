"""Runs the cases against the real memory passes in the scratch book.

``bootstrap`` has already pointed every data folder at a throwaway root; this module migrates it
once, keeps a pristine copy of that database, and restores the copy before each case so no case
can see another's rows. All model calls go through ``memory._provider()`` — the path the app uses —
wrapped only to count calls. ``--mock`` swaps the provider for canned replies and nothing else."""

from __future__ import annotations

import asyncio
import copy
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.evals.memory import bootstrap, mock_replies
from backend.evals.memory.scorer import CaseResult, score_case
from backend.evals.memory.spec import Case, Outcome

CASE_TIMEOUT_SECONDS = 900
MOCK_TURN_MODEL = "claude_cli:mock-model"


class HarnessError(Exception):
    """The run cannot happen (bad model string, no credentials, migration failed). Exit code 2."""


@dataclass
class Tally:
    calls: int = 0
    failed: int = 0
    last_error: str = ""
    by_source: dict = field(default_factory=dict)


class Recorder:
    """The memory provider, counting calls and failures; every other attribute passes through."""

    def __init__(self, inner, tally: Tally):
        self._inner, self._tally = inner, tally

    async def complete(self, prompt, **kwargs):
        self._tally.calls += 1
        source = str(kwargs.get("source") or "?")
        self._tally.by_source[source] = self._tally.by_source.get(source, 0) + 1
        try:
            return await self._inner.complete(prompt, **kwargs)
        except Exception as exc:
            self._tally.failed += 1
            self._tally.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            raise

    def __getattr__(self, name):
        return getattr(self._inner, name)


class MockModel:
    def __init__(self, persona: str, case_id):
        self._persona, self._case_id = persona, case_id

    async def complete(self, prompt, **kwargs):
        return mock_replies.reply(self._persona, self._case_id(), str(kwargs.get("source")), prompt)


class ScriptedChat:
    """Stands in for the chat provider: one scripted round per ``stream_chat`` call."""

    def __init__(self, persona: str, case_id: str):
        self._persona, self._case_id, self._rounds, self._calls = persona, case_id, None, 0

    async def stream_chat(self, *args, **kwargs):
        import json

        if self._rounds is None:
            context = str(kwargs.get("system") or "") + json.dumps(kwargs.get("messages") or [])
            self._rounds = mock_replies.chat_script(self._persona, self._case_id, context)
        script = self._rounds[min(self._calls, len(self._rounds) - 1)]
        self._calls += 1
        for event in script:
            yield event


@dataclass
class Config:
    model: str = ""
    mock: bool = False
    persona: str = mock_replies.GOOD


def parse_model(spec: str) -> tuple[str, str]:
    """``provider_key:model_id`` -> (key, id), checked against the catalog, the installed CLI and
    the credentials the transport needs. Raises HarnessError with a sentence that says what to do."""
    from backend.db.model_config_store import cli_is_installed, provider_entry

    key, _, model_id = spec.partition(":")
    entry = provider_entry(key)
    if not model_id or entry is None:
        raise HarnessError(
            f"--model must be '<provider_key>:<model_id>' (e.g. claude_cli:sonnet); got {spec!r}"
        )
    if entry.get("cli_binary"):
        if not cli_is_installed(key):
            raise HarnessError(f"the {entry['cli_binary']} CLI is not installed or not on PATH")
    elif entry.get("needs_api_key") and not entry.get("optional_api_key") and not api_key_for(key):
        raise HarnessError(
            f"no credentials for {key}: set {key.upper()}_API_KEY in the environment "
            "(the scratch book starts empty; it never reads your stored keys)"
        )
    return key, model_id


def api_key_for(provider_key: str) -> str:
    return os.environ.get(f"{provider_key.upper()}_API_KEY", "").strip()


class Harness:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.template = bootstrap.EVAL_ROOT / "template.db"
        self.tally = Tally()
        self.current_case = ""
        self.cost = 0.0
        self.totals = Tally()
        self._restore: list = []

    # ── lifecycle ────────────────────────────────────────────────────────────

    def prepare(self) -> None:
        from backend.core import paths
        from backend.db.engine import dispose_engine
        from backend.db.migrate import ensure_migrated

        bootstrap.assert_isolated()
        if not self.template.exists():
            try:
                ensure_migrated()
            except Exception as exc:
                raise HarnessError(f"the scratch database could not be migrated: {exc}") from exc
            dispose_engine()
            shutil.copy2(paths.DB_FILE, self.template)
        self._install_providers()

    async def shutdown(self) -> None:
        """Stop the CLI children a live run started (a persistent Codex app-server)."""
        if self.cfg.mock:
            return
        try:
            from backend.ai.models import codex_app_server

            for client in list(codex_app_server._clients.values()):
                await client.aclose()
            codex_app_server._clients.clear()
        except Exception:
            pass

    def close(self) -> None:
        for undo in reversed(self._restore):
            undo()
        self._restore.clear()

    def _install_providers(self) -> None:
        import backend.ai.tools  # noqa: F401  (fills the registry a turn reads)
        from backend.ai import assistant, memory

        real = memory._provider
        mock = MockModel(self.cfg.persona, lambda: self.current_case)
        memory._provider = lambda: Recorder(mock if self.cfg.mock else real(), self.tally)
        self._restore.append(lambda: setattr(memory, "_provider", real))
        if self.cfg.mock:
            original = assistant.get_provider
            assistant.get_provider = lambda *a, **k: ScriptedChat(
                self.cfg.persona, self.current_case
            )
            self._restore.append(lambda: setattr(assistant, "get_provider", original))

    # ── the scratch book ─────────────────────────────────────────────────────

    def _reset_book(self) -> None:
        from backend.ai import assistant, memory_sort
        from backend.core import paths
        from backend.db.engine import dispose_engine

        dispose_engine()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{paths.DB_FILE}{suffix}").unlink(missing_ok=True)
        shutil.copy2(self.template, paths.DB_FILE)
        assistant.SESSIONS.clear()
        assistant._SESSION_LOCKS.clear()
        memory_sort._retry_after = 0.0

    def _configure_model(self) -> None:
        if self.cfg.mock:
            return
        from backend.db.model_config_store import ModelConfigStore

        key = self.cfg.model.partition(":")[0]
        store = ModelConfigStore()
        if api_key_for(key):
            store.update_provider(key, {"api_key": api_key_for(key)})
        store.set_task_models({"memory": self.cfg.model})

    def seed(self, case: Case) -> None:
        from backend.db import MemoryStore
        from backend.db.memory_settings_store import MemorySettingsStore
        from backend.services import accounts
        from backend.services import profile as profile_service

        self._reset_book()
        self._configure_model()
        MemorySettingsStore().update({"enabled": True, "sensitive_topics": case.sensitive_topics})
        stored = {**profile_service.read_profile_raw(), "base_currency": "SGD"}
        if case.notes:
            stored["notes"] = case.notes
        profile_service.write_profile(stored)
        if case.facts:
            MemoryStore().bulk_insert(copy.deepcopy(list(case.facts)))
        if case.forgotten:
            from backend.ai.memory_structure import fingerprint
            from backend.db.memory_forgotten_store import MemoryForgottenStore

            MemoryForgottenStore().record({fingerprint(t) for t in case.forgotten})
        for name in case.accounts:
            accounts.create_account(
                {"name": name, "type": "cash", "currency": "SGD", "initial_balance": 100}
            )

    async def preflight(self) -> None:
        """One tiny call through the real path: a model that cannot answer fails the run here
        (exit 2), not as thirty failed cases."""
        if self.cfg.mock:
            return
        from backend.ai import memory

        self._reset_book()
        self._configure_model()
        try:
            await memory._provider().complete(
                "Reply with the single word OK.",
                model=memory._memory_model(),
                max_tokens=32,
                source="memory_eval_preflight",
                effort="none",
            )
        except Exception as exc:
            raise HarnessError(
                f"the model could not be reached: {type(exc).__name__}: {exc}"
            ) from exc
        self.tally = Tally()

    # ── one case ─────────────────────────────────────────────────────────────

    async def run_case(self, case: Case) -> CaseResult:
        from backend.core.user_time import user_today
        from backend.db import MemoryStore
        from backend.services import profile as profile_service

        started = time.monotonic()
        self.current_case = case.id
        self.tally = Tally()
        self.seed(case)
        out = Outcome(
            case=case,
            before={f["id"]: f for f in MemoryStore().all_facts()},
            after=[],
            profile_before=copy.deepcopy(profile_service.read_profile_raw()),
            profile={},
            today=user_today().isoformat(),
        )
        try:
            await asyncio.wait_for(self._execute(case, out), timeout=CASE_TIMEOUT_SECONDS)
        except Exception as exc:
            out.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        out.after = MemoryStore().all_facts()
        out.profile = copy.deepcopy(profile_service.read_profile_raw())
        self._bank_cost()
        results = score_case(case, out)
        self.totals.calls += self.tally.calls
        self.totals.failed += self.tally.failed
        self.totals.last_error = self.tally.last_error or self.totals.last_error
        return CaseResult(
            case.id,
            case.stage,
            case.description,
            results,
            out.error,
            time.monotonic() - started,
            {
                "model_calls": self.tally.calls,
                "failed_calls": self.tally.failed,
                **self.tally.by_source,
            },
        )

    async def _execute(self, case: Case, out: Outcome) -> None:
        from backend.ai import memory, memory_import, memory_sort, memory_tidy
        from backend.ai.memory_import_apply import apply_import

        if case.stage == "extract":
            history = [{"role": role, "content": text} for role, text in case.turns]
            await memory.extract_and_merge(history)
        elif case.stage == "sort":
            out.run = await memory_sort.sort_remaining()
        elif case.stage == "import":
            out.plan = await memory_import.plan_import(case.paste)
            out.applied = apply_import(out.plan)
        elif case.stage == "tidy":
            out.run = await memory_tidy.tidy(force=True)
        else:
            out.events = await self._turn(case)

    async def _turn(self, case: Case) -> list[dict]:
        """A real chat turn: the app's system prompt, the real tools, the chosen model."""
        from backend.ai import assistant
        from backend.ai.prompts import build_system_prompt

        model = MOCK_TURN_MODEL if self.cfg.mock else self.cfg.model
        session = f"memory-eval-{case.id}"
        events: list[dict] = []
        async for ev in assistant.run(
            session,
            [{"role": "user", "content": case.question}],
            build_system_prompt(),
            model=model,
        ):
            if ev.get("type") in ("text_delta", "tool_use_start"):
                events.append(
                    {"type": ev["type"], "text": ev.get("text", ""), "name": ev.get("name")}
                )
        return events

    def _bank_cost(self) -> None:
        try:
            from backend.db.cost_log_store import CostLogStore

            self.cost += sum(
                float(r.get("cost_usd") or 0)
                for r in CostLogStore().all()
                if str(r.get("source", "")).startswith("memory_")
            )
        except Exception:
            pass

    # ── the whole run ────────────────────────────────────────────────────────

    async def run(self, cases: list[Case], repeats: int, on_case=None) -> list[list[CaseResult]]:
        runs: list[list[CaseResult]] = []
        streak = 0
        for n in range(repeats):
            this = []
            for case in cases:
                res = await self.run_case(case)
                this.append(res)
                streak = (
                    streak + 1 if self.tally.calls and self.tally.failed == self.tally.calls else 0
                )
                if on_case:
                    on_case(n + 1, res)
                if streak >= 3:
                    raise HarnessError(f"the model failed every call: {self.totals.last_error}")
            runs.append(this)
        if self.totals.calls and self.totals.failed == self.totals.calls:
            raise HarnessError(f"the model failed every call: {self.totals.last_error}")
        return runs
