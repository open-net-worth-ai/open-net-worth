"""Regression test: total TOOL_SCHEMAS json.dumps size stays under a budget.

This catches prose creep — a contributor adding a verbose tool description that
inflates the per-request AI token cost without anyone noticing. Two budgets:

1. Full registry budget: guards total TOOL_SCHEMAS (all 215+ tools). Set well
   above the current full-registry baseline so normal additions don't trip it,
   but a runaway description block will.
   Baseline after lane/r3-scoping: ~216K chars.
   Budget: 290_000 chars. Raised from 260_000: five lanes added 11 new tools
   (set_category_color, set_envelope_target,
   simulate_cashflow growth/commitments params, FIRE profile fields,
   list/create/update/delete_plan_scenario, get/set_plan_baseline),
   pushing the registry to ~269.5K; then the two-way screen contract added
   run_screen_command (~281K — its description deliberately carries the
   complete-what-if + parked-chip semantics). 290K restores headroom.

2. Default-offer budget: guards the slim interactive offer (non-CORE tools
   excluded, no extra groups unlocked). This is what every interactive turn
   pays — so it's the cost-sensitive number.
   Baseline after lane/r3-scoping: ~109K chars (96 tools).
   Budget: 120_000 chars (~10K headroom for near-term additions).
"""

import json

import pytest


@pytest.fixture(scope="module")
def tool_schemas():
    from backend.ai.tools.registry import build_tool_schemas

    return build_tool_schemas()


