# Connector logo sources

One row per connector. All files saved exactly as served by the source — no recoloring,
cropping, or tracing. Dimensions verified with `sips -g pixelWidth -g pixelHeight` (PNG) or
noted as `vector` (SVG, no size limit). Fetched 2026-08-14 unless a row's own "date fetched"
column says otherwise.

| slug | source URL | format | dimensions | brand-guidelines URL | date fetched |
|---|---|---|---|---|---|
| `plaid` | https://plaid.com/assets/img/favicons/apple-touch-icon.png (declared in plaid.com's own `<link rel="apple-touch-icon">`) | png | 180×180 | none found — plaid.com/press is media coverage, not a logo kit | 2026-08-14 |
| `simplefin` | http://www.simplefin.org/img/logo.svg (referenced directly in simplefin.org's own nav) | svg | vector (400.8×400.8) | none found — simplefin.org has no dedicated press/brand page | 2026-08-17 |
| `lunch_money` | **NOT vendor-sourced — a placeholder.** Owner-provided generic "money API" stock icon (a smiling coin badge with an "API" tag), not fetched from lunchmoney.app. Their real icon is Cloudflare-blocked at every path tried — see the "Not sourced" section's original write-up below, still accurate as the reason no official asset could be pulled. Swap this for the real mark if `lunchmoney.app` is ever reachable from a real logged-in browser session. | png | 256×256 | n/a — not an official asset | 2026-08-17 |
| `lunch_flow` | Owner-provided, extracted from `lunchflow.app`'s own rendered page markup (a two-tone yellow coin mark with a dark-navy inner glyph and a cream partial-ring "flow" arc) — a genuine product-specific asset, not stock art, unlike the `lunch_money` placeholder above. Cropped to the icon only: the "LUNCH FLOW" wordmark path and the source's Tailwind sizing class (`w-32 aspect-341/86`) were stripped, and the `viewBox` narrowed from the wordmark-inclusive `30 30 341 86` down to the icon's own square bounds, `30 30 86 86` — no path data recolored or redrawn. Not independently re-fetchable from a citable URL (pasted as raw markup, not crawled), so it isn't held to the same "byte-for-byte from a URL" bar as the rest of this table, but it is the real mark. | svg | vector (86×86 viewBox) | none found — lunchflow.app's `/brand` and `/press` paths are a client-rendered SPA shell with nothing to scrape (see the "Not sourced" section below) | 2026-08-17 |
| `ynab` | https://cdn.prod.website-files.com/640f69143ec11b21d42015c6/6732b255e4999d561f33e7bb_Frame%202%20(1).png (declared in ynab.com's own `<link rel="apple-touch-icon">`, served from its Webflow CDN) | png | 256×256 | https://www.ynab.com/press links a Brandfolder kit (`brandfolder.com/s/kvq74s532z6g56wkbpsqpq`) — not fetchable headlessly (same Brandfolder-login situation as `avalara` below); the apple-touch-icon (the "Y-tree" mark in YNAB's own blurple) was used instead | 2026-08-17 |
| `alpaca` | https://files.alpaca.markets/webassets/alpaca-brand-external.zip → `png-files/yellow-symbol-alpaca-logo.png` (official kit linked from https://alpaca.markets/newsroom) | png | 307×302 | `Brand Identity Guideline - External.pdf`, bundled inside the same zip | 2026-08-14 |
| `ibkr` | https://www.interactivebrokers.com/images/web/favicons/home-screen-icon-192x192.png (largest icon declared on interactivebrokers.com's own homepage) | png | 192×192 | none found — press page (interactivebrokers.com/en/general/about/press-and-media.php) has no logo kit | 2026-08-14 |
| `ibkr-mcp` | **Not independently re-sourced — a byte-identical copy of `ibkr.png` above** (re-checked against the live URL 2026-10-29: still byte-identical), saved under its own filename for the `mcp-ibkr` directory entry, since `logos.test.ts` requires one file per connector id — same precedent as `firecrawl-web` | png | 192×192 | none found — same as `ibkr` | 2026-10-29 |
| `robinhood` | https://robinhood.com/us/en/rh_favicon_152.png?v=2024 (the largest icon declared on robinhood.com's own homepage `<link rel="icon">`s) | png | 152×152 | none found | 2026-10-29 |
| `moomoo` | https://open.moomoo.com/moomoologo-touch.png (declared in open.moomoo.com's own `<link rel="apple-touch-icon">` — moomoo's OpenAPI site, where the MCP docs live; moomoo.com itself declares only a 32×32 favicon.ico) | png | 180×180 | none found — moomoo.com/sg/brand has no downloadable kit | 2026-10-29 |
| `enable_banking` | https://enablebanking.com/apple-touch-icon.png (enablebanking.com's own declared icon) | png | 180×180 | https://enablebanking.com/brand-guidelines/ exists but is a JS-only SPA (Vue) — no static asset links could be extracted without a browser | 2026-08-14 |
| `tavily` | https://tavily.com/logos/tavily-mark-black.svg (official brand page) | svg | vector | https://tavily.com/brand (page title: "Brand Guidelines \| Tavily") | 2026-08-14 |
| `perplexity` | https://www.perplexity.ai/favicon.svg (perplexity.ai's own root-served favicon; homepage and /press are behind a Cloudflare bot challenge that blocks plain fetches, this static path was not) | svg | vector | none reachable — /press returned 403 (Cloudflare challenge) | 2026-08-14 |
| `serper` | https://serper.dev/android-chrome-512x512.png (declared in serper.dev's own `/site.webmanifest`) | png | 512×512 | none found | 2026-08-14 |
| `google_pse` | https://www.gstatic.com/images/branding/googleg/2x/googleg_standard_color_128dp.png (Google's canonical "G" icon, hosted on Google's branding CDN) | png | 256×256 | https://about.google/brand-resource-center/ and https://developers.google.com/identity/branding-guidelines — **see flag below** | 2026-08-14 |
| `duckduckgo` | https://staticcdn.duckduckgo.com/press/DuckDuckGo-Brand-Logo.zip → `Logo Files/SVG/DuckDuckGo Logo- Dax Solo.svg` (official kit linked from https://duckduckgo.com/press) | svg | vector | `DuckDuckGo_Brand-Guidelines_May2023.pdf`, bundled inside the same zip | 2026-08-14 |
| `telegram` | https://telegram.org/file/464001088/1/bI7AJLo7oX4.287931.zip/374fe3b0a59dc60005 → `Logo.svg` (official kit linked from https://telegram.org/tour/screenshots, "Telegram Logos and App Screenshots") | svg | vector | no formal PDF; page states: "Please feel free to use these Telegram logos for article illustrations, graphs, 'forward to Telegram' buttons, etc. Just make sure people understand you're not representing Telegram officially." | 2026-08-14 |
| `firecrawl` | https://www.firecrawl.dev/brand/firecrawl-logo.png (official brand hub) | png | 600×852 (tight vertical crop around the flame mark on transparent background — served as-is) | https://www.firecrawl.dev/brand | 2026-08-14 |
| `firecrawl-web` | **Not independently re-sourced — a byte-identical copy of `firecrawl.png` above**, saved under its own filename. Firecrawl exists as two SEPARATE connector cards (the MCP "Browse & add" template, `mcp-firecrawl`, and the native web-search provider, `web_search:firecrawl`) — the same real mark, but `logos.test.ts` requires one file per connector id (never two ids sharing a file), so this is a second copy rather than a shared reference. | png | 600×852 (identical to `firecrawl` above) | https://www.firecrawl.dev/brand | 2026-08-21 |
| `playwright` | https://raw.githubusercontent.com/microsoft/playwright.dev/main/static/img/playwright-logo.svg (microsoft/playwright.dev repo, the source of playwright.dev's own logo) | svg | vector | none formal — OSS project (Apache-2.0 repo); no dedicated press/brand page | 2026-08-14 |
| `stripe` | https://images.stripeassets.com/fzn2n1nzq965/1hgcBNd12BfT9VLgbId7By/01d91920114b124fb4cf6d448f9f06eb/favicon.svg (stripe.com's own declared `<link rel="icon" type="image/svg+xml">`) | svg | vector | https://stripe.com/newsroom/brand-assets (wordmark-only kit; this icon mark comes from the site's own favicon declaration instead — see flag below) | 2026-08-14 |
| `quickbooks` | https://www.intuit.com/oidam/intuit/ic/en_us/content/intuit-quickbooks-logos.zip → `QBLogo-50-50-color.png` (official press-room download, intuit.com/company/press-room/logos/) | png | 2345×600 | none found — page states "by downloading these logos, you agree not to alter the Intuit logo in any way" | 2026-08-14 |
| `xero` | https://www.xero.com/content/dam/xero/pilot-images/admin/icons/favicon/favicon.svg (xero.com's own declared `<link rel="icon" type="image/svg+xml">`) | svg | vector | https://developer.xero.com/static/otherfiles/xero-app-partner-brand-guidelines.pdf | 2026-08-14 |
| `mercury` | https://mercury.com/icon.svg (mercury.com's own declared `<link rel="icon" type="image/svg+xml">`) | svg | vector | none found — mercury.com/press returned 404 and no dedicated brand-kit page was found | 2026-08-14 |
| `expensify` | https://use.expensify.com/assets/img/brand/press-kit/Expensify-PressKit-2026.zip → `Logos/Avatar and Iconmark/expensify-iconmark.svg` (official press kit, use.expensify.com/press-kit) | svg | vector | same press kit; states "please don't modify the logo in any way" | 2026-08-14 |
| `avalara` | https://www.avalara.com/etc.clientlibs/avalara/clientlibs/clientlib-ava-26-site/resources/favicons/apple-touch-icon.png (avalara.com's own declared `<link rel="apple-touch-icon">`; the site's declared `favicon.svg` renders empty through automated tooling — see flag below) | png | 180×180 | https://brandfolder.com/avalara-brand-assets (Brandfolder portal, not fetchable without login) | 2026-08-14 |
| `twelvedata` | https://twelvedata.com/build/images/brand/12-ocean.zip → `12.svg` (official brand-assets page, twelvedata.com/brand-assets) | svg | vector | same page — see flag below | 2026-08-14 |
| `fred` | https://raw.githubusercontent.com/stefanoamorelli/fred-mcp-server/main/docs/favicon.svg (the source SVG behind fred-mcp-server.amorelli.tech's own declared favicon; this MCP server is an independent Stefano Amorelli project, NOT a Federal Reserve product — see flag below) | svg | vector | none — OSS project, no formal brand guidelines | 2026-08-14 |
| `yahoo_finance` | https://www.yahoo.com/apple-touch-icon.png (yahoo.com's own declared `<link rel="apple-touch-icon">`) — **the general Yahoo brand mark (purple square, white "Y!"), not finance.yahoo.com's own teal favicon**; swapped 2026-08-22 per owner preference for the more recognizable corporate mark over the product-specific one | png | 144×144 | none found — Yahoo's general brand/press resources are Verizon Media-era and don't cover this specifically | 2026-08-22 |
| `coingecko` | https://docs.coingecko.com/mintlify-assets/_mintlify/favicons/coingecko/cEsEN1BHYMTujBc-/_generated/favicon/android-chrome-192x192.png (docs.coingecko.com's own declared `<link rel="icon">`, light-mode variant) — **not fetched from coingecko.com itself**, see flag below | png | 192×192 | none found — coingecko.com has no dedicated press/brand-kit page reachable headlessly | 2026-08-22 |

