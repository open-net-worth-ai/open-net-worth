"""Settling a review item about a category or a category group: the person's choice is applied to
BOTH sides, as for a transaction. Unless the direction protects the side it would write — then
the item is accepted where it stands: the protected side's change waits, nothing is written, and
a later change on both sides asks again."""

from __future__ import annotations

from collections import Counter

from backend.core.errors import ConflictError
from backend.services.budget_app import current
from backend.services.ynab_migration import category_make as make
from backend.services.ynab_migration.mapping import clean
from backend.services.ynab_sync import assigned, conflicts
from backend.services.ynab_sync.category_new import News
from backend.services.ynab_sync.client import RateCapReached


def _check(sync: News) -> None:
    if sync.errors:
        raise ConflictError(sync.errors[0])
    if sync.deferred:
        raise ConflictError(
            f"{current().name} can't take any more requests for now. Try again shortly."
        )


def _protected(sync: News, keep: str) -> bool:
    """Keeping a side writes the other one — unless the direction protects that one."""
    return (sync.direction, keep) in (("from_ynab", "onw"), ("to_ynab", "ynab"))


def _category(sync: News, item: dict, keep: str) -> None:
    ycid = item["key"][4:]
    p = sync.base.cats.get(ycid)
    cat = sync.book.rows.get(p["category"]) if p else None
    ycat = sync.yc.get(ycid)
    if cat is None or ycat is None or ycat.get("deleted"):
        if p is not None:  # a side is gone, so there is nothing left to settle: let it go
            p["gone"] = True
            sync.base.set_cat(p)
        return
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
    if gp is None or yg is None or yg.get("deleted") or og is None:
        if gp is not None:
            gp["gone"] = True
            sync.base.set_group(gp)
        return
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
    name = clean(ycat["name"], 120)
    return {
        "id": ycid,
        "name": name,
        "label": name,
        "group_id": ycat["category_group_id"],
        "group_name": sync.ygroup_name(ycat["category_group_id"]),
        "hidden": sync.ystate(ycat)["hidden"],
        **({"kind": "income"} if ycat.get("is_income") else {}),
    }


def _choose(sync: News, item: dict, keep: str, choice: str | None) -> None:
    ycid = item["ynab_id"]
    if item["kind"] == conflicts.FAILED and keep == "onw":  # leave it out, and keep it out
        sync.base.decline(f"y:{ycid}")
        return
    ycat = sync.yc.get(ycid)
    if ycid in sync.base.cats:
        return  # matched meanwhile
    if ycat is None or ycat.get("deleted"):  # gone from YNAB: leave it out
        sync.base.decline(f"y:{ycid}")
        return
    c = _plan_category(sync, ycid)
    paired = {p["category"] for p in sync.base.cats.values()}
    adding = keep == "ynab" or (choice or "").startswith("env:")
    if adding and not sync.can_pull:
        raise ConflictError(
            f"In To {current().name} mode this book isn't changed. Switch to Both ways first."
        )
    if keep == "ynab":
        cat = sync.create_here(c)
        text = f"Added {cat['label']} here, as it is new in {current().name}."
        return sync.pair_new(c, sync.book.rows[cat["id"]], text, fresh=False)
    if choice not in [o["ref"] for o in item["options"]]:
        raise ConflictError("Pick which of your categories to use.")
    if choice.startswith("cat:"):
        cat = sync.book.rows.get(choice[4:])
        if cat is None:
            raise ConflictError("That category is no longer in this book. Pick another.")
        if cat["id"] in paired:
            raise ConflictError(
                f"That category is already matched with another {current().name} category. Pick another."
            )
        return sync.pair_existing(c, cat, paired)
    env = next((e for e in sync.book.envelopes if e["id"] == choice[4:]), None)
    if env is None:
        raise ConflictError("That envelope is no longer in this book. Pick another.")
    cat = make.make_category_in(sync.book, env, c["label"], c["hidden"])
    text = f"Added {cat['label']} to your envelope {env['name']}, as it is new in {current().name}."
    sync.pair_new(c, sync.book.rows[cat["id"]], text)


def _new_here(sync: News, item: dict, keep: str) -> None:
    if keep == "ynab":  # leave it out of YNAB, and keep it out
        sync.base.decline(f"o:{item['category']}")
        return
    cat = sync.book.rows.get(item["category"])
    if cat is None:
        return  # gone from this book: nothing to add to YNAB
    if not sync.can_push:
        app = current().name
        raise ConflictError(f"In From {app} mode {app} isn't changed. Switch to Both ways first.")
    sync.create_in_ynab(cat)
    _check(sync)


def _budget(sync: News, item: dict, keep: str) -> None:
    """YNAB has money assigned to a category the person took out of its envelope: give it one
    (and take YNAB's figures, as it was asked for), or leave it without."""
    ycid = item["ynab_id"]
    if keep == "onw":
        sync.base.decline(f"e:{ycid}")
        return
    p = sync.base.cats.get(ycid)
    cat = sync.book.rows.get(p["category"]) if p else None
    if p is None or cat is None or p.get("gone") or p["env"]:
        return
    if sync.book.env_of.get(cat["id"]) is not None:
        return  # it has joined an envelope since the question was asked: nothing to give
    if not sync.can_pull:
        raise ConflictError(
            f"In To {current().name} mode this book isn't changed. Switch to Both ways first."
        )
    env = sync.give_envelope_now(ycid, p, cat)
    figures = {k: v for k, v in sync.ynab_figures().items() if k[1] == ycid and v}
    sync.base.drop_assigned_of(ycid)
    for (month, cid), milli in figures.items():
        assigned.pull(assigned.Item(month, cid, env["id"], 0, 0, milli, "pull", False))
        sync.base.set_assigned(month, cid, milli)


def apply(item: dict, keep: str, choice: str | None, st: dict, meta: dict, base, client) -> None:
    sync = News(client, base, st, meta, Counter(), {})
    sync.quiet = True
    try:
        if item["entity"] == "category":
            _category(sync, item, keep)
        elif item["entity"] == "group":
            _group(sync, item, keep)
        elif item["entity"] == "budget":
            _budget(sync, item, keep)
        elif "ynab_id" in item:
            _choose(sync, item, keep, choice)
        else:
            _new_here(sync, item, keep)
    except RateCapReached:
        raise ConflictError(
            f"{current().name} can't take any more requests for now. Try again shortly."
        ) from None
    finally:
        sync.finish()
