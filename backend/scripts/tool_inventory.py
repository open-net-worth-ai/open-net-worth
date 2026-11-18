"""Print every registered AI tool with its parameters — the review artifact.

Why a script and not a doc: a hand-written tool list is the exact thing this codebase
keeps getting wrong. Descriptions drift from handlers, a rename leaves a dead name in a
stored playbook, a batch param keeps a singular name. A generated inventory cannot drift,
because it reads the live registry.

Usage
-----
    .venv/bin/python3 -m backend.scripts.tool_inventory              # human-readable
    .venv/bin/python3 -m backend.scripts.tool_inventory --json       # machine-readable
    .venv/bin/python3 -m backend.scripts.tool_inventory --lint       # ONLY the naming defects
    .venv/bin/python3 -m backend.scripts.tool_inventory --core       # just the CORE offer

The --lint pass encodes the rule the 2026-07-19 naming sweep applied:

    Is the array the set of ENTITIES the tool acts on, or an attribute OF one entity?

An entity set must be named plural, at BOTH the tool and the parameter. An attribute of a
single entity is correctly singular-named even though it is a list — `schedule_task` takes
`channels` because one task notifies many channels, and `update_grant` takes `tranches`
because one grant has many. Those are NOT defects and the linter must not flag them, which
is why ATTRIBUTE_LIST_PARAMS below is an explicit reviewed exemption rather than a
heuristic. A heuristic cannot tell the two apart; a person can, once.

The defect this catches, from a live failure the owner hit:

    delete_account(account_id='["acc_fbae2c470000", "acc_b1af41ae0000"]')
    -> {"ok": false, "error": "account not found — call list_accounts to get valid IDs"}

A singular parameter name that accepts a list invites the model to stringify it. Dispatch
now decodes that (registry.coerce_stringified_containers), but the name is the real fix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# Parameters that are legitimately a LIST but belong to ONE entity — reviewed, not guessed.
# Adding a name here is a decision: it asserts "this tool acts on one thing, and this list
# is an attribute of that thing." Get it wrong and the linter stops protecting that tool.
ATTRIBUTE_LIST_PARAMS: dict[str, str] = {
    "schedule_task.channels": "one task, many delivery channels",
    "update_scheduled_task.channels": "one task, many delivery channels",
    "simulate_retirement.streams": "one simulation, many streams",
    "simulate_retirement.events": "one simulation, many events",
    "update_grant.tranches": "one grant, many vesting tranches",
    "create_account.external_ids": "one account, many provider links (RFC connectors-as-data-sources)",
    "add_run_tasks.tasks": "one run, many tasks",
    "update_plan_scenario.streams": "one scenario, many streams",
    "update_plan_scenario.events": "one scenario, many events",
    "update_watchlist_item.search_queries": "one item, many queries",
    "update_watchlist_item.linked_tickers": "one item, many tickers",
    "update_watchlist_item.custom_feed_urls": "one item, many feeds",
    "set_price_alerts.channels": "one alert, many channels",
    "update_price_alerts.channels": "one alert, many channels",
    "set_funding_plan.funding_plan": "one plan, many rows",
    "propose_lot_dispose.lot_selection": "one disposal, many lots consumed",
    "update_goal.linked_account_ids": "one goal, many linked accounts",
    "reorder_envelope_groups.order": "the ordering IS the list",
    "reorder_envelopes_in_group.categories": "one group, an ordered list",
    "save_code_as_skill.parameters": "one skill, many parameters",
    "calculate.expressions": "one call, many expressions (batch by design)",
    # One entity, list-valued attributes — reviewed 2026-07-19 against each handler.
    "add_watchlist_item.search_queries": "one item, many queries",
    "add_watchlist_item.linked_tickers": "one item, many tickers",
    "add_watchlist_item.custom_feed_urls": "one item, many feeds",
    "create_agent.allowed_tools": "one agent, many tools",
    "create_agent.channels": "one agent, many channels",
    "create_skill.allowed_tools": "one skill, many tools",
    "create_skill.parameters": "one skill, many parameters",
    "create_commitment.installments": "one commitment, many installments",
    "create_commitment.funding_plan": "one commitment, one plan of many rows",
    "create_asset.facts": "one asset, its own free-form label/value fact list",
    "update_asset.facts": "one asset, its own free-form label/value fact list",
    "create_grant.tranches": "one grant, many vesting tranches",
    "create_plan_scenario.streams": "one scenario, many streams",
    "create_plan_scenario.events": "one scenario, many events",
    "create_goal.linked_account_ids": "one goal, many linked accounts",
    "create_envelope_group.categories": "one group, the categories placed in it",
    "notify.channels": "one notification, many delivery channels",
    "dispose_lots.lot_selection": "one disposal, many lots consumed",
    "get_portfolio.asset_class": "read FILTER, not an entity target",
    "get_portfolio.account_ids": "read FILTER, not an entity target",
    "get_portfolio.vesting_state": "read FILTER, not an entity target",
    # get_watchlist (2026-07-19d naming-finish param follow-up) is the SAME "read
    # everything, narrow by filters" shape as get_portfolio just above: unfiltered it
    # returns items across EVERY watchlist plus the full watchlists array and config, and
    # `values` only narrows that already-comprehensive read — it does not name the SET OF
    # ENTITIES the tool acts on the way delete_watchlists' `watchlist_ids` does. The tool
    # NAME stays singular for the same reason get_portfolio's does; renamed from `value`
    # ("value" was a poor name regardless of arity — see watchlist.py's module docstring).
    "get_watchlist.values": "read FILTER, not an entity target",
    "connector_sync.providers": "one sync sweep across providers",
    "add_transaction_refs.refs": "one transaction, many provenance pairs",
    "show_visual.blocks": "one visual in one reply, made of up to three blocks",
    # NOTE — seven entries were DELETED here by the 2026-07-19d naming-close lane, and the
    # deletion is the point. They exempted apply_lot_acquire.acquisitions,
    # apply_lot_dispose.disposals, apply_lot_delete.deletions,
    # apply_lot_dispose_undo.event_ids, apply_position_add.adds,
    # apply_position_update.updates and apply_position_remove.removals — every one of them
    # a genuine entity SET, admitted as such in the comment that carried them, exempted on
    # the sole grounds of "propose/apply name symmetry". Those tools were then renamed to
    # acquire_lots / dispose_lots / delete_lots / undo_lot_disposals / add_positions /
    # update_positions / remove_positions, which retires the symmetry argument (there is no
    # propose half left to stay symmetric with) AND satisfies the rule outright. So the
    # exemptions are gone rather than re-keyed: the linter now enforces those seven names
    # like any other, instead of a comment promising they are fine. An exemption that
    # survives the reason it was granted for is how a lint table rots into a rubber stamp.
}


def _types(spec: dict) -> set[str]:
    out: set[str] = set()
    raw = spec.get("type")
    if isinstance(raw, str):
        out.add(raw)
    elif isinstance(raw, list):
        out.update(t for t in raw if isinstance(t, str))
    for branch in spec.get("anyOf") or []:
        if isinstance(branch, dict):
            out |= _types(branch)
    return out


def _is_plural(word: str) -> bool:
    return word.endswith("s") and not word.endswith("ss")


def _fmt_type(spec: dict) -> str:
    ts = sorted(_types(spec))
    if "array" in ts:
        item = (spec.get("items") or {}).get("type")
        for branch in spec.get("anyOf") or []:
            if isinstance(branch, dict) and "array" in _types(branch):
                item = (branch.get("items") or {}).get("type") or item
        inner = f"[{item}]" if item else "[]"
        rest = [t for t in ts if t != "array"]
        return "|".join([*rest, inner]) if rest else inner
    return "|".join(ts) or "any"


# Tools whose root field is NOT a redundant mirror of its array — it is the BROADCAST
# VALUE, paired with a plural ids param ("hide THESE five"). Stripping `hidden` from
# set_payees_hidden would leave `payee_ids` with nothing to apply and the handler would
# build [{"payee_id": pid}] with no change in it — a silent no-op write, not a preserved
# fallback. These are a genuine two-shape design, not duplication, so they keep both.
BROADCAST_VALUE_TOOLS = {
    "delete_payees": "reassign_to broadcasts across payee_ids",
    "remove_payee_rules": "index broadcasts across payee_ids",
    "remove_watchlist_items": "reason/watchlist broadcast across item_ids",
    "set_news_sources_enabled": "enabled broadcasts across source_ids",
    "set_payees_autocategorize": "autocategorize broadcasts across payee_ids",
    "set_payees_default_category": "category/kind broadcast across payee_ids",
    "set_payees_hidden": "hidden broadcasts across payee_ids",
    "set_payees_rules": "rules broadcast across payee_ids",
    "update_price_alerts": "op/value/label/note broadcast across alert_ids",
    "update_watchlist_items": "label/note/… broadcast across item_ids",
}


def _item_properties(spec: dict) -> set[str]:
    items = spec.get("items") or {}
    if items.get("properties"):
        return set(items["properties"])
    for branch in spec.get("anyOf") or []:
        if isinstance(branch, dict) and "array" in _types(branch):
            return set((branch.get("items") or {}).get("properties") or {})
    return set()


def _dual_shape_defects(tool: str, schema: dict) -> list[str]:
    """A batch array advertised ALONGSIDE a root-level copy of its own item fields.

    Two spellings of one call is not generosity — it is a fork with no information in it.
    The model must pick, the pick means nothing, and a half-mix (a bare `payee_id` next to
    an `updates` array) type-checks while meaning something nobody designed. It also pays
    for the same shape twice in schema bytes on every request that offers the tool.

    `update_transactions` is the settled shape: advertise the array, keep accepting the
    bare form in the handler, leave that leniency undocumented. `schema_of_batch_only`
    applies it; this check is what stops the next batch tool from re-introducing the fork.
    """
    if tool in BROADCAST_VALUE_TOOLS:
        return []
    props = schema.get("properties") or {}
    out = []
    for name, spec in props.items():
        # A reviewed attribute list (one entity, many sub-rows) is not a batch form of the
        # tool, so a field name it shares with the root is vocabulary, not a fork.
        if f"{tool}.{name}" in ATTRIBUTE_LIST_PARAMS:
            continue
        mirrored = _item_properties(spec)
        dup = sorted((set(props) - {name}) & mirrored)
        if dup:
            out.append(
                f"schema advertises '{name}' AND a bare copy of its item fields "
                f"({', '.join(dup)}) — use schema_of_batch_only"
            )
    return out


def _dead_name_defects(text: str, dead: dict[str, str]) -> list[str]:
    """Descriptions that steer the model to a name no tool answers to any more.

    A description is the model's map of the tool surface. When it says "then call
    add_transaction", the model calls `add_transaction` — and since the alias table was
    deleted (ADR ``2026-08-25-tool-names-are-final``) that is a hard dispatch failure, not a
    silent redirect. The name is absent from the catalog and from every schema, so the model
    is being pointed at a tool it cannot see. Renaming a tool has to fix the SENTENCES about
    it too, not just its own key.

    ``dead`` comes from ``migrate_tool_names.RENAMES`` — the record of every name that has
    ever been retired, and so precisely the set a description must not use. It replaced the
    deleted ``registry._ALIASES``, which had served as this list until aliases were removed;
    ``tests/claim_sources.py`` made the same substitution for the prose sweep.
    """
    return sorted(
        {
            f"description names retired tool '{a}' — say '{c}'"
            for a, c in dead.items()
            if re.search(rf"\b{re.escape(a)}\b", text)
        }
    )


def undescribed_params(schema: dict, *, _path: str = "") -> list[str]:
    """Every parameter in ``schema`` with no description, at ANY depth.

    Walks into ``properties``, ``items`` and ``anyOf``/``oneOf`` branches. The obvious
    version of this check reads only top-level ``properties`` and reports a number that
    looks like success: measured 2026-07-20, top-level said **2 undescribed** while a
    depth-walking scan found **~154 more** nested inside array items and anyOf branches —
    including ``SetAlertItem.op``, the nested twin of a field that WAS documented, so the
    same concept was described in one shape and blank in the other.

    A model reading the schema does not care which nesting level a field sits at. A
    detector that cannot see most of the problem is worse than no detector, because it
    reports the row as clean. Hence this walks.

    Returns dotted paths (``updates[].category_id``) so a finding is actionable rather
    than just a count.
    """
    out: list[str] = []
    for key, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        here = f"{_path}{key}"
        if not (spec.get("description") or "").strip():
            out.append(here)
        out.extend(undescribed_params(spec, _path=f"{here}."))
        items = spec.get("items")
        if isinstance(items, dict):
            out.extend(undescribed_params(items, _path=f"{here}[]."))
        for branch in spec.get("anyOf") or spec.get("oneOf") or []:
            if isinstance(branch, dict):
                out.extend(undescribed_params(branch, _path=f"{here}."))
                b_items = branch.get("items")
                if isinstance(b_items, dict):
                    out.extend(undescribed_params(b_items, _path=f"{here}[]."))
    # De-duplicate while preserving order: a scalar-or-list anyOf reaches the same nested
    # field by two routes, and reporting it twice would inflate the count.
    return list(dict.fromkeys(out))


def inventory() -> list[dict[str, Any]]:
    from backend.ai.tools import TOOL_SCHEMAS
    from backend.ai.tools._groups import CORE_TOOL_NAMES
    from backend.scripts.tool_renames import RENAMES

    live = {t["name"] for t in TOOL_SCHEMAS}
    dead = {old: new for old, new in RENAMES.items() if old not in live}

    rows = []
    for t in sorted(TOOL_SCHEMAS, key=lambda x: x["name"]):
        schema = t.get("input_schema") or {}
        required = set(schema.get("required") or ())
        params = [
            {
                "name": k,
                "type": _fmt_type(v),
                "required": k in required,
                "takes_list": "array" in _types(v),
            }
            for k, v in (schema.get("properties") or {}).items()
        ]
        # Param descriptions steer the model exactly as the tool description does, so both
        # are swept — a stale name in `updates`' description is no less misleading.
        text = " ".join(
            [t.get("description") or ""]
            + [str(v.get("description") or "") for v in (schema.get("properties") or {}).values()]
        )
        rows.append(
            {
                "tool": t["name"],
                "core": t["name"] in CORE_TOOL_NAMES,
                "params": params,
                "defects": (
                    _defects(t["name"], params)
                    + _dual_shape_defects(t["name"], schema)
                    + _dead_name_defects(text, dead)
                ),
            }
        )
    return rows


# Object words that have no plural form — a plural test can never pass, and demanding one
# would produce a nonsense name ("move_moneys").
#
# "budget" joined this set in the 2026-07-19c naming-finish pass, as a DELIBERATE judgment
# call on `assign_budget` — not an oversight. `assign_budget`'s `assignments` array is, by
# the letter of the entity-vs-attribute rule, an entity SET (each row names a DIFFERENT
# envelope_id + its own amount/month — structurally identical to `set_envelope_targets`'
# `targets`, which this same pass DID pluralize). But `assign_budget` sits in the exact same
# file, doing the exact same "batch across envelopes" shape, as `move_money` — and
# `move_money` is already exempt here because "money" is the SUBSTANCE being acted on, not
# the enumerated targets (the envelopes are just where it goes). "budget" plays the
# identical substance role: in "assign budget to five envelopes," budget is a mass noun (like
# "assign money"/"allocate funds"), not a count of five different household budgets — pairing
# it with "s" produces a name that reads as "several distinct budgets" instead of "several
# envelope assignments within THE one budget," which is actively less honest than leaving it
# singular. Reversing the exemption stands or falls with `move_money`'s: keep both aligned
# rather than picking one arbitrarily. `assign_budget`'s own `assignments` param is already
# correctly plural, so the batch shape is not hidden — only the tool's own verb-object name
# reads as a domain (budget/money), matching its sibling.
UNCOUNTABLE_OBJECTS = {"money", "cash", "history", "config", "data", "budget"}

# Floor for the --lint vacuous-pass guard (see main()). Set far below the real registry
# size so adding or removing tools never touches it — it catches "the registry is empty",
# not "the registry changed".
_MIN_EXPECTED_TOOLS = 200


def _defects(tool: str, params: list[dict]) -> list[str]:
    """Naming defects, by the entity-vs-attribute rule in the module docstring."""
    out = []
    obj = "_".join(tool.split("_")[1:])
    for p in params:
        if not p["takes_list"]:
            continue
        if f"{tool}.{p['name']}" in ATTRIBUTE_LIST_PARAMS:
            continue
        if not _is_plural(p["name"]):
            out.append(f"param '{p['name']}' takes a list but is named singular")
        # ANY word in the object phrase being plural is enough — `set_transactions_cleared`,
        # `set_categories_hidden`, `set_payees_hidden` and `apply_rules_to_history` are all
        # correctly named, and an earlier version of this check flagged every one of them
        # because it only looked at the LAST word ("cleared", "hidden", "history").
        # Uncountables (`move_money`) can never satisfy a plural test at all, so they are
        # exempted explicitly rather than by accident.
        words = obj.split("_")
        if not any(_is_plural(w) for w in words) and not (set(words) & UNCOUNTABLE_OBJECTS):
            out.append(f"tool acts on many but is named singular ('{tool}')")
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--lint", action="store_true", help="only tools with naming defects")
    ap.add_argument("--core", action="store_true", help="only the CORE offer")
    ap.add_argument(
        "--undescribed",
        action="store_true",
        help="list every parameter with no description, at ANY nesting depth",
    )
    args = ap.parse_args()

    rows = inventory()
    checked = len(rows)

    # A LINT THAT CHECKED NOTHING MUST NOT PASS. Under --lint the printed count is the
    # number of DEFECTS, so "0 tools" is byte-identical whether 256 tools were checked
    # clean or zero were checked at all. An empty registry would sail through green.
    # A broken import already fails loudly (there is no try/except around it), but an
    # empty-but-importable TOOL_SCHEMAS would not — so assert a floor instead of trusting
    # that no future refactor introduces one. The floor is deliberately well below the
    # real count: it is a tripwire for catastrophic emptiness, not a count assertion that
    # would need editing every time a tool is added or removed.
    if args.lint and checked < _MIN_EXPECTED_TOOLS:
        print(
            f"FAIL: the registry reported only {checked} tools (expected at least "
            f"{_MIN_EXPECTED_TOOLS}) — refusing to report a clean lint against a registry "
            "that failed to populate."
        )
        return 1

    if args.core:
        rows = [r for r in rows if r["core"]]
    if args.lint:
        rows = [r for r in rows if r["defects"]]

    if args.json:
        print(json.dumps(rows, indent=2))
        # --lint --json is still a lint run: the exit code, not the output, is the gate.
        return 1 if (args.lint and rows) else 0

    for r in rows:
        flag = " [CORE]" if r["core"] else ""
        print(f"\n{r['tool']}{flag}")
        for p in r["params"]:
            req = "*" if p["required"] else " "
            print(f"  {req} {p['name']:28s} {p['type']}")
        for d in r["defects"]:
            print(f"  !! {d}")
    if args.undescribed:
        from backend.ai.tools import TOOL_SCHEMAS

        total = 0
        for t in sorted(TOOL_SCHEMAS, key=lambda x: x["name"]):
            missing = undescribed_params(t.get("input_schema") or {})
            if not missing:
                continue
            total += len(missing)
            print(f"\n{t['name']}  ({len(missing)})")
            for p in missing:
                print(f"    {p}")
        print(
            f"\n{total} parameter(s) with no description, across every nesting depth.\n"
            "A model reads the schema — an undescribed field on a write tool is one it has "
            "to guess at from the name alone."
        )
        return 0

    scope = f" (of {checked} checked)" if args.lint else ""
    print(f"\n{len(rows)} tools{scope} · '*' = required · '!!' = naming defect")

    # --lint EXITS NON-ZERO on any defect, so `make lint` fails the build. Without this the
    # linter printed its findings and returned 0 — it reported the 80 defects four lanes
    # then fixed, and would have reported the 81st just as quietly while CI stayed green.
    # A naming defect is cheap to fix at review time and expensive afterwards (a tool name
    # is persisted data: renaming it later needs an alias, a migration entry and a golden
    # update), which is exactly the shape of thing that belongs in a blocking gate.
    if args.lint and rows:
        print(
            f"FAIL: {len(rows)} tool(s) with naming defects — see backend/scripts/tool_inventory.py"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
