// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { WatchIntentSection } from './WatchIntentSection'

afterEach(cleanup)

function mount(onSave = vi.fn().mockResolvedValue(undefined), intent = null) {
  return {
    onSave,
    ...render(<WatchIntentSection sym="AMD" ccy="$" intent={intent} onSave={onSave} />),
  }
}

describe('WatchIntentSection', () => {
  it('commits on leaving a field, like the rest of the dialog — no Save button', async () => {
    const { getByLabelText, queryByText, onSave, findByText } = mount()
    expect(queryByText('Save')).toBeNull()
    const below = getByLabelText(/Buy at or under/)
    fireEvent.change(below, { target: { value: '90' } })
    fireEvent.blur(below)
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ buy_below: 90 }))
    expect(await findByText('Saved')).toBeTruthy()
    fireEvent.blur(below) // unchanged → no second write
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('refuses an inverted zone before it reaches the server', () => {
    const { getByLabelText, getByText, onSave } = mount()
    fireEvent.change(getByLabelText(/Buy at or under/), { target: { value: '70' } })
    const floor = getByLabelText(/but not under/)
    fireEvent.change(floor, { target: { value: '90' } })
    fireEvent.blur(floor)
    expect(getByText('The floor must be below the top of the zone.')).toBeTruthy()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('uses the thesis card’s wording, and its price boxes take only numbers', () => {
    const watched = mount()
    expect(watched.getByLabelText('Why you’d buy it')).toBeTruthy()
    const fair = watched.getByLabelText('Fair value ($)') as HTMLInputElement
    fireEvent.change(fair, { target: { value: 'abc1x5.0.5' } })
    expect(fair.value).toBe('15.05')
    cleanup()
    const held = render(
      <WatchIntentSection sym="AMD" ccy="$" held intent={null} onSave={vi.fn()} />,
    )
    expect(held.getByLabelText('Why you own it')).toBeTruthy()
    expect(held.getByText(/alerts about your thesis/)).toBeTruthy()
  })

  it('shows the server’s message when a save fails', async () => {
    const onSave = vi
      .fn()
      .mockRejectedValue(new Error('intent: fair_value must be a positive price'))
    const { getByLabelText, findByText } = mount(onSave)
    const fair = getByLabelText(/Fair value/)
    fireEvent.change(fair, { target: { value: '150' } })
    fireEvent.blur(fair)
    expect(await findByText(/fair_value must be a positive price/)).toBeTruthy()
  })
})
