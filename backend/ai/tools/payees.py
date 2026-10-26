"""Payee tools for Lucius (P4-PAYEE phase 1 + 2; batch-pluralized 2026-07-19).

Owner directive: every tool that acts on ONE payee should accept MANY, mirroring
``add_transactions``/``bulk_update_transactions``. Two shapes cover it, chosen per tool by
whether a divergent-per-item value is a real scenario:

  • **items-or-bare** (``_items_or_bare``) — each item is inherently distinct (a new payee's
    ``name``, a rename's ``new_name``, an alias/rule's own text) — there is no sensible
    "same value for everyone" case, so the array holds full per-item objects. The bare
    top-level fields (unchanged from before) are wrapped into a one-item batch internally,
    so the single-target call stays exactly as trivial as it always was.
  • **broadcast-or-updates** (``_broadcast_or_updates``) — the common case is "the SAME
    change on N payees" (hide these 5, set this category on these 3). ``payee_ids`` (a
    scalar-or-list PLURAL param — renamed from ``payee_id`` 2026-07-19b, a param rename
    needs no alias table the way a tool rename does, see ``_payee_ids_param``'s docstring;
    the old key still works) applies the tool's own field(s) to every id — no per-item
    ceremony. An explicit ``updates``/``removals`` array is still available for the rarer
    case where different payees need different values in the same call.

Every batch is NOT atomic (unlike ``add_transactions``' all-or-nothing ledger write): each
item is an independent entity operation, so one bad id/value reports its own error and
every other item still lands — mirroring ``bulk_update_transactions``. A single-target call
(bare fields, no array) gets the exact SAME response shape it always did (``payee``/
``merged_into``/``deleted``+``reassigned_to``/``updated``) — batching never changes what a
non-batch caller sees.

A renamed tool's old name is MIGRATED, not aliased — ``tool_renames.RENAMES``
rewrites every stored reference, after which the old name is simply unknown (a tool name is
persisted data — see ``registry.py``'s "Tool names are FINAL" note).

**Param-arity follow-up (2026-07-19b, owner: "why is account_id even though it's a list ->
it's not account_ids? what the fuck is that").** The 2026-07-19 pluralization above fixed
every tool NAME but left the entity-targeting PARAM singular (``payee_id``) even where it
takes a list — a live trap: a model reading ``payee_id``, wanting several, stringifies the
list, and ``delete_account``'s identical bug is what motivated
``registry.coerce_stringified_containers``. Every ``payee_id`` that widened to scalar-or-
list is renamed to ``payee_ids`` (``get_payees``, ``set_payees_hidden``,
``set_payees_default_category``, ``set_payees_autocategorize``, ``remove_payee_rules``,
``set_payees_rules``, ``apply_payee_rules_to_payees``, ``apply_payee_categories``,
``delete_payees`` — verified against ``backend/scripts/tool_inventory.py --lint``, which
encodes this exact rule). A PARAM rename is not a TOOL rename — no alias-table entry, just
read-side tolerance: ``_payee_ids_param`` reads ``payee_ids`` first, falling back to
``payee_id`` (a stored/scripted caller on the old key — scalar OR a real list — keeps
working). Deliberately NOT re-declared in the schema as a second ``anyOf`` property: that
would re-trip this exact linter rule on the tool's own schema (a singular-named property
that takes a list) for a decoy the model is never meant to pick, and
``additionalProperties`` is already ignored at dispatch (registry.py) so an undeclared key
still reaches the handler untouched. Known, accepted gap: a caller that JSON-stringifies a
LIST under the OLD key (`payee_id='["a","b"]'`) is not auto-decoded the way a stringified
``payee_ids`` is (`registry.coerce_stringified_containers` only fires for keys the schema
declares) — the class of caller this rename targets (an LLM guessing at encoding) sees the
new schema on its very next turn and stops doing this; a deterministic external
script/skill on the old key is unlikely to stringify one in the first place. The per-item
``payee_id`` inside an ``updates``/``renames``/``removals`` array, and the bare
single-target ``payee_id`` on
``add_payee_aliases``/``remove_payee_aliases``/``add_payee_rules``/``rename_payees``, are
correctly singular (an attribute of the ONE item in that slot, never a list) and are left
alone.

Read:
  • ``get_payees`` — first-class payee ENTITIES (id, name, aliases, default_categories…).
    ``payee_ids`` accepts a single id or a LIST for a multi-lookup in one call (renamed
    from ``payee_id`` 2026-07-19b — the old key still works).

Write (batch-capable; former singular names all still resolve as aliases):
  • ``create_payees``                  (was ``create_payee``)
  • ``rename_payees``                  (was ``rename_payee``)
  • ``set_payees_default_category``    (was ``set_payee_default_category``)
  • ``set_payees_hidden``              (was ``set_payee_hidden``)
  • ``set_payees_autocategorize``      (was ``set_payee_autocategorize``)
  • ``add_payee_aliases``              (was ``add_payee_alias``)
  • ``remove_payee_aliases``           (was ``remove_payee_alias``)
  • ``add_payee_rules``                (was ``add_payee_rule``)
  • ``remove_payee_rules``             (was ``remove_payee_rule``)
  • ``set_payees_rules``               (was ``set_payee_rules``)
  • ``apply_payee_rules``              — GLOBAL ledger rescan. Deliberately NOT pluralized:
                                         there is exactly one ledger, so "apply to several
                                         ledgers" is incoherent (like restore_checkpoint).
                                         ``apply_payee_rules_to_payees`` is its already-
                                         scoped, already-plural sibling for "just these
                                         payees".
  • ``apply_payee_rules_to_payees``    (was ``apply_payee_rules_to_payee``)
  • ``apply_payee_categories``         (was ``apply_payee_category``)
  • ``merge_payees``                   — name unchanged (already plural: a merge always
                                         names two payees); gained a ``merges`` batch array
                                         alongside the existing single-pair call.
  • ``delete_payees``                  (was ``delete_payee``)
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import payee_registry

# Without a cap the full payee book (~300 rows ≈ 13-16k tokens) lands in chat history
# on every get_payees call and is re-sent each model round-trip for the rest of the
# session — the live cost log showed it as a top history inflator.
_DEFAULT_PAYEE_LIMIT = 50
_MAX_PAYEE_QUERIES = 10


def _capped(rows: list[dict], limit: int, extra: dict) -> dict:
    total = len(rows)
    out = {**extra, "payees": rows[:limit], "count": min(limit, total), "total": total}
    if total > limit:
        out["note"] = (
            f"Showing {limit} of {total} payees (most-significant first). "
            "Pass query to find a specific payee, or a higher limit for more."
        )
    return out


# ── Batch shape helpers ─────────────────────────────────────────────────────────────
# Shared by every write tool below so the two batch shapes (items-or-bare,
# broadcast-or-updates) have exactly one implementation each, not one per tool.


def _as_id_list(value) -> Optional[list[str]]:
    """Normalize a payee-id target that may arrive as a bare string OR a list.

    Always returns a plain list (never collapsed back to a scalar) since every caller
    wants to iterate it; ``None`` when there is nothing to target.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else None
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    return None


