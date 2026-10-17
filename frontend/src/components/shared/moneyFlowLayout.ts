// Pure Sankey geometry for GET /api/cashflow/flow — shared by Plan's MoneyFlowChart and the
// Dashboard's CashFlowCard, which are otherwise separate components. No money math: every
// amount and share arrives server-computed; this only positions boxes, ribbons and labels.

import { sankey, sankeyLinkHorizontal, type SankeyLink, type SankeyNode } from 'd3-sankey'
import type { CashFlowGraph, CashFlowNode } from '../../types/aggregates'

export interface FlowLayoutOptions {
  width: number
  height: number
  /** Space reserved left of column 0 / right of the last column for labels. */
  labelLeft: number
  labelRight: number
  nodeWidth: number
  nodePadding: number
  /** Minimum vertical distance between two labels in one column. */
  labelGap: number
  /** Labels sit between the columns instead of in gutters, so the flow can span the full
   *  width: the income side (up to the total) labels to the right of its bars, the spending
   *  side to the left. A gap both sides label into is split between them. */
  labelsInside?: boolean
  /** The total draws no label (the compact graph), so its gap belongs to the next column. */
  totalUnlabelled?: boolean
}

export interface LaidNode extends CashFlowNode {
  x0: number
  x1: number
  y0: number
  y1: number
  labelX: number
  labelY: number
  anchor: 'start' | 'end'
  /** Label was pushed off its box to avoid a collision — draw a leader line. */
  leader: boolean
  /** Horizontal room for the label, in px — pass to `fitLabel`. */
  labelMax: number
}

export interface LaidLink {
  key: string
  source: string
  target: string
  amount: number
  width: number
  path: string
}

type N = SankeyNode<CashFlowNode, { amount: number }>
type L = SankeyLink<CashFlowNode, { amount: number }>

const SPEND_SLOTS = 4

/** CSS colour for a node, from the money-flow tokens in index.css. */
export function flowNodeColor(n: Pick<CashFlowNode, 'kind' | 'color_slot'>): string {
  if (n.kind === 'saved') return 'var(--mf-saved)'
  if (n.kind === 'shortfall') return 'var(--mf-short)'
  if (n.kind === 'spend' || n.kind === 'spend_group') {
    const slot = n.color_slot
    return slot != null && slot < SPEND_SLOTS ? `var(--mf-g${slot})` : 'var(--mf-g-rest)'
  }
  return 'var(--mf-income)'
}

/** A ribbon takes the colour of the side it feeds: income until the total, then its target. */
export function flowLinkColor(
  target: Pick<CashFlowNode, 'kind' | 'color_slot'>,
  source: Pick<CashFlowNode, 'kind'>,
): string {
  if (source.kind === 'shortfall') return 'var(--mf-short)'
  if (target.kind === 'total') return 'var(--mf-income)'
  return flowNodeColor(target)
}

function spreadLabels(nodes: N[], gap: number, top: number, bottom: number): Map<N, number> {
  const out = new Map<N, number>()
  const byCol = new Map<number, N[]>()
  for (const n of nodes) byCol.set(n.column, [...(byCol.get(n.column) ?? []), n])
  for (const col of byCol.values()) {
    col.sort((a, b) => (a.y0 ?? 0) - (b.y0 ?? 0))
    const ys = col.map((n) => ((n.y0 ?? 0) + (n.y1 ?? 0)) / 2)
    for (let i = 0; i < ys.length; i++) ys[i] = Math.max(ys[i]!, i ? ys[i - 1]! + gap : top)
    const over = ys.length ? ys[ys.length - 1]! - bottom : 0
    if (over > 0) {
      ys[ys.length - 1]! -= over
      for (let i = ys.length - 2; i >= 0; i--) ys[i] = Math.min(ys[i]!, ys[i + 1]! - gap)
    }
    col.forEach((n, i) => out.set(n, ys[i]!))
  }
  return out
}

/** Shorten `text` with an ellipsis to fit `maxPx` at `fontPx` (average glyph ≈ 0.56em). */
export function fitLabel(text: string, maxPx: number, fontPx = 12): string {
  const room = Math.floor(maxPx / (fontPx * 0.56))
  if (text.length <= room) return text
  return room <= 1 ? '' : text.slice(0, room - 1).trimEnd() + '…'
}

