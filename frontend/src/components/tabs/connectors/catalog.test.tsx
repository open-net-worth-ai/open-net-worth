// @vitest-environment jsdom
//
// The catalog as DATA, and the one thing that makes a catalog card worth more than the generic
// "Custom MCP server" card: an install preset that actually lands in the add form.
//
// Three defects this file exists to keep out:
//
//   1. A FABRICATED preset. The prototype shipped `https://mcp.playwright.dev`, a URL that has
//      never existed, so the one card that looked most finished was the one guaranteed to fail
//      its probe. Every preset here is asserted against the shape the vendor publishes, and a
//      preset keyed to an id no entry carries is a silent no-op — so that is asserted too.
//   2. An OVERCLAIMED publisher. `verification` is the field an internal RFC got wrong when it
//      called SEC EDGAR MCP and FRED MCP official; both are one independent maintainer's
//      projects. The table below is pinned per entry, not spot-checked.
//   3. A CARD THAT LOOKS BROKEN WITHOUT A REPO. Hosted vendor servers (Mercury, Avalara,
//      Expensify) have no GitHub at all, which is the normal case for that class and must render
//      as cleanly as one that does.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, act, renderHook } from '@testing-library/react'
import {
  CATALOG,
  RETIRED_CATALOG,
  MCP_INSTALL_PRESETS,
  catalogProvenanceFor,
  filterDirectory,
  previewEndpoint,
  type CatalogEntry,
  type DirectoryRow,
} from './catalog'
import { connectorLogo } from './logos'
import { CONNECTOR_CATEGORIES } from './model'
import { resolveDirectory } from './directoryRows'
import { DirectoryPreview } from './DirectoryPreview'
import { useMcpAddFlow } from './McpAddFlow'
import { toWireConfig } from './mcpApi'

afterEach(cleanup)

const MCP_ENTRIES = CATALOG.filter((e) => e.mcp)
const byId = (id: string): CatalogEntry => {
  const entry = CATALOG.find((e) => e.id === id)
  if (!entry) throw new Error(`no catalog entry ${id}`)
  return entry
}
const rowFor = (id: string): DirectoryRow => {
  const row = resolveDirectory([]).find((r) => r.id === id)
  if (!row) throw new Error(`no directory row ${id}`)
  return row
}

describe('the catalog is well-formed', () => {
  it('gives every entry a unique id and the fields a card needs', () => {
    expect(new Set(CATALOG.map((e) => e.id)).size).toBe(CATALOG.length)
    for (const e of CATALOG) {
      expect(e.name.trim(), e.id).not.toBe('')
      expect(e.icon.trim(), e.id).not.toBe('')
      expect(e.hue.trim(), e.id).not.toBe('')
      expect(e.families.length, e.id).toBeGreaterThan(0)
      expect(['verified', 'unverified', 'unknown'], e.id).toContain(e.verification)
      // `publisher: null` is only ever the generic add-flow launcher, which claims no author.
      expect(e.publisher === null, e.id).toBe(!!e.generic)
    }
  })

  it('leaves sub/desc to the live backend for every native connector — never both places', () => {
    // 2026-08-14: a native connector's card text used to be hand-authored here AND in
    // `_SHORT_DESC`/`_LONG_DESC` (backend/services/connectors.py), and the two silently
    // drifted apart (the owner hit this three times editing Email's copy in one session). An
    // MCP entry has no backend counterpart, so it's the one class still authoring both fields
    // directly; a native entry must author NEITHER — `resolveDirectory` reads them off the
    // live `ConnectorView` instead (see `directoryRows.test.ts` for that wiring).
    for (const e of CATALOG) {
      if (e.mcp) {
        expect(e.sub?.trim(), e.id).toBeTruthy()
        expect(e.desc?.trim(), e.id).toBeTruthy()
      } else {
        expect(e.sub, e.id).toBeUndefined()
        expect(e.desc, e.id).toBeUndefined()
      }
    }
  })

  it('links only to real absolute URLs, never a bare slug', () => {
    for (const e of CATALOG) {
      for (const url of [e.repoUrl, e.homepageUrl, e.supportUrl, e.privacyUrl]) {
        if (url === undefined) continue
        expect(url, e.id).toMatch(/^https:\/\/\S+$/)
      }
    }
  })

  it('never relabels one link as another — a homepage is not a support desk', () => {
    // Four distinct labels in the preview footer pointing at one URL is the dead-link failure
    // wearing a politer costume: every row looks like somewhere to go and only one is.
    for (const e of CATALOG) {
      const urls = [e.repoUrl, e.homepageUrl, e.supportUrl, e.privacyUrl].filter(Boolean)
      expect(new Set(urls).size, e.id).toBe(urls.length)
    }
  })

  it('sends support for a self-run server to that server’s own issue tracker', () => {
    // A GitHub support link must belong to the SAME repo the entry ships, not a sibling — an
    // issue filed on the wrong project is a dead end that looks like it worked.
    for (const e of CATALOG) {
      if (!e.supportUrl?.includes('github.com')) continue
      expect(e.supportUrl, e.id).toBe(`${e.repoUrl}/issues`)
    }
  })
})

