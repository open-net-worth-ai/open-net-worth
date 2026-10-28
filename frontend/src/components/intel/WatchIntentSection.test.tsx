// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { ThesisContext } from '../../api/investmentTheses'

const contexts: Record<string, ThesisContext | null> = {}
const saveInvestmentThesis = vi.fn()
vi.mock('../../api/investmentTheses', () => ({
  fetchThesisFor: async ({ ticker }: { ticker: string }) => contexts[ticker] ?? null,
  saveInvestmentThesis: (...args: unknown[]) => saveInvestmentThesis(...args),
}))

import { WatchIntentSection } from './WatchIntentSection'

afterEach(cleanup)

const held = (ticker: string): ThesisContext => ({
  instrument_id: `inst_${ticker}`,
  currency: 'EUR',
  held: true,
  thesis: {
    id: 'th_1',
    instrument_id: `inst_${ticker}`,
    role: 'satellite',
    reason: 'Share gains keep compounding.',
    breaks_if: 'Margins stay under 45%.',
    stop_below: 120,
    buy_below: 150,
    buy_above: 130,
    fair_value: 190,
    trim_above: 220,
    created_at: '2026-10-27T00:00:00Z',
    updated_at: '2026-10-27T00:00:00Z',
  },
})

describe('WatchIntentSection', () => {
  it('reads the thesis first, like the card — no form until Edit', async () => {
    contexts.HELD1 = held('HELD1')
    const { findByText, getByText, queryByLabelText } = render(
      <WatchIntentSection sym="HELD1" price={160} onSaved={vi.fn()} />,
    )
    expect(await findByText('Share gains keep compounding.')).toBeTruthy()
    expect(getByText('Margins stay under 45%.')).toBeTruthy()
    expect(getByText('Satellite')).toBeTruthy()
    expect(getByText('€120.00')).toBeTruthy()
    expect(getByText('−25.0%')).toBeTruthy()
    expect(queryByLabelText('Why you own it')).toBeNull()
    expect(getByText(/alerts about your thesis/)).toBeTruthy()
  })

  it('Edit opens the same fields as the asset page card, prices grouped', async () => {
    contexts.HELD3 = held('HELD3')
    contexts.HELD3!.thesis!.trim_above = 2200
    const { findByText, getByLabelText, getByText } = render(
      <WatchIntentSection sym="HELD3" onSaved={vi.fn()} />,
    )
    fireEvent.click(await findByText('Edit'))
    expect((getByLabelText('Why you own it') as HTMLTextAreaElement).value).toBe(
      'Share gains keep compounding.',
    )
    expect((getByLabelText('Thesis breaks if') as HTMLTextAreaElement).value).toBe(
      'Margins stay under 45%.',
    )
    expect((getByLabelText('Stop below') as HTMLInputElement).value).toBe('120')
    expect((getByLabelText('Trim above') as HTMLInputElement).value).toBe('2,200')
    expect(
      getByLabelText('Stop below')
        .closest('.ui-money-field')
        ?.querySelector('.ui-money-field__prefix')?.textContent,
    ).toBe('€')
    fireEvent.click(getByText('Cancel'))
    expect(getByText('Share gains keep compounding.')).toBeTruthy()
  })

  it('a watched stock with no thesis offers one start button, then the watch fields only', async () => {
    contexts.WATCH1 = { instrument_id: 'inst_w', currency: 'USD', held: false, thesis: null }
    const { findByRole, getByLabelText, queryByLabelText, queryByText } = render(
      <WatchIntentSection sym="WATCH1" onSaved={vi.fn()} />,
    )
    const start = await findByRole('button', { name: 'Write down why you’d buy WATCH1' })
    expect(queryByText('Add')).toBeNull()
    fireEvent.click(start)
    expect(getByLabelText('Why you’d buy it')).toBeTruthy()
    expect(queryByLabelText('Stop below')).toBeNull()
    expect(queryByLabelText('Thesis breaks if')).toBeNull()
  })

  it('saves through the same editor and goes back to the read view with the new thesis', async () => {
    contexts.HELD2 = held('HELD2')
    saveInvestmentThesis.mockResolvedValue({ ...held('HELD2').thesis, reason: 'Cheaper now.' })
    const onSaved = vi.fn()
    const { findByText, getByLabelText, getByText } = render(
      <WatchIntentSection sym="HELD2" onSaved={onSaved} />,
    )
    fireEvent.click(await findByText('Edit'))
    fireEvent.change(getByLabelText('Why you own it'), { target: { value: 'Cheaper now.' } })
    fireEvent.click(getByText('Save thesis'))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(saveInvestmentThesis.mock.calls[0]?.[0]).toBe('inst_HELD2')
    expect(saveInvestmentThesis.mock.calls[0]?.[1]?.reason).toBe('Cheaper now.')
    expect(await findByText('Cheaper now.')).toBeTruthy()
  })

  it('says when the ticker has no instrument to hold a thesis yet', async () => {
    const { findByText } = render(<WatchIntentSection sym="NOPE1" onSaved={vi.fn()} />)
    expect(await findByText('Open NOPE1’s page once to start a thesis.')).toBeTruthy()
  })
})
