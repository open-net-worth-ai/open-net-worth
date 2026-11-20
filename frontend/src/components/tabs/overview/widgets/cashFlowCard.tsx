// Dashboard "Money flow" card (defId rep-sankey) — income → total → spending groups + Saved, for
// the last full month or the period its settings pick, optionally narrowed to tags. Clicking a
// spending group redraws the card as that group → its categories; a category opens its
// transactions for the same months and tags. It draws with Plan › Cash Flow's own Money flow
// chart (moneyFlowChart.tsx), so the two look and behave the same: gradient ribbons, labels
// with each share, hover tracing a path. Every figure is the server's (GET /api/cashflow/flow).

import { useState } from 'react'
import type { ComponentType } from 'react'
import { fBase, fBaseCompact } from '../../../../utils/format'
import { Skeleton, Text } from '../../../ui'
import { ChevronLeft } from '../../../../ui/icons'
import { useCashFlowGraph, type ReportScope } from '../../../../hooks/serverState'
import type { CashFlowGraph, CashFlowNode } from '../../../../types/aggregates'
import { MONEY_FLOW_COMPACT_BELOW_PX, drillGroup } from '../../../shared/moneyFlowLayout'
import { MoneyFlowChart } from '../../plan/moneyFlowChart'
import type { RegisterDeepLink, WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { monthSpan, reportScope, tagsText } from './reportScope'
import { ReportFilter } from './ReportFilter'
import { useElementSize } from '../../../../hooks/useElementWidth'
import { useIsMobile } from '../../../../hooks/useIsMobile'

/** The least height the card lays the chart out at; a taller graph scrolls inside the card. */
const CARD_CHART_MIN = 240

function lastFullMonth(): string {
  const d = new Date()
  const total = d.getFullYear() * 12 + d.getMonth() - 1
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`
}

/** "Sep", or "Aug – Oct" for a period. */
function spanOf(g: Pick<CashFlowGraph, 'months'> | undefined, month: string): string {
  const months = g?.months?.length ? g.months : [month]
  return monthSpan(months[0]!, months[months.length - 1]!)
}

/** The register filter for one category, over the card's months and tags. */
function registerLink(node: CashFlowNode, g: CashFlowGraph, tags: string[]): RegisterDeepLink {
  const months = g.months?.length ? g.months : [g.month]
  const from = months[0]!
  const to = months[months.length - 1]!
  const ids = node.category_ids ?? []
  const range = {
    month: from,
    monthTo: to !== from ? to : undefined,
    tag: tags.length ? tags : undefined,
  }
  return node.uncategorized
    ? { ...range, uncategorized: true, kind: 'expense' }
    : { ...range, category: ids.length === 1 ? ids[0] : ids }
}

export const CashFlowCard: ComponentType<WidgetContentProps> = ({
  instance,
  def,
  onNavigate,
  onOpenRegister,
  onPatch,
}) => {
  const month = lastFullMonth()
  const sc = reportScope(instance, def)
  // Plan's rule: the full graph (groups and their categories) when the card is wide enough,
  // otherwise the compact one, where a group opens into its categories.
  const isMobile = useIsMobile()
  const [bodyRef, body] = useElementSize<HTMLDivElement>()
  // Unmeasured counts as compact: most cards are half-width, so they ask once.
  const compact = isMobile || body.w < MONEY_FLOW_COMPACT_BELOW_PX
  const query = useCashFlowGraph(month, compact, sc.scope)
  const g = query.data
  const [drilled, setDrilled] = useState<{ id: string; label: string } | null>(null)
  const span = spanOf(g, month)
  const cats = sc.categories.length
    ? sc.categories.length === 1
      ? '1 category'
      : `${sc.categories.length} categories`
    : ''
  // What the card is narrowed to, for its empty state and its accessible name.
  const where = [span, cats, tagsText(sc.tags)].filter(Boolean).join(' · ')
  const filter = onPatch ? (
    <ReportFilter
      instance={instance}
      def={def}
      onPatch={onPatch}
      label={g || !sc.scope.period ? span : sc.periodText}
    />
  ) : undefined
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
      <WidgetShell title={instance.title} base={def.title} fill actions={filter}>
        <div className="ov-mf" ref={bodyRef}>
          <Skeleton height="100%" />
        </div>
      </WidgetShell>
    )
  }
  if (!g || g.flow_total <= 0) {
    return (
      <WidgetShell title={instance.title} base={def.title} actions={filter}>
        <WidgetPlaceholder
          detail={
            !g ? "Couldn't load the money flow." : `No income or spending recorded in ${where}.`
          }
        />
      </WidgetShell>
    )
  }
  const meta = (
    <>
      <span className="money">{fBaseCompact(g.shortfall > 0 ? g.shortfall : g.saved)}</span>
      {g.shortfall > 0 ? ' over' : ' saved'}
    </>
  )
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      footer={footer}
      meta={meta}
      actions={filter}
    >
      <div className="ov-mf" ref={bodyRef}>
        {drilled && compact ? (
          <DrilledGroup
            month={month}
            scope={sc.scope}
            group={drilled}
            tags={sc.tags}
            where={where}
            onBack={() => setDrilled(null)}
            onOpenRegister={onOpenRegister}
          />
        ) : (
          <MoneyFlowChart
            graph={g}
            label={`Money flow for ${where}: ${fBase(g.income_total)} in, ${fBase(g.spend_total)} out`}
            shareOf={g.shortfall > 0 ? 'spending' : 'income'}
            onDrill={compact ? (n) => setDrilled({ id: n.id, label: n.label }) : undefined}
            onOpenCategory={
              onOpenRegister ? (n) => onOpenRegister(registerLink(n, g, sc.tags)) : undefined
            }
            fill
            minHeight={CARD_CHART_MIN}
          />
        )}
      </div>
    </WidgetShell>
  )
}

/** One spending group → its categories, over the card's months and tags. The compact graph
 *  stops at groups, so this reads the full one — exactly what Plan's phone drill-in shows. */
function DrilledGroup({
  month,
  scope,
  group,
  tags,
  where,
  onBack,
  onOpenRegister,
}: {
  month: string
  scope: ReportScope
  group: { id: string; label: string }
  tags: string[]
  where: string
  onBack: () => void
  onOpenRegister?: (filter: RegisterDeepLink) => void
}) {
  const query = useCashFlowGraph(month, false, scope)
  const full = query.data
  const drilled = full ? drillGroup(full, group.id) : null
  const head = drilled?.nodes[0]
  return (
    <>
      <div className="ov-mf-drill">
        <button type="button" className="ov-mf-drill-back" onClick={onBack}>
          <ChevronLeft size={14} aria-hidden="true" /> All groups
        </button>
        <span className="ov-mf-drill-title">
          {group.label}
          {head && (
            <>
              {' · '}
              <span className="money">{fBase(head.amount)}</span>
            </>
          )}
        </span>
      </div>
      {query.initialLoading && !full ? (
        <Skeleton height="100%" />
      ) : !drilled || !full ? (
        <WidgetPlaceholder detail={`No categories to show for ${group.label} in ${where}.`} />
      ) : (
        <MoneyFlowChart
          graph={drilled}
          label={`${group.label} in ${where}, by category`}
          shareOf={group.label}
          fill
          minHeight={CARD_CHART_MIN}
          onOpenCategory={
            onOpenRegister ? (n) => onOpenRegister(registerLink(n, full, tags)) : undefined
          }
        />
      )}
    </>
  )
}
