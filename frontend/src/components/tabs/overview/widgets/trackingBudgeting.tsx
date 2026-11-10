// Overview v2 — the hidden Tracking/Budgeting widgets: debt paydown and budget vs
// actual. Ported from the handoff's `overview_v2b/insights.jsx` and `budgets.jsx`,
// wired to real endpoints. Hidden from the Add-widget picker (`hidden: true` in
// definitions.tsx); the public demo's seeded layout places both.
//
// Real-data seams:
//   ins-debt      the portfolio aggregate's `debts` rows
//   bud-vsactual  GET /api/overview/budget (envelope assigned/activity)

import type { ComponentType } from 'react'
import { fBase, fBaseCompact, moneyClass } from '../../../../utils/format'
import { Button, Meter, Money, Text } from '../../../ui'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { useAnalytics } from '../useWidgetData'
import { usePlanningSummary } from '../../../../hooks/serverState'
import { ArrowRight } from '../../../../ui/icons'

// ── debt / liabilities — real balances, no payoff curve ──────────────────
//
// A projected payoff curve needs an amortization schedule (rate, term, min
// payment) this book doesn't model for every liability type — shipping the
// real current balances instead of a fabricated curve, per the task's own
// documented acceptable simplification.
const DebtPayoff: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  // Liability rows and their total come from the server aggregate — the same `debts` figure
  // the "Liabilities" KPI reads, so the two can't disagree.
  const { analytics: portfolio, loading } = useAnalytics()
  const debts = portfolio?.debts.rows ?? []
  const total = portfolio?.debts.total ?? 0
  const cap = rowCap(instance, def) ?? undefined
  const visible = debts.slice(0, cap)

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={3} />
      </WidgetShell>
    )
  }
  if (!debts.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={<>No liabilities on the books — nothing to pay down.</>} />
      </WidgetShell>
    )
  }

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={<Money as="span">{fBaseCompact(Math.abs(total))}</Money>}
      footer={
        onNavigate && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onNavigate(def.source?.tab || 'accounts')}
          >
            {def.source?.label ? `View in ${def.source.label}` : 'View accounts'}
            <ArrowRight size={14} aria-hidden="true" />
          </Button>
        )
      }
    >
      <Text as="p" size="xs" tone="faint" className="ov-ins-gapnote">
        What you owe on each account today.
      </Text>
      <div className="ov-ins-rows">
        {visible.map((d) => (
          <div
            key={d.key}
            className="ov-wmh-row"
            data-tip={`${d.label}\n${fBase(Math.abs(d.value))}`}
            data-tip-color="var(--bad)"
          >
            <span className="ov-iet-lab">
              <i style={{ background: d.color || 'var(--bad)' }} />
              {d.label}
            </span>
            <span className={moneyClass('ov-wmh-val')}>{fBase(Math.abs(d.value))}</span>
          </div>
        ))}
        {debts.length > visible.length && (
          <div className="ov-more">+{debts.length - visible.length} more</div>
        )}
      </div>
    </WidgetShell>
  )
}

// One server read (`GET /api/overview/budget`) carries the envelope progress rows and their
// totals, already summed by backend/services/overview
// (ADR 2026-07-31-overview-aggregates-are-server-computed).

// ── budget vs actual — real envelope assigned/activity progress bars ────

const BudgetVsActual: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const { data, initialLoading: loading, error } = usePlanningSummary()
  const rows = data?.envelopes.budgeted_rows ?? []
  const cap = rowCap(instance, def) ?? undefined
  const visible = rows.slice(0, cap)
  const totalSpent = data?.envelopes.budgeted_spent ?? 0
  const totalBudget = data?.envelopes.budgeted_total ?? 0
  const daysLeft = data?.days_left ?? 0

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Couldn't load your budget — try again shortly." />
      </WidgetShell>
    )
  }
  if (!rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={<>Assign money to envelopes in Plan to see progress here.</>} />
      </WidgetShell>
    )
  }

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      subtitle={`${daysLeft} day${daysLeft === 1 ? '' : 's'} left this month`}
      meta={
        <>
          <Money as="span">{fBaseCompact(totalSpent)}</Money> <span className="ov-t-meta">/</span>{' '}
          <Money as="span">{fBaseCompact(totalBudget)}</Money>
        </>
      }
      footer={
        onNavigate && (
          <Button size="sm" variant="ghost" onClick={() => onNavigate(def.source?.tab || 'plan')}>
            Edit budgets
            <ArrowRight size={14} aria-hidden="true" />
          </Button>
        )
      }
    >
      <div className="ov-bva">
        {visible.map((c) => (
          <div
            key={c.key}
            className="ov-bva-row"
            data-tip={`${c.label}\n${fBase(c.spent)} of ${fBase(c.assigned)}`}
            data-tip-color={c.over ? 'var(--bad)' : c.color || 'var(--accent)'}
          >
            <div className="ov-bva-top">
              <span className="ov-bva-nm">{c.label}</span>
              <span className={moneyClass('ov-bva-fig', c.over && 'neg')}>
                {fBase(c.spent)} <span className="ov-t-meta">/ {fBaseCompact(c.assigned)}</span>
              </span>
            </div>
            <Meter
              variant="linear"
              size="sm"
              value={c.fill_pct}
              color={c.over ? 'var(--bad)' : c.color || 'var(--accent)'}
              ariaLabel={`${c.label}: ${fBase(c.spent)} of ${fBase(c.assigned)} spent`}
              valueText={`${fBase(c.spent)} of ${fBase(c.assigned)}`}
            />
          </div>
        ))}
      </div>
    </WidgetShell>
  )
}

export const trackingBudgetingWidgetComponents: Record<
  string,
  ComponentType<WidgetContentProps>
> = {
  'ins-debt': DebtPayoff,
  'bud-vsactual': BudgetVsActual,
}
