// The Browse & add catalog (handoff spec §8).
//
// Ported from design_handoffs/connectors_handoff/prototype/redesign/connectors.jsx (`DIRECTORY`),
// with one hard rule the prototype could not enforce: EVERY entry here must correspond to a
// connector `backend/services/connectors.py` actually emits, or — for MCP — to a real server a
// user can point the add flow at. A catalog that disagrees with the live connector list is the
// recurring defect in this surface — past instances: a missing Alpaca card, a Lunch Money card
// shipped before its backend existed. Both fixed; named here so the next one gets caught sooner.
//
// The prototype hardcodes `installed: true` on six entries. That is its stand-in for live state:
// here `installed` is DERIVED from `/api/connectors` + `/api/mcp-clients`, which is why a catalog
// entry is `CatalogEntry` (everything but `installed`) and the browser resolves the rest.
// An installed entry STAYS LISTED — it keeps its card and swaps `+` for a settings gear
// (spec §6) — so nothing in this module may filter on installed.
//
// THIS MODULE IS A LEAF — it imports `model` and (for the form's own auth vocabulary, as a TYPE
// only) `mcpConfig`, both of which import nothing but `model`. `adapter.ts` reads
// `catalogProvenanceFor` from here so a connector's DETAIL page can show the same provenance its
// directory card does; the moment this file imports the adapter back, that becomes a cycle.
// `resolveDirectory`, which does need the adapter, lives in `directoryRows.ts`.

import { connectorLogo } from './logos'
import { FAMILY_SECTIONS } from './model'
import type {
  ConnectorCategory,
  ConnectorFamily,
  DirectoryEntry,
  FamilyKey,
  FamilyTab,
  VerificationState,
} from './model'
import type { McpAuth } from './mcpConfig'

// Provenance now lives on the shared view model, so the catalog, the directory card, the
// preview and the detail page all read ONE definition — see `VerificationState` in `model.ts`
// for why it is three states and not a boolean. Re-exported: several modules import it here.
export type { VerificationState } from './model'

/**
 * A catalog row as authored. `developer` is NOT authored here — it is projected from
 * `publisher` in `toRow`, so the provenance fields stay the only source of truth and cannot
 * drift from what the badge renders. `logo` is not authored either: it is looked up from the
 * entry's `id` (`logos.ts`), so a mark can never be attached to the wrong entry by hand.
 */
export interface CatalogEntry extends Omit<
  DirectoryEntry,
  'installed' | 'developer' | 'logo' | 'sub' | 'desc'
> {
  /**
   * The card subtitle + preview paragraph — OPTIONAL here, and that is load-bearing, not an
   * oversight. An MCP entry has no backend counterpart, so it authors both. A NATIVE connector
   * entry (one of `_SHORT_DESC`/`_LONG_DESC`'s keys in `backend/services/connectors.py`) must
   * NOT set either: that text is read live off the connector's own `ConnectorView` instead, by
   * `directoryRows.resolveDirectory`. Authoring it here too is exactly the 2026-08-14 bug this
   * split exists to prevent — the owner hit it three times in one session editing Email's copy
   * in this file without realizing the installed detail page reads a second, independent
   * string. `catalog.test.tsx` pins that every non-MCP entry leaves both undefined.
   */
  sub?: string
  desc?: string
  /** The vendor. `null` for a server the user points us at themselves. */
  publisher: string | null
  verification: VerificationState
  /**
   * Source repository, when there is one. Genuinely optional: a large class of real vendor MCP
   * servers are hosted OAuth/token endpoints with no public repo at all, and the preview must
   * render cleanly without it rather than showing an empty link row.
   */
  repoUrl?: string
  /** Product/docs page, when there is one. Same optionality as `repoUrl`. */
  homepageUrl?: string
  /**
   * Where a user goes for help with THIS server — the vendor's support portal for a hosted
   * service, the repository's issue tracker for one you run yourself.
   *
   * Absent unless the publisher really operates one. A homepage relabelled "Support" is the
   * dead-link failure in a politer costume: it looks like a place to get help and isn't. The
   * footer omits every absent link rather than rendering a placeholder.
   */
  supportUrl?: string
  /**
   * The privacy policy that actually GOVERNS this server, i.e. the one covering the service
   * your data reaches. A local stdio server that talks to no vendor service has none, and gets
   * none here — an unrelated corporate policy would imply a data relationship that does not
   * exist.
   */
  privacyUrl?: string
  /**
   * The generic add-flow launcher. It has nothing to preview — no endpoint, no tools, no
   * publisher — so a click anywhere on it goes straight to the add form (spec §6).
   */
  generic?: boolean
}

/** The provenance half of a catalog entry, on its own — what an installed server can inherit. */
export interface CatalogProvenance {
  publisher: string | null
  verification: VerificationState
  repoUrl?: string
  homepageUrl?: string
  /** The catalog entry's brand mark. Inherited the same way and for the same reason as the
   *  verification badge: an installed Playwright MCP must not read one identity on its
   *  directory card and another on its detail page one click later (D14). */
  logo?: string
  /** What the entry is FOR. Same inheritance, same reason — an installed server should not
   *  lose the topical tags its own catalog card carries. */
  categories: ConnectorCategory[]
}

/** A resolved directory row: the wire-facing `DirectoryEntry` plus the provenance the preview
 *  renders. `DirectoryCard`/`DirectoryBrowser` only need the former; `DirectoryPreview` needs
 *  both. */
export interface DirectoryRow extends DirectoryEntry {
  publisher: string | null
  verification: VerificationState
  repoUrl?: string
  homepageUrl?: string
  supportUrl?: string
  privacyUrl?: string
  generic?: boolean
  /** The live connector behind an installed row when its id differs from the row's — an MCP
   *  server matched to this catalog entry by endpoint (`mcp:api-ibkr-com` for `mcp-ibkr`). */
  installedId?: string
}

/**
 * The directory's family chips. Deliberately the SAME union the tab's family tabs use, so a
 * section's "+ Add a data source" can hand its own tab straight to the directory with no
 * translation — and so the two can never drift into disagreeing about what a family is.
 */
export type DirectoryFamilyFilter = FamilyTab

export type DirectorySort = 'popular' | 'az'

