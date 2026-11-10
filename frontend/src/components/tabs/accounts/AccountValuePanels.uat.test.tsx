// @vitest-environment jsdom
// PR #93 user-acceptance scenarios for the account card, footer and notices (UI-01..UI-18).
// Extends AccountValuePanels.test.tsx: every value_sync status, paused / removed connectors, the
// >10% holdings gap, a negative untracked part, base vs native figures. Exact text, to the cent.
// Synthetic figures only (ACME book, 123,456.78).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { AccountValueCard, AccountValueFooter, AccountValueNotice } from './AccountValuePanels'
import { fromApiAccount } from './data'
import type { ApiAccount, ApiValueSync } from './types'
import { setBaseCurrency } from '../../../utils/format'

const ORIGINAL_TZ = process.env.TZ

beforeAll(() => {
  process.env.TZ = 'UTC'
  setBaseCurrency('USD')
})
afterAll(() => {
  if (ORIGINAL_TZ === undefined) delete process.env.TZ
  else process.env.TZ = ORIGINAL_TZ
})
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 12))
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

const PLAID = {
  source: 'plaid',
  ref: 'pa-1',
  name: 'Brokerage',
  mask: '4321',
  linked_at: '2026-11-01T00:00:00Z',
  provider_label: 'Plaid',
  status: 'connected' as const,
}

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Brokerage X',
    kind: 'asset',
    type: 'investment',
    institution: 'Acme Securities',
    currency: 'USD',
    initial_balance_date: '2026-11-01',
    external_ids: [PLAID],
    balance: 123456.78,
    balance_base: 123456.78,
    cleared_balance: 123456.78,
    cleared_balance_base: 123456.78,
    value_source: 'total',
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 123456.78,
      changed_at: '2026-11-10T10:00:00+00:00',
    },
    value_siblings: [],
    value_breakdown: {
      tracked_holdings: 0,
      untracked: 123456.78,
      total: 123456.78,
      currency: 'USD',
    },
    ...over,
  }
}

const acct = (over: Partial<ApiAccount> = {}, holdingsBase = 0) =>
  fromApiAccount(api(over), holdingsBase)

function sync(over: Partial<ApiValueSync>, extra: Partial<ApiAccount> = {}): Partial<ApiAccount> {
  return {
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 123456.78,
      changed_at: '2026-11-10T10:00:00+00:00',
      ...over,
    },
    ...extra,
  }
}

function link(status: 'paused' | 'disconnected'): Partial<ApiAccount> {
  return { external_ids: [{ ...PLAID, status }] }
}

function renderNotice(a: ReturnType<typeof acct>) {
  const props = {
    account: a,
    onOpenConnector: vi.fn(),
    onEdit: vi.fn(),
    onReviewHoldings: vi.fn(),
    onConfirmHeld: vi.fn(),
  }
  const view = render(<AccountValueNotice {...props} />)
  const text = () => view.container.querySelector('[role="status"]')?.textContent ?? null
  return { props, text, view }
}

const buttons = () => screen.queryAllByRole('button').map((b) => b.textContent)

