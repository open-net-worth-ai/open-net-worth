import { useState } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import type { EvidenceSelection } from '../../../types/trading'
import { Badge, Button, EmptyState, ErrorState, Loading, Modal, PageTabs, Stack, Text } from '../../ui'
import { CodeBlock } from '../../chat/CodeBlock'
import { EventPayload } from './TradingAgentWork'
import { errorText, readable, tradingMoney, tradingTime } from './presentation'

export function TradingEvidenceInspector({
  runId,
  selection,
  onClose,
  onSelect,
}: {
  runId: string
  selection: EvidenceSelection
  onClose: () => void
  onSelect?: (selection: EvidenceSelection) => void
}) {
  const [tab, setTab] = useState('evidence-details')
  const query = useQueryCache(
    `${tradingRoot}/runs/${runId}/evidence/${selection.kind}/${selection.id}`,
    () => tradingApi.evidence(runId, selection),
  )
  const evidence = query.data
  return (
    <Modal open onClose={onClose} title="Linked trading evidence" size="xl">
      <Stack gap="--space-4">
        <Text size="sm" tone="muted">
          {readable(selection.kind)} · {selection.id}
        </Text>
        <PageTabs
          ariaLabel="Evidence sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'evidence-details', label: 'Details' },
            { key: 'evidence-data', label: 'Data' },
            { key: 'evidence-work', label: 'Agent work' },
            { key: 'evidence-files', label: 'Files' },
          ]}
        />
        {query.initialLoading && <Loading label="Loading evidence…" />}
        {!!query.error && (
          <ErrorState message={errorText(query.error)} onRetry={() => void query.refresh()} />
        )}
        {evidence && (
          <div role="tabpanel" aria-labelledby={`ui-page-tab-${tab}`}>
            <Stack gap="--space-4">
              {tab === 'evidence-details' && (
                <>
                  {evidence.orders?.map((order) => (
                    <Button
                      key={order.id}
                      variant="ghost"
                      onClick={() => onSelect?.({ kind: 'order', id: order.id })}
                    >
                      Inspect order {order.instrument.symbol} · {order.side}{' '}
                      {String(order.quantity)} · {readable(order.status)}
                    </Button>
                  ))}
                  {evidence.order ? (
                    <>
                      <Badge>{readable(evidence.order.status)}</Badge>
                      <Text>
                        {evidence.order.side} {String(evidence.order.quantity)}{' '}
                        {evidence.order.instrument.symbol} · {evidence.order.instrument.venue}
                      </Text>
                      <Text>
                        Order {evidence.order.id} · Broker{' '}
                        {evidence.order.broker_order_id ?? 'Not assigned'}
                      </Text>
                      <Text>
                        Effective mandate revision{' '}
                        {evidence.order.mandate_revision ?? 'Not recorded'}
                      </Text>
                      <CodeBlock className="trading-code">
                        {JSON.stringify(evidence.order, null, 2)}
                      </CodeBlock>
                    </>
                  ) : (
                    <Text tone="muted">
                      No order linked to this record. A decision may choose not to trade.
                    </Text>
                  )}
                  {evidence.fills.map((fill) => (
                    <Button
                      variant="ghost"
                      key={fill.id}
                      onClick={() => onSelect?.({ kind: 'fill', id: fill.id })}
                    >
                      {tradingTime(fill.timestamp)} · {String(fill.quantity)} @{' '}
                      {tradingMoney(
                        fill.price,
                        evidence.orders?.find((order) => order.id === fill.order_id)?.instrument
                          .currency ??
                          evidence.order?.instrument.currency ??
                          'USD',
                      )}{' '}
                      · Fees{' '}
                      {tradingMoney(
                        fill.fees,
                        evidence.orders?.find((order) => order.id === fill.order_id)?.instrument
                          .currency ??
                          evidence.order?.instrument.currency ??
                          'USD',
                      )}
                    </Button>
                  ))}
                  {!!evidence.missing_links.length && (
                    <Text tone="warn">{evidence.missing_links.join(' · ')}</Text>
                  )}
                </>
              )}
              {tab === 'evidence-data' && (
                <>
                  <Text weight={600}>{evidence.data_context.label}</Text>
                  <Text size="sm" tone="muted">
                    Availability cutoff {tradingTime(evidence.data_context.cutoff)} · Dataset{' '}
                    {evidence.data_context.data_version}
                  </Text>
                  {evidence.observations.length ? (
                    <CodeBlock className="trading-code">
                      {JSON.stringify(evidence.observations, null, 2)}
                    </CodeBlock>
                  ) : (
                    <EmptyState title="Data link not recorded" />
                  )}
                </>
              )}
              {tab === 'evidence-work' && (
                <>
                  {evidence.decision ? (
                    <>
                      <Text weight={600}>Recorded decision</Text>
                      <EventPayload event={evidence.decision} runId={runId} />
                    </>
                  ) : (
                    <Text tone="muted">Decision link not recorded.</Text>
                  )}
                  {evidence.events.map((event) => (
                    <details key={event.id}>
                      <summary>
                        {readable(event.kind)} ·{' '}
                        {tradingTime(event.environment_time ?? event.created_at)}
                      </summary>
                      <EventPayload event={event} runId={runId} />
                    </details>
                  ))}
                  {!evidence.events.length && (
                    <EmptyState
                      title="No linked work records"
                      body="Unrecorded rationale or provider reasoning is not reconstructed."
                    />
                  )}
                </>
              )}
              {tab === 'evidence-files' &&
                (evidence.files.length ? (
                  evidence.files.map((file, i) => (
                    <CodeBlock className="trading-code" key={i}>
                      {JSON.stringify(file, null, 2)}
                    </CodeBlock>
                  ))
                ) : (
                  <EmptyState title="No linked files recorded" />
                ))}
            </Stack>
          </div>
        )}
      </Stack>
    </Modal>
  )
}