/**
 * Every entry is backed by a real connector.
 *
 * Deliberately ABSENT, and why:
 *  - Webhook — its backend works (`webhook`), but it is intentionally not an addable card (§8).
 *  - Alpaca (data source) — retired 2026-10-29 for Alpaca's own MCP server (`mcp-alpaca`), which
 *    trades too; same treatment as IBKR Flex below.
 *  - IBKR Flex — retired 2026-10-29 for IBKR's own hosted MCP server (`mcp-ibkr`), which reads
 *    live where Flex is a day-late report. Backend kept (`disabled=True`); its entry lives in
 *    `RETIRED_CATALOG` below, unshown. ADR 2026-10-29-retire-ibkr-flex-for-ibkrs-own-mcp-server.
 *  - Yodlee — its backend works fully (`yodlee`), same as any other data source here. Hidden
 *    2026-08-14 (ADR 2026-08-14-hide-yodlee-enterprise-only-pricing): Yodlee requires an
 *    enterprise sales contract with no public self-serve tier — real reported minimums are
 *    $1,000-2,000+/month — which no self-hosted individual user of this app can realistically
 *    turn on, so it is no longer offered as something to add. An already-configured instance
 *    keeps its row on the main Connectors list (`ConnectorList`, driven by `/api/connectors`
 *    directly, never this catalog) — only fresh discovery through Browse & add is removed.
 *  - IRAS Tax Tables — the prototype's INSTALLED-example fixture, not an addable entry. No such
 *    server exists to point at, so it is not invented here; a real installed MCP server reaches
 *    the directory through `/api/mcp-clients`, not through this list.
 *  - Email is ONE entry, not two — one connector spans IMAP import and SMTP send (§8).
 *
 * `id` matches the live connector id for anything installable through the connector plane, so
 * `installed` resolves by a plain lookup. Catalog MCP entries carry a catalog key instead —
 * an MCP server's real id is whatever the user names it at add time.
 *
 * COPY RULES, both learned from real defects in this list — and both apply ONLY to the entries
 * that still author `sub`/`desc` here, i.e. the MCP ones (see `CatalogEntry.sub`'s doc comment
 * for why a native connector entry authors neither anymore):
 *
 *  1. `sub` AND `desc` RENDER TOGETHER on the preview page — the sub as the line under the name,
 *     the desc as the paragraph below it. They must carry DIFFERENT facts. Several pairs here
 *     were paraphrases of each other ("Crawl + scrape sites into clean markdown" over "crawls and
 *     scrapes whole sites into clean markdown").
 *  2. NEVER STATE A COVERAGE OR SCHEDULE CLAIM THIS FILE CANNOT CHECK. This list had Plaid as
 *     "worldwide" (it is US/Canada — see `services/account_linking.py`), Yodlee as "US-focused"
 *     (it is the global one), IBKR as a "nightly import" (the floor is 6h), Alpaca importing
 *     "trade history" (`services/aggregation/alpaca.py` reads `/v2/positions` and explicitly
 *     never touches `/v2/orders`), and Email importing transactions "automatically" (there is no
 *     ingestion pipeline — Automations decide what happens to a message). A native connector's
 *     copy is pinned the same way, just on the backend side — see
 *     `tests/test_connectors_service.py`.
 */
/** Entries withdrawn from the directory but kept, so a revert is one move back into
 *  `CATALOG`. Never rendered or searched. */
