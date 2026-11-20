"""How a CLI child ended, in words an operator can act on — shared by the CLI transports.

A signal is the usual silent death: nothing reaches stderr, so without this a crash, a memory
kill and a missing file all read as the same "not running" (2026-11-11, a Docker Codex install;
2026-11-15, issue #131, a Docker Claude Code install).
"""

from __future__ import annotations

import signal
from typing import Optional

_SIGNAL_HINTS = {
    "SIGKILL": "usually the system ran out of memory, or a container limit stopped it",
    "SIGSEGV": "{label} crashed; a different {label} version may not",
    "SIGILL": "this {label} build may not support this processor",
}


# Windows' `signal` lacks most of these (no SIGKILL), yet a 137 or -9 still reaches us from a
# container or WSL child, so the Linux numbering names what the platform's enum cannot.
_LINUX_SIGNAL_NAMES = {7: "SIGBUS", 9: "SIGKILL", 11: "SIGSEGV", 15: "SIGTERM"}
# Past this a "code above 128" is a Windows NTSTATUS (0xC0000409 ...), not a shell-style signal.
_MAX_SIGNAL = 64


def describe_exit(returncode: Optional[int], label: str = "the CLI") -> str:
    """``exited with code 3`` / ``was stopped by SIGKILL (…)`` / ``was still running …``.

    A shell-style code above 128 is read as that signal too: the npm launcher passes it on.
    """
    if returncode is None:
        return "was still running but never answered"
    signum = -returncode if returncode < 0 else returncode - 128 if returncode > 128 else 0
    if not signum or signum > _MAX_SIGNAL:
        return f"exited with code {returncode}"
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = _LINUX_SIGNAL_NAMES.get(signum, f"signal {signum}")
    detail = [f"exit code {returncode}"] if returncode > 0 else []
    if name in _SIGNAL_HINTS:
        detail.append(_SIGNAL_HINTS[name].format(label=label))
    return f"was stopped by {name}" + (f" ({'; '.join(detail)})" if detail else "")
