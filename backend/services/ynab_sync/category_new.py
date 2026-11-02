"""Categories and groups that are new on one side, group renames, and the budget (Assigned) link
between a category and its envelope. See ``category_sync`` for the order they run in."""

from __future__ import annotations

from backend.core.errors import ExternalServiceError, OpenNetWorthError
from backend.services import planning, ynab_pairs
from backend.services.ynab_migration import category_make as make
from backend.services.ynab_migration.category_match import env_ref, propose
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_pairs import norm
from backend.services.ynab_sync import conflicts
from backend.services.ynab_sync.category_base import shared_line
from backend.services.ynab_sync.category_pairs import Pairs
from backend.services.ynab_sync.client import RateCapReached


class News(Pairs):
    # ── groups ──
    def sync_groups(self) -> None:
        for ygid, gp in list(self.base.groups.items()):
            if gp.get("gone"):
                continue
            yg, og = (
                self.yg.get(ygid),
                next((g for g in self.book.groups if g["id"] == gp["group"]), None),
            )
            if yg is None or yg.get("deleted") or og is None:
                gp["gone"] = True  # one side is gone: neither deletes the other's group
                self.base.set_group(gp)
                continue
            try:
                self.sync_group(ygid, gp, clean(yg["name"], 120), og["name"])
            except RateCapReached:
                self.deferred += 1
                return

    def sync_group(self, ygid: str, gp: dict, y: str, o: str) -> None:
        y_changed, o_changed = y != gp["ynab"]["name"], o != gp["onw"]["name"]
        key = f"group:{ygid}"
        fields = [{"label": "Name", "onw": o, "ynab": y}]
        if y_changed and o_changed and not self.same("name", y, o):
            reason = (
                f"The group {o} was renamed in both Open Net Worth and YNAB. Pick the name to keep."
            )
            return self.file(conflicts.CONFLICT, key, o, reason, entity="group", fields=fields)
        if key in self.open:
            if y_changed != o_changed:
                return
            conflicts.close(key)
        if y_changed and o_changed:
            gp["ynab"]["name"], gp["onw"]["name"] = y, o
        elif y_changed and self.can_pull:
            self.pull_group_name(gp, y, o)
        elif o_changed and self.can_push:
            self.push_group_name(ygid, gp, y, o)
        else:
            return
        self.base.set_group(gp)

    def pull_group_name(self, gp: dict, y: str, o: str) -> None:
        try:
            planning.apply_budget_change(
                {"type": "group_rename", "group_id": gp["group"], "name": make.our_group_name(y)}
            )
        except OpenNetWorthError:
            reason = (
                f"YNAB renamed the group {o} to {y}, but this book could not take the change. "
                "Keep Open Net Worth's name to send it to YNAB instead."
            )
            fields = [{"label": "Name", "onw": o, "ynab": y}]
            return self.file(
                conflicts.FAILED, f"group:{gp['id'][2:]}", o, reason, entity="group", fields=fields
            )
        self.book.refresh()
        gp["ynab"]["name"], gp["onw"]["name"] = y, make.our_group_name(y)
        self.count("pulled", f"Renamed the group {o} to {y} here, as in YNAB.")

    def push_group_name(self, ygid: str, gp: dict, y: str, o: str) -> None:
        try:
            saved = self.send(self.client.update_group, ygid, o[:50])
        except RateCapReached:
            self.deferred += 1
            return
        except ExternalServiceError as exc:
            self.refused(exc)
            reason = f"YNAB did not accept the new name for the group {o}. Keep YNAB's name, or change it there and sync again."
            fields = [{"label": "Name", "onw": o, "ynab": y}]
            return self.file(
                conflicts.FAILED, f"group:{ygid}", o, reason, entity="group", fields=fields
            )
        self.put_group(saved)
        gp["ynab"]["name"], gp["onw"]["name"] = clean(saved["name"], 120), o
        self.count("sent", f"Renamed the group {y} to {o} in YNAB.")

    def ynab_group_for(self, group_id: str | None) -> str | None:
        """The YNAB group paired with one of ours: the paired one, else the one of the same name
        (also one whose old pair was dropped), else a new one."""
        if group_id is None:
            return None
        if (found := self.groups.ynab_id(group_id)) is not None:
            return found
        ours = next((g for g in self.book.groups if g["id"] == group_id), None)
        if ours is None:
            return None
        taken = {k for k, d in self.base.groups.items() if not d.get("gone")}
        same = [
            g for g in self.yg.values()
            if g["id"] not in taken and self.mirrorable_group(g) and norm(g["name"]) == norm(ours["name"])
        ]  # fmt: skip
        if len(same) == 1:
            self.groups.pair(same[0]["id"], clean(same[0]["name"], 120), ours)
            return same[0]["id"]
        saved = self.send(self.client.create_group, ours["name"][:50])
        self.put_group(saved)
        self.groups.pair(saved["id"], clean(saved["name"], 120), ours)
        self.count("sent", f"Added the group {ours['name']} to YNAB.")
        return saved["id"]

    # ── a category that is new in YNAB ──
    def pull_new(self, plan_cats: list[dict]) -> None:
        paired = {p["category"] for p in self.base.cats.values()}
        for c in plan_cats:
            if c["id"] in self.base.cats or f"y:{c['id']}" in self.base.declined:
                continue
            try:
                self.new_from_ynab(c, paired)
            except OpenNetWorthError:
                reason = (
                    f"YNAB's {c['name']} could not be added here. "
                    "Rename it in YNAB or here, then try again, or leave it out."
                )
                self.file(
                    conflicts.FAILED, f"new:{c['id']}", c["name"], reason,
                    entity="new_category", options=[], ynab_id=c["id"],
                )  # fmt: skip
        for key in [k for k in self.open if k.startswith("new:") and k[4:] in self.base.cats]:
            conflicts.close(key)

    def new_from_ynab(self, c: dict, paired: set[str]) -> None:
        m = propose(c["name"], self.book, paired, set())
        if m.kind in ("offer", "ambiguous", "envelope"):
            self.held_names.add(norm(c["name"]))
        if m.kind == "category":
            return self.pair_existing(c, m.category, paired)
        if not self.can_pull:
            return
        if m.kind in ("offer", "ambiguous"):
            return self.ask(c, m)
        if m.kind == "envelope":
            cat = make.make_category_in(self.book, m.envelope, c["label"], c["hidden"])
        else:
            cat = self.create_here(c)
        paired.add(cat["id"])
        self.pair_new(
            c, self.book.rows[cat["id"]], f"Added {cat['label']} here, as it is new in YNAB."
        )

    def create_here(self, c: dict) -> dict:
        if self.parking(c["group_id"]):
            return make.make_category(self.book, c["label"], None, c["hidden"], budgeted=False)[0]
        gid = self.groups.ensure(self.book, c["group_id"], c["group_name"])
        return make.make_category(self.book, c["label"], gid, c["hidden"])[0]

    def pair_existing(self, c: dict, cat: dict, paired: set[str]) -> None:
        paired.add(cat["id"])
        text = f"Matched YNAB's {c['name']} with your {cat['label']}."
        self.pair_new(c, cat, text)

    def pair_new(self, c: dict, cat: dict, text: str) -> None:
        solo, held = self.book.solo_envelope(cat["id"]), self.book.env_of.get(cat["id"])
        doc = ynab_pairs.cat_doc(
            self.plan_id, c["id"], cat["id"], solo["id"] if solo else None,
            self.ostate(cat), self.ystate(self.yc[c["id"]]),
        )  # fmt: skip
        self.base.set_cat(doc)
        if held is not None and solo is None:
            self.note_once(held["id"], held["name"], shared_line(held["name"]))
        self.count("pulled", text)

    def ask(self, c: dict, m) -> None:
        key = f"new:{c['id']}"
        if m.kind == "ambiguous":
            reason = f"YNAB's {c['name']} matches more than one of your categories. Pick one."
            options = m.options
        else:
            env = m.envelope["name"]
            reason = f"YNAB's {c['name']} matches your envelope {env}, which already holds other categories. Add it there, or as a new category."
            options = [{"ref": env_ref(m.envelope["id"]), "name": env}]
        self.file(
            conflicts.CHOOSE,
            key,
            c["name"],
            reason,
            entity="new_category",
            options=options,
            ynab_id=c["id"],
        )

    # ── a category that is new here ──
    def push_new(self) -> None:
        if not self.can_push:
            return
        paired = {p["category"] for p in self.base.cats.values()}
        for cat in self.book.matchable:
            skip = cat["id"] in paired or cat.get("hidden") or norm(cat["label"]) in self.held_names
            if skip or f"o:{cat['id']}" in self.base.declined:
                continue
            if f"newhere:{cat['id']}" in self.open:
                continue
            try:
                self.create_in_ynab(cat)
            except RateCapReached:
                self.deferred += 1
                return

    def same_named_in_ynab(self, label: str) -> list[dict]:
        """YNAB's categories of this name that no category here is paired with — hidden ones
        included, which is why this reads all of YNAB's, not only the ones the plan mirrors."""
        key = norm(label)
        return [
            c for c in self.yc.values()
            if not c.get("deleted") and not c.get("internal") and c["id"] not in self.base.cats
            and norm(clean(c["name"], 120)) == key
            and norm((self.yg.get(c.get("category_group_id")) or {}).get("name", "")) != "credit card payments"
        ]  # fmt: skip

    def create_in_ynab(self, cat: dict) -> None:
        key = f"newhere:{cat['id']}"
        same = self.same_named_in_ynab(cat["label"])
        if len(same) == 1:
            text = f"Matched YNAB's {clean(same[0]['name'], 120)} with your {cat['label']}."
            return self.pair_new({"id": same[0]["id"]}, cat, text)
        if same:
            reason = (
                f"YNAB has several categories called {cat['label']}. "
                "Rename or remove the extras in YNAB, then try again."
            )
            return self.file(conflicts.FAILED, key, cat["label"], reason, entity="new_category", options=[], category=cat["id"])  # fmt: skip
        solo, held = self.book.solo_envelope(cat["id"]), self.book.env_of.get(cat["id"])
        try:
            gid = self.ynab_group_for(held["group_id"]) if held else None
            saved = self.send(
                self.client.create_category, gid or self.not_budgeted_group(), cat["label"][:50]
            )
        except ExternalServiceError as exc:
            self.refused(exc)
            reason = (
                f"YNAB did not accept the category {cat['label']} or its group. "
                "Rename them here and sync again, or leave it out of YNAB."
            )
            return self.file(conflicts.FAILED, key, cat["label"], reason, entity="new_category", options=[], category=cat["id"])  # fmt: skip
        self.put_cat(saved)
        self.base.set_cat(
            ynab_pairs.cat_doc(
                self.plan_id, saved["id"], cat["id"], solo["id"] if solo else None,
                self.ostate(cat), self.ystate(saved),
            )
        )  # fmt: skip
        if held is not None and solo is None:
            self.note_once(held["id"], held["name"], shared_line(held["name"]))
        self.count("sent", f"Added {cat['label']} to YNAB.")

    # ── the budget link ──
    def budgets(self) -> dict[tuple[str, str], int]:
        """A category's Assigned syncs only while it is alone in its envelope. Leaving that state
        is said once, and YNAB's figures are noted meanwhile; coming back compares them with
        this book's, and returns them for the Assigned step to read as YNAB's figures."""
        resumed: dict[tuple[str, str], int] = {}
        for ycid, p in self.base.cats.items():
            cat = self.book.rows.get(p["category"])
            if p["gone"] or cat is None:
                continue
            if p.get("paused"):
                self.note_theirs(ycid, p)
            solo = self.book.solo_envelope(cat["id"])
            now = solo["id"] if solo else None
            if now != p["env"]:
                resumed |= self.move_budget(ycid, p, cat, now)
            if p["env"] is None and self.book.env_of.get(cat["id"]) is None:
                self.give_envelope_for_money(ycid, p, cat)
        return resumed

    def note_theirs(self, ycid: str, p: dict) -> None:
        theirs = p.setdefault("theirs", {})
        theirs.update({m: milli for (m, c), milli in self.assigned_seen.items() if c == ycid})
        self.base.set_cat(p)

    def move_budget(self, ycid: str, p: dict, cat: dict, now: str | None) -> dict:
        before = p["env"]
        p["env"] = now
        resumed: dict[tuple[str, str], int] = {}
        if now is not None:
            if before is None and p.get("paused"):
                resumed = self.resume_figures(ycid, p)
        else:
            p["paused"], p["theirs"] = True, {}
            self.say_paused(cat)
        self.base.set_cat(p)
        return resumed

    def say_paused(self, cat: dict) -> None:
        held = self.book.env_of.get(cat["id"])
        if held is not None:
            self.note_once(held["id"], held["name"], shared_line(held["name"]))
            return
        line = (
            f"{cat['label']} is no longer in an envelope, so its Assigned isn't synced with YNAB."
        )
        self.note_once(f"{cat['id']}:out", cat["label"], line)

    def resume_figures(self, ycid: str, p: dict) -> dict[tuple[str, str], int]:
        """YNAB's figure for every month either side holds: what it changed meanwhile, else what
        both agreed before. The baselines are cleared so any difference is asked about."""
        months = {m for (m, c) in self.base.assigned if c == ycid} | set(p.get("theirs") or {})
        figures = {
            (m, ycid): (p.get("theirs") or {}).get(m, self.base.assigned.get((m, ycid), 0))
            for m in months
        }
        self.base.drop_assigned_of(ycid)
        p["paused"], p["theirs"] = False, {}
        return figures

    def give_envelope_for_money(self, ycid: str, p: dict, cat: dict) -> None:
        """YNAB assigned money to a category that has no envelope here: it gets one of its own."""
        if ycid not in self.ynab_assigned or not self.can_pull:
            return
        yg = self.yg.get(self.yc.get(ycid, {}).get("category_group_id")) or {}
        gid = None
        if yg and not self.parking(yg["id"]):
            gid = self.groups.ensure(self.book, yg["id"], clean(yg["name"], 120))
        env = make.give_envelope(self.book, cat, gid)
        p["env"] = env["id"]
        # The agreed group is left as it was, so the next comparison sees the move here and
        # takes the YNAB category out of "Not budgeted" into the group paired with this one.
        self.base.set_cat(p)
        self.count(
            "pulled", f"Added an envelope for {cat['label']}, as YNAB has money assigned to it."
        )
