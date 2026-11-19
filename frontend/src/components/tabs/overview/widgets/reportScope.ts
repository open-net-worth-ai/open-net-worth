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

/** The option that reads its months from the widget's `periodFrom` / `periodTo` (YYYY-MM). */
export const CUSTOM_PERIOD = 'Custom'
/** Widgets that show one figure for the whole period (a breakdown, a flow, a gauge). */
export const TOTAL_PERIOD_OPTIONS = [...Object.keys(PERIOD_BY_LABEL), CUSTOM_PERIOD]
/** Widgets that chart month by month: a one-month window would be a single bar. */
export const SERIES_PERIOD_OPTIONS = [
  '3 months',
  '6 months',
  '12 months',
  'This year',
  CUSTOM_PERIOD,
]

// All three are drawn by ReportFilterControls, in the header pill and in the settings panel.
export function periodSetting(options: string[], fallback: string): WidgetSettingSchema {
  return {
    key: 'period',
    type: 'segment',
    label: 'Period',
    options,
    default: fallback,
    inline: true,
  }
}

export const TAGS_SETTING: WidgetSettingSchema = {
  key: 'tags',
  type: 'tags',
  label: 'Tags',
  default: [],
  inline: true,
}

export const CATEGORIES_SETTING: WidgetSettingSchema = {
  key: 'categories',
  type: 'categoryIds',
  label: 'Categories',
  default: [],
  inline: true,
}

export interface WidgetReportScope {
  /** What to send the server. */
  scope: ReportScope
  /** The chosen option's label ("3 months", "Custom"), to mark which one is picked. */
  periodLabel: string
  /** What the period reads as on the widget: the label, or a custom range's months. */
  periodText: string
  /** A custom range's months, when Custom is picked and both are set. */
  custom: { from: string; to: string } | null
  tags: string[]
  categories: string[]
}

function stringList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((t): t is string => typeof t === 'string' && !!t) : []
}

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/

export function reportScope(instance: WidgetInstance, def: WidgetDefinition): WidgetReportScope {
  const setting = def.settings?.find((s) => s.key === 'period')
  const fallback = typeof setting?.default === 'string' ? setting.default : ''
  const chosen = instance.config?.period
  const label = typeof chosen === 'string' && setting?.options?.includes(chosen) ? chosen : fallback
  const tags = stringList(instance.config?.tags)
  const categories = stringList(instance.config?.categories)
  const from = instance.config?.periodFrom
  const to = instance.config?.periodTo
  const custom =
    label === CUSTOM_PERIOD &&
    typeof from === 'string' &&
    typeof to === 'string' &&
    MONTH.test(from) &&
    MONTH.test(to) &&
    from <= to
      ? { from, to }
      : null
  const period =
    label === CUSTOM_PERIOD
      ? custom
        ? `${custom.from}:${custom.to}`
        : undefined
      : label && label !== fallback
        ? PERIOD_BY_LABEL[label]
        : undefined
  const periodText = custom ? monthSpan(custom.from, custom.to) : label
  return {
    scope: { period, tags, categories },
    periodLabel: label,
    periodText,
    custom,
    tags,
    categories,
  }
}

/** "Aug – Oct", or one month's name; a range across years names both ("Nov 2025 – Feb"). */
export function monthSpan(from: string, to: string): string {
  if (from === to) return fMonth(from, 'short')
  return `${fMonth(from, 'short')} – ${fMonth(to, 'short')}`
}

/** The tag chips a header shows ("#trip"), or nothing. */
export function tagsText(tags: string[]): string {
  return tags.map((t) => `#${t}`).join(' ')
}
