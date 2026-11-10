// Dashboard "Money flow" card (defId rep-sankey) — a glanceable 3-column money-flow Sankey for
// the last full month: income → total → spending groups + Saved. Deliberately NOT Plan's
// Money flow modal: no controls, no drill-down; "Open in Plan" goes there. It shares only the
// pure geometry (moneyFlowLayout) and the server graph (GET /api/cashflow/flow?compact=true).

import { useMemo } from 'react'
import type { ComponentType } from 'react'
import { useMaskText } from '../../../../utils/amountMask'
import { fBase, fBaseCompact } from '../../../../utils/format'
import { Skeleton, Text } from '../../../ui'
import { useCashFlowGraph } from '../../../../hooks/serverState'
import {
  fitLabel,
  flowLinkColor,
  flowNodeColor,
  layoutMoneyFlow,
} from '../../../shared/moneyFlowLayout'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { useElementSize } from '../../../../hooks/useElementWidth'

function lastFullMonth(): string {
  const d = new Date()
  const total = d.getFullYear() * 12 + d.getMonth() - 1
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`
}

function shortMonth(ym: string): string {
  const d = new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1)
  return d.toLocaleString('en', { month: 'short' })
}

export const CashFlowCard: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const maskText = useMaskText()
  const month = lastFullMonth()
  const query = useCashFlowGraph(month, true)
  const g = query.data
  const [ref, box] = useElementSize<HTMLDivElement>()
  // jsdom (and the first layout pass) measure 0 — fall back to the card's default size.
  const width = Math.max(box.w || 360, 260)
  const height = Math.max(box.h || 220, 180)
  const laid = useMemo(
    () =>
      g
        ? layoutMoneyFlow(g, {
            width,
            height,
            labelLeft: Math.min(100, width * 0.27),
            labelRight: Math.min(128, width * 0.32),
            nodeWidth: 7,
            nodePadding: 8,
            labelGap: 26,
          })
        : null,
    [g, width, height],
  )
  const byId = useMemo(() => new Map((laid?.nodes ?? []).map((n) => [n.id, n])), [laid])
  const link =
    onNavigate && def.source?.tab ? (
      <button type="button" className="ov-panel-link" onClick={() => onNavigate(def.source!.tab)}>
        Open in Plan
      </button>
    ) : undefined
  // Both sides of the flow net a refund against its category, which Plan → Cash Flow does not
  // (its In and Out are gross), so the same month reads smaller here. Say what this one counts.
  const footer = (
    <div className="ov-mf-foot">
      <Text as="span" size="xs" tone="faint">
        Net of refunds and reimbursements
      </Text>
      {link}
    </div>
  )

  if (query.initialLoading && !g) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill>
        <Skeleton height="100%" />
      </WidgetShell>
    )
  }
  if (!g || g.flow_total <= 0) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder
          detail={
            !g
              ? "Couldn't load the money flow."
              : `No income or spending recorded in ${shortMonth(month)}.`
          }
        />
      </WidgetShell>
    )
  }
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      footer={footer}
      meta={
        <>
          {shortMonth(month)} ·{' '}
          <span className="money">{fBaseCompact(g.shortfall > 0 ? g.shortfall : g.saved)}</span>
          {g.shortfall > 0 ? ' over' : ' saved'}
        </>
      }
    >
      <div className="ov-mf" ref={ref}>
        {laid && (
          <svg
            className="mf-svg ov-mf-svg"
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`Money flow for ${shortMonth(month)}: ${fBase(g.income_total)} in, ${fBase(g.spend_total)} out`}
          >
            <defs>
              <pattern
                id="ov-mf-hatch"
                width="6"
                height="6"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width="2" height="6" fill="var(--mf-short)" />
              </pattern>
            </defs>
            {laid.links.map((l) => (
              <path
                key={l.key}
                className="mf-lnk"
                d={l.path}
                stroke={flowLinkColor(byId.get(l.target)!, byId.get(l.source)!)}
                strokeWidth={l.width}
              >
                <title>{`${byId.get(l.source)!.label} → ${byId.get(l.target)!.label}: ${fBase(l.amount)}`}</title>
              </path>
            ))}
            {laid.nodes.map((n) => (
              <g key={n.id} className="mf-node">
                <title>{maskText(`${n.label}: ${fBase(n.amount)} (${n.pct}%)`)}</title>
                <rect
                  x={n.x0}
                  y={n.y0}
                  width={n.x1 - n.x0}
                  height={Math.max(2, n.y1 - n.y0)}
                  rx={2}
                  fill={n.kind === 'shortfall' ? 'url(#ov-mf-hatch)' : flowNodeColor(n)}
                />
                {n.kind !== 'total' && (
                  <text
                    className="mf-lbl ov-mf-lbl"
                    x={n.labelX}
                    y={n.labelY - 2}
                    textAnchor={n.anchor}
                  >
                    <tspan className="mf-lbl-name">{fitLabel(n.label, n.labelMax, 11)}</tspan>
                    <tspan className="mf-lbl-amt" x={n.labelX} dy={12}>
                      {fBase(n.amount)}
                    </tspan>
                  </text>
                )}
              </g>
            ))}
          </svg>
        )}
      </div>
    </WidgetShell>
  )
}
