"""Where each YNAB category goes in the book, decided for the review and again for the import
(ADR ``2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines``, amendment
2026-11-03). Mirrors ``links`` (accounts): the person's choices arrive as ``category_links``,
YNAB category id -> ``"cat:<id>"`` (one of the book's categories), ``"env:<id>"`` (a new category
inside one of its envelopes) or ``""`` (a new category with an envelope of its own); a category
with no choice takes what ``category_match.propose`` finds.

A decision is a dict ``{kind, category, envelope, options, chosen, offer}``: ``kind`` is
``linked`` (an earlier import already paired it), ``category``, ``envelope``, ``new`` or
``ambiguous`` (the person has to pick — an import does not start until they have)."""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services.ynab_migration.category_match import (
    Book,
    Match,
    cat_ref,
    env_ref,
    propose,
)
from backend.services.ynab_pairs import norm


def _state(kind: str, *, category=None, envelope=None, options=None, chosen=False, offer=None):
    return {
        "kind": kind,
        "category": category,
        "envelope": envelope,
        "options": options or [],
        "chosen": chosen,
        "offer": offer,
    }


def _explicit(c: dict, ref: str, book: Book, taken_c: set[str], taken_e: set[str]) -> dict:
    if ref == "":
        return _state("new", chosen=True)
    kind, _, ident = ref.partition(":")
    name = c["name"]
    if kind == "cat":
        row = next((r for r in book.matchable if r["id"] == ident), None)
        if row is None:
            raise ValidationError(f"The category chosen for {name} is no longer in this book.")
        if ident in taken_c:
            raise ValidationError(f"{row['label']} is chosen for two YNAB categories.")
        taken_c.add(ident)
        return _state("category", category=row, chosen=True)
    env = next((e for e in book.envelopes if e["id"] == ident), None) if kind == "env" else None
    if env is None:
        raise ValidationError(f"The envelope chosen for {name} is no longer in this book.")
    if ident in taken_e:
        raise ValidationError(f"{env['name']} is chosen for two YNAB categories.")
    taken_e.add(ident)
    return _state("envelope", envelope=env, chosen=True)


def _from_match(m: Match, taken_c: set[str], taken_e: set[str]) -> dict:
    if m.kind == "category":
        taken_c.add(m.category["id"])
        return _state("category", category=m.category)
    if m.kind == "envelope":
        taken_e.add(m.envelope["id"])
        return _state("envelope", envelope=m.envelope)
    if m.kind == "offer":
        return _state("new", offer=m.envelope)
    if m.kind == "ambiguous":
        return _state("ambiguous", options=m.options)
    return _state("new")


def decide(
    plan: dict, links: dict[str, str] | None, book: Book, paired: dict[str, dict]
) -> dict[str, dict]:
    """YNAB category id -> its decision. Raises ``ValidationError`` for a choice that is not a
    category of this plan or not available any more."""
    links = links or {}
    known = {c["id"] for c in plan["categories"]}
    if stray := next((k for k in links if k not in known), None):
        raise ValidationError(f"The YNAB category {stray} is not in the plan; review it again.")
    taken_c: set[str] = set()
    taken_e: set[str] = set()
    out: dict[str, dict] = {}
    for c in plan["categories"]:
        pair = paired.get(c["id"])
        if pair and not pair.get("gone") and pair["category"] in book.rows:
            taken_c.add(pair["category"])
            out[c["id"]] = _state("linked", category=book.rows[pair["category"]])
    for c in plan["categories"]:
        if c["id"] not in out and c["id"] in links:
            out[c["id"]] = _explicit(c, links[c["id"]], book, taken_c, taken_e)
    for c in plan["categories"]:
        if c["id"] not in out:
            out[c["id"]] = _from_match(propose(c["name"], book, taken_c, taken_e), taken_c, taken_e)
    return out


def unresolved(plan: dict, decisions: dict[str, dict]) -> list[str]:
    """The names of YNAB categories the person still has to pick a destination for."""
    return [c["name"] for c in plan["categories"] if decisions[c["id"]]["kind"] == "ambiguous"]


def _budget_note(book: Book, category: dict) -> str:
    env = book.env_of.get(category["id"])
    if env is None:
        return "Not budgeted here"
    if list(env.get("category_ids") or []) == [category["id"]]:
        return f"Budget syncs with envelope {env['name']}"
    return f"Envelope {env['name']} holds other categories too — its Assigned won't sync"


def note(book: Book, d: dict, c: dict, assigned: bool) -> str:
    """The one line under a category in the review: what its budget does."""
    kind = d["kind"]
    if kind in ("linked", "category"):
        text = _budget_note(book, d["category"])
        if text == "Not budgeted here" and assigned:
            return "Gets an envelope here, since YNAB has money assigned to it"
        return text
    if kind == "envelope":
        env = d["envelope"]
        if env.get("category_ids"):
            return f"Envelope {env['name']} holds other categories too — its Assigned won't sync"
        return f"Budget syncs with envelope {env['name']}"
    if kind == "ambiguous":
        return "Matches more than one of your categories. Pick one."
    if d["offer"] is not None:
        return f"Your envelope {d['offer']['name']} holds other categories; pick it to add this one there."
    return "Budget syncs with a new envelope of its own"


def has_assigned(plan: dict, ynab_id: str) -> bool:
    return any(month.get(ynab_id) for month in plan["assigned"].values())


def choices(book: Book) -> dict:
    """What a category can be sent to, listed once for every row of the review."""
    return {
        "categories": [{"ref": cat_ref(c["id"]), "name": c["label"]} for c in book.matchable],
        "envelopes": [
            {"ref": env_ref(e["id"]), "name": e["name"], "holds": len(e.get("category_ids") or [])}
            for e in book.envelopes
        ],
    }


def rows(plan: dict, decisions: dict[str, dict], book: Book) -> dict[str, dict]:
    """YNAB category id -> the review's view of its decision."""
    out = {}
    for c in plan["categories"]:
        d = decisions[c["id"]]
        target = d["category"] or d["envelope"]
        ref = None if target is None else (cat_ref if d["category"] else env_ref)(target["id"])
        out[c["id"]] = {
            "link": None
            if target is None
            else {"ref": ref, "name": target.get("label") or target["name"], "kind": d["kind"]},
            "chosen": d["chosen"],
            "ask": d["kind"] == "ambiguous",
            "options": d["options"],
            "note": note(book, d, c, has_assigned(plan, c["id"])),
        }
    return out


def group_matches(plan: dict, book: Book, paired: dict[str, dict]) -> dict[str, dict | None]:
    """YNAB category group id -> the group here it is paired with, or the one of the same name an
    import would pair it with, else ``None`` (the import creates it when a category needs it)."""
    taken = {d["group"] for d in paired.values() if not d.get("gone")}
    by_id = {g["id"]: g for g in book.groups}
    out: dict[str, dict | None] = {}
    for c in plan["categories"]:
        gid = c["group_id"]
        if gid in out:
            continue
        doc = paired.get(gid)
        if doc and not doc.get("gone"):
            out[gid] = by_id.get(doc["group"])
            continue
        same = [
            g
            for g in book.groups
            if g["id"] not in taken and norm(g["name"]) == norm(c["group_name"])
        ]
        out[gid] = same[0] if len(same) == 1 else None
        if out[gid]:
            taken.add(out[gid]["id"])
    return out