export const RETIRED_CATALOG: CatalogEntry[] = [
  {
    id: 'alpaca',
    name: 'Alpaca',
    families: ['Data source'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--crypto)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["alpaca"]`.
    publisher: 'Alpaca Securities',
    verification: 'unverified',
    homepageUrl: 'https://docs.alpaca.markets/us/docs/trading-api',
    supportUrl: 'https://alpaca.markets/support',
    // A PDF, and deliberately: alpaca.markets/privacy and /privacy-policy both answer 200
    // with a rendered "page not found", which a status check alone would have shipped. This
    // is the target Alpaca's own footer links to.
    privacyUrl: 'https://files.alpaca.markets/disclosures/PrivacyPolicy.pdf',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'ibkr',
    name: 'IBKR Flex',
    families: ['Data source'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--ibkr)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["ibkr"]`.
    publisher: 'Interactive Brokers',
    verification: 'unverified',
    homepageUrl: 'https://www.interactivebrokers.com/docs/web-api/flex-web-service/introduction',
    supportUrl: 'https://www.interactivebrokers.com/en/support/customer-service.php',
    privacyUrl: 'https://www.interactivebrokers.com/en/accounts/legalDocuments/privacy.php',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
]

export const CATALOG: CatalogEntry[] = [
  // ── Channels ───────────────────────────────────────────────────────────────
  {
    id: 'telegram',
    name: 'Telegram',
    families: ['Channel'],
    categories: ['Messaging'],
    icon: 'chat',
    hue: 'var(--accent)',
    // sub/desc: read live from services/connectors.py's `_SHORT_DESC`/`_LONG_DESC["telegram"]` —
    // see `CatalogEntry.sub`'s doc comment.
    publisher: 'Open Net Worth',
    verification: 'unverified',
    // OURS — we publish this connector, so the attribution line links to us, not to Telegram.
    homepageUrl: 'https://opennetworth.ai',
    // Telegram's policy all the same: `privacyUrl` is the one governing the SERVICE the data
    // reaches, and every message this connector sends crosses Telegram's servers.
    privacyUrl: 'https://telegram.org/privacy',
    // No support link: support for this connector is us, and sending someone with a connector
    // problem to telegram.org/support would be the wrong desk.
    mcp: false,
    popular: false,
    tools: [],
  },

  // ── Data sources ───────────────────────────────────────────────────────────
  {
    id: 'email',
    name: 'Email',
    families: ['Data source'],
    categories: ['Email'],
    icon: 'mail',
    hue: 'var(--recv)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["email"]` — see `CatalogEntry.sub`'s
    // doc comment. This is the exact connector the owner hit the drift bug on three times.
    publisher: 'Open Net Worth',
    verification: 'unverified',
    // OURS. No privacy link, and correctly so: this connector talks to the user's OWN mail
    // server. There is no vendor service in the middle whose policy would govern it.
    homepageUrl: 'https://opennetworth.ai',
    mcp: false,
    popular: false,
    tools: ['email_search_messages', 'email_get_messages', 'email_send_message'],
  },
  {
    id: 'simplefin',
    name: 'SimpleFIN',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--good)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["simplefin"]`.
    publisher: 'SimpleFIN',
    verification: 'unverified',
    homepageUrl: 'https://www.simplefin.org/',
    privacyUrl: 'https://beta-bridge.simplefin.org/info/privacy',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'plaid',
    name: 'Plaid',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--crypto)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["plaid"]`.
    publisher: 'Plaid Inc.',
    verification: 'unverified',
    homepageUrl: 'https://plaid.com/docs/link/',
    supportUrl: 'https://plaid.com/docs/support/',
    privacyUrl: 'https://plaid.com/legal/#end-user-privacy-policy',
    mcp: false,
    popular: true,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'lunch_money',
    name: 'Lunch Money',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--cash-c)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["lunch_money"]`.
    publisher: 'Lunch Money',
    verification: 'unverified',
    homepageUrl: 'https://lunchmoney.app',
    supportUrl: 'https://support.lunchmoney.app/',
    privacyUrl: 'https://lunchmoney.app/privacy',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'lunch_flow',
    name: 'Lunch Flow',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--good)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["lunch_flow"]`.
    publisher: 'Lunch Flow',
    verification: 'unverified',
    homepageUrl: 'https://lunchflow.app',
    supportUrl: 'https://discord.gg/TJn5mMV4jZ',
    privacyUrl: 'https://www.lunchflow.app/docs/guides/account/security-privacy',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'ynab',
    name: 'YNAB',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["ynab"]`.
    publisher: 'YNAB',
    verification: 'unverified',
    homepageUrl: 'https://www.ynab.com',
    supportUrl: 'https://support.ynab.com',
    privacyUrl: 'https://www.ynab.com/privacy-policy',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'enable_banking',
    name: 'Enable Banking',
    families: ['Data source'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--cash-c)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["enable_banking"]`.
    publisher: 'Enable Banking',
    verification: 'unverified',
    homepageUrl: 'https://enablebanking.com/docs/api/reference/',
    // No support link: enablebanking.com/contact-us is a meeting-booking form, not a desk.
    privacyUrl: 'https://enablebanking.com/privacy/',
    mcp: false,
    popular: false,
    tools: ['get_connector_status', 'connector_sync'],
  },
  {
    id: 'yahoo_finance',
    name: 'Yahoo Finance',
    families: ['Data source'],
    categories: ['Market data'],
    icon: 'investments',
    hue: 'var(--cash-c)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["yahoo_finance"]`.
    publisher: 'Yahoo Inc.',
    verification: 'unverified',
    homepageUrl: 'https://finance.yahoo.com',
    privacyUrl: 'https://legal.yahoo.com/us/en/yahoo/privacy/index.html',
    mcp: false,
    popular: false,
    // No Lucius tools — a price-fetch backend, not a callable tool surface (this is a
    // migration of the existing "Data" settings panel, not a new capability).
    tools: [],
  },
  {
    id: 'coingecko',
    name: 'CoinGecko',
    families: ['Data source'],
    categories: ['Market data'],
    icon: 'investments',
    hue: 'var(--crypto)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["coingecko"]`.
    publisher: 'CoinGecko',
    verification: 'unverified',
    homepageUrl: 'https://www.coingecko.com',
    supportUrl: 'https://support.coingecko.com/hc/en-us',
    privacyUrl: 'https://www.coingecko.com/en/privacy',
    mcp: false,
    popular: false,
    tools: [],
  },

  // ── Web (native providers — we build and operate these integrations) ────────
  {
    id: 'web_search:duckduckgo',
    name: 'DuckDuckGo',
    families: ['Web'],
    categories: ['Web search'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:duckduckgo"]`.
    //
    // NO authored `tag: 'Default'`. It is a STATIC claim about a setting the user can change, so
    // it went on reading "Default" after they made Tavily the default. The live chip on the list
    // row and the detail page is derived from `is_active` (`adapter.toEntry`) and stays true; the
    // out-of-the-box fact is stated in the backend's long description instead, qualified.
    publisher: 'DuckDuckGo',
    verification: 'unverified',
    homepageUrl: 'https://duckduckgo.com/',
    supportUrl: 'https://duckduckgo.com/duckduckgo-help-pages/',
    privacyUrl: 'https://duckduckgo.com/privacy',
    mcp: false,
    popular: false,
    tools: ['duckduckgo_search', 'duckduckgo_fetch_page'],
  },
  {
    id: 'web_search:tavily',
    name: 'Tavily',
    families: ['Web'],
    categories: ['Web search'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:tavily"]`.
    publisher: 'Tavily AI',
    verification: 'verified',
    homepageUrl: 'https://docs.tavily.com/documentation/api-reference/endpoint/search',
    supportUrl: 'https://help.tavily.com/',
    privacyUrl: 'https://www.tavily.com/privacy',
    mcp: false,
    popular: true,
    tools: ['tavily_search', 'tavily_fetch_page'],
  },
  {
    id: 'web_search:perplexity',
    name: 'Perplexity (Sonar)',
    families: ['Web'],
    categories: ['Web search'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:perplexity"]`.
    publisher: 'Perplexity AI',
    verification: 'verified',
    homepageUrl: 'https://docs.perplexity.ai/docs/getting-started/overview',
    // Neither support nor privacy: every Perplexity-operated help-centre and /hub/legal/ page
    // answers 403 behind a bot challenge, and the docs site has no legal path at all. An
    // unverifiable link is left out rather than guessed at.
    mcp: false,
    popular: false,
    tools: ['perplexity_search', 'perplexity_fetch_page'],
  },
  {
    id: 'web_search:serper',
    name: 'Serper',
    families: ['Web'],
    categories: ['Web search'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:serper"]`.
    publisher: 'Serper',
    verification: 'unverified',
    homepageUrl: 'https://serper.dev/',
    // No support link: Serper runs no portal — support is Discord/email only.
    privacyUrl: 'https://serper.dev/privacy',
    mcp: false,
    popular: false,
    tools: ['serper_search', 'serper_fetch_page'],
  },
  {
    id: 'web_search:google_pse',
    name: 'Google Programmable Search',
    families: ['Web'],
    categories: ['Web search'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:google_pse"]`.
    publisher: 'Google',
    verification: 'unverified',
    homepageUrl: 'https://developers.google.com/custom-search/v1/overview',
    supportUrl: 'https://support.google.com/programmable-search/',
    privacyUrl: 'https://policies.google.com/privacy',
    mcp: false,
    popular: false,
    tools: ['google_pse_search', 'google_pse_fetch_page'],
  },
  {
    id: 'web_search:firecrawl',
    name: 'Firecrawl',
    families: ['Web'],
    categories: ['Web search', 'Browsing'],
    icon: 'search',
    hue: 'var(--warn)',
    // sub/desc: read live from `_SHORT_DESC`/`_LONG_DESC["web_search:firecrawl"]`.
    publisher: 'Firecrawl',
    verification: 'verified',
    homepageUrl: 'https://docs.firecrawl.dev/',
    supportUrl: 'https://firecrawl.dev/contact',
    privacyUrl: 'https://firecrawl.dev/privacy-policy',
    mcp: false,
    popular: false,
    tools: ['firecrawl_search', 'firecrawl_fetch_page'],
  },

  // ── MCP (third-party — someone else's code, over a transport we don't own) ────
  //
  // Every `desc` here is written from the server's OWN published tool list, so "Lucius can …"
  // names capabilities that actually exist rather than what the vendor's marketing implies.
  // Where a server WRITES to a live financial account, the description says so plainly — as a
  // fact about what it does, not as a warning. There is deliberately no capability tier and no
  // caution copy on these cards: the permission ladder already lands every discovered tool on
  // Needs approval, and THAT is the control. A second, softer control here would only invite
  // someone to trust the label instead of the ladder.
  {
    // No `url`, no `developer`, no tools: there is nothing to preview, so clicking this card
    // anywhere goes straight to the add form (spec §6). It also claims NO author — we write
    // the add flow, not the server you point it at (spec §5).
    id: 'mcp-custom',
    name: 'Custom MCP server',
    families: ['MCP'],
    categories: [],
    icon: 'cpu',
    hue: 'var(--apt)',
    sub: 'Point Lucius at any MCP endpoint — URL or local command',
    desc: 'Connect any MCP-compatible server — a hosted URL or a command that runs on this machine — and Lucius gains whatever tools that server exposes. Lucius connects once before anything is saved, lists the tools it found, and shows you each one so you can set its permission level before it can ever be called. Useful when a service you already use publishes its own MCP server, when you run a server of your own, or when you need a capability nothing else in this directory covers — you supply the endpoint and credentials, and nothing is added until the connection actually answers.',
    // No publisher, and specifically NOT "Open Net Worth": we author the add flow, not the
    // server you point it at (spec §5 calls that claim out as a contradiction).
    publisher: null,
    verification: 'unknown',
    generic: true,
    mcp: true,
    popular: true,
    tools: [],
  },
  {
    id: 'mcp-playwright',
    name: 'Playwright MCP',
    families: ['MCP'],
    categories: ['Browsing'],
    icon: 'cpu',
    hue: 'var(--apt)',
    sub: 'Real browser rendering for Lucius in chat',
    desc: 'Connect Playwright MCP to give Lucius a real, controllable browser instead of a raw HTML fetch. Lucius can navigate to a page and wait for it to render, read a structured snapshot of what is actually on screen, click links and buttons, type into fields, fill and submit whole forms, and take screenshots it can show you in chat. Useful for anything a plain fetch returns empty — a bank or broker portal that renders in JavaScript, a multi-step flow that has to be walked rather than requested, or capturing what a statement page looked like so you can see exactly what Lucius saw.',
    publisher: 'Microsoft',
    verification: 'verified',
    repoUrl: 'https://github.com/microsoft/playwright-mcp',
    supportUrl: 'https://github.com/microsoft/playwright-mcp/issues',
    mcp: true,
    popular: false,
    tools: [
      'browser_navigate',
      'browser_snapshot',
      'browser_click',
      'browser_type',
      'browser_fill_form',
      'browser_take_screenshot',
    ],
  },
  {
    id: 'mcp-firecrawl',
    name: 'Firecrawl',
    families: ['MCP'],
    categories: ['Browsing', 'Web search'],
    icon: 'search',
    hue: 'var(--apt)',
    sub: 'Crawl + scrape sites into clean markdown',
    desc: 'Connect Firecrawl to let Lucius read whole sites rather than one page at a time. Lucius can scrape a single URL into clean markdown, map a site to discover every reachable URL, crawl a section and bring back all of it, run a web search that returns full page content instead of snippets, and extract structured fields from a set of pages against a shape you describe. Useful when the answer is spread across a documentation set, an investor-relations section, or a fee schedule split over a dozen pages — and for pulling a rate table or a set of figures back as data instead of prose.',
    publisher: 'Firecrawl',
    verification: 'verified',
    repoUrl: 'https://github.com/firecrawl/firecrawl-mcp-server',
    homepageUrl: 'https://docs.firecrawl.dev/mcp-server',
    supportUrl: 'https://github.com/firecrawl/firecrawl-mcp-server/issues',
    privacyUrl: 'https://www.firecrawl.dev/privacy-policy',
    mcp: true,
    popular: false,
    tools: [
      'firecrawl_search',
      'firecrawl_scrape',
      'firecrawl_crawl',
      'firecrawl_map',
      'firecrawl_extract',
    ],
  },

  // MCP · money and books — servers that reach a live financial account.
  {
    id: 'mcp-stripe',
    name: 'Stripe',
    families: ['MCP'],
    categories: ['Payments'],
    icon: 'accounts',
    hue: 'var(--apt)',
    sub: 'Payments, invoices, and balances from your Stripe account',
    desc: 'Connect Stripe to let Lucius work with your Stripe account directly. Lucius can read your balance, charges, payment intents, checkout sessions, invoices, subscriptions, customers, products, prices, payouts, disputes and balance transactions, issue a refund, create an invoice, and search Stripe’s API reference and documentation while you build against it. Its general write tool also creates and updates Stripe objects, so Lucius can raise an invoice or change a subscription, not only read one. Useful for reconciling what Stripe paid out against what landed in your bank, finding which invoices are still unpaid, tracing a customer’s billing history, or checking a balance without opening the dashboard.',
    publisher: 'Stripe',
    verification: 'verified',
    repoUrl: 'https://github.com/stripe/ai',
    homepageUrl: 'https://docs.stripe.com/mcp',
    supportUrl: 'https://support.stripe.com/',
    privacyUrl: 'https://stripe.com/privacy',
    mcp: true,
    popular: true,
    tools: [
      'stripe_api_read',
      'stripe_api_write',
      'stripe_api_search',
      'get_stripe_account_info',
      'create_refund',
      'stripe_report',
      'search_stripe_documentation',
    ],
  },
  {
    // NO install preset, deliberately — see `MCP_INSTALL_PRESETS`. Intuit publishes no npm
    // package (`@qboapi/qbo-mcp-server` is unpublished) and no image; the README's only documented
    // launch is `node <your clone>/dist/index.js`, an absolute path that is different on every
    // machine. There is nothing truthful to pre-fill, so step 1 opens empty and the repo link
    // carries the build instructions.
    id: 'mcp-quickbooks',
    name: 'QuickBooks Online',
    families: ['MCP'],
    categories: ['Accounting'],
    icon: 'accounts',
    hue: 'var(--apt)',
    sub: 'Read and write a QuickBooks Online company',
    desc: 'Connect QuickBooks Online to let Lucius work with your company’s books. Lucius can pull the Balance Sheet, Profit & Loss, Cash Flow, Trial Balance, General Ledger and aged receivables and payables, and search or read customers, vendors, invoices, bills, payments, purchases, deposits, transfers and journal entries across twenty-nine entity types. Create, update and delete tools are registered by default, so Lucius can also raise an invoice, record a payment, add a customer or void a document — each of those three categories can be switched off with an environment variable when you set the server up. Useful for pulling a report without opening QuickBooks, tracing a figure back to the document behind it, or reconciling a QuickBooks balance against an account tracked here.',
    publisher: 'Intuit',
    verification: 'verified',
    repoUrl: 'https://github.com/intuit/quickbooks-online-mcp-server',
    supportUrl: 'https://github.com/intuit/quickbooks-online-mcp-server/issues',
    privacyUrl: 'https://www.intuit.com/privacy/statement/',
    mcp: true,
    popular: false,
    tools: [
      'get_balance_sheet',
      'get_profit_and_loss',
      'get_general_ledger',
      'get_aged_receivables',
      'search_invoices',
      'create_invoice',
      'update_invoice',
      'create_payment',
    ],
  },
  {
    id: 'mcp-xero',
    name: 'Xero',
    families: ['MCP'],
    categories: ['Accounting'],
    icon: 'accounts',
    hue: 'var(--apt)',
    sub: 'Read and write a Xero organisation',
    desc: 'Connect Xero to let Lucius work with your organisation’s accounting data. Lucius can list invoices, bank transactions, contacts, accounts, items, payments, credit notes, quotes, manual journals, tracking categories and tax rates, and pull the Balance Sheet, Profit & Loss, Trial Balance and aged receivables and payables reports. It can also create and update invoices, payments, contacts, bank transactions, credit notes, quotes, items and manual journals, and read and edit payroll timesheets — write access is on by default, bounded only by the OAuth scopes on the connection you create. Useful for checking what is owed and what is overdue, reconciling a Xero bank feed against an account tracked here, or drafting an invoice straight out of a conversation.',
    publisher: 'Xero',
    verification: 'verified',
    repoUrl: 'https://github.com/XeroAPI/xero-mcp-server',
    homepageUrl: 'https://developer.xero.com/documentation/guides/oauth2/custom-connections/',
    supportUrl: 'https://github.com/XeroAPI/xero-mcp-server/issues',
    privacyUrl: 'https://www.xero.com/legal/privacy/',
    mcp: true,
    popular: false,
    tools: [
      'list-invoices',
      'list-bank-transactions',
      'list-report-balance-sheet',
      'list-profit-and-loss',
      'list-trial-balance',
      'create-invoice',
      'create-payment',
      'update-invoice',
    ],
  },
  {
    id: 'mcp-mercury',
    name: 'Mercury',
    families: ['MCP'],
    categories: ['Banking'],
    icon: 'accounts',
    hue: 'var(--apt)',
    sub: 'Read-only access to Mercury business banking',
    desc: 'Connect Mercury to let Lucius read your business banking data. Mercury’s hosted server exposes accounts, balances, transactions with the category, merchant and spending user attached to each, cards, recipients, treasury accounts and downloadable statements. It is read-only by design — it cannot move money, send a payment, or change anything in the account. Useful for reconciling Mercury balances against what is recorded here, categorising business spend, pulling a statement for a period you are closing, or answering “what did we pay this vendor last quarter” without opening the dashboard. Sign-in happens in the browser over OAuth.',
    publisher: 'Mercury',
    verification: 'verified',
    homepageUrl: 'https://docs.mercury.com/docs/what-is-mercury-mcp',
    // No `supportUrl`, and not for want of one: Mercury runs a help centre, but it refuses
    // every non-browser client, so it is the one link here that could not be checked the way
    // all the others were. Shipping an unchecked link is precisely what this file avoids.
    privacyUrl: 'https://mercury.com/legal/privacy',
    mcp: true,
    popular: false,
    tools: [],
  },
  {
    id: 'mcp-ibkr',
    name: 'Interactive Brokers',
    families: ['MCP'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--ibkr)',
    sub: 'Trades stocks, options & futures; live positions and P&L',
    desc: 'Connect Interactive Brokers’ own hosted server to let Lucius work with your IBKR account directly. It exposes account balances, positions, trades, orders and performance; quotes, price history, option chains and contract search; screeners, watchlists, price alerts and IBKR’s company research. It can also act: it previews, submits, modifies and cancels orders, and edits watchlists, alerts and saved screeners — every one of those tools asks for your approval before it runs, unless you change that. Useful for reconciling IBKR positions against what is recorded here, checking an allocation or a day’s move, researching a holding, or drafting an order you review before it goes. Sign-in happens on IBKR’s own login screen over OAuth, for one account at a time; you can revoke it in Client Portal under Settings › Manage Third-Party Consents.',
    publisher: 'Interactive Brokers',
    verification: 'verified',
    // IBKR's own pages publish this exact endpoint for "any MCP client": the AI-integrations
    // page, the connector v1.1.5 release notes (ibkrguides.com) and the 2026-07-28 press release.
    homepageUrl: 'https://www.interactivebrokers.com/en/trading/ai-integrations.php',
    supportUrl: 'https://www.interactivebrokers.com/en/support/customer-service.php',
    privacyUrl: 'https://www.interactivebrokers.com/en/accounts/legalDocuments/privacy.php',
    mcp: true,
    popular: true,
    tools: [
      'get_account_positions',
      'get_account_balances',
      'get_account_trades',
      'get_price_snapshot',
      'get_option_data',
      'preview_order',
      'submit_order',
    ],
  },
  {
    id: 'mcp-robinhood',
    name: 'Robinhood',
    families: ['MCP'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Trades US stocks in an agent account; reads every account',
    desc: 'Connect Robinhood’s own hosted Trading server to let Lucius see your Robinhood investing. It can read every Robinhood account you hold — account numbers, positions, balances and order history included — but it can only place trades inside a separate Agentic account that Robinhood opens when you connect, using only the money you move into it. Robinhood describes it as a beta that trades US equities for now. Robinhood sends a push notification for each trade, and every trading tool here asks for your approval before it runs unless you change that. Useful for reconciling Robinhood positions against what is recorded here, reviewing allocation and order history, or trying an idea with a small, ring-fenced balance. Sign-in happens on Robinhood’s own screen over OAuth.',
    publisher: 'Robinhood',
    verification: 'verified',
    // Robinhood's Help Center publishes this endpoint ("Onboarding an external agent"), and its
    // 2026-05-27 newsroom post announces the Trading MCP server.
    homepageUrl: 'https://robinhood.com/us/en/support/articles/agentic-trading-overview/',
    supportUrl: 'https://robinhood.com/us/en/support/',
    privacyUrl: 'https://robinhood.com/us/en/support/articles/privacy-policy/',
    mcp: true,
    popular: true,
    tools: [],
  },
  {
    id: 'mcp-moomoo',
    name: 'moomoo',
    families: ['MCP'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Trades and researches: quotes, options, live positions',
    desc: 'Connect moomoo’s own hosted server to let Lucius work with your moomoo account through moomoo OpenAPI. moomoo describes it as real-time quotes, derivatives screening, fundamentals and technicals, portfolio management and trade execution — so it can act on your account, and every trading tool here asks for your approval before it runs unless you change that. Unlike moomoo’s older API route it needs no OpenD gateway running on this machine. Useful for checking moomoo positions against what is recorded here, researching a holding, or screening options. Sign-in happens on moomoo’s own consent page over OAuth.',
    publisher: 'moomoo',
    verification: 'verified',
    // moomoo's OpenAPI docs (open.moomoo.com/mcp-docs) publish this endpoint and its OAuth flow.
    homepageUrl: 'https://open.moomoo.com/mcp-docs/overview',
    supportUrl: 'https://www.moomoo.com/us/support',
    privacyUrl: 'https://www.moomoo.com/privacy',
    mcp: true,
    popular: false,
    tools: [],
  },
  {
    id: 'mcp-alpaca',
    name: 'Alpaca MCP',
    families: ['MCP'],
    categories: ['Brokerage'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Trades stocks, options & crypto; paper trading by default',
    desc: 'Connect Alpaca’s own MCP server, run on this machine, to let Lucius trade at Alpaca — unlike the read-only Alpaca data source, which only imports positions. It reads your account, positions, orders and market data, and places, changes and cancels stock, option and crypto orders. It starts on paper trading: add ALPACA_PAPER_TRADE=false to the environment variables for real money. Every trading tool here asks for your approval before it runs unless you change that. Needs uv on this machine, and an Alpaca API key pair pasted into the environment variables.',
    publisher: 'Alpaca',
    verification: 'verified',
    // alpacahq/alpaca-mcp-server is Alpaca's own org; the PyPI package links back to it.
    repoUrl: 'https://github.com/alpacahq/alpaca-mcp-server',
    supportUrl: 'https://github.com/alpacahq/alpaca-mcp-server/issues',
    privacyUrl: 'https://files.alpaca.markets/disclosures/PrivacyPolicy.pdf',
    mcp: true,
    popular: false,
    tools: ['place_stock_order', 'place_option_order', 'place_crypto_order'],
  },
  {
    id: 'mcp-coinbase',
    name: 'Coinbase',
    families: ['MCP'],
    categories: ['Brokerage', 'Crypto'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Trades crypto on Coinbase; balances, orders, fills, fees',
    desc: 'Connect Coinbase’s own MCP server — part of the Coinbase CLI, run on this machine — to let Lucius trade on Coinbase Advanced Trade. It reads balances, portfolios, orders, fills, fees and market data; previews, places, edits and cancels orders; converts between assets; and moves funds between your own portfolios (never out to an external address). Every write tool here asks for your approval before it runs unless you change that. Coinbase’s hosted server only admits apps it has approved; this local one needs none. Needs Node.js 22 or later, and a Coinbase developer API key whose permissions you choose when you create it.',
    publisher: 'Coinbase',
    verification: 'verified',
    // @coinbase/coinbase-cli is published by Coinbase's own npm accounts. Help-centre and
    // privacy pages refuse non-browser clients, so they are the links left out here.
    homepageUrl: 'https://docs.cdp.coinbase.com/coinbase-cli/overview',
    mcp: true,
    popular: false,
    tools: ['coinbase_orders_create', 'coinbase_orders_preview', 'coinbase_balance'],
  },
  {
    id: 'mcp-okx',
    name: 'OKX',
    families: ['MCP'],
    categories: ['Brokerage', 'Crypto'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Trades spot, perpetuals & options on OKX; demo by default',
    desc: 'Connect OKX’s own Agent Trade Kit server, run on this machine, to let Lucius trade on OKX. It reads balances, positions, orders, fills and bills, and places, amends and cancels spot, perpetual-swap and option orders, including algo orders and grid bots. It starts in OKX’s demo (simulated) trading: remove --demo from the arguments for real money, or add --read-only to allow no orders at all. Every trading tool here asks for your approval before it runs unless you change that. Needs Node.js 18 or later, and an OKX API key, secret and passphrase.',
    publisher: 'OKX',
    verification: 'verified',
    // okx/agent-trade-kit is OKX's own org; the @okx_ai npm package is maintained from OKG.
    repoUrl: 'https://github.com/okx/agent-trade-kit',
    supportUrl: 'https://github.com/okx/agent-trade-kit/issues',
    privacyUrl: 'https://www.okx.com/help/privacy-policy-statement',
    mcp: true,
    popular: false,
    tools: ['spot_place_order', 'swap_place_order', 'account_get_balance'],
  },
  {
    id: 'mcp-kraken',
    name: 'Kraken',
    families: ['MCP'],
    categories: ['Brokerage', 'Crypto'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Crypto market data, balances, paper trading; live opt-in',
    desc: 'Connect Kraken’s own MCP server — part of the Kraken CLI, run on this machine — to let Lucius work with your Kraken account. As set up here it reads market data, your balances, orders, trades and ledgers, and paper-trades with no real money. Live spot orders are opt-in: add trade to the -s list in the arguments (futures for futures); keep funding out unless you want deposits and withdrawals offered. Every trading tool here asks for your approval before it runs unless you change that. Install the Kraken CLI first (macOS or Linux), and paste a Kraken API key and secret — create the key with only the permissions you want allowed.',
    publisher: 'Kraken',
    verification: 'verified',
    // krakenfx/kraken-cli is Kraken's own org; v0.5.1 moved the server to its own `kraken-mcp`
    // binary (`kraken mcp` in older docs now only prints a pointer to it).
    repoUrl: 'https://github.com/krakenfx/kraken-cli',
    homepageUrl: 'https://docs.kraken.com/home/mcp',
    supportUrl: 'https://github.com/krakenfx/kraken-cli/issues',
    privacyUrl: 'https://www.kraken.com/legal/privacy',
    mcp: true,
    popular: false,
    tools: ['kraken_balance', 'kraken_paper_buy', 'kraken_trades_history'],
  },
  {
    id: 'mcp-expensify',
    name: 'Expensify',
    families: ['MCP'],
    categories: ['Expenses'],
    icon: 'accounts',
    hue: 'var(--apt)',
    sub: 'Read-only search over expenses and reports',
    desc: 'Connect Expensify to let Lucius read your expense data. The server exposes a single Search tool, scoped to exactly what your own Expensify account can already see, across expenses, reports and receipts. Lucius can find spend by merchant, category, date range or person, summarise a period, flag expenses that are still missing a receipt, and compare spending across months. It is read-only — it cannot approve a report, edit an expense, reimburse anyone, or change workspace settings. Useful for folding reimbursable spend into a monthly review, checking what is still outstanding before a close, or finding the receipt behind a card charge. Sign-in happens in the browser over OAuth.',
    publisher: 'Expensify',
    verification: 'verified',
    homepageUrl: 'https://use.expensify.com/blog/expensify-mcp',
    supportUrl: 'https://help.expensify.com/',
    privacyUrl: 'https://www.expensify.com/privacy',
    mcp: true,
    popular: false,
    tools: ['search'],
  },
  {
    id: 'mcp-avalara',
    name: 'Avalara AvaTax',
    families: ['MCP'],
    categories: ['Tax'],
    icon: 'cpu',
    hue: 'var(--apt)',
    sub: 'Sales tax calculation and nexus lookups',
    desc: 'Connect Avalara AvaTax to let Lucius answer sales tax questions against your Avalara account. Lucius can calculate the tax on a transaction from its addresses and line items, validate and resolve an address to the jurisdiction it really falls in, read existing transactions for a company, list the companies on the account, look up nexus definitions and the nexus you are actually registered for, browse tax code definitions, and get a ranked tax-code recommendation for a product. It can also register a new nexus, which is a write to your Avalara account. Useful for checking what tax applies to a sale before you invoice it, confirming an address resolves where you expect, or reviewing where you are obliged to collect. Sign-in happens in the browser over OAuth.',
    publisher: 'Avalara',
    verification: 'verified',
    homepageUrl: 'https://developer.avalara.com/mcp-servers/avatax/',
    supportUrl: 'https://knowledge.avalara.com/',
    privacyUrl: 'https://www.avalara.com/us/en/legal/privacy-policy.html',
    mcp: true,
    popular: false,
    tools: [
      'calculate_tax',
      'validate_address',
      'get_transactions',
      'get_nexus',
      'get_tax_code_definitions',
      'get_tax_code_recommendations',
      'create_nexus',
    ],
  },

  // MCP · market and reference data.
  {
    id: 'mcp-twelvedata',
    name: 'Twelve Data',
    families: ['MCP'],
    categories: ['Market data'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Market prices, indicators, and fundamentals',
    desc: 'Connect Twelve Data to give Lucius live and historical market data across stocks, ETFs, mutual funds, forex, crypto and commodities. Lucius can search for a symbol, fetch a current price or a full quote, pull a historical time series, compute technical indicators such as RSI, MACD or moving averages, read a company profile, its key statistics, market cap, financial statements and news, convert between currencies, check whether a market is open, list the day’s movers, and read analyst ratings and price targets. Useful for pricing a holding this app has no feed for, checking a fund’s fundamentals before you add it, or asking what moved a position today and why.',
    publisher: 'Twelve Data',
    verification: 'verified',
    repoUrl: 'https://github.com/twelvedata/mcp',
    homepageUrl: 'https://twelvedata.com',
    supportUrl: 'https://support.twelvedata.com/',
    privacyUrl: 'https://twelvedata.com/privacy',
    mcp: true,
    popular: false,
    tools: [
      'search_symbol',
      'get_price',
      'get_quote',
      'get_time_series',
      'get_technical_indicator',
      'get_financials',
      'get_statistics',
      'get_company_news',
      'get_analyst_data',
      'currency_conversion',
    ],
  },
  {
    id: 'mcp-sec-edgar',
    name: 'SEC EDGAR',
    families: ['MCP'],
    categories: ['Research', 'Filings'],
    icon: 'search',
    hue: 'var(--apt)',
    sub: 'Company filings and XBRL financials from SEC EDGAR',
    desc: 'Connect this server to give Lucius direct access to SEC EDGAR. Lucius can look up a company’s CIK, read its profile and company facts, list its recent filings, pull the full text or a named section of a 10-K, 10-Q or 8-K, read balance sheet, income statement and cash flow figures parsed from a filing’s XBRL rather than scraped from its prose, read individual XBRL concepts, and read Form 3, 4 and 5 insider transactions — every answer carrying the filing URL it came from. Useful for checking a holding’s actual reported numbers before you act on a headline, reading what a company disclosed in an 8-K, or tracking insider buying and selling. Built and maintained independently by Stefano Amorelli: it reads the SEC’s public EDGAR system and is not an SEC product.',
    publisher: 'Stefano Amorelli',
    verification: 'unverified',
    repoUrl: 'https://github.com/stefanoamorelli/sec-edgar-mcp',
    homepageUrl: 'https://sec-edgar-mcp.amorelli.tech/',
    supportUrl: 'https://github.com/stefanoamorelli/sec-edgar-mcp/issues',
    mcp: true,
    popular: false,
    tools: [
      'lookup_company_cik',
      'get_company_info',
      'get_company_facts',
      'get_recent_filings',
      'get_filing_content',
      'get_filing_sections',
      'get_financial_statements',
      'get_xbrl_concepts',
      'get_insider_transactions',
      'analyze_8k_filing',
      'analyze_form_345',
    ],
  },
  {
    id: 'mcp-fred',
    name: 'FRED',
    families: ['MCP'],
    categories: ['Research', 'Economic data'],
    icon: 'investments',
    hue: 'var(--apt)',
    sub: 'Federal Reserve economic time series',
    desc: 'Connect this server to give Lucius the Federal Reserve Bank of St. Louis’s FRED database — over 800,000 economic time series. Lucius can search FRED for series matching a term, browse its category tree to find what exists on a subject, and fetch a series’ observations over a date range with the usual unit and frequency transformations. Useful for putting your own numbers in context: comparing a savings rate or a portfolio return against CPI inflation, checking where policy rates or mortgage rates sat when you took a loan, or pulling the unemployment or GDP series behind a piece of news. Needs a free FRED API key. Built and maintained independently by Stefano Amorelli: it reads FRED’s public API and is not a Federal Reserve product.',
    publisher: 'Stefano Amorelli',
    verification: 'unverified',
    repoUrl: 'https://github.com/stefanoamorelli/fred-mcp-server',
    homepageUrl: 'https://fred-mcp-server.amorelli.tech',
    supportUrl: 'https://github.com/stefanoamorelli/fred-mcp-server/issues',
    mcp: true,
    popular: false,
    tools: ['fred_search', 'fred_browse', 'fred_get_series'],
  },
]

/**
 * How the add flow pre-fills step 1 for a catalog MCP entry.
 *
 * EVERY field here is transcribed from the server's OWN published config block — its README, its
 * docs site, or the vendor's connect page — never inferred from a package name. These are kept
 * SEPARATE from `repoUrl`, which is only the entry's public home: presetting a repo URL as the
 * Server URL is what the prototype's synthetic `https://mcp.playwright.dev` amounted to, and it
 * guarantees a failed probe with an error that explains nothing. An entry we cannot source a real
 * config for gets NO preset at all (see `mcp-quickbooks`) — an empty step 1 is honest, a wrong
 * one is not.
 */
export interface McpInstallPreset {
  transport: 'remote' | 'local'
  url?: string
  command?: string
  args?: string
  /** `KEY=value` per line, pre-seeded with the keys the server requires but no values. */
  env?: string
  /**
   * Which credential the server actually takes, when the default guess would be wrong.
   *
   * The flow's own default — no auth for a hosted URL, an API key for a local command — is right
   * for a server that answers anonymously and wrong for every hosted vendor endpoint below, which
   * would probe unauthenticated and come back with a bare 401. `oauth` lands the form on the
   * real Sign-in flow (`McpConnectionFields.tsx`'s `McpOAuthField`, RFC `2026-08-14-mcp-oauth`)
   * with the URL already pre-filled, so a preset entry never has to ask the user to also go find
   * and paste the endpoint themselves.
   */
  auth?: McpAuth
}

export const MCP_INSTALL_PRESETS: Record<string, McpInstallPreset> = {
  'mcp-playwright': { transport: 'local', command: 'npx', args: '-y @playwright/mcp@latest' },
  'mcp-firecrawl': {
    transport: 'local',
    command: 'npx',
    args: '-y firecrawl-mcp',
    env: 'FIRECRAWL_API_KEY=',
  },
  // Stripe documents OAuth first but explicitly supports a restricted API key as an
  // `Authorization: Bearer` — the one hosted entry here that can be reached today.
  'mcp-stripe': { transport: 'remote', url: 'https://mcp.stripe.com', auth: 'key' },
  'mcp-xero': {
    transport: 'local',
    command: 'npx',
    args: '-y @xeroapi/xero-mcp-server@latest',
    env: 'XERO_CLIENT_ID=\nXERO_CLIENT_SECRET=',
  },
  'mcp-mercury': { transport: 'remote', url: 'https://mcp.mercury.com/mcp', auth: 'oauth' },
  // The address IBKR publishes for "any MCP client" (connector v1.1.5 release notes) — the same
  // OAuth server behind its Claude/ChatGPT/Grok directory listings.
  'mcp-ibkr': { transport: 'remote', url: 'https://api.ibkr.com/v1/api/mcp-public', auth: 'oauth' },
  'mcp-robinhood': {
    transport: 'remote',
    url: 'https://agent.robinhood.com/mcp/trading',
    auth: 'oauth',
  },
  'mcp-moomoo': { transport: 'remote', url: 'https://mcp.moomoo.com/mcp', auth: 'oauth' },
  // The three below run on this machine. Each env block is the vendor's own variable NAMES
  // (verified in its code, 2026-10-29) — never a value, a preset must not ship one. The
  // no-real-money default lives elsewhere: Alpaca's own default is paper, OKX's is `--demo`.
  'mcp-alpaca': {
    transport: 'local',
    command: 'uvx',
    args: 'alpaca-mcp-server',
    env: 'ALPACA_API_KEY=\nALPACA_SECRET_KEY=',
  },
  'mcp-coinbase': {
    transport: 'local',
    command: 'npx',
    args: '-y @coinbase/coinbase-cli mcp',
    env: 'COINBASE_KEY_ID=\nCOINBASE_KEY_SECRET=',
  },
  // Needs the Kraken CLI installed (its own installer or release binary — no npm/PyPI
  // package). No live-order or funding services by default: market, read-only account, paper.
  'mcp-kraken': {
    transport: 'local',
    command: 'kraken-mcp',
    args: '-s market,account,paper',
    env: 'KRAKEN_API_KEY=\nKRAKEN_API_SECRET=',
  },
  'mcp-okx': {
    transport: 'local',
    command: 'npx',
    args: '-y @okx_ai/okx-trade-mcp --demo',
    env: 'OKX_API_KEY=\nOKX_SECRET_KEY=\nOKX_PASSPHRASE=',
  },
  // Trailing slash is deliberate: the bare `/mcp` 308-redirects here, and the trailing form is
  // what Expensify's own help-centre config block publishes. Presetting the redirect would work
  // but adds a hop for no reason.
  'mcp-expensify': { transport: 'remote', url: 'https://www.expensify.com/mcp/', auth: 'oauth' },
  'mcp-avalara': { transport: 'remote', url: 'https://mcp.avalara.com/avatax', auth: 'oauth' },
  'mcp-twelvedata': { transport: 'remote', url: 'https://mcp.twelvedata.com/mcp', auth: 'oauth' },
  // The docs' own uvx block. `--from git+…` is theirs, not ours: it is what the vendor's
  // `mcpServers` example pins, and it needs no Docker daemon the way the README's block does.
  'mcp-sec-edgar': {
    transport: 'local',
    command: 'uvx',
    args: '--from git+https://github.com/stefanoamorelli/sec-edgar-mcp.git sec-edgar-mcp',
    env: 'SEC_EDGAR_USER_AGENT=',
  },
  'mcp-fred': {
    transport: 'local',
    command: 'npx',
    args: '-y fred-mcp-server',
    env: 'FRED_API_KEY=',
  },
}

const FAMILY_OF_FILTER: Record<FamilyKey, ConnectorFamily> = Object.fromEntries(
  FAMILY_SECTIONS.map((s) => [s.key, s.family]),
) as Record<FamilyKey, ConnectorFamily>

/** Chip labels come from the section table's `chipLabel` — one source of truth for the family
 *  vocabulary, shared with the list's tabs. */
export const DIRECTORY_FAMILY_CHIPS: [DirectoryFamilyFilter, string][] = [
  ['all', 'All'],
  ...FAMILY_SECTIONS.map((s) => [s.key, s.chipLabel] as [DirectoryFamilyFilter, string]),
]

/**
 * Whether `text` matches a search: every word must appear, and a plural finds its singular
 * ("emails" finds Email, "banks" finds a bank feed). Shared by the list and the directory so a
 * search reads the same in both.
 */
export function matchesSearch(text: string, query: string): boolean {
  const hay = text.toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every(
      (w) => hay.includes(w) || (w.length > 3 && w.endsWith('s') && hay.includes(w.slice(0, -1))),
    )
}

/**
 * Filter + sort the catalog for the grid.
 *
 * Installed entries are NOT removed — an installed connector keeps its card and swaps its `+`
 * for a gear (spec §6). Filtering them out is what produced "No matches" on the Channels chip
 * once the one real channel was already set up.
 */
export function filterDirectory(
  entries: DirectoryRow[],
  family: DirectoryFamilyFilter,
  query: string,
  sort: DirectorySort,
  category: ConnectorCategory | 'all' = 'all',
): DirectoryRow[] {
  const q = query.trim().toLowerCase()
  return entries
    .filter((d) => family === 'all' || d.families.includes(FAMILY_OF_FILTER[family]))
    .filter((d) => category === 'all' || d.categories.includes(category))
    .filter((d) => !q || matchesSearch(`${d.name} ${d.sub} ${d.categories.join(' ')}`, q))
    .sort((a, b) => (sort === 'az' ? byName(a, b) : byPopular(a, b)))
}

const byName = (a: DirectoryRow, b: DirectoryRow) => a.name.localeCompare(b.name)

/** "Popular": what you already have first, then the popular picks, then everything else —
 *  each group A–Z. Before, only the popular flag counted and the rest kept the catalog's
 *  hand-written order, so the list read as unsorted (owner review, 2026-10-29). */
function byPopular(a: DirectoryRow, b: DirectoryRow): number {
  const rank = (d: DirectoryRow) => (d.installed ? 0 : d.popular ? 1 : 2)
  return rank(a) - rank(b) || byName(a, b)
}

/** Popular tiles show ONLY on the unfiltered, unsearched view (spec §6). */
export function popularEntries(entries: DirectoryRow[]): DirectoryRow[] {
  return entries.filter((d) => d.popular)
}

/**
 * The generic MCP card has nothing to preview — no fixed endpoint, no tool list, no publisher —
 * so a click on it anywhere goes straight to the add form (spec §6).
 */
/**
 * The endpoint this entry would actually be reached at — a URL for a hosted server, the full
 * command line for a local one. This is what the vendor-trust panel means by "what you are
 * trusting is the endpoint, not the label", so it must never be a repo link.
 *
 * Returns `undefined` when there is nothing to show yet: the generic custom-server entry, or a
 * catalog entry with no install preset.
 */
export function previewEndpoint(entry: DirectoryRow): string | undefined {
  if (entry.installed) return entry.url
  const preset = MCP_INSTALL_PRESETS[entry.id]
  if (!preset) return undefined
  if (preset.transport === 'remote') return preset.url
  return [preset.command, preset.args].filter(Boolean).join(' ') || undefined
}

export function skipsPreview(entry: DirectoryRow): boolean {
  return !!entry.generic
}

/**
 * Catalog provenance for an INSTALLED server, matched by endpoint.
 *
 * The wire view carries no provenance, so `adapter.toEntry` alone can only ever say `unknown`
 * for an MCP server — which meant Playwright MCP read "Verified" on its directory card and
 * "Unknown publisher" on its own detail page one click later. Same server, two answers: exactly
 * the inconsistency the three-state field exists to prevent.
 *
 * The match is on the ENDPOINT, not the name, because the endpoint is the thing being trusted
 * and the only part a user cannot rename. A server pointed at a different endpoint therefore
 * inherits nothing, which is the safe direction: it falls back to `unknown`.
 */
export function catalogProvenanceFor(endpoint: string | undefined): CatalogProvenance | null {
  const needle = endpoint?.trim()
  if (!needle) return null
  for (const entry of CATALOG) {
    if (!entry.mcp) continue
    const preset = MCP_INSTALL_PRESETS[entry.id]
    if (!preset) continue
    const known =
      preset.transport === 'remote'
        ? preset.url
        : [preset.command, preset.args].filter(Boolean).join(' ')
    if (known && known.trim() === needle) {
      return {
        publisher: entry.publisher,
        verification: entry.verification,
        repoUrl: entry.repoUrl,
        homepageUrl: entry.homepageUrl,
        logo: connectorLogo(entry.id),
        categories: entry.categories,
      }
    }
  }
  return null
}