describe('the footer, one line per state (UI-07 / UI-18)', () => {
  function footer(a: ReturnType<typeof acct>) {
    const { container } = render(<AccountValueFooter account={a} />)
    return {
      text: screen.getByRole('status').textContent,
      warn: !!container.querySelector('.acct-src-dot--warn'),
    }
  }

  it.each([
    [
      'waiting',
      sync({ status: 'waiting', total: undefined, changed_at: undefined }),
      'Waiting for Plaid’s first total',
      false,
    ],
    ['fresh', sync({ status: 'fresh' }), 'Value synced via Plaid · last changed Nov 10', false],
    ['stale', sync({ status: 'stale' }), 'Value synced via Plaid · last changed Nov 10', true],
    ['error', sync({ status: 'error' }), 'Value synced via Plaid · last changed Nov 10', true],
    [
      'reconnect',
      sync({ status: 'reconnect' }),
      'Value synced via Plaid · last changed Nov 10',
      true,
    ],
    [
      'currency_mismatch',
      sync({ status: 'currency_mismatch' }),
      'Value synced via Plaid · last changed Nov 10',
      true,
    ],
    [
      'no_balance',
      sync({ status: 'no_balance' }),
      'Value synced via Plaid · last changed Nov 10',
      true,
    ],
    [
      'not_found',
      sync({ status: 'not_found' }),
      'Value synced via Plaid · last changed Nov 10',
      true,
    ],
    [
      'held_back after a total',
      sync({ status: 'held_back', held_total: 0 }),
      'Value synced via Plaid · last changed Nov 10',
      true,
    ],
    [
      'held_back, first total',
      sync({ status: 'held_back', held_total: 0, total: undefined, changed_at: undefined }),
      'Value you entered · Nov 1',
      true,
    ],
  ])('%s', (_n, over, text, warn) => {
    expect(footer(acct(over))).toEqual({ text, warn })
  })

  it('"kept" dates from the starting balance, never from the reading, and is not a warning', () => {
    expect(footer(acct(sync({ status: 'kept', total: undefined })))).toEqual({
      text: 'Value you kept · Nov 1',
      warn: false,
    })
  })

  it('a kept total dates from the last reconcile when there is one', () => {
    const a = acct({ ...sync({ status: 'kept' }), last_reconciled_at: '2026-10-20' })
    expect(footer(a).text).toBe('Value you kept · Oct 20')
  })

  it('an unlinked typed total says "Value you entered", never "synced"', () => {
    const f = footer(acct({ external_ids: [], value_sync: null }))
    expect(f.text).toBe('Value you entered · Nov 1')
    expect(f.warn).toBe(false)
  })

  it.each([
    ['paused', link('paused')],
    ['removed', link('disconnected')],
  ])('a %s connector reads "Last total from Plaid" as a warning', (_n, over) => {
    expect(footer(acct(over))).toEqual({ text: 'Last total from Plaid · Nov 10', warn: true })
  })

  it('a paused connector that never produced a total says "Value you entered"', () => {
    const a = acct({
      ...link('paused'),
      ...sync({ status: 'waiting', total: undefined, changed_at: undefined }),
    })
    expect(footer(a).text).toBe('Value you entered · Nov 1')
  })
})

