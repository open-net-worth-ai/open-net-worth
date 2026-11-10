import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, 'accounts.css'), 'utf8')
const source = readFileSync(resolve(__dirname, 'SearchFilter.tsx'), 'utf8')

describe('Accounts filter toolbar responsive contract', () => {
  it('keeps the Accounts status bar on the shared desktop page inset', () => {
    // Since 2026-08-24 .topbar is a SIBLING of .app-layout (.app-main > .topbar + .app-layout),
    // so the Accounts panel's left-inset zeroing cannot reach it and accounts.css no longer
    // needs to hand it its inset back — the shared `.topbar { padding-inline: … }` in index.css
    // owns it for every tab. Assert the shared rule, and that this file does NOT re-inset it.
    const shell = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8')
    expect(shell).toMatch(/\.topbar \{[^}]*padding-inline: max\(24px/s)
    expect(css).not.toContain('.acct-shell) .topbar')
  })

  // Keyed on the register column's width (`acctmain` container), never the viewport: a wide
  // window with the rail and Lucius open leaves a narrow column the viewport can't see.
  const narrowBlock = () =>
    css.match(/@container acctmain \(max-width: 700px\) \{([\s\S]*?)\n\}/)?.[1] ?? ''

  it('keeps controls on one row and hides date/column labels when the column is narrow', () => {
    const narrow = narrowBlock()

    expect(narrow).toMatch(/\.acct-filters \{\s*flex-wrap: nowrap;/)
    expect(narrow).toMatch(
      /\.acct-filters \.acct-viewlbl,\s*\.acct-filters \.acct-viewcaret \{\s*display: none;/,
    )
    expect(narrow).not.toMatch(/\.acct-filter-add\s*\{[^}]*display:\s*none/)
    expect(narrow).toMatch(/\.acct-searchwrap \.acct-search \{\s*min-width: 0;/)
    expect(css).not.toMatch(/@media \(max-width: 960px\) \{\s*\.acct-filters \{/)
  })

  it('keeps a dynamic accessible name on the icon-only date control', () => {
    expect(source).toContain('aria-label={`Date range: ${dateLabel}`}')
    expect(source).toContain('title={`Date range: ${dateLabel}`}')
  })

  it('shortens the add transaction label to Add when the column is narrow', () => {
    const narrow = narrowBlock()

    expect(source).toContain('aria-label="Add transaction"')
    expect(source).toContain('<span className="acct-filter-add-full">Add transaction</span>')
    expect(source).toContain('<span className="acct-filter-add-short">Add</span>')
    expect(css).toMatch(/\n\.acct-filter-add-short \{\s*display: none;/)
    expect(narrow).toMatch(/\.acct-filter-add-full \{\s*display: none;/)
    expect(narrow).toMatch(/\.acct-filter-add-short \{\s*display: inline;/)
  })
})
