// Crossover and Spending pace as panels. The server decides the crossover headline ("Now", the
// projected FIRE year, "Not yet") from the book's own net worth, and judges each envelope's pace
// (on pace / ahead / over); the views only draw what they are told.

import type { CSSProperties } from 'react'
import { fBase, fBaseCompact, fMonth } from '../../../../utils/format'
import { Money, Skeleton } from '../../../ui'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { FooterLink } from '../widgets/cashflowPlanning'
import type { PanelDefinition, PanelViewProps } from './panel'
import type { CrossoverPanel, SpendingPacePanel } from './types'

function CrossoverView(props: PanelViewProps<CrossoverPanel>) {
  const { instance, def, onNavigate, data, loading, error } = props
  const footer = <FooterLink label="Open Plan" tab={def.source?.tab} onNavigate={onNavigate} />
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <Skeleton height={100} />
      </WidgetShell>
    )
  }
  if (error || !data?.known) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Needs at least one month of spending history to project a crossover." />
      </WidgetShell>
    )
  }
  const headline =
    data.headline === 'now' ? 'Now' : data.headline === 'year' ? String(data.year) : 'Not yet'
  return (
    <WidgetShell title={instance.title} base={def.title} bodyAlign="center" footer={footer}>
      <div className="ov-cross">
        <div className="ov-cross-hd">
          <span className="ov-cross-yr">{headline}</span>
          {data.projected && <span className="ov-cross-note">projected</span>}
        </div>
        <div className="ov-env-bar ov-goal-bar">
          <span
            className="ov-env-fill"
            style={{ width: `${data.coverage_bar_pct}%`, background: 'var(--good)' }}
          />
        </div>
        <div className="ov-cross-stats">
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Passive income now</span>
            <b className="ov-cross-val">
              <Money as="span">{fBaseCompact(data.passive_monthly)}</Money>/mo
            </b>
          </div>
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Baseline spending</span>
            <b className="ov-cross-val">
              <Money as="span">{fBaseCompact(data.expenses_monthly)}</Money>/mo
            </b>
          </div>
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Covered</span>
            <b className="ov-cross-val">{data.coverage_pct_rounded}%</b>
          </div>
        </div>
        {data.to_go_monthly > 0 && (
          <div className="ov-cross-cap">
            <Money as="span">{fBaseCompact(data.to_go_monthly)}</Money>/mo to go
          </div>
        )}
      </div>
    </WidgetShell>
  )
}

export const crossoverPanel: PanelDefinition<CrossoverPanel> = {
  query: () => ({ source: 'panel_crossover', params: {} }),
  signals: ['portfolio', 'ledger', 'accounts', 'assets', 'profile'],
  View: CrossoverView,
}

const STATE_COLOR = { on_pace: 'var(--good)', ahead: 'var(--warn)', over: 'var(--bad)' } as const

type PaceRow = SpendingPacePanel['rows'][number]

function SpendingPaceView({ instance, def, data }: PanelViewProps<SpendingPacePanel>) {
  if (!data)
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div />
      </WidgetShell>
    )
  const month = fMonth(data.month, 'short').toUpperCase()
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={`${month} ${data.day_of_month} · DAY ${data.day_of_month}/${data.days_in_month}`}
    >
      <div className="spending-pace-widget">
        <div className="time" data-od-id="time-elapsed">
          <i></i> {data.pace_pct_rounded}% of month elapsed
        </div>
        {data.unmatched.length > 0 && (
          <div className="ov-t-meta" role="note">
            No budgeted envelope named {data.unmatched.join(', ')} — showing this month's top
            spending instead. Pick categories in the widget settings.
          </div>
        )}
        {data.rows.length === 0 && (
          <div className="ov-t-meta">No budgeted envelopes have spending this month.</div>
        )}
        {data.rows.map((row) => (
          <PaceRowView key={row.key} row={row} tickPct={data.tick_pct} />
        ))}
      </div>
    </WidgetShell>
  )
}

function PaceRowView({ row, tickPct }: { row: PaceRow; tickPct: number }) {
  const state = STATE_COLOR[row.state]
  return (
    <article
      className="row"
      style={
        {
          '--spent': `${row.bar_pct}%`,
          '--health': state,
          '--category': row.color || state,
        } as CSSProperties
      }
    >
      <div className="top">
        <div className="category">
          <i className="dot"></i>
          {row.label}
        </div>
        <div className="amount">
          <b>
            <Money>{fBase(row.spent)}</Money>
          </b>{' '}
          / <Money>{fBase(row.assigned)}</Money> · <Money>{row.fill_pct_rounded}%</Money>
        </div>
      </div>
      <div
        className="bar"
        aria-label={`${row.label}: ${row.fill_pct_rounded} percent of budget spent`}
      >
        <i className="fill"></i>
        <i className="tick" style={{ left: `${tickPct}%` }} aria-hidden="true"></i>
      </div>
      <div className="stats">
        <span>
          Avg{' '}
          <b>
            <Money>{fBase(row.avg_per_day)}</Money>/day
          </b>
        </span>
        <span>
          <b>
            <Money>{fBase(row.left_per_day)}</Money>/day
          </b>{' '}
          left
        </span>
        <span className="remaining">
          <Money>{fBase(row.remaining)}</Money> left
        </span>
      </div>
    </article>
  )
}

function pickedCategories(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((c): c is string => typeof c === 'string' && !!c) : []
}

export const spendingPacePanel: PanelDefinition<SpendingPacePanel> = {
  query: (instance) => ({
    source: 'panel_spending_pace',
    params: { categories: pickedCategories(instance.config?.categories) },
  }),
  signals: ['ledger', 'accounts', 'budget'],
  View: SpendingPaceView,
}