describe('the card: figures and labels (UI-01 / UI-02 / UI-05 / UI-06)', () => {
  function card(a: ReturnType<typeof acct>, over = {}) {
    const props = { onAddHolding: vi.fn(), onSwitch: vi.fn(), ...over }
    const view = render(<AccountValueCard account={a} {...props} />)
    return { props, view }
  }

  it('a linked total with nothing tracked: Total value $123,456.78 alone, no split', () => {
    card(acct())
    expect(screen.getByText('Total value')).toBeTruthy()
    expect(screen.getByText('$123,456.78')).toBeTruthy()
    expect(screen.queryByText('Tracked holdings')).toBeNull()
    expect(screen.queryByText('Cash and untracked holdings')).toBeNull()
    expect(screen.getByText('Add holdings for live prices, gains and allocation')).toBeTruthy()
  })

  it('tracked + untracked = total, with the same text under each figure', () => {
    const { view } = card(acct({ balance: 4980, balance_base: 4980 }, 3020))
    const parts = [...view.container.querySelectorAll('.acct-bal-part')].map((p) => [
      p.querySelector('.acct-bal-fig')?.textContent,
      p.querySelector('.acct-bal-lbl')?.textContent,
    ])
    expect(parts).toEqual([
      ['$3,020.00', 'Tracked holdings'],
      ['$4,980.00', 'Cash and untracked holdings'],
      ['$8,000.00', 'Total value'],
    ])
    expect(view.container.querySelectorAll('.acct-bal-op')).toHaveLength(2)
    expect(screen.queryByText('Add holdings for live prices, gains and allocation')).toBeNull()
  })

  it('a negative untracked part shows -$50.00 in red and a total of $9,750.00', () => {
    const { view } = card(acct({ balance: -50, balance_base: -50 }, 9800))
    const figs = [...view.container.querySelectorAll('.acct-bal-fig')]
    expect(figs.map((f) => f.textContent)).toEqual(['$9,800.00', '-$50.00', '$9,750.00'])
    expect(figs[1]!.className).toContain('neg')
    expect(figs[0]!.className).not.toContain('neg')
  })

  it('a waiting total shows a dash, no $0.00, no Add holding, no switch prompt', () => {
    card(acct({ ...sync({ status: 'waiting', total: undefined }), balance: 0, balance_base: 0 }))
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.queryByText('$0.00')).toBeNull()
    expect(buttons()).not.toContain('Add holding')
    expect(buttons()).not.toContain('Switch')
  })

  it('base vs native: with no holdings an EUR account reads in euros; with holdings, in USD', () => {
    const eurOnly = acct({
      currency: 'EUR',
      balance: 10000,
      balance_base: 11000,
      value_breakdown: { tracked_holdings: 0, untracked: 10000, total: 10000, currency: 'EUR' },
    })
    const { view } = card(eurOnly)
    expect(view.container.querySelector('.acct-src-one .acct-bal-fig')?.textContent).toBe(
      '€10,000.00',
    )
    cleanup()
    const withHoldings = acct({ currency: 'EUR', balance: 7254.55, balance_base: 7980.01 }, 3020)
    const { view: v2 } = card(withHoldings)
    expect([...v2.container.querySelectorAll('.acct-bal-fig')].map((f) => f.textContent)).toEqual([
      '$3,020.00',
      '$7,980.01',
      '$11,000.01',
    ])
  })

  it('Add holding calls its handler once', () => {
    const { props } = card(acct())
    fireEvent.click(screen.getByRole('button', { name: 'Add holding' }))
    expect(props.onAddHolding).toHaveBeenCalledTimes(1)
  })

  it('UI-03: names the untracked sliver to the cent and offers the switch', () => {
    const { props } = card(acct({ balance: 60, balance_base: 60 }, 3020))
    expect(screen.getByText(/Only/).textContent).toBe(
      'Only $60.00 is untracked. Switch to live holdings?',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }))
    expect(props.onSwitch).toHaveBeenCalledTimes(1)
    expect(buttons()).not.toContain('Add holding')
  })

  it('UI-04: a negative untracked part gets Add holding, not the switch prompt', () => {
    card(acct({ balance: -50, balance_base: -50 }, 9800))
    expect(buttons()).toContain('Add holding')
    expect(buttons()).not.toContain('Switch')
  })

  it('UI-06: a Live holdings account is Holdings + Cash and has no total-only prompts', () => {
    const { view } = card(acct({ value_source: 'holdings', balance: 500, balance_base: 500 }, 3020))
    expect(
      [...view.container.querySelectorAll('.acct-bal-part')].map((p) => p.textContent),
    ).toEqual(['$3,020.00Holdings', '$500.00Cash', '$3,520.00Total value'])
    expect(buttons()).not.toContain('Switch')
    expect(buttons()).not.toContain('Add holding')
    expect(screen.queryByRole('button', { name: 'What these figures mean' })).toBeNull()
  })

  it('a total account carries the "What these figures mean" help', () => {
    card(acct())
    expect(screen.getByRole('button', { name: 'What these figures mean' })).toBeTruthy()
  })
})

