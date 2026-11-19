import { useFlag } from '../../../../hooks/useFlag'
import { useTaCost, useTaStatus } from '../../../../hooks/useTradingAgents'
import { TA_CLI_PROVIDERS } from '../../../../types/tradingAgentsConstants'
import type { TaCostSummary } from '../../../../types/tradingAgents'
import { fCurrency } from '../../../../utils/format'
import { ErrorState, Loading, Meter } from '../../../ui'
import { Panel } from '../ds'
import { costWindowQuery } from './costWindow'
import type { CostWindow } from './costWindow'
import { Bars, DayBars, RecentRunsTable } from './TradingAgentsCostParts'
import { modelRows, roleRows, tickerRows } from './tradingAgentsCostRows'

const USD = (n: number) => fCurrency(n, 'USD')

const NOT_ENABLED = new Set(['flag_off', 'connector_off', 'connector_paused'])

interface Props {
  window: CostWindow
  windowLabel: string
  formatCost: (value: number) => string
  formatTokens: (value: number) => string
}

function MonthLine({ month }: { month: TaCostSummary['month'] }) {
  const pct = month.pct_used
  return (
    <div className="rd-bars">
      {pct === null ? (
        <span className="rd-help">Metered runs are off (monthly cap is $0).</span>
      ) : (
        <Meter
          size="md"
          value={pct * 100}
          tone={pct >= 1 ? 'bad' : pct >= 0.8 ? 'warn' : 'good'}
          ariaLabel="TradingAgents spend this month"
          valueText={`${USD(month.spend_usd)} of ${USD(month.cap_usd)}`}
        />
      )}
      <span className="rd-help">
        This month {USD(month.spend_usd)} of {USD(month.cap_usd)} · Per run{' '}
        {USD(month.per_run_cap_usd)} cap · Max {month.max_llm_calls} calls per run
      </span>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rd-stat">
      <span className="rd-stat-lbl">{label}</span>
      <span className="rd-stat-val">{value}</span>
    </div>
  )
}

/** Plan-only runs record tokens but no dollars: their tokens are the models on a subscription. */
function planTokens(summary: TaCostSummary): { input: number; output: number } {
  return Object.values(summary.by_model)
    .filter((m) => m.transport && TA_CLI_PROVIDERS.has(m.transport))
    .reduce(
      (sum, m) => ({ input: sum.input + m.input_tokens, output: sum.output + m.output_tokens }),
      { input: 0, output: 0 },
    )
}

function Body({ summary, props }: { summary: TaCostSummary; props: Props }) {
  const fmt = { formatCost: props.formatCost, formatTokens: props.formatTokens }
  const { totals } = summary
  const plan = planTokens(summary)
  const outcome = [
    `${totals.done} done`,
    `${totals.failed} failed`,
    `${totals.stopped} stopped`,
    totals.active > 0 ? `${totals.active} running` : '',
  ].filter(Boolean)
  if (totals.runs === 0) {
    return (
      <>
        <MonthLine month={summary.month} />
        <div className="rd-service-cost-empty">No TradingAgents runs in this period.</div>
      </>
    )
  }
  return (
    <>
      <MonthLine month={summary.month} />
      <div className="rd-stats rd-cost-stats">
        <Stat label="Metered spend" value={props.formatCost(totals.metered_usd)} />
        <Stat label="Runs" value={totals.runs.toLocaleString()} />
        <Stat label="Average per run" value={props.formatCost(totals.avg_cost_per_run_usd)} />
        <Stat label="Median per run" value={props.formatCost(totals.median_cost_per_run_usd)} />
        <Stat label="Model calls" value={totals.calls.toLocaleString()} />
        <Stat
          label="Tokens in / out"
          value={`${props.formatTokens(totals.input_tokens)} / ${props.formatTokens(totals.output_tokens)}`}
        />
        {totals.plan_included_calls > 0 && (
          <Stat label="Plan-included calls" value={totals.plan_included_calls.toLocaleString()} />
        )}
      </div>
      {totals.plan_included_calls > 0 && (
        <span className="rd-help">
          {props.formatTokens(plan.input)} in / {props.formatTokens(plan.output)} out tokens on a
          subscription. Counted, not billed.
        </span>
      )}
      <span className="rd-help">{outcome.join(' · ')}</span>
      {totals.unpriced_calls > 0 && (
        <span className="rd-help" role="status">
          {totals.unpriced_calls} calls had no price; real spend is higher.
        </span>
      )}
      <Bars
        heading={totals.metered_usd === 0 ? 'By role (share of calls)' : 'By role'}
        rows={roleRows(summary, fmt)}
      />
      <Bars heading="By model" rows={modelRows(summary, fmt)} />
      <Bars heading="By ticker" rows={tickerRows(summary, fmt)} />
      <DayBars days={summary.by_day} formatCost={props.formatCost} />
      <RecentRunsTable recent={summary.recent} {...fmt} />
    </>
  )
}

/** Settings → Cost & Performance: TradingAgents runs, spend and limits, under the section's one
 *  period picker. Nothing renders while the flag is off or not loaded, nor for an owner who never
 *  enabled the connector and has no runs. */
export function TradingAgentsCostPanel(props: Props) {
  const flag = useFlag('trading_agents')
  if (flag !== true) return null
  return <CostPanel {...props} />
}

function CostPanel(props: Props) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const status = useTaStatus()
  const cost = useTaCost(costWindowQuery(props.window, timezone), true)
  const summary = cost.data
  const connectorOn = !!status.data && !NOT_ENABLED.has(status.data.availability.status)
  const hasRuns = !!summary && (summary.totals.runs > 0 || summary.totals.calls > 0)
  if (!connectorOn && !hasRuns) return null

  return (
    <Panel
      title="TradingAgents"
      sub="Multi-agent stock research · runs, spend and limits"
      actions={
        summary ? (
          <div className="rd-service-cost-total">
            <span>{`Metered spend · ${props.windowLabel}`}</span>
            <strong>{props.formatCost(summary.totals.metered_usd)}</strong>
          </div>
        ) : undefined
      }
    >
      {summary ? (
        <Body summary={summary} props={props} />
      ) : cost.error ? (
        <ErrorState
          title="Couldn't load TradingAgents spend"
          message="Try again."
          onRetry={() => void cost.refresh()}
        />
      ) : (
        <Loading label="Loading TradingAgents spend…" />
      )}
    </Panel>
  )
}
