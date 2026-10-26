"""payee_registry — entity logic for first-class payees (P4-PAYEE phase 1 + 2).

Owns resolve-or-create, default-category learning, usage stats, the registry
read projection, and the Phase-2 management operations (rename, update, merge,
delete, alias management) — so the ledger choke point stays readable and this
module is unit-testable in isolation.

Layer: services. Imports ONLY ``db`` (``PayeeStore``, ``TransactionStore``) +
``services.payees`` (``_normalize_payee``) + ``core.errors``. No ``api``/``ai``.

Phase-1: ``autocategorize`` is always on and ``hidden`` is stored but unused in UI.
Phase-2: Full management surface (rename/merge/delete/alias) shipped here.
"""

from __future__ import annotations

import re as _re

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.payee_store import PayeeStore
from backend.db.transaction_store import txn_text
from backend.services.payees import _normalize_payee

_VALID_MATCH_TYPES = frozenset({"contains", "is", "regex"})

# Kinds that can carry a per-kind payee default (adjustment never has a category).
_DEFAULT_CATEGORY_KINDS: tuple[str, ...] = ("expense", "income", "transfer", "investment")

# Module-level singleton store (cheap; JsonStore reads lazily per call).
_store = PayeeStore()


def normalize_default_categories(mapping: dict | None, *, category_rows=None) -> dict:
    """Coerce a per-kind default-category map into stored form: keep only the four
    categorizable kinds, resolve each value (id OR label) to a stored category id, and
    drop blanks / unknown ids / reserved (structural / kind-name) keys. Never raises."""
    from backend.services import categories as categories_service

    reserved = _reserved_default_keys()
    out: dict[str, str] = {}
    for kind in _DEFAULT_CATEGORY_KINDS:
        ref = (mapping or {}).get(kind)
        if not ref:
            continue
        resolved = categories_service.resolve_category_ref(ref, rows=category_rows)
        if resolved and resolved not in reserved:
            out[kind] = resolved
    return out


def _reserved_default_keys() -> frozenset[str]:
    """Keys that may never become a payee default: structural categories (split sentinel,
    kind-fold anchors, the uncategorized sink) plus transaction KIND names — a default of
    'income'/'transfer' would re-inject a kind name as a category via the payee-default
    fill in create_transaction, bypassing the choke point's reserved-category guard.
    Imported lazily to avoid a heavy import at module load and any import cycle
    through the categories service."""
    from backend.services.categories import _KIND_NAME_KEYS, STRUCTURAL_KEYS

    return STRUCTURAL_KEYS | _KIND_NAME_KEYS


def _assert_name_available(name: str, *, exclude_id: str | None = None) -> None:
    """Raise ``ConflictError`` if another payee already answers to ``name``.

    Uniqueness is on the NORMALIZED form and spans both indexes a user sees a payee
    by: the other entities' display names AND their aliases (an alias is what
    resolves a bank description to the entity, so a name matching someone else's
    alias is just as indistinguishable in autocomplete). Blank-normalizing names are
    left alone — the caller's non-empty check owns that.

    NOT applied to the auto-mint path (``resolve_payee_id``): minting is on the
    ledger write path and must never fail, and it cannot duplicate anyway — it looks
    the normalized key up in the alias index first.
    """
    norm = _normalize_payee(name)
    if not norm:
        return
    for other in _store.all():
        if exclude_id and other["id"] == exclude_id:
            continue
        if norm == _normalize_payee(other.get("name") or "") or norm in (
            other.get("aliases") or []
        ):
            raise ConflictError(
                f"a payee whose normalized name matches already exists: "
                f"'{other.get('name', other['id'])}'"
            )


def _display_name(description: str) -> str:
    """The name a freshly-minted payee shows. Phase-1 decision: keep the raw
    description (trimmed) — Phase-2 rename cleans it up. Falls back to the normalized
    key if the raw description is blank."""
    raw = (description or "").strip()
    return raw or _normalize_payee(description)


