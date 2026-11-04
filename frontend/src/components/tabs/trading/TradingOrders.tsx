import { useState } from 'react'
import type { TradingOrder, TradingFill } from '../../../types/trading'
import { Panel, Button, Badge, EmptyState, Modal, Stack, Text, ConfirmDialog } from '../../ui'
import { readable, statusTone, tradingTime, tradingMoney } from './presentation'

const cancelable = new Set(['pending', 'open', 'partially_filled'])
export function TradingOrders({
  orders,
  fills,
  onCancel,
  busy,
  onInspect,
}: {
  orders: TradingOrder[]
  fills: TradingFill[]
  onCancel: (order: TradingOrder) => Promise<void>
  busy: boolean
  onInspect?: (order: TradingOrder) => void
}) {
  const [selectedId, setSelected] = useState<string | null>(null)
  const selected = orders.find((order) => order.id === selectedId)
  const [cancel, setCancel] = useState<TradingOrder | null>(null)
  return (
    <>
      <Panel title="Orders">
        {!orders.length ? (
          <EmptyState
            title="No orders yet"
            body="An order acknowledgement and its eventual fills are shown separately."
          />
        ) : (
          <div className="trading-table-wrap">
            <table className="trading-table">
              <thead>
                <tr>
                  <th>Submitted</th>
                  <th>Instrument / venue</th>
                  <th>Side</th>
                  <th>Quantity</th>
                  <th>Filled</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <td>{tradingTime(order.created_at)}</td>
                    <td>
                      {order.instrument.symbol}
                      <Text as="div" size="xs" tone="muted">
                        {order.instrument.venue}
                      </Text>
                    </td>
                    <td>{order.side}</td>
                    <td className="trading-number">{String(order.quantity)}</td>
                    <td className="trading-number">{String(order.filled_quantity ?? 0)}</td>
                    <td>
                      <Badge tone={statusTone(order.status)}>{readable(order.status)}</Badge>
                    </td>
                    <td>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => (onInspect ? onInspect(order) : setSelected(order.id))}
                        aria-label={`View order ${order.instrument.symbol}`}
                      >
                        Details
                      </Button>
                      {cancelable.has(order.status) && (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() => setCancel(order)}
                          aria-label={`Cancel order ${order.instrument.symbol}`}
                        >
                          Cancel
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {selected && (
        <Modal
          open
          onClose={() => setSelected(null)}
          title={`Order · ${selected.instrument.symbol}`}
          size="lg"
        >
          <Stack gap="--space-4">
            <Badge tone={statusTone(selected.status)}>{readable(selected.status)}</Badge>
            <Text>Request: {selected.request_id}</Text>
            <Text>Broker order: {selected.broker_order_id ?? 'Not assigned'}</Text>
            <Text>
              {selected.side} {String(selected.quantity)} · {selected.order_type}
            </Text>
            {selected.status === 'submission_unknown' && (
              <Text tone="warn">
                The broker outcome is unknown. Reconcile the order before submitting another order.
              </Text>
            )}
            <Panel title="Fills">
              {fills.filter((fill) => fill.order_id === selected.id).length ? (
                fills
                  .filter((fill) => fill.order_id === selected.id)
                  .map((fill) => (
                    <Text as="p" key={fill.id}>
                      {tradingTime(fill.timestamp)} · {String(fill.quantity)} @{' '}
                      {tradingMoney(fill.price, selected.instrument.currency)} · Fees{' '}
                      {tradingMoney(fill.fees, selected.instrument.currency)}
                    </Text>
                  ))
              ) : (
                <EmptyState title="No fills recorded" />
              )}
            </Panel>
          </Stack>
        </Modal>
      )}
      <ConfirmDialog
        data={
          cancel
            ? {
                title: 'Cancel order',
                body: 'Request cancellation. An order may still fill before the broker confirms cancellation.',
                confirmLabel: 'Request cancellation',
                confirmDisabled: busy,
              }
            : null
        }
        onCancel={() => setCancel(null)}
        onConfirm={() => {
          if (cancel) void onCancel(cancel).then(() => setCancel(null))
        }}
      />
    </>
  )
}
