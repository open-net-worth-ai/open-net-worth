"""The news screen's database work runs off the event loop.

`handle_arrivals` runs inside the RSS poll on the server's shared event loop. A synchronous
store call there waits for SQLite (a JSON-field scan of pending analyses, or the writer lock)
ON the loop thread, freezing every request — the "loop thread BLOCKED" reports of 2026-11-19
named `PendingAnalysisStore.recent_material_events` reached from `news_screen._screen`.
"""

from __future__ import annotations

import asyncio
import threading

from tests.test_news_screen import _TOPIC_ITEM, _evidence_row, _stub


def _record_threads(monkeypatch, cls, names, calls):
    for name in names:
        original = getattr(cls, name)

        def _wrapped(self, *args, _orig=original, _name=name, **kwargs):
            calls.append((_name, threading.get_ident()))
            return _orig(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, _wrapped)


def test_screening_a_material_headline_touches_the_database_off_the_loop(
    tmp_path, monkeypatch, db_isolated
):
    import backend.db.news_screen_state_store as nss_mod
    import backend.db.pending_analysis_store as pas_mod

    ns, pa_store, llm_calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)
    calls: list[tuple[str, int]] = []
    _record_threads(
        monkeypatch,
        pas_mod.PendingAnalysisStore,
        ["recent_material_events", "upsert_pending"],
        calls,
    )
    _record_threads(monkeypatch, nss_mod.NewsScreenStateStore, ["get", "save"], calls)

    async def _go():
        await ns.handle_arrivals([_evidence_row(["topic:wli-onwai", "scan:intel"])])
        return threading.get_ident()

    loop_thread = asyncio.run(_go())

    assert len(llm_calls) == 1
    assert len(pa_store.pending()) == 1  # the material headline was still queued
    names = {name for name, _ in calls}
    assert {"recent_material_events", "upsert_pending", "get", "save"} <= names
    on_loop = [name for name, thread in calls if thread == loop_thread]
    assert on_loop == [], f"database calls ran on the event loop thread: {on_loop}"
