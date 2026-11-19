"""The analysis sweep's store calls run off the event loop.

The sweep runs on the server's shared event loop. A synchronous PendingAnalysisStore call there
waits for SQLite's writer (up to busy_timeout) ON the loop thread, freezing every request: the
"loop thread BLOCKED" reports of 2026-11-19 named claim / mark_failed / mark_done in this sweep.
"""

from __future__ import annotations

import asyncio
import threading

from tests.test_analyst_sweep import _seed_pending_row, _seed_signal, _stub_analyst


def _record_store_threads(monkeypatch, names):
    """Wrap PendingAnalysisStore methods to record the thread each call ran on."""
    import backend.db.pending_analysis_store as pas_mod

    calls: list[tuple[str, int]] = []
    for name in names:
        original = getattr(pas_mod.PendingAnalysisStore, name)

        def _wrapped(self, *args, _orig=original, _name=name, **kwargs):
            calls.append((_name, threading.get_ident()))
            return _orig(self, *args, **kwargs)

        monkeypatch.setattr(pas_mod.PendingAnalysisStore, name, _wrapped)
    return calls


def _run_sweep_on_loop(analyst_mod) -> tuple[int, int]:
    """Run one sweep; return (analyzed count, the event loop thread's ident)."""

    async def _go():
        return await analyst_mod.run_analysis_sweep(), threading.get_ident()

    return asyncio.run(_go())


def _calls_on_loop(calls, loop_thread):
    return [name for name, thread in calls if thread == loop_thread]


def test_sweep_failed_row_writes_stay_off_the_loop(tmp_path, monkeypatch, db_isolated):
    import backend.monitoring.analyst as analyst_mod

    _stub_analyst(monkeypatch, tmp_path, db_isolated, llm_response=RuntimeError("refused"))
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)
    calls = _record_store_threads(monkeypatch, ["claim", "mark_failed"])

    count, loop_thread = _run_sweep_on_loop(analyst_mod)

    assert count == 0
    assert {name for name, _ in calls} == {"claim", "mark_failed"}
    assert _calls_on_loop(calls, loop_thread) == []


def test_sweep_done_row_writes_stay_off_the_loop(tmp_path, monkeypatch, db_isolated):
    import backend.monitoring.analyst as analyst_mod

    _stub_analyst(monkeypatch, tmp_path, db_isolated)
    _seed_signal(tmp_path, db_isolated)
    _seed_pending_row(tmp_path, db_isolated)
    calls = _record_store_threads(monkeypatch, ["claim", "mark_done"])

    count, loop_thread = _run_sweep_on_loop(analyst_mod)

    assert count == 1
    assert {name for name, _ in calls} == {"claim", "mark_done"}
    assert _calls_on_loop(calls, loop_thread) == []
