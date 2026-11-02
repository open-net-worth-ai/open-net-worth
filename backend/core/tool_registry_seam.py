"""Tool-name registry seam — the inversion that lets ``services`` check stored tool
names against the live AI tool registry without importing
``ai`` (which would trip import-linter Contract 3: services must not import api/ai).

This mirrors the existing checkpoint-hook inversion (``core.io.register_write_hook``):
``ai/tools/__init__`` registers its ``registered_tool_names`` provider here at
import time, and ``services.skills`` reads the names via ``tool_names()``. So the
edges are ``services → core`` and ``ai → core`` — both downward, no
``services → ai`` import. This module imports nothing internal.

If the provider is never registered (e.g. a unit test that imports ``services``
without ``ai``), ``tool_names()`` returns an empty set — callers must treat an
empty registry as "cannot validate" rather than "no tools exist". Tests stub the
provider with a fixed set.
"""

from __future__ import annotations

from typing import Callable, Optional

_provider: Optional[Callable[[], list[str]]] = None
_writes_provider: Optional[Callable[[str], bool]] = None
_asks_by_default_provider: Optional[Callable[[str], bool]] = None


def register_tool_name_provider(fn: Optional[Callable[[], list[str]]]) -> None:
    """Install (or clear, with ``None``) the registered-tool-names provider.

    Last registration wins. ``ai/tools/__init__`` calls this at import time with
    ``registry.registered_tool_names``.
    """
    global _provider
    _provider = fn


def tool_names() -> set[str]:
    """The set of currently-registered AI tool names, or ``set()`` if no provider
    is installed. Best-effort — a failing provider yields an empty set."""
    fn = _provider
    if fn is None:
        return set()
    try:
        return set(fn())
    except Exception:  # pragma: no cover - defensive; never block a skill read
        return set()


def register_tool_writes_provider(fn: Optional[Callable[[str], bool]]) -> None:
    """Install the "does this tool write?" provider (``ai/tools`` registers it at import).

    This is a FACTUAL classification derived from each tool's own ``mutates=(…)`` registry
    tag — a tool that declares no mutated data domain writes nothing persistent. It is NOT a
    permission tier and nothing consults it to refuse a call; it exists so ``services`` can
    answer "is this suggested playbook read-only?" without importing ``ai``."""
    global _writes_provider
    _writes_provider = fn


def tool_writes(name: str) -> bool:
    """True when ``name`` is a WRITE tool (declares a mutated domain).

    Fails CLOSED — an unregistered provider or a raising one answers ``True`` ("assume it
    writes"), so a caller filtering for read-only tools gets an empty result rather than
    silently treating every tool as safe."""
    fn = _writes_provider
    if fn is None:
        return True
    try:
        return bool(fn(name))
    except Exception:  # pragma: no cover - defensive; fail closed
        return True


def register_tool_asks_by_default_provider(fn: Optional[Callable[[str], bool]]) -> None:
    """Install the "does an undecided call to this tool ask first?" provider (``ai/tools``
    registers it at import). Read only under ``permissions_v2``.

    The answer follows what the tool DOES — a real change, or a send out of this install, asks; a
    read runs (``ai.tools._confirm_gate_v2.is_gated_tool``). It is a SEED, not a permission: a
    level the owner stored always outranks it. It exists so ``services`` — the permission table's
    seed rule and the Connectors page's ladder, which must DISPLAY the level that will actually
    fire — can ask the question without importing ``ai``."""
    global _asks_by_default_provider
    _asks_by_default_provider = fn


def tool_asks_by_default(name: str) -> bool:
    """True when an undecided call to ``name`` asks the owner first.

    Fails CLOSED — an unregistered provider or a raising one answers ``True`` ("asks"), so a
    display that cannot reach the registry shows the cautious level rather than promising a tool
    runs freely."""
    fn = _asks_by_default_provider
    if fn is None:
        return True
    try:
        return bool(fn(name))
    except Exception:  # pragma: no cover - defensive; fail closed
        return True
