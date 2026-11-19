// The period and tags a Reports widget covers, read from its settings. The setting stores the
// chip's label (the settings panel compares labels); this maps it to the server's period id.
// A widget left on its default option sends no period at all, so it keeps the window it
// always had (Spending by category's "this month, or last month while this one is empty").

import type { ReportScope } from '../../../../hooks/serverState'
import type { ReportPeriod } from '../../../../types/aggregates'
import { fMonth } from '../../../../utils/format'
import type { WidgetDefinition, WidgetInstance, WidgetSettingSchema } from '../types'

const PERIOD_BY_LABEL: Record<string, ReportPeriod> = {
  'This month': 'this_month',
  'Last month': 'last_month',
  '3 months': '3m',
  '6 months': '6m',
  '12 months': '12m',
  'This year': 'ytd',
}

/** Widgets that show one figure for the whole period (a breakdown, a flow, a gauge). */
export const TOTAL_PERIOD_OPTIONS = Object.keys(PERIOD_BY_LABEL)
/** Widgets that chart month by month: a one-month window would be a single bar. */
export const SERIES_PERIOD_OPTIONS = ['3 months', '6 months', '12 months', 'This year']

export function periodSetting(options: string[], fallback: string): WidgetSettingSchema {
  return { key: 'period', type: 'segment', label: 'Period', options, default: fallback }
}

export const TAGS_SETTING: WidgetSettingSchema = {
  key: 'tags',
  type: 'tags',
  label: 'Tags',
  default: [],
}

export interface WidgetReportScope {
  /** What to send the server. */
  scope: ReportScope
  /** The chosen option's label, for the widget's header. */
  periodLabel: string
  tags: string[]
}

export function reportScope(instance: WidgetInstance, def: WidgetDefinition): WidgetReportScope {
  const setting = def.settings?.find((s) => s.key === 'period')
  const fallback = typeof setting?.default === 'string' ? setting.default : ''
  const chosen = instance.config?.period
  const label = typeof chosen === 'string' && setting?.options?.includes(chosen) ? chosen : fallback
  const raw = instance.config?.tags
  const tags = Array.isArray(raw)
    ? raw.filter((t): t is string => typeof t === 'string' && !!t)
    : []
  const period = label && label !== fallback ? PERIOD_BY_LABEL[label] : undefined
  return { scope: { period, tags }, periodLabel: label, tags }
}

/** "Aug – Oct 2026", or one month's name. */
export function monthSpan(from: string, to: string): string {
  if (from === to) return fMonth(from, 'short')
  return `${fMonth(from, 'short')} – ${fMonth(to, 'short')}`
}

/** The tag chips a header shows ("#trip"), or nothing. */
export function tagsText(tags: string[]): string {
  return tags.map((t) => `#${t}`).join(' ')
}
