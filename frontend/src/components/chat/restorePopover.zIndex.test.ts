/**
 * The chat's Restore popover is portaled to <body>. In the floating Lucius panel (.lx-dock) it
 * opened at --z-modal, UNDER the panel, so Restore fetched its preview and seemed to do nothing
 * (hand UAT, 2026-11-11). jsdom doesn't compute the cascade, so this reads the real CSS and the
 * generated token scale — the technique dragRightClick.zIndex.test.tsx uses.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const chatCss = readFileSync(resolve(__dirname, '../../chat.css'), 'utf8')
const luciusCss = readFileSync(resolve(__dirname, '../../lucius.css'), 'utf8')
const tokensCss = readFileSync(resolve(__dirname, '../../index.tokens.generated.css'), 'utf8')

function ruleBody(css: string, selector: string): string {
  const start = css.indexOf(selector + ' {')
  expect(start, `no \`${selector} {\` rule`).toBeGreaterThan(-1)
  return css.slice(start, css.indexOf('}', start))
}

function token(name: string): number {
  const m = new RegExp(`--z-${name}:\\s*(-?\\d+)\\s*;`).exec(tokensCss)
  expect(m, `--z-${name} not found`).not.toBeNull()
  return Number(m![1])
}

function zIndex(body: string): number {
  const plus = /z-index:\s*calc\(var\(--z-toast\)\s*\+\s*(\d+)\)/.exec(body)
  if (plus) return token('toast') + Number(plus[1])
  const plain = /z-index:\s*var\(--z-([a-z]+)\)/.exec(body)
  expect(plain, 'no z-index declaration').not.toBeNull()
  return token(plain![1]!)
}

describe('Restore popover layering', () => {
  it('paints above the floating Lucius panel', () => {
    const popover = zIndex(ruleBody(chatCss, '.msg-restore-popover'))
    const dock = zIndex(ruleBody(luciusCss, '.lx-dock'))

    expect(popover).toBeGreaterThan(dock)
  })
})
