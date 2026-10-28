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

    A filed fact (live, or carrying a section) always means its expiry, whatever ``tier`` a
    legacy row still carries — an Open item expires on its date. Only a legacy fact not yet
    filed keeps the old rule: it expires when it was ``time_bound`` (a ``durable`` fact with a
    stray date never did).
    """
    exp = fact.get("expires_at")
    if not exp or str(exp)[:10] >= today[:10]:
        return False
    if fact.get("state") == "live" or fact.get("section"):
        return True
    return fact.get("tier", "time_bound") == "time_bound"


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

    def remembered_facts(self, today: str | None = None) -> list[dict]:
        """Every fact that still counts — live and not yet sorted, not retired, not expired. A
        fact with no ``state`` predates the migration and is waiting to be sorted. This is what
        recall searches and what the prompt counts."""
        today = today or user_today().isoformat()
        return [
            f for f in self.all_facts() if f.get("state") != "retired" and not is_expired(f, today)
        ]

    def active_facts(self, today: str | None = None) -> list[dict]:
        """The live facts — the ones Lucius is shown in full. A fact with no ``state`` is not
        live: the migration stamps it ``unsorted`` until it is filed."""
        return [f for f in self.remembered_facts(today) if f.get("state") == "live"]

    def remove_by_id(self, id_val: str) -> bool:
        return self.delete(id_val)