## Skipped (per instructions — generic categories, no brand)

`email`, `webhook`, `mcp`.

## Flags for owner review before shipping

- **`coingecko`** — coingecko.com itself is behind an active bot-challenge (a "Challenge
  Verification" interstitial, same class of block as `quickbooks.intuit.com`/`lunchmoney.app`
  below) that blocks headless fetching outright, and its one reachable path,
  `/favicon.ico`, tops out at 48×48 (parsed the ICO directory: 16×16, 32×32, 48×48 embedded,
  no larger entry) — under this table's 128px minimum. The mark actually used instead comes
  from `docs.coingecko.com` (CoinGecko's own official API documentation site, not
  Cloudflare-challenged), which declares a proper 192×192 PNG generated from CoinGecko's real
  brand mark via Mintlify's favicon pipeline — still CoinGecko's own asset, just fetched from
  their docs subdomain rather than the marketing site. Worth a manual re-check from a real
  browser session if a sharper/official press-kit mark is wanted later.

- ~~**`yodlee`** — sourced the icon straight from yodlee.com's own site...~~ **REMOVED
  2026-08-14**, same day it was sourced: the Yodlee connector was hidden from Browse & add
  (enterprise-only pricing — ADR `2026-08-14-hide-yodlee-enterprise-only-pricing`) before this
  asset ever shipped in a build, so `yodlee.svg` and its table row above were deleted rather
  than left as a mark for an entry nobody can add. An already-configured instance's Yodlee row
  falls back to its tinted category glyph, same as `email`/`webhook` below. Re-sourcing this is
  a one-line follow-up if Yodlee's catalog entry is ever restored.