def _match_rule(description: str, rule: dict) -> bool:
    """Case-insensitive rule match against the raw description."""
    raw = (description or "").lower()
    val = (rule.get("value") or "").lower()
    if not val:
        return False
    mt = rule.get("match_type", "contains")
    if mt == "is":
        return raw == val
    if mt == "contains":
        return val in raw
    if mt == "regex":
        try:
            return bool(_re.search(val, raw, _re.IGNORECASE))
        except _re.error:
            return False
    return False


def _find_by_rules(description: str) -> str | None:
    """Scan all payees' raw rules; return first matching payee_id."""
    if not description:
        return None
    for payee in _store.all():
        for rule in payee.get("rules") or []:
            if _match_rule(description, rule):
                return payee["id"]
    return None


def _validate_rule(rule: dict) -> None:
    mt = rule.get("match_type")
    if mt not in _VALID_MATCH_TYPES:
        raise ValidationError(f"match_type must be one of: {sorted(_VALID_MATCH_TYPES)}")
    val = (rule.get("value") or "").strip()
    if not val:
        raise ValidationError("rule value must be non-empty")
    if mt == "regex":
        try:
            _re.compile(val)
        except _re.error as exc:
            raise ValidationError(f"invalid regex: {exc}") from exc


def resolve_payee_id(description: str, *, create: bool = True) -> str | None:
    """Resolve a raw transaction ``description`` to a payee id.

    Normalizes via ``_normalize_payee``; returns the existing payee whose aliases
    contain that key, else (when ``create``) mints a new payee seeded with that alias
    and returns its id. Returns None for a blank/normalizes-to-empty description (e.g.
    a bare amount) or when ``create=False`` and nothing matches. Never raises.
    """
    try:
        description = (description or "").strip()
        if not description:
            return None
        # Step 1: user-defined raw-match rules take precedence over alias lookup.
        pid = _find_by_rules(description)
        if pid:
            return pid
        # Step 2: normalize and find by alias.
        norm = _normalize_payee(description)
        if not norm:
            return None
        hit = _store.find_by_alias(norm)
        if hit:
            return hit["id"]
        # Step 2.5: alias-PREFIX fallback — banks append location/country suffixes to the
        # same merchant line ("7 ELEVEN 138 ROBINSO" → "… ROBINSO Singapore SG"), which made
        # an exact-alias book miss its own payee (booked Uncategorized despite a default
        # category, seen live). Guarded: alias ≥ 8 chars, match ends at a token boundary,
        # and a UNIQUE longest winner — ambiguity falls through rather than guessing.
        prefix_hit = _find_by_alias_prefix(norm)
        if prefix_hit:
            return prefix_hit["id"]
        if not create:
            return None
        return _store.create(_display_name(description), aliases=[norm])["id"]
    except Exception:
        return None


def resolve_payee_id_by_name(name: str, *, create: bool = True) -> str | None:
    """Resolve a KNOWN ENTITY NAME (a venue, an institution — not a raw transaction
    description) to a payee id, matching the payee's own display ``name`` first.

    Distinct from ``resolve_payee_id``: that one matches raw bank/card descriptions
    against ALIASES (what a payee gets minted from), and a payee's own name is never
    automatically added as one of its aliases — so calling it with a clean entity name
    like "IBKR" would miss an existing "IBKR" payee whose aliases are all raw
    descriptions ("rklb buy 5 118 2", …) and mint a duplicate. This matches the NAME
    directly, falls back to the alias engine (a caller might pass something that was
    itself minted from a raw description), and only mints as the last resort — seeding
    the new payee's alias too, so a future raw-description lookup can still find it.
    Never raises."""
    try:
        name = (name or "").strip()
        if not name:
            return None
        norm = _normalize_payee(name)
        if not norm:
            return None
        for payee in _store.all():
            if _normalize_payee(payee.get("name") or "") == norm:
                return payee["id"]
        hit = resolve_payee_id(name, create=False)
        if hit:
            return hit
        if not create:
            return None
        return _store.create(name, aliases=[norm])["id"]
    except Exception:
        return None


