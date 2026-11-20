import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Regression: the "Already in your ledger — will link" band underlined every date, payee,
 * note and amount inside it.
 *
 * Root cause — a class-name collision, not a styling slip. The ported prototype uses bare
 * single-word tone MODIFIERS (`link`, `new`, `skip`, …) and renders the band as
 * `<section class="imp3-sec link">`; the app also ships a GLOBAL `.link` inline-text-link
 * utility in index.css that sets `text-decoration: underline`. Per CSS Text Decoration §2.2
 * a text-decoration propagates to every in-flow descendant box and CANNOT be cancelled by
 * those descendants — so putting `text-decoration: none` on the children does nothing. It
 * only dies on the element that declares it.
 *
 * These assertions pin both halves of the collision, so the guard fails loudly whether the
 * neutraliser is removed OR the global utility stops underlining (making the guard moot).
 */
const read = (p: string) => readFileSync(resolve(__dirname, p), 'utf8')

describe('statement import — global `.link` utility collision', () => {
  const importCss = read('./statementImport.css')
  const indexCss = read('../../../index.css')
  const modal = read('./ImportModal.tsx')

  it('still has the collision to defend against', () => {
    // The prototype modifier: the strong band's tone is the bare word `link`.
    expect(modal).toMatch(/tone:\s*'link'/)
    expect(modal).toMatch(/className=\{'imp3-sec '\s*\+\s*sec\.tone\}/)
    // The global utility that collides with it. `^\s*` because the rule sits inside an @layer
    // block: how deep Prettier indents it is not part of the collision this test defends.
    expect(indexCss).toMatch(/^\s*\.link \{[^}]*text-decoration:\s*underline/m)
  })

  it('neutralises the utility on non-anchors inside the import workspace', () => {
    expect(importCss).toMatch(/^\.imp-pane--ws \.link:not\(a\) \{[^}]*text-decoration:\s*none/m)
  })

  it('stops the utility repainting the band’s inherited text colour', () => {
    expect(importCss).toMatch(/^\.imp3-sec\.link \{[^}]*color:\s*inherit/m)
  })

  /**
   * Second regression, same screen: the sticky filter bar left a 2px slit at the top of the
   * scrollport that every row scrolled visibly through. A sticky box is clamped to its
   * containing block (the scroller's CONTENT box) while `top: 0` is measured from the
   * scrollport (which includes padding) — so any padding-top on .imp3-scroll is a gap the bar
   * cannot cover. Measured at 2px in two separate screenshots before the fix.
   */
  it('leaves no padding above the sticky filter bar for content to show through', () => {
    const scroller = importCss.match(/^\.imp3-scroll \{[\s\S]*?^\}/m)?.[0]
    expect(scroller, '.imp3-scroll rule not found').toBeDefined()
    const padding = scroller!
      .match(/^\s*padding:\s*([^;]+);/m)?.[1]
      ?.trim()
      .split(/\s+/)
    expect(padding, 'padding shorthand not found').toBeDefined()
    expect(padding?.[0], 'padding-top must be 0 — see the rule comment').toBe('0')
    expect(scroller).not.toMatch(/padding-top:\s*(?!0)/)
    expect(importCss).toMatch(
      /^\.imp3-scroll > :first-child:not\(\.imp3-filter\) \{[^}]*margin-top/m,
    )
  })

  it('keeps the filter bar pinned to the scrollport top', () => {
    const bar = importCss.match(/^\.imp3-filter \{[\s\S]*?^\}/m)?.[0]
    expect(bar).toMatch(/position:\s*sticky/)
    expect(bar).toMatch(/top:\s*0/)
    // Opaque, or the rows would still read through it once it does cover the top.
    expect(bar).toMatch(/background:\s*var\(--modal-bg\)/)
  })

  /**
   * Third regression, same row: the category cell was a hard 140px rail holding a `flex: none`
   * chip, so a transfer identity ("Transfer ← Meridian Multiplier (SGD)") simply overran it and
   * collided with whatever came next (owner screenshot, 2026-07-29). The cell now treats 140px
   * as a floor, stretches to a capped maximum, and the label — in its own span — ellipsizes.
   */
  it('lets the category chip stretch then ellipsize instead of overrunning the row', () => {
    const cell = importCss.match(/^\.imp3-arow-catcol \{[^}]*\}/m)?.[0]
    expect(cell, '.imp3-arow-catcol rule not found').toBeDefined()
    expect(cell).not.toMatch(/flex:\s*none/)
    expect(cell).toMatch(/min-width:\s*140px/)
    expect(cell).toMatch(/max-width:\s*\d/)
    // The chip must be shrinkable inside that cell — the base .imp3-cat is `flex: none`.
    expect(importCss).toMatch(/^\.imp3-arow-catcol \.imp3-cat \{[^}]*flex:\s*0 1 auto/m)
    const label = importCss.match(/^\.imp3-cat > span \{[^}]*\}/m)?.[0]
    expect(label).toMatch(/min-width:\s*0/)
    expect(label).toMatch(/text-overflow:\s*ellipsis/)
    // …and the row chips must actually WRAP their label in that span, or the rule never applies.
    expect(modal).toMatch(/<span>\{categoryLabel\}<\/span>/)
    expect(modal).toMatch(/<span>\{category\}<\/span>/)
  })

  it('does not try to cancel the propagated underline on descendants (it cannot work)', () => {
    for (const cls of [
      'imp3-sec-sub',
      'imp3-arow-date',
      'imp3-arow-desc',
      'imp3-arow-amt',
      'imp3-arow-match-name',
      'imp3-arow-match-amt',
    ]) {
      const rule = new RegExp(`^\\.${cls} \\{[^}]*\\}`, 'm')
      const match = importCss.match(rule)
      expect(match, `${cls} rule not found`).not.toBeNull()
      expect(match![0]).not.toMatch(/text-decoration:\s*none/)
    }
  })
})
