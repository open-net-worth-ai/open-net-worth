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
  levels: [{ kind: 'stop', price: 112, source: 'thesis', distance_pct: -18.2 }],
  actions: [
    { type: 'order', side: 'sell', quantity: 5, limit: 260 },
    { type: 'order', side: 'sell', quantity: 5, limit: 280 },
    { type: 'alert', when: 'below', price: 112 },
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

  it('shows the one call, then one button per action Lucius proposed, in its order', () => {
    renderWithComposer(TRIM)
    expect(screen.getByText('Trim')).toBeTruthy()
    expect(screen.getByText(/Up a lot\./)).toBeTruthy()
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Sell 5 at $260.00',
      'Sell 5 at $280.00',
      'Alert me below $112.00',
    ])
  })

  it('fills Lucius’s composer with the request and the stock, and opens it', () => {
    const opened: Event[] = []
    const onOpen = (e: Event) => opened.push(e)
    window.addEventListener('lx-dock-open', onOpen)
    const seen = renderWithComposer(TRIM)
    fireEvent.click(screen.getByRole('button', { name: 'Sell 5 at $280.00' }))
    expect(seen.pending).toBe('Sell 5 at a $280.00 limit, as you suggested.')
    expect(seen.mentions).toEqual([{ type: 'instrument', ref: 'inst-acme', label: 'ACME' }])
    fireEvent.click(screen.getByRole('button', { name: 'Alert me below $112.00' }))
    expect(seen.pending).toBe('Set a price alert: tell me if it falls to $112.00.')
    expect(opened).toHaveLength(2)
    window.removeEventListener('lx-dock-open', onOpen)
  })

  it('a call with no actions — the usual case — has nothing to tap', () => {
    renderWithComposer({ ...TRIM, action: 'hold', actions: [] })
    expect(screen.getByText('Hold')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