_ALIAS_PREFIX_MIN_LEN = 8


def _find_by_alias_prefix(norm: str) -> dict | None:
    """The payee whose stored alias is the longest ≥8-char token-boundary prefix of
    ``norm`` — unique winners only (two different payees tying → None, never a guess)."""
    best: tuple[int, dict] | None = None
    tied = False
    for payee in _store.all():
        for alias in payee.get("aliases") or []:
            a = (alias or "").strip()
            if len(a) < _ALIAS_PREFIX_MIN_LEN or not norm.startswith(a):
                continue
            if len(norm) > len(a) and norm[len(a)] != " ":
                continue  # must end at a token boundary ("grab" never claims "grabfood x")
            if best is None or len(a) > best[0]:
                best, tied = (len(a), payee), False
            elif len(a) == best[0] and payee["id"] != best[1]["id"]:
                tied = True
    if best is None or tied:
        return None
    return best[1]


def require_payee(payee_id: str) -> dict:
    """Assert a CALLER-SUPPLIED payee id refers to a real entity and return it.
    Guards the transaction write paths against hallucinated/stale ids becoming
    dangling links. Raises ``NotFoundError``. (Ids minted by ``resolve_payee_id``
    come from the store by construction and don't need this.)"""
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(
            f"payee {payee_id!r} not found — look it up with get_payees or create it with create_payee"
        )
    return p


def default_category_for(
    payee_id: str | None, kind: str = "expense", *, category_rows: list[dict] | None = None
) -> str | None:
    """The category a payee should auto-fill for a transaction of ``kind``, or None.

    Per-kind (owner decision 2026-07-10): reads ``default_categories[kind]`` so an
    expense category never bleeds onto income/transfer/investment.

    Gated by ``autocategorize``. A stored default that is now reserved (kind names /
    structural keys) is never returned — the fill runs AFTER the choke point's
    reserved-category guard, so leaking one would bypass it. The stored value is also
    resolved through ``categories.resolve_category_ref`` (id OR label, same as every other
    reader) before being handed back — a default can go stale (its category deleted after
    the fact) or predate id-only storage (a bare label): either way, a value that no longer
    resolves to a LIVE category is never returned, so a blank category falls through to
    None (Uncategorized) instead of ``resolve_transaction_row`` filling it with a dangling id and
    then raising "Unknown category". ``category_rows`` lets a bulk caller (import/AI batch)
    pass a pre-loaded ``categories.all_category_rows()`` once instead of re-reading the store
    per row — the same optimisation the explicit-category resolution already gets. Never
    raises."""
    if not payee_id or kind == "adjustment":
        return None
    try:
        p = _store.find_by_id(payee_id)
        if not p or not p.get("autocategorize", True):
            return None
        cat = (p.get("default_categories") or {}).get(kind)
        if not cat or cat in _reserved_default_keys():
            return None
        from backend.services import categories as categories_service

        resolved = categories_service.resolve_category_ref(cat, rows=category_rows)
        if not resolved or resolved in _reserved_default_keys():
            return None
        return resolved
    except Exception:
        return None


def learn_category(payee_id: str | None, category: str | None, kind: str = "expense") -> None:
    """Silently learn the payee's default category for ``kind`` from transaction activity
    — first-write-wins PER KIND (never overwrites an existing per-kind default). Skips
    reserved keys, non-categorizable kinds, and kinds the user set to Uncategorized
    (``uncategorized_kinds``). Best-effort; never raises."""
    if not payee_id or not category or kind not in _DEFAULT_CATEGORY_KINDS:
        return
    try:
        if category in _reserved_default_keys():
            return
        p = _store.find_by_id(payee_id)
        if not p:
            return
        if (p.get("default_categories") or {}).get(kind):
            return
        if kind in (p.get("uncategorized_kinds") or []):
            return
        _store.set_default_category_for_kind(payee_id, kind, category)
    except Exception:
        pass


