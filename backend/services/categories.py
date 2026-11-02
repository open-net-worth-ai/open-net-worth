"""
categories_service — category lifecycle: create / relabel / merge / delete.

A category is an OPAQUE, IMMUTABLE id (``cat_<12hex>``, minted via ``core.ids.new_id`` — see
docs/adr/2026-07-05-category-opaque-ids.md) referenced across the whole app:

  * ``transactions[].category``            (the ledger — money data)
  * ``profile.monthly_budgets`` keys       (per-category caps — the legacy rollover system)
  * ``automations[].execution.set.category`` (transaction Automation action)

Assigned amounts and targets are envelope-id native (docs/adr/2026-07-05-envelope-id-native.md)
— a category rename/delete NEVER touches ``budget_assignments.json``/
``profile.category_targets`` directly; ``services.envelopes`` keeps the envelope's own
membership/money true instead (``on_category_renamed``/``on_category_deleted``).

Categories are PURE DATA stored via ``CategoryStore``. Seeded once on first boot from
``models.category_defs.STARTER_CATEGORIES`` (stable hardcoded ids); afterwards
fully user-owned. ``label`` is the ONLY human-readable field — an id carries no meaning and is
never derived from a label (no slug, no ``normalize_key``).

Money-data safety (the load-bearing reason this is a service, not a router/tool):

  * **Relabeling never re-keys.** The id is permanent from creation, so a relabel is a pure
    ``label`` update — it can never touch an amount or a reference elsewhere.
  * **Merging is an explicit fold**, never implicit: ``rename_category(category_id, merge_into=...)``
    folds ``category_id``'s data into an EXISTING, different category id and deletes ``category_id``'s row.
  * **Checkpoint-covered.** Every write goes through a store ``_save_raw`` (transactions,
    rules, categories) or ``budgets.write_profile_raw`` (profile) — both fire the
    ``core.io`` write hook, so a rename/delete inside an AI turn is undoable as one unit.

Only one id is STRUCTURAL (``STRUCTURAL_KEYS``: ``split``):
  * ``split`` — the sentinel a split txn carries (not pickable; the ONE category id that is a
    literal string rather than a minted ``cat_xxx`` — see ``models.category_defs``).
Category is OPTIONAL on expense/income/investment — None when unclassified; there is no
forced "uncategorized" sink. Every stored category (including former builtins) is deletable
with reassign (default: clear to None), exactly like a custom category.

Kind names (expense/income/transfer/adjustment/investment) are RESERVED and can never be
stored as a transaction's ``category`` value (``services.ledger._validate_reserved_category``)
— kinds and categories are disjoint namespaces. With opaque ids this collision is now also
structurally impossible (a minted id can never equal a kind name); ``_KIND_NAME_KEYS`` stays
as the shared constant other guards (``payee_registry``) still reserve against.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections import Counter

from sqlalchemy import Connection

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.db.category_store import CategoryStore
from backend.db.engine import locked_unit_of_work
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.tables import categories as categories_table
from backend.db.tables import envelope_groups as envelope_groups_table
from backend.db.tables import transactions as transactions_table
from backend.db.transaction_store import TransactionStore
from backend.services import budgets as budgets_service

logger = logging.getLogger(__name__)

# STRUCTURAL keys are load-bearing and can NEVER be renamed or deleted:
#   * ``split`` — the P3-SP sentinel a split txn carries (not pickable).
# All other categories (including former built-ins like food, transport, adjustment, …) are
# user-deletable with reassign — they are plain data rows in the store, not code anchors.
# Category is OPTIONAL on expense/income/investment — None means "unclassified" (no sink).
STRUCTURAL_KEYS: frozenset[str] = frozenset({"split"})

# Keys that may NOT be hidden — same as structural (the UI always needs the split sentinel).
_NOT_HIDEABLE: frozenset[str] = STRUCTURAL_KEYS

_VALID_KINDS = ("expense", "income", "transfer", "adjustment", "investment")

# Kind names — reserved against collision with a category value. With opaque ids a MINTED
# category id can never literally equal one of these, so this frozenset is no longer consulted
# by create/rename here; it stays as the shared constant `payee_registry._reserved_default_keys`
# and `services.ledger._validate_reserved_category` guard against (a caller-supplied RAW string,
# never a resolved id, could still literally be "income"/"transfer"/etc).
_KIND_NAME_KEYS: frozenset[str] = frozenset(
    {"expense", "income", "transfer", "adjustment", "investment"}
)

groups_store = EnvelopeGroupsStore()
transaction_store = TransactionStore()


def ensure_builtins_seeded() -> int:
    """Seed the starter categories and the starter envelope groups into an EMPTY store, once.

    A fresh book has no categories at all, and the ledger REFUSES an unknown category rather
    than coercing one — so anything that writes transactions into a fresh book has to run
    this first. The app's own boot does (``main.py`` lifespan); so does the demo builder,
    which writes a whole book without ever starting the app. Returns how many categories were
    seeded. The groups are empty headings — ``envelopes.ensure_envelopes_migrated`` fills them
    with the starter envelopes (``STARTER_GROUPS`` names which starter category goes where).
    """
    from backend.models.category_defs import STARTER_CATEGORIES, STARTER_GROUPS

    store = CategoryStore()
    with locked_unit_of_work(categories_table, envelope_groups_table) as conn:
        existing = store.all(conn=conn)
        if existing:
            # A book that lost the structural sentinel refuses every split write; restore it.
            if not any(r.get("id") in STRUCTURAL_KEYS for r in existing):
                store.bulk_insert(
                    [c for c in STARTER_CATEGORIES if c["id"] in STRUCTURAL_KEYS], conn=conn
                )
                logger.warning("restored the missing structural 'split' category")
            return 0
        store.bulk_insert(list(STARTER_CATEGORIES), conn=conn)
        if not groups_store.all(conn=conn):
            groups_store.bulk_insert(
                [
                    {
                        "id": new_id("grp"),
                        "name": name,
                        "collapsed": False,
                        "order": order,
                        "is_others": False,
                    }
                    for order, (name, _category_ids) in enumerate(STARTER_GROUPS)
                ],
                conn=conn,
            )
    logger.info("seeded %d starter categories", len(STARTER_CATEGORIES))
    return len(STARTER_CATEGORIES)


# ── Name → id resolution (the one real behavioral break of the opaque-id cutover) ────
# There is no longer a name→id resolver "for free" (an id is no longer the name). Any write
# path that may receive a human-readable label instead of an id — an LLM/import extracting text
# off a statement, a foreign CSV/QIF category column — resolves through here so a label the
# user already recognizes still works.


def all_category_rows() -> list[dict]:
    """Every category row (built-in + custom, hidden included) — a bulk caller (CSV/QIF
    import, bulk_create_transactions) reads this ONCE and passes it to repeated
    ``resolve_category_ref(rows=...)`` calls instead of re-reading the store per item."""
    return CategoryStore().all()


def resolve_category_ref(ref: str | None, *, rows: list[dict] | None = None) -> str | None:
    """Resolve ``ref`` — a category id OR a label (case-insensitive EXACT match) — to its
    store id. Returns None for a blank/unknown ``ref`` (never raises; callers decide whether
    an unresolved ref is fatal).

    ``rows`` lets a caller processing many rows (bulk import) pass a pre-loaded
    ``CategoryStore().all()`` once instead of re-reading the store per item."""
    ref = (ref or "").strip()
    if not ref:
        return None
    rows = rows if rows is not None else all_category_rows()
    for row in rows:
        if row.get("id") == ref:
            return ref
    # Label match. Labels are enforced unique at the write path (create/rename go through
    # _assert_unique_label), so a well-formed book has ≤1 match. The >1-match guard below stays
    # as defense for a legacy/pre-guard book or a direct store write: an ambiguous label must
    # NOT silently resolve to whichever row sorts first — that could book a txn/import under the
    # wrong-KIND category and corrupt taxability / the envelope fold. On >1 match return None
    # (caller treats it as unresolved: the write choke rejects an explicit unknown, an import
    # leaves it Uncategorized) and log so the ambiguity is visible.
    lowered = ref.lower()
    matches = [row.get("id") for row in rows if (row.get("label") or "").strip().lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "resolve_category_ref: %r matches %d categories by label — ambiguous, not resolving",
            ref,
            len(matches),
        )
    return None


def _all_ids() -> set[str]:
    """All category ids currently in the store."""
    return {r["id"] for r in CategoryStore().all()}


# ── Category listing + kind resolution ───────────────────────────────────────


def get_all_categories(include_hidden: bool = False) -> list[dict]:
    """Return all categories from the store, optionally including hidden ones.

    Service twin of api/ledger.get_all_categories. api/ledger delegates to this.
    After P2+P3, this is a pure store query — no profile fallback.
    """
    return CategoryStore().where(include_hidden=include_hidden)


def kind_for(category: str | None) -> str:
    """Return the transaction kind for a category id (falls back to 'expense').

    Pure store query after P2+P3.
    """
    category_id = (category or "").strip()
    entry = CategoryStore().get(category_id)
    if entry is not None:
        return entry.get("kind", "expense")
    return "expense"


def hidden_categories() -> set[str]:
    """Category ids currently marked hidden in the store."""
    return {r["id"] for r in CategoryStore().all() if r.get("hidden")}


# ── Create ────────────────────────────────────────────────────────────────────


def _assert_unique_label(label: str, *, exclude_id: str | None = None) -> None:
    """Refuse a category whose label (case-insensitively, trimmed) matches an existing
    category's. Labels are a flat, user-facing namespace, so two same-named categories are
    ambiguous — they'd also make ``resolve_category_ref`` unresolvable (it returns None on a
    label that matches >1 row). Hidden rows still occupy their name (hidden ≠ deleted), so
    they count. ``exclude_id`` skips the row being relabeled, making a rename to a category's
    own current label a no-op rather than a self-clash."""
    wanted = label.strip().lower()
    for row in CategoryStore().all():
        if row.get("id") == exclude_id:
            continue
        if (row.get("label") or "").strip().lower() == wanted:
            raise ConflictError(f"A category named '{label}' already exists")


# The app's own category palette (frontend budgetParts.NEWCAT_PALETTE). A category created
# WITHOUT a colour — by a Lucius tool, an import or a bare API call — gets one of these instead
# of the blank that renders as a grey chip (owner, 2026-09-25).
CATEGORY_PALETTE = (
    "#34b08a",
    "#3b86dd",
    "#e0a032",
    "#9a7fd0",
    "#22b8cf",
    "#d4537e",
    "#e2554f",
    "#1d9e75",
    "#bd7a1a",
)


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _pick_category_color(store: CategoryStore) -> str:
    """A palette colour for a category created without one: random among the LEAST-used
    colours, so a run of new categories comes out varied instead of piling onto one."""
    used = Counter((row.get("color") or "").lower() for row in store.all())
    fewest = min(used[c] for c in CATEGORY_PALETTE)
    return secrets.choice([c for c in CATEGORY_PALETTE if used[c] == fewest])


def create_category(
    label: str,
    kind: str = "expense",
    color: str = "",
) -> dict:
    """Create a category with a freshly minted opaque id (``cat_<12hex>``) — NO slug/category_id
    derivation from ``label``. Raises ValidationError on a blank label, an invalid kind or a non-hex colour (the swatch
    reaches the UI as a CSS value, so only ``#rrggbb`` is ever stored).

    A category is a pure label: it joins no group (groups hold ENVELOPES, never categories) and
    mints no envelope.
    """
    label = (label or "").strip()
    if not label:
        raise ValidationError("label is required")
    if kind not in _VALID_KINDS:
        raise ValidationError("kind must be expense, income, transfer, adjustment, or investment")
    _assert_unique_label(label)

    store = CategoryStore()
    cid = new_id("cat", exists=lambda c: store.get(c) is not None)

    color = (color or "").strip().lower()
    if color and not _HEX_COLOR_RE.match(color):
        raise ValidationError("color must be a 6-digit hex string like #3b86dd")
    color = color or _pick_category_color(store)
    entry: dict = {"id": cid, "label": label, "kind": kind, "color": color}

    # Envelope ⊥ category (RFC envelope-category-full-split.md §Backend 2): creating a
    # category NEVER mints an envelope (nor the reverse — see envelopes.create_envelope) and
    # never touches a group — a fresh category is a pure label with no budget row until a user
    # explicitly links it to an envelope. ``planning.sync_commitment_envelopes`` mints its OWN
    # zero-category envelopes directly (via ``envelopes.create_envelope``).
    store.insert(entry)

    logger.info("create_category id=%s label=%s kind=%s", cid, label, kind)
    return {"ok": True, **entry}


# ── Relabel / merge (the atomic choke point) ─────────────────────────────────


def rename_category(
    category_id: str, new_label: str | None = None, merge_into: str | None = None
) -> dict:
    """Relabel a category, or fold it into another (``merge_into``).

    With an opaque, IMMUTABLE id, this can never re-category_id anything — the id is permanent from
    creation. Exactly one of two things happens:

      * **Relabel** (``merge_into`` omitted): ``new_label`` becomes the row's ``label``. A
        same-named 1:1 envelope follows the label (``envelopes.on_category_relabeled``).
      * **Merge** (``merge_into`` given — an EXISTING, different category id): every reference
        to ``category_id`` — transactions (top-level + split lines), the legacy per-category budget
        cap, rule actions — is re-pointed to ``merge_into``; the envelope
        mapping folds via ``envelopes.on_category_renamed`` (assigned money/target move if
        ``category_id`` was its envelope's sole member); ``category_id``'s own row is then deleted.
        ``new_label`` is ignored on a merge — the destination's own label is authored intent,
        never overwritten by the loser's.

    The structural ``split`` id can never be renamed, merged, or a merge target
    (ValidationError). Raises NotFoundError if ``category_id`` (or ``merge_into``, when given) doesn't
    exist; ValidationError if ``merge_into == category_id``, or neither ``new_label`` nor
    ``merge_into`` is given.

    Returns ``{ok, category_id, label, merged: False}`` on a relabel, or
    ``{ok, category_id, merged_into, merged: True, counts}`` on a merge.
    """
    category_id = (category_id or "").strip()
    if not category_id:
        raise ValidationError("category_id is required")
    if category_id in STRUCTURAL_KEYS:
        raise ValidationError(f"'{category_id}' is a structural category and cannot be renamed")

    store = CategoryStore()
    existing = store.get(category_id)
    if existing is None:
        raise NotFoundError(f"Category '{category_id}' not found")

    merge_into = (merge_into or "").strip() or None

    if merge_into is None:
        label = (new_label or "").strip()
        if not label:
            raise ValidationError("new_label is required (or pass merge_into to merge)")
        _assert_unique_label(label, exclude_id=category_id)
        old_label = existing.get("label") or ""
        store.update_by_id(category_id, {"label": label})
        # A rename does not touch Automations: category ids are OPAQUE and stable, so an
        # Automation naming this id still names the right category. (This used to call
        # rewrite_category_refs(id, id) — a rewrite to the same value, whose only output was
        # the count logged below.)
        rewritten = 0
        # A same-named 1:1 envelope follows the label (owner catch, 2026-07-04).
        from backend.services import envelopes as _envelopes_service

        followed = _envelopes_service.on_category_relabeled(category_id, old_label, label)
        logger.info(
            "rename_category relabel %s label=%s envelope_followed=%s automations=%s",
            category_id,
            label,
            followed,
            rewritten,
        )
        return {"ok": True, "category_id": category_id, "label": label, "merged": False}

    if merge_into == category_id:
        raise ValidationError("cannot merge a category into itself")
    if merge_into in STRUCTURAL_KEYS:
        raise ValidationError(
            f"'{merge_into}' is a structural category and cannot be a merge target"
        )
    if store.get(merge_into) is None:
        raise NotFoundError(f"Category '{merge_into}' not found")

    # The two DB-table writes (transactions re-key, the losing
    # category row's delete) commit as ONE transaction (plan 4a task 5 — mirrors the 7 money
    # chokes in services/ledger.py). `_fold_budgets`/`_fold_automations` stay OUTSIDE: budgets
    # lives in profile.json (not a wave table this wave — still JsonStore) and automations is
    # ALSO still JsonStore this wave (the hybrid period, spec D7) — neither can join a DB
    # transaction. Sequenced after the block commits rather than interleaved with it (the
    # original interleaved order) so a failure inside the transaction leaves them untouched
    # too, instead of already-folded ahead of a re-key that then never landed.
    with locked_unit_of_work(categories_table, transactions_table) as conn:
        txn_count = _fold_transactions(category_id, merge_into, conn=conn)
        store.delete(category_id, conn=conn)

    counts = {
        "transactions": txn_count,
        "budgets": _fold_budgets(category_id, merge_into),
        "automations": _fold_automations(category_id, merge_into),
    }

    # Envelope mapping maintenance: the envelope holding `category_id` folds into `merge_into`'s
    # (assigned money/target move if `category_id` was its sole member — envelope-id native, see
    # envelopes.on_category_renamed). Runs AFTER the store row is gone so nothing resolves it —
    # and after the transaction above commits: envelopes+budget_assignments is a SEPARATE
    # locked_unit_of_work (services.envelopes' own choke), never nested inside this one.
    from backend.services import envelopes as envelopes_service

    counts["envelopes"] = envelopes_service.on_category_renamed(category_id, merge_into, True).get(
        "envelopes", 0
    )

    # A payee's default naming the merged-away id would point nowhere — it follows the merge.
    from backend.services import payee_registry

    payee_registry.on_category_deleted(category_id, reassign_to=merge_into)
    envelopes_service.on_income_category_folded(category_id, merge_into)

    logger.info("rename_category merge %s→%s counts=%s", category_id, merge_into, counts)
    return {
        "ok": True,
        "category_id": category_id,
        "merged_into": merge_into,
        "merged": True,
        "counts": counts,
    }


def _fold_transactions(old_key: str, new_key: str, *, conn: Connection | None = None) -> int:
    """Point every txn on ``old_key`` at ``new_key`` — the merge fold's ledger half.

    One bulk rewrite (checkpoint-covered). Also folds split lines for completeness. Called
    ONLY from a merge now (a plain relabel never touches a transaction — the id never
    changes). ``conn`` composes the write into a caller's own open transaction (choke
    composition, plan 4a task 5) — omitting it (``None``, the default) is byte-identical to
    before: the write opens its own lock-held transaction. The READ above (``.all()``) is
    always a separate, lock-free connection, same as before this task."""
    txns = transaction_store.all()
    n = 0
    for t in txns:
        changed = False
        if t.get("category") == old_key:
            t["category"] = new_key
            changed = True
        # Also fold any split lines that reference the old id.
        for s in t.get("splits") or []:
            if s.get("category") == old_key:
                s["category"] = new_key
                changed = True
        if changed:
            n += 1
    if n:
        transaction_store.replace_all(txns, conn=conn)
    return n


def _fold_budgets(old_key: str, new_key: str) -> int:
    """Move the per-category budget cap from old→new on a merge. The destination's existing
    cap wins (authored intent), so the source's cap is dropped rather than clobbering."""
    budgets = budgets_service.read_budgets()
    if old_key not in budgets:
        return 0
    src = budgets.pop(old_key)
    if new_key not in budgets:
        budgets[new_key] = src
    budgets_service.write_budgets(budgets)
    return 1


def _fold_automations(old_key: str, new_key: str) -> int:
    """Point every Automation that assigns ``old_key`` at the merge target.

    A merge is the one case where the id genuinely MOVES, so an Automation naming the old
    one must follow or it will assign a category that no longer exists. Since the migration
    to Lucius the id lives in the instructions PROSE, so this rewrites the text — narrowly,
    by exact id, which is safe because a category id is an opaque `cat_<12hex>` token that
    cannot collide with prose.
    """
    from backend.db.automation_store import AutomationStore

    store = AutomationStore()
    changed = 0
    for row in store.all():
        instructions = row.get("instructions") or ""
        if old_key not in instructions:
            continue
        store.update_by_id(row["id"], {"instructions": instructions.replace(old_key, new_key)})
        changed += 1
    return changed


def automations_naming_category(category_id: str) -> list[dict]:
    """Automations whose instructions assign ``category_id`` — `[{id, name}]`, newest last.

    Text search, because the migration to Lucius moved the id out of a structured
    ``execution.set`` field and into prose. An opaque `cat_<12hex>` id cannot occur by
    accident, so a substring hit is a real reference.
    """
    from backend.db.automation_store import AutomationStore

    if not category_id:
        return []
    return [
        {"id": row["id"], "name": row.get("name") or row["id"]}
        for row in AutomationStore().all()
        if category_id in (row.get("instructions") or "")
    ]


# ── Delete (in-use safe) ──────────────────────────────────────────────────────


def category_in_use(category_id: str) -> int:
    """How many non-deleted transactions reference ``category_id`` — top-level OR in a split line.

    Split rows carry ``category="split"`` at the top level, so a category used only inside
    split lines is invisible to a top-level-only check. Counting split-line usage here is what
    makes ``delete_category`` refuse (or reassign via ``_fold_transactions``) instead of
    silently deleting the row and orphaning that money out of every budget total.
    """
    category_id = (category_id or "").strip()
    n = 0
    for t in transaction_store.all():
        if t.get("deleted"):
            continue
        if t.get("category") == category_id or any(
            s.get("category") == category_id for s in (t.get("splits") or [])
        ):
            n += 1
    return n


def _carry_assigned_to_target(target: str | None, zeroed: dict[str, float]) -> float:
    """Move the assigned money a deleted category's envelope just released onto the reassign
    target's OWN envelope, for the CURRENT month only. Returns what actually moved (0.0 = the
    money stayed released to Ready to Assign, exactly as before this existed).

    The activity follows the reassign target, so the money set aside for it should follow too —
    that is what YNAB does, and releasing it to Ready to Assign instead made RTA jump by the
    deleted category's Assigned figure while the spending it was covering simply moved house.
    Only the current month moves: the deletion itself is a forward-state edit, and rewriting
    any OTHER month's assignments would be a retroactive edit of a budget nobody asked to
    change. Every other month — earlier or later alike — keeps releasing, as it always did.

    SIX ways this correctly carries NOTHING, every one of them leaving the money released to
    Ready to Assign (the behaviour before this existed) rather than failing the delete: no
    reassign target at all (the explicit "leave it Uncategorized" choice); a target category that
    is not linked to any envelope (unbudgeted — a pure transaction label with nowhere to hold
    money; inventing a pot for it is a different feature); an envelope that SURVIVED the delete
    because other categories still reference it (nothing was released — ``zeroed`` is empty);
    nothing assigned this month in the first place; a BOT-MANAGED destination; and a
    destination whose own assigned figure is more negative than the carry is big. The last two
    are guards, not impossibilities.

    Routes through ``planning.add_to_assigned`` — the ``assign`` choke — so this lands as an
    ordinary, checkpoint-covered assignment rather than a second writer of envelope-keyed
    money. It is a SEPARATE transaction from the release, which is the same residual window
    ``delete_category``'s automation rewrite already documents: a crash between the two leaves
    the money released to RTA, i.e. degraded to the old behaviour, never lost or doubled.

    The two guards are scars, not theory. A bot-managed destination (a card's payment envelope,
    a Commitments-synced one) is NOT structurally unreachable, as this docstring claimed while
    nothing enforced the zero-category property that claim rested on: linking a category onto
    one and then deleting another category into it dropped user money inside a pot the app
    funds by computation, corrupting that card's coverage math and RTA.
    ``envelopes.add_category`` now refuses the link at the root; this guard stays because a
    book may already hold such a row, and a money write may not rest on a convention it does
    not enforce itself. A destination already BELOW zero (a ``move_money`` pull-back artifact,
    a legitimate state per ``_op_move_money``) cannot take the carry at all: ``assign`` refuses
    a negative figure, and lifting the slot to zero instead would invent the difference — money
    the deleted envelope never held. So the carry is skipped WHOLE (``skip_if_negative``) and
    stays released; a part of it small enough to fit could only exist if all of it fitted. That
    case used to RAISE here, AFTER ``on_category_deleted`` had committed — leaving the category
    alive with its envelope already gone, until the identical delete was retried."""
    if not target:
        return 0.0
    from backend.services import envelopes as envelopes_service
    from backend.services import planning as planning_service

    month = planning_service._this_month()
    amount = round(zeroed.get(month, 0.0), 2)
    if amount <= 0:
        return 0.0
    destination = envelopes_service.env_of().get(target)
    if destination is None or destination in envelopes_service.bot_managed_envelope_ids():
        return 0.0
    landed = planning_service.add_to_assigned(
        destination, amount, month=month, skip_if_negative=True
    )
    if landed is None:
        logger.info(
            "delete_category released assigned=%s — envelope=%s cannot absorb it (month=%s)",
            amount,
            destination,
            month,
        )
        return 0.0
    logger.info(
        "delete_category carried assigned=%s to envelope=%s month=%s", amount, destination, month
    )
    return amount


def delete_category(
    category_id: str, reassign: bool = False, reassign_to: str | None = None
) -> dict:
    """Delete a category and scrub it from budgets/targets/assignments/rules.

    The structural ``split`` id can NEVER be deleted (ValidationError). Every other category
    row IS deletable with reassign — deletion REMOVES the store row (not a deleted_builtins
    append), so a deleted category is genuinely gone from classification and never resurrected.

    In-use safety: if transactions still reference it, REFUSE (ConflictError) unless
    ``reassign=True`` — then those transactions are re-pointed to ``reassign_to`` (default:
    None, i.e. cleared to "unsorted"). Missing category_id → NotFoundError.

    Rule cleanup: a rule whose only action set this category is DELETED entirely (no-op zombie).
    A rule that also sets tags or note_append is DISABLED so the user can decide.

    Money follows the reassignment: when ``reassign=True`` names a real, envelope-backed
    ``reassign_to``, THIS MONTH's assigned amount on the deleted category's emptied envelope
    moves onto the target's envelope instead of being released to Ready to Assign
    (``_carry_assigned_to_target`` — see it for the six cases where nothing moves and it
    releases as before). It can never fail the delete, and ``reassigned_to`` in the response
    names the target whenever anything — transactions or money — actually went there.

    Returns {ok, category_id, reassigned, in_use, reassigned_to, carried_assigned}.
    """
    category_id = (category_id or "").strip()
    if category_id in STRUCTURAL_KEYS:
        raise ValidationError(f"'{category_id}' is a structural category and cannot be removed")

    store = CategoryStore()
    if store.get(category_id) is None:
        raise NotFoundError(f"Category '{category_id}' not found")

    target: str | None = (reassign_to or "").strip() or None
    if target in STRUCTURAL_KEYS:
        raise ValidationError(
            f"Cannot reassign transactions to the structural category_id '{target}'"
        )
    if target == category_id:
        raise ValidationError("Cannot reassign a category to itself")
    if target is not None and store.get(target) is None:
        raise ValidationError(f"Reassign target '{target}' is not an existing category")

    in_use = category_in_use(category_id)
    if in_use and not reassign:
        raise ConflictError(
            f"'{category_id}' is used by {in_use} transaction(s). "
            f"Pass reassign=true to move them to '{target}' before deleting."
        )

    # An Automation that assigns this category REFUSES the delete, the same way a referencing
    # transaction does. Rules used to hold the id in a structured `execution.set` field, which
    # delete could rewrite; migrated to Lucius (2026-08-02) the id lives in the instructions
    # PROSE, and nothing can rewrite that safely. Left alone, the Automation keeps naming a
    # dead id and its next run calls update_transactions with an unknown category — which
    # RAISES by invariant — so it fails on every matching transaction from then on, with
    # nothing said at delete time. Refusing names them while they can still be edited.
    naming = automations_naming_category(category_id)
    if naming and not reassign:
        listed = ", ".join(f"{row['name']!r}" for row in naming[:5])
        more = f" (+{len(naming) - 5} more)" if len(naming) > 5 else ""
        raise ConflictError(
            f"'{category_id}' is assigned by {len(naming)} Automation(s): {listed}{more}. "
            f"Pass reassign to move them (a target re-points them; no target pauses them), "
            "or edit them first — otherwise their next run fails on an unknown category."
        )

    # Scrub config references (no merge target — these are just removed). Assigned amounts
    # and targets are envelope-id native — nothing is ever stored under a bare category category_id,
    # so there is no assignment/target scrub here; ``envelopes.on_category_deleted`` below
    # releases the envelope's OWN (env-keyed) assigned money and target.
    budgets = budgets_service.read_budgets()
    if budgets.pop(category_id, None) is not None:
        budgets_service.write_budgets(budgets)

    # Envelope mapping maintenance (envelope≠category flip): detach the category_id from its
    # envelope; an emptied envelope is archived with its env-keyed assigned amounts zeroed
    # (money released back to RTA — the env-keyed twin of the category scrub above). Runs
    # BEFORE the categories/transactions transaction below — UNCHANGED
    # relative order from before this task, deliberately preserved: a crash between the two
    # leaves a harmless "zombie" category row (envelope already cleaned, nothing still
    # references it); the reverse order would risk a dangling envelope reference to an
    # already-deleted category, which is worse. envelopes+budget_assignments is a SEPARATE
    # locked_unit_of_work (services.envelopes' own choke) — this call is sequential, never
    # nested inside the one below (Task-2 lesson: composing a store write that is ALSO
    # reachable from another open transaction self-deadlocks on SQLite).
    from backend.services import envelopes as envelopes_service

    released = envelopes_service.on_category_deleted(category_id)
    # ...and then follow the money to wherever the ACTIVITY just went, rather than dropping it
    # into Ready to Assign behind the user's back — but ONLY when the caller actually asked for
    # a reassignment. `reassign_to` on its own is a defensive argument, not an instruction (the
    # `delete_categories` tool documents it as mattering only alongside `reassign`), and acting
    # on it produced a response that contradicted itself: money moved to a target the very same
    # response reported as `reassigned_to: None`.
    carried = _carry_assigned_to_target(target, released.get("zeroed") or {}) if reassign else 0.0

    # The two DB-table writes (transaction re-key, THE CORE FIX — remove the row from the
    # store, deletion is real, not a suppress-list append) commit as ONE transaction (plan 4a
    # task 5 — mirrors the 7 money chokes in services/ledger.py).
    with locked_unit_of_work(categories_table, transactions_table) as conn:
        reassigned = _fold_transactions(category_id, target, conn=conn) if in_use else 0
        store.delete(category_id, conn=conn)

    # Follow the reassignment into the Automations too. Same rewrite the merge path uses — a
    # reassign IS a merge from the Automation's point of view: the category it names is going
    # away and its work should continue against the target. Stays a SEPARATE write, ordered
    # AFTER the transaction above commits — automations is still a JsonStore file this wave
    # (the hybrid period, spec D7), so it can never join that transaction.
    #
    # RESIDUAL ATOMICITY WINDOW (coordinator-approved to keep this wave, tracked for
    # closure): a crash between the transaction above committing and THIS write leaves the
    # category re-key (categories/transactions) committed while the automation
    # instruction-rewrite is not yet applied — a category rule still naming the now-deleted
    # id. Small and self-healing (the rule mis-fires once on its next run, gets noticed and
    # fixed) and strictly no worse than today's fully-separate writes — this wave is a net
    # improvement, not a regression. Tracked: make bug MSG="planning cat-delete: fold the
    # automation-instruction rewrite into the category re-key's locked_unit_of_work once
    # automations is DB-backed (Wave D) — residual hybrid-period window from plan 4a T5" (see
    # task-5 report for the bug id).
    if naming:
        if target:
            _fold_automations(category_id, target)
        else:
            # Nothing to re-point at: the category it assigned is simply gone. PAUSE rather
            # than delete — the match conditions and the rest of the instructions are the
            # owner's work, worth keeping to edit — and rather than leave it active, where
            # its next run would fail on an unknown category.
            from backend.db.automation_store import AutomationStore

            automation_store = AutomationStore()
            for row in naming:
                automation_store.update_by_id(row["id"], {"status": "paused"})

    # A payee's default naming the deleted category would point nowhere — re-point it where the
    # transactions went (only when asked to reassign), else clear it. Best-effort, never raises.
    from backend.services import payee_registry

    payee_registry.on_category_deleted(category_id, reassign_to=target if reassign else None)
    # ...and so does the 50/30/20 engine's counted-income list, but only onto an INCOME
    # target — the list is income-kind by contract (proportional_config).
    carry_to = target if reassign and target and kind_for(target) == "income" else None
    envelopes_service.on_income_category_folded(category_id, carry_to)

    logger.info(
        "delete_category category_id=%s reassigned=%s to=%s", category_id, reassigned, target
    )
    return {
        "ok": True,
        "category_id": category_id,
        "reassigned": reassigned,
        # Wherever this delete actually ROUTED something — the transactions (``in_use``) or
        # the assigned money (``carried``). None means nothing went anywhere, so it can never
        # sit beside a non-zero ``carried_assigned``.
        "reassigned_to": target if (in_use or carried) else None,
        "in_use": in_use,
        "carried_assigned": carried,
    }


# ── Hide / unhide (presentational — does NOT touch transactions or math) ──────


def set_category_hidden(category_id: str, hidden: bool) -> dict:
    """Hide or unhide a category by updating its ``hidden`` field in the store.

    * The structural ``split`` id cannot be hidden (raises ``ValidationError``).
    * The id must exist in the store — raises ``NotFoundError`` otherwise.
    * Hiding has **no effect on data**: transactions, assignments, budgets, and all ledger
      math are untouched. Checkpoint-covered (store write fires ``core.io.fire_write_hook``).

    Returns ``{ok, category_id, hidden}``.
    """
    category_id = (category_id or "").strip()
    if not category_id:
        raise ValidationError("category_id is required")
    if category_id in _NOT_HIDEABLE:
        raise ValidationError(f"'{category_id}' is a structural category and cannot be hidden")

    store = CategoryStore()
    if store.get(category_id) is None:
        raise NotFoundError(f"Category '{category_id}' not found")

    store.update_by_id(category_id, {"hidden": hidden})
    logger.info("set_category_hidden category_id=%s hidden=%s", category_id, hidden)
    return {"ok": True, "category_id": category_id, "hidden": hidden}


# ── Color ─────────────────────────────────────────────────────────────────────


def set_category_color(category_id: str, color: str) -> dict:
    """Persist a category's swatch colour in the store.

    Works for any category — there is no longer a distinction between built-in overrides
    and custom-entry colors. The ``color`` field is updated directly on the store row.

    ``color`` must be a 6-digit hex string (``#3b86dd``) — ``ValidationError`` otherwise.
    Unknown id → ``NotFoundError``. Checkpoint-covered (store write fires the write hook).

    Returns ``{ok, category_id, color}``.
    """
    category_id = (category_id or "").strip()
    if not category_id:
        raise ValidationError("category_id is required")
    color = (color or "").strip().lower()
    if not _HEX_COLOR_RE.match(color):
        raise ValidationError("color must be a 6-digit hex string like #3b86dd")

    store = CategoryStore()
    if store.get(category_id) is None:
        raise NotFoundError(f"Category '{category_id}' not found")

    store.update_by_id(category_id, {"color": color})
    logger.info("set_category_color category_id=%s color=%s", category_id, color)
    return {"ok": True, "category_id": category_id, "color": color}
