// @vitest-environment jsdom
//
// The directory preview page (spec §6, divergence D23).
//
// The page this replaces stated the publisher FOUR times (title, "Developed by", the trust
// panel's name slot, the footer's Author field), the endpoint twice, and headed a boxed
// section "VENDOR TRUST" — our internal name for the concept, on the one screen whose job is
// "should I even look at this". The shape asserted here is: attribution as a LINK, one muted
// sentence under it, chip rows, and a quiet Details footer that still discloses the endpoint.
//
// The three-state honesty survives the simplification — it moved out of a badge and into the
// sentence, and a test per state pins that the three stay distinguishable.
//
// The fixture's `id` is deliberately NOT a real catalog key: `previewEndpoint` reads the
// `MCP_INSTALL_PRESETS` table for a non-installed entry, and these tests are about layout,
// not about what Firecrawl's install command happens to be this week.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { DirectoryPreview } from './DirectoryPreview'
import type { DirectoryRow } from './catalog'

afterEach(cleanup)

function row(overrides: Partial<DirectoryRow> = {}): DirectoryRow {
  return {
    id: 'mcp-fixture',
    name: 'Firecrawl',
    icon: 'search',
    hue: 'var(--accent)',
    families: ['MCP'],
    sub: 'Scrape and crawl the web',
    desc: 'Turn any page into clean markdown for Lucius to read.',
    mcp: true,
    publisher: 'Firecrawl',
    verification: 'verified',
    categories: ['Browsing'],
    tools: ['scrape', 'crawl'],
    popular: false,
    installed: false,
    ...overrides,
  }
}

/** A connector WE build and operate — the case that was reading as third-party code. */
function nativeRow(overrides: Partial<DirectoryRow> = {}): Partial<DirectoryRow> {
  return {
    id: 'web_search:serper',
    name: 'Serper',
    mcp: false,
    families: ['Web'],
    categories: ['Web search', 'Browsing'],
    publisher: 'Serper',
    verification: 'unverified',
    tools: ['web_search', 'fetch_page'],
    ...overrides,
  }
}

function preview(overrides: Partial<DirectoryRow> = {}, props: { prompts?: string[] } = {}) {
  return render(
    <DirectoryPreview
      d={row(overrides)}
      prompts={props.prompts}
      onAdd={() => {}}
      onConfigure={() => {}}
    />,
  )
}

/** Every outbound link on the page. */
const links = (c: HTMLElement) => [...c.querySelectorAll('a.cn-extlink')] as HTMLAnchorElement[]
/** The one muted trust sentence. */
const sentence = (c: HTMLElement) => c.querySelector('.rd-help')?.textContent ?? ''

describe('attribution is a link when we have somewhere to point', () => {
  it('links the publisher name, preferring the docs page over the repo', () => {
    const { container } = preview({
      homepageUrl: 'https://firecrawl.dev',
      repoUrl: 'https://github.com/firecrawl/firecrawl-mcp-server',
    })

    const link = screen.getByText(/Developed by/).querySelector('a.cn-extlink') as HTMLAnchorElement
    expect(link).toBeTruthy()
    expect(link.textContent).toContain('Firecrawl')
    expect(link.getAttribute('href')).toBe('https://firecrawl.dev')
    // Every href here is a third party's, several of them self-reported by a server.
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(link.getAttribute('target')).toBe('_blank')
    // …and the external-link glyph rides with it.
    expect(link.querySelector('svg')).toBeTruthy()
    expect(links(container).length).toBeGreaterThan(0)
  })

  it('falls back to the repo when there is no docs page', () => {
    preview({ repoUrl: 'https://github.com/firecrawl/firecrawl-mcp-server' })
    const link = screen.getByText(/Developed by/).querySelector('a.cn-extlink') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://github.com/firecrawl/firecrawl-mcp-server')
  })

  it('renders the name as PLAIN TEXT when the entry has neither — never a fabricated link', () => {
    // The ordinary case for a hosted vendor server with no public repo: attribution is still
    // owed, a link is not available, and inventing one would be a phishing surface.
    const { container } = preview({ publisher: 'Hosted Vendor Inc.' })
    const developedBy = screen.getByText(/Developed by/)
    expect(developedBy.textContent).toContain('Hosted Vendor Inc.')
    expect(developedBy.querySelector('a')).toBeNull()
    expect(links(container)).toHaveLength(0)
  })

  it('claims no author at all when there is no publisher', () => {
    preview({ publisher: null, verification: 'unknown' })
    expect(screen.queryByText(/Developed by/)).toBeNull()
  })
})

