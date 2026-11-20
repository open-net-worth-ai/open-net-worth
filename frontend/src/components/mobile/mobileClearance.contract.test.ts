// Every viewport-bottom-anchored surface has to clear the fixed tab bar. That clearance
// used to be an enumerated selector list inside the phone media query, naming eleven
// component classes the shell has no other business knowing about — so a new toast or
// bulk bar painted UNDER the navigation until someone remembered to add it there (and
// one entry, `.acct-bulk-bar`, named a class that no longer existed at all).
//
// It is now one opt-in utility, `.mb-anchored`, applied by the component that needs it.

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8')
const indexCss = read('../../index.css')

/** Component → the exact className literal that must carry the utility. */
const ANCHORED: Record<string, string> = {
  '../intel/AssetDetailView.tsx': 'asd-ask-bar mb-anchored',
  '../tabs/accounts/BulkBar.tsx': 'acct-bulk mb-anchored',
  '../tabs/accounts/Toast.tsx': 'acct-toast mb-anchored',
  '../tabs/plan/outlook.tsx': 'pl-rail-fab mb-anchored',
  '../tabs/plan/Plan.tsx': 'pl-toast mb-anchored',
  '../tabs/plan/UndoPill.tsx': 'pl-undo mb-anchored',
  '../tabs/commitments/Commitments.tsx': 'c2-toast mb-anchored',
  '../tabs/assets/Assets.tsx': 'as-toast mb-anchored',
  '../tabs/overview/OvToast.tsx': 'ov-toast mb-anchored',
  '../intel/ScanToast.tsx': 'intel-scan-toast mb-anchored',
}

describe('mobile tab-bar clearance', () => {
  it('is granted by exactly one utility rule, not an enumerated selector list', () => {
    const rules = [
      ...indexCss.matchAll(/([^{}]*)\{[^{}]*bottom:\s*var\(--mobile-tabbar-clearance\)[^{}]*\}/g),
    ].map((m) => m[1]!.replace(/\/\*[\s\S]*?\*\//g, '').trim())

    expect(rules).toEqual(['.app-shell.mb-shell .mb-anchored'])
  })

  it.each(Object.entries(ANCHORED))('%s opts in via .mb-anchored', (file, className) => {
    expect(read(file)).toContain(className)
  })
})

describe('mobile scroller', () => {
  // An absolute box with no positioned ancestor (an `.sr-only` label) otherwise resolves
  // against the page, grows the DOCUMENT past the viewport, and iOS scrolls the whole shell —
  // tab bar included — off the screen.
  it('is the containing block for absolute descendants', () => {
    const rule = indexCss.match(/\.app-shell\.mb-shell \.app-layout \{([^{}]*)\}/)
    expect(rule?.[1]).toMatch(/position:\s*relative/)
  })
})
