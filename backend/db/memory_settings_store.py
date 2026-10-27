"""
MemorySettingsStore — the memory settings of the Lucius memory subsystem.

One dict in the shared ``settings`` table, namespace "memory_settings", via
``NamespacedSettingsStore``: ``enabled`` ("Generate memory from chats", default on),
``search_chats`` (Lucius may search past conversations, default on) and ``sensitive_topics``
(health, religion, politics… may be remembered, default off). A missing row or key reads as
its default. Every setter is one database-serialized read-modify-write, so two toggles
changed together never drop each other's key.
"""

from __future__ import annotations

from backend.core.config import get_settings
from backend.db.payload_store import NamespacedSettingsStore


class MemorySettingsStore(NamespacedSettingsStore):
    def __init__(self, engine=None):
        super().__init__("memory_settings", engine=engine)

    # ── Public API ─────────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        """Return True (the safe default) when the namespace row is absent or key missing."""
        return bool(self.get().get("enabled", True))

    def search_chats(self) -> bool:
        return bool(self.get().get("search_chats", True))

    def sensitive_topics(self) -> bool:
        return bool(self.get().get("sensitive_topics", False))

    def settings(self) -> dict:
        doc = self.get()
        return {
            "enabled": bool(doc.get("enabled", True)),
            "search_chats": bool(doc.get("search_chats", True)),
            "sensitive_topics": bool(doc.get("sensitive_topics", False)),
        }

    def set_enabled(self, on: bool) -> dict:
        """Persist the flag and return the updated settings dict."""
        self._set("enabled", bool(on))
        return {"enabled": bool(on)}

    def set_search_chats(self, on: bool) -> None:
        self._set("search_chats", bool(on))

    def set_sensitive_topics(self, on: bool) -> None:
        self._set("sensitive_topics", bool(on))

    def _set(self, key: str, value: bool) -> None:
        with self._use_serialized_namespace_conn(None) as conn:
            doc = dict(self.get(conn=conn))
            doc[key] = value
            self.save(doc, conn=conn)


def memory_enabled_for(agent: dict | None) -> bool:
    """Return True iff both the global toggle AND (if agent given) per-agent flag are ON.

    Single AND-gate used by all memory paths across services and ai layers so the
    logic stays in one place at the db level where both layers can import it.
    """
    if not MemorySettingsStore().enabled():
        return False
    if agent is not None and not agent.get("memory_enabled", True):
        return False
    return True


def memory_writes_enabled() -> bool:
    """True when Lucius may ADD, forget or tidy facts — the toggle, and never on a public demo.

    A demo's memory is shared by every visitor, so a fact distilled from one visitor's chat would
    surface in the next visitor's, and extraction is a model call nobody is charged for. Facts
    already in the book (the seeded ones) are still READ — injection checks ``enabled()`` only."""
    return not get_settings().demo_mode and MemorySettingsStore().enabled()