describe('categories describe the connector; families are the lane', () => {
  // The reported defect: Stripe's landing page read `CATEGORIES 1 → MCP` — the lane the reader
  // had just clicked, presented as the one thing we could say about the connector.

  it('draws every tag from the closed vocabulary — no entry invents its own', () => {
    // Without this, two accounting connectors end up filed under "Accounting" and
    // "Bookkeeping" and a category filter finds neither.
    const allowed = new Set<string>(CONNECTOR_CATEGORIES)
    for (const e of CATALOG) {
      for (const c of e.categories) expect(allowed.has(c), `${e.id}: ${c}`).toBe(true)
    }
  })

  it('keeps lane-only words out of the vocabulary entirely', () => {
    // `Channel` / `Data source` / `MCP` say only which lane you are browsing. `Web` is
    // deliberately NOT in this set even though a lane shares the word: as a topic it means
    // "this reaches the live web", which is the honest tag for Firecrawl and Playwright —
    // two entries in the MCP lane, where it can echo nothing.
    for (const c of CONNECTOR_CATEGORIES) {
      expect(['Channel', 'Data source', 'MCP']).not.toContain(c)
    }
  })

  it('never files an entry under its lane and nothing else', () => {
    for (const e of CATALOG) {
      if (e.generic) continue
      expect(e.categories.join(' • '), e.id).not.toBe(e.families.join(' • '))
    }
  })

  it('gives every real entry at least one, and the generic launcher none', () => {
    // The custom-server card describes no product — it is the add form's front door, and
    // filing it under a topic would claim knowledge of a server nobody has pointed at yet.
    for (const e of CATALOG) {
      if (e.generic) expect(e.categories, e.id).toEqual([])
      else expect(e.categories.length, e.id).toBeGreaterThan(0)
    }
  })

  it('files each lane where a user would look for it', () => {
    const cats = (id: string) => byId(id).categories
    expect(cats('mcp-stripe')).toEqual(['Payments'])
    expect(cats('mcp-quickbooks')).toEqual(['Accounting'])
    expect(cats('mcp-xero')).toEqual(['Accounting'])
    expect(cats('plaid')).toEqual(['Banking'])
    expect(cats('web_search:serper')).toEqual(['Web search'])
    expect(cats('mcp-playwright')).toEqual(['Browsing'])
    expect(cats('telegram')).toEqual(['Messaging'])
  })

  it('does not tag a read-only banking server as payments', () => {
    // Mercury's server explicitly cannot move money, send a payment, or change anything.
    expect(byId('mcp-mercury').categories).toEqual(['Banking'])
  })

  it('leaves no vocabulary entry unused — a value nobody picks is a value nobody agreed on', () => {
    const used = new Set(CATALOG.flatMap((e) => e.categories))
    expect(CONNECTOR_CATEGORIES.filter((c) => !used.has(c))).toEqual([])
  })
})

