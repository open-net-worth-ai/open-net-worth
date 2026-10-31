"""Presentation copy and native tool bundles for every connector kind and instance.

Pure data, split out of ``connector_catalog`` so the catalog stays about structure: what a
connector IS lives there, what it SAYS lives here. Keys are a connector id when the text is
specific to one instance (the six web-search providers share one kind but each has its own
blurb and tool pair) and a kind otherwise — ``connector_catalog`` resolves id before kind.

The tool NAMES are the exact registry ids (Lucius-callable verbatim), so the detail view lists
nothing the assistant cannot already call. Display-only: the registry stays the single source
of truth for execution, so nothing here touches a parity or headless surface.
"""

from __future__ import annotations

from backend.core.mcp_external import is_read_only
from backend.models.connector import ConnectorTool

# ── presentational metadata: short/long descriptions + the per-connector tool bundle ──
#
# These are DISPLAY-ONLY (the detail-view lane). The tool NAMES below are the EXACT registry
# ids (Lucius-callable verbatim — every name maps to a tool in CLAUDE.md's AI/chat-tools list),
# so the detail view lists nothing the assistant can't already call. Adding these touches NO
# parity/headless surface; the registry remains the single source of truth for execution.


def _t(
    name: str,
    description: str = "",
    source: str = "native",
    read_only: bool | None = None,
) -> ConnectorTool:
    """One entry of the static native bundle. ``level`` is deliberately NOT set here — it is
    per-connector user data, joined when a view is built (``connectors._decorate``); these instances are
    module-level singletons shared across every view.

    ``read_only`` defaults to the shared name-verb taxonomy and is OVERRIDDEN where this
    hand-written map knows better. That mirrors an MCP server's own ``readOnlyHint``: the
    allowlist is the fallback for a tool nobody can vouch for, and these are first-party
    tools we can. ``web_search`` is the case that needs it — it reads the web and writes
    nothing, but "search" is its OBJECT, not its verb prefix, so the allowlist files it
    under Write.
    """
    return ConnectorTool(
        name=name,
        description=description,
        source=source,
        read_only=is_read_only(name) if read_only is None else read_only,
    )


