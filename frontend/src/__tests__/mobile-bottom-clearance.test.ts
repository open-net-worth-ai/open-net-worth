import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
const planCss = readFileSync(resolve(__dirname, '../components/tabs/plan/plan.css'), 'utf8')
const chatCss = readFileSync(resolve(__dirname, '../chat.css'), 'utf8')
const accountsCss = readFileSync(
  resolve(__dirname, '../components/tabs/accounts/accounts.css'),
  'utf8',
)
const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')
const main = readFileSync(resolve(__dirname, '../main.tsx'), 'utf8')

// The phone boundary, as an alternation of the two canonical ladder forms. Both `640px` and
// its `.98px` parity twin are allowed by scripts/check-breakpoints.mjs, and that linter — not
// this file — owns which one a given block uses. Assertions below are about the RULES inside
// the phone query; spelling the boundary out as a literal coupled them to a refactor they
// have no stake in, and broke all three when index.css moved onto the parity form.
const PHONE_BP = '6(?:40|39\\.98)px'

describe('mobile bottom navigation clearance', () => {
  it('locks the app viewport at scale 1 so page-level pinch zoom is disabled', () => {
    expect(html).toMatch(
      /<meta\s+name="viewport"\s+content="width=device-width,\s*initial-scale=1(?:\.0)?,\s*maximum-scale=1(?:\.0)?,\s*user-scalable=no,\s*viewport-fit=cover,\s*interactive-widget=resizes-content"\s*\/>/,
    )
  })

  it('keeps the mobile navigation flat in every theme', () => {
    expect(css).toMatch(/\.mb-tabbar\s*\{[^}]*box-shadow:\s*none;/)
  })

  it('allows all six mobile tabs to shrink inside a 320px viewport', () => {
    expect(css).toMatch(/\.mb-tab\s*\{[^}]*min-width:\s*0;/)
  })

  it('lets Plan inherit the same mobile top and horizontal padding as Overview', () => {
    expect(planCss).toMatch(
      /@media \(max-width: 560px\)\s*\{[\s\S]*?\.pl-page\s*\{\s*padding:\s*0 0 110px;\s*\}/,
    )
  })

  it('does not add document-style bottom padding to panel tabs such as Accounts', () => {
    expect(css).toMatch(
      /\.app-shell\.mb-shell \.app-layout\.app-layout--panel\s*\{\s*padding-bottom:\s*0;\s*\}/,
    )
  })

  it('removes the doubled outer gutters around the in-flow Lucius mobile tab', () => {
    // Matched on `:has(.app-layout--lucius) .topbar`, not the bare two-class selector this
    // once used. index.css raised the specificity deliberately — its own comment records why:
    // `.app-layout--lucius .topbar` (2 classes) "LOST that fight silently", so padding-top: 0
    // never applied and the topbar kept reserving safe-area under .lucius-mobile-head's own,
    // stacking a dead gap above the floating buttons on a real device. Pin the part that
    // carries the behaviour, not the prefix that is free to change again.
    expect(css).toMatch(
      /:has\(\.app-layout--lucius\) \.topbar\s*\{[^}]*padding-bottom:\s*0;[^}]*margin-bottom:\s*0;/,
    )
    // .lx-side's own edge-to-edge bleed hack (width:calc(100% + 32px); margin-inline:-16px)
    // was specific to the OLD mobile hack that bent ChatPanel's own floating-dock-shaped
    // .lx-side into the mobile tab (index.css, retired 2026-08-13). The purpose-built
    // LuciusMobile.tsx/.lucius-mobile-shell that replaced it gets the same edge-to-edge
    // result a different way — position:fixed; left:0; right:0 bypasses .app-layout's own
    // padding directly, with no compensating negative-margin hack needed.
    const mobileCss = readFileSync(
      resolve(__dirname, '../components/tabs/lucius/lucius-mobile.css'),
      'utf8',
    )
    expect(mobileCss).toMatch(
      /\.lucius-mobile-shell\s*\{[^}]*position:\s*fixed;[^}]*left:\s*0;[^}]*right:\s*0;/,
    )
  })

  // Was a 16px baseline forced with !important to block iOS focus-zoom. index.html ships
  // `maximum-scale=1.0, user-scalable=no`, which suppresses that zoom anyway, so the
  // override only served to silently beat every component's own font-size.
  it('uses a 14px baseline and lets components size their own controls', () => {
    expect(css).toMatch(/body\s*\{[^}]*font-size:\s*var\(--text-md\);/)
    // PHONE_BP, not a literal: `640px` and `639.98px` are BOTH on the canonical ladder
    // (scripts/check-breakpoints.mjs), these rules sit on the .98 parity form today, and which
    // form a block uses is that linter's business — not this test's. Pinning the literal here
    // only coupled a font-size assertion to a boundary refactor, and duly broke on one.
    expect(css).toMatch(
      new RegExp(
        `@media \\(max-width: ${PHONE_BP}\\)[\\s\\S]*?input:not\\(\\[type='checkbox'\\]\\):not\\(\\[type='radio'\\]\\):not\\(\\[type='range'\\]\\):not\\(\\[type='color'\\]\\),[\\s\\S]*?textarea,[\\s\\S]*?select,[\\s\\S]*?\\[contenteditable='true'\\]\\s*\\{\\s*font-size:\\s*inherit;\\s*\\}`,
      ),
    )
    expect(css).not.toMatch(/font-size:\s*16px\s*!important/)
    // The viewport meta is what makes sub-16px safe — if it goes, 16px has to come back.
    const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
    expect(html).toMatch(/maximum-scale=1\.0/)
    expect(html).toMatch(/user-scalable=no/)
    // Chat prose reads 14px on BOTH phone and desktop. This used to be enforced by a
    // phone-only override in index.css; 1fdc6268 removed that deliberately once desktop
    // started reading from --chat-text as well, so the two agree without an override.
    // Assert the invariant that now carries it — the token and its consumers — rather than
    // the override, which no longer exists and whose absence is the point.
    expect(chatCss).toMatch(/--chat-text:\s*14px;/)
    expect(chatCss).toMatch(/\.msg-user \.msg-text\s*\{[^}]*font-size:\s*var\(--chat-text\);/)
    expect(chatCss).toMatch(/\.msg-assistant \.msg-text\s*\{[^}]*font-size:\s*var\(--chat-text\);/)
    expect(css).not.toMatch(/\.app-layout--lucius \.lx-msg\s*\{[^}]*font-size:/)
  })

  it('keeps the model and effort selectors on the compact utility type scale', () => {
    expect(css).toMatch(
      new RegExp(
        `@media \\(max-width: ${PHONE_BP}\\)[\\s\\S]*?\\.chat-model-select\\.ui-select\\s*\\{[^}]*font-size:\\s*var\\(--text-xs\\) !important;`,
      ),
    )
  })

  it('wraps long transaction drawer values instead of widening the mobile sheet', () => {
    expect(accountsCss).toMatch(
      /\.acct-detail-v\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;/,
    )
    expect(accountsCss).toMatch(
      /\.acct-detail-src\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;[^}]*flex-wrap:\s*wrap;/,
    )
  })

  it('keeps Plan target dialogs inside the mobile viewport', () => {
    expect(css).toMatch(
      /@media \(max-width: 640px\)\s*\{[\s\S]*?:root\s*\{\s*--lx-sidebar-w:\s*0px;\s*\}/,
    )
    expect(planCss).toMatch(
      /\.pl-tgt-row > span:first-child\s*\{[^}]*min-width:\s*0;[^}]*white-space:\s*normal;/,
    )
    expect(planCss).toMatch(/\.pl-tgt-row b\s*\{[^}]*flex:\s*none;/)
  })

  it('keeps sticky panels and floating feedback above the tab bar', () => {
    // Both derive from --mb-tabbar-h so the keyboard rules can reclaim the bar's space
    // by zeroing one variable instead of leaving a dead band where the bar was.
    expect(css).toContain('--mb-tabbar-h: calc(57px + var(--safe-b));')
    expect(css).toContain('--mobile-tabbar-clearance: calc(var(--mb-tabbar-h) + var(--space-4));')
    // One shared `.mb-anchored` hook rather than a per-surface selector list.
    expect(css).toMatch(
      /\.app-shell\.mb-shell \.mb-anchored\s*\{\s*bottom: var\(--mobile-tabbar-clearance\);\s*\}/,
    )
    // The shell is a fixed-height column; the pane flexes inside it and owns the scroll.
    expect(css).toMatch(
      /\.app-shell\.mb-shell\s*\{[^}]*height: 100dvh;[^}]*flex-direction: column;[^}]*overflow: hidden;/,
    )
    expect(css).toMatch(
      /\.app-shell\.mb-shell \.app-layout\s*\{[^}]*flex: 1;[^}]*min-height: 0;[^}]*overflow-y: auto;/,
    )
    expect(css).toMatch(
      /\.app-shell\.mb-shell \.asd-ask-bar\s*\{[^}]*position:\s*fixed;[^}]*left:\s*0;[^}]*right:\s*0;/,
    )
  })

  it('uses the small viewport in an iOS browser tab so Safari chrome cannot cover the nav', () => {
    const defaultRule = css.indexOf('height: 100dvh;', css.indexOf('.app-shell.mb-shell'))
    const safariBrowserRule = css.indexOf(
      ":root[data-onw-display-mode='browser'] .app-shell.mb-shell",
    )

    expect(defaultRule).toBeGreaterThan(-1)
    expect(safariBrowserRule).toBeGreaterThan(defaultRule)
    const safariBrowserBody = css.slice(safariBrowserRule).match(/^[^{]*\{([^}]*)\}/)?.[1]
    expect(safariBrowserBody).toBeDefined()
    expect(safariBrowserBody).toContain('height: 100svh;')
    // The desktop shell owns `min-height: 100vh`. Unless browser mode releases that floor,
    // the larger minimum wins and the `100svh` declaration is mathematically inert.
    expect(safariBrowserBody).toContain('min-height: 0;')
    expect(css).toMatch(
      /@supports \(-webkit-touch-callout:\s*none\)\s*\{\s*@media \(max-width: 639\.98px\)\s*\{\s*:root\[data-onw-display-mode='browser'\] \.app-shell\.mb-shell\s*\{[^{}]*height:\s*100svh;/,
    )
    expect(css.match(/height:\s*100svh;/g)).toHaveLength(1)
    // Installed/PWA mode must never take the browser's small viewport: on iOS standalone it
    // leaves a permanent dead band below the app. It is sized from the real web view instead
    // (`height: 100%` down the ancestor chain — iOS resolves 100dvh to the full screen, past
    // the status bar), so the only standalone rules are percentage heights, never `svh`.
    const standaloneRule = css.match(
      /:root\[data-onw-display-mode='standalone'\] \.app-shell\.mb-shell\s*\{([^}]*)\}/,
    )?.[1]
    expect(standaloneRule).toBeDefined()
    expect(standaloneRule).toContain('height: 100%;')
    expect(standaloneRule).toContain('min-height: 0;')
    expect(standaloneRule).not.toContain('svh')
    const displayModeStamp = main.indexOf('stampPwaDisplayMode()')
    const reactMount = main.indexOf('createRoot(')
    expect(displayModeStamp).toBeGreaterThan(-1)
    expect(reactMount).toBeGreaterThan(-1)
    expect(displayModeStamp).toBeLessThan(reactMount)
  })

  // Was a JS hook measuring visualViewport deltas, with three invented constants that were
  // wrong on the owner's device six different ways. The browser does this for free once the
  // viewport meta asks it to; all that is left is "get out of the way while I type".
  it('takes the tab bar out of the layout whenever a text field has focus', () => {
    expect(html).toMatch(/name="viewport"[^>]*interactive-widget=resizes-content/)
    expect(css).toContain('.mb-shell:has(textarea:focus) .mb-tabbar')
    // The list's LAST selector is the one that carries the block.
    expect(css).toMatch(
      /:has\(\[contenteditable='true'\]:focus\) \.mb-tabbar\s*\{\s*display:\s*none;/,
    )
    // And the bar is no longer position:fixed — that is what iOS re-anchors to the keyboard.
    expect(css).toMatch(/\.mb-tabbar\s*\{(?:(?!\})[\s\S])*flex: none;/)
    expect(css).not.toMatch(/\.mb-tabbar\s*\{(?:(?!\})[\s\S])*position:\s*fixed;/)
    // Hiding it must also release its reserved height, or a dead band is left behind.
    expect(css).toMatch(/\.mb-shell:has\([^)]*:focus[\s\S]{0,400}?--mb-tabbar-h:\s*0px;/)
    expect(css).not.toContain('mobile-keyboard-open')
  })
})
