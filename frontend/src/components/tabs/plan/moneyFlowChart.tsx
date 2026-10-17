// plan/moneyFlowChart.tsx — the Money flow modal's Sankey (SVG). Geometry comes from the shared
// pure layout; every figure is the server's (GET /api/cashflow/flow). Hover traces a node's
// whole path; a spending category is a link to its transactions.

import { useId, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import type { CashFlowGraph, CashFlowNode } from '../../../types/aggregates'
import {
  fitLabel,
  flowLinkColor,
  flowNodeColor,
  layoutMoneyFlow,
  traceFlow,
  type LaidNode,
} from '../../shared/moneyFlowLayout'
import { fBase } from './planData'
import { useElementSize } from '../../../hooks/useElementWidth'

const MIN_WIDTH = 300
const TOUCH = typeof window !== 'undefined' && !!window.matchMedia?.('(hover: none)').matches

interface Tip {
  x: number
  y: number
  title: string
  body: string
  hint?: string
}

export function MoneyFlowChart({
  graph,
  label,
  shareOf,
  onOpenCategory,
  onDrill,
  fill,
}: {
  graph: Pick<CashFlowGraph, 'nodes' | 'links' | 'columns'>
  /** Accessible name for the whole chart. */
  label: string
  /** What a node's `pct` is a share of — "income", "spending", or a group's name. */
  shareOf: string
  onOpenCategory?: (node: CashFlowNode) => void
  /** Tapping a spending group drills into its categories (the compact view). */
  onDrill?: (node: CashFlowNode) => void
  /** Grow to the height the parent gives it (a flex column), never below the content minimum. */
  fill?: boolean
}) {
  const [wrapRef, box] = useElementSize<HTMLDivElement>()
  const measured = box.w
  const [hover, setHover] = useState<string | null>(null)
  const [tip, setTip] = useState<Tip | null>(null)
  // jsdom measures 0 — lay out at a desktop default so the chart still renders.
  const width = Math.max(measured || 960, MIN_WIDTH)
  const narrow = graph.columns <= 3
  // The drill-in view (2 columns) shows each category's share of its group beside the amount.
  const drill = graph.columns === 2
  // Labels sit between the columns so the flow spans the whole width. The drill-in view keeps
  // its right gutter: its categories' names are the whole point and need the room.
  const inside = !drill
  const showPct = !narrow || drill
  const tallest = Math.max(
    ...Array.from(
      { length: graph.columns },
      (_, c) => graph.nodes.filter((n) => n.column === c).length,
    ),
  )
  const labelGap = 30
  const minHeight = Math.max(narrow ? 300 : 380, tallest * labelGap + 16)
  const height = fill ? Math.max(minHeight, Math.floor(box.h)) : minHeight
  // useId's colons/guillemets aren't safe inside url(#…) — keep only id-safe characters.
  const gradId = 'mfg' + useId().replace(/[^\w-]/g, '')

  const laid = useMemo(
    () =>
      layoutMoneyFlow(graph, {
        width,
        height,
        // Drill-in (2 columns): the header chip already names the group, so its label is
        // dropped and the categories get the room.
        labelLeft: inside ? 2 : 4,
        labelRight: inside ? 2 : width * 0.55,
        labelsInside: inside,
        totalUnlabelled: narrow,
        nodeWidth: narrow ? 8 : 10,
        nodePadding: narrow ? 10 : height > 520 ? 18 : 14,
        labelGap,
      }),
    [graph, width, height, narrow, inside],
  )
  const byId = useMemo(() => new Map(laid.nodes.map((n) => [n.id, n])), [laid.nodes])
  const hot = useMemo(() => (hover ? traceFlow(laid.links, hover) : null), [hover, laid.links])

  // Viewport coordinates + a position:fixed tooltip: an in-flow tooltip could overflow the
  // modal body, toggle its scrollbar, resize the chart under the cursor and re-fire hover —
  // an update loop (seen in QA on a real mouse click).
  const place = (e: React.MouseEvent | React.FocusEvent, t: Omit<Tip, 'x' | 'y'>) => {
    if ('clientX' in e) return setTip({ ...t, x: e.clientX, y: e.clientY })
    const r = (e.target as Element).getBoundingClientRect()
    setTip({ ...t, x: r.right, y: r.top })
  }
  // Position only — never re-set the content. A move handler that re-placed the tooltip from
  // its render-time `tip` overwrote the NEW content set by mouseenter in the same tick, so the
  // tooltip kept naming the flow the pointer had just left while the highlight moved on.
  const move = (e: React.MouseEvent) => {
    const { clientX, clientY } = e
    setTip((t) => (t ? { ...t, x: clientX, y: clientY } : t))
  }
  const clickable = (n: LaidNode) =>
    n.kind === 'spend' && (!!n.category_ids?.length || !!n.uncategorized) && !!onOpenCategory
  // "sg-other" folds several groups together, so there is no single group to open.
  const drillable = (n: LaidNode) =>
    n.kind === 'spend_group' && n.id !== 'sg-other' && !!onDrill && n.column > 0
  const nodeTip = (n: LaidNode) => ({
    title: n.label,
    body:
      n.column === 0 && graph.columns === 2
        ? fBase(n.amount)
        : `${fBase(n.amount)} · ${n.pct}% of ${shareOf}`,
    hint: clickable(n)
      ? `${TOUCH ? 'Tap' : 'Click'} to see transactions`
      : drillable(n)
        ? 'Tap to see its categories'
        : undefined,
  })
  const clear = () => {
    setHover(null)
    setTip(null)
  }

  return (
    <div
      className={'mf-chart' + (fill ? ' mf-chart--fill' : '')}
      style={fill ? { minHeight } : undefined}
      ref={wrapRef}
      onMouseLeave={clear}
    >
      <svg
        className={'mf-svg' + (hot ? ' mf-svg--dim' : '')}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
      >
        <defs>
          <pattern
            id="mf-hatch"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="2" height="6" fill="var(--mf-short)" />
          </pattern>
          {/* Each ribbon fades from the colour of the node it leaves to the one it feeds. */}
          {laid.links.map((l, i) => {
            const s = byId.get(l.source)!
            const t = byId.get(l.target)!
            return (
              <linearGradient
                key={l.key}
                id={`${gradId}-${i}`}
                gradientUnits="userSpaceOnUse"
                x1={s.x1}
                x2={t.x0}
              >
                <stop offset="0" style={{ stopColor: flowNodeColor(s) }} />
                <stop offset="1" style={{ stopColor: flowLinkColor(t, s) }} />
              </linearGradient>
            )
          })}
        </defs>
        <g>
          {laid.links.map((l, i) => (
            <path
              key={l.key}
              className={'mf-lnk' + (hot?.links.has(l.key) ? ' is-hot' : '')}
              d={l.path}
              stroke={`url(#${gradId}-${i})`}
              strokeWidth={l.width}
              strokeDasharray={byId.get(l.source)?.kind === 'shortfall' ? '4 3' : undefined}
              onMouseEnter={(e) => {
                // Trace from the non-total end, so a ribbon out of the pooled total lights its
                // own branch rather than the whole graph.
                setHover(byId.get(l.source)?.kind === 'total' ? l.target : l.source)
                place(e, {
                  title: `${byId.get(l.source)!.label} → ${byId.get(l.target)!.label}`,
                  body: fBase(l.amount),
                })
              }}
              onMouseMove={move}
            />
          ))}
        </g>
        {/* Every leader before any label, so each label's halo masks the lines that pass
            behind it — drawn per node, a later node's leader struck through earlier text. */}
        <g aria-hidden>
          {laid.nodes
            .filter((n) => n.leader)
            .map((n) => (
              <path
                key={n.id}
                className={'mf-leader' + (hot?.nodes.has(n.id) ? ' is-hot' : '')}
                d={`M${n.anchor === 'end' ? n.x0 : n.x1},${(n.y0 + n.y1) / 2} L${n.labelX + (n.anchor === 'end' ? 4 : -4)},${n.labelY}`}
              />
            ))}
        </g>
        {laid.nodes.map((n) => {
          const open = clickable(n)
            ? () => onOpenCategory!(n)
            : drillable(n)
              ? () => {
                  clear()
                  onDrill!(n)
                }
              : undefined
          return (
            <g
              key={n.id}
              className={
                'mf-node' + (hot?.nodes.has(n.id) ? ' is-hot' : '') + (open ? ' is-link' : '')
              }
              tabIndex={0}
              role={open ? 'link' : undefined}
              aria-label={`${n.label}: ${fBase(n.amount)}, ${n.pct}%`}
              onMouseEnter={(e) => {
                setHover(n.id)
                place(e, nodeTip(n))
              }}
              onMouseMove={move}
              onFocus={(e) => {
                setHover(n.id)
                place(e, nodeTip(n))
              }}
              onBlur={clear}
              onClick={open}
              onKeyDown={(e) => {
                if (open && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault()
                  open()
                }
              }}
            >
              <rect
                x={n.x0}
                y={n.y0}
                width={n.x1 - n.x0}
                height={Math.max(2, n.y1 - n.y0)}
                rx={2}
                fill={n.kind === 'shortfall' ? 'url(#mf-hatch)' : flowNodeColor(n)}
                stroke={n.kind === 'shortfall' ? 'var(--mf-short)' : undefined}
              />
              {!(narrow && n.kind === 'total') && !(drill && n.column === 0) && (
                <text className="mf-lbl" x={n.labelX} y={n.labelY - 2} textAnchor={n.anchor}>
                  <tspan className="mf-lbl-name">{fitLabel(n.label, n.labelMax)}</tspan>
                  <tspan className="mf-lbl-amt mask" x={n.labelX} dy={13}>
                    {fitLabel(
                      showPct ? `${fBase(n.amount)} · ${n.pct}%` : fBase(n.amount),
                      n.labelMax,
                      11,
                    )}
                  </tspan>
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {tip &&
        createPortal(
          <div
            className="mf-tip"
            style={{
              left: Math.min(tip.x + 14, window.innerWidth - 252),
              top: Math.min(tip.y + 14, window.innerHeight - 90),
            }}
            role="tooltip"
          >
            <div className="mf-tip-t">{tip.title}</div>
            <div className="mf-tip-b mask">{tip.body}</div>
            {tip.hint && <div className="mf-tip-h">{tip.hint}</div>}
          </div>,
          document.body,
        )}
    </div>
  )
}
