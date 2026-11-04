import { useState } from 'react'
import { useQueryCache } from '../../../state/queryCache'
import { tradingApi, tradingRoot } from '../../../api/trading'
import {
  Panel,
  Button,
  Badge,
  EmptyState,
  Loading,
  ErrorState,
  Stack,
  Cluster,
  Text,
  Modal,
} from '../../ui'
import type { TradingEvent } from '../../../types/trading'
import { errorText, readable, tradingTime } from './presentation'

export function TradingActivity({
  runId,
  artifactsOnly = false,
}: {
  runId: string
  artifactsOnly?: boolean
}) {
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<TradingEvent | null>(null)
  const query = useQueryCache(`${tradingRoot}/runs/${runId}/events?offset=${offset}&limit=50`, () =>
    tradingApi.events(runId, offset),
  )
  const events = (query.data?.items ?? []).filter(
    (event) =>
      !artifactsOnly ||
      event.kind.includes('script') ||
      (Array.isArray(event.payload.artifacts) && event.payload.artifacts.length > 0),
  )
  return (
    <Panel title={artifactsOnly ? 'Artifacts' : 'Activity'}>
      <Stack gap="--space-4">
        {query.initialLoading && <Loading label="Loading activity…" />}
        {!!query.error && (
          <ErrorState
            message={`Updates interrupted. ${errorText(query.error)}`}
            onRetry={() => void query.refresh()}
          />
        )}
        {!query.initialLoading && !query.error && !events.length && (
          <EmptyState
            title={artifactsOnly ? 'No artifacts on this page' : 'No agent activity yet'}
            body={
              artifactsOnly
                ? 'Script source, execution results and decision artifacts are retained with their activity.'
                : 'Decisions, orders, failures and lifecycle changes will appear here.'
            }
          />
        )}
        {events.map((event) => (
          <div key={event.id} className="trading-event">
            <Cluster justify="between">
              <Badge
                tone={
                  event.kind.includes('error') || event.kind.includes('failed') ? 'neg' : 'neutral'
                }
              >
                {readable(event.kind)}
              </Badge>
              <Text size="xs" tone="muted">
                {tradingTime(event.environment_time)} · received {tradingTime(event.created_at)}
              </Text>
            </Cluster>
            {typeof event.payload.summary === 'string' && (
              <Text as="p">{event.payload.summary}</Text>
            )}
            <Button variant="ghost" size="sm" onClick={() => setSelected(event)}>
              View {artifactsOnly ? 'artifact' : 'event'} details
            </Button>
          </div>
        ))}
        <Cluster justify="between">
          <Button
            disabled={offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 50))}
          >
            Previous events
          </Button>
          <Text size="xs" tone="muted">
            {query.data?.total ?? 0} events
          </Text>
          <Button
            disabled={offset + 50 >= (query.data?.total ?? 0)}
            onClick={() => setOffset((value) => value + 50)}
          >
            Next events
          </Button>
        </Cluster>
        {selected && (
          <Modal open onClose={() => setSelected(null)} title={readable(selected.kind)} size="lg">
            <pre className="trading-code">{JSON.stringify(selected.payload, null, 2)}</pre>
          </Modal>
        )}
      </Stack>
    </Panel>
  )
}