def _payee_ids_param(input_data: dict) -> Optional[list[str]]:
    """Read the multi-payee target under its current name (``payee_ids``), falling back
    to the pre-rename name (``payee_id``, which used to double as scalar-or-list before
    the 2026-07-19 param-arity fix). A param rename needs no stored-data migration the way
    a TOOL rename does (see registry.py's "Tool names are FINAL" note) — just this read-side
    tolerance, so a stored/scripted caller or an in-flight session still on the old key
    keeps working.
    """
    raw = input_data.get("payee_ids")
    if raw is None:
        raw = input_data.get("payee_id")
    return _as_id_list(raw)


def _items_or_bare(input_data: dict, array_key: str, bare_keys: tuple[str, ...]) -> list[dict]:
    """The ``add_transactions`` shape-leniency convention: an explicit array (``array_key``)
    is used verbatim; otherwise the top-level ``bare_keys`` are wrapped into ONE item, so a
    single-target call needs no array-of-objects ceremony. A dict passed unwrapped inside the
    array key is treated as a single item — the same leniency ``add_transactions`` applies to
    a bare ``transactions`` object.
    """
    raw = input_data.get(array_key)
    if raw is None:
        bare = {k: input_data[k] for k in bare_keys if k in input_data}
        return [bare] if bare else []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _broadcast_or_updates(
    input_data: dict, updates_key: str, shared_fields: tuple[str, ...]
) -> list[dict]:
    """The "same change on N payees" shape: an explicit ``updates_key`` array of per-payee
    dicts takes precedence (divergent values per target); otherwise ``payee_ids`` (scalar or
    list) broadcasts the SAME top-level field value(s) to every listed id — the common case,
    with no per-item ceremony required.
    """
    raw = input_data.get(updates_key)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    ids = _payee_ids_param(input_data)
    if not ids:
        return []
    shared = {k: input_data[k] for k in shared_fields if k in input_data}
    return [{"payee_id": pid, **shared} for pid in ids]


def _batch_result(results: list[dict], *, count_key: str) -> dict:
    """The shared batch-response envelope: ``ok`` = every item ok, ``count_key`` = how many
    succeeded, ``results`` = the per-index outcome — never omitted, so the caller can always
    tell EXACTLY which of N items landed (mirrors ``add_transactions``'s own ``results``).
    """
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count == len(results), count_key: ok_count, "results": results}


def _single_or_batch(out: dict, results: list[dict], single_value, value_key: str) -> dict:
    """Fold the batch envelope back down to the exact pre-batch response shape when the
    caller only ever targeted one payee — a single-target call must see byte-identical
    output to before, not a `results: [...]` wrapper it never asked for."""
    if len(results) != 1:
        return out
    if single_value is not None:
        out[value_key] = single_value
    else:
        out["error"] = results[0].get("error")
    return out


# ── get_payees ───────────────────────────────────────────────────────────────────────


