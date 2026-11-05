// @vitest-environment jsdom
//
// Hide amounts on an instrument page: what you own is masked, and the market's own numbers are
// not. Walks the rendered text, so a new unmasked figure fails without naming its selector.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { splitFigures } from '../../utils/amountMask'
import type { AssetPosition } from '../../types/assetDetail'
import { MaskedText } from '../ui/MaskedText'
import { PositionSubtab } from './PositionSubtab'
import { YourPositionCard } from './YourPositionCard'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../shared/LotsPanel', () => ({ LotsPanel: () => null }))
vi.mock('../../api/client', () => ({
  authFetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ events: [] }) }),
}))

afterEach(cleanup)

const position = {
  position_id: 'p1',
  qty: 135.7461,
  cost_basis: 508.84,
  cost_currency: 'USD',
  market_value_base: 81990.64,
  unrealized_pl_base: 12917.99,
  unrealized_pl_pct: 18.7,
  weight_pct: 32.21,
  per_account: [],
  lots: [],
  realized: [],
  income: [],
  unvested: {
    units: 82,
    market_value_base: 13723.5,
    next_vest: { date: '2026-12-15', units: 41 },
    tranches: [],
  },
} as unknown as AssetPosition

const income = { trailing_12m_base: 436.49 } as never

function unmaskedFigures(root: Element): string[] {
  const found: string[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode
    if (node.parentElement?.closest('.money, .mask')) continue
    found.push(
      ...splitFigures(node.textContent ?? '')
        .filter((p) => p.figure)
        .map((p) => p.text),
    )
  }
  return found
}

describe('Hide amounts on the instrument page', () => {
  it('masks all of the Position summary, and keeps its labels', () => {
    const { container } = render(
      <PositionSubtab position={position} symbol="ACME" instrumentId="i1" income={income} />,
    )
    expect(unmaskedFigures(container)).toEqual([])
    const value = (label: string) =>
      [...container.querySelectorAll('.asd-pos-sum-cell')].find(
        (cell) => cell.querySelector('.asd-pos-sum-k')?.textContent === label,
      )
    // A bare quantity is what the figure scan can't see, so this one is asserted by name.
    expect(value('Quantity')?.querySelector('.asd-pos-sum-v')?.classList).toContain('money')
    expect(value('Market value')?.querySelector('.asd-pos-sum-sub')?.textContent).toBe(
      '32.21% of book',
    )
    expect(value('Market value')?.querySelector('.asd-pos-sum-sub')?.classList).not.toContain(
      'money',
    )
  })

  it('masks all of the Your Position card', () => {
    const { container } = render(
      <YourPositionCard position={position} symbol="ACME" income={income} />,
    )
    expect(unmaskedFigures(container)).toEqual([])
    const values = container.querySelectorAll('.asd-pos-value')
    expect(values.length).toBeGreaterThan(5)
    values.forEach((value) => expect(value.classList).toContain('money'))
  })
})

describe('MaskedText', () => {
  it('masks the figures and leaves the sentence readable', () => {
    const { container } = render(
      <MaskedText>NET vests in 40 days ($13,723.50 indicative).</MaskedText>,
    )
    expect(container.textContent).toBe('NET vests in 40 days ($13,723.50 indicative).')
    expect(container.querySelectorAll('.money')).toHaveLength(1)
    expect(container.querySelector('.money')?.textContent).toBe('$13,723.50')
  })
})