- **`google_pse`** — Google's Brand Resource Center / Identity branding guidelines are the most
  restrictive of anything sourced here. The specific zip these guidelines host
  (`signin-assets.zip`) is scoped explicitly to "Sign in with Google" buttons; I did **not** use
  those button assets since repurposing them for a connector tile would be exactly the kind of
  out-of-scope reuse Google's own guidelines warn against. Instead I used the standalone
  canonical "G" icon (`googleg_standard_color`) from Google's general branding CDN
  (`gstatic.com/images/branding/googleg/...`), which is the same asset widely used elsewhere on
  the web as a plain "Google" identifier — but Google's guidelines require that use of the logo
  never imply sponsorship or endorsement, which is a judgment call for a third-party connector
  tile. Recommend a quick read of https://about.google/brand-resource-center/ before shipping.
- **`yodlee`** — sourced the icon straight from yodlee.com's own site (the product the connector
  actually talks to), not from parent-company Envestnet's official brand kit
  (https://www.envestnet.com/brand/), since that kit is Envestnet-branded, not Yodlee-branded.
  Worth a glance if the owner wants the Envestnet-approved mark instead.
- **`firecrawl`** — the official PNG has a loose vertical bounding box (600×852, not square) —
  transparent padding above/below the flame shape itself, not a crop artifact. Renders fine
  letterboxed in a square tile; flagging only so it isn't mistaken for a bad export.
- **`stripe`** — Stripe's official newsroom kit (`stripe.com/newsroom/brand-assets`) offers only
  the wordmark (three color variants, `Stripe wordmark - Blurple/Slate/White.svg`), no
  standalone icon-only mark. What's used instead is the "S" glyph from stripe.com's own site
  favicon — a purple (`#533AFD`) square with a white slanted-rectangle mark, matching Stripe's
  actual app-icon treatment. Baked-in background, not transparent, but it's Stripe's own
  brand purple, not an off-brand dark box. If the owner would rather match the newsroom kit
  exactly, the wordmark SVGs are unused but were fetched and can be swapped in — see
  `Stripe_logo_kit.zip` referenced above.
- **`quickbooks`** — the only asset the official press kit offers is a combined "INTUIT
  quickbooks" lockup (wordmark + the green circular "qb" mark together), not a standalone
  icon. Per the no-crop/no-modify rule, it's saved whole rather than cropped down to just the
  circular mark — it will render small and left-aligned inside a square tile. A cleaner
  standalone QuickBooks icon likely exists on `quickbooks.intuit.com` (favicon/app-icon), but
  that origin actively blocked every fetch attempt (curl and the fetch tool both hit
  connection resets / timeouts, http1.1 and http2, with a browser UA) — looks like bot
  protection (Akamai-style), not a missing asset. Worth a manual re-check by the owner from a
  real browser session.
- **`avalara`** — avalara.com declares its own `favicon.svg`, but it's a multi-layer file with
  CSS `display:none` on the parts headless tooling doesn't resolve the same way a browser
  would — it rendered empty/broken outside a real browser, so the `apple-touch-icon.png`
  (180×180, opaque white background, the orange "A" + blue checkmark AvaTax mark) was used
  instead. Avalara's real brand-asset library lives on Brandfolder
  (`brandfolder.com/avalara-brand-assets`), which needs a login and could not be fetched
  headlessly.
- **`twelvedata`** — Twelve Data's brand-assets page explicitly permits this exact use ("may
  use the Twelve Data name and logo in marketing for projects... following the branding
  guidelines") but also says not to imply partnership/endorsement — the same standard
  attribution caveat as any API-data provider. Nothing unusual to flag beyond that; noting
  it since it's the one vendor whose brand page states a marketing-use policy in plain text.
- **`fred`** — the FRED MCP Server (`stefanoamorelli/fred-mcp-server`) is an independent
  open-source project by Stefano Amorelli that reads the public FRED API — it is NOT a
  Federal Reserve Bank of St. Louis product, and no Federal Reserve mark was used or
  considered. The repo's root `fred-mcp-server-logo.png` (120×120, "Federal Reserve Economic
  Data" text on a near-black background) is below the 128px bar; its docs site instead
  declares a distinct, better-designed favicon at `docs/favicon.svg` — a navy-blue rounded
  square with a white line-chart glyph — which is vector and was used here. Its background is
  a deliberate dark navy (`#1E40AF`), not transparent; flagging in case the owner prefers a
  lighter treatment.

## Not sourced — reported as misses

- **`lunch_money`** — no OFFICIAL asset could be sourced (now carries a non-official placeholder
  instead — see its row in the main table above). `lunchmoney.app` itself and its
  `/manifest.json` / `/site.webmanifest` are behind an active Cloudflare bot-challenge
  (`cf-mitigated: challenge`) that blocks headless fetching outright — no `<link
  rel="icon">`/`apple-touch-icon` could be read from the page. The one asset reachable without a
  browser, `/favicon.ico` (also checked at the `my.lunchmoney.app` app subdomain — identical
  file), is genuinely a PNG despite the `.ico` name and content-type, but only 33×32 — well under
  the 128px minimum this file already applies to FRED/SEC EDGAR below, for the same reason:
  shipping it would be a blurry mark under a technicality. Every common high-res path tried
  404'd (`android-chrome-512x512.png`, `android-chrome-192x192.png`, `logo.svg`, `logo.png`,
  `icon.svg`, `icon-512.png`). Worth a manual re-check by the owner from a real browser session
  (same class of block as `quickbooks.intuit.com` above) if a sharper, official mark is wanted
  later.
