// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'
import { PositionVerdictLine } from './PositionVerdictLine'
import type { PositionVerdict } from '../../types/assetDetail'

const TRIM: PositionVerdict = {
  action: 'trim',
  why: 'Up a lot.',
  currency: 'USD',
  order: { side: 'sell', quantity: 25, limit: 270 },
  levels: [
    { kind: 'stop', price: 112, source: 'thesis', distance_pct: -18.2 },
    { kind: 'trim', price: 160, source: 'lucius', distance_pct: 16.8 },
  ],
}

function renderWithComposer(verdict: PositionVerdict) {
  const seen: { pending: string | null; mentions: unknown[] } = { pending: null, mentions: [] }
  function Probe() {
    const c = useChatComposer()
    seen.pending = c.pendingText
    seen.mentions = c.pendingMentions
    return null
  }
  render(
    <ChatComposerProvider>
      <Probe />
      <PositionVerdictLine
        instrumentId="inst-acme"
        name="Acme"
        ticker="ACME"
        priceMaxFrac={2}
        verdict={verdict}
      />
    </ChatComposerProvider>,
  )
  return seen
}

describe('PositionVerdictLine', () => {
  afterEach(cleanup)

  it('shows the call and why, with each price only once — in the suggestion that uses it', () => {
    renderWithComposer(TRIM)
    expect(screen.getByText('Trim')).toBeTruthy()
    expect(screen.getByText(/Up a lot\./)).toBeTruthy()
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Sell 25 at $270.00',
      'Alert me below $112.00 or above $160.00',
    ])
  })

  it('fills Lucius’s composer with the request and the stock, and opens it', () => {
    const opened: Event[] = []
    const onOpen = (e: Event) => opened.push(e)
    window.addEventListener('lx-dock-open', onOpen)
    const seen = renderWithComposer(TRIM)
    fireEvent.click(screen.getByRole('button', { name: 'Sell 25 at $270.00' }))
    expect(seen.pending).toBe('Sell 25 at a $270.00 limit, as you suggested.')
    expect(seen.mentions).toEqual([{ type: 'instrument', ref: 'inst-acme', label: 'ACME' }])
    fireEvent.click(screen.getByRole('button', { name: /^Alert me/ }))
    expect(seen.pending).toBe(
      'Set price alerts: tell me if it falls to $112.00 or rises to $160.00.',
    )
    expect(opened).toHaveLength(2)
    window.removeEventListener('lx-dock-open', onOpen)
  })

  it('a hold with no levels is the call alone, with nothing to tap', () => {
    renderWithComposer({ action: 'hold', why: 'Nothing changed.', currency: 'USD', levels: [] })
    expect(screen.getByText('Hold')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
