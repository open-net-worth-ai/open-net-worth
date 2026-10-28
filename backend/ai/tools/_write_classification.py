"""The two reviewed classification sets `tests/test_tool_domains.py` polices.

Moved out of that test file (2026-08-27) so PRODUCTION code can import them — decay
(``backend/ai/assistant.py``) needs to know "is this tool a write" to decide whether a
result is eligible for age decay at all, and importing from ``tests/`` is not a real
option. `tests/test_tool_domains.py` imports both from here now instead of defining
them; the sweep tests (every tool is EXACTLY one of `mutates=(...)` /
`READ_ONLY_TOOLS` / `NO_DOMAIN_WRITE_TOOLS`, no stale entries, etc.) are unchanged —
this file is data, not policy.
"""

# Reviewed READS: traced through their service/store call chain and confirmed to persist
# nothing. Adding a name here is a reviewed decision — trace the tool first, and remember
# that a `get_`/`list_`/`propose_` prefix proves nothing.
READ_ONLY_TOOLS = frozenset(
    {
        "calculate",
        # Read-only database access (ai/tools/sql.py). Neither can write: the database
        # itself refuses every non-read action (db/sql_console.py's authorizer), and neither
        # can reach a credential table.
        #
        # `run_sql_query` DOES append one `sql_query_log` row per call. That was briefly
        # classified NO_DOMAIN_WRITE for it, and that was wrong twice over. First, this
        # module's own docstring puts telemetry explicitly out of scope for `mutates=`
        # ("telemetry and conversation bookkeeping ... none of them back a view"), and
        # `tool_call_log` is already written by the DISPATCHER on behalf of every member of
        # this set — so "writes nothing persistent" here has always meant "writes no
        # user-meaningful data", not "causes no row anywhere". Whether the row is written by
        # the dispatcher or by the handler is not a distinction this codebase draws.
        # Second, and worse: NO_DOMAIN_WRITE membership pulls a tool into
        # `EXTERNAL_EFFECT_TOOLS` / `_SIDE_EFFECT_NO_DOMAIN` / the discovery gate's
        # `_GATE_NEVER_AUTO_EXECUTE`, whose other members are `email_send_message` (outbound
        # SMTP) and `run_script`/`run_code` (a subprocess). A bounded, read-only, logged
        # SELECT is not in that company, and blocking the discovery gate from auto-executing
        # it would degrade a pure read for no safety gain.
        "describe_db_schema",
        "run_sql_query",
        # Product-documentation reads. They parse markdown under docs/product/ and touch
        # no store, no network, and nothing under DATA_DIR — headless-safe by construction.
        "read_doc",
        "search_docs",
        # Memory search (ai/tools/memory.py) — reads MemoryStore.remembered_facts(), scores in
        # memory.recall_facts, writes nothing (unlike remember_fact/forget_fact).
        "recall_memory",
        # Keyword search over session_messages text (ai/tools/conversations.py): one SELECT,
        # visible conversations only, writes nothing.
        "search_past_conversations",
        # Was a read-SHAPED WRITER until 2026-08-23: `fetch_live` recorded every fetched
        # point into price_history, so asking what a price is appended to the portfolio's
        # own series. It now passes `record_history=False` — the series is accumulated by
        # the paths that VALUE holdings, not by a question about a ticker.
        "get_live_prices",
        # Was a read-SHAPED WRITER until 2026-08-23: it reconciled the auto-managed
        # My-holdings watchlist on every read. That is now `scheduler.watchlist_sync`
        # (daily + shortly after boot), so no reader writes.
        "get_watchlist",
        # Was a read-SHAPED WRITER until 2026-08-23: its zombie sweep stamped
        # status='superseded' on every listing. It now passes `persist_sweep=False` — the
        # filtering half still runs, so no reader sees a review for a deleted transaction.
        "list_match_reviews",
        # Direct single-provider price lookups (yahoo_get_ticker_prices /
        # coingecko_get_crypto_prices) — traced through services.prices.fetch_yahoo_quotes /
        # fetch_coingecko_quotes. Deliberately NOT the get_live_prices shape: neither caches
        # nor records into price_history (see that module's own note), so unlike
        # get_live_prices there is no write to classify at all.
        "yahoo_get_ticker_prices",
        "coingecko_get_crypto_prices",
        # Direct single-provider price HISTORY tools (2026-08-23) — same reasoning as the
        # two direct quote tools directly above: traced through
        # services.prices.fetch_history_result, which caches in an in-memory TTL dict only
        # (`_history_cache`), never the durable price_history table.
        "yahoo_get_price_history",
        "coingecko_get_price_history",
        # Reads accounts + transactions + statements and folds balances. Unlike
        # verify_statement_balance (which persists its balance_check onto the batch), this
        # one stores nothing — deliberately, so it can be re-run freely as a diagnostic.
        "check_statement_drift",
        # PRE-write duplicate check. Traced through services/match_preflight: matching.check
        # (indexed external-ref lookup), matching.match_batch (ledger read + in-memory stamp
        # restored in a finally), _load_rates (fx cache read), known_transfer_routes (ledger
        # read). It classifies rows that do NOT exist yet — it cannot enqueue a review, remember
        # a decision or append a transaction, and tests/dedup/test_check_duplicates.py asserts the
        # transaction store and review queue are byte-unchanged across a call.
        "check_duplicates",
        # Parses source and reads tool schemas to report the domain's own shape.
        # Touches no store — the introspection is entirely static.
        "describe",
        "describe_account",
        "fetch_page",
        "find_duplicates",
        "get_actions",
        "get_alert_channels",
        "get_alerts",
        "get_investment_theses",
        # Composes services.asset_detail.read_asset_detail (called with
        # record_price_history=False, persist_exchange=False — see that function's own
        # docstring for the two write side effects those flags exist to opt out of) +
        # services.asset_detail.analyst_consensus + IntelStoriesStore.find_story_by_ticker.
        # Traced: none of the three persists anything once the two flags are set — including
        # read_asset_detail's allow_stale=True background-refresh path (services.prices.
        # _refresh_in_background), which now threads record_history through too (M4 fix,
        # security-review 2026-08-29 — it used to hardcode record_history=True on that path
        # regardless of what the caller asked for, so a cache entry aged past the fast TTL but
        # short of the stale hard limit left a price_history row behind on every call).
        # Owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port). Traced: calls
        # services.owned_assets.get_asset / list_assets only — no store write.
        "get_asset",
        "get_asset_context",
        "get_budget_month",
        # The pluggable-budget-engines Lucius surface (ai/tools/budget_engines.py, RFC
        # 2026-08-22-pluggable-budget-engines.md Decision F) — these three read the ENGINES
        # registry / call an engine's fold()/draft(), never a store write. list_budget_engines
        # additionally reads BudgetEngineStateStore (a plain SELECT); get_budget_view/
        # draft_budget_engine_config call BudgetEngine.fold()/draft(), which for zero_based
        # delegates to services.planning.budget_month() — the same read get_budget_month above
        # already performs. The write half of Decision F (apply_budget_operation/
        # set_home_budget_engine, Phase 2/3) is now registered too — both carry a real
        # mutates=("budget",) tag via services.budget_engines.service, so they need no entry
        # here.
        "list_budget_engines",
        "get_budget_view",
        "draft_budget_engine_config",
        # Wraps services.card_statements.statement_summary (a pure fold over accounts +
        # transactions + statement records already read elsewhere) plus a local re-derivation
        # of the SAME payment-matching filter statement_summary itself uses, from fields the
        # summary already publishes. Neither path writes.
        "get_card_statement_status",
        "get_category_suggestions",
        "get_commitments",
        # The bulk multi-provider status read (providers= argument, defaults to all six)
        # PLUS, since 2026-08-13 (RFC §B3), each provider's own first-class, single-
        # provider twin — same underlying providers_service.status([key]) read, no write.
        "get_connector_status",
        "simplefin_get_status",
        "plaid_get_status",
        "lunch_money_get_status",
        "lunch_flow_get_status",
        "ynab_get_status",
        "enable_banking_get_status",
        "yodlee_get_status",
        "alpaca_get_status",
        "ibkr_get_status",
        # The connector CONFIGURATION reads (ai/tools/connector_config.py). Traced:
        # get_connector_tools folds services.connectors.list_connectors() (pure store
        # reads + in-memory synthesis) with tool_permissions.get_all_permissions() (one
        # SELECT), and derive_group_level is a pure function; list_mcp_servers projects
        # McpClientStore.public(), a read. Neither has a write twin ON PURPOSE — a tool that
        # SET a permission would let the model convert one approved confirm card into a
        # permanent bypass of every future one, and adding an MCP server is setup, which is
        # human-only (see the module docstring).
        "get_connector_tools",
        "list_mcp_servers",
        "get_cost_summary",
        "get_dividend_calendar",
        # Reads this session's own stored transcript + .fold.json sidecar (both already
        # persisted by the fold mechanism itself) and returns a bounded slice — never
        # writes anything. RFC context-folding-v2 §4.
        "get_earlier_conversation_detail",
        "get_equity_report",
        "get_fees_summary",
        "get_fire_projection",
        "get_fixed_income",
        "get_forecast_scorecard",
        "get_goal_allocation",
        "get_goals",
        "get_income",
        "get_income_forecast",
        "get_intel_digest",
        "get_ledger_summary",
        "get_lots",
        "get_model_config",
        "get_net_worth",
        "get_net_worth_history",
        "get_news_latency",
        "get_news_sources",
        "get_notifications",
        "get_option_contract",
        "get_options",
        "get_payees",
        "get_rule_suggestions",
        "get_plan_baseline",
        "get_portfolio",
        "get_portfolio_xray",
        "get_profile",
        "get_realized_pnl",
        "get_rebalance_plan",
        # Pure classification over the ledger — detection is recomputed on every read and
        # NEVER persisted; only the separate set_/declare_ tools write user corrections.
        "get_recurring_streams",
        "get_recent_logs",
        "get_report",
        "get_returns",
        "get_risk_signals",
        "get_screen_detail",
        "show_guide",  # lights up a control in the open app; writes nothing
        "get_spending_breakdown",
        "get_spending_summary",
        "get_statement",
        "get_transactions",
        "get_uncategorized_summary",
        "get_upcoming_vests",
        "get_uploads",
        "list_accounts",
        # Owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port). Traced: calls
        # services.owned_assets.list_assets only — no store write.
        "list_assets",
        # Unified Automation catalog read; AutomationStore().all() only.
        "list_automations",
        "list_categories",
        "list_checkpoints",
        "list_envelopes",
        "list_grants",
        "list_import_batches",
        "list_match_decisions",
        "list_pending_releases",
        "list_plan_scenarios",
        "list_skills",
        "list_statements",
        "list_watchlists",
        # NOTE: the 13 propose_* names that used to live here (propose_position_add/
        # update/remove, propose_lot_acquire/dispose/delete/dispose_undo, propose_vest(
        # _undo), propose_8b_import, propose_open_option, propose_close_option,
        # propose_record_income) are GONE from this set as of 2026-07-19 — each is now an
        # ALIAS of its apply_* twin (the propose_*/apply_* merge, tests/test_tool_domains.py's
        # module docstring), which carries a real mutates=(...) tag. Re-adding one here would
        # be the exact stale-entry bug test_exempt_sets_have_no_stale_entries polices for.
        # Traced: reads AttachmentStore metadata/payload + (for an image/pdf_pages kind)
        # calls the configured image reader, exactly like the automatic current-turn
        # fallback in ai/attachments.py. Persists nothing — the reader's result rides
        # back as the tool's own result text; the only store touch anywhere near this
        # path (AttachmentStore.mark_used, a last-use provenance stamp) is deliberately
        # NOT called from this tool, unlike the current-turn path.
        "read_attachments",
        # Same trace as read_attachments, above, pointed at ReceiptStore instead:
        # ReceiptStore.find_by_id/read_bytes (metadata + blob read) plus, for an
        # image/scanned-PDF receipt, the SAME configured image reader via the shared
        # ai/tools/_vision_read.py core. Persists nothing.
        "read_receipts",
        # The Documents read model (services/vault.py) over attachments, artifacts, statements
        # and receipts: list reads four stores' metadata; read goes through read_source (store
        # reads + the SAME configured image reader as read_attachments) and persists nothing.
        "list_documents",
        "read_documents",
        "read_skill_file",
        # Validates/normalizes against read-only reference options; it never reaches the
        # Automation mutation choke or any store write.
        "preview_automation",
        "save_code_as_skill",
        # Federates providers_for_country() + per-bank list_institutions() (Enable Banking
        # only, since GoCardless was removed) — pure reads. Used to carry mutates=("config",)
        # because the GoCardless lookup minted/persisted an OAuth token; that provider is
        # gone and Enable Banking's list_institutions signs a fresh JWT per call without
        # writing anything, so the tag was retired with it (scope-down, 2026-08-13).
        "search_institutions",
        "simulate_cashflow",
        "simulate_retirement",
        "trace_transactions",
        "web_search",
        "x_search",
        # Per-provider web-search tools (2026-08-13, RFC §B3) — same shape as
        # web_search/fetch_page above, just scoped to one named provider each.
        "duckduckgo_search",
        "duckduckgo_fetch_page",
        "tavily_search",
        "tavily_fetch_page",
        "perplexity_search",
        "perplexity_fetch_page",
        "serper_search",
        "serper_fetch_page",
        "google_pse_search",
        "google_pse_fetch_page",
        "firecrawl_search",
        "firecrawl_fetch_page",
    }
)

