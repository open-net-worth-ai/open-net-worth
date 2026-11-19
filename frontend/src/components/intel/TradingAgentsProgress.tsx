import { useEffect, useState } from 'react'
import type { TaRun } from '../../types/tradingAgents'
import { fCurrency } from '../../utils/format'
import { fElapsed, runPlanLabel, stageRows } from '../../utils/tradingAgents'
import type { StageState } from '../../utils/tradingAgents'
import { Button, Cluster, Stack, Tag, Text } from '../ui'

const MARK: Record<StageState, string> = { done: '✓', active: '●', pending: '○' }
const SPOKEN: Record<StageState, string> = {
  done: 'Done: ',
  active: 'In progress: ',
  pending: 'Waiting: ',
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

function costLine(run: TaRun): string {
  const calls = `${run.llm_calls} model ${run.llm_calls === 1 ? 'call' : 'calls'}`
  const plan = runPlanLabel(run.config_snapshot)
  const parts: string[] = []
  if (plan && run.cost_usd === 0) {
    parts.push(`${calls} on your ${plan}`)
  } else {
    parts.push(
      calls,
      `${fCurrency(run.cost_usd, 'USD')} so far (of ${fCurrency(run.cap_usd, 'USD')})`,
    )
  }
  if (run.unpriced_calls > 0) parts.push(`${run.unpriced_calls} calls unpriced`)
  return parts.join(' · ')
}

/** A running (or queued) run: the five stages with their state, and what it has used so far.
 *  Only the active stage's text is a live region; the clock and the cost are not announced. */
export function TradingAgentsProgress({
  run,
  queuedBehind,
  cancelling,
  onCancel,
}: {
  run: TaRun
  /** Other runs ahead of this one in the queue. */
  queuedBehind: number
  cancelling: boolean
  onCancel: () => void
}) {
  const now = useNow()
  const started = Date.parse(run.started_at ?? run.created_at)
  const queued = run.status === 'queued'
  const rows = stageRows(run, now)

  return (
    <Stack gap="--space-5">
      <Cluster justify="between" gap="--space-4">
        <Text size="sm" weight={600}>
          {run.ticker} · analysing {run.trade_date}
        </Text>
        <Cluster gap="--space-3">
          <Tag tone="muted">{queued ? 'Queued' : 'Running'}</Tag>
          {run.requested_by === 'lucius' && <Tag tone="lucius">Asked by Lucius</Tag>}
          {run.requested_by === 'automation' && <Tag tone="muted">Automation</Tag>}
        </Cluster>
      </Cluster>
      {queued ? (
        <Text size="sm" tone="muted">
          {queuedBehind > 0
            ? `Queued behind ${queuedBehind} other ${queuedBehind === 1 ? 'run' : 'runs'}`
            : 'Starting…'}
        </Text>
      ) : (
        <div className="ta-stages" role="list" aria-label="Analysis stages">
          {rows.map((row) => (
            <div key={row.id} className={`ta-stage ta-stage--${row.state}`} role="listitem">
              <span className="ta-stage__mark" aria-hidden="true">
                {MARK[row.state]}
              </span>
              <span>
                <span className="ta-stage__label">
                  <span className="sr-only">{SPOKEN[row.state]}</span>
                  {row.label}
                </span>
                {row.sub && (
                  <span
                    className="ta-stage__sub"
                    {...(row.state === 'active' ? { role: 'status' } : {})}
                  >
                    {' '}
                    {row.sub}
                  </span>
                )}
              </span>
              <span className="ta-stage__time">
                {row.seconds === null ? '' : fElapsed(row.seconds)}
              </span>
            </div>
          ))}
        </div>
      )}
      {!queued && rows.every((row) => row.state === 'pending') && (
        <Text size="xs" tone="faint">
          Starting…
        </Text>
      )}
      <Text size="xs" tone="faint">
        Elapsed {fElapsed(Number.isNaN(started) ? 0 : (now - started) / 1000)} · {costLine(run)}
      </Text>
      <div className="ta-actions">
        <Button
          variant="secondary"
          size="sm"
          disabled={cancelling}
          aria-label={`Cancel analysis of ${run.ticker}`}
          onClick={onCancel}
        >
          {cancelling ? 'Cancelling…' : 'Cancel'}
        </Button>
      </div>
    </Stack>
  )
}