def set_default_categories(payee_id: str, mapping: dict | None) -> dict:
    """Replace a payee's per-kind default map (validated + resolved). Returns the updated
    entity. Raises ``NotFoundError`` if the payee is missing."""
    if not payee_id:
        raise ValidationError("payee_id required")
    clean = normalize_default_categories(mapping)
    previous = _store.find_by_id(payee_id)
    if previous is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    result = _store.update_by_id(
        payee_id,
        {
            "default_categories": clean,
            "uncategorized_kinds": _uncategorized_kinds_after(previous, mapping, clean),
        },
    )
    if result is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    return result


def _uncategorized_kinds_after(previous: dict | None, incoming: dict | None, clean: dict) -> list:
    """The kinds the user has deliberately set to Uncategorized after a user edit of the
    default map. A cleared slot is otherwise indistinguishable from a never-set one, and
    ``learn_category`` refills an empty slot from the next categorized transaction. A kind
    is pinned when the edit sends it blank or drops a default it had (the editor and the AI
    tool both send the whole map); picking a category unpins it."""
    previous = previous or {}
    before = previous.get("default_categories") or {}
    incoming = incoming or {}
    pinned = set(previous.get("uncategorized_kinds") or [])
    for kind in _DEFAULT_CATEGORY_KINDS:
        if clean.get(kind):
            pinned.discard(kind)
        elif (kind in incoming and not incoming[kind]) or before.get(kind):
            pinned.add(kind)
    return [k for k in _DEFAULT_CATEGORY_KINDS if k in pinned]


def on_category_deleted(category_id: str, reassign_to: str | None = None) -> dict:
    """Re-point every payee default naming ``category_id`` — the payee-side half of a
    category delete/merge (mirrors ``services.envelopes.on_category_deleted``'s naming).

    A payee's ``default_categories[kind]`` naming the old id is re-pointed to
    ``reassign_to`` (a merge's target, or a delete's own reassign target) when one is given
    and it isn't the id itself/a reserved key, else CLEARED — the same live-category
    guarantee ``default_category_for`` now enforces on read, kept true going forward instead
    of relying on every reader to notice the dangling id. Writes through
    ``set_default_category_for_kind`` (the store's normal per-kind path, checkpoint-covered
    like every other payee mutation). Returns ``{repointed, cleared}`` — counts of KIND SLOTS
    touched, not payees. Never raises — best-effort maintenance, same contract as
    ``learn_category``/``note_usage``.

    Not wired to a caller yet: ``categories.delete_category``/``rename_category`` (merge)
    would call this after their own category-row write commits — see this repo's payee-side
    fix report for the exact one-line call each needs."""
    out = {"repointed": 0, "cleared": 0}
    if not category_id:
        return out
    target = (reassign_to or "").strip() or None
    if target == category_id or target in _reserved_default_keys():
        target = None
    try:
        for p in _store.all():
            per_kind = p.get("default_categories") or {}
            for kind, cat in list(per_kind.items()):
                if cat != category_id:
                    continue
                _store.set_default_category_for_kind(p["id"], kind, target)
                out["repointed" if target else "cleared"] += 1
    except Exception:
        pass
    return out


def note_usage(payee_id: str | None, when: str | None) -> None:
    """Best-effort last_used update for autocomplete ranking. txn_count is live-computed
    by list_registry() and is NOT stored, so this only persists the date signal."""
    if not payee_id:
        return
    try:
        _store.bump_usage(payee_id, when=when)
    except Exception:
        pass


def payee_names() -> dict[str, str]:
    """Payee id → display name, straight from the payee table — for labelling rows. Never
    touches the ledger."""
    return {p["id"]: p.get("name", "") for p in _store.all()}