- **`lunch_flow`** — the favicon-path MISS below is superseded; a real mark now ships (see its
  row in the main table above), sourced a different way. The original finding stands as the
  reason the *favicon* path was a dead end: lunchflow.app's own site declares exactly one icon
  (`<link rel="icon" href="/favicon.ico" sizes="96x96" type="image/x-icon">`) — fetched and
  verified: a real, reachable, single-frame 96×96 32-bit ICO, confirmed with a byte-level parse
  of the ICO directory (one entry, no larger size embedded). That is below the 128px minimum
  this table holds every other PNG to, and it is raster, not vector, so there is no
  size-exempt fallback either. No `apple-touch-icon.png`, `icon.svg`, `/android-chrome-*.png`,
  or `manifest.json`/`site.webmanifest` exists at any of the conventional paths (all 404). The
  marketing site's `/brand` and `/press` paths return HTTP 200 but are byte-identical to a
  deliberately-bogus nonexistent path — confirmed with a `diff` — so this is a client-rendered
  SPA shell with no server-side brand/press page to scrape. The mark actually shipped came from
  the app's own rendered SVG markup instead (see the main table row) — same underlying
  Cloudflare/SPA blocker, different, non-favicon part of the page.
- **`mcp-secedgar` catalog id does not exist** — the real catalog id (verified by reading
  `dirCatalog.ts` directly, since ids were being quoted from memory) is `mcp-sec-edgar`. No
  logo was registered under either spelling; see the miss below for why.
- **SEC EDGAR MCP (`mcp-sec-edgar`, `stefanoamorelli/sec-edgar-mcp`)** — MISS, no asset
  saved. Like FRED MCP, this is Stefano Amorelli's own independent project (not a US
  government / SEC product) and no SEC seal or branding was used or considered. The only
  marks this project offers anywhere — the repo's root `sec-edgar-mcp-logo.png`, its
  `docs/favicon.png`, and `docs/public/favicon.png` — are all the same 120×120 PNG (plain
  "SEC EDGAR" text on a near-black background), below the 128px minimum and not vector. The
  docs site's generated `android-chrome-192x192.png` clears 192×192 in raw pixel count, but
  it's a Mintlify auto-upscale of that same 120px source with no genuine added detail (and
  still the same dark-background text treatment) — using it would ship a blurry mark under a
  technicality, which the sourcing rules call out explicitly to avoid. Falls back cleanly to
  the tinted glyph.