describe('native entries carry links too, and every one was fetched', () => {
  // The MCP lane verified all 18 of its links by fetching them; the natives shipped with none
  // at all, so Serper's footer showed a bare unlinked Author and no More info block. These
  // were verified 2026-08-14 the same way: HTTP 200 DIRECT, no redirect, and the body read to
  // confirm the page is what the label claims. Two soft-404s were caught that way —
  // alpaca.markets/privacy and /privacy-policy both answer 200 with a rendered "not found".
  const NATIVE = CATALOG.filter((e) => !e.mcp)

  it('gives every native entry somewhere to read about it', () => {
    for (const e of NATIVE) expect(e.homepageUrl, e.id).toBeTruthy()
  })

  it('points a connector WE publish at our own site, and nobody else at it', () => {
    for (const e of CATALOG) {
      const ours = e.homepageUrl === 'https://opennetworth.ai'
      expect(ours, e.id).toBe(e.publisher === 'Open Net Worth')
    }
  })

  it('never claims our site for the generic custom-server entry (spec §5)', () => {
    const generic = byId('mcp-custom')
    expect(generic.publisher).toBeNull()
    expect(generic.homepageUrl).toBeUndefined()
    expect(generic.verification).toBe('unknown')
  })

  it('leaves a field UNDEFINED rather than shipping one that could not be verified', () => {
    // Perplexity answers 403 behind a bot challenge on every help-centre and legal path; the
    // honest result is no link, not a plausible-looking one.
    const perplexity = byId('web_search:perplexity')
    expect(perplexity.supportUrl).toBeUndefined()
    expect(perplexity.privacyUrl).toBeUndefined()
    expect(perplexity.homepageUrl).toBe('https://docs.perplexity.ai/docs/getting-started/overview')
  })

  it('sends each verified link to the page that was actually checked', () => {
    const expected: [string, 'homepageUrl' | 'supportUrl' | 'privacyUrl', string][] = [
      ['plaid', 'homepageUrl', 'https://plaid.com/docs/link/'],
      ['plaid', 'privacyUrl', 'https://plaid.com/legal/#end-user-privacy-policy'],
      ['enable_banking', 'homepageUrl', 'https://enablebanking.com/docs/api/reference/'],
      ['web_search:serper', 'privacyUrl', 'https://serper.dev/privacy'],
      [
        'web_search:google_pse',
        'homepageUrl',
        'https://developers.google.com/custom-search/v1/overview',
      ],
      ['telegram', 'privacyUrl', 'https://telegram.org/privacy'],
    ]
    for (const [id, field, url] of expected) expect(byId(id)[field], `${id}.${field}`).toBe(url)
  })

  it('gives a native connector no support link rather than a sales form', () => {
    // Enable Banking's and Tavily's "contact" pages are all sales funnels; Serper runs no
    // portal at all. A homepage relabelled Support is a dead end that looks like help.
    for (const id of ['enable_banking', 'web_search:serper', 'telegram', 'email']) {
      expect(byId(id).supportUrl, id).toBeUndefined()
    }
    expect(byId('web_search:tavily').supportUrl).toBe('https://help.tavily.com/')
  })
})

describe('Yodlee: disabled, not removed (2026-08-14)', () => {
  // ADR 2026-08-14-hide-yodlee-enterprise-only-pricing: enterprise-only pricing makes it
  // impractical for this app's actual self-hosted users, so it is no longer OFFERED as
  // something to add. Its backend (services/aggregation/yodlee.py, YodleeConfigStore,
  // /api/yodlee/*) stays fully intact — only this catalog entry is gone.

  it('is absent from the Browse & add catalog', () => {
    expect(CATALOG.find((e) => e.id === 'yodlee')).toBeUndefined()
  })

  it('is absent from a resolved directory row list too', () => {
    expect(resolveDirectory([]).find((r) => r.id === 'yodlee')).toBeUndefined()
  })
})