describe('one muted trust sentence, and it differs by state', () => {
  it('verified — says we verified the publisher, and what that still does not cover', () => {
    const { container } = preview({ verification: 'verified' })
    expect(sentence(container)).toContain('We’ve verified this publisher')
    expect(sentence(container)).toContain('doesn’t control which tools a developer makes available')
  })

  it('unverified — says plainly that we have NOT verified the publisher', () => {
    const { container } = preview({ verification: 'unverified' })
    expect(sentence(container)).toContain('Only use connectors from developers you trust')
    expect(sentence(container)).toContain('We haven’t verified this publisher')
  })

  it('unknown — says we do not know who published it', () => {
    const { container } = preview({ publisher: null, verification: 'unknown' })
    expect(sentence(container)).toContain('We don’t know who published this one')
    expect(sentence(container)).toContain('can’t vouch for its code')
  })

  it('renders three DISTINCT sentences — the states never collapse into one', () => {
    const read = (v: DirectoryRow['verification']) => {
      const { container } = preview({ verification: v })
      const text = sentence(container)
      cleanup()
      return text
    }
    const all = [read('verified'), read('unverified'), read('unknown')]
    expect(new Set(all).size).toBe(3)
    expect(all.every((s) => s.length > 0)).toBe(true)
  })

  it('says nothing of the sort about a connector WE publish', () => {
    // We are not a third party to ourselves: attribution, no disclaimer.
    const { container } = preview({ publisher: 'Open Net Worth', verification: 'unverified' })
    expect(screen.getByText(/Developed by/).textContent).toContain('Open Net Worth')
    expect(container.querySelector('.rd-help')).toBeNull()
  })
})

describe('the trust sentence is MCP-only (spec §5, CNR-TRUST-NATIVE-ABSENT)', () => {
  it('never appears on a NATIVE connector — we author that integration', () => {
    // The reported defect: Serper, a Web provider we build and operate against an API we
    // chose, telling the reader "we haven't verified this publisher". There is no third party
    // in that loop to be warned about.
    const { container } = preview(nativeRow())
    expect(container.querySelector('.rd-help')).toBeNull()
    expect(container.textContent).not.toContain('Only use connectors from developers you trust')
    expect(container.textContent).not.toContain('haven’t verified this publisher')
  })

  it('always credits us, not the vendor — a native line names no third party (2026-08-21, simplified)', () => {
    // Serper is a real vendor, but WE wrote this integration — "Developed by Serper" would
    // claim their code runs here, which is false. The vendor's own name is dropped from THIS
    // line entirely (still visible via the card's name/logo/description and the Details
    // footer's "Vendor" field) rather than named alongside ours.
    preview(nativeRow())
    expect(screen.getByText(/Developed by/).textContent).toContain('Open Net Worth')
    expect(screen.getByText(/Developed by/).textContent).not.toContain('Serper')
  })

  it("links to our own site, not the vendor's — even when we have a verified vendor URL", () => {
    preview(nativeRow({ homepageUrl: 'https://serper.dev' }))
    const link = screen.getByText(/Developed by/).querySelector('a.cn-extlink') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://opennetworth.ai')
  })

  it('keeps the sentence on every MCP state — the split is by lane, not by verification', () => {
    for (const v of ['verified', 'unverified', 'unknown'] as const) {
      const { container } = preview({ mcp: true, verification: v })
      expect(container.querySelector('.rd-help'), v).toBeTruthy()
      cleanup()
    }
  })

  it('shows nothing at all for a native connector with no publisher', () => {
    // No attribution to make and no sentence to make it with — the block disappears rather
    // than rendering an empty div.
    const { container } = preview(nativeRow({ publisher: null, verification: 'unknown' }))
    expect(screen.queryByText(/Developed by/)).toBeNull()
    expect(container.querySelector('.rd-help')).toBeNull()
  })
})

describe('the boxed trust panel is gone from this page', () => {
  it('never says "vendor trust" — that is our word for it, not the reader’s', () => {
    const { container } = preview({ verification: 'unverified' })
    expect(container.textContent?.toLowerCase()).not.toContain('vendor trust')
  })

  it('drops the panel badge and the panel note, which the page now answers once each', () => {
    const { container } = preview({ verification: 'unverified' })
    expect(screen.queryByText('Unverified publisher')).toBeNull()
    expect(screen.queryByText('Verified publisher')).toBeNull()
    expect(screen.queryByText('Unverified publisher')).toBeNull()
    expect(container.textContent).not.toContain('third-party MCP server')
  })

  it('names the publisher three times, not four', () => {
    // Title, attribution line, Details/Author. The panel's name slot was the fourth.
    const { container } = preview({ homepageUrl: 'https://firecrawl.dev' })
    expect((container.textContent ?? '').match(/Firecrawl/g)).toHaveLength(3)
  })
})