class GetPayeesInput(ToolInput):
    """Schema-of-record for ``get_payees`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None,
        description=(
            "Exact payee id (pay_…), e.g. from a transaction row or an earlier "
            "create_payees. Pass a LIST to look up several at once in one call. "
            "Returns those entities; query/queries/limit are ignored."
        ),
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "Case-insensitive substring match on the payee name and aliases. "
            "Used alone, keeps the flat payees/count/total response. With nonblank queries, "
            "appears as the first attributed result and counts toward the 10-term combined cap."
        ),
    )
    queries: Optional[List[str]] = Field(
        default=None,
        description=(
            "Case-insensitive substring matches on payee names and aliases. Together with a "
            "nonblank scalar query, at most 10 nonblank terms are allowed; blanks are ignored. "
            "Returns one ordered result per term, including zero matches; each result has query, "
            "payees, showing, and total. Limit applies independently per result and is capped "
            "at 50."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        description=(
            "Max rows to return. Default 50. For plural queries, applies independently per "
            "result and is capped at 50."
        ),
    )


@tool(
    "get_payees",
    description=(
        "List the user's first-class payee ENTITIES. Each row has the payee id (pay_…), "
        "name, default_categories (per-kind, auto-applied to new transactions of that payee), "
        "autocategorize, hidden, aliases, txn_count (live count of "
        "non-deleted transactions), and last_used; most-used first.\n"
        "Use to look up a payee_id before stamping it on a transaction, answer "
        "'what category does <merchant> default to?', or inspect/manage saved payees.\n"
        "payee_ids accepts a LIST — look up several specific payees in one call instead "
        "of one call each.\n"
        "queries accepts name/alias substrings — use it instead of repeating get_payees calls. "
        "At most 10 result groups across scalar query and nonblank queries (blanks ignored). "
        "One ordered result per term (zero matches included), each with its own "
        "payees/showing/total; limit applies per result, capped at 50. A scalar query "
        "supplied with queries is the first result.\n"
        "Results are capped (default 50 rows, most-used first) — always pass query/queries "
        "(by name) or payee_ids (exact) to find specific payees instead of paging the full book."
    ),
    input_schema=schema_of(GetPayeesInput, tool_name="get_payees"),
)
async def get_payees(input_data: dict):
    ids = _payee_ids_param(input_data)
    if ids and len(ids) == 1:
        pid = ids[0]
        try:
            # list_registry() gives the live-count projection; search it first.
            # If it returns [] (silent exception inside), fall back to require_payee
            # so we still surface a real NotFoundError rather than a false one.
            rows = payee_registry.list_registry(with_usage=True)
            row = next((r for r in rows if r["id"] == pid), None)
            if row is None:
                # list_registry() returned [] (silent exception) but the payee may still exist —
                # require_payee raises NotFoundError ONLY if truly missing; otherwise fall back to
                # its raw row (correct identity, just without the live txn_count).
                row = payee_registry.require_payee(pid)
            return json.dumps({"payee": row}, ensure_ascii=False), None
        except NotFoundError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False), None
    if ids:
        return _get_payees_by_ids(ids)

    query = str(input_data.get("query") or "").strip()
    queries = [str(term).strip() for term in input_data.get("queries") or [] if str(term).strip()]
    query_terms = ([query] if query else []) + queries
    if len(query_terms) > _MAX_PAYEE_QUERIES:
        return json.dumps(
            {
                "error": (
                    f"{len(query_terms)} queries exceeds the {_MAX_PAYEE_QUERIES}-query cap "
                    "for one call — split into multiple calls."
                )
            },
            ensure_ascii=False,
        ), None

    try:
        limit = int(input_data.get("limit") or _DEFAULT_PAYEE_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_PAYEE_LIMIT
    limit = max(1, limit)

    rows = payee_registry.list_registry(with_usage=True)

    def matches(row: dict, term: str) -> bool:
        lowered = term.lower()
        return lowered in str(row.get("name") or "").lower() or any(
            lowered in str(alias).lower() for alias in row.get("aliases") or []
        )

    if queries:
        result_limit = min(limit, _DEFAULT_PAYEE_LIMIT)
        results = []
        for term in query_terms:
            matches_for_term = [row for row in rows if matches(row, term)]
            total = len(matches_for_term)
            result = {
                "query": term,
                "payees": matches_for_term[:result_limit],
                "showing": min(result_limit, total),
                "total": total,
            }
            if total > result_limit:
                result["note"] = (
                    f"Showing {result_limit} of {total} payees for query {term!r} "
                    "(most-significant first). Narrow the query to find a specific payee."
                )
            results.append(result)
        return json.dumps({"results": results}, ensure_ascii=False), None

    if query:
        rows = [r for r in rows if matches(r, query)]
    return json.dumps(_capped(rows, limit, {}), ensure_ascii=False), None


def _get_payees_by_ids(ids: list[str]) -> tuple[str, None]:
    """The multi-id branch of ``get_payees``: look each up, reporting misses by id rather
    than silently dropping them (a dropped id looks identical to "doesn't exist at all"
    and this is a read the model may act on)."""
    rows = payee_registry.list_registry(with_usage=True)
    by_id = {r["id"]: r for r in rows}
    found: list[dict] = []
    not_found: list[str] = []
    for pid in ids:
        row = by_id.get(pid)
        if row is None:
            try:
                row = payee_registry.require_payee(pid)
            except NotFoundError:
                not_found.append(pid)
                continue
        found.append(row)
    out: dict = {"payees": found, "showing": len(found), "total": len(found)}
    if not_found:
        out["not_found"] = not_found
        out["note"] = (
            f"{len(not_found)} of {len(ids)} payee id(s) not found: {', '.join(not_found)}."
        )
    return json.dumps(out, ensure_ascii=False), None


# ── create_payees ────────────────────────────────────────────────────────────────────

_CREATE_PAYEE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Display name for the new payee (e.g. 'Raghu SV', 'NTUC FairPrice').",
        },
        "default_category": {
            "type": ["string", "null"],
            "description": (
                "Optional. Default category for this payee's EXPENSE transactions — an "
                "existing label or id from list_categories. Stored WITHOUT an existence "
                "check: an unknown value then rejects every future transaction for this "
                "payee. Use set_payees_default_category for other kinds."
            ),
        },
    },
    "required": ["name"],
}


@tool(
    "create_payees",
    description=(
        "Create one or many named payee entities — use when linking a transaction to a "
        "person/merchant that doesn't exist yet, rather than relying on automatic "
        "description-based resolution. After creating, pass the returned payee_id to "
        "update_transactions.\n\n"
        "Single payee: name (+ optional default_category). Several in one call: `payees` "
        "— [{name, default_category?}, ...].\n\n"
        "The normalized name is stored as the initial alias, so future imports whose "
        "descriptions normalize to the same string route here automatically.\n\n"
        "Each item fails independently — a conflicting name doesn't block the rest of the "
        "batch — if a payee with the same normalized name already exists; check with "
        "get_payees first, or rename_payees to fix an existing one."
    ),
    # Batch array only — the bare `name`/`default_category` fields are still ACCEPTED by the
    # handler (via _items_or_bare below), just not advertised. Hand-written rather than
    # schema_of_batch_only because this tool's schema is still a literal: default_category
    # is an explicit-null field (`type: ["string","null"]`), the one quirk class the
    # derivation deliberately migrates LAST — an explicit null CLEARS the category while
    # omission leaves it untouched, so collapsing it is a silent semantic change.
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "payees": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Create one or MULTIPLE payees in one call: [{name, default_category?}, ...]."
                ),
                "items": _CREATE_PAYEE_ITEM_SCHEMA,
            },
        },
        "required": ["payees"],
    },
    mutates=("payees", "ledger"),
)
async def create_payees(input_data: dict):
    items = _items_or_bare(input_data, "payees", ("name", "default_category"))
    if not items:
        return json.dumps({"ok": False, "error": "name (or payees) is required"}), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        name = item.get("name", "")
        cat = item.get("default_category") or None
        try:
            result = payee_registry.create_payee(name, default_category=cat)
            results.append({"index": i, "ok": True, "id": result["id"], "name": result["name"]})
            single_value = result
        except (ConflictError, NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="created")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── rename_payees ────────────────────────────────────────────────────────────────────

_PAYEE_ID_DESC = "The payee's id (pay_…)."


class RenameItem(NestedInput):
    """Schema-of-record for a ``rename_payees.renames`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    new_name: str = Field(description="The new display name.")


class RenamePayeesInput(ToolInput):
    """Schema-of-record for ``rename_payees`` (see ``schema_from_model``)."""

    payee_id: Optional[str] = Field(default=None, description=_PAYEE_ID_DESC)
    new_name: Optional[str] = Field(default=None, description="The new display name.")
    renames: List[RenameItem] = Field(
        default_factory=list,
        description=(
            "Rename MULTIPLE payees in one call: [{payee_id, new_name}, ...]. Omit "
            "when renaming a single payee via payee_id/new_name above."
        ),
    )


@tool(
    "rename_payees",
    description=(
        "Rename one or many payees' display names. Since transactions store the payee_id "
        "(not the name), this is a pure entity update — all existing transactions "
        "automatically show the new name.\n\n"
        "Single payee: payee_id + new_name. Several in one call: `renames` — "
        "[{payee_id, new_name}, ...]. Requires each payee's id (get it from get_payees)."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        RenamePayeesInput, array="renames", tool_name="rename_payees"
    ),
    mutates=("payees", "ledger"),
)
async def rename_payees(input_data: dict):
    items = _items_or_bare(input_data, "renames", ("payee_id", "new_name"))
    if not items:
        return json.dumps(
            {"ok": False, "error": "payee_id and new_name (or renames) is required"}
        ), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id", "")
        name = item.get("new_name", "")
        try:
            result = payee_registry.update_payee(pid, {"name": name})
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (ConflictError, NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="renamed")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── set_payees_default_category ───────────────────────────────────────────────────────
# `category` stays an EXPLICIT-NULL field (type: ["string", "null"]) on every path — top
# level and per-item override alike. null CLEARS the default; the tool always writes the
# field (set or clear), never treats a call as a silent no-op. Collapsing this to a bare
# "string" would make clearing a default inexpressible in the schema — a money-semantics
# regression, not a cleanup.

_SET_DEFAULT_CATEGORY_KIND_ENUM = ["expense", "income", "transfer", "investment"]

_SET_DEFAULT_CATEGORY_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "payee_id": {"type": "string", "description": "The payee's id (pay_…)."},
        "category": {
            "type": ["string", "null"],
            "description": "Category id/label to set as this kind's default, or null to clear.",
        },
        "kind": {
            "type": "string",
            "enum": _SET_DEFAULT_CATEGORY_KIND_ENUM,
            "description": "Which transaction kind this default applies to. Default: expense.",
        },
    },
    "required": ["payee_id", "category"],
}


@tool(
    "set_payees_default_category",
    description=(
        "Set or clear a default category for one transaction kind, on one or many payees "
        "at once. Defaults are per-kind: an expense default never bleeds onto income/"
        "transfer/investment. Pass `kind` (expense|income|transfer|investment; default "
        "expense) and a category id/label, or null to clear that kind's default. Applied "
        "after rules, before the uncategorized sink.\n\n"
        "Single payee: payee_ids (one id) + category (+ optional kind). The SAME category/"
        "kind on SEVERAL payees: payee_ids as a LIST. Different categories per payee in one "
        "call: `updates` — [{payee_id, category, kind?}, ...] — takes precedence over the "
        "top-level fields. Use get_payees for payee ids."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "payee_ids": {
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                "description": (
                    "One payee id, or a list to set the SAME category/kind on several at once."
                ),
            },
            "category": {
                "type": ["string", "null"],
                "description": (
                    "Category id/label to set as this kind's default, or null to clear. "
                    "Broadcast to every id in payee_ids."
                ),
            },
            "kind": {
                "type": "string",
                "enum": _SET_DEFAULT_CATEGORY_KIND_ENUM,
                "description": "Which transaction kind this default applies to. Default: expense.",
            },
            "updates": {
                "type": "array",
                "description": (
                    "Per-payee overrides when different payees need different categories/"
                    "kinds in one call. Takes precedence over payee_ids/category/kind above."
                ),
                "items": _SET_DEFAULT_CATEGORY_ITEM_SCHEMA,
            },
        },
        "required": [],
    },
    mutates=("payees", "ledger"),
)
async def set_payees_default_category(input_data: dict):
    items = _broadcast_or_updates(input_data, "updates", ("category", "kind"))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or updates) is required"}), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id") or ""
        cat = item.get("category")
        kind = item.get("kind") or "expense"
        try:
            if cat is not None and cat in payee_registry._reserved_default_keys():
                raise ValidationError(
                    f"category {cat!r} is structural and cannot be a payee default"
                )
            existing = dict(payee_registry.require_payee(pid).get("default_categories") or {})
            existing[kind] = cat or None
            result = payee_registry.update_payee(pid, {"default_categories": existing})
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── set_payees_hidden ────────────────────────────────────────────────────────────────


class SetHiddenItem(NestedInput):
    """Schema-of-record for a ``set_payees_hidden.updates`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    hidden: bool = Field(description="true = hide from suggestions; false = show.")


