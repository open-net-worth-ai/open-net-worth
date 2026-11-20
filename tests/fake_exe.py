"""A fake command-line program written in Python, runnable on POSIX and Windows alike.

Tests stand in for `claude`, `codex` and friends with a small Python script. On POSIX that is
an executable file with a `#!` line. Windows ignores `#!` and only runs real executables or
`.cmd`/`.bat` files, so there the script is written next to a `.cmd` launcher that runs it
with this interpreter, the same shape npm gives `claude.cmd` and `codex.cmd`.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def python_cli(path: Path, source: str) -> Path:
    """Write ``source`` as a program at ``path``; return the path to run it by.

    ``source`` is the Python body; a leading ``#!`` line is dropped on Windows and supplied on
    POSIX when missing. On Windows the return value is ``path`` with ``.cmd`` appended, so a
    test must use the returned path rather than ``path`` itself.
    """
    body = source.lstrip("\n")
    if body.startswith("#!"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    if not IS_WINDOWS:
        path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path
    script = path.with_name(path.name + ".py")
    script.write_text(body, encoding="utf-8")
    launcher = path.with_name(path.name + ".cmd")
    launcher.write_text(f'@"{sys.executable}" "%~dp0{script.name}" %*\r\n', encoding="ascii")
    return launcher
