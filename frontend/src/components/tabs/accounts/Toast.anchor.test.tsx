// @vitest-environment jsdom
//
// Regression: the Accounts toast ("13 marked as reviewed") was `position: fixed; left: 50%;
// transform: translateX(-50%)` — centred on the VIEWPORT. The register is not the viewport:
// the nav rail and the accounts sidebar eat space on the left and the Lucius panel eats
// ~400px on the right, so the pill floated well to the right of the table it belonged to.
//
// The bulk bar directly above it already solved this with an inset box (`left: <offsets>;
// right: <panel>` + auto centring). The toast now uses the same technique, opt-in via
// anchor="register" so the import modal's full-screen overlay keeps viewport centring.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Toast } from './Toast'

afterEach(() => cleanup())

const css = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')

/** The declaration block of a single selector, as written in accounts.css. */
function rule(selector: string): string {
  const at = css.indexOf(selector + ' {')
  expect(at, `missing rule for "${selector}"`).toBeGreaterThan(-1)
  return css.slice(at, css.indexOf('}', at))
}

describe('Toast anchoring', () => {
  it('defaults to viewport centring so the import modal overlay is unchanged', () => {
    render(<Toast message="Imported 4 files" />)
    const el = screen.getByRole('status')
    expect(el.className).toContain('acct-toast')
    expect(el.className).not.toContain('anchor-reg')
    expect(el.getAttribute('style')).toBeNull()
  })

  it('opts into register centring and publishes the rail width', () => {
    render(<Toast message="13 marked as reviewed" anchor="register" railWidth={286} />)
    const el = screen.getByRole('status')
    expect(el.className).toContain('anchor-reg')
    expect(el.style.getPropertyValue('--acct-toast-rail-w')).toBe('286px')
  })

  it('publishes a zero rail width when the sidebar is collapsed', () => {
    render(<Toast message="13 marked as reviewed" anchor="register" railWidth={0} />)
    expect(screen.getByRole('status').style.getPropertyValue('--acct-toast-rail-w')).toBe('0px')
  })

  it('announces itself to assistive tech', () => {
    render(<Toast message="13 marked as reviewed" />)
    expect(screen.getByRole('status')).toHaveProperty('ariaLive', 'polite')
  })

  it('rises above the bulk bar only while it is up', () => {
    const { rerender } = render(<Toast message="Reconciled — in sync" />)
    expect(screen.getByRole('status').className).not.toContain('acct-toast--above-bulk')
    rerender(<Toast message="13 marked as reviewed" aboveBulkBar />)
    expect(screen.getByRole('status').className).toContain('acct-toast--above-bulk')
  })

  it('renders nothing without a message', () => {
    const { container } = render(<Toast message={null} />)
    expect(container.firstChild).toBeNull()
  })
})

// Owner report 2026-10-26: "Reconciled — in sync" floated over the register rows. The pill sat
// 84px up — the height that clears the bulk bar — even with nothing selected, while every other
// screen's toast sits ~24px from the bottom.
describe('.acct-toast vertical position', () => {
  it('sits on the same bottom line as the other toasts by default', () => {
    expect(rule('.acct-toast')).toContain('bottom: 24px')
  })

  it('clears the bulk bar (bottom 24px + its height) only in the lifted variant', () => {
    expect(rule('.acct-toast.acct-toast--above-bulk')).toContain('bottom: 84px')
  })
})

describe('.acct-toast.anchor-reg geometry', () => {
  const anchored = () => rule('.acct-toast.anchor-reg')

  it('clears the nav rail and the accounts sidebar on the left', () => {
    expect(anchored()).toContain('var(--lx-sidebar-w, 0px)')
    expect(anchored()).toContain('var(--acct-toast-rail-w, var(--acct-side-w))')
  })

  it('centres via an inset box, NOT the viewport-centring translate', () => {
    // `left: 50%` + `translateX(-50%)` is the bug. An inset box with auto side margins
    // centres inside whatever space is actually left over.
    expect(anchored()).toContain('right: 0')
    expect(anchored()).toContain('transform: none')
    expect(anchored()).toContain('margin-inline: auto')
    expect(anchored()).toContain('width: fit-content')
  })

  it('pulls its right edge in by the Lucius panel width when the chat is open', () => {
    expect(rule('.app-shell.chat-open .acct-toast.anchor-reg')).toContain(
      'right: var(--chat-width, 400px)',
    )
  })

  it('uses a translate-free entry animation so the centring is not clobbered', () => {
    // The shared `acct-toast-in` keyframes animate `translate(-50%, …)`; reusing them here
    // would re-apply the very offset this variant removes.
    expect(anchored()).toContain('animation-name: acct-toast-rise')
    expect(rule('@keyframes acct-toast-rise')).toContain('translateY(8px)')
  })
})
