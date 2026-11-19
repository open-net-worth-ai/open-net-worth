"""Launch-time rewrite of a ``claude`` argv: the system prompt travels in a file.

Lucius's system prompt carries the whole tool manifest — hundreds of thousands of characters
for the full registry. Windows caps a command line at 32,767 characters, so passing it as
``--system-prompt <text>`` made ``CreateProcess`` fail with ``[WinError 206] The filename or
extension is too long`` (Linux caps one argument at 128 KiB too). ``--system-prompt-file``
lifts the limit on every platform.

Callers keep building the LOGICAL argv with ``--system-prompt <text>``: it is the live-session
pool's key, which must change when the prompt does. Only the argv handed to the OS is rewritten,
and the file lives in the process's own scratch directory, removed with it.
"""

from __future__ import annotations

import os

SYSTEM_PROMPT_FLAG = "--system-prompt"
SYSTEM_PROMPT_FILE_FLAG = "--system-prompt-file"
SYSTEM_PROMPT_FILENAME = "system-prompt.txt"


def with_system_prompt_file(argv: list[str], workdir: str) -> list[str]:
    """Return ``argv`` with ``--system-prompt <text>`` replaced by a file in ``workdir``."""
    try:
        i = argv.index(SYSTEM_PROMPT_FLAG)
    except ValueError:
        return list(argv)
    if i + 1 >= len(argv):
        return list(argv)
    path = os.path.join(workdir, SYSTEM_PROMPT_FILENAME)
    # Bytes, not text mode: Windows text mode would turn every "\n" into "\r\n".
    with open(path, "wb") as fh:
        fh.write(argv[i + 1].encode("utf-8"))
    return argv[:i] + [SYSTEM_PROMPT_FILE_FLAG, path] + argv[i + 2 :]
