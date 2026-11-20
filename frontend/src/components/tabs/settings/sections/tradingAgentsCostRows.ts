import {
  TA_CLI_LABEL,
  TA_CLI_PROVIDERS,
  TA_ROLE_LABEL,
} from '../../../../types/tradingAgentsConstants'
import type { TaCostSummary } from '../../../../types/tradingAgents'

export interface Formatters {
  formatCost: (value: number) => string
  formatTokens: (value: number) => string
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

const humanize = (id: string) => {
  const words = id.replace(/_/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export interface BarRow {
  key: string
  label: string
  title?: string
  mono?: boolean
  /** 0-1 share of the track. */
  share: number
  value: string
  note: string
}

function costValue(part: { cost_usd: number; plan_included_calls: number }, fmt: Formatters) {
  return part.cost_usd === 0 && part.plan_included_calls > 0
    ? 'plan-included'
    : fmt.formatCost(part.cost_usd)
}

export function roleRows(summary: TaCostSummary, fmt: Formatters): BarRow[] {
  const byCalls = summary.totals.metered_usd === 0
  const base = byCalls ? summary.totals.calls : summary.totals.metered_usd
  return Object.entries(summary.by_role)
    .sort(([, a], [, b]) => (byCalls ? b.calls - a.calls : b.cost_usd - a.cost_usd))
    .map(([role, part]) => ({
      key: role,
      label: TA_ROLE_LABEL[role] ?? humanize(role),
      share: base ? (byCalls ? part.calls : part.cost_usd) / base : 0,
      value: costValue(part, fmt),
      note: plural(part.calls, 'call'),
    }))
}

function transportLabel(transport: string | null): string {
  return transport && TA_CLI_PROVIDERS.has(transport)
    ? `${TA_CLI_LABEL[transport] ?? transport} plan`
    : 'API'
}

export function modelRows(summary: TaCostSummary, fmt: Formatters): BarRow[] {
  const base = summary.totals.metered_usd
  return Object.entries(summary.by_model)
    .sort(([, a], [, b]) => b.cost_usd - a.cost_usd || b.calls - a.calls)
    .map(([ref, part]) => ({
      key: ref,
      label: `${ref.slice(ref.indexOf(':') + 1)} · ${transportLabel(part.transport)}`,
      title: ref,
      mono: true,
      share: base ? part.cost_usd / base : 0,
      value: costValue(part, fmt),
      note: `${plural(part.calls, 'call')} · ${fmt.formatTokens(part.input_tokens + part.output_tokens)} tokens`,
    }))
}

export function tickerRows(summary: TaCostSummary, fmt: Formatters): BarRow[] {
  const base = summary.totals.metered_usd
  return summary.by_ticker.map((part) => ({
    key: part.ticker,
    label: part.ticker,
    share: base ? part.cost_usd / base : 0,
    value: costValue(part, fmt),
    note: plural(part.runs, 'run'),
  }))
}
