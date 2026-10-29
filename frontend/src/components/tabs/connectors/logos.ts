// Real brand marks for the connectors that have one.
//
// Spec §10 said icons stay generic category glyphs because "real logos are a licensing
// question, not a design one". That question has since been ANSWERED, per file, in
// `frontend/public/connector-logos/SOURCES.md` — every mark there was fetched from the
// vendor's own official brand kit or the icon their own site declares, saved byte-for-byte
// with no recolouring or tracing, and carries its source URL, dimensions and brand-guidelines
// link. So the glyphs stop being the only answer; they become the FALLBACK. Recorded as
// divergence D25 in the handoff's PARITY_AND_GAPS.md.
//
// THIS MODULE IS A LEAF — it imports nothing, so `catalog` (itself a leaf) can read it.
//
// The map is keyed by the EXACT catalog / connector id, never by a normalised or
// prefix-stripped one. That is what keeps a user-added MCP server out of it: its id is
// `mcp:<server_id>` where the server_id is whatever the user typed, so a server called
// `stripe` cannot borrow Stripe's mark by naming itself after it. An installed server that
// really IS a catalog entry gets its logo the same way it gets its provenance — matched back
// to the catalog BY ENDPOINT (`catalogProvenanceFor`, divergence D14), the one part of a
// server a user cannot rename.

/** Served from `frontend/public/connector-logos/` — see that directory's `SOURCES.md`. */
const LOGO_DIR = '/connector-logos'

/**
 * Connector/catalog id → file name. Every file in the directory appears exactly once; a
 * connector absent from this map has no mark and falls back to its tinted category glyph,
 * which is the ordinary case for `email`, `webhook`, the generic custom-server card, and
 * every hosted MCP vendor we could not source an official asset for.
 *
 * Exported for `logos.test.ts` only, which walks the directory on disk in both directions —
 * a file nobody references and a reference to a file that is not there are both defects a
 * hand-maintained table acquires silently.
 */
export const LOGO_FILES: Record<string, string> = {
  telegram: 'telegram.svg',
  plaid: 'plaid.png',
  simplefin: 'simplefin.svg',
  // Not vendor-sourced — a placeholder pending a real lunchmoney.app asset. See SOURCES.md.
  lunch_money: 'lunch_money.png',
  lunch_flow: 'lunch_flow.svg',
  ynab: 'ynab.png',
  alpaca: 'alpaca.png',
  ibkr: 'ibkr.png',
  enable_banking: 'enable_banking.png',
  yahoo_finance: 'yahoo_finance.png',
  coingecko: 'coingecko.png',
  'web_search:duckduckgo': 'duckduckgo.svg',
  'web_search:tavily': 'tavily.svg',
  'web_search:perplexity': 'perplexity.svg',
  'web_search:serper': 'serper.png',
  'web_search:google_pse': 'google_pse.png',
  'web_search:firecrawl': 'firecrawl-web.png',
  'mcp-playwright': 'playwright.svg',
  'mcp-firecrawl': 'firecrawl.png',
  'mcp-stripe': 'stripe.svg',
  'mcp-quickbooks': 'quickbooks.png',
  'mcp-xero': 'xero.svg',
  'mcp-mercury': 'mercury.svg',
  'mcp-ibkr': 'ibkr-mcp.png',
  'mcp-robinhood': 'robinhood.png',
  'mcp-moomoo': 'moomoo.png',
  'mcp-alpaca': 'alpaca-mcp.png',
  'mcp-coinbase': 'coinbase.png',
  'mcp-okx': 'okx.png',
  'mcp-kraken': 'kraken.png',
  'mcp-expensify': 'expensify.svg',
  'mcp-avalara': 'avalara.png',
  'mcp-twelvedata': 'twelvedata.svg',
  'mcp-fred': 'fred.svg',
}

/** The brand mark for a connector, or `undefined` when it has none. */
export function connectorLogo(id: string | undefined): string | undefined {
  const file = id ? LOGO_FILES[id] : undefined
  return file ? `${LOGO_DIR}/${file}` : undefined
}
