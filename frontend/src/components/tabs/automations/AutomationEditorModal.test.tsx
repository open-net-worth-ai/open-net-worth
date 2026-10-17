// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { AutomationEditorModal } from './AutomationEditorModal'

afterEach(cleanup)

describe('AutomationEditorModal', () => {
  // ADR 2026-10-17: the websites a run may open unasked are the owner's list, never Lucius's.
  it('never pre-fills the websites list from a draft Lucius proposed', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        seed={{ name: 'Receipts', instructions: 'File receipts.', fetch_hosts: ['attacker.example'] }}
      />,
    )
    const field = screen.getByLabelText(/websites it may open/i) as HTMLInputElement
    expect(field.value).toBe('')
  })

  it('starts an untimed new Automation as on-demand, never an implicit schedule', () => {
    render(<AutomationEditorModal open onClose={vi.fn()} onSubmit={vi.fn()} />)

    expect(screen.getByText('Only when you ask')).toBeTruthy()
  })

  // ADR 2026-10-11: the instructions say what Lucius does; when it runs is set only here.
  it('never reads the timing out of the instructions — the trigger is set in its own fields', () => {
    render(<AutomationEditorModal open onClose={vi.fn()} onSubmit={vi.fn()} />)
    expect(document.getElementById('automation-schedule-type')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /edit trigger details/i })).toBeNull()
    fireEvent.change(screen.getByLabelText(/instructions/i), {
      target: { value: 'Every Monday at 8am, send me my weekly report.' },
    })
    expect(screen.getByText('Only when you ask')).toBeTruthy()
    expect(screen.queryByText(/reading the trigger/i)).toBeNull()
  })

  it('requires name, instructions and a complete trigger before saving', async () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{ kind: 'event', event: 'balance', when: {}, limit: 'day' }}
      />,
    )
    const save = screen.getByRole('button', { name: 'Create automation' })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'Cash floor' } })
    fireEvent.change(screen.getByLabelText(/instructions/i), { target: { value: 'Warn me.' } })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/set the account, direction and amount below/i)).toBeTruthy()
  })

  it('shows an unsupported persisted interval as Custom and never coerces it', () => {
    const onAskLucius = vi.fn()
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{ kind: 'schedule', type: 'interval', seconds: 900 }}
        onAskLucius={onAskLucius}
      />,
    )
    expect(screen.getByText('Every 15 minutes')).toBeTruthy()
    const repeats = screen.getByLabelText(/Repeats/) as HTMLSelectElement
    expect(repeats.value).toBe('custom')
    fireEvent.change(repeats, { target: { value: 'custom' } })
    // The draft is unsaved, so the prompt itself must say what it is — Lucius cannot look it up.
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('isn’t saved yet'))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('Name: untitled'))
    expect(screen.getByText('Every 15 minutes')).toBeTruthy()
  })

  it('shows a twelve-hour recurrence as Custom without an interval editor', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{ kind: 'schedule', type: 'interval', seconds: 43_200 }}
      />,
    )
    expect(screen.getByText('Every 12 hours')).toBeTruthy()
    expect((screen.getByLabelText(/Repeats/) as HTMLSelectElement).value).toBe('custom')
    expect(screen.queryByLabelText(/Interval/)).toBeNull()
  })

  it('offers Hourly as one preset without a second interval choice', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{ kind: 'schedule', type: 'interval', seconds: 3600 }}
      />,
    )
    expect((screen.getByLabelText(/Repeats/) as HTMLSelectElement).value).toBe('interval')
    expect(screen.queryByLabelText(/Interval/)).toBeNull()
  })

  it('keeps an exact Custom RRULE when its selected schedule tab is clicked again', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{
          kind: 'schedule',
          type: 'rrule',
          rrule: 'FREQ=HOURLY;INTERVAL=6',
          dtstart: '2026-08-02T09:00:00+08:00',
        }}
      />,
    )
    fireEvent.click(screen.getByRole('radio', { name: 'Schedule' }))

    expect((screen.getByLabelText(/Repeats/) as HTMLSelectElement).value).toBe('custom')
    expect(screen.getByText(/FREQ=HOURLY;INTERVAL=6/)).toBeTruthy()
  })

  it('allows a signed balance threshold for liability accounts', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{
          kind: 'event',
          event: 'balance',
          when: { account: 'acc_card', direction: 'below', amount: -500 },
          limit: 'day',
        }}
      />,
    )

    const amount = screen.getByLabelText(/^Amount/) as HTMLInputElement
    expect(amount.value).toBe('-500')
    expect(amount.getAttribute('min')).toBeNull()
  })

  it('suggests your own instruments for a ticker and keeps a free-text one', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        initialTrigger={{
          kind: 'event',
          event: 'price',
          when: { direction: 'below', value: 10 },
          limit: 'day',
        }}
        options={{
          accounts: [],
          instruments: ['AAPL', 'AMD', 'BTC', 'ONWAI', 'META', 'MSFT', 'ORCL', 'TSLA'].map(
            (ticker) => ({ id: ticker, label: ticker }),
          ),
        }}
      />,
    )

    const ticker = screen.getByRole('combobox', { name: /Ticker/ }) as HTMLInputElement
    fireEvent.focus(ticker)
    expect(
      within(screen.getByRole('listbox', { name: 'Ticker suggestions' })).getAllByRole('option'),
    ).toHaveLength(5)

    fireEvent.change(ticker, { target: { value: 'me' } })
    expect(
      within(screen.getByRole('listbox', { name: 'Ticker suggestions' })).getAllByRole('option'),
    ).toHaveLength(1)
    expect(screen.getByRole('option', { name: 'META' })).toBeTruthy()

    fireEvent.change(ticker, { target: { value: 'ZZNEW' } })
    expect(ticker.value).toBe('ZZNEW')
  })
})
