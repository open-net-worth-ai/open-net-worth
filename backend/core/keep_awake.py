"""Hold off idle system sleep while this process runs — the seam for each OS's way of asking.

Only IDLE sleep: the display may still turn off, and a closed lid or a chosen Sleep still
sleeps. Every hold ends with the process, so a crash never leaves the machine awake.

- macOS: ``caffeinate -i -w <pid>``, which also exits by itself when this process does.
- Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)``. The state belongs
  to the calling THREAD, so one dedicated thread sets it, waits, and clears it.
- Anything else (Linux, a container): unsupported. Desktop environments honour different
  inhibitor APIs, and a container cannot affect its host's power at all.

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Optional

from backend.core.process_tree import spawn_kwargs

_log = logging.getLogger(__name__)

_CAFFEINATE = "/usr/bin/caffeinate"
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def supported() -> bool:
    if sys.platform == "darwin":
        return os.path.exists(_CAFFEINATE)
    return sys.platform == "win32"


class _MacHold:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            [_CAFFEINATE, "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **spawn_kwargs(),
        )

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class _WindowsHold:
    def __init__(self) -> None:
        self._release: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._release = threading.Event()
        self._thread = threading.Thread(
            target=self._hold, args=(self._release,), name="keep-awake", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _hold(release: threading.Event) -> None:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED):
            _log.warning("keep awake: Windows refused the execution-state request")
            return
        try:
            release.wait()
        finally:
            kernel32.SetThreadExecutionState(_ES_CONTINUOUS)

    def stop(self) -> None:
        release, thread = self._release, self._thread
        self._release = self._thread = None
        if release is not None:
            release.set()
        if thread is not None:
            thread.join(timeout=5)


class SleepHold:
    """Idempotent: ``hold(True)`` twice keeps one hold, ``hold(False)`` with none is a no-op."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._impl: Optional[object] = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def hold(self, enabled: bool) -> bool:
        """Start or stop the hold; returns whether one is now active. Never raises: failing to
        keep the machine awake must not fail the request or the boot that asked."""
        with self._lock:
            if not enabled or not supported():
                self._stop()
                return False
            try:
                if self._impl is None:
                    self._impl = _MacHold() if sys.platform == "darwin" else _WindowsHold()
                self._impl.start()  # type: ignore[attr-defined]
                self._active = True
            except OSError:
                _log.warning("keep awake: could not start the sleep hold", exc_info=True)
                self._active = False
            return self._active

    def _stop(self) -> None:
        if self._impl is not None:
            try:
                self._impl.stop()  # type: ignore[attr-defined]
            except OSError:
                _log.warning("keep awake: could not stop the sleep hold", exc_info=True)
        self._active = False


SLEEP_HOLD = SleepHold()