describe('MCP provenance — what we confirmed, stated exactly', () => {
  // Re-verified live via `gh api repos/<owner>/<repo>` before shipping. `verified` means the
  // publisher is the vendor it claims (an org-owned repo, or the vendor's own hosted endpoint);
  // `unverified` means real and known but NOT vendor-official.
  const EXPECTED: [string, string | null, string][] = [
    ['mcp-custom', null, 'unknown'],
    ['mcp-playwright', 'Microsoft', 'verified'],
    ['mcp-firecrawl', 'Firecrawl', 'verified'],
    ['mcp-stripe', 'Stripe', 'verified'],
    ['mcp-quickbooks', 'Intuit', 'verified'],
    ['mcp-xero', 'Xero', 'verified'],
    ['mcp-mercury', 'Mercury', 'verified'],
    // IBKR publishes this endpoint itself (ai-integrations page, connector v1.1.5 release notes,
    // 2026-07-28 press release) and serves it from api.ibkr.com.
    ['mcp-ibkr', 'Interactive Brokers', 'verified'],
    // Each vendor's own docs publish the endpoint: Robinhood's Help Center, moomoo's OpenAPI docs.
    ['mcp-robinhood', 'Robinhood', 'verified'],
    ['mcp-moomoo', 'moomoo', 'verified'],
    // Local servers each vendor publishes from its own org: alpacahq, @coinbase, okx.
    ['mcp-alpaca', 'Alpaca', 'verified'],
    ['mcp-coinbase', 'Coinbase', 'verified'],
    ['mcp-okx', 'OKX', 'verified'],
    ['mcp-kraken', 'Kraken', 'verified'],
    ['mcp-expensify', 'Expensify', 'verified'],
    ['mcp-avalara', 'Avalara', 'verified'],
    ['mcp-twelvedata', 'Twelve Data', 'verified'],
    // Community projects by one independent maintainer — NOT an SEC or Federal Reserve product.
    ['mcp-sec-edgar', 'Stefano Amorelli', 'unverified'],
    ['mcp-fred', 'Stefano Amorelli', 'unverified'],
  ]

  it('covers every MCP entry, so a new one cannot ship unpinned', () => {
    expect(MCP_ENTRIES.map((e) => e.id).sort()).toEqual(EXPECTED.map(([id]) => id).sort())
  })

  it.each(EXPECTED)('%s is published by %s (%s)', (id, publisher, verification) => {
    const entry = byId(id)
    expect(entry.publisher).toBe(publisher)
    expect(entry.verification).toBe(verification)
  })

  it('gives every server with a repository somewhere to ask for help', () => {
    // An issue tracker always exists on a public repo, so an absent support link there is an
    // oversight rather than a judgement call.
    for (const e of MCP_ENTRIES) {
      if (!e.repoUrl) continue
      expect(e.supportUrl, e.id).toBeTruthy()
    }
  })

  it('gives every HOSTED server the privacy policy that governs it', () => {
    // A remote preset means your data leaves this machine for the vendor's service, so the
    // policy covering that service is not optional context. A local stdio server reaches no
    // service of the publisher's and correctly has none.
    for (const e of MCP_ENTRIES) {
      if (MCP_INSTALL_PRESETS[e.id]?.transport !== 'remote') continue
      expect(e.privacyUrl, e.id).toBeTruthy()
    }
  })

  it('never lets a community server read as vendor-official', () => {
    for (const id of ['mcp-sec-edgar', 'mcp-fred']) {
      expect(byId(id).verification).not.toBe('verified')
      // The prose must not fight the badge either — both carry the maintainer's own disclaimer.
      expect(byId(id).desc).toMatch(/independently/i)
    }
  })
})

describe('MCP descriptions say what Lucius can actually do', () => {
  it('gives every MCP entry a short card line and a full preview paragraph', () => {
    for (const e of MCP_ENTRIES) {
      // The card renders `sub` on one line beside the name; a paragraph there would wrap badly.
      expect(e.sub?.length, e.id).toBeLessThanOrEqual(60)
      expect(e.desc?.length, e.id).toBeGreaterThanOrEqual(300)
      expect(e.desc, e.id).toMatch(/Lucius/)
    }
  })

  it('describes the full-CRUD servers as writing, not reading', () => {
    // Both default to registering create/update/delete tools; an internal RFC had them read-only.
    expect(byId('mcp-quickbooks').desc).toMatch(/create, update and delete tools are registered/i)
    expect(byId('mcp-xero').desc).toMatch(/write access is on by default/i)
  })

  it('describes the read-only servers as read-only', () => {
    for (const id of ['mcp-mercury', 'mcp-expensify']) {
      expect(byId(id).desc, id).toMatch(/read-only/i)
    }
  })
})

