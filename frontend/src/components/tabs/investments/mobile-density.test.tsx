// @vitest-environment jsdom
//
// Investments tab — phone information density.
// Spec: docs/superpowers/specs/2026-07-30-investments-mobile-density-design.md
// Acceptance: docs/qa/acceptance/investments-mobile.md
//
// The owner's complaint was that the tab front-loads four dense blocks before the first
// holding, two of which repeat the SAME number ("today's change") three times over. These
// tests pin the de-duplication and the collapses so a later refactor can't quietly restore
// the wall of text.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, within, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Masthead } from './Masthead'
import { Brief } from './Brief'
import { resolveInvestmentActiveTab, useCompactInvestmentsPane } from './useCompactInvestmentsPane'
import type { DerivedBook } from './useInvestmentsBook'
import type { InvestmentSignal } from '../../../types/investmentSignals'
import { fBase } from '../../../utils/format'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// The Brief holds its footprint until drivers settle. Drivers and the masthead's YTD stat
// both read through useQueryCache, so stubbing that seam lands the UI in a stable loaded state.
// clearQueryCache stays real: vitest.setup calls it before every test.
vi.mock('../../../state/queryCache', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../state/queryCache')>()
  return {
    ...actual,
    useQueryCache: () => ({
      data: null,
      initialLoading: false,
      loading: false,
      error: null,
      refetch: () => {},
    }),
  }
})

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see vitest.setup.ts).
 *  `globalThis.matchMedia` is a getter-only accessor there, so the property must be redefined
 *  rather than assigned. Returns a restore fn. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

/** The `data-ctx-fact` names Lucius would read off this subtree, in document order. */
function factsIn(root: HTMLElement): string[] {
  return [...root.querySelectorAll('[data-ctx-fact]')].map(
    (el) => el.getAttribute('data-ctx-fact')!,
  )
}

function makeBook(over: Partial<DerivedBook> = {}): DerivedBook {
  return {
    sections: [],
    all: [],
    grand: 100000.0,
    empty: false,
    todayPL: 256.59,
    todayPct: 0.09,
    // Server-computed on the real payload; fixed here so the density assertions stay about
    // layout, not arithmetic.
    totalPL: 10000.0,
    totalPLPct: 12.6,
    pctOfPortfolio: 72.9,
    portfolioTotal: 250000.0,
    movers: [],
    risk: [],
    alloc: [],
    thresholdPp: 5,
    topName: null,
    loading: false,
    ...over,
  }
}

// ── D1 — masthead collapses to hero + one meta line ──────────────────────────

describe('Masthead on a phone', () => {
  const props = {
    portfolioTotal: 250000.0,
    totalDay: 256.59,
    totalPL: 2000.0,
  }

  it('renders ONE cell, not the 2x2 grid', () => {
    const restore = mockMobile(true)
    const { container } = render(<Masthead d={makeBook()} {...props} />)
    expect(container.querySelectorAll('.mk-sum-cell')).toHaveLength(1)
    expect(container.querySelector('.mk-sum--solo')).not.toBeNull()
    expect(container.querySelector('.mk-sum--4')).toBeNull()
    restore()
  })

  it("drops the duplicated TODAY cell — today's change appears exactly once", () => {
    const restore = mockMobile(true)
    const { container } = render(<Masthead d={makeBook()} {...props} />)
    const hits = container.querySelectorAll('[data-ctx-fact="Today"]')
    expect(hits).toHaveLength(1)
    // ...and it is the hero's own sub-line, not a separate labelled cell.
    expect(container.querySelector('.mk-sum-sub [data-ctx-fact="Today"]')).not.toBeNull()
    expect(screen.queryByText('Today')).toBeNull()
    restore()
  })

  it('keeps unrealized P&L and % of net worth on the meta line, still ctx-reportable', () => {
    const restore = mockMobile(true)
    const { container } = render(<Masthead d={makeBook()} {...props} />)
    const meta = container.querySelector('.mk-sum-meta')
    expect(meta).not.toBeNull()
    expect(meta?.textContent).toMatch(/72\.9% of net worth/)
    // Enumerate rather than select: jsdom's selector engine can't match an attribute VALUE
    // containing "&", so `[data-ctx-fact="Unrealized P&L"]` silently returns null there.
    expect(factsIn(meta as HTMLElement)).toEqual(['Unrealized P&L', '% of net worth'])
    restore()
  })

  it('omits the meta line entirely when neither fact has a value', () => {
    const restore = mockMobile(true)
    const { container } = render(
      <Masthead
        d={makeBook({ pctOfPortfolio: 0, totalPLPct: 0, portfolioTotal: 0 })}
        totalDay={0}
        totalPL={0}
      />,
    )
    expect(container.querySelector('.mk-sum-meta')).toBeNull()
    restore()
  })

  it('desktop uses four unique cells, with % of net worth under Market value', () => {
    const restore = mockMobile(false)
    const { container } = render(<Masthead d={makeBook()} {...props} />)
    expect(container.querySelectorAll('.mk-sum-cell')).toHaveLength(4)
    expect(container.querySelector('.mk-sum--4')).not.toBeNull()
    expect(container.querySelectorAll('[data-ctx-fact="Today"]')).toHaveLength(1)
    const heroSub = container.querySelector('.mk-sum-cell:first-child .mk-sum-sub')
    expect(heroSub?.textContent).toBe('72.9% of net worth')
    expect(factsIn(container)).toEqual([
      'Market value',
      '% of net worth',
      'Today',
      'YTD return',
      'Unrealized P&L',
    ])
    expect(container.textContent).not.toContain(fBase(250000.0))
    restore()
  })
})

