"""The tool-rename ledger — every AI-tool name that has ever been retired, and what replaced it.

This is DATA, not a migration. It was extracted (2026-08-28) from a one-shot
`migrate_tool_names.py` that rewrote stored tool names in `agents.json` and skill frontmatter;
that migration is gone, because a fresh install has no stored old names to rewrite. The table
it carried is not gone, because two live consumers still need it:

  * `backend/scripts/tool_inventory.py --lint` (run by `make lint`) — flags a retired name that
    reappears in a shipped tool, which is the check that keeps a rename final.
  * `tests/test_portfolio_lots_batch_tools.py` — asserts a retired name does not resolve.

**Why a rename is recorded rather than aliased.** A tool name is persisted data: it lives in
stored automations, in skill frontmatter, in backups and in in-flight transcripts. Aliasing an
old name keeps it alive forever and lets two names drift; recording it here makes the rename
final and gives the linter something to check against. See
`docs/ai/TOOL_RENAMES.md` and ADR `2026-08-25-tool-names-are-final`.

Was EMPTY by design (2026-08-28) after a predecessor repository's 90 entries were dropped as
unreachable history. First real entries (2026-09-05, RFC ``connectors-as-data-sources``):
every bank/broker connector became a READ-ONLY data source, retiring ``connector_sync`` and
the nine ``<provider>_sync_now`` tools outright — no single-tool replacement for the bulk
``connector_sync`` name (the model now calls whichever provider's own fetch tool it means),
a direct one for each per-provider sync tool (its sibling fetch tool, RFC §A).

Add an entry when YOU rename OR retire a tool. Never remove one afterwards — an entry's
whole job is to outlive the name it retires, so the linter can catch a description still
naming the dead one.
"""

from __future__ import annotations

RENAMES: dict[str, str] = {
    # 2026-09-26, budget & category tools review (ADR 2026-09-26-budget-and-category-tools):
    # a category has no parent, so it is CREATED (conventions rule 2); and it is DELETED —
    # `remove_` detaches a thing from its parent.
    "add_categories": "create_categories",
    "remove_categories": "delete_categories",
    # Entity-set arity (conventions rule 1): several categories in one call.
    "link_category": "link_categories",
    "unlink_category": "unlink_categories",
    "move_envelope_to_group": "move_envelopes_to_group",
    # One envelope edit tool, not one tool per field: name/colour/note in `updates`.
    "rename_envelope": "update_envelopes",
    "set_envelope_note": "update_envelopes",
    "get_envelope_note": "list_envelopes (every row carries its note; envelope_ids=[…] reads one)",
    # 2026-09-26 (ADR 2026-09-26-deleting-an-envelope-deletes-it): "we should stop archiving
    # envelope, just delete" — delete_envelope now hard-deletes for real, so there is nothing
    # left to un-archive or to list as archived-and-restorable.
    "restore_envelope": "retired — deleting an envelope removes it; Undo right after a delete puts it back (restore_checkpoint for an older Lucius write)",
    "list_archived_envelopes": "retired — deleting an envelope removes it; Undo right after a delete puts it back (restore_checkpoint for an older Lucius write)",
    # 2026-09-26: wrote budget_config.rollover_start — Zero-Based's carry floor and Ready to
    # Assign's zero line — under a spending-limit name, bypassing the fresh start's safeguards.
    "set_spending_limit_rollover_start": "retired — a fresh start (Plan → Budget) moves the budget's start",
    # 2026-09-26, phase 3: the flat per-category spending limits (profile.monthly_budgets) have
    # no screen and feed no figure the user sees — get_spending_summary reads the active budget
    # method — so a limit written through these was invisible (a live chat built a whole "set
    # your caps" answer on them). A cap is the budget method's: 50/30/20 caps via
    # apply_budget_operation, Zero-Based targets via set_envelope_targets.
    "set_spending_limits": "retired — apply_budget_operation (50/30/20 set_caps / copy_targets_to_caps) or set_envelope_targets (Zero-Based)",
    "set_spending_limit_rollovers": "retired — Zero-Based envelopes carry their balance on their own",
    # 2026-10-11: it was web_search with the X scope appended — the guidance now lives in
    # web_search's description, and web_search is the search connector's tool.
    "x_search": "retired — web_search with (site:x.com OR site:twitter.com) in the query",
    # Savings goals are "Goals" in the app; "target" also named Zero-Based's per-envelope
    # target and the investment target mix — three meanings for one word.
    "get_targets": "get_goals",
    "get_target_allocation": "get_goal_allocation",
    "create_target": "create_goal",
    "update_target": "update_goal",
    "delete_target": "delete_goal",
    # "Verb" was the engines' internal word for a budget method's own action; the owner found
    # it meaningless in a chat step ("Apply budget verb"). Its `verb_id` param is `operation`.
    "apply_budget_verb": "apply_budget_operation",
    "connector_sync": "the specific provider's own fetch tool (e.g. plaid_fetch_transactions)",
    "simplefin_sync_now": "simplefin_fetch_transactions",
    "plaid_sync_now": "plaid_fetch_transactions",
    "lunch_money_sync_now": "lunch_money_fetch_transactions",
    "lunch_flow_sync_now": "lunch_flow_fetch_transactions",
    "ynab_sync_now": "ynab_fetch_transactions",
    "enable_banking_sync_now": "enable_banking_fetch_transactions",
    "yodlee_sync_now": "yodlee_fetch_transactions",
    "alpaca_sync_now": "alpaca_fetch_positions",
    "ibkr_sync_now": "ibkr_fetch_activity",
    # 2026-09-07: too easily read as "tag/flag this asset" rather than "record a new value
    # observation" — confused against create_asset's own `value` field, which already becomes
    # an asset's first mark at creation. See backend/ai/tools/asset_values.py.
    "add_asset_mark": "record_asset_value",
    # 2026-10-10 (owner): it now reports EVERY connector the Connectors screen lists, not only
    # the bank/broker aggregators, and reads are `get_`-prefixed (`connectors=` replaces
    # `providers=`).
    "connector_status": "get_connector_status",
    # 2026-10-10 (owner): it lists what a connector can do — its tools, read-only vs write,
    # each with its permission level — so it is named for the tool list.
    "get_connector_permissions": "get_connector_tools",
}