def list_registry(*, with_usage: bool = False) -> list[dict]:
    """Registry read projection for the payee pickers + autocomplete (most-used first) and,
    with ``with_usage``, the Manage-Payees editor: ``txn_count``/``last_used`` per payee,
    computed live (non-deleted rows only) so they stay right after soft-deletes and purges.
    The usage comes from one grouped query; the picker list carries the ORDER it implies, not
    the numbers. Never raises."""
    try:
        from backend.db.transaction_store import TransactionStore

        usage = TransactionStore().payee_usage()
        rows = []
        for p in _store.all():
            count, last = usage.get(p["id"], (0, ""))
            row = {
                "id": p["id"],
                "name": p.get("name", ""),
                "default_categories": dict(p.get("default_categories") or {}),
                "autocategorize": bool(p.get("autocategorize", True)),
                "hidden": bool(p.get("hidden", False)),
                "aliases": list(p.get("aliases") or []),
                "rules": list(p.get("rules") or []),
                "_order": (count, last or p.get("last_used") or ""),
            }
            if with_usage:
                row["txn_count"] = count
                row["last_used"] = last or p.get("last_used")
            rows.append(row)
        rows.sort(key=lambda r: r["_order"], reverse=True)
        for r in rows:
            del r["_order"]
        return rows
    except Exception:
        return []


# ── Phase-2 management operations ───────────────────────────────────────────


