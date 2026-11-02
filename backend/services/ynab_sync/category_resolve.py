"""Settling a review item about a category or a category group: the person's choice is applied to
BOTH sides, as for a transaction. Unless the direction protects the side it would write — then
the item is accepted where it stands: the protected side's change waits, nothing is written, and
a later change on both sides asks again."""

from __future__ import annotations

from collections import Counter

from backend.core.errors import ConflictError
from backend.services.ynab_migration import category_make as make
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_sync import conflicts
from backend.services.ynab_sync.category_new import News


def _check(sync: News) -> None:
    if sync.errors:
        raise ConflictError(sync.errors[0])
    if sync.deferred:
        raise ConflictError("YNAB can't take any more requests for now. Try again shortly.")


def _protected(sync: News, keep: str) -> bool:
    """Keeping a side writes the other one — unless the direction protects that one."""
    return (sync.direction, keep) in (("from_ynab", "onw"), ("to_ynab", "ynab"))


def _category(sync: News, item: dict, keep: str) -> None:
    ycid = item["key"][4:]
    p = sync.base.cats.get(ycid)
    cat = sync.book.rows.get(p["category"]) if p else None
    ycat = sync.yc.get(ycid)
    if p is not None and cat is None and item["kind"] == conflicts.FAILED:
        p["gone"] = True  # YNAB would not take the move; leaving it where it is is the answer
        sync.base.set_cat(p)
        return
    if cat is None or ycat is None:
        raise ConflictError("Neither side of this item exists any more.")
    y, o = sync.ystate(ycat), sync.ostate(cat)
    # A refused change has no second version to pick between: whatever still differs is settled.
    wanted = ("conflict", "pull", "push") if item["kind"] == conflicts.FAILED else ("conflict",)
    fields = [f for f, a in sync.actions(p, y, o).items() if a in wanted]
    if _protected(sync, keep):
        for f in fields:  # the other side's current state becomes the agreed one
            side, now = ("ynab", y) if keep == "onw" else ("onw", o)
            p[side][f] = now[f]
    elif keep == "onw":
        sync.push(ycid, p, cat, y, o, fields)
    else:
        sync.pull(ycid, p, cat, y, o, fields)
    _check(sync)
    sync.base.set_cat(p)


def _group(sync: News, item: dict, keep: str) -> None:
    ygid = item["key"][6:]
    gp = sync.base.groups.get(ygid)
    yg = sync.yg.get(ygid)
    og = next((g for g in sync.book.groups if gp and g["id"] == gp["group"]), None)
    if gp is None or yg is None or og is None:
        raise ConflictError("Neither side of this item exists any more.")
    y, o = clean(yg["name"], 120), og["name"]
    if _protected(sync, keep):
        side, now = ("ynab", y) if keep == "onw" else ("onw", o)
        gp[side]["name"] = now
    elif keep == "onw":
        sync.push_group_name(ygid, gp, y, o)
    else:
        sync.pull_group_name(gp, y, o)
    _check(sync)
    sync.base.set_group(gp)


def _plan_category(sync: News, ycid: str) -> dict:
    ycat = sync.yc.get(ycid)
    if ycat is None or ycat.get("deleted"):
        raise ConflictError("This category is no longer in YNAB.")
    name = clean(ycat["name"], 120)
    return {
        "id": ycid,
        "name": name,
        "label": name,
        "group_id": ycat["category_group_id"],
        "group_name": sync.ygroup_name(ycat["category_group_id"]),
        "hidden": sync.ystate(ycat)["hidden"],
    }


def _choose(sync: News, item: dict, keep: str, choice: str | None) -> None:
    ycid = item["ynab_id"]
    if item["kind"] == conflicts.FAILED and keep == "onw":  # leave it out, and keep it out
        sync.base.decline(f"y:{ycid}")
        return
    if ycid in sync.base.cats:
        raise ConflictError("That YNAB category is already matched. Sync again to see it.")
    c = _plan_category(sync, ycid)
    paired = {p["category"] for p in sync.base.cats.values()}
    adding = keep == "ynab" or (choice or "").startswith("env:")
    if adding and not sync.can_pull:
        raise ConflictError("In To YNAB mode this book isn't changed. Switch to Both ways first.")
    if keep == "ynab":
        cat = sync.create_here(c)
        text = f"Added {cat['label']} here, as it is new in YNAB."
        return sync.pair_new(c, sync.book.rows[cat["id"]], text)
    if choice not in [o["ref"] for o in item["options"]]:
        raise ConflictError("Pick which of your categories to use.")
    if choice.startswith("cat:"):
        cat = sync.book.rows.get(choice[4:])
        if cat is None:
            raise ConflictError("That category is no longer in this book. Pick another.")
        if cat["id"] in paired:
            raise ConflictError(
                "That category is already matched with another YNAB category. Pick another."
            )
        return sync.pair_existing(c, cat, paired)
    env = next((e for e in sync.book.envelopes if e["id"] == choice[4:]), None)
    if env is None:
        raise ConflictError("That envelope is no longer in this book. Pick another.")
    cat = make.make_category_in(sync.book, env, c["label"], c["hidden"])
    text = f"Added {cat['label']} to your envelope {env['name']}, as it is new in YNAB."
    sync.pair_new(c, sync.book.rows[cat["id"]], text)


def _new_here(sync: News, item: dict, keep: str) -> None:
    if keep == "ynab":  # leave it out of YNAB, and keep it out
        sync.base.decline(f"o:{item['category']}")
        return
    cat = sync.book.rows.get(item["category"])
    if cat is None:
        raise ConflictError("That category no longer exists here.")
    if not sync.can_push:
        raise ConflictError("In From YNAB mode YNAB isn't changed. Switch to Both ways first.")
    sync.create_in_ynab(cat)
    _check(sync)


def apply(item: dict, keep: str, choice: str | None, st: dict, meta: dict, base, client) -> None:
    sync = News(client, base, st, meta, Counter(), {})
    sync.quiet = True
    try:
        if item["entity"] == "category":
            _category(sync, item, keep)
        elif item["entity"] == "group":
            _group(sync, item, keep)
        elif "ynab_id" in item:
            _choose(sync, item, keep, choice)
        else:
            _new_here(sync, item, keep)
    finally:
        sync.finish()
