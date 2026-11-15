"""The server never runs on uvloop (issue #131).

On Linux uvloop starts every child process with a bare ``fork()`` and runs Python's after-fork
hooks in the copy of a busy, multi-threaded server. On a Docker host that child segfaulted in
libc's ``pthread_join`` before Claude Code ever ran — chat, the analysis sweep and asset briefs
all failed with "exited with status -11", every spawn until a restart. ``dmesg`` named the
crashing process ``python3``, not ``claude``. Every launch path pins the standard loop.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.core.config import SERVER_EVENT_LOOP

ROOT = Path(__file__).resolve().parents[1]


def test_the_server_loop_is_the_standard_one():
    assert SERVER_EVENT_LOOP == "asyncio"


def _uvicorn_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
        and node.func.attr in ("run", "Config")
    ]


def test_every_python_launch_path_passes_the_loop():
    # The desktop app is the one exception: it awaits server.serve() inside its own
    # asyncio.run(), so uvicorn never picks a loop there.
    exempt = {ROOT / "backend" / "cli" / "desktop.py"}
    found = []
    for path in (ROOT / "backend").rglob("*.py"):
        if path in exempt:
            continue
        for call in _uvicorn_calls(path):
            loops = [kw for kw in call.keywords if kw.arg == "loop"]
            found.append(path)
            assert loops, f"{path.relative_to(ROOT)}:{call.lineno} starts uvicorn without loop="
            assert ast.unparse(loops[0].value) == "SERVER_EVENT_LOOP", (
                f"{path.relative_to(ROOT)}:{call.lineno} must pass loop=SERVER_EVENT_LOOP"
            )
    assert {p.name for p in found} >= {"main.py", "serve.py"}


def test_the_desktop_app_runs_its_own_standard_loop():
    source = (ROOT / "backend" / "cli" / "desktop.py").read_text(encoding="utf-8")
    assert "asyncio.run(serve(" in source and "uvloop" not in source


def test_every_makefile_launch_passes_the_loop():
    lines = [
        line
        for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if re.search(r"-m uvicorn backend\.main:app", line)
    ]
    assert lines
    for line in lines:
        assert "--loop asyncio" in line, line.strip()
