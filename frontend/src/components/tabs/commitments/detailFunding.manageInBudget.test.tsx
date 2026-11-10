// Regression test: "Manage in Budget →" used to navigate({ to: '/plan' }) with no sub-tab,
// so Plan fell back to whatever sub-tab localStorage['pl-subtab'] last had (e.g. Outlook),
// not Budget — owner: "it opens the plan tab, but outlook is selected". Fix: pass an explicit
// ?sub=budget, mirroring the existing 'View on Outlook' chip's ?sub=outlook deep-link.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { Funding } from './detailFunding'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'
import type { EnvelopeAccrualState } from './fundingValue'

setBaseCurrency('SGD')

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
const mockNavigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useRouterState: () => ({ matches: [] }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const commitment: Commitment = {
  id: 'c1',
  name: 'Parkview Residences',
  kind: 'purchase',
  currency: 'SGD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  envelope_id: 'env_1',
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [
    {
      id: 'i1',
      label: '1st',
      description: '',
      due_date: '2026-08-01',
      amount: 1000,
      status: 'upcoming',
      paid_txn_id: null,
    },
  ],
  funding_plan: [
    {
      id: 'w1',
      type: 'windfall',
      label: 'Bonus',
      amount: 1000,
      currency: 'SGD',
      ref_position_id: null,
      sell_quantity: null,
      available_date: null,
      ref_account_id: null,
      repayment_commitment_id: null,
      note: '',
      disabled: false,
      cadence: null,
      status: 'planned',
      realized_txn_id: null,
      value_base: 1000,
    },
  ],
}

const envelope: EnvelopeAccrualState = {
  envelope: 'Parkview Residences',
  accumulated: 21.15,
  per_period: 0,
  target_this_month: 4000,
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
  rename: vi.fn(),
  updateNote: vi.fn(),
  setStatus: vi.fn(),
  deleteCommitment: vi.fn(),
} as unknown as DetailMutations

describe('Funding tab — Manage in Budget deep-link', () => {
  it('navigates to /plan with ?sub=budget when a synced envelope is resolved', () => {
    const { getByRole } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={envelope} positions={{}} m={m} />,
    )
    fireEvent.click(getByRole('button', { name: 'Manage in Budget' }))
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/plan', search: { sub: 'budget' } }),
    )
  })

  it('navigates to /plan with ?sub=budget from the not-yet-resolved envelope_id fallback too', () => {
    const unresolvedEnvelope: Commitment = { ...commitment, envelope_id: 'env_pending' }
    const { getByRole } = render(
      <Funding
        commitment={unresolvedEnvelope}
        fx={{ SGD: 1 }}
        envelope={null}
        positions={{}}
        m={m}
      />,
    )
    fireEvent.click(getByRole('button', { name: 'Manage in Budget' }))
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/plan', search: { sub: 'budget' } }),
    )
  })
})
