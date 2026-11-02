"""Which of the book's own categories (or envelopes) a YNAB category goes with, and what the book
looks like to decide it. Shared by the import's review and by the two-way sync, which meets a
category that is new in YNAB with the same rules (ADR
``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``, amendment 2026-11-03).

Names are compared with emoji and symbols dropped, case folded and spaces collapsed
(``ynab_pairs.norm``), in this order — the first step that finds anything decides:

  1. one of the book's categories, not yet paired with a YNAB category: pair with it. Its envelope
     decides the budget — alone in it, the envelope's Assigned syncs; shared, only the labels sync;
     in no envelope, the category is not budgeted here;
  2. an envelope of that name: an empty one gets the category (it becomes the envelope's only
     one); one that holds categories is only OFFERED, never taken on its own;
  3. nothing: a new category with an envelope of its own.

Two candidates that read the same are never guessed between (``ambiguous``), and nothing existing
is renamed or moved to make a match."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning
from backend.services.ynab_pairs import norm


def cat_ref(category_id: str) -> str:
    return f"cat:{category_id}"


def env_ref(envelope_id: str) -> str:
    return f"env:{envelope_id}"


@dataclass
class Match:
    kind: str  # category | envelope | offer | ambiguous | new
    category: dict | None = None
    envelope: dict | None = None
    options: list[dict] = field(default_factory=list)  # ambiguous: [{"ref", "name"}]


class Book:
    """The categories, envelopes and envelope groups a YNAB category can be matched with."""

    def __init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        rows = [c for c in categories_service.all_category_rows() if c["id"] != "split"]
        self.rows = {c["id"]: c for c in rows}
        self.matchable = [c for c in rows if c.get("kind", "expense") == "expense"]
        self.labels = {c["label"].strip().lower() for c in rows}
        live = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
        self.live = {e["id"]: e for e in live}
        bot = envelopes_service.bot_managed_envelope_ids()
        self.envelopes = [e for e in live if e["id"] not in bot]
        self.names = {e["name"].strip().lower() for e in live}
        self.env_of = {cid: e for e in live for cid in e.get("category_ids") or []}
        self.groups = [g for g in planning.list_groups() if not g.get("synced")]

    def solo_envelope(self, category_id: str) -> dict | None:
        """The envelope that holds ONLY this category: its Assigned is the category's budget."""
        env = self.env_of.get(category_id)
        return env if env and list(env.get("category_ids") or []) == [category_id] else None

    def free_label(self, label: str, *, envelopes: bool = True) -> str:
        """``label``, or ``label (YNAB)`` / ``label (YNAB 2)`` … when a category (or, with
        ``envelopes``, an envelope) already carries it."""
        taken = self.labels | (self.names if envelopes else set())
        out, n = label, 1
        while out.strip().lower() in taken:
            n += 1
            out = f"{label} (YNAB)" if n == 2 else f"{label} (YNAB {n - 1})"
        return out

    def free_envelope_name(self, name: str) -> str:
        out, n = name, 1
        while out.strip().lower() in self.names:
            n += 1
            out = f"{name} (YNAB)" if n == 2 else f"{name} (YNAB {n - 1})"
        return out


def propose(name: str, book: Book, taken_cats: set[str], taken_envs: set[str]) -> Match:
    key = norm(name)
    if not key:
        return Match("new")
    cats = [c for c in book.matchable if norm(c["label"]) == key and c["id"] not in taken_cats]
    if len(cats) == 1:
        return Match("category", category=cats[0])
    if cats:
        return Match(
            "ambiguous", options=[{"ref": cat_ref(c["id"]), "name": c["label"]} for c in cats]
        )
    envs = [e for e in book.envelopes if norm(e["name"]) == key and e["id"] not in taken_envs]
    if len(envs) == 1:
        return Match("offer" if envs[0].get("category_ids") else "envelope", envelope=envs[0])
    if envs:
        return Match(
            "ambiguous", options=[{"ref": env_ref(e["id"]), "name": e["name"]} for e in envs]
        )
    return Match("new")
