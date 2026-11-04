import { useEffect, useRef, useState } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import type { EvidenceSelection, TradingEvent, TradingRun } from '../../../types/trading'
import {
  Badge,
  Button,
  Cluster,
  EmptyState,
  ErrorState,
  Loading,
  PageTabs,
  Panel,
  Stack,
  Text,
} from '../../ui'
import { CodeBlock } from '../../chat/CodeBlock'
import { errorText, readable, tradingMoney, tradingTime } from './presentation'
import { RunTools } from './RunTools'
import { TradingFiles } from './TradingFiles'

export function EventPayload({ event, runId }: { event: TradingEvent; runId?: string }) {
  const [expanded, setExpanded] = useState(false)
  const chunked = event.payload.availability === 'chunked'
  const query = useQueryCache(
    `${tradingRoot}/runs/${runId}/work/${event.id}`,
    () => tradingApi.workRecord(runId!, event.id),
    { enabled: !!runId && chunked && expanded },
  )
  return (
    <Stack gap="--space-3">
      {chunked && runId && (
        <Button size="sm" onClick={() => setExpanded(true)}>
          Load complete record
        </Button>
      )}
      {query.initialLoading && expanded && <Loading label="Loading complete record…" />}
      {!!query.error && (
        <ErrorState message={errorText(query.error)} onRetry={() => void query.refresh()} />
      )}
      <CodeBlock className="trading-code">
        {JSON.stringify(query.data?.payload ?? event.payload, null, 2)}
      </CodeBlock>
    </Stack>
  )
}
const displayText = (event: TradingEvent) => {
  if (event.kind === 'agent_input') return undefined
  const payload = event.payload
  return ['summary', 'content', 'text', 'message']
    .map((key) => payload[key])
    .find((value): value is string => typeof value === 'string')
}
function eventName(event: TradingEvent) {
  const name = event.payload.name ?? event.payload.tool_name
  return `${readable(event.kind)}${typeof name === 'string' ? ` · ${name}` : ''}`
}

