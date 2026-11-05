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
from backend.services.budget_app import current
from backend.services.ynab_migration.category_match import (
    CLOSE,
    Book,
    Match,
    cat_ref,
    closeness,
    env_ref,
    propose,
    spelled_alike,
)
from backend.services.ynab_pairs import is_parking_name, norm


def _state(
    kind: str,
    *,
    category=None,
    envelope=None,
    options=None,
    chosen=False,
    offer=None,
    close=False,
    among=False,
):
    return {
        "kind": kind,
        "category": category,
        "envelope": envelope,
        "options": options or [],
        "chosen": chosen,
        "offer": offer,
        "close": close,
        # Picked from several of the book's that read the same, as the one spelled most alike.
        "among": among,
    }


def is_income(c: dict) -> bool:
    return c.get("kind") == "income"


def _kinds_differ(name: str, label: str) -> ValidationError:
    return ValidationError(f"{name} can't go with {label}: income pairs only with income.")


def _explicit(c: dict, ref: str, book: Book, taken_c: set[str], taken_e: set[str]) -> dict:
    if ref == "":
        return _state("new", chosen=True)
    kind, _, ident = ref.partition(":")
    name, income = c["name"], is_income(c)
    if kind == "cat":
        pool = book.income if income else book.matchable
        row = next((r for r in pool if r["id"] == ident), None)
        if row is None and current().caps.income_categories and ident in book.rows:
            raise _kinds_differ(name, book.rows[ident]["label"])
        if row is None:
            raise ValidationError(f"The category chosen for {name} is no longer in this book.")
        if ident in taken_c:
            raise ValidationError(f"{row['label']} is chosen for two {current().name} categories.")
        taken_c.add(ident)
        return _state("category", category=row, chosen=True)
    if income:
        raise ValidationError(f"{name} is income, so it has no envelope here.")
    env = next((e for e in book.envelopes if e["id"] == ident), None) if kind == "env" else None
    if env is None:
        raise ValidationError(f"The envelope chosen for {name} is no longer in this book.")
    if ident in taken_e:
        raise ValidationError(f"{env['name']} is chosen for two {current().name} categories.")
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
    if any(k not in known for k in links):
        raise ValidationError(
            "One of the categories you picked is no longer in the plan. Review it again."
        )
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
            found = propose(c["name"], book, taken_c, taken_e, income=is_income(c))
            out[c["id"]] = _from_match(found, taken_c, taken_e)
    _settle_ties(plan, out, book, taken_c, taken_e)
    _suggest_close(plan, out, book, taken_c)
    return out


def _settle_ties(
    plan: dict, out: dict[str, dict], book: Book, taken_c: set[str], taken_e: set[str]
) -> None:
    """A YNAB category that reads the same as several of the book's takes the one spelled most
    like it (YNAB's "Fun Money": "fun money" over "Fun money!"). Only a true tie is left for the
    person to pick. The review alone does this; the sync files a ``choose`` item instead."""
    for c in plan["categories"]:
        d = out[c["id"]]
        if d["kind"] != "ambiguous":
            continue
        free = [o for o in d["options"] if o["ref"].partition(":")[2] not in taken_c | taken_e]
        ranked = sorted(free, key=lambda o: -spelled_alike(c["name"], o["name"]))
        if not ranked:
            out[c["id"]] = _state("new")
            continue
        best = spelled_alike(c["name"], ranked[0]["name"])
        if len(ranked) > 1 and spelled_alike(c["name"], ranked[1]["name"]) == best:
            continue
        out[c["id"]] = _take(ranked[0]["ref"], d["options"], book, taken_c, taken_e)


def _take(ref: str, options: list, book: Book, taken_c: set[str], taken_e: set[str]) -> dict:
    kind, _, ident = ref.partition(":")
    if kind == "cat":
        taken_c.add(ident)
        return _state("category", category=book.rows[ident], options=options, among=True)
    env = next(e for e in book.envelopes if e["id"] == ident)
    if env.get("category_ids"):
        return _state("new", offer=env, options=options, among=True)
    taken_e.add(ident)
    return _state("envelope", envelope=env, options=options, among=True)


def _suggest_close(plan: dict, out: dict[str, dict], book: Book, taken_c: set[str]) -> None:
    """A category left new, with nothing of the same name, takes the book's category whose name
    is closest — the closest pair first, so no category is suggested for two rows."""
    open_ = [c for c in plan["categories"] if _is_plain_new(out[c["id"]])]
    pairs = sorted(
        (
            (closeness(c["name"], row["label"]), i, row["id"], c["id"])
            for i, c in enumerate(open_)
            for row in (book.income if is_income(c) else book.matchable)
            if row["id"] not in taken_c
        ),
        key=lambda t: (-t[0], t[1]),
    )
    for score, _, cat_id, ynab_id in pairs:
        if score < CLOSE:
            break
        if cat_id in taken_c or not _is_plain_new(out[ynab_id]):
            continue
        taken_c.add(cat_id)
        out[ynab_id] = _state("category", category=book.rows[cat_id], close=True)


def _is_plain_new(d: dict) -> bool:
    return d["kind"] == "new" and not d["chosen"] and d["offer"] is None


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
    if is_income(c) and kind != "ambiguous":
        return "Income: it has no envelope here, and what comes in raises Ready to Assign"
    if kind in ("linked", "category"):
        text = _budget_note(book, d["category"])
        if text == "Not budgeted here" and assigned:
            return f"Gets an envelope here, since {current().name} has money assigned to it"
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


def _envelope_color(book: Book, env: dict) -> str:
    """The Plan screen's colour for an envelope: its own, else its first category's."""
    first = (env.get("category_ids") or [None])[0]
    return env.get("color") or (book.rows.get(first) or {}).get("color") or ""


def _envelope_view(book: Book, env: dict | None) -> dict | None:
    if env is None:
        return None
    return {
        "name": env["name"],
        "color": _envelope_color(book, env),
        "shared": len(env.get("category_ids") or []) > 1,
    }


def choices(book: Book) -> dict:
    """What a category can be sent to, listed once for every row of the review."""
    income = (
        {"income_categories": [{"ref": cat_ref(c["id"]), "name": c["label"]} for c in book.income]}
        if current().caps.income_categories
        else {}
    )
    return {
        **income,
        "categories": [
            {
                "ref": cat_ref(c["id"]),
                "name": c["label"],
                "color": c.get("color") or "",
                "envelope": _envelope_view(book, book.env_of.get(c["id"])),
                # What an envelope made for it would be called (it sits in none yet).
                "new_envelope": None
                if c["id"] in book.env_of
                else book.free_envelope_name(c["label"]),
            }
            for c in book.matchable
        ],
        "envelopes": [
            {
                "ref": env_ref(e["id"]),
                "name": e["name"],
                "color": _envelope_color(book, e),
                "holds": len(e.get("category_ids") or []),
            }
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
            "close": d["close"],
            "among": d["among"],
            # The name a new category made for it would get, and so its new envelope too.
            "new_name": book.free_label(c["label"]),
            "offer": None if d["offer"] is None else env_ref(d["offer"]["id"]),
            "ask": d["kind"] == "ambiguous",
            "options": d["options"],
            "assigned": has_assigned(plan, c["id"]),
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
        if gid in out or is_income(c):
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
        if is_parking_name(c["group_name"]):
            same = []
        out[gid] = same[0] if len(same) == 1 else None
        if out[gid]:
            taken.add(out[gid]["id"])
    return out
