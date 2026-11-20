import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * The account type picker is ONE column at every width.
 *
 * Each of the seven rows is `<label> · <one-line meaning>` (spec 2026-09-07-account-taxonomy).
 * A two-column tier wrapped every meaning onto two or three lines, and the list stopped
 * reading as the liquidity ladder it is ordered as. The 860px tier used to force `1fr 1fr`,
 * with a second override at 640px to undo it again.
 */
const css = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')

describe('.acct-typepick', () => {
  it('declares one column, once', () => {
    const declarations = css.match(/\.acct-typepick \{[^}]*\}/g) ?? []
    expect(declarations).toHaveLength(1)
    expect(declarations[0]).toMatch(/grid-template-columns:\s*1fr;/)
  })

  it('is never widened by a media query', () => {
    expect(css).not.toMatch(/\.acct-typepick \{[^}]*grid-template-columns:\s*1fr 1fr/)
  })
})

describe('edit-account scrollbar', () => {
  it('keeps equal content padding while sitting at the modal edge', () => {
    expect(css).toMatch(
      /\.acct-wizard--edit \{[^}]*margin-inline:\s*calc\(-1 \* var\(--space-8\)\)/s,
    )
    expect(css).toMatch(/\.acct-wizard--edit \{[^}]*padding-inline:\s*var\(--space-8\)/s)
    expect(css).toMatch(/\.acct-wizard--edit \{[^}]*scrollbar-gutter:\s*stable both-edges/s)
    expect(css).toMatch(/\.acct-wizard--edit \{[^}]*scrollbar-color:\s*transparent transparent/s)
    expect(css).toMatch(/\.acct-wizard--edit\.is-scrolling[^}]*scrollbar-color:/s)
    expect(css).not.toMatch(/\.acct-wizard--edit:focus-within[^}]*scrollbar-color:/s)
    expect(css).toMatch(
      /\.acct-wizard--edit\.is-scrolling\s*\{[^}]*scrollbar-color:\s*color-mix\(in srgb, var\(--muted\) 55%, transparent\) transparent/,
    )
    expect(css).toMatch(
      /\.acct-wizard--edit\.is-scrolling::-webkit-scrollbar-thumb\s*\{[^}]*background:\s*color-mix\(in srgb, var\(--muted\) 55%, transparent\)/,
    )
  })
})

describe('account-type typography', () => {
  it('matches field-value size for the selected type while keeping its meaning subordinate', () => {
    expect(css).toMatch(/\.acct-type-chosen-lbl \{[^}]*font-size:\s*var\(--text-md\)/s)
    expect(css).toMatch(/\.acct-type-chosen-grp \{[^}]*font-size:\s*var\(--text-xs\)/s)
  })

  it('uses the same hierarchy inside the type picker', () => {
    expect(css).toMatch(/\.acct-type-lbl \{[^}]*font-size:\s*var\(--text-md\)/s)
    expect(css).toMatch(/\.acct-type-meaning \{[^}]*font-size:\s*var\(--text-xs\)/s)
  })
})

describe('edit-account metadata', () => {
  it('uses one vertical column and opens the colour picker inward', () => {
    // The edit form's metadata (`.acct-identity-fields`, which replaced the old two-column
    // `.acct-classification-fields` grid) stacks as a single flex column.
    expect(css).toMatch(
      /\.acct-identity-fields \{[^}]*display:\s*flex;[^}]*flex-direction:\s*column/s,
    )
    expect(css).toMatch(/\.acct-cp-pop \{[^}]*left:\s*0;[^}]*right:\s*auto/s)
  })
})