def test_total_tool_schemas_size_under_budget(tool_schemas):
    """Total json.dumps(TOOL_SCHEMAS) must stay under the prose-creep budget."""
    total = len(json.dumps(tool_schemas))
    # 290k (2026-07-11) covers both lanes' additions: screen-command tools AND the
    # +2 routing tools (get/update_routing_config, ~1.1k, prose-trimmed).
    # 296k (2026-07-12, options-e2e): +8 option tools (~5.9k after two prose-trim
    # passes — shared field props, bare-typed apply schema). A new money domain's
    # read/propose/apply surface is intentional growth, not prose creep.
    # 298k (2026-07-16, batch-aware-dedup Phase 3): bulk_add_transactions gained the
    # duplicate pre-flight's abort-and-report contract + the `decisions` re-submit
    # parameter (~1.4k after two prose-trim passes on the tool description + schema).
    # ALSO covers INT9 Phase B (same day, concurrent lane): +4 news-source tools
    # (get/set_news_source_enabled/add/remove_news_source, ~1.2k after aggressive
    # prose-trimming — bare-typed schema properties, one-line descriptions).
    # 299k (2026-07-17, SI-66 import routing rules): rules' shared _SET_SCHEMA gained
    # route_to_account (~0.6k after a prose-trim pass — the schema embeds in
    # test/create/update_rule, so every description char costs ×3).
    # 300k (2026-07-17, persistent uploads): +2 upload-management tools
    # (get_uploads/delete_upload, ~0.4k after a prose-trim pass — one-line
    # descriptions, bare-typed ref_id). New user-facing surface, not prose creep.
    # 302k (2026-07-17, note→description+memo split): the ledger tools
    # (add/bulk_add/update_transaction) replaced the single `note` param with
    # `description`+`memo`, and rules gained opt-in memo_* match conditions + a
    # memo_append action (embedded in test/create/update_rule → ×3). ~1.4k after
    # prose-trim passes — a genuine new user-facing field (personal memo), not creep.
    # 304k (2026-07-19, tool-surface reliability program — pluralize categories):
    # add_category/rename_category/remove_category/set_category_color each gained a batch
    # field (`categories`/`renames`/
    # `removals`/`updates`) so N single-item calls collapse into one; the nested
    # per-item schema is necessarily inlined twice (no $ref support), which is most
    # of the ~3.3k added even after two prose-trim passes (item-level field prose
    # cut to a terse mirror of the already-explained single-item fields, per
    # `_txn_schema.py`'s "teach the rule once" convention). set_category_hidden
    # instead grew `category_id` into a scalar-or-list `anyOf` (the `_txn_schema.py`
    # idiom already used for account_ids/tag/payee_id) — genuine new capability
    # (owner, 3x: "make them accept multi queries... instead of just update_payee"),
    # not creep.
    # 2026-07-19: 304_000 -> 345_000. Seven lanes made every single-entity tool accept a
    # BATCH (reads and writes), which inlines an item-shape schema per tool — no $ref
    # support, so the shape is written twice. That is real new capability, not prose bloat;
    # each lane trimmed descriptions to fit and they still cumulatively exceeded the cap.
    #
    # This guards the FULL registry, which only ships whole to MCP/agent callers. What an
    # interactive turn pays is the CORE offer, measured right after this change at 13 tools
    # / 31,590 chars (~7,897 tok) — DOWN from ~13,758 tok at 20 tools before the CORE diet.
    # So per-turn cost improved while this number grew. If that stops being true, this
    # budget is the wrong guard and the CORE offer needs its own.
    # 2026-07-19c: 345_000 -> 350_000 after the intel/watchlist slice merged (13 tools
    # gained batch shapes). Trim commits on lane/payee-tools-multi and
    # lane/accounts-audit-batch-tools are pending and should claw some of this back.
    # 2026-07-20: 350_000 -> 362_000. A detector fix revealed 156 parameters carrying NO
    # description (the previous count of 2 came from a scan that only read top-level
    # `properties` — see backend/scripts/tool_inventory.py --undescribed). 47 were closed
    # on the MONEY-CRITICAL tools first, where a model guessing a field from its name
    # produces a wrong realized gain or a wrong tax classification rather than a cosmetic
    # error: dispose_lots 14->1, update_instruments 12->3, add_positions 10->1,
    # acquire_lots 9->1, update_grant 4->0. That landed at 349,987 of 350,000 — THIRTEEN
    # characters — which is not a margin: the next description of any length would have
    # turned this test red for a lane unrelated to the cause, and a gate people learn to
    # bypass is worse than no gate.
    # This is the docstring's own "intentional growth, not prose creep" case, and ~650
    # chars of verifiably DEAD text were reclaimed first (five array descriptions still
    # said "Omit the single position_id/quantity fields" after schema_of_batch_only had
    # already stripped them). The 109 params still undescribed cannot be documented
    # without this headroom.
    # NOTE the asymmetry that makes this low-risk: THIS budget guards the full registry,
    # which is only paid when a tool is loaded via find_tools. The cost-sensitive number
    # is the default-offer budget below (the slim interactive offer every turn pays) —
    # unchanged by this work, and currently ~33K of its own 120K.
    # 2026-07-20 (SECOND raise the same day): 362_000 -> 380_000, for the two
    # parameter-description lanes (109 undescribed params -> 30). Each lane fit alone
    # (355,421 and 360,788); together they land at 366,375, which is how a per-lane check
    # misses a combined breach.
    #
    # I am flagging the pattern rather than quietly bumping it twice: this cap has now been
    # raised NINE times (260K -> 290 -> 296 -> 298 -> ... -> 345 -> 350 -> 362 -> 380), always
    # for a defensible reason, and never once lowered. A number that only ever moves up is not
    # a gate; it is a speed bump with a changelog.
    #
    # Looked for reclaimable bloat before raising and found none: the distribution is FLAT
    # (255 tools, mean 1,436 chars, the ten biggest are 16% of the total) and 34% of the
    # registry is tool-description prose, which is the prose that makes the tools usable. The
    # growth is real, so the honest move is to raise it and say so.
    #
    # The design is the wrong SHAPE and that is boarded: an absolute cap on a registry that
    # legitimately grows will be raised forever. What actually catches "prose creep" — the
    # thing this test was written for — is a per-change DELTA or a per-tool MEAN, either of
    # which stays meaningful as the registry grows. See docs/launch/BOARD.md.
    #
    # The number that is genuinely cost-sensitive is the default-offer budget below (what
    # every interactive turn pays); it is unaffected by all of this and sits at ~34K of 45K.
    # 2026-07-31: 380_000 -> 385_000 (the TENTH raise — see the flag above, which still
    # stands and which I am not going to pretend this raise disproves). Cause: the two-step
    # vest (ADR 2026-07-31) added `record_release` + `list_pending_releases` and a
    # `release_required` field that necessarily inlines into BOTH create_grant and
    # update_grant. Trimmed BEFORE raising, not after: the first draft measured 383,347
    # (over by 3,347) and a prose pass on exactly these five schemas — not on unrelated
    # tools — took the additions from ~3,900 to ~2,700 chars, landing at 382,133. The
    # remainder is the capability itself: a vest whose shares are not delivered yet is a
    # state the model cannot otherwise see or act on, and the descriptions are what stop it
    # calling record_release on a grant that has no release step.
    # Unchanged, as always: these two tools are NOT in CORE, so the cost-sensitive
    # default-offer budget below is untouched by this.
    #
    #
    # 2026-07-31 (TENTH raise): 380_000 -> 388_000, for ONE tool — `check_duplicates`, the
    # pre-write duplicate check (the reason this tool exists: the same card payment booked twice by the email
    # agent, caught by eye). Measured: baseline 377,833 -> 383,516, i.e. +5,683 for the tool, of
    # which 4,615 is its input_schema. That schema is ~80% the per-item TRANSACTION shape, and it
    # is expensive for a reason worth stating rather than trimming: the item schema is a proper
    # SUBSET of `add_transactions`' own, built from the SAME `_txn_schema` field table, so "paste
    # the rows you were about to write" is literally true and the check classifies byte-identical
    # drafts to the write it precedes. Every field in the subset feeds a classifier path
    # (`settled_amount`/`original_*` are the F22 original-charge fingerprint; `category` decides
    # the derived `kind` the classifier branches on) — dropping any of them to save bytes would
    # buy the cap with a verdict that no longer matches what the write computes, which is the one
    # property that makes the tool worth calling.
    # Reclaimed what was actually reclaimable first: a prose-trim pass on the tool description
    # (1,199 -> 947 chars) — the only slack in the change. No other tool was touched.
    # The asymmetry above still holds and is why this is low-risk: `check_duplicates` is NOT in
    # CORE_TOOL_NAMES, so no interactive turn pays for it unless the model loads it; the
    # default-offer budget below is unchanged by this lane.
    #
    # 2026-07-31 (merge reconciliation): 388_000 -> 392_000. The two raises above were
    # measured INDEPENDENTLY — the two-step vest lane and the check_duplicates lane each
    # sized their addition against a baseline that did not contain the other's. Merged, the
    # real total is 389,375, over BOTH proposed caps. Nothing new was added here; this is
    # the arithmetic neither lane could see. Measured, not estimated.
    # 2026-08-02: 392_000 -> 398_000 for the unified Automation tool surface. Six new,
    # non-CORE tools add 4,945 measured chars: list/create/update/delete/run plus the
    # review-first preview tool whose nested draft schema is the majority of the increase.
    # They are loaded on demand, so the cost-sensitive default offer below is unchanged.
    # 2026-08-21: 398_000 -> 400_000 for the new Firecrawl web-search provider. Two new,
    # non-CORE, connector-gated tools (`firecrawl_search`/`firecrawl_fetch_page`) measured
    # 1,056 chars over the prior cap — the same shape as every other per-provider search
    # pair, loaded on demand via `find_tools` and hidden until the connector is enabled, so
    # the default offer below is unchanged.
    # 2026-08-22: 400_000 -> 404_000 for the Yahoo Finance / CoinGecko connector migration.
    # Two new, non-CORE, connector-gated tools (`yahoo_get_ticker_prices`/
    # `coingecko_get_crypto_prices`) took the measured total from 399,338 to 401,552 — 2,214
    # chars, the same per-provider shape as the Firecrawl raise above. Both are hidden until
    # their connector is enabled and reached via `find_tools`, so the cost-sensitive default
    # offer below is unchanged. Measured at both commits, not estimated.
    # 2026-08-23: two sibling history tools (`yahoo_get_price_history`/
    # `coingecko_get_price_history`, same connector gating) took the measured total from
    # 401,552 to 403,778 — 2,226 chars. Still under the 404_000 cap (no raise needed);
    # noted here so the next raise's delta is measured against the right baseline.
    #
    # 2026-08-23 (merge): the pluggable-budget-engines RFC landed on top of the two history
    # tools above. Each lane measured its addition against a baseline that did not contain the
    # other's, so the cap is re-measured HERE against the merged registry rather than taking
    # either lane's number. See the RFC note below for that lane's own trim work.
    #
    # 2026-08-23: for the pluggable-budget-engines RFC
    # (`.claude/rfcs/2026-08-22-pluggable-budget-engines.md`, Decision F). Five new, non-CORE
    # tools (`list_budget_engines`, `get_budget_view`, `draft_budget_engine_config`,
    # `apply_budget_verb`, `set_home_budget_engine`) measured 4,993 chars pre-trim, landing the
    # registry at 406,555 — over the prior cap by 2,555. Trimmed BEFORE raising: heaviest on
    # `apply_budget_verb`'s own description (924 -> 656 chars) — dropped the inline RFC
    # citation and the redundant "mirrors apply_budget_change" framing, kept the (engine_id,
    # verb_id, params) call shape, "verbs are per-engine and discoverable via
    # list_budget_engines", and "an unknown verb errors, naming the real ones" (the verb_id
    # field's own per-engine verb-id enumeration was left untouched — that's the one thing
    # standing between the model and guessing a zero_based verb name). The other four each lost
    # their inline RFC/Decision citation too (no other tool description in the registry carries
    # one — that framing belongs in this module's docstring, not in what every call pays for)
    # plus light wording trims. That brought the five tools to 4,524 chars and the real total to
    # 406,086. 408,000 leaves the usual few-thousand-char headroom rather than fitting to the
    # byte. None of the five are in CORE_TOOL_NAMES, so the cost-sensitive default-offer budget
    # below is unaffected — still exercising only the pre-existing core set.
    # 2026-08-23: 412_000 -> 414_000. Read-only SQL access adds two tools (run_sql_query,
    # describe_db_schema, ~1.8k including their parameter schemas). Real new capability —
    # the questions a fixed-projection tool cannot express — not prose creep, and trimmed
    # twice before raising this: the two descriptions went 1094 -> 781 and 720 -> 503 chars,
    # keeping only the four things a model must know (read-only is enforced; call
    # describe_db_schema first because most fields live in a `payload` JSON column; results
    # are capped and the true total is unknown when truncated; this is never a source for a
    # reported figure). Per the docstring above, this guards the FULL registry, which only
    # ships whole to MCP/agent callers — NEITHER tool is in the CORE offer, so what an
    # interactive turn pays is unchanged by this.
    # 2026-09-06: 414_000 -> 429_000 for RFC ``connectors-as-data-sources``. connector_sync
    # and the nine per-provider ``<provider>_sync_now`` tools are RETIRED outright (no
    # single-tool replacement for the bulk name — see ``tool_renames.RENAMES``) and replaced
    # by nine ``<provider>_fetch_transactions``/``alpaca_fetch_positions``/
    # ``ibkr_fetch_activity`` tools, ~21,954 measured chars. Each carries its OWN full
    # schema even though seven share one Pydantic model (`FetchTransactionsInput`) — a tool
    # per provider is the point, so `connector=` gating and a permission decision apply
    # per-provider rather than to one shared name (the RFC's stated reason for retiring the
    # bulk `connector_sync` tool in the first place). Trimmed before raising: every
    # description's non-'ok'-status sentence dropped its inline enumeration of the five
    # status literals (already documented on the result's own `status` field), saving ~700
    # chars across the nine. None of the nine are in CORE_TOOL_NAMES — a fetch tool is
    # reached via `find_tools` once its connector is enabled, so the cost-sensitive
    # default-offer budget below is unaffected.
    #
    # 2026-09-06 (same base, independent raise later reconciled on merge): 414_000 -> 418_000.
    # The owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port) adds eight tools
    # (list_assets/get_asset/create_asset/update_asset/record_asset_value/dispose_asset/
    # delete_asset/create_asset_category), 8,027 chars measured (408,122 -> 416,149) — a whole
    # new domain the model previously had no tool surface for at all, not prose creep on an
    # existing tool.
    #
    # 2026-09-07 (merge of both, connectors-as-data-sources + assets-tab-faithful-port):
    # provisional 440_000 pending a re-measure with both tool sets registered together —
    # correct to the real measured total + the usual few-thousand-char margin once `make check`
    # runs on the merged tree (see docs/launch/BOARD.md, the connectors-as-data-sources entry).
    #
    # 2026-09-29: 440_000 -> 441_000. set_price_alerts/update_price_alerts gain `kind`
    # ('price' | 'move') so Lucius can set a one-day percent-move alert the store and price
    # monitor already supported but the tools could not reach — +629 measured chars
    # (439,740 -> 440,369), trimmed from +1,368 first. Neither tool is in CORE_TOOL_NAMES.
    # 2026-10-14: 441_000 -> 462_000. Measured 439,786 at the previous raise's commit
    # (3e3ee7e91, 2026-09-29) -> 459,009 now, after trimming. Gross growth +19.9K in two weeks,
    # all capability rather than prose creep: eight new non-CORE tools (+11.5K: update_lots,
    # get_spending_breakdown, get_connector_tools/get_connector_status, approve_transactions,
    # recall_memory, remove_from_documents, show_guide) net of the two retired connector tools,
    # plus ~12K across existing ones (create_artifact's pdf/xlsx/csv types and chart specs,
    # automation price triggers, update_watchlist_items, monitoring config, get_lots/get_returns).
    # Trimmed BEFORE raising: create_artifact 2,260 -> 1,500 and get_spending_breakdown 1,614 ->
    # 1,500 chars (the per-tool cap), plus the CORE offer's get_net_worth / get_transactions /
    # get_payees / get_portfolio prose, which took the default offer from 50,265 to 49,873 so its
    # own 50_000 cap stays unraised. The cost-sensitive default-offer number is unchanged.
    # 2026-10-29: 462_000 -> 465_000. Investment theses (ADR
    # 2026-10-27-an-investment-thesis-lives-on-the-instrument) add three non-CORE tools —
    # get/set/delete_investment_theses — a new domain, ~2.5K chars after trimming
    # get_investment_theses' description; the watchlist intent fields it absorbs shed a little.
    budget = 465_000
    assert total < budget, (
        f"TOOL_SCHEMAS json.dumps size is {total:,} chars — over the {budget:,}-char budget. "
        "Shorten a tool description or raise the budget intentionally (with a comment)."
    )


