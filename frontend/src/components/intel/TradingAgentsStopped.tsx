import type { TaRun, TaRunSummary } from '../../types/tradingAgents'
import { fCurrency, timeAgo } from '../../utils/format'
import { Badge, Button, Cluster, Stack, Text } from '../ui'
import type { BadgeTone } from '../ui'

const USD = (n: number) => fCurrency(n, 'USD')

function describe(run: TaRunSummary): { label: string; tone: BadgeTone; why: string } {
  switch (run.status) {
    case 'stopped_budget':
      return {
        label: 'Stopped: budget',
        tone: 'warn',
        why: `It reached its ${USD(run.cap_usd)} per-run cap after ${run.llm_calls} calls.`,
      }
    case 'stopped_calls':
      return { label: 'Stopped: call limit', tone: 'warn', why: 'It reached its model-call limit.' }
    case 'timed_out':
      return { label: 'Timed out', tone: 'warn', why: 'It ran out of time before it finished.' }
    case 'cancelled':
      return {
        label: 'Cancelled',
        tone: 'muted',
        why: run.requested_by === 'user' ? 'You cancelled this run.' : 'Cancelled.',
      }
    case 'interrupted':
      return { label: 'Interrupted', tone: 'warn', why: 'The app restarted while it was running.' }
    default:
      return { label: 'Failed', tone: 'neg', why: run.error || 'The run failed.' }
  }
}

/** A run that ended without a rating: why, what it cost before it stopped, and a retry that
 *  starts a fresh run (the old one stays in History). Partial reports are not shown. */
export function TradingAgentsStopped({
  run,
  retrying,
  retryError,
  onRetry,
}: {
  run: TaRun | TaRunSummary
  retrying: boolean
  retryError: string | null
  onRetry: () => void
}) {
  const { label, tone, why } = describe(run)
  return (
    <Stack gap="--space-4">
      <Cluster justify="between" gap="--space-4">
        <Badge tone={tone}>{label}</Badge>
        <Button variant="primary" size="sm" loading={retrying} onClick={onRetry}>
          Retry
        </Button>
      </Cluster>
      <Text size="sm">{why}</Text>
      <Text size="xs" tone="faint">
        {run.llm_calls} calls · {USD(run.cost_usd)} spent before it stopped ·{' '}
        {timeAgo(run.finished_at ?? run.created_at)}
      </Text>
      {retryError && (
        <Text size="xs" tone="bad">
          {retryError}
        </Text>
      )}
    </Stack>
  )
}
