"""What every part of the categories sync shares: the two sides' state for one category, sending
to YNAB within the run's request allowance, and filing a review item. See ``category_sync``."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from backend.core.errors import ExternalServiceError
from backend.services.budget_app import current
from backend.services.ynab_migration import category_make as make
from backend.services.ynab_migration.category_match import Book
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_pairs import NOT_BUDGETED, PARKING, norm
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync import conflicts, delta, recorder
from backend.services.ynab_sync.baselines import Baselines
from backend.services.ynab_sync.client import RateCapReached, YnabClient

# YNAB's API cannot hide a category, so one hidden, deleted or merged here is moved into the group
# HIDDEN_HERE (made once, for the person to hide in YNAB if they like). Neither it, "Not budgeted"
# nor YNAB's own "Hidden Categories" (``ynab_pairs.PARKING``) is ever a group the book follows a
# category into.
FIELDS = ("name", "hidden", "group")


@dataclass
class Resumed:
    """The categories whose Assigned must be compared afresh this run (``cids``: they have no
    agreed baseline, so every difference goes to review) and YNAB's figures for them."""

    figures: dict[tuple[str, str], int] = field(default_factory=dict)
    cids: set[str] = field(default_factory=set)


def trim(text: str) -> str:
    return text.strip().rstrip(".")


def shared_line(envelope_name: str) -> str:
    return f"{envelope_name} holds several categories, so its Assigned isn't synced with {current().name}."