describe('notices: every state says its own thing (UI-08..UI-15)', () => {
  it('held_back: names the reported figure, asks, and Keep / Use confirm accept false / true', () => {
    const a = acct({
      ...sync({ status: 'held_back', total: 500000, held_total: 5000 }),
      balance: 500000,
      balance_base: 500000,
      value_breakdown: { tracked_holdings: 0, untracked: 500000, total: 500000, currency: 'USD' },
    })
    const { props, text } = renderNotice(a)
    expect(text()).toContain(
      '●Acme Securities reported $5,000.00. That looks wrong, so it wasn’t used. Is it real?',
    )
    expect(buttons()).toEqual(['Keep $500,000.00', 'Use $5,000.00'])
    fireEvent.click(screen.getByRole('button', { name: 'Keep $500,000.00' }))
    expect(props.onConfirmHeld).toHaveBeenLastCalledWith(false)
    fireEvent.click(screen.getByRole('button', { name: 'Use $5,000.00' }))
    expect(props.onConfirmHeld).toHaveBeenLastCalledWith(true)
    expect(props.onConfirmHeld).toHaveBeenCalledTimes(2)
  })

  it('held_back at 0.00 against 123,456.78: "Keep $123,456.78" / "Use $0.00"', () => {
    renderNotice(acct(sync({ status: 'held_back', held_total: 0 })))
    expect(buttons()).toEqual(['Keep $123,456.78', 'Use $0.00'])
  })

  it('held_back keeps the account figure in the account’s own currency (EUR)', () => {
    const a = acct({
      ...sync({ status: 'held_back', total: 10000, held_total: 0 }),
      currency: 'EUR',
      balance: 10000,
      balance_base: 11000,
      value_breakdown: { tracked_holdings: 0, untracked: 10000, total: 10000, currency: 'EUR' },
    })
    renderNotice(a)
    expect(buttons()).toEqual(['Keep €10,000.00', 'Use €0.00'])
  })

  it('held_back with no held figure shows no notice', () => {
    const { text } = renderNotice(acct(sync({ status: 'held_back' })))
    expect(text()).toBeNull()
  })

  it('reconnect: says the sign-in expired and what the account keeps, and Reconnect opens the connector', () => {
    const { props, text } = renderNotice(acct(sync({ status: 'reconnect' })))
    expect(text()).toContain(
      'Your sign-in to Acme Securities has expired. Reconnect to keep this account up to date. Until then it keeps $123,456.78 from Nov 10.',
    )
    expect(buttons()).toEqual(['Reconnect'])
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(props.onOpenConnector).toHaveBeenCalledTimes(1)
  })

  it('currency_mismatch with a reported code names it; Edit account opens the editor', () => {
    const { props, text } = renderNotice(
      acct(sync({ status: 'currency_mismatch', reported_currency: 'EUR' })),
    )
    expect(text()).toContain(
      'Acme Securities reports this account in EUR, but it’s set up in USD. Nothing was booked, so the value hasn’t changed.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Edit account' }))
    expect(props.onEdit).toHaveBeenCalledTimes(1)
  })

  it('currency_mismatch with no clean code says "another currency"', () => {
    const { text } = renderNotice(acct(sync({ status: 'currency_mismatch' })))
    expect(text()).toContain('reports this account in another currency, but it’s set up in USD.')
  })

  it.each([
    ['error', 'Plaid couldn’t be read, so it keeps $123,456.78. It will try again.'],
    [
      'no_balance',
      'Plaid didn’t report a total for this account, so it keeps $123,456.78. It will try again.',
    ],
    [
      'not_found',
      'Plaid no longer lists this account, so it keeps $123,456.78. It will try again.',
    ],
  ] as const)('%s: its own copy, with Open Plaid', (status, copy) => {
    const { props, text } = renderNotice(acct(sync({ status })))
    expect(text()).toContain(copy)
    expect(buttons()).toEqual(['Open Plaid'])
    fireEvent.click(screen.getByRole('button', { name: 'Open Plaid' }))
    expect(props.onOpenConnector).toHaveBeenCalledTimes(1)
  })

  it('stale: says the total has not changed since the date', () => {
    const { text } = renderNotice(
      acct(sync({ status: 'stale', changed_at: '2026-11-02T10:00:00+00:00' })),
    )
    expect(text()).toContain(
      'Acme Securities’s total hasn’t changed since Nov 2, so this value may be out of date.',
    )
    expect(buttons()).toEqual(['Open Plaid'])
  })

  it.each([
    ['paused', link('paused'), 'Plaid is paused, so this account isn’t updating.'],
    ['removed', link('disconnected'), 'Plaid is removed, so this account isn’t updating.'],
  ])(
    'a %s connector: keeps the total, says to reconcile, offers no Keep / Use',
    (_n, over, lead) => {
      const { text } = renderNotice(acct(over))
      expect(text()).toContain(`${lead} It keeps $123,456.78. Reconcile to update it yourself.`)
      expect(buttons()).toEqual(['Open Plaid'])
    },
  )

  it('a paused connector wins over a held-back total: no Keep / Use while nothing is updating', () => {
    const a = acct({ ...link('paused'), ...sync({ status: 'held_back', held_total: 0 }) })
    renderNotice(a)
    expect(buttons()).toEqual(['Open Plaid'])
    expect(screen.queryByText(/reported/)).toBeNull()
  })

  it.each(['waiting', 'fresh', 'kept'] as const)('%s: no notice at all', (status) => {
    const { text } = renderNotice(acct(sync({ status })))
    expect(text()).toBeNull()
  })

  it('an unlinked typed total: no notice', () => {
    const { text } = renderNotice(acct({ external_ids: [], value_sync: null }))
    expect(text()).toBeNull()
  })
})