def create_payee(name: str, *, default_category: str | None = None) -> dict:
    """Explicitly create a new payee entity with the given display name.
    The normalized form of ``name`` is stored as the initial alias.
    Raises ``ConflictError`` if another payee already answers to that normalized name
    (by its own name or an alias — see ``_assert_name_available``), ``ValidationError``
    on bad input."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("name must be non-empty")
    if default_category is not None and default_category in _reserved_default_keys():
        raise ValidationError(
            f"category {default_category!r} is structural and cannot be a payee default"
        )

    _assert_name_available(name)
    norm = _normalize_payee(name)
    aliases = [norm] if norm else []
    payee = _store.create(name, aliases=aliases)
    if default_category:
        # Same normalization the update path uses (id OR label, dead ref dropped) — never
        # store the raw caller value unchecked (that let a stale/typo'd category name sit on
        # a payee forever, silently doing nothing).
        resolved = normalize_default_categories({"expense": default_category}).get("expense")
        if resolved:
            _store.set_default_category_for_kind(payee["id"], "expense", resolved)
            payee = _store.find_by_id(payee["id"]) or payee
    return payee


def update_payee(payee_id: str, changes: dict) -> dict:
    """Apply a partial patch to a payee entity. Accepted keys: ``name``,
    ``default_categories`` (per-kind map, validated + resolved), ``autocategorize``,
    ``hidden``. A ``name`` change is held to the same uniqueness rule as
    ``create_payee``. Raises ``NotFoundError`` / ``ValidationError`` / ``ConflictError``."""
    if not payee_id:
        raise ValidationError("payee_id required")
    allowed = {"name", "default_categories", "autocategorize", "hidden"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError(f"unknown payee fields: {unknown!r}")

    clean: dict = {}
    if "name" in changes:
        name = (changes["name"] or "").strip()
        if not name:
            raise ValidationError("name must be non-empty")
        if _store.find_by_id(payee_id) is None:
            raise NotFoundError(f"payee {payee_id!r} not found")
        _assert_name_available(name, exclude_id=payee_id)
        clean["name"] = name
    if "default_categories" in changes:
        clean["default_categories"] = normalize_default_categories(changes["default_categories"])
        clean["uncategorized_kinds"] = _uncategorized_kinds_after(
            _store.find_by_id(payee_id), changes["default_categories"], clean["default_categories"]
        )
    if "autocategorize" in changes:
        clean["autocategorize"] = bool(changes["autocategorize"])
    if "hidden" in changes:
        clean["hidden"] = bool(changes["hidden"])

    if not clean:
        p = _store.find_by_id(payee_id)
        if p is None:
            raise NotFoundError(f"payee {payee_id!r} not found")
        return p
    result = _store.update_by_id(payee_id, clean)
    if result is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    if "name" in clean:
        # A renamed payee must ANSWER to its new name, exactly as one created under that
        # name does (``create_payee`` seeds it). Otherwise: clean up an auto-minted bank
        # line by renaming it, later type the tidy name on a manual transaction, and the
        # mint path — which bypasses the uniqueness guard by design, since it sits on the
        # ledger write path and must never fail — creates a SECOND entity with the same
        # name. ADDITIVE only: the pre-rename aliases are the bank-feed matchers, and
        # dropping one silently stops imports resolving and re-mints on the next
        # statement. Removal stays a deliberate per-alias act (``remove_alias`` / merge).
        # Safe to claim: ``_assert_name_available`` above proved no other payee holds it.
        norm = _normalize_payee(clean["name"])
        if norm:
            result = _store.add_alias(payee_id, norm) or result
    return result


def merge_payees(source_id: str, target_id: str) -> dict:
    """Merge ``source`` into ``target``: fold aliases, sum usage stats, re-point all
    transactions that carry source_id to target_id, then delete the source entity.
    Returns the updated target. Raises ``NotFoundError`` / ``ValidationError``."""
    if source_id == target_id:
        raise ValidationError("source and target payee must be different")
    source = _store.find_by_id(source_id)
    if source is None:
        raise NotFoundError(f"source payee {source_id!r} not found")
    target = _store.find_by_id(target_id)
    if target is None:
        raise NotFoundError(f"target payee {target_id!r} not found")

    # Fold aliases from source into target
    for alias in source.get("aliases") or []:
        _store.add_alias(target_id, alias)

    # Fold rules from source into target (deduplicate by value)
    src_rules = list(source.get("rules") or [])
    if src_rules:
        tgt_rules = list(target.get("rules") or [])
        for rule in src_rules:
            if rule not in tgt_rules:
                tgt_rules.append(rule)
        _store.update_by_id(target_id, {"rules": tgt_rules})

    # Merge last_used (take max date; txn_count is live-computed so needs no write)
    src_last = source.get("last_used") or ""
    tgt_last = target.get("last_used") or ""
    merged_last = max(src_last, tgt_last) or None
    if merged_last != target.get("last_used"):
        _store.update_by_id(target_id, {"last_used": merged_last})

    # Re-point transactions then delete source
    _reassign_transactions(source_id, target_id, note=f"payee_merge:{source_id}->{target_id}")
    _store.delete(source_id)

    result = _store.find_by_id(target_id)
    if result is None:
        raise NotFoundError(f"target payee {target_id!r} vanished during merge")
    return result


def delete_payee(payee_id: str, *, reassign_to: str | None = None) -> None:
    """Delete a payee entity. If ``reassign_to`` is given, re-point its transactions
    to that payee (merge path). Otherwise clear ``payee_id`` on all its transactions.
    Raises ``NotFoundError`` if the entity or reassign target is missing."""
    if not payee_id:
        raise ValidationError("payee_id required")
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")

    if reassign_to:
        if not _store.find_by_id(reassign_to):
            raise NotFoundError(f"reassign_to payee {reassign_to!r} not found")
        _reassign_transactions(
            payee_id, reassign_to, note=f"payee_delete_reassign:{payee_id}->{reassign_to}"
        )
    else:
        _clear_payee_id_on_transactions(payee_id)

    _store.delete(payee_id)


def add_alias(payee_id: str, raw_text: str) -> dict:
    """Normalize ``raw_text`` and add it as an alias to a payee. Raises
    ``NotFoundError`` if the payee is missing, ``ConflictError`` if the alias is
    already claimed by a different payee, ``ValidationError`` if it normalizes to empty."""
    norm = _normalize_payee(raw_text)
    if not norm:
        raise ValidationError(
            "alias text normalizes to an empty string — try a more specific phrase"
        )
    existing = _store.find_by_alias(norm)
    if existing and existing["id"] != payee_id:
        raise ConflictError(
            f"alias already claimed by payee '{existing.get('name', existing['id'])}'"
        )
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    result = _store.add_alias(payee_id, norm)
    return result or _store.find_by_id(payee_id)


def remove_alias(payee_id: str, alias: str) -> dict:
    """Remove a normalized alias from a payee. No-op if the alias wasn't there.
    Raises ``NotFoundError`` if the payee is missing."""
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    aliases = [a for a in (p.get("aliases") or []) if a != alias]
    result = _store.update_by_id(payee_id, {"aliases": aliases})
    return result or p


# ── Rule management ─────────────────────────────────────────────────────────


def _check_rule_conflict(rule: dict, exclude_payee_id: str) -> None:
    """Raise ConflictError if another payee already has an identical rule."""
    for other in _store.all():
        if other["id"] == exclude_payee_id:
            continue
        for existing in other.get("rules") or []:
            if (
                existing.get("match_type") == rule["match_type"]
                and existing.get("value") == rule["value"]
            ):
                raise ConflictError(
                    f"rule already exists on payee '{other.get('name', other['id'])}'"
                )


def add_payee_rule(payee_id: str, match_type: str, value: str) -> dict:
    """Append a raw-match rule to a payee. Rules run before alias lookup on every
    resolve_payee_id() call. Raises NotFoundError / ValidationError / ConflictError."""
    rule = {"match_type": match_type, "value": (value or "").strip()}
    _validate_rule(rule)
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    _check_rule_conflict(rule, payee_id)
    rules = list(p.get("rules") or [])
    rules.append(rule)
    return _store.update_by_id(payee_id, {"rules": rules}) or p


def remove_payee_rule(payee_id: str, index: int) -> dict:
    """Remove the rule at position ``index``. Raises NotFoundError / ValidationError."""
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    rules = list(p.get("rules") or [])
    if index < 0 or index >= len(rules):
        raise ValidationError(f"rule index {index} out of range (0–{len(rules) - 1})")
    rules.pop(index)
    return _store.update_by_id(payee_id, {"rules": rules}) or p


def set_payee_rules(payee_id: str, rules: list) -> dict:
    """Replace all rules on a payee. Validates each entry. Raises NotFoundError / ValidationError."""
    if not isinstance(rules, list):
        raise ValidationError("rules must be a list")
    for i, rule in enumerate(rules):
        try:
            _validate_rule(rule)
        except ValidationError as exc:
            raise ValidationError(f"rule[{i}]: {exc}") from exc
    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    clean = [{"match_type": r["match_type"], "value": r["value"].strip()} for r in rules]
    return _store.update_by_id(payee_id, {"rules": clean}) or p


def apply_payee_rules_to_history() -> dict:
    """Re-point existing transactions to match current rules + aliases.

    For every non-deleted transaction that has a description, runs
    ``resolve_payee_id(create=False)`` — which checks user rules first, then alias
    lookup. If the result differs from the stored ``payee_id`` (and is not None),
    updates the transaction in place. Returns ``{updated: N}``. Batch-records an
    "update" audit event for exactly the rows that changed (transaction-provenance
    invariant — a bulk store write is never silent)."""
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_service

    store = TransactionStore()
    txns = store.all()
    updated_txns = []
    changed: list[dict] = []
    for t in txns:
        if t.get("deleted"):
            updated_txns.append(t)
            continue
        desc = txn_text(t).strip()
        if not desc:
            updated_txns.append(t)
            continue
        resolved = resolve_payee_id(desc, create=False)
        if resolved is not None and resolved != t.get("payee_id"):
            row = {**t, "payee_id": resolved}
            changed.append(row)
            updated_txns.append(row)
        else:
            updated_txns.append(t)
    if changed:
        store.replace_all(updated_txns)
        audit_service.record_txn_events("update", changed, note="apply_payee_rules:history")
    return {"updated": len(changed)}


def apply_payee_rules_to_existing(payee_id: str) -> dict:
    """Re-point existing transactions whose description matches any of this payee's
    renaming rules but are not yet assigned to this payee.
    Returns ``{updated: N}``. Batch-records an "update" audit event for exactly the
    rows that changed (transaction-provenance invariant)."""
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_service

    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    rules = p.get("rules") or []
    if not rules:
        return {"updated": 0}

    store = TransactionStore()
    txns = store.all()
    updated_txns = []
    changed: list[dict] = []
    for t in txns:
        if t.get("deleted"):
            updated_txns.append(t)
            continue
        desc = txn_text(t).strip()
        if desc and t.get("payee_id") != payee_id:
            for rule in rules:
                if _match_rule(desc, rule):
                    row = {**t, "payee_id": payee_id}
                    changed.append(row)
                    updated_txns.append(row)
                    break
            else:
                updated_txns.append(t)
        else:
            updated_txns.append(t)
    if changed:
        store.replace_all(updated_txns)
        audit_service.record_txn_events("update", changed, note=f"apply_payee_rules:{payee_id}")
    return {"updated": len(changed)}


def apply_default_category_to_transactions(payee_id: str) -> dict:
    """Stamp each of the payee's PER-KIND defaults onto its own non-deleted transactions
    of that kind (skips splits, adjustments, and deleted rows).
    Returns ``{updated: N}``. Batch-records one "update" audit event for exactly the
    rows that changed (transaction-provenance invariant)."""
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_service

    p = _store.find_by_id(payee_id)
    if p is None:
        raise NotFoundError(f"payee {payee_id!r} not found")
    per_kind = dict(p.get("default_categories") or {})
    per_kind = {k: v for k, v in per_kind.items() if k in _DEFAULT_CATEGORY_KINDS and v}
    if not per_kind:
        raise ValidationError("payee has no default category set")

    store = TransactionStore()
    txns = store.all()
    updated_txns = []
    changed: list[dict] = []
    for t in txns:
        target = per_kind.get(t.get("kind"))
        if (
            target
            and not t.get("deleted")
            and t.get("payee_id") == payee_id
            and t.get("category") != "split"
            and t.get("category") != target
        ):
            row = {**t, "category": target}
            changed.append(row)
            updated_txns.append(row)
        else:
            updated_txns.append(t)
    if changed:
        store.replace_all(updated_txns)
        audit_service.record_txn_events("update", changed, note=f"apply_payee_category:{payee_id}")
    return {"updated": len(changed)}


# ── Private helpers ──────────────────────────────────────────────────────────


def _reassign_transactions(from_id: str, to_id: str, *, note: str | None = None) -> None:
    """Re-point all transactions with payee_id == from_id to to_id in one replace_all,
    then batch-record an "update" audit event for exactly the rows reassigned
    (transaction-provenance invariant — a bulk store write is never silent)."""
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_service

    store = TransactionStore()
    txns = store.all()
    changed: list[dict] = []
    updated = []
    for t in txns:
        if t.get("payee_id") == from_id:
            row = {**t, "payee_id": to_id}
            changed.append(row)
            updated.append(row)
        else:
            updated.append(t)
    store.replace_all(updated)
    audit_service.record_txn_events("update", changed, note=note)


def _clear_payee_id_on_transactions(payee_id: str) -> None:
    """Null out payee_id on all transactions with this payee in one replace_all,
    then batch-record an "update" audit event for exactly the rows cleared
    (transaction-provenance invariant — a bulk store write is never silent)."""
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_service

    store = TransactionStore()
    txns = store.all()
    changed: list[dict] = []
    updated = []
    for t in txns:
        if t.get("payee_id") == payee_id:
            row = {**t, "payee_id": None}
            changed.append(row)
            updated.append(row)
        else:
            updated.append(t)
    store.replace_all(updated)
    audit_service.record_txn_events("update", changed, note=f"payee_delete_clear:{payee_id}")
