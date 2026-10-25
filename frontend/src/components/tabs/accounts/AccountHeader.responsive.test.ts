import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (file: string) => readFileSync(resolve(__dirname, file), 'utf8')
const header = read('AccountHeader.tsx')
const css = read('accounts.css')

describe('Account header at narrow widths', () => {
  it('uses the shared icon seam for every account action', () => {
    expect(header).toMatch(/from '\.\.\/\.\.\/\.\.\/ui\/icons'/)
    expect(header).toMatch(/ListChecks/)
    expect(header).not.toMatch(/<svg/)
  })

  it('moves Reconcile into the existing overflow menu', () => {
    expect(header).toMatch(
      /<ReconcilePopover\s+account=\{account\}\s+onReconcile=\{onReconcile\}\s+menuTrigger\s+onDone=\{\(\) => setMenuOpen\(false\)\}\s*\/>/,
    )
    expect(css).toMatch(/\.acct-head-reconcile\s*\{[^}]*display: none/)
    expect(css).toMatch(/\.acct-pop-wrap--menu\s*\{[^}]*display: block/)
    expect(css).toMatch(
      /@media \(max-width: 640px\) \{\s*\.acct-pop-wrap--menu \.acct-pop\s*\{[^}]*position: absolute[^}]*top: calc\(100% \+ 8px\)[^}]*right: 0/,
    )
    expect(css).not.toMatch(/\.acct-pop-wrap--menu \.acct-pop\s*\{[^}]*position: fixed/)
  })

  it('allows account titles to wrap rather than ellipsize beside the menu', () => {
    expect(css).toMatch(
      /\.acct-head-title\s*\{[^}]*white-space: normal[^}]*overflow: visible[^}]*text-overflow: clip/,
    )
  })
})
