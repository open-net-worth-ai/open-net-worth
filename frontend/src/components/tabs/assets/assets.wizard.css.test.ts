import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * The Add asset dialog fits each step (owner, 2026-09-26): a fixed-height frame left a short
 * step as two fields floating in a large empty box. A tall step is bounded by the dialog body's
 * own max-height instead, and scrolls there.
 */
const css = readFileSync(resolve(__dirname, './assets.css'), 'utf8')

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^${escaped} \\{[^}]*\\}`, 'm').exec(css)?.[0] ?? ''
}

describe('the Add asset dialog frame', () => {
  it('has no fixed height at any width and never clips — the dialog body scrolls', () => {
    const frame = rule('.as-wizard')
    expect(frame).toMatch(/display:\s*flex;/)
    expect(frame).not.toMatch(/(^|[\s;{])(min-|max-)?height:/)
    expect(frame).not.toMatch(/overflow|overscroll/)
    expect(css).not.toMatch(/\.as-wizard \{[^}]*\bheight:/)
  })

  it('draws the progress bar from tokens only', () => {
    const segment = rule('.as-wizard-progress > span')
    expect(segment).toMatch(/height:\s*var\(--space-1\);/)
    expect(segment).toMatch(/border-radius:\s*var\(--radius-pill\);/)
    // --line-strong: plain --line all but vanished on a dark surface.
    expect(segment).toMatch(/background:\s*var\(--line-strong\);/)
    // A done step is a selection state, so it reads in ink, not the blue accent.
    expect(rule('.as-wizard-progress > span.on')).toMatch(/background:\s*var\(--ink\);/)
    expect(rule('.as-wizard-progress')).toMatch(/gap:\s*var\(--space-2\);/)
  })
})