def test_default_offer_size_under_budget():
    """Default interactive offer (CORE_TOOL_NAMES, no extra groups loaded) must stay under budget.

    This is the cold-start per-turn cost: every fresh interactive session sends only
    the CORE set before the model calls find_tools to load more.
    2026-07-19 (docs/adr/2026-07-19-core-tool-offer-diet.md): the evidence-based CORE
    diet cut this from 20 to 13 tools (~37.4K -> ~30.7K chars) on measured per-tool
    session-spread over real Lucius sessions.
    Budget: 50_000 chars (raised from 45_000 when update_profile joined CORE — its own
    measured session-spread is low, but the onboarding skill's first instruction calls it,
    so leaving it non-core meant a guaranteed bounce-and-retry on every brand-new user's
    very first tool call; kept structural like import_transactions, not frequency-based).
    Its schema alone runs ~9.2K chars — three ISO-3166 country-code enums (nationalities /
    countries_of_residence / tax_residencies), ~200 entries each, each inlined in full
    because Anthropic's tool schema format has no $ref/$defs to dedupe them against.
    Raised to 55_000 by the owner (2026-10-14) to keep recall_memory core.
    """
    from backend.ai import assistant

    # Fresh call with no ContextVar state — mirrors a cold-start interactive turn.
    default_offer = assistant._active_tools()
    total = len(json.dumps(default_offer))
    budget = 55_000
    assert total < budget, (
        f"Default offer json.dumps size is {total:,} chars ({len(default_offer)} tools) — "
        f"over the {budget:,}-char budget. "
        "Shorten a core tool description or move a tool out of CORE_TOOL_NAMES."
    )


def test_no_single_description_over_limit(tool_schemas):
    """No individual tool description should exceed 1500 chars (catches single fat tools).

    Exceptions: bulk_add_transactions and import_transactions are owned by a separate lane
    and are excluded from this check — their size is tracked by the total budget test
    above. (``fetch_emails`` was the third exception; its replacements — search_messages,
    get_messages — are both comfortably under this cap and need no exclusion.)
    """
    limit = 1_500
    # These tools are maintained by another lane; exclude them from the per-tool cap.
    lane_excluded = {"bulk_add_transactions", "import_transactions"}
    offenders = [
        (t["name"], len(t["description"]))
        for t in tool_schemas
        if len(t["description"]) > limit and t["name"] not in lane_excluded
    ]
    assert not offenders, f"Tool descriptions over {limit} chars (shorten them): " + ", ".join(
        f"{n}={c}" for n, c in offenders
    )
