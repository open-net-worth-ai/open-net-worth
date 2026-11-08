import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const src = (path: string) => resolve(__dirname, '..', path)
const luciusCss = readFileSync(src('lucius.css'), 'utf8')
const layoutCss = readFileSync(src('index.css'), 'utf8')
const accountsCss = readFileSync(src('components/tabs/accounts/accounts.css'), 'utf8')
const investmentsCss = readFileSync(src('components/tabs/investments/design.css'), 'utf8')
const chatPanel = readFileSync(src('components/chat/ChatPanel.tsx'), 'utf8')
const luciusHome = readFileSync(src('components/tabs/lucius/LuciusHome.tsx'), 'utf8')
const accounts = readFileSync(src('components/tabs/accounts/AccountsTab.tsx'), 'utf8')
const txnDetail = readFileSync(src('components/tabs/accounts/TxnDetail.tsx'), 'utf8')
const register = readFileSync(src('components/tabs/accounts/Register.tsx'), 'utf8')
const feedTable = readFileSync(src('components/tabs/investments/FeedTable.tsx'), 'utf8')

describe('iPad desktop support', () => {
  it('caps the top safe area on touch desktop layouts and adds no floor over what the OS reports', () => {
    expect(layoutCss).toMatch(
      /@media \(min-width: 640px\) and \(any-pointer: coarse\)\s*\{\s*:root\s*\{\s*--safe-t:\s*min\(env\(safe-area-inset-top, 0px\), 32px\);/,
    )
    expect(layoutCss).not.toMatch(/--safe-t:\s*(max|clamp)\([^;]*24px/)
  })

  it('keeps the desktop navigation header below the shared top safe area', () => {
    const headerRule = layoutCss.match(/\.sidebar-header\s*\{([\s\S]*?)\n\s*\}/)?.[1]
    expect(headerRule).toContain('padding: var(--safe-t) 14px 0;')
  })

  it('keeps the docked Lucius panel background at the viewport edge while its controls clear iOS chrome', () => {
    expect(luciusCss).toMatch(/\.lx-side\s*\{[\s\S]*?top:\s*0;/)
    expect(luciusCss).toMatch(
      /\.lx-side-head\s*\{[\s\S]*?padding:\s*calc\(10px \+ var\(--safe-t\)\) 12px 10px;/,
    )
  })

  it('lets the ticker topbar grow for a nonzero top safe area instead of constraining it to 52px', () => {
    const tickerRule = layoutCss.match(/\.topbar:has\(\.ticker\)\s*\{([\s\S]*?)\n\s*\}/)?.[1]
    expect(tickerRule).toContain('min-height: 52px;')
    expect(tickerRule).not.toMatch(/(^|\n)\s*height:\s*52px;/)
  })

  it('starts every docked panel resize with Pointer Events rather than mouse-only handlers', () => {
    for (const component of [chatPanel, luciusHome, accounts, txnDetail, register, feedTable]) {
      expect(component).toContain('usePointerResize')
      expect(component).toContain('onPointerDown')
      expect(component).not.toMatch(/onMouseDown=\{(?:handle|start|on)\w*Resize\w*\}/)
    }
  })

  it('keeps each iPad resize target touch-native and 24px wide without covering Accounts collapse', () => {
    expect(accountsCss).toMatch(/\.acct-side-resize\s*\{[\s\S]*?touch-action:\s*none;/)
    expect(accountsCss).toMatch(
      /@media \(pointer: coarse\)\s*\{[\s\S]*?\.acct-side-resize\s*\{[\s\S]*?top:\s*40px;[\s\S]*?right:\s*0;[\s\S]*?width:\s*24px;/,
    )
    expect(accountsCss).toMatch(
      /@media \(pointer: coarse\)\s*\{[\s\S]*?\.acct-col-resize\s*\{[\s\S]*?width:\s*24px;/,
    )
    expect(investmentsCss).toMatch(/\.mk-col-handle\s*\{[\s\S]*?touch-action:\s*none;/)
    expect(investmentsCss).toMatch(
      /@media \(pointer: coarse\)\s*\{[\s\S]*?\.mk-col-handle\s*\{[\s\S]*?width:\s*24px;/,
    )
  })

  it('does not autofocus the Lucius composer when the primary pointer is coarse', () => {
    expect(chatPanel).toContain(
      "import { useIsCoarsePointer } from '../../hooks/useIsCoarsePointer'",
    )
    expect(chatPanel).toContain(
      'const shouldAutoFocusComposer = autoFocusComposer && !isCoarsePointer',
    )
    expect(chatPanel).toContain('autoFocus={open && shouldAutoFocusComposer}')
    expect(chatPanel).toContain('if (!homeMode || !shouldAutoFocusComposer) return')
  })
})
