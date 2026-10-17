import { describe, expect, it } from 'vitest'
import type { CashFlowGraph } from '../../types/aggregates'
import {
  drillGroup,
  flowLinkColor,
  flowNodeColor,
  layoutMoneyFlow,
  traceFlow,
} from './moneyFlowLayout'

const GRAPH: Pick<CashFlowGraph, 'nodes' | 'links' | 'columns'> = {
  columns: 3,
  nodes: [
    { id: 'total', label: 'Income', column: 1, kind: 'total', amount: 1000, pct: 100 },
    { id: 'inc:a', label: 'Salary', column: 0, kind: 'income', amount: 900, pct: 90 },
    { id: 'inc:b', label: 'Interest', column: 0, kind: 'income', amount: 60, pct: 6 },
    { id: 'inc:c', label: 'Cashback', column: 0, kind: 'income', amount: 40, pct: 4 },
    {
      id: 'sg:h',
      label: 'Housing',
      column: 2,
      kind: 'spend_group',
      amount: 600,
      pct: 60,
      color_slot: 0,
    },
    { id: 'saved', label: 'Saved', column: 2, kind: 'saved', amount: 400, pct: 40 },
  ],
  links: [
    { source: 'inc:a', target: 'total', amount: 900 },
    { source: 'inc:b', target: 'total', amount: 60 },
    { source: 'inc:c', target: 'total', amount: 40 },
    { source: 'total', target: 'sg:h', amount: 600 },
    { source: 'total', target: 'saved', amount: 400 },
  ],
}

const OPTS = {
  width: 400,
  height: 200,
  labelLeft: 80,
  labelRight: 100,
  nodeWidth: 8,
  nodePadding: 8,
  labelGap: 26,
}

describe('layoutMoneyFlow', () => {
  it('places each server column left to right and keeps every link', () => {
    const { nodes, links } = layoutMoneyFlow(GRAPH, OPTS)
    const x = (id: string) => nodes.find((n) => n.id === id)!.x0
    expect(x('inc:a')).toBeLessThan(x('total'))
    expect(x('total')).toBeLessThan(x('sg:h'))
    expect(x('sg:h')).toBe(x('saved'))
    expect(links).toHaveLength(5)
    expect(links.every((l) => l.path.startsWith('M'))).toBe(true)
  })

  it('spreads labels in a column so none are closer than labelGap', () => {
    const { nodes } = layoutMoneyFlow(GRAPH, OPTS)
    const ys = nodes
      .filter((n) => n.column === 0)
      .map((n) => n.labelY)
      .sort((a, b) => a - b)
    for (let i = 1; i < ys.length; i++)
      expect(ys[i]! - ys[i - 1]!).toBeGreaterThanOrEqual(OPTS.labelGap - 0.01)
    expect(Math.max(...ys)).toBeLessThanOrEqual(OPTS.height)
  })

  it('anchors the first column’s labels to the left of their boxes', () => {
    const { nodes } = layoutMoneyFlow(GRAPH, OPTS)
    const salary = nodes.find((n) => n.id === 'inc:a')!
    const housing = nodes.find((n) => n.id === 'sg:h')!
    expect(salary.anchor).toBe('end')
    expect(salary.labelX).toBeLessThan(salary.x0)
    expect(housing.anchor).toBe('start')
    expect(housing.labelX).toBeGreaterThan(housing.x1)
  })

  it('labels between the columns and spans the full width when labelsInside', () => {
    const { nodes } = layoutMoneyFlow(GRAPH, {
      ...OPTS,
      labelLeft: 0,
      labelRight: 0,
      labelsInside: true,
    })
    const at = (id: string) => nodes.find((n) => n.id === id)!
    expect(at('inc:a').x0).toBe(0)
    expect(at('sg:h').x1).toBe(OPTS.width)
    // The income side labels to the right of its bars, the spending side to the left.
    for (const id of ['inc:a', 'total']) {
      expect(at(id).anchor).toBe('start')
      expect(at(id).labelX).toBeGreaterThan(at(id).x1)
    }
    expect(at('sg:h').anchor).toBe('end')
    expect(at('sg:h').labelX).toBeLessThan(at('sg:h').x0)
    // The total and the groups label into the same gap, so each gets half of it.
    const gap = at('sg:h').x0 - at('total').x1
    expect(at('total').labelMax + at('sg:h').labelMax).toBeLessThanOrEqual(gap)
    expect(at('inc:a').labelMax).toBeGreaterThan(gap / 2)
  })

  it('gives the whole gap to the groups when the total draws no label', () => {
    const opts = { ...OPTS, labelLeft: 0, labelRight: 0, labelsInside: true }
    const shared = layoutMoneyFlow(GRAPH, opts).nodes.find((n) => n.id === 'sg:h')!
    const alone = layoutMoneyFlow(GRAPH, { ...opts, totalUnlabelled: true }).nodes.find(
      (n) => n.id === 'sg:h',
    )!
    expect(alone.labelMax).toBeGreaterThan(shared.labelMax * 1.5)
  })

  it('returns nothing for an empty month', () => {
    expect(layoutMoneyFlow({ columns: 4, nodes: [], links: [] }, OPTS)).toEqual({
      nodes: [],
      links: [],
    })
  })
})

