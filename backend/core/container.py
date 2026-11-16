"""Whether this process runs inside a container (Docker, Podman).

A container changes what a user on the host has to type: a command that exists in here does
not exist on their computer, and a path in here means nothing out there.
"""

from __future__ import annotations

from pathlib import Path

_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def in_container() -> bool:
    return any(Path(marker).exists() for marker in _CONTAINER_MARKERS)
