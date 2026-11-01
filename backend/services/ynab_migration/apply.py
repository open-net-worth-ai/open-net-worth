"""The WRITE half of the YNAB migration. Every write goes through the app's own choke points —
``accounts.create_account``, ``planning.apply_budget_change``, ``ledger.bulk_create_transactions`` —
never a store directly, so it is validated and audited like a hand edit. It is NOT inside a Lucius
turn, so there is no turn checkpoint: ``take_backup`` is the undo, and the job takes it first.

Idempotent by construction: accounts are found by their YNAB ``external_ids`` link, transactions
skip any YNAB ref already in the ledger (the importer's own scheme), envelopes are found by name,
assignments are only written where they differ.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from typing import Callable

from backend.core.errors import ConflictError, OpenNetWorthError
from backend.core.txn_sources import canonical_writer
from backend.services import accounts as accounts_service
from backend.services import backup as backup_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services import profile as profile_service
from backend.services.ynab_migration.mapping import SOURCE, render_piece

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, int], None]
TOL = 0.005
CHUNK = 500


def _change(**change) -> dict:
    return planning.apply_budget_change(change)


def book_state(plan: dict) -> tuple[str, list[dict]]:
    """``fresh`` (no accounts), ``rerun`` (every account came from this plan), else ``foreign``."""
    existing = ledger_service.accounts.query_accounts(include_archived=True)
    mine = {a["id"] for a in plan["accounts"]}
    if not existing:
        return "fresh", existing
    foreign = [
        a
        for a in existing
        if not any(
            e.get("source") == SOURCE and e.get("ref") in mine for e in a.get("external_ids") or []
        )
    ]
    return ("foreign" if foreign else "rerun"), existing


def take_backup() -> str:
    """A full server-side backup before the first write; the migration does not start without one."""
    result = backup_service.run_backup(force=True, trigger="manual")
    if "already exists" in str(result.get("error")):  # names are second-resolution: a re-run
        time.sleep(1.1)
        result = backup_service.run_backup(force=True, trigger="manual")
    if not result.get("ok"):
        raise ConflictError(
            "Could not take a backup first, so nothing was changed "
            f"({result.get('error') or result.get('skipped') or 'unknown error'})."
        )
    return result["name"]


def ensure_base_currency(currency: str, money: list[str]) -> str | None:
    """Adopt the plan's currency only in a book with no money data; otherwise it must match."""
    base = ledger_service.base_currency().upper()
    if base == currency:
        return None
    if money:
        raise ConflictError(
            f"This book's base currency is {base} but the YNAB plan is {currency}, and the book "
            f"already holds {', '.join(money)}. Change the base currency in Settings first, or "
            "import into an empty book."
        )
    profile = profile_service.read_profile()
    profile["base_currency"] = currency
    profile_service.handle_base_currency_change(profile, base, currency)
    profile_service.write_profile(profile)
    return f"Base currency set from {base} to {currency}."


def sync_accounts(plan: dict, existing: list[dict]) -> tuple[dict[str, dict], int]:
    linked = {
        e["ref"]: a
        for a in existing
        for e in a.get("external_ids") or []
        if e.get("source") == SOURCE
    }
    rows, created = {}, 0
    for pa in plan["accounts"]:
        row = linked.get(pa["id"])
        if row is None:
            row = accounts_service.create_account(dict(pa["payload"]))
            created += 1
        rows[pa["id"]] = row
    return rows, created


def archive_closed(plan: dict, rows: dict[str, dict]) -> int:
    done = 0
    for pa in plan["accounts"]:
        row = rows[pa["id"]]
        if pa["closed"] and not row.get("archived"):
            accounts_service.update_account(row["id"], {"archived": True})
            done += 1
    return done


def resolve_envelopes(plan: dict, create: bool) -> tuple[dict[str, dict], Counter]:
    """YNAB category id -> {category, envelope}; creates (or reuses by name) when ``create``."""
    cats = {
        c["label"].lower(): c for c in categories_service.all_category_rows() if c["id"] != "split"
    }
    envs = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
    env_of = {cid: e for e in envs for cid in e.get("category_ids") or []}
    env_named = {e["name"].lower(): e for e in envs}
    groups = {g["name"].lower(): g for g in planning.list_groups()}
    out: dict[str, dict] = {}
    made: Counter = Counter()
    for c in plan["categories"]:
        cat = cats.get(c["label"].lower())
        if cat is None and create:
            cat = _change(type="category_create", label=c["label"], kind="expense")
            made["categories"] += 1
        group = groups.get(c["group"].lower())
        if group is None and create:
            group = _change(type="group_create", name=c["group"])["group"]
            groups[c["group"].lower()] = group
            made["groups"] += 1
        env = env_of.get(cat["id"]) if cat else None
        if cat and env is None and create:
            env = env_named.get(c["label"].lower())
            if env is None or env.get("category_ids"):
                env = _change(type="envelope_create", name=c["label"], group_id=group["id"])[
                    "envelope"
                ]
                made["envelopes"] += 1
            _change(type="envelope_add_category", envelope_id=env["id"], category_id=cat["id"])
            env_of[cat["id"]] = env
        if env and group and create and env.get("group_id") != group["id"]:
            _change(type="move_category", category=env["id"], group_id=group["id"])
        if cat and create and c["hidden"] and not cat.get("hidden"):
            categories_service.set_category_hidden(cat["id"], True)
        out[c["id"]] = {"category": cat and cat["id"], "envelope": env and env["id"]}
    return out, made


