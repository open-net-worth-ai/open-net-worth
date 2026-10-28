// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { HoldingPlan } from '../../types/assetDetail'

const saveHoldingPlan = vi.fn()
let failWith: string | null = null
vi.mock('../../api/holdingPlans', () => ({
  saveHoldingPlan: async (...args: unknown[]) => {
    if (failWith) throw new Error(failWith)
    return saveHoldingPlan(...args)
  },
}))

import { YourPlanCard } from './YourPlanCard'

afterEach(cleanup)
beforeEach(() => {
  saveHoldingPlan.mockReset()
  failWith = null
})

const PLAN: HoldingPlan = {
  id: 'hp_1',
  instrument_id: 'inst_acme',
  role: 'satellite',
  thesis: 'Share gains keep compounding.',
  exit_if: 'Margins stay under 45%.',
  stop_below: 120,
  buy_below: 150,
  buy_above: 130,
  fair_value: 190,
  trim_above: 220,
  created_at: '2026-10-27T00:00:00Z',
  updated_at: '2026-10-27T00:00:00Z',
}

function mount(props: Partial<Parameters<typeof YourPlanCard>[0]> = {}) {
  const onSaved = vi.fn()
  const utils = render(
    <YourPlanCard
      instrumentId="inst_acme"
      ticker="ACME"
      plan={PLAN}
      held
      price={162.4}
      priceMaxFrac={2}
      onSaved={onSaved}
      {...props}
    />,
  )
  return { onSaved, ...utils }
}

describe('YourPlanCard', () => {
  it('shows a holding’s role, thesis, sell condition and every level with its distance', () => {
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

  it('invites a first plan when there is none', () => {
    const { getByText } = mount({ plan: null })
    expect(getByText('Write down why you own ACME.')).toBeTruthy()
    expect(getByText('Add')).toBeTruthy()
  })

  it('saves the edited plan and keeps a watched card’s hidden fields', async () => {
    saveHoldingPlan.mockResolvedValue({ ...PLAN, thesis: 'Cheaper now.' })
    const { getByText, getByLabelText, onSaved } = mount({ held: false })
    fireEvent.click(getByText('Edit'))
    fireEvent.change(getByLabelText('Why you’d buy it'), { target: { value: 'Cheaper now.' } })
    fireEvent.click(getByText('Save plan'))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(saveHoldingPlan).toHaveBeenCalledWith('inst_acme', {
      role: 'satellite',
      thesis: 'Cheaper now.',
      exit_if: 'Margins stay under 45%.',
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
    fireEvent.click(getByText('Save plan'))
    expect(saveHoldingPlan).not.toHaveBeenCalled()
  })

  it('refuses a price it cannot read instead of clearing that level', () => {
    const { getByText, getByLabelText } = mount()
    fireEvent.click(getByText('Edit'))
    fireEvent.change(getByLabelText('Stop below'), { target: { value: '12abc' } })
    expect(getByText('Stop below must be a positive number.')).toBeTruthy()
    fireEvent.click(getByText('Save plan'))
    expect(saveHoldingPlan).not.toHaveBeenCalled()
  })

  it('shows the server’s reason when a save fails', async () => {
    failWith = 'Your stop must be below your trim level.'
    const { getByText, findByText } = mount()
    fireEvent.click(getByText('Edit'))
    fireEvent.click(getByText('Save plan'))
    expect(await findByText('Your stop must be below your trim level.')).toBeTruthy()
  })
})