class SetPayeesHiddenInput(ToolInput):
    """Schema-of-record for ``set_payees_hidden`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None,
        description="One payee id, or a list to set the SAME hidden value on several at once.",
    )
    hidden: Optional[bool] = Field(
        default=None,
        description="true = hide from suggestions; false = show. Broadcast to every id in payee_ids.",
    )
    updates: List[SetHiddenItem] = Field(
        default_factory=list,
        description=(
            "Per-payee overrides: [{payee_id, hidden}, ...]. Takes precedence over "
            "payee_ids/hidden above."
        ),
    )


@tool(
    "set_payees_hidden",
    description=(
        "Show or hide one or many payees from the autocomplete suggestions in the "
        "transaction form. Hidden payees still auto-resolve and auto-categorize on import; "
        "they just don't appear in the manual picker. Use to declutter rarely-used or "
        "one-off payees.\n\n"
        "Single payee: payee_ids (one id) + hidden. The SAME value on SEVERAL payees: "
        "payee_ids as a LIST. Different values per payee in one call: `updates` — "
        "[{payee_id, hidden}, ...]."
    ),
    input_schema=schema_of(SetPayeesHiddenInput, tool_name="set_payees_hidden"),
    mutates=("payees", "ledger"),
)
async def set_payees_hidden(input_data: dict):
    items = _broadcast_or_updates(input_data, "updates", ("hidden",))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or updates) is required"}), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id") or ""
        hidden = bool(item.get("hidden", False))
        try:
            result = payee_registry.update_payee(pid, {"hidden": hidden})
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── set_payees_autocategorize ──────────────────────────────────────────────────────────


class SetAutocategorizeItem(NestedInput):
    """Schema-of-record for a ``set_payees_autocategorize.updates`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    autocategorize: bool = Field(
        description="true = auto-apply default_category; false = always arrive uncategorized."
    )


class SetPayeesAutocategorizeInput(ToolInput):
    """Schema-of-record for ``set_payees_autocategorize`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None,
        description="One payee id, or a list to set the SAME autocategorize value on several at once.",
    )
    autocategorize: Optional[bool] = Field(
        default=None,
        description=(
            "true = auto-apply default_category; false = always arrive uncategorized. "
            "Broadcast to every id in payee_ids."
        ),
    )
    updates: List[SetAutocategorizeItem] = Field(
        default_factory=list,
        description=(
            "Per-payee overrides: [{payee_id, autocategorize}, ...]. Takes "
            "precedence over payee_ids/autocategorize above."
        ),
    )


