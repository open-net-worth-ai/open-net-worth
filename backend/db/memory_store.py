"""
MemoryStore — Lucius's cross-session memory.

A flat list of memory items. Each item is a distilled fact about the user (a
preference, decision, intention, or piece of context) that should persist across
conversations. See docs/rfc/lucius-memory.md.

PLAN 4D TASK 2: DB-backed (table ``memory``) via ``PayloadTableStore``. **``user_id``-scoped
(spec:276), NOT ``book_id``** — a memory fact is a per-USER relationship with Lucius, not a
per-book ledger fact. Single-user for now (no ``user_id`` column yet); family mode adds one in
a LATER migration, never ``book_id``.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import memory

TIERS = ("durable", "time_bound")
SOURCES = ("implicit", "explicit")


def is_expired(fact: dict, today: str) -> bool:
    """True when the fact's ``expires_at`` is before ``today``.

    A fact written under the section model carries no ``tier`` and always means its expiry; a
    legacy one expires only when it was ``time_bound`` (a ``durable`` fact with a stray date
    never did).
    """
    exp = fact.get("expires_at")
    return bool(exp) and exp < today and fact.get("tier", "time_bound") == "time_bound"


class MemoryStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(memory, id_field="id", engine=engine)

    def all_facts(self) -> list[dict]:
        """All items, newest-updated first."""
        return sorted(
            self.all(),
            key=lambda f: f.get("updated_at", ""),
            reverse=True,
        )

    def active_facts(self, today: str | None = None) -> list[dict]:
        """
        All items except those whose expiry has passed (see ``is_expired``).
        This is what the system prompt injects.
        """
        today = today or user_today().isoformat()
        out = []
        for f in self.all_facts():
            if is_expired(f, today):
                continue
            out.append(f)
        return out

    def remove_by_id(self, id_val: str) -> bool:
        return self.delete(id_val)
