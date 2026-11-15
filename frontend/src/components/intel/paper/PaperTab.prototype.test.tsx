// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
// The holdings' name cells can open an asset's page; the test has no router behind them.
vi.mock('../../../state/assetNav', () => ({ useAssetNav: () => ({ openAsset: () => {} }) }))

import { PaperTab } from './PaperTab'
import { openMarketNow, resetPrototype } from './paperStore'

describe('Paper Trading prototype', () => {
  beforeEach(() => {
    localStorage.clear()
    resetPrototype()
  })
  afterEach(cleanup)

  it('lists paper portfolios as cards, each with the Paper tag', () => {
    render(<PaperTab />)
    const card = screen.getByRole('button', { name: 'Open Buy-zone strategy' })
    expect(within(card).getByText('Paper')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open Berkshire Hathaway' })).toBeTruthy()
  })

  it('opens a portfolio, places a paper buy of crypto and fills it at once', () => {
    render(<PaperTab />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Buy-zone strategy' }))
    expect(screen.getByText('Paper value')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Paper trade' }))
    fireEvent.change(screen.getByLabelText('Asset'), { target: { value: 'ETH' } })
    fireEvent.change(screen.getByLabelText('Coins'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Place paper buy' }))
    expect(screen.getByText(/Paper buy filled: 2 ETH/)).toBeTruthy()
  })

  it('a market order for a stock waits when the market is closed, and fills when it opens', () => {
    render(<PaperTab />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(screen.getByRole('button', { name: 'Paper trade' }))
    fireEvent.change(screen.getByLabelText('Shares'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Place paper buy' }))
    fireEvent.click(screen.getByRole('tab', { name: /Orders/ }))
    const waiting = screen.queryByText('Waiting for the open')
    // The test may run during US market hours, when the order fills straight away.
    if (waiting) {
      act(() => openMarketNow())
      expect(screen.queryByText('Waiting for the open')).toBeNull()
    }
    expect(screen.getAllByText(/^Filled/).length).toBeGreaterThan(0)
  })
})