export function layoutMoneyFlow(
  graph: Pick<CashFlowGraph, 'nodes' | 'links' | 'columns'>,
  o: FlowLayoutOptions,
): { nodes: LaidNode[]; links: LaidLink[] } {
  if (!graph.nodes.length || !graph.links.length) return { nodes: [], links: [] }
  const generator = sankey<CashFlowNode, { amount: number }>()
    .nodeId((d) => d.id)
    .nodeAlign((d) => (d as N).column)
    .nodeSort(null as unknown as (a: N, b: N) => number)
    .nodeWidth(o.nodeWidth)
    .nodePadding(o.nodePadding)
    .extent([
      [o.labelLeft, 4],
      [o.width - o.labelRight, o.height - 4],
    ])
  const g = generator({
    nodes: graph.nodes.map((n) => ({ ...n })),
    links: graph.links.map((l) => ({ ...l, value: l.amount })),
  })
  const labelY = spreadLabels(g.nodes, o.labelGap, o.labelGap / 2, o.height - o.labelGap / 2)
  const colX = new Map<number, number>()
  const colX1 = new Map<number, number>()
  for (const n of g.nodes) {
    colX.set(n.column, Math.min(colX.get(n.column) ?? Infinity, n.x0 ?? 0))
    colX1.set(n.column, Math.max(colX1.get(n.column) ?? -Infinity, n.x1 ?? 0))
  }
  const split = Math.max(0, ...g.nodes.filter((n) => n.kind === 'total').map((n) => n.column))
  const toRight = (c: number) => (o.labelsInside ? c <= split : c !== 0)
  const path = sankeyLinkHorizontal<N, L>()
  const insideLabelRoom = (c: number, left: boolean, x0: number, x1: number) => {
    const across = left ? c - 1 : c + 1
    const room = left ? x0 - (colX1.get(across) ?? 0) : (colX.get(across) ?? o.width) - x1
    const shared =
      colX.has(across) && toRight(across) === left && !(o.totalUnlabelled && across === split)
    return (shared ? room / 2 : room) - 14
  }

  const nodes: LaidNode[] = g.nodes.map((n) => {
    const x0 = n.x0 ?? 0
    const x1 = n.x1 ?? 0
    const y0 = n.y0 ?? 0
    const y1 = n.y1 ?? 0
    const ly = labelY.get(n) ?? (y0 + y1) / 2
    const left = !toRight(n.column)
    const labelMax = o.labelsInside
      ? insideLabelRoom(n.column, left, x0, x1)
      : left
        ? x0 - 10
        : (colX.get(n.column + 1) ?? o.width) - x1 - 14
    return {
      ...(n as CashFlowNode),
      x0,
      x1,
      y0,
      y1,
      labelX: left ? x0 - 8 : x1 + 8,
      labelY: ly,
      anchor: left ? 'end' : 'start',
      leader: Math.abs(ly - (y0 + y1) / 2) > (y1 - y0) / 2 + 2,
      labelMax,
    }
  })
  const links: LaidLink[] = g.links.map((l) => {
    const s = l.source as N
    const t = l.target as N
    return {
      key: `${s.id}→${t.id}`,
      source: s.id,
      target: t.id,
      amount: l.amount,
      width: Math.max(1, l.width ?? 1),
      path: path(l) ?? '',
    }
  })
  return { nodes, links }
}

/** Every node and link upstream and downstream of `id` — the hover highlight. Income pools
 *  at the total, so tracing any income source lights all spending, and vice versa. */
export function traceFlow(
  links: Array<{ key: string; source: string; target: string }>,
  id: string,
): { nodes: Set<string>; links: Set<string> } {
  const nodes = new Set<string>([id])
  const hot = new Set<string>()
  const walk = (from: string, up: boolean) => {
    for (const l of links) {
      if ((up ? l.target : l.source) !== from || hot.has(l.key)) continue
      hot.add(l.key)
      const next = up ? l.source : l.target
      nodes.add(next)
      walk(next, up)
    }
  }
  walk(id, true)
  walk(id, false)
  return { nodes, links: hot }
}

/** One spending group and its categories, as a 2-column graph — the phone's drill-in view.
 *  Pure extraction from the FULL graph (no arithmetic): each category's `pct` becomes its
 *  server-computed share of the group. Null when the group isn't in `graph` or has no
 *  categories. */
export function drillGroup(
  graph: Pick<CashFlowGraph, 'nodes' | 'links'>,
  groupId: string,
): Pick<CashFlowGraph, 'nodes' | 'links' | 'columns'> | null {
  const group = graph.nodes.find((n) => n.id === groupId && n.kind === 'spend_group')
  if (!group) return null
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const links = graph.links.filter((l) => l.source === groupId)
  const children = links.map((l) => byId.get(l.target)).filter((n) => n?.kind === 'spend')
  if (!children.length) return null
  return {
    columns: 2,
    nodes: [
      { ...group, column: 0, pct: 100 },
      ...children.map((c) => ({ ...c!, column: 1, pct: c!.group_pct ?? c!.pct })),
    ],
    links,
  }
}
