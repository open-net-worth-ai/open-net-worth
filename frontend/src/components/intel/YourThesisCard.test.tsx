// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { InvestmentThesis } from '../../types/assetDetail'

const saveInvestmentThesis = vi.fn()
let failWith: string | null = null
vi.mock('../../api/investmentTheses', () => ({
  saveInvestmentThesis: async (...args: unknown[]) => {
    if (failWith) throw new Error(failWith)
    return saveInvestmentThesis(...args)
  },
}))

import { YourThesisCard } from './YourThesisCard'
import { groupPrice } from './thesisFields'

afterEach(cleanup)
beforeEach(() => {
  saveInvestmentThesis.mockReset()
  failWith = null
})

const THESIS: InvestmentThesis = {
  id: 'hp_1',
  instrument_id: 'inst_acme',
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
}

function mount(props: Partial<Parameters<typeof YourThesisCard>[0]> = {}) {
  const onSaved = vi.fn()
  const utils = render(
    <YourThesisCard
      instrumentId="inst_acme"
      ticker="ACME"
      thesis={THESIS}
      held
      price={162.4}
      priceMaxFrac={2}
      onSaved={onSaved}
      {...props}
    />,
  )
  return { onSaved, ...utils }
}

describe('YourThesisCard', () => {
  it('shows a holding’s role, thesis, breaks-if and every level with its distance', () => {
    const { getByText } = mount()
    expect(getByText('Satellite')).toBeTruthy()
    expect(getByText('Share gains keep compounding.')).toBeTruthy()
    expect(getByText('Margins stay under 45%.')).toBeTruthy()
    for (const label of ['Stop below', 'Buy zone', 'Fair value', 'Trim above']) {
      expect(getByText(label)).toBeTruthy()
    }
    expect(getByText('−26.1%')).toBeTruthy()
    expect(getByText('+35.5%')).toBeTruthy()
  })

  it('hides the held-only parts on a watched instrument', () => {
    const { queryByText, getByText } = mount({ held: false })
    expect(queryByText('Satellite')).toBeNull()
    expect(queryByText('Margins stay under 45%.')).toBeNull()
    expect(queryByText('Stop below')).toBeNull()
    expect(queryByText('Trim above')).toBeNull()
    expect(getByText('Buy zone')).toBeTruthy()
  })

  it('invites a first thesis when there is none', () => {
    const { getByText } = mount({ thesis: null })
    expect(getByText('Write down why you own ACME.')).toBeTruthy()
    expect(getByText('Add')).toBeTruthy()
  })

  it('says which currency the levels are in', () => {
    const { getByText, getByLabelText } = mount({ currency: 'EUR' })
    expect(getByText('€120.00')).toBeTruthy()
    fireEvent.click(getByText('Edit'))
    expect(getByText('Prices in EUR')).toBeTruthy()
    expect(getByLabelText('Stop below')).toBeTruthy()
  })

  it('opens a new holding’s thesis on Core instead of an empty role', async () => {
    saveInvestmentThesis.mockResolvedValue({ ...THESIS, role: 'core', reason: 'Why not.' })
    const { getByText, getByLabelText, getByRole, onSaved } = mount({ thesis: null })
    fireEvent.click(getByText('Add'))
    expect(getByRole('tab', { name: 'Core' }).getAttribute('aria-selected')).toBe('true')
    fireEvent.change(getByLabelText('Why you own it'), { target: { value: 'Why not.' } })
    fireEvent.click(getByText('Save thesis'))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(saveInvestmentThesis.mock.calls[0]?.[1]?.role).toBe('core')
  })

  it('saves the edited thesis and keeps a watched card’s hidden fields', async () => {
    saveInvestmentThesis.mockResolvedValue({ ...THESIS, reason: 'Cheaper now.' })
    const { getByText, getByLabelText, onSaved } = mount({ held: false })
    fireEvent.click(getByText('Edit'))
    fireEvent.change(getByLabelText('Why you’d buy it'), { target: { value: 'Cheaper now.' } })
    fireEvent.click(getByText('Save thesis'))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(saveInvestmentThesis).toHaveBeenCalledWith('inst_acme', {
      role: 'satellite',
      reason: 'Cheaper now.',
      breaks_if: 'Margins stay under 45%.',
      stop_below: 120,
      trim_above: 220,
      buy_below: 150,
      buy_above: 130,
      fair_value: 190,
    })
    expect(getByText('Cheaper now.')).toBeTruthy()
  })

  it('refuses an inverted buy zone before it reaches the server', () => {
    const { getByText, getByLabelText } = mount()
    fireEvent.click(getByText('Edit'))
    fireEvent.change(getByLabelText('…but not under'), { target: { value: '160' } })
    expect(getByText('The floor must be below the top of the buy zone.')).toBeTruthy()
    fireEvent.click(getByText('Save thesis'))
    expect(saveInvestmentThesis).not.toHaveBeenCalled()
  })

  it('keeps only numbers in a price box and still refuses zero', () => {
    const { getByText, getByLabelText } = mount()
    fireEvent.click(getByText('Edit'))
    const stop = getByLabelText('Stop below') as HTMLInputElement
    fireEvent.change(stop, { target: { value: 'fdsfds' } })
    expect(stop.value).toBe('')
    fireEvent.change(stop, { target: { value: '1,2a0.5.5' } })
    expect(stop.value).toBe('1,20.55')
    fireEvent.change(stop, { target: { value: '0' } })
    expect(getByText('Stop below must be a positive number.')).toBeTruthy()
    fireEvent.click(getByText('Save thesis'))
    expect(saveInvestmentThesis).not.toHaveBeenCalled()
  })

  it('groups a price once you leave the box, and saves the plain number', async () => {
    saveInvestmentThesis.mockResolvedValue(THESIS)
    const { getByText, getByLabelText } = mount()
    fireEvent.click(getByText('Edit'))
    const fair = getByLabelText('Fair value') as HTMLInputElement
    fireEvent.change(fair, { target: { value: '232323223' } })
    expect(fair.value).toBe('232323223')
    fireEvent.blur(fair)
    expect(fair.value).toBe('232,323,223')
    fireEvent.click(getByText('Save thesis'))
    await waitFor(() => expect(saveInvestmentThesis).toHaveBeenCalled())
    expect(saveInvestmentThesis.mock.calls[0]?.[1]?.fair_value).toBe(232323223)
  })

  it('shows the server’s reason when a save fails', async () => {
    failWith = 'Your stop must be below your trim level.'
    const { getByText, findByText } = mount()
    fireEvent.click(getByText('Edit'))
    fireEvent.click(getByText('Save thesis'))
    expect(await findByText('Your stop must be below your trim level.')).toBeTruthy()
  })
})

describe('groupPrice', () => {
  it('groups the whole part and keeps the decimals as typed', () => {
    expect(groupPrice('232323223')).toBe('232,323,223')
    expect(groupPrice('1234.5')).toBe('1,234.5')
    expect(groupPrice('1,2345')).toBe('12,345')
    expect(groupPrice('999')).toBe('999')
    expect(groupPrice('0.00012')).toBe('0.00012')
    expect(groupPrice('')).toBe('')
    expect(groupPrice('1.2.3')).toBe('1.2.3')
  })
})