export function TradingAgentWork({
  run,
  selection,
  highlightedEvent,
  onSelect,
}: {
  run: TradingRun
  selection: EvidenceSelection | null
  highlightedEvent?: TradingEvent | null
  onSelect: (selection: EvidenceSelection) => void
}) {
  const [tab, setTab] = useState('work-timeline')
  const [manualOffset, setOffset] = useState(0)
  const [follow, setFollow] = useState(true)
  const [ignoredSelection, setIgnoredSelection] = useState<string | null>(null)
  const head = useQueryCache(`${tradingRoot}/runs/${run.id}/events?offset=0&limit=50`, () =>
    tradingApi.events(run.id, 0),
  )
  const total = head.data?.total ?? 0
  const selectedPage =
    highlightedEvent?.sequence && highlightedEvent.id !== ignoredSelection
      ? Math.floor((highlightedEvent.sequence - 1) / 50) * 50
      : null
  const offset =
    selectedPage ?? (follow ? Math.floor(Math.max(0, total - 1) / 50) * 50 : manualOffset)
  const query = useQueryCache(
    `${tradingRoot}/runs/${run.id}/events?offset=${offset}&limit=50`,
    () => tradingApi.events(run.id, offset),
  )
  const events = (query.data?.items ?? []).filter((event) => event.kind !== 'agent_record_part')
  const files = events.filter(
    (event) =>
      event.kind.includes('script') ||
      event.kind.includes('file') ||
      event.kind.includes('artifact') ||
      Array.isArray(event.payload.artifacts),
  )
  const execution = events.filter(
    (event) =>
      event.kind.includes('agent_') ||
      event.kind.includes('script') ||
      event.kind.includes('executor') ||
      event.kind.includes('wake'),
  )
  const visible = tab === 'work-files' ? files : tab === 'work-execution' ? execution : events
  const hasReasoning = events.some((event) => event.kind === 'agent_reasoning')
  const stream = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (selectedPage != null)
      stream.current
        ?.querySelector<HTMLElement>('[data-trading-event-selected="true"]')
        ?.scrollIntoView?.({ block: 'nearest' })
    else if (follow && stream.current) stream.current.scrollTop = stream.current.scrollHeight
  }, [follow, events.length, offset, selectedPage])
  return (
    <Panel title="Agent work" className="trading-observation-panel">
      <Stack gap="--space-4">
        <Cluster justify="between">
          <Text weight={600}>{run.workspace_snapshot.agent_label}</Text>
          <Badge>{readable(run.agent_state)}</Badge>
        </Cluster>
        <Text size="sm" tone="muted">
          Agent cost {tradingMoney(run.model_cost_usd, 'USD')} · Separate from trading P&amp;L
        </Text>
        <PageTabs
          ariaLabel="Agent work sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'work-timeline', label: 'Timeline' },
            { key: 'work-files', label: 'Files' },
            { key: 'work-execution', label: 'Execution' },
          ]}
        />
        <div role="tabpanel" aria-labelledby={`ui-page-tab-${tab}`}>
          <Stack gap="--space-4">
            {query.initialLoading && <Loading label="Loading agent work…" />}
            {!!query.error && (
              <ErrorState
                message={`Updates interrupted. ${errorText(query.error)}`}
                onRetry={() => void query.refresh()}
              />
            )}
            {!query.initialLoading && !query.error && !visible.length && (
              <EmptyState
                title={
                  tab === 'work-files'
                    ? 'No recorded files on this page'
                    : 'No reported work on this page'
                }
                body="The workspace shows actual emitted records. This executor has not reported matching details here."
              />
            )}
            {tab === 'work-timeline' && !hasReasoning && (
              <Text size="sm" tone="muted">
                Provider reasoning was not supplied on this page. Recorded analysis and outputs
                appear below when available.
              </Text>
            )}
            {tab === 'work-execution' && (
              <Text size="sm" tone="muted">
                Only reported actor and execution records appear here. A child-agent hierarchy is
                unavailable unless the executor reports it.
              </Text>
            )}
            {tab === 'work-files' && <TradingFiles runId={run.id} onSelect={onSelect} />}
            <div
              ref={stream}
              className="trading-work-stream"
              aria-label="Recorded agent work"
              onScroll={(event) => {
                const element = event.currentTarget
                if (
                  element.scrollHeight - element.scrollTop - element.clientHeight > 80 &&
                  follow
                ) {
                  setOffset(offset)
                  setIgnoredSelection(highlightedEvent?.id ?? null)
                  setFollow(false)
                  setIgnoredSelection(highlightedEvent?.id ?? null)
                }
              }}
            >
              {visible.map((event) => (
                <article
                  key={event.id}
                  data-trading-event-selected={
                    selection?.kind === 'event' && selection.id === event.id ? 'true' : undefined
                  }
                  className={`trading-event${selection?.kind === 'event' && selection.id === event.id ? ' trading-event-selected' : ''}`}
                >
                  <Cluster justify="between">
                    <Text size="sm" weight={600}>
                      {eventName(event)}
                    </Text>
                    <Text size="xs" tone="muted">
                      {tradingTime(event.environment_time ?? event.created_at)}
                    </Text>
                  </Cluster>
                  {displayText(event) && (
                    <Text as="p" className="trading-mandate-text">
                      {displayText(event)}
                    </Text>
                  )}
                  {event.kind === 'agent_input' && (
                    <Text size="sm" tone="muted">
                      Recorded input. Expand the record to inspect the exact instructions and
                      environment supplied to the executor.
                    </Text>
                  )}
                  {event.kind === 'agent_reasoning' && (
                    <Text size="xs" tone="muted">
                      Provider-reported reasoning or summary ·{' '}
                      {String(event.payload.source ?? 'executor')}
                    </Text>
                  )}
                  <details>
                    <summary>Inputs, outputs and recorded details</summary>
                    <EventPayload event={event} runId={run.id} />
                  </details>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onSelect({ kind: 'event', id: event.id })}
                  >
                    Inspect linked evidence
                  </Button>
                </article>
              ))}
            </div>
          </Stack>
        </div>
        <Cluster justify="between">
          <Button
            size="sm"
            disabled={offset === 0}
            onClick={() => {
              setFollow(false)
              setIgnoredSelection(highlightedEvent?.id ?? null)
              setOffset(Math.max(0, offset - 50))
            }}
          >
            Earlier work
          </Button>
          <Text size="xs" tone="muted">
            {total ? `${offset + 1}–${Math.min(offset + 50, total)} of ${total}` : '0 records'}
          </Text>
          <Button
            size="sm"
            disabled={offset + 50 >= total}
            onClick={() => {
              setFollow(false)
              setIgnoredSelection(highlightedEvent?.id ?? null)
              setOffset(offset + 50)
            }}
          >
            Later work
          </Button>
          {(!follow || selectedPage != null) && (
            <Button
              size="sm"
              onClick={() => {
                setFollow(true)
                setIgnoredSelection(highlightedEvent?.id ?? null)
              }}
            >
              Follow latest
            </Button>
          )}
        </Cluster>
        <details>
          <summary>Executor connection and manual diagnostics</summary>
          <RunTools run={run} />
        </details>
      </Stack>
    </Panel>
  )
}
