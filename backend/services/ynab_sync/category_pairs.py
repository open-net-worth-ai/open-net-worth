"""Categories and groups that are already paired: a rename, a move to another group or a hide on
one side reaches the other, within the sync's direction. The rule is the transactions' (ADR
``2026-10-20`` and its amendments): per field, nobody changed it -> nothing; one side -> the other
side follows; both, to the same thing -> agreed; both, to different things -> the review list."""

from __future__ import annotations

from backend.core.errors import ExternalServiceError, OpenNetWorthError
from backend.services import planning
from backend.services.budget_app import current
from backend.services.ynab_migration import category_make as make
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_pairs import HIDDEN_HERE
from backend.services.ynab_sync import conflicts
from backend.services.ynab_sync.category_base import FIELDS, Base
from backend.services.ynab_sync.client import RateCapReached

LABELS = {"name": "Name", "hidden": "Visibility", "group": "Group"}


def hides() -> bool:
    return current().caps.hide_categories


class Pairs(Base):
    # ── deciding ──
    def same(self, field: str, y, o) -> bool:
        if field == "name":
            return str(y).strip().casefold() == str(o).strip().casefold()
        if field == "group":
            return self.groups.our_id(y) == o
        return y == o

    def actions(self, p: dict, y: dict, o: dict) -> dict[str, str]:
        out = {}
        for f in FIELDS:
            y_changed, o_changed = y[f] != p["ynab"][f], o[f] != p["onw"][f]
            if f == "group" and o[f] is None:
                o_changed = False  # in no envelope: there is no group to move
            if f == "group" and y_changed and self.parking(y[f]):
                y_changed = False  # a move into a parking group is not one the book follows
            if y_changed and o_changed:
                out[f] = "adopt" if self.same(f, y[f], o[f]) else "conflict"
            elif y_changed or o_changed:
                out[f] = "pull" if y_changed else "push"
        return out

    def fields(self, acts: dict, y: dict, o: dict) -> list[dict]:
        def show(f, v, side):
            if f == "hidden":
                return "Hidden" if v else "Shown"
            if f == "group":
                return (self.ogroup_name(v) if side == "onw" else self.ygroup_name(v)) or "—"
            return v

        return [
            {"label": LABELS[f], "onw": show(f, o[f], "onw"), "ynab": show(f, y[f], "ynab")}
            for f in FIELDS
            if f in acts
        ]

    # ── one pair ──
    def sync_pairs(self) -> None:
        for ycid, p in list(self.base.cats.items()):
            try:
                self.sync_pair(ycid, p)
            except RateCapReached:
                self.deferred += 1
                return

    def sync_pair(self, ycid: str, p: dict) -> None:
        cat, ycat = self.book.rows.get(p["category"]), self.yc.get(ycid)
        if p["gone"] and cat is not None and ycat and not ycat.get("deleted"):
            p["gone"] = False  # restored here: it follows the pair's state again
        if p["gone"]:
            return
        if cat is None:
            return self.ours_gone(ycid, p, ycat)
        y = self.ystate(ycat) if ycat else {**p["ynab"], "hidden": True}
        o = self.ostate(cat)
        if o["group"] is None and p["onw"]["group"] is not None:
            p["onw"]["group"] = None
            self.base.set_cat(p)
        acts = self.actions(p, y, o)
        key = f"cat:{ycid}"
        if "conflict" in acts.values():
            clashing = {f for f, a in acts.items() if a == "conflict"}
            reason = (
                f"Renamed here and in {current().name}. Pick the name to keep."
                if clashing == {"name"}
                else f"Changed here and in {current().name}. Pick the version to keep."
            )
            return self.file(
                conflicts.CONFLICT, key, o["name"], reason, entity="category",
                fields=self.fields({f: a for f, a in acts.items() if a == "conflict"}, y, o),
            )  # fmt: skip
        if key in self.open:
            if set(acts.values()) - {"adopt"}:
                return
            conflicts.close(key)
        self.apply(ycid, p, cat, y, o, acts)

    def apply(self, ycid: str, p: dict, cat: dict, y: dict, o: dict, acts: dict) -> None:
        for f, a in acts.items():
            if a == "adopt":
                p["ynab"][f], p["onw"][f] = y[f], o[f]
        pulls = [f for f, a in acts.items() if a == "pull"]
        pushes = [f for f, a in acts.items() if a == "push"]
        if pulls and self.can_pull:
            self.pull(ycid, p, cat, y, o, pulls)
        if pushes and self.can_push:
            self.push(ycid, p, cat, y, o, pushes)
        if acts:
            self.base.set_cat(p)

    # ── YNAB -> here ──
    def pull(self, ycid: str, p: dict, cat: dict, y: dict, o: dict, fields: list[str]) -> None:
        old = o["name"]
        try:
            for f in fields:
                if f == "name":
                    o["name"] = self.pull_name(cat, y["name"])
                    self.count(
                        "pulled", f"Renamed {old} to {o['name']} here, as in {current().name}."
                    )
                elif f == "hidden":
                    make.set_hidden([cat["id"]], y["hidden"])
                    o["hidden"] = y["hidden"]
                    verb = "Hid" if y["hidden"] else "Showed"
                    self.count("pulled", f"{verb} {o['name']} here, as in {current().name}.")
                else:
                    o["group"] = self.pull_group(cat, y, o)
                p["ynab"][f], p["onw"][f] = y[f], o[f]
        except OpenNetWorthError:
            app = current().name
            reason = (
                f"{app} changed {old}, but this book could not take the change. "
                f"Keep Open Net Worth's version to send it to {app} instead."
            )
            self.file(
                conflicts.FAILED, f"cat:{ycid}", old, reason, entity="category",
                fields=self.fields({f: "pull" for f in fields}, y, o),
            )  # fmt: skip

    def pull_name(self, cat: dict, name: str) -> str:
        result = planning.apply_budget_change(
            {"type": "category_rename", "key": cat["id"], "new_label": name}
        )
        self.book.refresh()
        return result.get("label", name)

    def pull_group(self, cat: dict, y: dict, o: dict) -> str | None:
        """Move the envelope that holds only this category into the group YNAB moved it to."""
        solo = self.book.solo_envelope(cat["id"])
        ygroup = self.yg.get(y["group"])
        if solo is None or ygroup is None or self.parking(ygroup["id"]):
            return o["group"]
        gid = self.groups.ensure(self.book, ygroup["id"], clean(ygroup["name"], 120))
        if solo["group_id"] != gid:
            planning.apply_budget_change(
                {"type": "move_category", "category": solo["id"], "group_id": gid}
            )
            self.book.refresh()
            self.count(
                "pulled",
                f"Moved {solo['name']} to {self.ogroup_name(gid)} here, as in {current().name}.",
            )
        return gid

    # ── here -> YNAB ──
    def target_group(self, ycid: str, p: dict, o: dict, moved: bool) -> str | None:
        """The YNAB group this category belongs in: "Hidden in Open Net Worth" while it is hidden
        here. When shown again: the group it was taken from, unless its envelope has moved since
        (then the group paired with the envelope's), else where it is, else "Not budgeted"."""
        current_group = (self.yc.get(ycid) or {}).get("category_group_id")
        if o["hidden"] and not hides():
            if not self.parking(current_group):
                p["home"], p["home_for"] = current_group, o["group"]
            return self.parking_group(HIDDEN_HERE)
        home = p.get("home")
        home_ok = home in self.yg and not self.yg[home].get("deleted") and not self.parking(home)
        if home_ok and self.parking(current_group) and p.get("home_for") == o["group"]:
            return home
        if o["group"] and (moved or self.parking(current_group)):
            return self.ynab_group_for(o["group"])
        if not self.parking(current_group):
            return current_group
        return home if home_ok else self.not_budgeted_group()

    def push(self, ycid: str, p: dict, cat: dict, y: dict, o: dict, fields: list[str]) -> None:
        try:
            body: dict = {}
            if "name" in fields:
                body["name"] = o["name"][:50]
            if hides() and "hidden" in fields:
                body["hidden"] = o["hidden"]
            if {"group"} & set(fields) if hides() else {"hidden", "group"} & set(fields):
                gid = self.target_group(ycid, p, o, "group" in fields)
                if gid and gid != (self.yc.get(ycid) or {}).get("category_group_id"):
                    body["category_group_id"] = gid
            saved = self.send(self.client.update_category, ycid, body) if body else self.yc[ycid]
        except RateCapReached:
            self.deferred += 1
            return
        except ExternalServiceError as exc:
            self.refused(exc)
            return self.refuse(ycid, o["name"], fields, y, o)
        if not self.confirmed(saved, body):
            return self.refuse(ycid, o["name"], fields, y, o)
        self.put_cat(saved)
        if not o["hidden"]:
            p.pop("parked", None)
        now = self.ystate(saved)
        for f in fields:
            p["ynab"][f], p["onw"][f] = now[f], o[f]
        if "category_group_id" in body:  # a move to or from a parking group has been made
            p["ynab"]["group"] = now["group"]
        self.say_pushed(y, o, fields, body)

    def refuse(self, ycid: str, name: str, fields: list[str], y: dict, o: dict) -> None:
        app = current().name
        reason = (
            f"{app} did not accept the change to {name}. Keep {app}'s version, "
            "or change it there and sync again."
        )
        self.file(
            conflicts.FAILED, f"cat:{ycid}", name, reason, entity="category",
            fields=self.fields({f: "push" for f in fields}, y, o),
        )  # fmt: skip

    def say_pushed(self, y: dict, o: dict, fields: list[str], body: dict) -> None:
        name, app = o["name"], current().name
        parked = o["hidden"] and not hides()
        if "name" in body:
            self.count("sent", f"Renamed {y['name']} to {name} in {app}.")
        if "hidden" in body:
            verb = "Hid" if o["hidden"] else "Showed"
            self.count("sent", f"{verb} {name} in {app}, as it is here.")
        if "category_group_id" in body:
            where = HIDDEN_HERE if parked else self.ygroup_name(body["category_group_id"])
            if parked:
                line = f"Moved {name} to {where} in {app}, as it is hidden here. You can hide it in {app} yourself."
            elif "hidden" in fields and not hides():
                line = f"Moved {name} back to {where} in {app}, as it is shown here."
            else:
                line = f"Moved {name} to {where} in {app}."
            self.count("sent", line)

    def hide_gone(self, ycid: str, p: dict, name: str, app: str) -> None:
        """An app that can hide a category hides it, and is never asked to delete it."""
        try:
            saved = self.send(self.client.update_category, ycid, {"hidden": True})
        except RateCapReached:
            self.deferred += 1
            return
        except ExternalServiceError as exc:
            self.refused(exc)
            reason = f"{app} would not hide {name}. Hide it in {app} yourself."
            return self.file(conflicts.FAILED, f"cat:{ycid}", name, reason, entity="category")
        self.put_cat(saved)
        p["onw"]["hidden"], p["ynab"]["hidden"] = True, True
        self.count("sent", f"Hid {name} in {app}, as it was removed here.")
        self.base.set_cat(p)

    def ours_gone(self, ycid: str, p: dict, ycat: dict | None) -> None:
        """Deleted here (or merged into another category): YNAB's API can neither delete nor hide
        a category, so it is moved into "Hidden in Open Net Worth" and the pair is kept. Its
        transactions were relabelled here, and follow."""
        name, app = p["onw"]["name"], current().name
        if ycat is None or ycat.get("deleted") or self.ystate(ycat)["hidden"]:
            p["gone"] = True
        elif p.get("parked") or not self.can_push or f"cat:{ycid}" in self.open:
            return
        elif hides():
            return self.hide_gone(ycid, p, name, app)
        else:
            try:
                gid = self.parking_group(HIDDEN_HERE)
                body = {"category_group_id": gid}
                saved = self.send(self.client.update_category, ycid, body)
            except RateCapReached:
                self.deferred += 1
                return
            except ExternalServiceError as exc:
                self.refused(exc)
                reason = f"{app} would not move {name} to {HIDDEN_HERE}. Move it there or hide it in {app} yourself."
                return self.file(conflicts.FAILED, f"cat:{ycid}", name, reason, entity="category")
            if not self.confirmed(saved, body):
                return
            p["home"], p["home_for"] = ycat.get("category_group_id"), p["onw"]["group"]
            self.put_cat(saved)
            p["parked"] = True
            p["ynab"]["group"], p["onw"]["hidden"] = gid, True
            line = f"Moved {name} to {HIDDEN_HERE} in {app}, as it was removed here. You can hide it in {app} yourself."
            self.count("sent", line)
        self.base.set_cat(p)
