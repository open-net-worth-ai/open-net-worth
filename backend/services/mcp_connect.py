"""How an AI app on this machine should launch the stdio MCP bridge (Settings → MCP).

Only what the server can KNOW, never a guess: in the desktop app the interpreter running this
process is the bundled Python, which has the package installed — so ``<it> -m backend.cli mcp``
works from anywhere, and the bundled ``onw`` script does not (its shebang names the build
folder). Any other install has ``onw`` beside its interpreter when it is a ``uv tool`` or a
checkout's venv; an absolute path matters because GUI apps rarely inherit the shell's PATH.
In a container no path in here exists on the user's computer, so it reports ``container`` and
names no command: the AI app runs ``onw`` installed on ITS computer, pointed at this address.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from backend.core.config import get_settings, instance_root
from backend.core.container import in_container
from backend.core.shipped_assets import REPO_ROOT


def _onw_beside_interpreter() -> Optional[Path]:
    candidate = Path(sys.executable).parent / ("onw.exe" if os.name == "nt" else "onw")
    return candidate if candidate.is_file() else None


def _source_checkout() -> Optional[str]:
    if (REPO_ROOT / "pyproject.toml").is_file() and (REPO_ROOT / "backend" / "mcp").is_dir():
        return str(REPO_ROOT)
    return None


def connect_info() -> dict:
    if get_settings().desktop_mode:
        return {
            "desktop": True,
            "command": sys.executable,
            "args": ["-m", "backend.cli", "mcp"],
            "command_found": True,
            # The desktop port changes per launch; the bridge reads it from this folder's lock.
            "root": str(instance_root()),
            "source_root": None,
            "container": False,
        }
    if in_container():
        return {
            "desktop": False,
            "command": "onw",
            "args": ["mcp"],
            "command_found": False,
            "root": None,
            "source_root": None,
            "container": True,
        }
    onw = _onw_beside_interpreter()
    return {
        "desktop": False,
        "command": str(onw) if onw else "onw",
        "args": ["mcp"],
        "command_found": onw is not None,
        "root": None,
        "source_root": _source_checkout(),
        "container": False,
    }