// ── D2 — brief trims in place ────────────────────────────────────────────────

function makeSignal(id: string, narrative: string): InvestmentSignal {
  return { id, tag: 'CONCENTRATION', tone: 'warn', narrative } as InvestmentSignal
}

const BRIEF_SIGNALS = [
  makeSignal('s1', 'ACME is 31% of investments, 11 percentage points above the 20% guideline.'),
  makeSignal('s2', '3 asset classes off target. equity is 21.6 percentage points overweight.'),
  makeSignal('s3', 'AAPL goes ex-div in 11 days — S$1.15 projected.'),
]

function briefElement() {
  return <Brief d={makeBook()} signals={BRIEF_SIGNALS} decisionsCount={2} loading={false} />
}

function renderBrief() {
  return render(briefElement())
}

describe('Portfolio brief on a phone', () => {
  it('drops its own TODAY stat — the masthead already carries that number', () => {
    const restore = mockMobile(true)
    const { container } = renderBrief()
    expect(within(container).queryByText('TODAY')).toBeNull()
    restore()
  })

  it('renders no stats band', () => {
    const restore = mockMobile(true)
    const { container } = renderBrief()
    expect(container.querySelector('.intel-brief-stats')).toBeNull()
    restore()
  })

  it('folds the secondary bullets behind a disclosure, closed by default', () => {
    const restore = mockMobile(true)
    const { container } = renderBrief()
    // Lead narrative stays visible — it is the reason the card exists.
    expect(container.textContent).toMatch(/ACME is 31% of investments/)
    expect(container.querySelector('.brief-more')).toBeNull()

    const toggle = within(container).getByRole('button', { name: /2 more/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(toggle)
    expect(container.querySelector('.brief-more')).not.toBeNull()
    expect(within(container).getByText(/equity is 21\.6 percentage points overweight/)).toBeTruthy()
    expect(
      within(container)
        .getByRole('button', { name: /show less/i })
        .getAttribute('aria-expanded'),
    ).toBe('true')
    restore()
  })

  it('loads a skeleton shaped like the phone card, not the desktop one', () => {
    const restore = mockMobile(true)
    const { container } = render(<Brief d={makeBook()} signals={[]} decisionsCount={0} loading />)
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    // The real phone card has no stats band, so the placeholder must not reserve one —
    // otherwise the swap-in collapses that height and the page jumps.
    expect(container.querySelector('.intel-brief-stats')).toBeNull()
    expect(container.querySelector('.intel-brief-stat-div')).toBeNull()
    restore()
  })

  it('keeps the desktop skeleton insights-only too', () => {
    const restore = mockMobile(false)
    const { container } = render(<Brief d={makeBook()} signals={[]} decisionsCount={0} loading />)
    expect(container.querySelector('.intel-brief-stats')).toBeNull()
    expect(container.querySelector('.intel-brief-stat-div')).toBeNull()
    restore()
  })

  it('desktop has no metric stats and shows the bullets unfolded', () => {
    const restore = mockMobile(false)
    const { container } = renderBrief()
    expect(within(container).queryByText('TODAY')).toBeNull()
    expect(within(container).queryByText(/YTD/)).toBeNull()
    expect(container.querySelector('.intel-brief-stat-div')).toBeNull()
    expect(container.querySelector('.brief-more')).not.toBeNull()
    expect(within(container).queryByRole('button', { name: /2 more/ })).toBeNull()
    restore()
  })

  it('uses the same compact brief when Lucius narrows the Investments pane', () => {
    const restoreMobile = mockMobile(false)
    const originalResizeObserver = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      const width = this.classList.contains('intel-tab') ? 700 : 0
      return {
        width,
        height: 0,
        top: 0,
        right: width,
        bottom: 0,
        left: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }
    })

    try {
      const { container } = render(<div className="intel-tab">{briefElement()}</div>)
      expect(within(container).queryByText('TODAY')).toBeNull()
      expect(container.querySelector('.brief-more')).toBeNull()
      expect(within(container).getByRole('button', { name: /2 more/ })).toBeTruthy()
    } finally {
      globalThis.ResizeObserver = originalResizeObserver
      restoreMobile()
    }
  })
})

