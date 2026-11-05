// @vitest-environment jsdom
//
// Needs attention lists the Portfolio Brief's open decisions, so it can never say "All clear"
// while Investments shows "1 to decide" (persona QA, 2026-10-14). It used to compute a
// concentration flag of its own, from a different denominator.

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { overviewCashflowCoreWidgetComponents } from './overviewCashflowCore'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

const CONCENTRATION = {
  id: 'concentration',
  kind: 'concentration',
  tone: 'warn',
  actionable: true,
  tag: 'Concentration',
  narrative: 'ACME is 44% of investments, 24 percentage points above the 20% guideline.',
}
const TODAY = {
  id: 'today',
  kind: 'today',
  tone: 'good',
  actionable: false,
  tag: 'Today',
  narrative: 'Portfolio is +$40.00 (+1.2%) today.',
}

const NO_FLAGS = {
  base_currency: 'USD',
  attention: {
    holdings_total: 0,
    debt_total: 0,
    contingent_total: 0,
    contingent_pct: 0,
    contingent_flagged: false,
    cash_total: 0,
    thin_cash: false,
    has_investments: false,
  },
}

function respond(
  brief: unknown,
  attention: Record<string, unknown> = {},
  extra: Record<string, unknown> = {},
) {
  authFetch.mockImplementation((url: string) => {
    const body = url.startsWith('/api/investments/brief')
      ? brief
      : url.startsWith('/api/portfolio/analytics')
        ? { ...NO_FLAGS, attention: { ...NO_FLAGS.attention, ...attention } }
        : (extra[url] ?? {})
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
  })
}

function renderAttention() {
  const Comp = overviewCashflowCoreWidgetComponents.attention!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'attention')!
  const instance: WidgetInstance = {
    instanceId: 'w-attention',
    defId: 'attention',
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config: {},
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('Needs attention and the Portfolio Brief', () => {
  it('lists an open portfolio decision instead of saying all clear', async () => {
    respond({ signals: [CONCENTRATION, TODAY], decisions_count: 1, base_currency: 'USD' })
    renderAttention()
    await waitFor(() => expect(screen.getByText(/Position concentration/)).toBeTruthy())
    expect(document.body.textContent).toMatch(/ACME is 44% of investments/)
    expect(screen.queryByText(/All clear/)).toBeNull()
    expect(screen.queryByText(/Portfolio is \+\$40/)).toBeNull()
  })

  it('says all clear only when the brief has nothing to decide', async () => {
    respond({ signals: [TODAY], decisions_count: 0, base_currency: 'USD' })
    renderAttention()
    await waitFor(() => expect(screen.getByText(/All clear/)).toBeTruthy())
  })
})

describe('Needs attention card bills', () => {
  const brief = { signals: [TODAY], decisions_count: 0, base_currency: 'USD' }
  const card = (label: string, days: number, amount: number, estimated = false) => ({
    date: '2026-10-20',
    label,
    account_id: 'acc-' + label,
    amount_base: amount,
    estimated,
    days_until_due: days,
  })

  // Owner decision (2026-10-16): a card balance alone asks nothing of you; a statement about
  // to fall due does. The server lists unpaid statements due within a week or overdue.
  it('flags each card statement due soon or overdue, with its amount and when', async () => {
    respond(
      brief,
      { debt_total: -392 },
      {
        '/api/commitments/summary': {
          events: [],
          cards_due_soon: [card('Master', -2, 300), card('Visa', 3, 6276.57, true)],
        },
      },
    )
    renderAttention()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/Visa: S?\$6,276\.57 due in 3 days/),
    )
    expect(document.body.textContent).toMatch(/Master: S?\$300\.00 overdue by 2 days/)
    expect(screen.getByText(/Estimated from the card's statement cycle\./)).toBeTruthy()
  })

  it('says nothing about a card balance when no statement is due soon', async () => {
    respond(brief, { debt_total: -392 })
    renderAttention()
    await waitFor(() => expect(screen.getByText(/All clear/)).toBeTruthy())
    expect(screen.queryByText(/outstanding/)).toBeNull()
  })
})

describe('Needs attention shares its list with the Lucius home', () => {
  it('counts overdue payments and transactions to review as flags', async () => {
    respond(
      { signals: [TODAY], decisions_count: 0, base_currency: 'USD' },
      {},
      {
        '/api/commitments/summary': { overdue_count: 8, overdue_total_base: 1200, events: [] },
        '/api/transactions/recently-added?needs_review=true&limit=1': { total: 5 },
      },
    )
    renderAttention()
    await waitFor(() => expect(screen.getByText(/8 overdue payments/)).toBeTruthy())
    expect(screen.getByText(/5 transactions to review/)).toBeTruthy()
    expect(screen.getByText(/2/, { selector: 'b' })).toBeTruthy()
    expect(screen.queryByText(/All clear/)).toBeNull()
  })
})
