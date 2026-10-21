"""Monthly Assigned, both ways. Per (month, YNAB category): the figure both sides held at the
last sync (the baseline), the ledger's figure now, and YNAB's figure now. The same rule as
transactions (``units.decide``) decides, over whole cents.

Card payment categories are YNAB's to set: their Assigned is read from YNAB and never sent.
Each Assigned the sync sends leaves a dated line in that YNAB category's note, keeping the last
ten such lines and the rest of the note as the person wrote it."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from backend.core.errors import ExternalServiceError
from backend.core.user_time import user_today
from backend.services import planning
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync.client import YnabClient
from backend.services.ynab_sync.context import Context
from backend.services.ynab_sync.units import decide

NOTE_LINE = re.compile(r"^ONW \d{4}-\d{2}: assigned ")
NOTE_LINES = 10
NOTE_LIMIT = 500


@dataclass
class Item:
    month: str
    cid: str
    env: str
    base: int  # milliunits
    onw: int
    ynab: int
    action: str
    card: bool

    @property
    def key(self) -> str:
        return f"a:{self.month}:{self.cid}"


def _cents(milli: int) -> int:
    return round(milli / 10)


def note_with(note: str | None, line: str) -> str | None:
    """``note`` with ``line`` appended, our own lines capped; ``None`` when it cannot fit."""
    lines = (note or "").split("\n")
    theirs = "\n".join(ln for ln in lines if not NOTE_LINE.match(ln)).rstrip()
    ours = ([ln for ln in lines if NOTE_LINE.match(ln)] + [line])[-NOTE_LINES:]
    while ours and len("\n".join(filter(None, [theirs, *ours]))) > NOTE_LIMIT:
        ours = ours[1:]
    return "\n".join(filter(None, [theirs, *ours])) if line in ours else None


def _unique_envelopes(ctx: Context) -> dict[str, str]:
    """YNAB category -> envelope, skipping envelopes two categories share (their one Assigned
    figure cannot be split between them)."""
    users = Counter(ctx.env_of.values())
    return {cid: env for cid, env in ctx.env_of.items() if users[env] == 1}


def _in_window(month: str, known: list[str]) -> bool:
    today = user_today()
    ahead = f"{today.year + today.month // 12:04d}-{today.month % 12 + 1:02d}"
    return month in known or (bool(known) and known[0] <= month <= ahead)


def evaluate(
    ctx: Context,
    baseline: dict,
    seen: dict,
    onw_all: dict[str, dict[str, float]],
    first: bool = False,
) -> list[Item]:
    """The Assigned entries that need acting on this run. On the first run nothing is pushed: YNAB
    may have changed since the migration, so every difference is for the person to settle."""
    plain, card = _unique_envelopes(ctx), ctx.card_env
    by_env = {env: cid for cid, env in {**plain, **card}.items()}
    keys = set(baseline) | set(seen)
    keys |= {(m, by_env[e]) for m, envs in onw_all.items() for e in envs if e in by_env}
    items = []
    for month, cid in sorted(keys):
        env = plain.get(cid) or card.get(cid)
        if not env:
            continue
        b = baseline.get((month, cid), 0)
        o = round(onw_all.get(month, {}).get(env, 0.0) * 1000)
        y = seen.get((month, cid), b)
        is_card = cid in card
        action = decide(_cents(b), _cents(b) if is_card else _cents(o), _cents(y))
        if action == "push" and not _in_window(month, ctx.meta["months"]):
            continue
        if action == "push" and first:
            action = "conflict"
        if action != "noop":
            items.append(Item(month, cid, env, b, o, y, action, is_card))
    return items


def pull(item: Item) -> None:
    """Set the envelope's Assigned to YNAB's figure (negatives included, as the migration does)."""
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": item.month,
            "envelope_id": item.env,
            "amount": item.ynab / 1000,
        },
        allow_negative=True,
    )


def line_for(item: Item) -> str:
    return f"ONW {item.month}: assigned {item.onw / 1000:.2f} (was {item.base / 1000:.2f})"


def push(
    client: YnabClient, items: list[Item], categories: dict[str, dict]
) -> tuple[list[Item], list[Item], list[tuple[Item, str]]]:
    """Send the ledger's figures; returns (sent, deferred past the request budget, rejected).
    A category's note line is written once per category, after its months."""
    sent, deferred, rejected = [], [], []
    by_cat: dict[str, list[Item]] = {}
    for item in items:
        by_cat.setdefault(item.cid, []).append(item)
    for cid, group in by_cat.items():
        if client.budget.left() < len(group) + 1:
            deferred += group
            continue
        done = []
        for item in group:
            try:
                client.set_assigned(item.month, item.cid, item.onw)
                done.append(item)
            except ExternalServiceError as exc:
                if not ynab_client.rejected(exc):
                    raise
                rejected.append((item, str(exc)))
        sent += done
        _write_note(client, categories[cid], [line_for(i) for i in done])
    return sent, deferred, rejected


def _write_note(client: YnabClient, category: dict, lines: list[str]) -> None:
    note = category.get("note")
    for line in lines:
        note = note_with(note, line) or note
    if lines and note != category.get("note"):
        category["note"] = client.set_note(category["id"], note).get("note", note)
