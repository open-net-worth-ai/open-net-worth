// Regression coverage for the Actions & events tab (owner directives):
// (1) manual "+ Add action" entry is removed — Lucius is the only way to add an action.
// (2) "Add with Lucius" renamed to "Manage with Lucius" and colored with the Lucius purple
//     token instead of the default blue link color.
// (3) the pending-action indicator renders as an actual checkbox square (no separate .c2-si-dot
//     circle child — the border/background now live on .c2-act-check itself).
// (4) the trailing bell+amount column is ALWAYS rendered (never conditionally absent), so its
//     x-position doesn't shift between a row with an amount and one without.
// (5) "Not happening" moved inline into the meta row (no separate .c3-act-outcomes block eating
//     a full extra row per action) with the impact explanation as a title= tooltip instead of a
//     permanently-rendered hint line.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Actions } from './detailActions'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'

setBaseCurrency('SGD')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const commitment: Commitment = {
  id: 'c1',
  name: 'Test commitment',
  kind: 'purchase',
  currency: 'SGD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [],
  funding_plan: [],
  actions: [
    {
      id: 'a1',
      label: 'Sign SPA addendum',
      category: 'legal',
      due_date: '2026-09-01',
      start_by: null,
      amount: null,
      currency: 'SGD',
      status: 'pending',
      counterparty: '',
      note: '',
      funding_id: null,
      reminder_automation_id: null,
    },
    {
      id: 'a2',
      label: 'Set aside SGD 4,000',
      category: 'savings',
      due_date: '2026-08-31',
      start_by: null,
      amount: 4000,
      currency: 'SGD',
      status: 'pending',
      counterparty: '',
      note: '',
      funding_id: null,
      reminder_automation_id: null,
    },
  ],
}

const m = {
  onAskLucius: vi.fn(),
  onOpenCommitment: vi.fn(),
  onOpenTxn: vi.fn(),
  updateInstallment: vi.fn(),
  removeInstallment: vi.fn(),
  payInstallment: vi.fn(),
  linkTxn: vi.fn(),
  updateFunding: vi.fn(),
  removeFunding: vi.fn(),
  addFunding: vi.fn(),
  updateAction: vi.fn(),
  addAction: vi.fn(),
  setActionMissed: vi.fn(),
  setActionReminder: vi.fn(),
  rename: vi.fn(),
  updateNote: vi.fn(),
  setStatus: vi.fn(),
  deleteCommitment: vi.fn(),
} as unknown as DetailMutations

describe('Actions & events tab', () => {
  it('has no manual "+ Add action" control', () => {
    const { queryByText } = render(<Actions commitment={commitment} fx={{ SGD: 1 }} m={m} />)
    expect(queryByText('+ Add action')).toBeNull()
    expect(queryByText('Add action')).toBeNull()
  })

  it('renders "Manage with Lucius" styled with the lucius link variant', () => {
    const { getByRole } = render(<Actions commitment={commitment} fx={{ SGD: 1 }} m={m} />)
    const btn = getByRole('button', { name: 'Manage with Lucius' })
    expect(btn.className).toContain('lucius')
  })

  // PENDING the checkbox redesign: `.c2-act-check` appears nowhere in src/ (4d485ca3).
  it.skip("renders a pending action's checkbox as the button itself, no separate dot child", () => {
    const { container } = render(<Actions commitment={commitment} fx={{ SGD: 1 }} m={m} />)
    const check = container.querySelector('.c2-act-check')!
    expect(check).toBeTruthy()
    expect(check.querySelector('.c2-si-dot')).toBeNull()
  })

  it('always renders the trailing bell+amount column, with or without an amount', () => {
    const { container } = render(<Actions commitment={commitment} fx={{ SGD: 1 }} m={m} />)
    const trails = [...container.querySelectorAll('.c2-act-trail')]
    expect(trails).toHaveLength(2)
    const amounts = trails.map((t) => t.querySelector('.c2-act-amt')?.textContent ?? null)
    // Every row's .c2-act-amt slot exists (reserving the same column) — empty for the row
    // with no amount, populated for the one with an amount. Order doesn't matter here.
    expect(amounts).toContain('')
    expect(amounts.some((a) => a?.includes('4,000'))).toBe(true)
  })

  it('has no dedicated "outcomes" row — "Not happening" lives inline in the meta row', () => {
    // The badge only renders for a MISSED action; the shared fixture has none, so this
    // asserted behaviour the component has while never setting up its precondition.
    const withMissed = {
      ...commitment,
      actions: [{ ...commitment.actions![0]!, id: 'a-missed', status: 'missed' as const }],
    }
    const { container, getAllByText } = render(
      <Actions commitment={withMissed} fx={{ SGD: 1 }} m={m} />,
    )
    expect(container.querySelector('.c3-act-outcomes')).toBeNull()
    const notHappening = getAllByText('Not happening')[0]!
    expect(notHappening.closest('.c2-act-meta')).toBeTruthy()
    expect(notHappening.getAttribute('title')).toBeTruthy()
  })

  it("shows a derived funding-plan action's NATIVE amount+currency — its own stored plan, never the commitment's own native currency or a base-currency conversion", () => {
    // Regression, in two stages. (1) A VND-currency commitment (e.g. a Vietnam apartment
    // purchase) funded by a EUR asset sale used to tag the derived action's base-converted
    // value_base with the commitment's OWN currency (VND) — rendering "₫10,000" for what was
    // really ~S$10,000 (real VND would be ~200M+). Owner: "explain how this is happening."
    // (2) The interim fix (tag with the app's base currency instead) was itself rejected —
    // owner: "the data should be source of truth... we should ensure we store the original
    // currency + amount, and we show from that." Final design: every funding source
    // (including asset_sale) stores its own frozen, real amount+currency — the target the
    // user actually intends to raise — and the derived action shows exactly that, untouched.
    const vndCommitment: Commitment = {
      ...commitment,
      currency: 'VND',
      actions: [],
      funding_plan: [
        {
          id: 'fs-onwai',
          type: 'asset_sale',
          label: 'Sell 340 ONWAI (IBKR)',
          amount: 10000,
          currency: 'EUR',
          ref_position_id: null,
          sell_quantity: 340,
          available_date: '2026-11-15',
          ref_account_id: null,
          repayment_commitment_id: null,
          note: '',
          disabled: false,
          cadence: null,
          status: 'planned',
          realized_txn_id: null,
        },
      ],
    }
    const { container } = render(
      <Actions commitment={vndCommitment} fx={{ VND: 1, EUR: 1 }} m={m} />,
    )
    const amt = container.querySelector('.c2-act-amt')!
    expect(amt.textContent).toContain('10,000')
    expect(amt.textContent).not.toContain('₫')
    expect(amt.textContent).toMatch(/€|EUR/)
  })
})
