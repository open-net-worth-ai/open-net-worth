import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')

// With no z-index of its own, the full-screen call sat behind the chat: the call was on and the
// screen just showed the conversation (owner's phone, 2026-11-07).
it('the full-screen call sits above the app', () => {
  const rule = css.match(/\.lx-callstage--screen \{([^}]*)\}/)?.[1] ?? ''
  expect(rule).toContain('position: fixed;')
  expect(rule).toContain('z-index: calc(var(--z-toast) - 1);')
})