describe('Details — the endpoint kept its place, in a footer rather than a panel', () => {
  it('shows the Author and the Connector URL with a copy button', () => {
    const { container } = preview({
      installed: true,
      url: 'https://mcp.firecrawl.dev/v1',
      homepageUrl: 'https://firecrawl.dev',
    })

    expect(screen.getByText('Details')).toBeTruthy()
    expect(screen.getByText('Author')).toBeTruthy()
    expect(screen.getByText('Connector URL')).toBeTruthy()
    expect(container.querySelector('code.rd-code')?.textContent).toBe(
      'https://mcp.firecrawl.dev/v1',
    )
    expect(screen.getByLabelText('Copy')).toBeTruthy()
  })

  it('calls a local command a Command — `npx -y firecrawl-mcp` is not a URL', () => {
    preview({ installed: true, url: 'npx -y firecrawl-mcp' })
    expect(screen.getByText('Command')).toBeTruthy()
    expect(screen.queryByText('Connector URL')).toBeNull()
  })

  it('copies the endpoint to the clipboard', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    preview({ installed: true, url: 'https://mcp.firecrawl.dev/v1' })
    fireEvent.click(screen.getByLabelText('Copy'))

    expect(writeText).toHaveBeenCalledWith('https://mcp.firecrawl.dev/v1')
    expect(screen.getByLabelText('Copied')).toBeTruthy()
  })

  it('lists only REAL links under More info', () => {
    const { container } = preview({
      homepageUrl: 'https://firecrawl.dev',
      repoUrl: 'https://github.com/firecrawl/firecrawl-mcp-server',
    })
    expect(screen.getByText('More info')).toBeTruthy()
    expect(screen.getByText('Documentation')).toBeTruthy()
    expect(screen.getByText('Source repository')).toBeTruthy()
    // D11: never a dead `href="#"` placeholder for Support / Privacy policy.
    expect(container.querySelector('a[href="#"]')).toBeNull()
    expect(screen.queryByText('Support')).toBeNull()
    expect(screen.queryByText('Privacy policy')).toBeNull()
  })

  it('omits More info entirely when an entry has no links', () => {
    preview({ installed: true, url: 'https://mcp.firecrawl.dev/v1' })
    expect(screen.queryByText('More info')).toBeNull()
    expect(screen.getByText('Details')).toBeTruthy()
  })

  it('omits the whole footer, divider included, when there is nothing to put in it', () => {
    const { container } = preview({ publisher: null, verification: 'unknown', mcp: false })
    expect(screen.queryByText('Details')).toBeNull()
    expect(container.querySelector('hr.rd-hr')).toBeNull()
  })
})

describe('the chip rows survive the simplification', () => {
  it('counts tools, prompts and categories', () => {
    preview({}, { prompts: ['Scrape this page', 'Crawl the docs'] })
    expect(screen.getByText('Tools').textContent).toContain('2')
    expect(screen.getByText('Prompt suggestions').textContent).toContain('2')
    expect(screen.getByText('Categories').textContent).toContain('1')
  })

  it('drops a row rather than rendering an empty one', () => {
    preview({ tools: [], categories: [] })
    expect(screen.queryByText('Tools')).toBeNull()
    expect(screen.queryByText('Prompt suggestions')).toBeNull()
    expect(screen.queryByText('Categories')).toBeNull()
  })
})

