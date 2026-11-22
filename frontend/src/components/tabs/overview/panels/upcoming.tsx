// Next 30 days as a panel: the server picks the rows (today through +30 on the profile's clock),
// counts the days, and sums both totals — the list and its "going out" total come from one read.

import { fBase, fDate } from '../../../../utils/format'
import { Money } from '../../../ui'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { TimelineStrip } from '../Timeline'
import type { PanelDefinition, PanelViewProps } from './panel'
import type { UpcomingPanel } from './types'

// The calendar's colours: money coming in is green, money going out red.
const directionColor = (d: 'in' | 'out'): string => (d === 'in' ? 'var(--good)' : 'var(--bad)')

function UpcomingView({ instance, def, onNavigate, data, loading }: PanelViewProps<UpcomingPanel>) {
  const footer = onNavigate && (
    <button type="button" className="ov-panel-link" onClick={() => onNavigate('commitments')}>
      {data?.no_dated_bills ? 'Add due dates' : 'All commitments'}
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    </button>
  )
  if (loading || !data) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={3} />
      </WidgetShell>
    )
  }
  const overdueLine = data.overdue_count > 0 && (
    <button
      type="button"
      className="ov-upcoming-overdue"
      onClick={onNavigate ? () => onNavigate('commitments') : undefined}
      disabled={!onNavigate}
    >
      {data.overdue_count} overdue
      {data.overdue_total_base > 0 && (
        <>
          {' · '}
          <Money as="span">{fBase(data.overdue_total_base)}</Money>
        </>
      )}
    </button>
  )
  if (!data.rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        {overdueLine}
        <WidgetPlaceholder
          detail={
            data.no_dated_bills
              ? 'No dated bills yet. Add due dates to a bill or installment and what is coming up shows here.'
              : data.overdue_count
                ? 'Nothing else due to pay in the next 30 days.'
                : 'Nothing due to pay in the next 30 days.'
          }
        />
      </WidgetShell>
    )
  }
  const cap = rowCap(instance, def)
  const visible = cap == null ? data.rows : data.rows.slice(0, cap)
  const hidden = cap == null ? [] : data.rows.slice(cap)
  const strip = (
    <TimelineStrip
      axis={[0, data.days]}
      ticks={[
        { pos: 0, label: 'today' },
        { pos: data.days, label: `+${data.days}d` },
      ]}
      events={data.rows.map((e) => ({
        pos: e.days,
        color: directionColor(e.direction),
        dateLabel: '',
        label: e.label,
      }))}
    />
  )
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={data.no_dated_bills ? 'No dated bills yet' : undefined}
      subhead={strip}
      footer={footer}
    >
      <div className="ov-upcoming-totals">
        {!data.no_dated_bills && (
          <p className="ov-upcoming-out">
            <Money as="span">{fBase(data.going_out_total_base)}</Money> going out
          </p>
        )}
        {data.coming_in_total_base > 0 && (
          <p className="ov-upcoming-income">
            <Money as="span">{fBase(data.coming_in_total_base)}</Money> coming in
          </p>
        )}
        {overdueLine}
      </div>
      <div className="ov-upcoming">
        {visible.map((e, i) => (
          <div key={i} className="ov-upcoming-row">
            <span className="ov-upcoming-date">{fDate(e.date)}</span>
            <span className="ov-upcoming-dot" style={{ background: directionColor(e.direction) }} />
            <span className="ov-upcoming-label">
              {e.label} <span className="ov-upcoming-sub">• {e.sub}</span>
            </span>
            <span className="ov-upcoming-amt">
              {e.amount_base != null && <Money as="span">{fBase(e.amount_base)}</Money>}{' '}
              {e.days === 0 ? 'today' : `in ${e.days}d`}
            </span>
          </div>
        ))}
        {hidden.length > 0 && (
          <div className="ov-more">+{hidden.length} more in the next 30 days</div>
        )}
      </div>
    </WidgetShell>
  )
}

export const upcomingPanel: PanelDefinition<UpcomingPanel> = {
  query: () => ({ source: 'panel_upcoming', params: {} }),
  signals: ['ledger', 'accounts', 'commitments', 'assets'],
  View: UpcomingView,
}
