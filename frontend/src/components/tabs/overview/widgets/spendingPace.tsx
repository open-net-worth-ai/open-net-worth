import { useMemo } from 'react'
import type { ComponentType } from 'react'
import { fBase } from '../../../../utils/format'
import { Money } from '../../../ui'
import { WidgetShell } from '../WidgetShell'
import type { WidgetContentProps } from '../types'
import { usePlanningSummary } from '../../../../hooks/serverState'
import type { EnvelopeRow, PlanningSummary } from '../../../../types/aggregates'

const TOP_SPENDERS = 3

export const SpendingPaceWidget: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const { data: summary } = usePlanningSummary()

  // Which envelopes to show: the ones the user picked; with no pick (the default), or when
  // none of the picked names is a budgeted envelope this month, the top spenders — and in the
  // second case the widget says so rather than quietly showing something else.
  const { categories, unmatched } = useMemo(() => {
    if (!summary) return { categories: [] as EnvelopeRow[], unmatched: [] as string[] }
    const budgeted = summary.envelopes.budgeted_rows
    const picked = ((instance.config.categories as string[] | undefined) ?? []).filter(Boolean)
    const matched = picked
      .map((name) => budgeted.find((r) => r.label === name))
      .filter((r): r is EnvelopeRow => r !== undefined)
    if (matched.length > 0) return { categories: matched, unmatched: [] as string[] }
    return {
      categories: [...budgeted].sort((a, b) => b.spent - a.spent).slice(0, TOP_SPENDERS),
      unmatched: picked,
    }
  }, [summary, instance.config.categories])

  if (!summary)
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div />
      </WidgetShell>
    )

  const DAYS_IN_MONTH = summary.days_in_month
  const CURRENT_DAY = summary.day_of_month
  const PERCENT_ELAPSED = summary.pace_pct

  const monthStr = new Date().toLocaleString('en-US', { month: 'short' }).toUpperCase()

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={`${monthStr} ${CURRENT_DAY} · DAY ${CURRENT_DAY}/${DAYS_IN_MONTH}`}
    >
      <div className="spending-pace-widget">
        <div className="time" data-od-id="time-elapsed">
          <i></i> {Math.round(PERCENT_ELAPSED)}% of month elapsed
        </div>

        {unmatched.length > 0 && (
          <div className="ov-t-meta" role="note">
            No budgeted envelope named {unmatched.join(', ')} — showing this month's top spending
            instead. Pick categories in the widget settings.
          </div>
        )}
        {categories.length === 0 && (
          <div className="ov-t-meta">No budgeted envelopes have spending this month.</div>
        )}
        {categories.map((cat) => (
          <PaceRow key={cat.key} cat={cat} summary={summary} />
        ))}
      </div>
    </WidgetShell>
  )
}

function PaceRow({ cat, summary }: { cat: EnvelopeRow; summary: PlanningSummary }) {
  const PERCENT_ELAPSED = summary.pace_pct

  const spent = cat.spent
  const assigned = cat.assigned
  const spentPct = cat.fill_pct

  let state = 'var(--good)'
  if (spentPct > PERCENT_ELAPSED * 1.1) state = 'var(--warn)'
  if (cat.over) state = 'var(--bad)'

  const dotColor = cat.color || state

  return (
    <article
      className="row"
      style={
        {
          '--spent': `${Math.min(100, spentPct)}%`,
          '--health': state,
          '--category': dotColor,
        } as any
      }
    >
      <div className="top">
        <div className="category">
          <i className="dot"></i>
          {cat.label}
        </div>
        <div className="amount">
          <b>
            <Money>{fBase(spent)}</Money>
          </b>{' '}
          / <Money>{fBase(assigned)}</Money> · <Money>{Math.round(spentPct)}%</Money>
        </div>
      </div>

      <div
        className="bar"
        aria-label={`${cat.label}: ${Math.round(spentPct)} percent of budget spent`}
      >
        <i className="fill"></i>
        <i
          className="tick"
          style={{ left: `${Math.min(100, Math.round(PERCENT_ELAPSED))}%` }}
          aria-hidden="true"
        ></i>
      </div>

      <div className="stats">
        <span>
          Avg{' '}
          <b>
            <Money>{fBase(cat.avg_per_day)}</Money>/day
          </b>
        </span>
        <span>
          <b>
            <Money>{fBase(cat.left_per_day)}</Money>/day
          </b>{' '}
          left
        </span>
        <span className="remaining">
          <Money>{fBase(cat.remaining)}</Money> left
        </span>
      </div>
    </article>
  )
}

export const spendingPaceWidgetComponents = {
  'spending-pace': SpendingPaceWidget,
}
