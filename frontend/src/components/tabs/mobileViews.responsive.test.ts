import { existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const indexCss = readFileSync(new URL('../../index.css', import.meta.url), 'utf8')
const investmentsCss = readFileSync(new URL('./investments/design.css', import.meta.url), 'utf8')
const investmentsSource = readFileSync(
  new URL('./investments/InvestmentsTab.tsx', import.meta.url),
  'utf8',
)
const planCss = readFileSync(new URL('./plan/plan.css', import.meta.url), 'utf8')
const commitmentsCss = readFileSync(
  new URL('./commitments/commitments.css', import.meta.url),
  'utf8',
)
const briefSource = readFileSync(new URL('./investments/Brief.tsx', import.meta.url), 'utf8')
const intelSource = readFileSync(new URL('./Intel.tsx', import.meta.url), 'utf8')
// The Intel cards' Ask Lucius lives in the header row both tabs share (IntelCardHead).
const intelBriefSource = readFileSync(
  new URL('../intel/IntelCardHead.tsx', import.meta.url),
  'utf8',
)
const commitmentRowsSource = readFileSync(
  new URL('./commitments/rows.tsx', import.meta.url),
  'utf8',
)
const commitmentActionsSource = readFileSync(
  new URL('./commitments/detailActions.tsx', import.meta.url),
  'utf8',
)
const acquireLotFormSource = readFileSync(
  new URL('../shared/AcquireLotForm.tsx', import.meta.url),
  'utf8',
)

describe('affected mobile views', () => {
  it('uses the concise Intel action label in the portfolio brief', () => {
    expect(briefSource).toMatch(/>\s*Intel(\{' '\})?\s*<span className="intel-brief-ask-spark">/)
    expect(briefSource).not.toContain('See more in Intel')
  })

  it('keeps the shared brief header and driver strip on compact single-line rails', () => {
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.intel-brief-head\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*\}/,
    )
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.intel-brief-movers\s*\{[^}]*overflow-x:\s*auto;[^}]*flex-wrap:\s*nowrap;[^}]*\}/,
    )
  })

  it('keeps the one-to-two line Intel brief on phones', () => {
    expect(indexCss).not.toMatch(/\.intel-brief-body\s*\{\s*display:\s*none/)
  })

  it('compacts only Ask Lucius while preserving the visible Intel label', () => {
    expect(intelBriefSource).toContain('className="intel-brief-ask intel-brief-ask--lucius"')
    expect(intelBriefSource).toContain('className="intel-brief-ask-label"')
    expect(briefSource).toContain('className="intel-brief-ask intel-brief-ask--intel"')
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.intel-brief-ask--lucius\s*\{[^}]*width:\s*44px;[^}]*\}/,
    )
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.intel-brief-ask--lucius \.intel-brief-ask-label\s*\{[^}]*display:\s*none;[^}]*\}/,
    )
  })

  it('shows an icon + label scan action on phones at the standard button height', () => {
    expect(intelSource).toContain('aria-label={scanning ?')
    expect(intelSource).toContain('className="intel-scan-button"')
    expect(intelSource).toContain("scanning ? 'Scanning…' : 'Scan'")
    // Compact horizontal padding only — no forced min-height/width, so it shares
    // the standard .ui-btn--sm height every other tab's header actions use.
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.intel-scan-button\s*\{[^}]*padding:\s*var\(--space-2\) var\(--space-3\);[^}]*\}/,
    )
    expect(indexCss).not.toMatch(/\.intel-scan-button\s*\{[^}]*min-height:\s*44px;[^}]*\}/)
    expect(indexCss).not.toContain('.intel-scan-button__label')
  })

  it('keeps every holdings control on one non-wrapping mobile row', () => {
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*nowrap;[^}]*width:\s*100%;[^}]*\}/,
    )
    // Search TAKES the leftover width — it is the control you type into.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-search-wrap\s*\{[^}]*flex:\s*1 1 auto;[^}]*\}/,
    )
    expect(investmentsSource).toContain('aria-label="Search holdings"')
    expect(investmentsSource).toContain("aria-label={groupBy === 'none' ? 'Grouping: No grouping'")
    expect(investmentsSource).toContain('aria-label="Card view"')
    expect(investmentsSource).toContain('aria-label="Table view"')
  })

  // Owner-reported, 2026-07-30: "the spacing here is not even among buttons", "why do the
  // search and All have a smaller gap than All and All accounts", "I see an arrow icon for
  // the grouping", "when I type I can't see anything".
  it('gives the phone toolbar even gaps and one uniform control height', () => {
    // ONE gap declaration — every gap is equal by construction. The uneven spacing was
    // never the gap: the search input's own padding made its border box 4px wider than
    // its 34px wrap, eating the space beside it. Proof: qa/measure-toolbar.mjs.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar\s*\{[^}]*gap:\s*10px;[^}]*\}/,
    )
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar > \*\s*\{\s*height:\s*34px;\s*\}/,
    )
    // .mk-density must declare border-box, or height:34px resolves to a 40px border box
    // (34 + 2px padding + 1px border ×2) and the row reads ragged.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-density\s*\{[^}]*box-sizing:\s*border-box;[^}]*height:\s*34px;[^}]*\}/,
    )
    expect(investmentsSource).toMatch(/variant="primary"\s+size="sm"\s+className="mk-add-holding"/)
    expect(investmentsSource).toMatch(/className="mk-add-holding"[\s\S]{0,120}>\s*Add\s*</)
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar > \.mk-add-holding\s*\{[^}]*box-sizing:\s*border-box;[^}]*height:\s*34px;[^}]*\}/,
    )
  })

  it('keeps asset header actions beside a wrapping title and gives its subtitle the full row', () => {
    expect(indexCss).toMatch(
      /\.asd-header\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto;[^}]*\}/,
    )
    expect(indexCss).toMatch(/\.asd-asset-subline-row\s*\{[^}]*grid-column:\s*1 \/ -1;[^}]*\}/)
  })

  // The blanket iOS anti-zoom rule uses !important, so NOTHING in a component stylesheet
  // can size a form control on a phone. That is why every font-size set on these controls
  // was silently ignored; the sizing has to live beside that rule.
  it('lets phone form controls inherit the 14px baseline instead of forcing a size', () => {
    expect(indexCss).toMatch(
      /input:not\(\[type='checkbox'\]\)[\s\S]{0,240}?\[contenteditable='true'\]\s*\{\s*font-size:\s*inherit;\s*\}/,
    )
    // The old blanket !important beat every component's own font-size, so a control that
    // asked to be smaller silently wasn't — with nothing in its CSS to explain why.
    expect(indexCss).not.toMatch(/font-size:\s*16px\s*!important/)
    // With the blanket override gone, a component CAN now size its own control — the
    // phone toolbar sizes both its search box and its selects to the 14px scale.
    const phoneSearch = investmentsCss.match(/\.mk-search \{[^}]*height: 100%[^}]*\}/)
    expect(phoneSearch?.[0]).toMatch(/font-size:\s*var\(--text-md\)/)
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar > \.mk-toolsel\s*\{[^}]*font-size:\s*var\(--text-md\);/,
    )
  })

  // The keyboard is the browser's job: `interactive-widget=resizes-content` shrinks the
  // layout viewport, and CSS takes the navigation away while a text field has focus. The JS
  // hook that used to infer all this from visualViewport deltas is gone — see
  // components/mobile/mobileKeyboard.contract.test.ts.
  it('takes the tab bar out of the layout whenever a text field has focus', () => {
    expect(indexCss).toContain('.mb-shell:has(textarea:focus) .mb-tabbar')
    expect(indexCss).toMatch(/:has\([^)]*:focus\)[\s\S]{0,400}?--mb-tabbar-h: 0px;/)
  })

  // performance.css was a byte-for-byte second copy of 38 design.css rules, and since
  // PerformanceTab is an Investments sub-tab both always loaded, performance.css last —
  // so its copy won every equal-specificity tie and silently reverted phone overrides.
  // It did that twice (the toolbar's grouping select, then the stat popovers) before
  // being deleted. Every class it held lives in design.css now.
  it('keeps the Performance sub-tab on the one Investments stylesheet', () => {
    expect(existsSync(new URL('./investments/performance.css', import.meta.url))).toBe(false)
    expect(
      readFileSync(new URL('./investments/PerformanceTab.tsx', import.meta.url), 'utf8'),
    ).toContain("import './design.css'")
    for (const cls of ['.mk-toolsel', '.mk-panel', '.stat-pop', '.rk-cell']) {
      expect(investmentsCss).toContain(`\n${cls} {`)
    }
  })

  // overflow-y:auto makes overflow-x compute to `auto`, so ONE stray box lets the whole
  // pane drag sideways. The Performance sub-tab did exactly that.
  it('never lets the phone content pane scroll horizontally', () => {
    expect(indexCss).toMatch(
      /\.app-shell\.mb-shell \.app-layout \{[^}]*overflow-y: auto;[^}]*overflow-x: hidden;/,
    )
  })

  it('gives the phone search the whole row while it is in use', () => {
    expect(investmentsSource).toMatch(/mk-toolbar--searching/)
    expect(investmentsSource).toContain('onFocus={() => setSearchFocus(true)}')
    // Every other control steps aside so the query stays fully readable while typing.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-toolbar--searching > \.mk-toolsel,\s*\.mk-toolbar--searching > \.mk-filter-wrap,\s*\.mk-toolbar--searching > \.mk-density\s*\{\s*display:\s*none;\s*\}/,
    )
  })

  // Owner, 2026-07-30: "merge types filter and all account filters into one filter only",
  // "just show as a filter icon".
  it('merges the phone type + account filters into one icon-triggered sheet', () => {
    expect(investmentsSource).toContain('<HoldingsFilterSheet')
    // The two separate phone selects are gone; the account select is wide-pane-only now.
    expect(investmentsSource).not.toContain('aria-label="Filter by asset class"')
    expect(investmentsSource).toContain('className="mk-pane-filter"')
    expect(investmentsSource).toMatch(
      /!isMobile && \(\s*<select\s*className="mk-toolsel mk-account-filter"/,
    )
    const sheet = readFileSync(
      new URL('./investments/HoldingsFilterSheet.tsx', import.meta.url),
      'utf8',
    )
    // Collapsed to an icon, it must still signal that something is filtered.
    expect(sheet).toContain('mk-filter-btn')
    expect(investmentsCss).toMatch(
      /\.mk-filter-btn\.is-active::after\s*\{[^}]*background:\s*var\(--accent\)/,
    )
    // Both groups live in the one sheet.
    expect(sheet).toMatch(/group\('Type',/)
    expect(sheet).toMatch(/group\('Account',/)
  })

  it('shows YTD-vs-benchmark once on a phone — in the hero, not as an extra line', () => {
    const mastheadSource = readFileSync(
      new URL('./investments/Masthead.tsx', import.meta.url),
      'utf8',
    )
    // Exactly one YTD block, and it lives in the hero row beside the total.
    expect(mastheadSource.match(/mk-sum-ytd"/g) ?? []).toHaveLength(1)
    expect(mastheadSource).not.toContain('mk-sum-meta--ytd')
    expect(mastheadSource).toContain('mk-sum-solo-top')
    // The brief is insights-only at every width, so it cannot duplicate the hero metric.
    expect(briefSource).not.toContain('intel-brief-stats')
  })

  // spec 2026-07-30-investments-mobile-density D4. The previous mobile card shared one flex
  // line across value/weight/P&L, which ellipsised them mid-number ("S$69,135.… 22.7% +S$1…").
  it('lays the mobile holding card out as identity | value with a price/P&L footer band', () => {
    // Identity absorbs the leftover width; the value track is `auto` so money never truncates.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-hold-inner\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto;[^}]*\}/,
    )
    // `display: contents` promotes the three .mk-col children to grid items of .mk-hold-inner
    // so value / weight / P&L can be placed in different cells.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-card-metrics\s*\{\s*display:\s*contents;\s*\}/,
    )
    // Every metric right-aligns to the same edge → figures line up across cards.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-card-metrics > \.mk-col\s*\{[^}]*text-align:\s*right;[^}]*\}/,
    )
    // Price+today and P&L share row 3. Spacing separates the data bands; a horizontal rule
    // would make the compact card look needlessly segmented.
    expect(investmentsCss).not.toMatch(/\.mk-hold-inner::after/)
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-card-metrics > \.mk-col:nth-child\(3\)\s*\{[^}]*grid-row:\s*3;[^}]*\}/,
    )
    // Portfolio weight is useful in the full layout, but is redundant visual noise on a phone.
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-card-metrics > \.mk-col:nth-child\(2\)\s*\{[^}]*display:\s*none;[^}]*\}/,
    )
    expect(investmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-hold-inner > \.mk-market-move\s*\{[^}]*grid-row:\s*3;[^}]*flex-direction:\s*row;[^}]*\}/,
    )
    // No ellipsis on money: only the company name and the price string may truncate.
    expect(investmentsCss).not.toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.mk-card-metrics \.mk-v[\s\S]*?text-overflow:\s*ellipsis/,
    )
  })

  it('gives the Add holding sheet a single-column mobile form with uniform controls', () => {
    expect(acquireLotFormSource).toContain('className="disposal-grid acquire-lot-grid"')
    expect(acquireLotFormSource).toContain('wrapperClassName="acquire-lot-datefield"')
    expect(acquireLotFormSource).toContain('className="disposal-deposit acquire-lot-funding"')
    expect(indexCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.acquire-lot-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);[^}]*\}/,
    )
    // Native date and select controls otherwise pick different intrinsic heights on iOS.
    expect(indexCss).toMatch(
      /\.acquire-lot-grid \.ui-input,\s*\.acquire-lot-grid \.ui-select,[\s\S]*?\{[^}]*height:\s*48px;[^}]*box-sizing:\s*border-box;[^}]*\}/,
    )
    expect(indexCss).toMatch(
      /\.acquire-lot-datefield\s*\{[^}]*height:\s*48px;[^}]*overflow:\s*hidden;[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*\}/,
    )
    expect(indexCss).toMatch(
      /\.acquire-lot-funding \.ui-select\s*\{[^}]*height:\s*48px;[^}]*box-sizing:\s*border-box;[^}]*\}/,
    )
    expect(indexCss).toMatch(
      /\.acquire-lot-funding\s*\{[^}]*font-size:\s*var\(--text-sm\);[^}]*line-height:\s*1\.4;/,
    )
    expect(indexCss).toMatch(
      /\.acquire-lot-funding \.disposal-deposit-hint\s*\{[^}]*font-size:\s*var\(--text-sm\);[^}]*line-height:\s*1\.4;/,
    )
    // The desktop three-column grid uses the same control height; native date chrome may not
    // determine a row's size on its own.
    expect(indexCss).toMatch(
      /\.acquire-lot-grid \.ui-input,\s*\.acquire-lot-grid \.ui-select,[\s\S]*?\{[^}]*height:\s*48px;[^}]*\}/,
    )
  })

  it('keeps FIRE settings inline, hides the subtitle, and compacts Ready to assign', () => {
    expect(planCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.pl-page > \.ui-page-header\s*\{[^}]*flex-direction:\s*row;[^}]*flex-wrap:\s*nowrap;[^}]*\}/,
    )
    expect(planCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.pl-page \.ui-page-header__subtitle\s*\{[^}]*display:\s*none;[^}]*\}/,
    )
    expect(planCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.pl-sum-row\s*\{[^}]*padding:\s*8px 12px 7px;[^}]*\}/,
    )
    expect(planCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.pl-sum-action\s*\{[^}]*min-height:\s*38px;[^}]*flex-wrap:\s*nowrap;[^}]*\}/,
    )
  })

  it('keeps commitment filters and the compact sort control on one mobile row', () => {
    expect(commitmentRowsSource).toContain('className="c2-select c2-select--sort"')
    expect(commitmentActionsSource).not.toContain('c2-select--sort')
    expect(commitmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.c2-sec-tools\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*width:\s*100%;[^}]*\}/,
    )
    // Still the compact icon-only control (text-indent hides the label so it stays on the
    // filter row) — but sized to --tap-min, not the original 36×28 box. 36px was under the
    // 44px floor the design system already tokenises, so the sort affordance was the hardest
    // thing on the screen to hit.
    expect(commitmentsCss).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?\.c2-select--sort\s*\{[^}]*width:\s*var\(--tap-min\);[^}]*text-indent:\s*-999px;[^}]*\}/,
    )
  })
})
