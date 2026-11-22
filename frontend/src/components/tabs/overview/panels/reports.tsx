// Spending by category and Savings rate as panels. One query, `panel_report` — the report rollup
// plus what these two widgets used to work out in the browser: which month the spending widgets
// show (and whether it fell back), each category's share, and the savings verdict with the gap
// when more went out than came in.

import { fBaseCompact, fMonth } from '../../../../utils/format'
import { Text } from '../../../ui'
import { scopeParams } from '../../../../hooks/serverState/aggregates'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { BreakdownBody, type BreakdownRow } from '../Breakdown'
import { OvChart } from '../charts'
import { vizOf } from '../vizModel'
import type { WidgetDefinition, WidgetInstance } from '../types'
import { monthSpan, reportScope, tagsText } from '../widgets/reportScope'
import {
  emptyFor,
  filterPill,
  gaugeConfig,
  MonthNote,
  REPORT_MONTHS,
  withColor,
} from '../widgets/reports'
import type { PanelDefinition, PanelQuery, PanelViewProps } from './panel'
import type { ReportPanel } from './types'

const SIGNALS = ['ledger', 'accounts', 'budget'] as const

function reportQuery(instance: WidgetInstance, def: WidgetDefinition): PanelQuery {
  const { scope } = reportScope(instance, def)
  return {
    source: 'panel_report',
    params: { months: REPORT_MONTHS, merchant_limit: 10, ...scopeParams(scope) },
  }
}

/** The header's words for the widget's scope: its period span, categories and tags. */
function scopeText(instance: WidgetInstance, def: WidgetDefinition, data?: ReportPanel) {
  const sc = reportScope(instance, def)
  const span = data?.period ? monthSpan(data.period.from, data.period.to) : ''
  const n = sc.categories.length
  const cats = n ? (n === 1 ? '1 category' : `${n} categories`) : ''
  return {
    span,
    chosen: sc.scope.period ? sc.periodText : '',
    meta: [span, cats, tagsText(sc.tags)].filter(Boolean).join(' · '),
  }
}

function SpendCategoriesView(props: PanelViewProps<ReportPanel>) {
  const { instance, def, onPatch, data, loading, error } = props
  const { span, chosen, meta } = scopeText(instance, def, data)
  const shown = data?.spending
  const viz = vizOf(instance, def) || 'list'
  const filter = filterPill(
    instance,
    def,
    onPatch,
    span || chosen || (shown?.fell_back && shown.from ? fMonth(shown.from, 'short') : 'This month'),
  )
  const rows: BreakdownRow[] = withColor(shown?.expense_rows ?? []).map((r) => ({
    key: r.key,
    label: r.label,
    value: r.amount,
    color: r.color,
    pct: r.pct,
  }))
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} actions={filter}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} actions={filter}>
        <WidgetPlaceholder
          detail={
            error
              ? "Couldn't load the monthly report."
              : emptyFor('spending', meta, 'No spending recorded this month yet.')
          }
        />
      </WidgetShell>
    )
  }
  const note =
    shown?.fell_back && !data?.period
      ? `${fMonth(shown.spending_month, 'long')}. Nothing spent in ${fMonth(shown.current_month, 'long')} yet.`
      : null
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      actions={filter}
      fill={viz !== 'list'}
      chart={{
        value: viz,
        options: [
          { value: 'list', label: 'List' },
          { value: 'bar', label: 'Bar' },
          { value: 'pie', label: 'Pie' },
        ],
        onChange: (v) => onPatch({ config: { ...instance.config, viz: v } }),
      }}
    >
      {note && <MonthNote>{note}</MonthNote>}
      <BreakdownBody
        rows={rows}
        total={shown?.expense_total ?? 0}
        viz={viz}
        limit={rowCap(instance, def)}
      />
    </WidgetShell>
  )
}

function SavingsRateView(props: PanelViewProps<ReportPanel>) {
  const { instance, def, onPatch, data, loading, error } = props
  const { span, chosen, meta } = scopeText(instance, def, data)
  const savedYm = data?.savings?.ym
  const filter = filterPill(
    instance,
    def,
    onPatch,
    span ||
      chosen ||
      (savedYm && savedYm !== data?.current?.ym ? fMonth(`${savedYm}-01`, 'short') : 'This month'),
  )
  const savings = data?.savings_view
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} bodyAlign="center" actions={filter}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !savings?.known) {
    return (
      <WidgetShell title={instance.title} base={def.title} actions={filter}>
        <WidgetPlaceholder
          detail={
            error
              ? "Couldn't load the monthly report."
              : emptyFor('income', meta, 'No income recorded in the last few months.')
          }
        />
      </WidgetShell>
    )
  }
  const trailing = savings.trailing
  const avgTip = trailing
    ? `${trailing.months}-month average (${fMonth(`${trailing.from}-01`, 'short')} to ` +
      `${fMonth(`${trailing.to}-01`, 'short')}): ${fBaseCompact(trailing.monthly)} a month` +
      (trailing.rate_pct_rounded != null ? `, ${trailing.rate_pct_rounded}%` : '')
    : undefined
  return (
    <WidgetShell title={instance.title} base={def.title} bodyAlign="center" actions={filter}>
      <div className="ov-savings">
        <div className="ov-savings-gauge-wrap" title={avgTip}>
          <OvChart
            config={gaugeConfig(savings.saved, savings.income, 'var(--good)')}
            sig={`${data?.period?.id ?? data?.current?.ym}:${savings.saved}:${savings.income}`}
            minHeight={80}
          />
          <div className="ov-savings-pct money">{savings.rate_pct_rounded}%</div>
        </div>
        <Text as="p" size="xs" tone="faint" className="ov-savings-sub">
          {savings.verdict === 'overspent' ? (
            <>
              <span className="money">{fBaseCompact(savings.overspent)}</span> more spent than{' '}
              <span className="money">{fBaseCompact(savings.income)}</span> income
            </>
          ) : (
            <>
              <span className="money">{fBaseCompact(savings.saved)}</span> of{' '}
              <span className="money">{fBaseCompact(savings.income)}</span> saved
            </>
          )}
        </Text>
      </div>
    </WidgetShell>
  )
}

export const spendCategoriesPanel: PanelDefinition<ReportPanel> = {
  query: reportQuery,
  signals: SIGNALS,
  View: SpendCategoriesView,
}

export const savingsRatePanel: PanelDefinition<ReportPanel> = {
  query: reportQuery,
  signals: SIGNALS,
  View: SavingsRateView,
}
