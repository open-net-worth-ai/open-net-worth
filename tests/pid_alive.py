"""Whether a process id is still running, on POSIX and Windows alike.

``os.kill(pid, 0)`` is a liveness probe only on POSIX: on Windows any signal other than the
console events is ``TerminateProcess`` (so the "probe" kills what it asks about), and a pid that
is already gone raises ``[WinError 87]``. Windows asks for the exit code instead, which also
reads as "exited" for a dead process whose handle some parent still holds open.
"""

from __future__ import annotations

import os

IS_WINDOWS = os.name == "nt"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_is_alive(pid: int) -> bool:
    if IS_WINDOWS:
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, just not ours to signal
        return True
    return True
