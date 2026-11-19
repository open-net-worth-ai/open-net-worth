import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')

function t_rule(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return source.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('Transaction Review mode deck and actions', () => {
  it('paints Skip as a tappable action, not a muted ghost, on desktop and on the white mobile pill', () => {
    const rules = [
      ...css.matchAll(/\.acct-review-actions \.acct-review-skip[^{]*\{([^}]*)\}/g),
    ].map((match) => match[1] ?? '')
    expect(rules.length).toBeGreaterThanOrEqual(2)
    expect(rules[0]).toMatch(/color:\s*var\(--ink\)/)
    expect(rules.some((body) => /color:\s*rgb\(32 33 36\)/.test(body))).toBe(true)
  })

  it('replaces the deck labels the rear card covered with an on-card swipe badge and wash', () => {
    expect(css).not.toMatch(/\.acct-review-swipe-labels/)
    expect(css).toMatch(/\.acct-review-swipe-badge\.is-approve/)
    expect(css).toMatch(/\.acct-review-swipe-badge\.is-skip/)
    expect(t_rule(css, '.acct-review-swipe-wash')).toMatch(/border-radius:\s*inherit/)
    expect(t_rule(css, '.acct-review-swipe-wash.is-skip')).toMatch(/background:/)
  })

  it('gives the desktop decisions a real button hit area and pill shape', () => {
    const body = t_rule(css, '.acct-review-actions .ui-btn')
    expect(body).toMatch(/min-height:\s*40px/)
    expect(body).toMatch(/border-radius:\s*var\(--radius-pill\)/)
    expect(t_rule(css, '.acct-review-actions .ui-btn--ghost')).toMatch(
      /border:\s*1px solid var\(--line\)/,
    )
  })

  it('lets a tall card scroll on a short desktop viewport instead of clipping it', () => {
    expect(t_rule(css, '.acct-review-stage')).toMatch(/overflow:\s*hidden auto/)
  })

  it('guards the Details pill hover behind a hover-capable pointer so a tap never paints it first', () => {
    expect(css).toMatch(/@media \(hover: hover\) \{\s*\.acct-review-expand:hover \{/)
    expect(css).not.toMatch(/\n\.acct-review-expand:hover \{/)
  })

  it('fades an edge only while that edge is cutting the category list off', () => {
    expect(t_rule(css, '.acct-review-category-results')).toMatch(/overflow-y:\s*auto/)
    // Each edge has its own rule, so an end you have reached stops fading.
    expect(css).toMatch(
      /\.acct-review-category-results\[data-fade-top\] \{\n\s*-webkit-mask-image: linear-gradient\(180deg, transparent/,
    )
    expect(css).toMatch(
      /\.acct-review-category-results\[data-fade-bottom\] \{\n\s*-webkit-mask-image: linear-gradient\(180deg, #000/,
    )
    expect(css).toMatch(/\[data-fade-top\]\[data-fade-bottom\] \{/)
    // Clearance is scroll-end space INSIDE the list, equal to the card's own top padding, so
    // it shows only once you reach the end. The card itself adds nothing below the fade.
    expect(css).toMatch(
      /\[data-fade-bottom\] \{\n\s*padding: 10px 2px var\(--acct-review-card-pad\);/,
    )
    expect(css).toMatch(/data-fade-bottom\]\n\s*\) \{\n\s*padding-bottom: 0;/)
  })

  it('outlines only the front card, and only for keyboard-driven focus', () => {
    expect(css).toMatch(
      /\.acct-review-stage:focus-visible:not\(\[data-input='pointer'\]\)\s+\.acct-review-card:not\(\.acct-review-card--rear\)\s*\{/,
    )
    expect(css).not.toMatch(/\.acct-review-stage:focus-visible \.acct-review-card \{/)
  })

  it('moves the rear card with a transform, never by resizing its box', () => {
    // Animating inset/width would relayout the rear card's contents every frame, which reads
    // as the amount shimmering while the next card comes up.
    expect(css).toMatch(
      /\n\.acct-review-card--rear \{[^}]*transform: translateY\(10px\) scaleX\(0\.955\)/,
    )
    expect(css).toMatch(/\n\.acct-review-card--rear \{[^}]*transition: transform 220ms/)
    expect(css).not.toMatch(/transition:\s*inset/)
    expect(css).toMatch(/is-advancing \.acct-review-card--rear \{\n\s*transform: none;/)
    // A card in motion owns its layer, so its text is rasterised once instead of per frame.
    expect(css).toMatch(/will-change: transform;/)
  })

  it('draws the rear card as a real card on the same surface instead of a tinted pseudo-element', () => {
    expect(css).not.toMatch(/\.acct-review-deck\.has-next::before/)
    const rear = css.match(/\n\.acct-review-card--rear \{([^}]*)\}/)?.[1] ?? ''
    expect(rear).toMatch(/position:\s*absolute/)
    expect(rear).not.toMatch(/background:/)
  })

  it('cuts every card in the deck as a receipt, the one behind in a fainter edge', () => {
    // The owner's pick (2026-09-28) over leaving the card behind plain.
    expect(t_rule(css, '.acct-review-card')).toMatch(/mask-image:\s*var\(--receipt-mask\)/)
    const rear = css.match(/\n\.acct-review-card--rear \{([^}]*)\}/)?.[1] ?? ''
    expect(rear).not.toMatch(/mask/)
    expect(rear).toMatch(/--receipt-edge:\s*var\(--receipt-edge-behind\)/)
    // It rises to the front card's edge, and the card dropping into its slot keeps the fainter.
    expect(css).toMatch(
      /is-advancing \.acct-review-card--rear \{[^}]*--receipt-edge:\s*var\(--line\)/,
    )
    expect(css).toMatch(
      /is-advancing \.acct-review-card--deep \{[^}]*--receipt-edge:\s*var\(--receipt-edge-behind\)/,
    )
  })
})