# Tools that DO something irreversible or stateful but have NO local data domain to
# refresh — so ``mutates=`` would emit a data_changed event no view could act on. Each
# entry carries its reason; this set is NOT a place to park an untraced tool.
NO_DOMAIN_WRITE_TOOLS: dict[str, str] = {
    # Outbound SMTP only. Irreversible externally; nothing local changes.
    "email_send_message": "sends mail via the connector's SMTP; writes no local store",
    # Loads tool groups/names into the turn's scope. The stickiness lands in chat session
    # meta + the discovery telemetry log — conversation bookkeeping, not a data domain.
    "find_tools": "turn tool-scope + session meta/telemetry only",
    # The running Automation's own task list. Lives in a per-turn ContextVar; the adapter
    # copies it onto the run row it was already writing. No data domain.
    "add_run_tasks": "turn-local run task list; the adapter persists it on the run row",
    "update_run_task": "turn-local run task list; the adapter persists it on the run row",
    # Narrows the turn to the skill's allowlist via ContextVars. The skill's own tool calls
    # dispatch individually through this same registry and carry their OWN mutates tags,
    # so the wildcard is unnecessary — coverage is not lost.
    "activate_skill": "turn-local scope narrowing; the skill's tool calls are tagged individually",
    # Sandboxed subprocess: no network, no DATA_DIR in the child env, scratch tempdir
    # removed after the run. Cannot reach a store.
    "run_script": "sandboxed subprocess, no store access",
    "run_code": "sandboxed subprocess, no store access; staging is an in-memory dict",
    # Documented in CLAUDE.md as ZERO store writes — ephemeral UI state pushed over SSE.
    "run_screen_command": "ephemeral UI state only, no store writes",
}