@tool(
    "set_payees_autocategorize",
    description=(
        "Enable or disable the auto-category feature for one or many payees. When enabled "
        "(the default), a payee's default_category is automatically applied to new "
        "transactions from it. When disabled, new transactions arrive uncategorized even if "
        "a default_category is set — useful for payees where the right category varies per "
        "transaction.\n\n"
        "Single payee: payee_ids (one id) + autocategorize. The SAME value on SEVERAL "
        "payees: payee_ids as a LIST. Different values per payee in one call: `updates` — "
        "[{payee_id, autocategorize}, ...]."
    ),
    input_schema=schema_of(SetPayeesAutocategorizeInput, tool_name="set_payees_autocategorize"),
    mutates=("payees", "ledger"),
)
async def set_payees_autocategorize(input_data: dict):
    items = _broadcast_or_updates(input_data, "updates", ("autocategorize",))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or updates) is required"}), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id") or ""
        val = bool(item.get("autocategorize", True))
        try:
            result = payee_registry.update_payee(pid, {"autocategorize": val})
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── add_payee_aliases ────────────────────────────────────────────────────────────────
# No broadcast case: an alias is a raw string that must normalize to a value unique to
# ONE payee (a conflict on a second payee is a ConflictError, not a legitimate share), so
# each item is inherently distinct — items-or-bare, never broadcast-or-updates.


class AddAliasItem(NestedInput):
    """Schema-of-record for an ``add_payee_aliases.aliases`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    text: str = Field(
        description="Raw text to normalize and add as an alias (e.g. 'Starbucks Coffee SG')."
    )


class AddPayeeAliasesInput(ToolInput):
    """Schema-of-record for ``add_payee_aliases`` (see ``schema_from_model``)."""

    payee_id: Optional[str] = Field(default=None, description=_PAYEE_ID_DESC)
    text: Optional[str] = Field(
        default=None, description="Raw text to normalize and add as an alias."
    )
    aliases: List[AddAliasItem] = Field(
        default_factory=list,
        description=(
            "Add MULTIPLE aliases in one call: [{payee_id, text}, ...]. Omit when "
            "adding a single alias via payee_id/text above."
        ),
    )


@tool(
    "add_payee_aliases",
    description=(
        "Add a normalized alias to one or many payees in one call. Aliases work at the "
        "NORMALIZATION layer — after stripping processor prefixes and collapsing "
        "whitespace/punctuation. Use this when you know a specific normalized string that "
        "should map to a payee (e.g. 'starbucks coffee' → Starbucks payee). For raw-"
        "description matching (before normalization), use add_payee_rules instead.\n\n"
        "Single alias: payee_id + text. Several in one call (same payee or different "
        "payees): `aliases` — [{payee_id, text}, ...].\n\n"
        "Pass the raw text; the service normalizes it. Each item fails independently if "
        "the normalized form is already claimed by a different payee."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        AddPayeeAliasesInput, array="aliases", tool_name="add_payee_aliases"
    ),
    mutates=("payees", "ledger"),
)
async def add_payee_aliases(input_data: dict):
    items = _items_or_bare(input_data, "aliases", ("payee_id", "text"))
    if not items:
        return json.dumps(
            {"ok": False, "error": "payee_id and text (or aliases) is required"}
        ), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id", "")
        text = item.get("text", "")
        try:
            result = payee_registry.add_alias(pid, text)
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (ConflictError, NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="added")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── remove_payee_aliases ─────────────────────────────────────────────────────────────


class RemoveAliasItem(NestedInput):
    """Schema-of-record for a ``remove_payee_aliases.aliases`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    alias: str = Field(
        description="The exact normalized alias string to remove (as shown in get_payees registry output)."
    )


class RemovePayeeAliasesInput(ToolInput):
    """Schema-of-record for ``remove_payee_aliases`` (see ``schema_from_model``)."""

    payee_id: Optional[str] = Field(default=None, description=_PAYEE_ID_DESC)
    alias: Optional[str] = Field(
        default=None, description="The exact normalized alias string to remove."
    )
    aliases: List[RemoveAliasItem] = Field(
        default_factory=list,
        description=(
            "Remove MULTIPLE aliases in one call: [{payee_id, alias}, ...]. Omit "
            "when removing a single alias via payee_id/alias above."
        ),
    )


@tool(
    "remove_payee_aliases",
    description=(
        "Remove a normalized alias from one or many payees. Use get_payees to inspect the "
        "current aliases list and copy the exact normalized string to remove. No-op per "
        "item if the alias is not present.\n\n"
        "Single removal: payee_id + alias. Several in one call: `aliases` — [{payee_id, "
        "alias}, ...]."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        RemovePayeeAliasesInput, array="aliases", tool_name="remove_payee_aliases"
    ),
    mutates=("payees", "ledger"),
)
async def remove_payee_aliases(input_data: dict):
    items = _items_or_bare(input_data, "aliases", ("payee_id", "alias"))
    if not items:
        return json.dumps(
            {"ok": False, "error": "payee_id and alias (or aliases) is required"}
        ), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id", "")
        alias = item.get("alias", "")
        try:
            result = payee_registry.remove_alias(pid, alias)
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="removed")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── add_payee_rules ──────────────────────────────────────────────────────────────────
# No broadcast case: a rule's match value must be unique across payees (see
# `_check_rule_conflict`), so the SAME rule on two payees is a conflict, not a share.

_MATCH_TYPE_DESC = "How to match: 'contains' (substring), 'is' (exact), or 'regex'."
_RULE_VALUE_DESC = "The pattern to match against the raw transaction description."


class AddRuleItem(NestedInput):
    """Schema-of-record for an ``add_payee_rules.rules`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    match_type: Literal["contains", "is", "regex"] = Field(description=_MATCH_TYPE_DESC)
    value: str = Field(description=_RULE_VALUE_DESC)


class AddPayeeRulesInput(ToolInput):
    """Schema-of-record for ``add_payee_rules`` (see ``schema_from_model``)."""

    payee_id: Optional[str] = Field(default=None, description=_PAYEE_ID_DESC)
    match_type: Optional[Literal["contains", "is", "regex"]] = Field(
        default=None, description=_MATCH_TYPE_DESC
    )
    value: Optional[str] = Field(default=None, description=_RULE_VALUE_DESC)
    rules: List[AddRuleItem] = Field(
        default_factory=list,
        description=(
            "Add MULTIPLE rules in one call: [{payee_id, match_type, value}, ...]. "
            "Omit when adding a single rule via the top-level fields above."
        ),
    )