describe('tool chips show the raw registry name, not a human label', () => {
  const chip = (c: HTMLElement, text: string) =>
    [...c.querySelectorAll('.cn-tagchip')].find((n) => n.textContent === text) as HTMLElement

  it('renders the wire identifier for a known tool, unchanged', () => {
    // The chip is a summary of the same tools the permission ladder governs, keyed by this
    // exact identifier — humanizing it here would show one name on this page and another on
    // the ladder for the same tool. User-friendly phrasing belongs in the prose description.
    const { container } = preview(nativeRow())
    expect(chip(container, 'web_search')).toBeTruthy()
    expect(chip(container, 'fetch_page')).toBeTruthy()
    // Scoped to the Tools row: the Categories row legitimately carries a "Web search" tag.
    const tools = [...container.querySelectorAll('.cn-sublabel')].find((n) =>
      n.textContent?.startsWith('Tools'),
    )!.parentElement!
    expect(tools.textContent).not.toContain('Web search')
  })

  it('renders a tool DISCOVERED on a server we have never seen, unchanged', () => {
    const { container } = preview({ tools: ['weird_vendor_thing'] })
    expect(chip(container, 'weird_vendor_thing')).toBeTruthy()
  })

  it('adds no title — the visible text already IS the identifier', () => {
    const { container } = preview(nativeRow())
    expect(chip(container, 'web_search').getAttribute('title')).toBeNull()
  })

  it('leaves prompts and categories alone — they are already prose', () => {
    const { container } = preview(nativeRow(), { prompts: ['Look up today’s SGD/USD rate'] })
    expect(chip(container, 'Look up today’s SGD/USD rate').getAttribute('title')).toBeNull()
  })
})

describe('Categories describe the connector, never echo its lane', () => {
  it('shows the entry’s topical tags', () => {
    const { container } = preview(nativeRow())
    expect(screen.getByText('Categories').textContent).toContain('2')
    expect([...container.querySelectorAll('.cn-tagchip')].map((n) => n.textContent)).toContain(
      'Web search',
    )
  })

  it('never prints the family — the lane the reader is already browsing', () => {
    // The reported defect: Stripe's landing page read `CATEGORIES 1 → MCP`.
    const { container } = preview({
      families: ['MCP'],
      categories: ['Payments'],
      tools: [],
    })
    const chips = [...container.querySelectorAll('.cn-tagchip')].map((n) => n.textContent)
    expect(chips).toEqual(['Payments'])
    expect(chips).not.toContain('MCP')
  })

  it('drops the row for an entry with nothing honest to say', () => {
    // A user's own MCP server: no catalog entry, so no topics.
    preview({ categories: [] })
    expect(screen.queryByText('Categories')).toBeNull()
  })
})

describe('the header — a large tile, the title centred against it, one heavy action', () => {
  it('renders the identity block with the large tile', () => {
    const { container } = preview()
    const head = container.querySelector('.cn-idhead') as HTMLElement
    expect(head).toBeTruthy()
    expect(head.querySelector('.cn-tile.lg')).toBeTruthy()
    expect(head.querySelector('.cn-idhead-name')?.textContent).toContain('Firecrawl')
    expect(head.querySelector('.cn-idhead-sub')?.textContent).toBe('Scrape and crawl the web')
  })

  it('sizes the primary action up — this is what the page exists to drive', () => {
    const { container } = preview()
    const add = screen.getByRole('button', { name: /^Add/ })
    expect(add.className).toContain('rd-btn primary lg')
    expect(container.querySelector('.cn-idhead-actions')?.contains(add)).toBe(true)
  })

  it('gives an installed entry the same weight of action', () => {
    preview({ installed: true })
    expect(screen.getByRole('button', { name: 'Open settings' }).className).toContain('lg')
  })

  it('draws the real brand mark when the connector has one', () => {
    const { container } = preview({ logo: '/connector-logos/firecrawl.png' })
    const img = container.querySelector('.cn-tile-logo') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/connector-logos/firecrawl.png')
    // A mark sits on its own light plate, not on the category tint it replaces.
    expect(container.querySelector('.cn-tile')?.className).toContain('plate')
    // `alt=""` — the name is already beside it, so announcing the mark repeats it.
    expect(img.getAttribute('alt')).toBe('')
  })

  it('falls back to the tinted category glyph when there is no mark', () => {
    // The ordinary case: several connectors have none, and a user's own MCP server never will.
    const { container } = preview()
    expect(container.querySelector('.cn-tile-logo')).toBeNull()
    expect(container.querySelector('.cn-tile')?.className).not.toContain('plate')
    expect(container.querySelector('.cn-tile svg')).toBeTruthy()
  })

  it('falls back DELIBERATELY when the file fails to load, not to a broken image', () => {
    const { container } = preview({ logo: '/connector-logos/gone.png' })
    fireEvent.error(container.querySelector('.cn-tile-logo') as HTMLImageElement)
    expect(container.querySelector('.cn-tile-logo')).toBeNull()
    expect(container.querySelector('.cn-tile svg')).toBeTruthy()
    expect(container.querySelector('.cn-tile')?.className).not.toContain('plate')
  })
})
