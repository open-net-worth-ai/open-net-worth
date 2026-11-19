import { TA_STATUS_LABEL } from '../../../../types/tradingAgentsConstants'
import type { TaCostSummary } from '../../../../types/tradingAgents'
import { fDate } from '../../../../utils/format'
import { TradingAgentsRating } from '../../../intel/TradingAgentsRating'
import type { BarRow, Formatters } from './tradingAgentsCostRows'

/** The settings panels' proportion bars (`rd-bars`): label, track, value. */
export function Bars({ heading, rows }: { heading: string; rows: BarRow[] }) {
  return (
    <div className="rd-bars">
      <div className="rd-bars-lbl">{heading}</div>
      {rows.length === 0 ? (
        <span className="rd-help">Nothing recorded in this period.</span>
      ) : (
        rows.map((row) => (
          <div className="rd-bar" key={row.key}>
            <span className={'rd-bar-k' + (row.mono ? ' rd-mono' : '')} title={row.title}>
              {row.label}
            </span>
            <span className="rd-bar-track">
              <span style={{ width: `${Math.min(100, Math.max(0, row.share * 100))}%` }}></span>
            </span>
            <span className="rd-bar-v">
              {row.value}
              <em>{row.note}</em>
            </span>
          </div>
        ))
      )}
    </div>
  )
}

const MAX_DAYS = 60

/** Spend by day as a plain strip: spend has no up/down colouring, so not a sparkline. The
 *  figures are repeated in the table below, so the strip carries a text summary only. */
export function DayBars({
  days,
  formatCost,
}: {
  days: TaCostSummary['by_day']
  formatCost: Formatters['formatCost']
}) {
  const shown = days.slice(-MAX_DAYS)
  if (shown.length === 0) return null
  const peak = shown.reduce((best, d) => (d.cost_usd > best.cost_usd ? d : best), shown[0]!)
  const summary =
    peak.cost_usd > 0
      ? `Spend by day. Peak ${formatCost(peak.cost_usd)} on ${fDate(peak.date)}.`
      : 'No metered spend in this period.'
  return (
    <div>
      <div className="rd-bars-lbl">By day</div>
      <div className="ta-daybars" role="img" aria-label={summary}>
        {shown.map((d) => (
          <span
            key={d.date}
            className="ta-daybars__bar"
            style={{
              height: `${peak.cost_usd > 0 ? Math.max(4, (d.cost_usd / peak.cost_usd) * 100) : 4}%`,
            }}
          />
        ))}
      </div>
      <span className="rd-help">{summary}</span>
    </div>
  )
}

export function RecentRunsTable({
  recent,
  formatCost,
  formatTokens,
}: { recent: TaCostSummary['recent'] } & Formatters) {
  return (
    <div className="rd-service-cost-table-wrap">
      <table className="rd-service-cost-table">
        <caption className="sr-only">Recent runs</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Ticker</th>
            <th scope="col">Rating</th>
            <th scope="col">Status</th>
            <th scope="col">Calls</th>
            <th scope="col">Tokens</th>
            <th scope="col">Estimate</th>
            <th scope="col">Actual</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((run) => {
            const plan = run.cost_usd === 0 && run.plan_included_calls > 0
            return (
              <tr key={run.id}>
                <td>{fDate(run.created_at)}</td>
                <td>{run.ticker}</td>
                <td>{run.signal ? <TradingAgentsRating signal={run.signal} compact /> : '—'}</td>
                <td>{TA_STATUS_LABEL[run.status]}</td>
                <td>{run.calls}</td>
                <td>
                  {formatTokens(run.input_tokens)}/{formatTokens(run.output_tokens)}
                </td>
                <td>{run.estimate_usd === null ? '—' : formatCost(run.estimate_usd)}</td>
                <td>
                  {plan ? (
                    <span title="Ran on a subscription: no dollars">Plan</span>
                  ) : (
                    formatCost(run.cost_usd)
                  )}
                  {run.unpriced_calls > 0 && (
                    <span title={`No price for ${run.unpriced_calls} calls`}> !</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