@tool(
    "add_payee_rules",
    description=(
        "Add a raw-match renaming rule to one or many payees in one call. Rules run BEFORE "
        "the normalizer on every import/create, so they resolve descriptions that the "
        "normalizer can't handle cleanly (e.g. 'ACME COFFEE #123' → Acme Coffee payee via "
        "contains='acme coffee').\n\n"
        "match_type options:\n"
        "  • 'contains' — description contains this text (case-insensitive). Most common.\n"
        "  • 'is'       — description exactly equals this text (case-insensitive).\n"
        "  • 'regex'    — Python regex matched against the full description.\n\n"
        "Single rule: payee_id + match_type + value. Several in one call (same payee or "
        "different payees): `rules` — [{payee_id, match_type, value}, ...].\n\n"
        "Use get_payees to find payee ids. Confirm with the user before adding."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        AddPayeeRulesInput, array="rules", tool_name="add_payee_rules"
    ),
    mutates=("payees", "ledger"),
)
async def add_payee_rules(input_data: dict):
    items = _items_or_bare(input_data, "rules", ("payee_id", "match_type", "value"))
    if not items:
        return json.dumps(
            {"ok": False, "error": "payee_id, match_type and value (or rules) is required"}
        ), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id", "")
        mt = item.get("match_type", "")
        val = item.get("value", "")
        try:
            result = payee_registry.add_payee_rule(pid, mt, val)
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (ConflictError, NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="added")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── remove_payee_rules ───────────────────────────────────────────────────────────────


class RemoveRuleItem(NestedInput):
    """Schema-of-record for a ``remove_payee_rules.removals`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    index: int = Field(description="Zero-based index of the rule to remove.")


class RemovePayeeRulesInput(ToolInput):
    """Schema-of-record for ``remove_payee_rules`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None,
        description="One payee id, or a list to remove the SAME index on several at once.",
    )
    index: Optional[int] = Field(
        default=None,
        description="Zero-based index of the rule to remove. Broadcast to every id in payee_ids.",
    )
    removals: List[RemoveRuleItem] = Field(
        default_factory=list,
        description=(
            "Per-payee overrides: [{payee_id, index}, ...]. Takes precedence over "
            "payee_ids/index above."
        ),
    )


@tool(
    "remove_payee_rules",
    description=(
        "Remove a renaming rule from one or many payees by its index in each payee's rules "
        "list. Use get_payees to inspect the current rules list and find the index.\n\n"
        "Single removal: payee_ids (one id) + index. The SAME index on SEVERAL payees: "
        "payee_ids as a LIST. Different indices per payee in one call: `removals` — "
        "[{payee_id, index}, ...] — removals on the SAME payee are applied "
        "highest-index-first so every index you listed still refers to the row you meant, "
        "regardless of order. Confirm with the user before removing."
    ),
    input_schema=schema_of(RemovePayeeRulesInput, tool_name="remove_payee_rules"),
    mutates=("payees", "ledger"),
)
async def remove_payee_rules(input_data: dict):
    items = _broadcast_or_updates(input_data, "removals", ("index",))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or removals) is required"}), None

    # Group by payee so several removals on the SAME payee are applied HIGHEST index
    # first — removing low-to-high would shift every later index out from under itself
    # (removing rule 0 turns what was rule 2 into rule 1).
    by_payee: dict[str, list[tuple[int, int]]] = {}
    for i, item in enumerate(items):
        pid = item.get("payee_id") or ""
        by_payee.setdefault(pid, []).append((i, int(item.get("index", 0))))

    results_by_index: dict[int, dict] = {}
    single_value: Optional[dict] = None
    for pid, entries in by_payee.items():
        for req_i, rule_idx in sorted(entries, key=lambda e: e[1], reverse=True):
            try:
                result = payee_registry.remove_payee_rule(pid, rule_idx)
                results_by_index[req_i] = {"index": req_i, "ok": True, "id": pid}
                single_value = result
            except (NotFoundError, ValidationError) as e:
                results_by_index[req_i] = {"index": req_i, "ok": False, "error": str(e)}

    results = [results_by_index[i] for i in range(len(items))]
    out = _batch_result(results, count_key="removed")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── set_payees_rules ─────────────────────────────────────────────────────────────────


class RuleSpec(NestedInput):
    """Schema-of-record for one rule inside a ``set_payees_rules`` replacement list (see
    ``schema_from_model``) — shared by the broadcast ``rules`` field and each
    ``updates[].rules`` entry, since both are the identical bare shape."""

    match_type: Literal["contains", "is", "regex"] = Field(
        description=(
            "How `value` is tested against the transaction's raw bank description: "
            "'contains' (substring), 'is' (whole string equals), 'regex' (Python regex "
            "search). All three are case-insensitive."
        )
    )
    value: str = Field(
        description=(
            "The pattern to match, non-empty (blank is refused). Payee rules run BEFORE "
            "alias lookup and the FIRST matching rule across ALL payees wins, so prefer a "
            "distinctive fragment over a broad one. An invalid regex is refused at write."
        )
    )


class SetRulesItem(NestedInput):
    """Schema-of-record for a ``set_payees_rules.updates`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description=_PAYEE_ID_DESC)
    rules: List[RuleSpec] = Field(
        description="The complete replacement rules list for this payee. Pass [] to clear all rules."
    )


