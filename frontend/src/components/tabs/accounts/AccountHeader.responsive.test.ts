import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (file: string) => readFileSync(resolve(__dirname, file), 'utf8')
const header = read('AccountHeader.tsx')
const css = read('accounts.css')

describe('Account header at narrow widths', () => {
  it('uses the shared icon seam for every account action', () => {
    expect(header).toMatch(/from '\.\.\/\.\.\/\.\.\/ui\/icons'/)
    expect(header).not.toMatch(/<svg/)
  })

  // Owner, 2026-10-26: the action sits beside the status it changes — on the reconcile line,
  // at every width — not as a header button that folds into the ⋯ menu.
  it('keeps Reconcile on the reconcile line, not the header or the ⋯ menu', () => {
    expect(header).not.toMatch(/menuTrigger/)
    expect(header.match(/<ReconcilePopover /g)).toHaveLength(1)
    expect(css).not.toMatch(/acct-head-reconcile|acct-pop-wrap--menu/)
    expect(css).toMatch(/\.acct-pop-wrap--line \.acct-pop\s*\{[^}]*left: 0/)
    // …and on a narrow column it hangs from the line's right edge so it can't run off screen.
    expect(css).toMatch(
      /@container acctmain \(max-width: 600px\) \{[^@]*\.acct-pop-wrap--line \.acct-pop\s*\{[^}]*right: 0/,
    )
  })

  it('allows account titles to wrap rather than ellipsize beside the menu', () => {
    expect(css).toMatch(
      /\.acct-head-title\s*\{[^}]*white-space: normal[^}]*overflow: visible[^}]*text-overflow: clip/,
    )
  })
})