describe('notice: holdings beyond the typed total', () => {
  it('names the holdings, the excess and the total, to the cent', () => {
    const a = acct({ balance: -50, balance_base: -50, external_ids: [], value_sync: null }, 9800)
    const { props, text } = renderNotice(a)
    expect(text()).toContain(
      'Your holdings add up to $9,800.00, $50.00 more than the total of $9,750.00. Check for a typo, or the total may be out of date.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Review holdings' }))
    expect(props.onReviewHoldings).toHaveBeenCalledTimes(1)
  })

  it('a zero untracked part is not a problem', () => {
    const { text } = renderNotice(acct({ balance: 0, balance_base: 0 }, 3020))
    expect(text()).toBeNull()
  })
})

describe('notice: a Live holdings account far from the reported total (UI-13)', () => {
  const live = (reported: number, mineHoldings: number, over: Partial<ApiAccount> = {}) =>
    acct(
      {
        value_source: 'holdings',
        balance: 0,
        balance_base: 0,
        value_sync: {
          status: 'compare',
          source: 'plaid',
          provider_label: 'Plaid',
          reported_total: reported,
        },
        ...over,
      },
      mineHoldings,
    )

  it('reported far above: "You may be missing a holding"', () => {
    const { props, text } = renderNotice(live(10000, 3020))
    expect(text()).toContain(
      'Acme Securities reports $10,000.00, but your holdings and cash add up to $3,020.00. You may be missing a holding.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Review holdings' }))
    expect(props.onReviewHoldings).toHaveBeenCalledTimes(1)
  })

  it('reported far below: "counting a holding twice"', () => {
    const { text } = renderNotice(live(1000, 3020))
    expect(text()).toContain('You may be counting a holding twice.')
  })

  it('a gap of exactly 10% stays quiet; one cent more speaks', () => {
    expect(renderNotice(live(1000, 900)).text()).toBeNull()
    cleanup()
    expect(renderNotice(live(1000, 899.99)).text()).toContain('reports $1,000.00')
  })

  it('stays quiet within tolerance (3,300.00 vs 3,020.00)', () => {
    expect(renderNotice(live(3300, 3020)).text()).toBeNull()
  })

  it('stays quiet for a foreign-currency account (the reported figure is native, holdings are base)', () => {
    expect(renderNotice(live(10000, 3020, { currency: 'EUR' })).text()).toBeNull()
  })

  it('stays quiet when other accounts share the institution', () => {
    expect(renderNotice(live(10000, 3020, { value_siblings: ['Brokerage Y'] })).text()).toBeNull()
  })

  it('stays quiet with no reported total', () => {
    const a = live(0, 3020, {
      value_sync: { status: 'compare', source: 'plaid', provider_label: 'Plaid' },
    })
    expect(renderNotice(a).text()).toBeNull()
  })

  it('a Brokerage total account on compare never raises the holdings-gap notice', () => {
    const a = live(10000, 3020, { value_source: 'total' })
    expect(renderNotice(a).text()).toBeNull()
  })
})