class SetPayeesRulesInput(ToolInput):
    """Schema-of-record for ``set_payees_rules`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None,
        description="One payee id, or a list to set the SAME rules on several at once.",
    )
    rules: List[RuleSpec] = Field(
        default_factory=list,
        description=(
            "The complete replacement rules list. Pass [] to clear all rules. "
            "Broadcast to every id in payee_ids."
        ),
    )
    updates: List[SetRulesItem] = Field(
        default_factory=list,
        description=(
            "Per-payee overrides: [{payee_id, rules}, ...]. Takes precedence over "
            "payee_ids/rules above."
        ),
    )


@tool(
    "set_payees_rules",
    description=(
        "Replace ALL renaming rules on one or many payees in one call. Validates each "
        "entry. Use this when you want to reorder, bulk-edit, or clear all rules at once "
        "rather than adding/removing one at a time.\n\n"
        "Single payee: payee_ids (one id) + rules. The SAME replacement list on SEVERAL "
        "payees: payee_ids as a LIST. A different replacement list per payee in one call: "
        "`updates` — [{payee_id, rules}, ...].\n\n"
        "Each rule is {match_type: 'contains'|'is'|'regex', value: '<pattern>'}. An empty "
        "list clears all rules for that payee. Confirm with the user before replacing."
    ),
    input_schema=schema_of(SetPayeesRulesInput, tool_name="set_payees_rules"),
    mutates=("payees", "ledger"),
)
async def set_payees_rules(input_data: dict):
    items = _broadcast_or_updates(input_data, "updates", ("rules",))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or updates) is required"}), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id", "")
        rules = item.get("rules", [])
        try:
            result = payee_registry.set_payee_rules(pid, rules)
            results.append({"index": i, "ok": True, "id": pid})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="updated")
    return json.dumps(
        _single_or_batch(out, results, single_value, "payee"), ensure_ascii=False
    ), None


# ── apply_payee_rules ────────────────────────────────────────────────────────────────
# Deliberately NOT pluralized: this rescans the ENTIRE ledger against EVERY payee's rules
# and aliases — there is exactly one ledger, so "apply to several ledgers" is incoherent
# (the same reasoning as restore_checkpoint). apply_payee_rules_to_payees is the already-
# scoped, already-plural sibling for "just these payees".


class ApplyPayeeRulesInput(ToolInput):
    """Schema-of-record for ``apply_payee_rules`` (see ``schema_from_model``)."""


@tool(
    "apply_payee_rules",
    description=(
        "Re-point existing transactions to match the current payee rules and aliases. "
        "Run this after adding or editing renaming rules so that historical transactions — "
        "imported before the rules existed — are routed to the correct payees.\n\n"
        "For every non-deleted transaction, runs the resolver (rules first, then alias lookup) "
        "with create=false. If the result differs from the stored payee_id, the transaction is "
        "updated in place. Returns {updated: N}.\n\n"
        "This is a bulk write. The pre-write state is "
        "checkpoint-captured automatically."
    ),
    input_schema=schema_of(ApplyPayeeRulesInput, tool_name="apply_payee_rules"),
    mutates=("payees", "ledger"),
)
async def apply_payee_rules(input_data: dict):
    try:
        result = payee_registry.apply_payee_rules_to_history()
        return json.dumps(result, ensure_ascii=False), None
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), None


# ── apply_payee_rules_to_payees ──────────────────────────────────────────────────────
# Blast-radius note (carried over from the pre-batch tool): this is a bulk HISTORICAL
# relabel, not idle metadata — each id in payee_ids independently rescans every non-deleted
# transaction. Batching multiplies that per-call, so the response always reports the
# per-payee breakdown, keeping the true scope legible. Whether the owner is ASKED is the
# harness gate's decision (`_confirm_gate.should_gate`), never a parameter the model sets
# for itself — the `confirm=true` flag this note used to describe was removed 2026-08-27.


class ApplyPayeeRulesToPayeesInput(ToolInput):
    """Schema-of-record for ``apply_payee_rules_to_payees`` (see ``schema_from_model``)."""

    payee_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="One payee id, or a list to run this for several payees at once.",
    )


@tool(
    "apply_payee_rules_to_payees",
    description=(
        "Re-point existing transactions to one or many payees, using only each payee's OWN "
        "renaming rules. Unlike apply_payee_rules (which rescans the ENTIRE ledger against "
        "EVERY payee's rules and aliases), this only touches transactions whose description "
        "matches one of the given payee's rules and are not already assigned to it.\n\n"
        "Pass payee_ids as a single id, or a LIST to run this for several payees in one "
        "call — each is still scoped to its own rules only. Use this whenever the ask is "
        "about specific payees, not the whole book.\n\n"
        "This is a bulk write. "
        "The pre-write state is checkpoint-captured automatically."
    ),
    input_schema=schema_of(ApplyPayeeRulesToPayeesInput, tool_name="apply_payee_rules_to_payees"),
    mutates=("payees", "ledger"),
)
async def apply_payee_rules_to_payees(input_data: dict):
    ids = _payee_ids_param(input_data)
    if not ids:
        return json.dumps({"error": "payee_ids is required"}, ensure_ascii=False), None
    if len(ids) == 1:
        try:
            result = payee_registry.apply_payee_rules_to_existing(ids[0])
            return json.dumps(result, ensure_ascii=False), None
        except NotFoundError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False), None

    total = 0
    by_payee: list[dict] = []
    for pid in ids:
        try:
            result = payee_registry.apply_payee_rules_to_existing(pid)
            total += result.get("updated", 0)
            by_payee.append({"payee_id": pid, "updated": result.get("updated", 0)})
        except NotFoundError as e:
            by_payee.append({"payee_id": pid, "error": str(e)})
    return json.dumps({"updated": total, "by_payee": by_payee}, ensure_ascii=False), None


# ── apply_payee_categories ───────────────────────────────────────────────────────────
# Same blast-radius note as apply_payee_rules_to_payees above: a bulk historical relabel,
# scoped per payee.


class ApplyPayeeCategoriesInput(ToolInput):
    """Schema-of-record for ``apply_payee_categories`` (see ``schema_from_model``)."""

    payee_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="One payee id, or a list to run this for several payees at once.",
    )


@tool(
    "apply_payee_categories",
    description=(
        "Stamp one or many payees' own per-kind default categories onto ALL of their "
        "existing non-deleted expense/income/transfer/investment transactions that don't "
        "already carry it (split rows and adjustments are skipped). Use this to "
        "re-categorize a payee's PAST transactions after setting or changing its "
        "default_category — this does NOT touch payee resolution/rules at all, just the "
        "category field. Fails per payee if it has no default category set.\n\n"
        "Pass payee_ids as a single id, or a LIST to run this for several payees in one "
        "call.\n\n"
        "This is a bulk write. "
        "The pre-write state is checkpoint-captured automatically."
    ),
    input_schema=schema_of(ApplyPayeeCategoriesInput, tool_name="apply_payee_categories"),
    mutates=("payees", "ledger"),
)
async def apply_payee_categories(input_data: dict):
    ids = _payee_ids_param(input_data)
    if not ids:
        return json.dumps({"error": "payee_ids is required"}, ensure_ascii=False), None
    if len(ids) == 1:
        try:
            result = payee_registry.apply_default_category_to_transactions(ids[0])
            return json.dumps(result, ensure_ascii=False), None
        except (NotFoundError, ValidationError) as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False), None

    total = 0
    by_payee: list[dict] = []
    for pid in ids:
        try:
            result = payee_registry.apply_default_category_to_transactions(pid)
            total += result.get("updated", 0)
            by_payee.append({"payee_id": pid, "updated": result.get("updated", 0)})
        except (NotFoundError, ValidationError) as e:
            by_payee.append({"payee_id": pid, "error": str(e)})
    return json.dumps({"updated": total, "by_payee": by_payee}, ensure_ascii=False), None


# ── merge_payees ─────────────────────────────────────────────────────────────────────
# Name unchanged: "payees" (plural) already reflects that one merge touches two entities,
# so extending it to a BATCH of merges doesn't misdescribe it the way create_payee/
# rename_payee etc. did. Gains a `merges` array for several merges (e.g. folding a cluster
# of duplicates into one canonical payee) alongside the existing single-pair call.

_MERGE_SOURCE_DESC = "The payee to merge away (will be deleted)."
_MERGE_TARGET_DESC = "The payee to merge into (kept)."


class MergeItem(NestedInput):
    """Schema-of-record for a ``merge_payees.merges`` batch item (see
    ``schema_from_model``)."""

    source_id: str = Field(description=_MERGE_SOURCE_DESC)
    target_id: str = Field(description=_MERGE_TARGET_DESC)


class MergePayeesInput(ToolInput):
    """Schema-of-record for ``merge_payees`` (see ``schema_from_model``)."""

    source_id: Optional[str] = Field(default=None, description=_MERGE_SOURCE_DESC)
    target_id: Optional[str] = Field(default=None, description=_MERGE_TARGET_DESC)
    merges: List[MergeItem] = Field(
        default_factory=list,
        description=(
            "Multiple merges in one call: [{source_id, target_id}, ...]. Omit when "
            "merging a single pair via source_id/target_id above."
        ),
    )


@tool(
    "merge_payees",
    description=(
        "Merge one payee (source) into another (target). This:\n"
        "  1. Folds all of source's aliases into target (so future transactions that matched "
        "source now match target instead).\n"
        "  2. Re-points every transaction that had source's payee_id to target's payee_id.\n"
        "  3. Takes the later of source/target last_used (txn_count is live-computed from "
        "the re-pointed transactions — no stored value to merge).\n"
        "  4. Deletes the source entity permanently.\n\n"
        "Single pair: source_id + target_id. Several merges in one call (e.g. folding a "
        "cluster of duplicates into one canonical payee): `merges` — [{source_id, "
        "target_id}, ...], applied IN ORDER, so a later entry may reference a payee an "
        "earlier entry just merged into.\n\n"
        "Use this to clean up duplicates (e.g. 'Anthropic' + 'Anthropic PBC'). Get payee "
        "ids from get_payees. This is irreversible."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(MergePayeesInput, array="merges", tool_name="merge_payees"),
    mutates=("payees", "ledger"),
)
async def merge_payees(input_data: dict):
    items = _items_or_bare(input_data, "merges", ("source_id", "target_id"))
    if not items:
        return json.dumps(
            {"ok": False, "error": "source_id and target_id (or merges) is required"}
        ), None

    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        src = item.get("source_id", "")
        tgt = item.get("target_id", "")
        try:
            result = payee_registry.merge_payees(src, tgt)
            results.append({"index": i, "ok": True, "source_id": src, "target_id": tgt})
            single_value = result
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="merged")
    return json.dumps(
        _single_or_batch(out, results, single_value, "merged_into"), ensure_ascii=False
    ), None


# ── delete_payees ────────────────────────────────────────────────────────────────────


class DeleteItem(NestedInput):
    """Schema-of-record for a ``delete_payees.updates`` batch item (see
    ``schema_from_model``)."""

    payee_id: str = Field(description="The payee to delete (pay_… id).")
    reassign_to: Optional[str] = Field(
        default=None,
        description="Optional: id of another payee to receive this payee's transactions.",
    )


class DeletePayeesInput(ToolInput):
    """Schema-of-record for ``delete_payees`` (see ``schema_from_model``)."""

    # See update_price_alerts.alert_ids in alerts.py for why this is the bare (non-null)
    # Union with `default=None` — the literal's anyOf has no null branch even though the
    # field is optional.
    payee_ids: Union[str, List[str]] = Field(
        default=None, description="One payee id, or a list to delete several the same way."
    )
    reassign_to: Optional[str] = Field(
        default=None,
        description=(
            "Optional: id of another payee to receive every deleted payee's "
            "transactions. Broadcast to every id in payee_ids."
        ),
    )
    updates: List[DeleteItem] = Field(
        default_factory=list,
        description=(
            "Per-payee overrides when different payees need different reassignment "
            "targets in one call. Takes precedence over payee_ids/reassign_to above."
        ),
    )


@tool(
    "delete_payees",
    description=(
        "Delete one or many payee entities permanently. If reassign_to is given, a deleted "
        "payee's transactions are re-pointed to that payee before deletion (safe "
        "reassignment). If omitted, payee_id is cleared (set to null) on its transactions "
        "— they stay in the ledger but lose their payee link.\n\n"
        "Single payee: payee_ids (one id, + optional reassign_to). Delete SEVERAL payees "
        "the SAME way: payee_ids as a LIST (reassign_to, if given, applies to every one of "
        "them — e.g. folding a cluster of duplicates into one survivor). Different "
        "reassignment targets per payee in one call: `updates` — [{payee_id, "
        "reassign_to?}, ...] — takes precedence over the top-level fields.\n\n"
        "This is irreversible."
    ),
    input_schema=schema_of(DeletePayeesInput, tool_name="delete_payees"),
    mutates=("payees", "ledger"),
)
async def delete_payees(input_data: dict):
    items = _broadcast_or_updates(input_data, "updates", ("reassign_to",))
    if not items:
        return json.dumps({"ok": False, "error": "payee_ids (or updates) is required"}), None

    results: list[dict] = []
    single_value: Optional[tuple[str, Optional[str]]] = None
    for i, item in enumerate(items):
        pid = item.get("payee_id") or ""
        reassign = item.get("reassign_to") or None
        try:
            payee_registry.delete_payee(pid, reassign_to=reassign)
            results.append({"index": i, "ok": True, "id": pid, "reassigned_to": reassign})
            single_value = (pid, reassign)
        except (NotFoundError, ValidationError) as e:
            results.append({"index": i, "ok": False, "error": str(e)})

    out = _batch_result(results, count_key="deleted")
    if len(results) == 1:
        if single_value is not None:
            out["deleted"], out["reassigned_to"] = single_value
        else:
            out["error"] = results[0].get("error")
    return json.dumps(out, ensure_ascii=False), None