// ── D3 — toolbar: class dropdown in, chip row and density toggle out ─────────

// `import.meta.url` is an http: URL under the jsdom environment — resolve from __dirname
// instead (same approach as investments-foundation.test.tsx).
const tabSource = readFileSync(resolve(__dirname, 'InvestmentsTab.tsx'), 'utf8')
const paneHookSource = readFileSync(resolve(__dirname, 'useCompactInvestmentsPane.ts'), 'utf8')
const designSource = readFileSync(resolve(__dirname, 'design.css'), 'utf8')
const indexSource = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8')

describe('initial Investments loading', () => {
  it('uses one full portfolio shell regardless of the saved subtab', () => {
    expect(tabSource).toContain('const initialPortfolioLoading = d.empty && (loading || d.loading)')
    expect(tabSource).not.toContain('const initialHoldingsLoading =')
  })
})

describe('holdings toolbar on a phone', () => {
  it('keeps the class pills on a phone and leaves the filter sheet account-only', () => {
    expect(tabSource).toContain('<HoldingsFilterSheet')
    // Neither phone select survives — they ate the row that search needs.
    expect(tabSource).not.toContain('aria-label="Filter by asset class"')
    // Asset class is pills at every width, so the sheet is not handed the class options.
    expect(tabSource).not.toMatch(/classOptions=/)
    expect(tabSource).toMatch(/<span className="mk-chip-n">\{counts\[ff\.id\] \?\? 0\}<\/span>/)
  })

  it('renders the class pills at every width, pinned with search in one header', () => {
    // No !isMobile gate any more — the pills are the phone's class filter too.
    expect(tabSource).not.toMatch(/\{!isMobile && \(\s*<div className="intel-feed-bar">/)
    expect(tabSource).toMatch(
      /<div className="mk-feed-head">[\s\S]*className=\{`mk-toolbar[\s\S]*<div className="intel-feed-bar">/,
    )
    expect(designSource).toMatch(
      /@media \(max-width: 640px\) \{\s*\.mk-feed-head \{\s*position: sticky;/,
    )
  })

  it('uses the account-only filter button when Lucius narrows the pane, keeping the pills', () => {
    expect(tabSource).toContain('className="mk-pane-filter"')
    expect(tabSource).toContain("presentation={isMobile ? 'sheet' : 'popover'}")
    expect(tabSource).toContain('className="mk-toolsel mk-account-filter"')
    const narrow = designSource.match(
      /@container holdings-feed \(max-width: 860px\) \{[\s\S]*?\n\}/,
    )?.[0]
    expect(narrow).toBeTruthy()
    expect(narrow).toMatch(/\.mk-pane-filter \{\s*display: flex;/)
    expect(narrow).toMatch(/\.mk-account-filter \{\s*display: none;/)
    expect(narrow).not.toMatch(/intel-feed-bar|mk-chips[^-]/)
  })

  it('compacts grouping to the library Grid2X2 icon in a narrow pane without removing its native choices', () => {
    expect(tabSource).toContain('<Grid2X2 size={17}')
    expect(tabSource).toContain('<option value="account">Group: Account</option>')
    expect(designSource).toMatch(
      /@container holdings-feed \(max-width: 860px\)[\s\S]*\.mk-group-wrap \{[\s\S]*width: 34px;[\s\S]*\.mk-group-wrap > \.mk-toolsel \{[^}]*opacity: 0;/,
    )
  })

  it('keeps card/table switchable on a phone', () => {
    // Reversed on 2026-07-30 ("i want the card vs table back") — the toggle renders at
    // every width and the stored preference is honoured as-is.
    expect(tabSource).not.toMatch(/\{!isMobile && \(\s*<div className="mk-density"/)
    expect(tabSource).toContain("density === 'cards' ? (")
    expect(tabSource).not.toContain('effDensity')
  })

  it('drops grouping on a phone without clobbering the saved preference', () => {
    expect(tabSource).toMatch(/const effGroupBy: GroupByKey = isMobile \? 'none' : groupBy/)
    // Both consumers must read the effective value, or a saved desktop grouping would
    // still slice the phone list into headed sections.
    expect(tabSource).toContain('shouldMergeAcrossAccounts(effGroupBy, acct)')
    expect(tabSource).toContain('buildGroups(filteredRows, effGroupBy')
    // setGroupBy is the only localStorage writer — the gate must never call it.
    expect(tabSource).not.toMatch(/isMobile[^\n]*setGroupBy/)
  })
})

// ── Cockpit moves to its own sub-tab whenever it cannot stay side-by-side ──
//
// Stacked under the feed it sat below every holding — a 30-row scroll to reach. Lucius can
// create the same narrow pane inside a desktop viewport, so viewport-only state is insufficient.

describe('Analysis sub-tab', () => {
  it('switches at the actual pane boundary when Lucius opens and closes', () => {
    let paneWidth = 861
    let resize: ResizeObserverCallback | null = null
    const originalResizeObserver = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    const rect = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        const width = this.classList.contains('intel-tab') ? paneWidth : 0
        return {
          width,
          height: 0,
          top: 0,
          right: width,
          bottom: 0,
          left: 0,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        }
      })

    function Probe() {
      const { paneAnchorRef, cockpitInSubtab } = useCompactInvestmentsPane(false)
      return (
        <div className="intel-tab">
          <div ref={paneAnchorRef} data-testid="pane-mode">
            {cockpitInSubtab ? 'subtab' : 'side-by-side'}
          </div>
        </div>
      )
    }

    try {
      render(<Probe />)
      expect(screen.getByTestId('pane-mode').textContent).toBe('side-by-side')

      paneWidth = 860
      act(() => resize?.([], {} as ResizeObserver))
      expect(screen.getByTestId('pane-mode').textContent).toBe('subtab')

      paneWidth = 861
      act(() => resize?.([], {} as ResizeObserver))
      expect(screen.getByTestId('pane-mode').textContent).toBe('side-by-side')
    } finally {
      rect.mockRestore()
      globalThis.ResizeObserver = originalResizeObserver
    }
  })

  it('starts measuring once the anchor mounts after a loading render (no stale first guess)', () => {
    const originalResizeObserver = globalThis.ResizeObserver
    let resize: ResizeObserverCallback | undefined
    globalThis.ResizeObserver = class {
      constructor(cb: ResizeObserverCallback) {
        resize = cb
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    let paneWidth = 1400
    const rect = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        const width = this.classList.contains('intel-tab') ? paneWidth : 0
        return {
          width,
          height: 0,
          top: 0,
          right: width,
          bottom: 0,
          left: 0,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        }
      })

    // Starts as a phone guess (isMobile at mount) with NO anchor — the loading skeleton.
    function Probe({ loaded }: { loaded: boolean }) {
      const { paneAnchorRef, cockpitInSubtab } = useCompactInvestmentsPane(false)
      return (
        <div className="intel-tab">
          {loaded ? (
            <div ref={paneAnchorRef} data-testid="pane-mode">
              {cockpitInSubtab ? 'subtab' : 'side-by-side'}
            </div>
          ) : (
            <div>loading</div>
          )}
        </div>
      )
    }

    try {
      paneWidth = 700
      const { rerender } = render(<Probe loaded={false} />)
      rerender(<Probe loaded />)
      expect(screen.getByTestId('pane-mode').textContent).toBe('subtab')

      paneWidth = 1400
      act(() => resize?.([], {} as ResizeObserver))
      expect(screen.getByTestId('pane-mode').textContent).toBe('side-by-side')
    } finally {
      rect.mockRestore()
      globalThis.ResizeObserver = originalResizeObserver
    }
  })

  it('uses the same 860px pane boundary as the side-by-side grid', () => {
    expect(paneHookSource).toMatch(/const COCKPIT_SUBTAB_MAX = 860/)
    expect(tabSource).toMatch(/useCompactInvestmentsPane\(isMobile\)/)
    expect(indexSource).toMatch(
      /@container \(max-width: 860px\)[\s\S]*\.intel-layout \{[\s\S]*grid-template-columns: minmax\(0, 1fr\)/,
    )
  })

  it('exists on a phone or narrow desktop pane and holds the cockpit cards', () => {
    expect(tabSource).toMatch(/\{ value: 'analysis', label: 'Analysis' \}/)
    // Filtered out only when the cockpit has enough room to remain a true side column.
    expect(tabSource).toMatch(
      /const segments = cockpitInSubtab \? SEGMENTS : SEGMENTS\.filter\(\(s\) => s\.value !== 'analysis'\)/,
    )
    expect(tabSource).toMatch(/activeTab === 'analysis'[\s\S]{0,80}inv-analysis/)
  })

  it('renders the cockpit from ONE definition so the two placements cannot drift', () => {
    // A second copy of Allocation/SectorGeo/Risk/Rebalance would rot independently.
    expect(tabSource.match(/<Allocation\b/g) ?? []).toHaveLength(1)
    expect(tabSource.match(/<SectorGeo\b/g) ?? []).toHaveLength(1)
    expect(tabSource.match(/<Rebalance\b/g) ?? []).toHaveLength(1)
    expect(tabSource).toMatch(
      /\{!cockpitInSubtab && <div className="intel-side">\{cockpit\}<\/div>\}/,
    )
  })

  it('never strands a widened window on a tab that does not exist there', () => {
    // A saved or deep-linked 'analysis' falls back once the pane grows or while the book is
    // loading/empty, so the user sees Holdings instead of a blank 60vh panel.
    expect(tabSource).toMatch(
      /const activeTab: InvTab = resolveInvestmentActiveTab\(invtab, cockpitInSubtab, d\.empty\)/,
    )
    // Every panel and the tab strip must read activeTab, not the raw stored value.
    expect(tabSource).not.toMatch(/\{invtab === '/)
    expect(tabSource).toMatch(/value=\{activeTab\}/)
  })

  it('shows Holdings instead of a blank Analysis panel while the book is loading or empty', () => {
    expect(resolveInvestmentActiveTab('analysis', true, true)).toBe('holdings')
    expect(resolveInvestmentActiveTab('analysis', true, false)).toBe('analysis')
    expect(resolveInvestmentActiveTab('analysis', false, false)).toBe('holdings')
  })
})

// ── Card view carries the per-share price on desktop (owner, 2026-07-30) ──

describe('holdings card price column', () => {
  const cardsSource = readFileSync(resolve(__dirname, 'FeedCards.tsx'), 'utf8')
  const css = readFileSync(resolve(__dirname, 'design.css'), 'utf8')

  it('gives price its own labelled column rather than a line under TODAY', () => {
    expect(cardsSource).toMatch(/\{ k: 'price', label: 'Price' \}/)
    expect(cardsSource).toContain('mk-price-col')
    // Six headers, six grid tracks — a mismatch silently shears the whole row.
    const start = cardsSource.indexOf('const HEAD_COLS')
    const heads = cardsSource.slice(start, cardsSource.indexOf('\n]', start))
    expect(heads.match(/\{ k: '/g) ?? []).toHaveLength(6)
    const tracks = css.match(/\.mk-cardhead \{[^}]*grid-template-columns:([^;]+);/)
    // Split on top-level whitespace only — minmax(140px, 1.5fr) is ONE track.
    expect(tracks?.[1]?.trim().split(/\s+(?![^(]*\))/)).toHaveLength(6)
  })

  it('quotes the price in the instrument currency, not the base', () => {
    // fUnitPrice(r.price, r.ccy) — a converted per-share figure is not a real quote, and
    // fCurrency's fixed 2-decimal rounding would show a sub-cent coin as a misleading "$0.00".
    expect(cardsSource).toMatch(
      /mk-price-col[\s\S]{0,140}fUnitPrice\(r\.price, r\.ccy(?:, priceDigits\(r\.cls\))?\)/,
    )
  })

  it('hides the desktop price column on a phone, where the footer band shows it', () => {
    expect(css).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-price-col\s*\{\s*display:\s*none;\s*\}/,
    )
    // It must NOT be one of .mk-card-metrics' three .mk-col children, or the phone
    // nth-child placement of value / weight / P&L shifts by one. Assert it sits AFTER
    // the .mk-card-metrics element closes.
    const metricsEnd = cardsSource.indexOf('mk-market-move')
    const metricsStart = cardsSource.indexOf('className="mk-card-metrics"')
    const priceIdx = cardsSource.indexOf('mk-col mk-price-col')
    expect(priceIdx).toBeGreaterThan(metricsStart)
    expect(priceIdx).toBeLessThan(metricsEnd)
    // …and that .mk-card-metrics still has exactly three .mk-col children.
    const metricsBlock = cardsSource.slice(metricsStart, priceIdx)
    expect(metricsBlock.match(/className="mk-col"/g) ?? []).toHaveLength(3)
  })
})