describe('colours', () => {
  it('keeps red for overspending and gives groups past the fourth a neutral hue', () => {
    expect(flowNodeColor({ kind: 'shortfall' })).toBe('var(--mf-short)')
    expect(flowNodeColor({ kind: 'spend_group', color_slot: 3 })).toBe('var(--mf-g3)')
    expect(flowNodeColor({ kind: 'spend_group', color_slot: 4 })).toBe('var(--mf-g-rest)')
    expect(flowLinkColor({ kind: 'total' }, { kind: 'income' })).toBe('var(--mf-income)')
    expect(flowLinkColor({ kind: 'spend_group', color_slot: 1 }, { kind: 'total' })).toBe(
      'var(--mf-g1)',
    )
  })
})

describe('traceFlow', () => {
  it('lights a node’s upstream and downstream path', () => {
    const links = GRAPH.links.map((l) => ({ ...l, key: `${l.source}→${l.target}` }))
    const hot = traceFlow(links, 'sg:h')
    expect(hot.nodes.has('total')).toBe(true)
    expect(hot.nodes.has('inc:a')).toBe(true)
    expect(hot.nodes.has('saved')).toBe(false)
    expect(hot.links.has('total→saved')).toBe(false)
  })
})

describe('drillGroup', () => {
  const FULL = {
    nodes: [
      { id: 'total', label: 'Income', column: 2, kind: 'total' as const, amount: 1000, pct: 100 },
      { id: 'sg:h', label: 'Bills', column: 3, kind: 'spend_group' as const, amount: 600, pct: 60 },
      {
        id: 'sp:r',
        label: 'Rent',
        column: 4,
        kind: 'spend' as const,
        amount: 450,
        pct: 45,
        group_pct: 75,
      },
      {
        id: 'sp:u',
        label: 'Utilities',
        column: 4,
        kind: 'spend' as const,
        amount: 150,
        pct: 15,
        group_pct: 25,
      },
      { id: 'saved', label: 'Saved', column: 3, kind: 'saved' as const, amount: 400, pct: 40 },
    ],
    links: [
      { source: 'total', target: 'sg:h', amount: 600 },
      { source: 'total', target: 'saved', amount: 400 },
      { source: 'sg:h', target: 'sp:r', amount: 450 },
      { source: 'sg:h', target: 'sp:u', amount: 150 },
    ],
  }

  it('extracts the group and its categories, with shares of the group', () => {
    const g = drillGroup(FULL, 'sg:h')!
    expect(g.columns).toBe(2)
    expect(g.nodes.map((n) => [n.label, n.column, n.pct])).toEqual([
      ['Bills', 0, 100],
      ['Rent', 1, 75],
      ['Utilities', 1, 25],
    ])
    expect(g.links).toHaveLength(2)
  })

  it('returns null for a node that is not a drillable group', () => {
    expect(drillGroup(FULL, 'saved')).toBeNull()
    expect(drillGroup(FULL, 'sg:missing')).toBeNull()
  })
})