class Base:
    def __init__(
        self,
        client: YnabClient,
        base: Baselines,
        st: dict,
        meta: dict,
        tally: Counter,
        assigned_seen: dict,
        full: bool = False,
    ) -> None:
        self.client, self.base, self.meta, self.tally = client, base, meta, tally
        self.direction, self.plan_id = st["direction"], st["plan_id"]
        self.book = Book()
        self.groups = make.Groups(self.plan_id, base.groups, base.set_group)
        self.open = conflicts.open_keys()
        self.yc = {c["id"]: c for c in meta["categories"]}
        self.yg = {g["id"]: g for g in meta["groups"]}
        self.held_names: set[str] = set()
        self.noted: set[str] = set()
        self.deferred = 0
        self.quiet = False  # resolving one item: a refusal is the caller's error, not a new item
        self.errors: list[str] = []
        self.assigned_seen, self.full = assigned_seen, full
        self.fresh: set[str] = set()
        self._figures: dict[tuple[str, str], int] | None = None
        self.ynab_assigned = {cid for (_, cid), milli in assigned_seen.items() if milli}

    @property
    def can_pull(self) -> bool:
        return self.direction != "to_ynab"

    @property
    def can_push(self) -> bool:
        return self.direction != "from_ynab"

    # ── the two sides of a category ──
    def ystate(self, c: dict) -> dict:
        g = self.yg.get(c.get("category_group_id")) or {}
        hidden = bool(c.get("hidden") or c.get("deleted") or g.get("hidden") or g.get("deleted"))
        home = c.get("original_category_group_id") if c.get("hidden") else None
        return {
            "name": clean(c["name"], 120),
            "hidden": hidden,
            "group": home or c.get("category_group_id"),
        }

    def ostate(self, cat: dict) -> dict:
        """The group is that of whichever envelope holds the category, alone or with others; a
        category in no envelope has none, and lives in YNAB's "Not budgeted"."""
        held = self.book.env_of.get(cat["id"])
        return {
            "name": cat["label"],
            "hidden": bool(cat.get("hidden")),
            "group": held["group_id"] if held else None,
        }

    def ygroup_name(self, ygid: str | None) -> str:
        return clean((self.yg.get(ygid) or {}).get("name", ""), 120)

    def ogroup_name(self, gid: str | None) -> str:
        return next((g["name"] for g in self.book.groups if g["id"] == gid), "")

    # ── YNAB's side, kept in step with what a write returned ──
    def put_cat(self, saved: dict) -> None:
        self.yc[saved["id"]] = {**self.yc.get(saved["id"], {}), **saved}

    def put_group(self, saved: dict) -> None:
        self.yg[saved["id"]] = {**self.yg.get(saved["id"], {}), **saved}

    def parking(self, ygid: str | None) -> bool:
        """A YNAB group that only holds categories the book has no group for. An app that can hide
        a category parks nothing, so there the one such group is where a category in no envelope
        lives."""
        name = norm((self.yg.get(ygid) or {}).get("name", ""))
        return name == norm(NOT_BUDGETED) if current().caps.hide_categories else name in PARKING

    def parking_group(self, name: str) -> str:
        """The YNAB group of this name, made on first use."""
        found = next(
            (g for g in self.yg.values() if not g.get("deleted") and norm(g["name"]) == norm(name)),
            None,
        )
        if found:
            return found["id"]
        saved = self.send(self.client.create_group, name)
        self.put_group(saved)
        self.count("sent", f"Added the group {name} to {current().name}.")
        return saved["id"]

    def not_budgeted_group(self) -> str:
        return self.parking_group(NOT_BUDGETED)

    def mirrorable_group(self, g: dict) -> bool:
        """A YNAB group a book group can pair with: not deleted, not the one YNAB keeps for card
        payments, not a parking group, not an internal one, not an app's income group."""
        reserved = ("credit card payments",) if current().caps.card_payment_categories else ()
        if g.get("deleted") or g.get("is_income") or norm(g["name"]) in (*reserved, *PARKING):
            return False
        members = [c for c in self.yc.values() if c.get("category_group_id") == g["id"]]
        return not (members and all(c.get("internal") for c in members))

    # ── writing to YNAB ──
    def send(self, call, *args):
        """One request within the categories' allowance (transactions and Assigned keep the rest
        of a run's); ``RateCapReached`` when it is used up."""
        if self.client.budget.used >= current().caps.category_request_cap:
            raise RateCapReached
        return call(*args)

    def refused(self, exc: ExternalServiceError) -> bool:
        """True when YNAB refused this request's content; anything else (auth, outage) stops the run."""
        if not ynab_client.rejected(exc):
            raise exc
        return True

    def confirmed(self, saved: dict, body: dict) -> bool:
        """YNAB's answer says what we asked for, so a state is only recorded as agreed when it is."""
        return saved.get("name", body.get("name")) == body.get(
            "name", saved.get("name")
        ) and saved.get("category_group_id", body.get("category_group_id")) == body.get(
            "category_group_id", saved.get("category_group_id")
        )

    def ynab_figures(self) -> dict[tuple[str, str], int]:
        """YNAB's Assigned for every category and month: this run's read when it was a full one,
        else one more request for the whole plan."""
        if self._figures is None:
            if self.full:
                self._figures = dict(self.assigned_seen)
            else:
                data = self.send(self.client.read_plan, None)
                plan = data.get("plan") or data.get("budget") or {}
                self._figures = delta.assigned_of(plan)
        return self._figures

    # ── telling the person ──
    def count(self, direction: str, text: str) -> None:
        self.tally["categories"] += 1
        recorder.category_change(direction, text)

    def note_shared(self, env: dict) -> None:
        """Said once, ever: the import (or an earlier run) may have said it already."""
        if not self.base.told(env["id"]):
            self.base.tell(env["id"])
            self.note_once(env["id"], env["name"], shared_line(env["name"]))

    def note_once(self, key: str, item: str, line: str) -> None:
        if key not in self.noted:
            self.noted.add(key)
            recorder.note(item, line)

    def file(self, kind: str, key: str, title: str, reason: str, **extra) -> None:
        if self.quiet:
            self.errors.append(reason)
        elif key not in self.open:
            conflicts.file(kind, key, title, reason, None, None, **extra)
            self.open[key] = {"key": key}

    def finish(self) -> None:
        """Write what the run learned of YNAB's side back to the meta, and say what waits."""
        self.meta["categories"] = list(self.yc.values())
        self.meta["groups"] = list(self.yg.values())
        if self.deferred:
            self.tally["deferred"] += self.deferred
            recorder.deferred(
                "Categories",
                f"Some category changes wait for the next run ({current().name} request limit).",
            )
