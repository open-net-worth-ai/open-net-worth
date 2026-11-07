// @vitest-environment jsdom
//
// A commitment's own shortfall is its funding sources against its payments, starting from zero
// (`_plan_fidelity_verdict`): it never reads an account balance or the cash floor. "Runs S$2,645
// short of your cash floor" on a loan with no funding sources yet read as cash running out.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CommitmentDetail } from './detail'
import { CommitmentRows } from './rows'
import { Masthead } from './masthead'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment, PortfolioCoverage } from '../../../types/commitments'
import type { CoverageDerived } from './coverageEngine'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => children,
}))

const LOAN: Commitment = {
  id: 'acme-loan',
  name: 'ACME loan',
  kind: 'loan_repayment',
  currency: 'USD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01',
  installments: [
    {
      id: 'i1',
      label: 'Payment 1/2',
      description: '',
      due_date: '2027-01-30',
      amount: 1000,
      status: 'upcoming',
      paid_txn_id: null,
    },
    {
      id: 'i2',
      label: 'Payment 2/2',
      description: '',
      due_date: '2027-02-28',
      amount: 1000,
      status: 'upcoming',
      paid_txn_id: null,
    },
  ],
  funding_plan: [],
  coverage: {
    assessed: true,
    at_risk: true,
    shortfall_base: 1000,
    earliest_shortfall_date: '2027-01-30',
  },
} as unknown as Commitment

function textOf(el: Element | null): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
}

beforeEach(() => {
  setBaseCurrency('USD')
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-07-20T12:00:00Z'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('a commitment shortfall says it is about funding, not cash', () => {
  function renderDetail(onAskLucius = vi.fn()) {
    return render(
      <CommitmentDetail
        commitment={LOAN}
        fx={{ USD: 1 }}
        onBack={vi.fn()}
        onAskLucius={onAskLucius}
        onChanged={vi.fn()}
        onOpenCommitment={vi.fn()}
      />,
    )
  }

  it('the detail chip reads as unfunded, naming the amount and date', () => {
    const { container } = renderDetail()
    const chip = textOf(container.querySelector('.c2-chip.short'))
    expect(chip).toContain('$1,000.00 unfunded by')
    expect(chip).not.toMatch(/^short/i)
  })

  it('the detail banner never mentions the cash floor and says balances are not counted', () => {
    const { container } = renderDetail()
    const banner = textOf(container.querySelector('.c2-ribbon-core'))
    expect(banner).toContain("This plan's funding runs $1,000.00 short of its payments by")
    expect(banner).toContain('not your account balances')
    expect(banner).not.toMatch(/cash floor/i)
    expect(textOf(container.querySelector('.c2-ribbon-lite'))).toContain('$1,000.00 unfunded by')
  })

  it('the prompt handed to Lucius describes the same thing', () => {
    const onAskLucius = vi.fn()
    renderDetail(onAskLucius)
    fireEvent.click(screen.getByRole('button', { name: /fix/i }))
    expect(onAskLucius).toHaveBeenCalledTimes(1)
    const prompt = JSON.stringify(onAskLucius.mock.calls[0])
    expect(prompt).toContain('funding runs $1,000.00 short of its payments')
    expect(prompt).toContain('not my account balances')
    expect(prompt).not.toMatch(/cash floor/i)
  })

  it('the list row chip reads as unfunded', () => {
    const { container } = render(
      <CommitmentRows
        list={[LOAN]}
        fx={{ USD: 1 }}
        onOpen={vi.fn()}
        filter="all"
        setFilter={vi.fn()}
        sort="next"
        setSort={vi.fn()}
      />,
    )
    expect(textOf(container.querySelector('.c2-chip.short'))).toBe('$1,000.00 unfunded')
  })

  it('the At risk empty state does not claim funding was checked against the cash floor', () => {
    const { container } = render(
      <CommitmentRows
        list={[{ ...LOAN, coverage: { ...LOAN.coverage!, at_risk: false } }]}
        fx={{ USD: 1 }}
        onOpen={vi.fn()}
        filter="risk"
        setFilter={vi.fn()}
        sort="next"
        setSort={vi.fn()}
      />,
    )
    expect(container.textContent).toContain('Nothing at risk')
    expect(container.textContent).not.toMatch(/cash floor/i)
  })

  it("the summary names the plan's own funding, while its headline stays about cash", () => {
    const cov = {
      base_currency: 'USD',
      at_risk: true,
      earliest_shortfall_date: '2027-01-01',
      total_shortfall_base: 5000,
      min_balance_base: -5000,
      lowest_balance_month: '2027-01',
      horizon_months: 12,
      low_balance_threshold: 0,
      over_committed: false,
      spans_currencies: false,
      reconciliation: { earmarked_base: 0, liquid_base: 0, over_committed: false, excess_base: 0 },
      commitments: [],
      timeline: [
        { year_month: '2027-01', balance: -5000, total_in: 0, total_out: 0, is_tight: false },
      ],
    } as PortfolioCoverage
    const derived: CoverageDerived = {
      timeline: cov.timeline,
      gapIdx: 0,
      minIdx: 0,
      minBal: -5000,
      floor: 0,
      riskId: LOAN.id,
      riskDate: '2027-01-30',
      shortfall: 5000,
    }
    const { container } = render(
      <Masthead
        list={[LOAN]}
        fx={{ USD: 1 }}
        cov={cov}
        dueSoon={0}
        derived={derived}
        horizon={12}
        onHorizon={vi.fn()}
        hzOptions={[]}
        topProposal={null}
        onReview={vi.fn()}
        onFix={vi.fn()}
      />,
    )
    expect(textOf(container.querySelector('.c2-ribbon-core'))).toContain(
      "ACME loan's funding runs $1,000 short of its payments by",
    )
    expect(textOf(container.querySelector('.c2-sum-hero'))).toContain('Short $5,000')
  })
})