def prune_starter(prune: dict, groups: list[dict]) -> Counter:
    """Remove the untouched starter items ``guards.starter_prune_plan`` listed (and the starter
    groups they leave empty). Each removal is best-effort: anything the app refuses stays."""
    removed: Counter = Counter()

    def attempt(counter: str, **change) -> None:
        try:
            _change(**change)
            removed[counter] += 1
        except OpenNetWorthError:
            logger.info("starter item kept: %s", change)

    for c in prune["categories"]:
        attempt("categories", type="category_delete", key=c["id"])
    gone = {e["id"] for e in envelopes_service.all_envelopes() if not e.get("archived")}
    for env_id in prune["envelopes"]:
        if env_id in gone:
            attempt("envelopes", type="envelope_delete", envelope_id=env_id)
    for g in groups:
        attempt("groups", type="group_delete", group_id=g["id"])
    return removed


def _book_chunk(rows: list[dict], batch_id: str, tally: Counter, problems: list[dict]) -> None:
    try:
        # Every row carries its own YNAB id, so the ref floor is the dedup; the fuzzy matcher
        # refused genuine same-day, same-amount twins (two 50.00 transfers on one date).
        created, skipped = ledger_service.bulk_create_transactions(
            rows,
            batch_id=batch_id,
            force=True,
            dedup_by_external_ref=True,
            labels_as_given=True,
        )
    except OpenNetWorthError as exc:
        if len(rows) == 1:
            logger.warning("ynab migration row refused: %s", exc.message)
            tally["failed"] += 1
            problems.append(
                {"ref": rows[0]["external_refs"][0]["ref"], "reason": exc.message, "kind": "failed"}
            )
            return
        mid = len(rows) // 2
        _book_chunk(rows[:mid], batch_id, tally, problems)
        _book_chunk(rows[mid:], batch_id, tally, problems)
        return
    tally["created"] += len(created)
    for sk in skipped:
        if sk.get("_previously_deleted"):
            key = "previously_deleted"
        elif sk.get("_duplicate_reason"):
            key = "refused_duplicate"
        else:
            tally["already_present"] += 1
            continue
        tally[key] += 1
        problems.append(
            {
                "ref": sk["external_refs"][0]["ref"],
                "reason": sk.get("_duplicate_reason", ""),
                "kind": key,
            }
        )


def send_transactions(
    plan: dict,
    acct_ids: dict[str, str],
    cat_ids: dict[str, str],
    progress: Progress,
) -> tuple[Counter, list[dict]]:
    """Render and book one chunk at a time, so only a chunk of payloads is ever in memory."""
    source = canonical_writer(SOURCE) or SOURCE
    pieces = plan["pieces"]
    batch_id, tally, problems = str(uuid.uuid4()), Counter(), []
    for i in range(0, len(pieces), CHUNK):
        rows = [
            {**render_piece(p, acct_ids, cat_ids, plan["currency"]), "source": source}
            for p in pieces[i : i + CHUNK]
        ]
        _book_chunk(rows, batch_id, tally, problems)
        progress("booking transactions", min(i + CHUNK, len(pieces)), len(pieces))
    return tally, problems


def mark_cleared(plan: dict) -> int:
    """Rows are booked with their cleared flag; this only catches up rows an older run booked
    uncleared. One write per row, so it must stay a catch-up, never the main path."""
    want = {r for p in plan["pieces"] if p["cleared"] for r in p["refs"]}
    ids = [
        t["id"]
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and not t.get("cleared")
        and any(
            x.get("source") == SOURCE and x.get("ref") in want for x in t.get("external_refs") or []
        )
    ]
    for i in range(0, len(ids), CHUNK):
        ledger_service.bulk_update_transactions(ids[i : i + CHUNK], {"cleared": True})
    return len(ids)


def month_rows(month: str) -> tuple[dict, dict[str, dict]]:
    table = planning.budget_month(month)
    rows = {
        r["envelope_id"]: r
        for g in table["groups"]
        for r in g["categories"]
        if r.get("envelope_id")
    }
    return table, rows


def card_envelopes(plan: dict) -> dict[str, dict]:
    """YNAB card payment category id -> the card's own payment envelope here. YNAB names the
    category after its card, so the match is by the YNAB account name each account keeps."""
    by_name = {
        e.get("name"): a.get("payment_envelope_id")
        for a in ledger_service.accounts.query_accounts(include_archived=True)
        for e in a.get("external_ids") or []
        if e.get("source") == SOURCE and a.get("payment_envelope_id")
    }
    return {
        cid: {"category": None, "envelope": by_name[name]}
        for cid, name in plan.get("card_categories", {}).items()
        if name in by_name
    }


def apply_budget(
    plan: dict, env_ids: dict[str, dict], progress: Progress
) -> tuple[Counter, list[dict]]:
    """Per month, per envelope: set Assigned to YNAB's budgeted, negatives included — YNAB lets
    a month's Assigned go below zero (money moved out), and ``move_money`` cannot reproduce that
    past what the envelope had available, so the figure is written as-is. Counts the figures
    written (``created``), already right (``existing``) and refused (``failed``)."""
    tally: Counter = Counter()
    problems: list[dict] = []
    months = sorted(plan["assigned"])
    for n, month in enumerate(months, 1):
        _, rows = month_rows(month)
        for cid, milli in plan["assigned"][month].items():
            env = env_ids.get(cid, {}).get("envelope")
            if not env or env not in rows:
                continue
            target = round(milli / 1000, 2)
            if abs(target - rows[env]["assigned"]) < TOL:
                tally["existing"] += 1
                continue
            try:
                planning.apply_budget_change(
                    {"type": "assign", "month": month, "envelope_id": env, "amount": target},
                    allow_negative=True,
                )
                tally["created"] += 1
            except OpenNetWorthError as exc:
                tally["failed"] += 1
                problems.append({"ref": month, "reason": exc.message, "kind": "assigned"})
        progress("setting monthly assignments", n, len(months))
    return tally, problems
