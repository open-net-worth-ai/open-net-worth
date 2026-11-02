// @vitest-environment jsdom
//
// The "Record sale" card. Its display keys mirror `_build_lot_dispose_change`
// (backend/ai/tools/lots.py); the figures are the live QA sale of 2 VOO @ 610 on the
// synthetic demo book (docs/qa/evidence/reloaded-confirm-card).
//
// What it pins is the approval-card anatomy (docs/APPROVAL_CARDS.md): one lead line, a muted
// meta line, fact rows (the gain and where the cash goes), and the per-lot table behind a
// disclosure that still names the term mix. The no-cash case is ONE fact row with its
// explanation in a hover — it used to be a "deposit to" row PLUS a paragraph.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

afterEach(cleanup)

function sale(display: Record<string, unknown> = {}): ProposedChange {
  return {
    id: 'toolu_sale',
    type: 'lot_dispose',
    grant_tool: 'dispose_lots',
    display: {
      symbol: 'VOO',
      venue: 'Meridian Wealth',
      quantity: 2,
      method: 'fifo',
      is_taxable: true,
      proceeds_per_unit: 610,
      proceeds_currency: 'USD',
      disposal_fees: 0,
      disposed_at: '2026-10-11',
      total_proceeds: 1220,
      proceeds_account_id: null,
      proceeds_account_name: null,
      consumptions: [
        {
          lot_id: 'lot_a',
          qty: 0.5464,
          unit_cost: 452.65,
          cost_currency: 'USD',
          acquired_at: '2024-05-03',
          term: 'long',
          realized_gain: 85.97,
        },
        {
          lot_id: 'lot_b',
          qty: 1.4536,
          unit_cost: 442.97,
          cost_currency: 'USD',
          acquired_at: '2024-06-05',
          term: 'long',
          realized_gain: 242.8,
        },
      ],
      realized_gain: 328.77,
      realized_gain_base: 328.77,
      base_currency: 'USD',
      ...display,
    },
  } as ProposedChange
}

describe('Record sale card', () => {
  const fact = (container: HTMLElement, label: string) =>
    [...container.querySelectorAll('.ac-fact')]
      .find((r) => r.querySelector('.ac-fact-label')?.textContent === label)
      ?.querySelector('.ac-fact-value') as HTMLElement | undefined

  it('leads with what is sold, then the gain as a fact', () => {
    const { container } = render(<ChangePreviewV2 change={sale()} onRespond={() => {}} />)

    expect(container.querySelector('.ac-lead')?.textContent).toBe('Sell 2 @ $610.00$1,220.00')
    expect(container.querySelector('.ac-meta')?.textContent).toBe('Oct 11, 2026 · FIFO')
    const gain = fact(container, 'Realized gain')!
    expect(gain.textContent).toBe('+$328.77')
    expect(gain.className).toContain('ac-tone-good')
  })

  it('folds the lots behind a summary that still says their term', () => {
    const { container } = render(<ChangePreviewV2 change={sale()} onRespond={() => {}} />)

    const lots = container.querySelector('details.ac-details') as HTMLDetailsElement
    expect(lots.open).toBe(false)
    expect(lots.querySelector('summary')?.textContent).toBe('2 lots · all long-term')
    expect(lots.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('names a mixed term split', () => {
    const mixed = sale()
    const second = mixed.display?.consumptions?.[1]
    if (!second) throw new Error('sale() fixture should carry two lots')
    second.term = 'short'
    render(<ChangePreviewV2 change={mixed} onRespond={() => {}} />)

    expect(screen.getByText('2 lots · 1 long-term, 1 short-term')).toBeTruthy()
  })

  it('says in one row that no cash is booked, the why in a hover', () => {
    const { container } = render(<ChangePreviewV2 change={sale()} onRespond={() => {}} />)

    const cash = fact(container, 'Cash')!
    expect(cash.textContent).toBe('not booked')
    expect(cash.className).toContain('ac-tone-warn')
    expect(cash.title).toBe(
      'No cash account named. Fine if the money is already in an account; otherwise net worth drops $1,220.00.',
    )
    expect(container.textContent).not.toMatch(/deposit to/i)
    expect(container.textContent).not.toMatch(/will NOT be added/)
  })

  it('names the deposit account when there is one', () => {
    const { container } = render(
      <ChangePreviewV2
        change={sale({ proceeds_account_name: 'Brokerage Cash' })}
        onRespond={() => {}}
      />,
    )

    const cash = fact(container, 'Cash')!
    expect(cash.textContent).toBe('Brokerage Cash')
    expect(cash.className).not.toContain('ac-tone-warn')
    expect(cash.title).toBe('')
  })

  it('a transfer between own accounts books no gain and says where it went', () => {
    const { container } = render(
      <ChangePreviewV2
        change={sale({
          is_taxable: false,
          transfer_to_account_name: 'IBKR',
          proceeds_per_unit: 0,
          total_proceeds: 0,
          realized_gain: null,
        })}
        onRespond={() => {}}
      />,
    )

    expect(container.querySelector('.ac-lead')?.textContent).toBe('Move 2 → IBKR')
    expect(container.querySelector('.ac-meta')?.textContent).toContain(
      'basis carries over, no gain',
    )
    expect(container.querySelector('.ac-facts')).toBeNull()
  })
})
