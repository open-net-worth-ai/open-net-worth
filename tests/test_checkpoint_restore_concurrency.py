"""Regression tests for finding 5, docs/review/concurrent-conversations-security.md:

``ai.checkpoint.restore()``'s concurrency guard used to be per-SESSION
(``_session_turn_active(session_id)``), but the data it restores
(``_restore_dest`` + ``_atomic_write_bytes``) overwrites GLOBAL files
(transactions.json, accounts.json, ...) wholesale. Before concurrent conversations, at
most one interactive turn could ever be in flight, so per-session and global were
indistinguishable. Concurrent conversations broke that: conversation B could restore an
old checkpoint while conversation A was mid-turn writing, clobbering A's in-flight
writes with no checkpoint able to undo the resulting mixture.

These tests pin the corrected, GLOBAL guard and the two things it must not break:
  1. the model calling ``restore_checkpoint`` as a TOOL mid-its-own-turn (a real,
     intentional path — sequential, not concurrent, so it must not block itself), and
  2. ``_active_turns`` bookkeeping surviving an exception *and* an ``asyncio.CancelledError``
     (a ``BaseException``, so an ``except Exception`` clause alone would miss it) so a
     later restore is never refused forever.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import assistant
from backend.ai import checkpoint as ckpt


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point checkpoint storage at a tmp dir so these tests never touch the shared
    process-wide test DATA_DIR's checkpoint index (belt-and-braces on top of the
    conftest-level DATA_DIR redirect — see tests/conftest.py)."""
    data_dir = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ckpt, "DATA_DIR", data_dir)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)
    yield tmp_path


def _make_checkpoint(isolated_dir, session_id: str) -> str:
    """A completed turn that snapshots one file — a real, restorable checkpoint."""
    data_file = isolated_dir / "data" / f"{session_id}.json"
    data_file.write_text(json.dumps([{"id": "a1"}]))
    ctx, token = ckpt.begin_turn(session_id, anchor_history_len=0, message_preview="t")
    ckpt._snapshot_active(data_file)
    entry = ckpt.end_turn(ctx, token)
    assert entry is not None, "setup: turn should have produced a checkpoint"
    return entry["id"]


# ── The guard itself ─────────────────────────────────────────────────────────


def test_restore_refused_when_a_different_session_has_a_live_turn(isolated):
    """The exact scenario in finding 5: two conversations mid-turn. Restoring while
    inside one must be refused — the OTHER session's writes are to the same global
    files, so a per-session guard would silently let this clobber them."""
    cp_id = _make_checkpoint(isolated, "session-A")

    ctx_b, tok_b = ckpt.begin_turn("session-B", anchor_history_len=0, message_preview="writing")
    try:
        ctx_a, tok_a = ckpt.begin_turn("session-A", anchor_history_len=1, message_preview="undo")
        try:
            with pytest.raises(RuntimeError, match="Another conversation"):
                ckpt.restore(cp_id)
        finally:
            ckpt.end_turn(ctx_a, tok_a)
    finally:
        ckpt.end_turn(ctx_b, tok_b)


def test_restore_allowed_when_called_from_its_own_live_turn(isolated):
    """The model calling restore_checkpoint as a TOOL mid-its-own-turn must keep
    working — that write is sequential (the turn is paused on this very call, not
    running concurrently with it), so a session's own in-flight turn must not count
    against itself."""
    cp_id = _make_checkpoint(isolated, "session-A")  # an earlier, already-ended turn

    ctx, token = ckpt.begin_turn("session-A", anchor_history_len=1, message_preview="undo that")
    try:
        result = ckpt.restore(cp_id)
        assert result["checkpoint_id"] == cp_id
    finally:
        ckpt.end_turn(ctx, token)


def test_restore_allowed_when_no_turns_are_live(isolated):
    cp_id = _make_checkpoint(isolated, "session-A")
    result = ckpt.restore(cp_id)
    assert result["checkpoint_id"] == cp_id


# ── _active_turns bookkeeping survives exception / cancellation ─────────────
#
# These drive the REAL backend.ai.assistant.run()/._run_turn machinery (not a
# hand-rolled begin_turn/end_turn pair) so the assertion is about the actual wiring:
# _run_turn's `finally` — not its `except Exception` clause — is what calls
# _finalise_checkpoint() -> ckpt.end_turn(), and `finally` runs on ANY exception,
# including a CancelledError that `except Exception` would never see.


async def _consume(gen):
    async for _ in gen:
        pass


def test_active_turns_decrements_after_a_turn_raises(monkeypatch, isolated):
    """A turn that fails must not leave its session's counter stuck > 0 — that would
    refuse every future restore for that session forever."""
    sid = "test-ckpt-restore-exc-session"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    other_cp_id = _make_checkpoint(isolated, "other-session-exc")

    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "_save_session", lambda *a, **k: None)

    class _RaisingProvider:
        async def stream_chat(self, *a, **kw):
            raise RuntimeError("boom")
            yield  # pragma: no cover — unreachable; keeps this an async generator

    monkeypatch.setattr(assistant, "get_provider", lambda *a, **kw: _RaisingProvider())

    async def _go():
        with pytest.raises(RuntimeError, match="boom"):
            await _consume(
                assistant.run(
                    session_id=sid,
                    new_messages=[{"role": "user", "content": "hi"}],
                    system="s",
                )
            )

    asyncio.run(_go())

    assert ckpt._active_turns.get(sid, 0) == 0, "turn counter leaked after an exception"
    # The actual consequence a leak would cause: a later, unrelated restore refused forever.
    result = ckpt.restore(other_cp_id)
    assert result["checkpoint_id"] == other_cp_id


def test_active_turns_decrements_after_a_turn_is_cancelled(monkeypatch, isolated):
    """Same as above, but via a real asyncio.CancelledError (e.g. /stop, or a client
    disconnect) instead of a raised Exception — the regression most likely to be
    missed, per the review finding."""
    sid = "test-ckpt-restore-cancel-session"
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)

    other_cp_id = _make_checkpoint(isolated, "other-session-cancel")

    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "_save_session", lambda *a, **k: None)

    async def _go():
        # Constructed here, not in the outer sync test body: Python 3.9 requires a
        # running loop at construction time (mirrors test_p0_5_concurrency.py).
        started = asyncio.Event()
        parked = asyncio.Event()

        class _ParkingProvider:
            async def stream_chat(self, *a, **kw):
                started.set()
                yield {"type": "text_delta", "text": "..."}
                await parked.wait()  # never set — only a cancel ends this
                yield {"type": "done", "stop_reason": "end_turn"}  # pragma: no cover

        monkeypatch.setattr(assistant, "get_provider", lambda *a, **kw: _ParkingProvider())

        task = asyncio.create_task(
            _consume(
                assistant.run(
                    session_id=sid,
                    new_messages=[{"role": "user", "content": "hi"}],
                    system="s",
                )
            )
        )
        await started.wait()
        assert ckpt._active_turns.get(sid, 0) > 0, "setup: turn should be live before cancelling"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(_go())

    assert ckpt._active_turns.get(sid, 0) == 0, "turn counter leaked after CancelledError"
    result = ckpt.restore(other_cp_id)
    assert result["checkpoint_id"] == other_cp_id
