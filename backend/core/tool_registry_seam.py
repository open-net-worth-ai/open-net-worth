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


def register_tool_asks_by_default_provider(fn: Optional[Callable[[str], bool]]) -> None:
    """Install the "does an undecided call to this tool ask first?" provider (``ai/tools``
    registers it at import).

    The answer follows what the tool DOES — a real change, or a send out of this install,
    asks; a read runs (``ai.tools._confirm_gate.is_gated_tool``, itself derived from each
    tool's own ``mutates=`` tag). It is a DEFAULT, not a permission: a level the owner stored
    always outranks it. It exists so ``services`` — the Connectors page's permission ladder,
    which must DISPLAY the level that will actually fire — can ask the question without
    importing ``ai``."""
    global _asks_by_default_provider
    _asks_by_default_provider = fn


def tool_asks_by_default(name: str) -> bool:
    """True when an undecided call to ``name`` asks the owner first.

    Fails CLOSED — an unregistered provider or a raising one answers ``True`` ("assume it
    asks"), so a display that cannot reach the registry shows the cautious level rather than
    promising a tool runs freely."""
    fn = _asks_by_default_provider
    if fn is None:
        return True
    try:
        return bool(fn(name))
    except Exception:  # pragma: no cover - defensive; fail closed
        return True


#: One native tool as the permission table sees it: ``(owner, tool, asks_by_default)``.
ToolCatalogEntry = tuple[str, str, bool]

_catalog_provider: Optional[Callable[[], list[ToolCatalogEntry]]] = None


def register_tool_catalog_provider(fn: Optional[Callable[[], list[ToolCatalogEntry]]]) -> None:
    """Install the provider of every native tool's permission facts (``ai/tools`` registers it at
    import): its permission owner, its name, and whether its seed level is Ask.

    It is what lets ``services.tool_permissions.sync_seeds`` give every tool a row at boot
    without importing ``ai`` — the same inversion as the two providers above. A tool that
    belongs to no row of its own (``web_search`` / ``fetch_page`` resolve to the active search
    provider's tool) or whose connector is withdrawn is left out by the provider."""
    global _catalog_provider
    _catalog_provider = fn


def tool_catalog() -> list[ToolCatalogEntry]:
    """Every native tool that has its own permission row, or ``[]`` if no provider is installed.

    ``[]`` means "cannot say", never "no tools exist" — a caller must not prune rows on it."""
    fn = _catalog_provider
    if fn is None:
        return []
    try:
        return list(fn())
    except Exception:  # pragma: no cover - defensive; a broken provider seeds nothing
        return []