describe('install presets', () => {
  it('are keyed to a real MCP entry — a stray key would silently do nothing', () => {
    for (const id of Object.keys(MCP_INSTALL_PRESETS)) {
      expect(byId(id).mcp, id).toBe(true)
      expect(byId(id).generic, id).toBeUndefined()
    }
  })

  it('cover every MCP entry except the two that have nothing truthful to pre-fill', () => {
    // `mcp-custom` is the generic launcher (no fixed endpoint by definition); `mcp-quickbooks`
    // publishes no package or image, only `node <your clone>/dist/index.js`. Shipping a card
    // with an empty step 1 beats shipping one with an invented command.
    const missing = MCP_ENTRIES.filter((e) => !MCP_INSTALL_PRESETS[e.id]).map((e) => e.id)
    expect(missing.sort()).toEqual(['mcp-custom', 'mcp-quickbooks'])
  })

  it('never preset a repo or docs link as the endpoint', () => {
    for (const [id, preset] of Object.entries(MCP_INSTALL_PRESETS)) {
      const endpoint = previewEndpoint(rowFor(id))
      expect(endpoint, id).toBeTruthy()
      if (preset.transport === 'remote') {
        expect(endpoint, id).toBe(preset.url)
        expect(endpoint, id).not.toMatch(/github\.com/)
        expect(endpoint, id).not.toBe(byId(id).repoUrl)
        expect(endpoint, id).not.toBe(byId(id).homepageUrl)
      }
    }
  })

  it('carry the endpoint back to the catalog provenance, so both screens agree', () => {
    for (const id of Object.keys(MCP_INSTALL_PRESETS)) {
      const entry = byId(id)
      expect(catalogProvenanceFor(previewEndpoint(rowFor(id))), id).toEqual({
        publisher: entry.publisher,
        verification: entry.verification,
        repoUrl: entry.repoUrl,
        homepageUrl: entry.homepageUrl,
        // Identity travels with provenance and for the same reason (D14): an installed server
        // must not read one thing on its card and another on its detail page.
        logo: connectorLogo(entry.id),
        categories: entry.categories,
      })
    }
  })

  it('pre-seed env KEYS with no values — a preset must never ship a credential', () => {
    for (const [id, preset] of Object.entries(MCP_INSTALL_PRESETS)) {
      if (!preset.env) continue
      for (const line of preset.env.split('\n')) {
        expect(line, id).toMatch(/^[A-Z][A-Z0-9_]*=$/)
      }
    }
  })
})

describe('a preset lands in the add flow’s own fields', () => {
  const start = (id: string) => {
    const hook = renderHook(() => useMcpAddFlow({ takenIds: [], onSaved: () => {} }))
    act(() => hook.result.current.start(rowFor(id)))
    return hook.result
  }

  it('fills a stdio server’s command, args and required env key names', () => {
    const flow = start('mcp-xero')
    expect(flow.current.form.transport).toBe('local')
    expect(flow.current.form.command).toBe('npx')
    expect(flow.current.form.args).toBe('-y @xeroapi/xero-mcp-server@latest')
    expect(flow.current.form.env).toBe('XERO_CLIENT_ID=\nXERO_CLIENT_SECRET=')
    expect(flow.current.canConnect).toBe(true)

    // …and the wire config the probe would receive is a real stdio invocation.
    expect(toWireConfig(flow.current.form)).toMatchObject({
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@xeroapi/xero-mcp-server@latest'],
      env: { XERO_CLIENT_ID: '', XERO_CLIENT_SECRET: '' },
      auth_type: 'none',
    })
  })

  it('splits a multi-word uvx invocation into separate argv entries', () => {
    const flow = start('mcp-sec-edgar')
    expect(toWireConfig(flow.current.form)).toMatchObject({
      transport: 'stdio',
      command: 'uvx',
      args: ['--from', 'git+https://github.com/stefanoamorelli/sec-edgar-mcp.git', 'sec-edgar-mcp'],
      env: { SEC_EDGAR_USER_AGENT: '' },
    })
  })

  it('puts a token-authenticated hosted server on API key, but gates Test connect on a key', () => {
    const flow = start('mcp-stripe')
    expect(flow.current.form.transport).toBe('remote')
    expect(flow.current.form.url).toBe('https://mcp.stripe.com')
    // Stripe documents OAuth first but accepts a restricted key as `Authorization: Bearer`.
    expect(flow.current.form.auth).toBe('key')
    // A `key`-auth server with no key entered yet is a guaranteed 401 against the real
    // endpoint — Test connect stays disabled until one is typed in.
    expect(flow.current.canConnect).toBe(false)
    act(() => flow.current.setForm((f) => ({ ...f, key: 'rk_live_x' })))
    expect(flow.current.canConnect).toBe(true)
    expect(toWireConfig(flow.current.form)).toMatchObject({
      transport: 'http',
      url: 'https://mcp.stripe.com',
      auth_type: 'bearer',
      auth_header: 'Authorization',
    })
  })

  it('lands an OAuth-only server ready to Connect, URL pre-filled', () => {
    // RFC `2026-08-14-mcp-oauth` shipped the sign-in these four vendors need. `canConnect` no
    // longer requires a prior sign-in (2026-08-19): the one footer button now triggers sign-in
    // itself when idle, then chains straight into the probe once it lands — so it is enabled
    // the moment a URL exists, same as any other remote server.
    for (const id of ['mcp-mercury', 'mcp-avalara', 'mcp-expensify', 'mcp-twelvedata']) {
      const flow = start(id)
      expect(flow.current.form.url, id).toBe(MCP_INSTALL_PRESETS[id]!.url)
      expect(flow.current.form.auth, id).toBe('oauth')
      expect(flow.current.canConnect, id).toBe(true)
      expect(flow.current.oauth.status, id).toBe('idle')
      expect(flow.current.oauth.error, id).toBe(null)
      cleanup()
    }
  })

  it('opens an empty step 1 for an entry with no preset', () => {
    const flow = start('mcp-quickbooks')
    expect(flow.current.form.url).toBe('')
    expect(flow.current.form.command).toBe('')
    expect(flow.current.form.env).toBe('')
    expect(flow.current.canConnect).toBe(false)
    // It still arrives curated, so the review step vouches for Intuit rather than saying unknown.
    expect(flow.current.meta).toMatchObject({ publisher: 'Intuit', verification: 'verified' })
  })
})