# Per-kind native tool bundles. A provider absent here (or mapped to ``[]``) is
# transport-only — it delivers/receives messages, it is not a Lucius tool.
#
# Email is a bidirectional comms channel: email_search_messages + email_get_messages (receive,
# split search/hydrate — see ADR 2026-08-12-email-search-hydrate-split, which retired the earlier
# fused fetch_emails tool these replaced) and email_send_message (send, SMTP compose). The
# hardcoded ingestion pipeline was retired — spending-logging is now the seeded
# email-transaction-logger Agent composing these blocks.
TOOLS: dict[str, list[ConnectorTool]] = {
    "email": [
        # Both READ-shaped, but the connector-first rename (2026-08-20) moved the read verb
        # off the name's PREFIX — same reason `web_search` below needs an explicit override:
        # the shared allowlist matches a leading verb, not one buried after `email_`.
        _t(
            "email_search_messages",
            "Search recent emails by date window (headers only, no body).",
            read_only=True,
        ),
        _t(
            "email_get_messages",
            "Read selected emails in full, by the ids email_search_messages shows.",
            read_only=True,
        ),
        _t("email_send_message", "Compose and send an email via SMTP (confirm-first)."),
    ],
    # ``web_search`` (the shared kind) is the FALLBACK only — every real card
    # is ONE of the id-scoped entries below (``connector_catalog.copy_for`` tries the id first),
    # showing that provider's own tool instead of the generic bulk pair every card used to
    # show identically. This kind-level entry is unreachable by any live card today; it stays
    # as the safety net the id-first lookup falls back to if a provider is ever added here
    # without its own entry.
    "web_search": [
        _t("web_search", "Search the web for current information.", read_only=True),
        _t("fetch_page", "Read the full text of a web page by URL."),
    ],
    "web_search:duckduckgo": [
        _t("duckduckgo_search", "Search the web via DuckDuckGo specifically.", read_only=True),
        _t(
            "duckduckgo_fetch_page",
            "Read the full text of a URL via DuckDuckGo specifically.",
            read_only=True,
        ),
    ],
    "web_search:tavily": [
        _t("tavily_search", "Search the web via Tavily specifically.", read_only=True),
        _t(
            "tavily_fetch_page",
            "Read the full text of a URL via Tavily specifically.",
            read_only=True,
        ),
    ],
    "web_search:perplexity": [
        _t(
            "perplexity_search",
            "Search the web via Perplexity (Sonar) specifically.",
            read_only=True,
        ),
        _t(
            "perplexity_fetch_page",
            "Read the full text of a URL via Perplexity (Sonar) specifically.",
            read_only=True,
        ),
    ],
    "web_search:serper": [
        _t("serper_search", "Search the web via Serper specifically.", read_only=True),
        _t(
            "serper_fetch_page",
            "Read the full text of a URL via Serper specifically.",
            read_only=True,
        ),
    ],
    "web_search:google_pse": [
        _t(
            "google_pse_search",
            "Search the web via Google Programmable Search specifically.",
            read_only=True,
        ),
        _t(
            "google_pse_fetch_page",
            "Read the full text of a URL via Google Programmable Search specifically.",
            read_only=True,
        ),
    ],
    "web_search:firecrawl": [
        _t("firecrawl_search", "Search the web via Firecrawl specifically.", read_only=True),
        _t(
            "firecrawl_fetch_page",
            "Read the full text of a URL via Firecrawl specifically — "
            "handles JS-rendered pages a plain fetch can't.",
            read_only=True,
        ),
    ],
    # Each of the nine aggregation providers has its OWN first-class, separately-schema'd
    # status/fetch pair — `<provider>_get_status` / `<provider>_fetch_transactions` (or
    # `alpaca_fetch_positions`/`ibkr_fetch_activity` for the two brokers) — promoted back
    # from pre-consolidation aliases (RFC .claude/rfcs/2026-08-10-connectors-architecture.md
    # §B3, 2026-08-13; full history in backend/ai/tools/connectors.py's module docstring).
    # These, not the generic bulk pair, are what a user looking at ONE connector's detail
    # page should see as "this connector's tools".
    #
    # RFC `connectors-as-data-sources` (2026-09-05) retired the per-provider `<id>_sync_now`
    # tools outright — no alias, `backend/scripts/tool_renames.py::RENAMES` — replacing each
    # with a READ-shaped `_fetch_*` tool: it returns draft rows/positions, books NOTHING
    # itself (the model's own next `add_transactions`/position-tool call does, through the
    # ordinary choke), and is `read_only=True` here for the same "read-shaped but writes a
    # raw-evidence snapshot + status" reason `email_search_messages` above is. The shared
    # `get_connector_status` BULK tool is KEPT (its sibling bulk `connector_sync` is the one
    # retired without a single-tool replacement — the model now calls whichever provider's
    # own fetch tool it means) — a real, offered tool documented as the multi-provider
    # convenience path (same module docstring; also docs/ai/TOOL_CATALOG.md's Connectors
    # entry). It is deliberately NOT listed on any single provider's bundle here, for the
    # identical reason it carries no `connector=` discovery tag: it spans all nine
    # providers, so no ONE connector owns it — a detail page's "Tool permissions" section is
    # scoped to what THIS connector's own tools are. (Its permission level is also keyed
    # per-connector-id in this UI; listing the same shared tool name under nine different
    # connectors would let a level set from one provider's page look like it covers the tool
    # everywhere, when it wouldn't.)
    "simplefin": [
        _t("simplefin_get_status", "Read the SimpleFIN connector status and sync stats."),
        _t(
            "simplefin_fetch_transactions",
            "Fetch new SimpleFIN transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "simplefin_list_accounts",
            "List the SimpleFIN accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "plaid": [
        _t("plaid_get_status", "Read the Plaid connector status and sync stats."),
        _t(
            "plaid_fetch_transactions",
            "Fetch new Plaid transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "plaid_list_accounts",
            "List the Plaid accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "lunch_money": [
        _t("lunch_money_get_status", "Read the Lunch Money connector status and sync stats."),
        _t(
            "lunch_money_fetch_transactions",
            "Fetch new Lunch Money transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "lunch_money_list_accounts",
            "List the Lunch Money accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "lunch_flow": [
        _t("lunch_flow_get_status", "Read the Lunch Flow connector status and sync stats."),
        _t(
            "lunch_flow_fetch_transactions",
            "Fetch new Lunch Flow transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "lunch_flow_list_accounts",
            "List the Lunch Flow accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "ynab": [
        _t("ynab_get_status", "Read the YNAB connector status and sync stats."),
        _t(
            "ynab_fetch_transactions",
            "Fetch new YNAB transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "ynab_list_accounts",
            "List the YNAB accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "enable_banking": [
        _t(
            "enable_banking_get_status",
            "Read the Enable Banking connector status and sync stats.",
        ),
        _t(
            "enable_banking_fetch_transactions",
            "Fetch new Enable Banking transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "enable_banking_list_accounts",
            "List the Enable Banking accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "yodlee": [
        _t("yodlee_get_status", "Read the Yodlee connector status and sync stats."),
        _t(
            "yodlee_fetch_transactions",
            "Fetch new Yodlee transactions for a date window; nothing is booked.",
            read_only=True,
        ),
        _t(
            "yodlee_list_accounts",
            "List the Yodlee accounts and which of yours each is linked to; no transactions.",
            read_only=True,
        ),
    ],
    "alpaca": [
        _t("alpaca_get_status", "Read the Alpaca connector status and sync stats."),
        _t(
            "alpaca_fetch_positions",
            "Fetch the Alpaca positions and cash snapshot; nothing is booked.",
            read_only=True,
        ),
    ],
    "ibkr": [
        _t("ibkr_get_status", "Read the IBKR connector status and sync stats."),
        _t(
            "ibkr_fetch_activity",
            "Fetch IBKR positions, trades and cash events for a date window; nothing is booked.",
            read_only=True,
        ),
    ],
    # Yahoo Finance / CoinGecko (market data, 2026-08-22): each connector's ONE
    # provider-specific tool — direct, no cross-provider fallback (unlike the shared,
    # untagged `get_live_prices`, which stays off BOTH bundles: it's the general-purpose
    # portfolio-pricing tool, available regardless of either connector's own state).
    "yahoo_finance": [
        _t(
            "yahoo_get_ticker_prices",
            "Query Yahoo Finance directly for one or more tickers — no CoinGecko fallback.",
            read_only=True,
        ),
        _t(
            "yahoo_get_price_history",
            "Chart historical prices for one ticker directly from Yahoo Finance — no CoinGecko fallback.",
            read_only=True,
        ),
    ],
    "coingecko": [
        _t(
            "coingecko_get_crypto_prices",
            "Query CoinGecko directly for crypto prices (and optional facts) — no Yahoo routing.",
            read_only=True,
        ),
        _t(
            "coingecko_get_price_history",
            "Chart historical prices for one crypto asset directly from CoinGecko — no Yahoo routing.",
            read_only=True,
        ),
    ],
    # Each channel's own SEND tool (retired the shared, untagged `notify` 2026-08-20 — see
    # ai/tools/notifications.py's module docstring). RECEIVE (a Telegram message arriving)
    # is still transport-only, no tool: the bot forwards it straight into the turn, there is
    # nothing for Lucius to call to "read" it.
    "telegram": [
        _t("telegram_send_message", "Send a message to the user's Telegram right now."),
    ],
    "webhook": [
        _t("webhook_send_message", "Send a message to the user's configured webhook right now."),
    ],
}

# Short subtitle (card + detail header) per kind.
#
# THE SHORT AND THE LONG ARE RENDERED TOGETHER, and must therefore say DIFFERENT things: the
# detail page prints the short as the identity sub-line and the long as the paragraph directly
# beneath it. They used to paraphrase each other ("POST notifications to any URL (e.g. a Slack or
# Discord incoming webhook)" over "Deliver notifications by POSTing to any URL — e.g. a Slack or
# Discord incoming webhook"), which reads as a stutter, not as two facts. The split:
#
#   short — WHAT IT IS, one line, no verb ceremony.
#   long  — WHAT YOU GET, what you must set up, and what it will NOT do. Never a restatement.
#
# Neither says "write-only". That is our storage vocabulary, it reads as a claim about the key's
# permissions at the vendor, and a separate lane is making stored secrets revealable — so it is
# also on its way to being false. What a blank field means is stated by the field that has one.
SHORT: dict[str, str] = {
    "email": "Auto-logs bank and broker activity, straight from your own mailbox.",
    "web_search": "Web search and page reading for Lucius.",
    # Web search is SIX connectors sharing one kind (one card per provider) —
    # ``connector_catalog.copy_for`` looks up by ``id`` before
    # ``kind``, so these override the family blurb above per provider. Verbatim from the
    # Browse & add catalog's own per-provider copy before the 2026-08-14 fix that made this
    # module the ONE place it is hand-authored (`dirCatalog.ts`'s entries now read it back off
    # the live `ConnectorView` via `directoryRows.resolveDirectory`, instead of duplicating it).
    "web_search:duckduckgo": "Web search with no account",
    "web_search:tavily": "Search built for AI agents",
    "web_search:perplexity": "Sonar search and answers",
    "web_search:serper": "Google results, fast and low-cost",
    "web_search:google_pse": "Google Custom Search",
    "web_search:firecrawl": "Search + full-page scraping, JS-rendered pages included",
    "telegram": "Chat with Lucius and get alerts on Telegram.",
    "webhook": "Send notifications to any URL.",
    "simplefin": "Bank and card accounts from the US and Canada, via a setup token.",
    "plaid": "Bank and card accounts from the US and Canada.",
    "lunch_money": "Transactions already aggregated in your Lunch Money account.",
    "lunch_flow": "Bank and investment accounts across 30+ countries, via OAuth.",
    "ynab": "Transactions already tracked in your YNAB budget.",
    "enable_banking": "Bank accounts from the EU and UK, under PSD2.",
    "yodlee": "Bank and card accounts, worldwide.",
    "alpaca": "Read-only: imports your Alpaca positions and cash",
    "ibkr": "Read-only: daily import of IBKR positions, trades, cash",
    "yahoo_finance": "Prices for stocks, ETFs and supported major crypto — no key needed.",
    "coingecko": "Crypto prices, search and facts — the fallback behind Yahoo Finance.",
    "mcp": "A third-party tool server you connected.",
}

# A fuller paragraph for the detail Modal per kind.
LONG: dict[str, str] = {
    "email": (
        "Lucius can search your mail by date, read the messages you point it at, and send from "
        "the same mailbox — reading never changes anything in your inbox. A ready-made "
        "Automation logs spending straight from your bank's notification emails, and the same "
        "reading capability books brokerage trade confirmations just as well; edit that one, or "
        "build your own."
    ),
    # Deliberately does NOT explain the Default provider: the control that sets it lives in this
    # connector's own form, and the sentence explaining it belongs beside that control, not in a
    # paragraph on the screen before.
    "web_search": (
        "Pick a provider and add its API key. Several can be set up at once, and Lucius can be "
        "asked for a specific one by name."
    ),
    # Same id-over-kind override as `SHORT` above, same provenance (the pre-2026-08-14
    # `dirCatalog.ts` per-provider `desc`).
    "web_search:duckduckgo": (
        "Search and page reads with nothing to set up, and no account to create. It is the "
        "default provider until you choose another one. Add a paid provider alongside it if "
        "you want a different index or higher rate limits."
    ),
    "web_search:tavily": (
        "Returns extracted page text rather than raw HTML, so Lucius gets clean content from "
        "both search results and page reads."
    ),
    "web_search:perplexity": (
        "Answers the query directly and returns the sources it cited, rather than a list of "
        "links to read."
    ),
    "web_search:serper": (
        "Google search results through serper.dev — the same index you would search yourself, "
        "at a low per-query price."
    ),
    "web_search:google_pse": (
        "Google's Programmable Search Engine. Needs an API key and a search engine ID you "
        "create in Google's console."
    ),
    "web_search:firecrawl": (
        "Search plus full page scraping, including JS-rendered pages a plain fetch can't read "
        "— the provider to pick if page reads are coming back thin or blocked."
    ),
    "telegram": (
        "Message your own bot to ask questions, send photos, and approve changes, with the same "
        "abilities Lucius has in the app. Price and news alerts arrive the same way. Only the "
        "chats you allow can reach it."
    ),
    "webhook": (
        "Alerts and digests are posted as JSON to the address you give — a Slack or Discord "
        "incoming webhook, or your own endpoint. Add a signing secret and it rides along as a "
        "request header, so the receiver can check the call came from you."
    ),
    "simplefin": (
        "SimpleFIN is a paid bridge (about $15 a year) some self-hosters use for banks Plaid "
        "doesn't reach. Create a SimpleFIN access, paste the one-time setup token it gives "
        "you, and balances and transactions flow into your ledger from then on."
    ),
    "plaid": (
        "Sign in to your bank through Plaid's hosted link, and balances and transactions flow "
        "into your ledger from then on. Each sync pulls only what is new, and anything already "
        "imported is skipped."
    ),
    "lunch_money": (
        "If you already track spending in Lunch Money, paste a personal access token from its "
        "Settings → Developers page and its transactions flow into your ledger from here on. "
        "Pulls transactions only — Lunch Money's own manually-tracked assets are not imported, "
        "and categories are left for you or an Automation to set."
    ),
    "lunch_flow": (
        "Sign in through Lunch Flow's own hosted page and link a bank — accounts and "
        "transactions flow into your ledger from then on. Covers thousands of institutions "
        "across 30+ countries. Needs a Lunch Flow developer client_id/secret pair (from "
        "lunchflow.app's own dashboard) configured first. Each sync pulls what's new by "
        "account, and anything already imported is skipped."
    ),
    "ynab": (
        "If you already track spending in YNAB, paste a personal access token from its "
        "Account Settings → Developer Settings page and its transactions flow into your "
        "ledger from here on. A token that can see more than one budget asks you to pick "
        "one in this connector's settings; when it can see only one, that one is picked "
        "automatically. Categories are left for you or an Automation to set."
    ),
    "enable_banking": (
        "Pick your bank, authorise access at the bank's own site, and balances and transactions "
        "flow into your ledger. Anything already imported is skipped."
    ),
    "yodlee": (
        "Yodlee covers banks and cards in the US, Canada and many international markets. You "
        "need two things: a set of API credentials, and a Yodlee user account for the bank logins "
        "themselves."
    ),
    "alpaca": (
        "Your open positions and cash are read into your portfolio and kept up to date. Pick "
        "paper or live to match the account your keys belong to. This connector only reads — it "
        "never places or cancels an order."
    ),
    "ibkr": (
        "Interactive Brokers exports your account as a Flex Query — a batch report rather than a "
        "live feed, so figures arrive about a day behind. Set the query up under Reports → Flex "
        "Queries → Activity, set Format to XML, include OpenPositions, Trades and CashTransactions, "
        "then paste its token and ID here. Shares and ETFs are priced for you; options and futures "
        "you price yourself. The token can only export data — it cannot place orders."
    ),
    "yahoo_finance": (
        "Every equity and ETF quote, plus supported major crypto pairs priced in USD, comes "
        "from Yahoo Finance first — no account or API key needed. CoinGecko is the fallback "
        "when Yahoo can't price something; the setting below controls which one goes first "
        "for crypto specifically."
    ),
    "coingecko": (
        "Used whenever Yahoo Finance can't price a crypto asset, plus crypto search and "
        "facts. Works with no key on a small shared free quota; add a Demo or Pro API key "
        "(from CoinGecko's own site) for a higher one — Pro plans set their own limits with "
        "CoinGecko directly."
    ),
    # No "mcp" entry: a custom server's page has no paragraph. Its subtitle is the one line
    # about it, and the publisher panel and Tool permissions below already say it is a
    # third-party server and what Lucius may call.
}
