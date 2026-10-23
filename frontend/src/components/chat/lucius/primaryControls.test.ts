import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('composer primary controls', () => {
  it('uses ink for screen-awareness active states', () => {
    expect(rule('.lx-aw.on .lx-aw-toggle')).toContain('color: var(--ink)')
    expect(rule('.lx-scope-ic')).toContain('color: var(--ink)')
    expect(rule('.lx-switch.on')).toContain('background: var(--ink)')
    expect(rule('.lx-switch.on')).toContain('border-color: var(--ink)')
    expect(rule('.lx-scope-peek.open')).toContain('border-color: var(--ink)')
  })

  it('uses the CTA tokens for the send control, neutral when disabled', () => {
    expect(rule('.lx-send')).toContain('background: var(--cta)')
    expect(rule('.lx-send')).toContain('color: var(--ink-on-cta)')
    const disabled = rule(".lx-send:disabled:not([aria-busy='true'])")
    expect(disabled).toContain('background: var(--bg-2)')
    expect(disabled).toContain('color: var(--faint)')
  })

  it('keeps the STOP state off the accent entirely', () => {
    // Stop is not a primary action — it is the way out of one — so it sits on the neutral
    // ramp in both states: surface2/muted/line at rest, and the matching hover tokens on
    // hover. This assertion used to demand `border-color: var(--primary)` on hover, which
    // put the accent on the one control whose job is to CANCEL the accented action.
    expect(rule('.lx-send.lx-stop')).toContain('background: var(--surface2)')
    const hover = rule('.lx-send.lx-stop:hover')
    expect(hover).toContain('background: var(--hover-surface2)')
    expect(hover).toContain('border-color: var(--hover-line)')
    expect(hover).not.toContain('var(--primary)')
  })
})