describe('a hosted server with no repo renders cleanly', () => {
  // Asserted through hrefs and text rather than the preview's layout, which is another lane's
  // to move: what must hold is that a missing `repoUrl` leaves NO dangling link behind, and
  // that the endpoint on show is the preset — never the repo or docs URL.
  const noop = () => {}
  const hrefs = (root: HTMLElement) =>
    Array.from(root.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? '')

  it('renders a hosted vendor server that has no repository at all', () => {
    const mercury = rowFor('mcp-mercury')
    expect(mercury.repoUrl).toBeUndefined()
    const { container } = render(<DirectoryPreview d={mercury} onAdd={noop} onConfigure={noop} />)

    const links = hrefs(container)
    expect(links.every((h) => /^https:\/\/\S+$/.test(h))).toBe(true)
    expect(links).toContain('https://docs.mercury.com/docs/what-is-mercury-mcp')
    expect(links.some((h) => h.includes('github.com'))).toBe(false)
    expect(container.textContent).toContain('https://mcp.mercury.com/mcp')
    expect(container.textContent).toContain(mercury.desc)
  })

  it('renders the generic entry, which has neither a repo nor a docs page', () => {
    const custom = rowFor('mcp-custom')
    expect(custom.repoUrl).toBeUndefined()
    expect(custom.homepageUrl).toBeUndefined()
    const { container } = render(<DirectoryPreview d={custom} onAdd={noop} onConfigure={noop} />)

    // No endpoint to show and nothing to link to — and crucially, no `href="#"` placeholder.
    expect(hrefs(container)).toEqual([])
    expect(container.textContent).toContain(custom.desc)
  })

  it('shows the source repository for an entry that has one', () => {
    const { container } = render(
      <DirectoryPreview d={rowFor('mcp-sec-edgar')} onAdd={noop} onConfigure={noop} />,
    )
    expect(hrefs(container)).toContain('https://github.com/stefanoamorelli/sec-edgar-mcp')
  })
})

describe('retired entries', () => {
  it('keeps IBKR Flex and the Alpaca data source out, their entries set aside not deleted', () => {
    expect(CATALOG.some((e) => e.id === 'ibkr' || e.id === 'alpaca')).toBe(false)
    expect(RETIRED_CATALOG.map((e) => e.id).sort()).toEqual(['alpaca', 'ibkr'])
  })
})

describe('Sort: Popular', () => {
  it('lists what you have first, then the popular picks, then the rest — each A–Z', () => {
    const plaid = {
      id: 'plaid',
      kind: 'plaid',
      name: 'Plaid',
      enabled: true,
      status: 'active' as const,
      pausable: true,
      config: {},
      secrets_public: {},
      tools: [],
    }
    const rows = filterDirectory(resolveDirectory([plaid]), 'data', '', 'popular')
    const names = rows.map((r) => r.name)
    expect(names[0]).toBe('Plaid')
    const rest = rows.slice(1)
    const rank = (r: (typeof rows)[number]) => (r.installed ? 0 : r.popular ? 1 : 2)
    for (let i = 1; i < rest.length; i++) {
      const [a, b] = [rest[i - 1], rest[i]]
      expect(rank(a) < rank(b) || (rank(a) === rank(b) && a.name.localeCompare(b.name) <= 0)).toBe(
        true,
      )
    }
  })
})
