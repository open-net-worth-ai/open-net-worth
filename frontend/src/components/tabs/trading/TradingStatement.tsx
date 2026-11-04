import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import { Button, EmptyState, ErrorState, Loading, Panel, Stack, SummaryBar, Text } from '../../ui'
import type { EvidenceSelection } from '../../../types/trading'
import { errorText, readable, tradingMoney, tradingTime } from './presentation'

export function TradingStatement({
  runId,
  onSelect,
}: {
  runId: string
  onSelect: (selection: EvidenceSelection) => void
}) {
  const query = useQueryCache(`${tradingRoot}/runs/${runId}/statement`, () =>
    tradingApi.statement(runId),
  )
  const statement = query.data
  return (
    <Stack gap="--space-4">
      {query.initialLoading && <Loading label="Loading statement…" />}
      {!!query.error && (
        <ErrorState message={errorText(query.error)} onRetry={() => void query.refresh()} />
      )}
      {statement && (
        <>
          <Text weight={600}>Account statement</Text>
          <Text size="sm" tone="muted">
            {tradingTime(statement.period.start_at)} – {tradingTime(statement.period.as_of)}
          </Text>
          <SummaryBar
            cards={[
              {
                label: 'Opening equity',
                value: tradingMoney(statement.initial_equity, statement.base_currency),
              },
              {
                label: 'Closing equity',
                value: tradingMoney(statement.equity, statement.base_currency),
              },
              { label: 'Trading P&L', value: tradingMoney(statement.pnl, statement.base_currency) },
            ]}
          />
          <Text>
            Realized {tradingMoney(statement.realized_pnl, statement.base_currency)} · Unrealized{' '}
            {tradingMoney(statement.unrealized_pnl, statement.base_currency)} · Fees{' '}
            {tradingMoney(statement.fees, statement.base_currency)}
            {!statement.fees_complete ? ' (incomplete)' : ''}
          </Text>
          {!statement.valuation_complete && (
            <Text tone="warn">
              Valuation is incomplete. Missing prices and FX are not treated as zero.
            </Text>
          )}
          {statement.accounts.map((account) => (
            <Panel key={account.id} title={`${account.name} · ${account.currency}`}>
              <Stack gap="--space-4">
                <Text>
                  Opening cash {tradingMoney(account.opening_cash, account.currency)} · Closing cash{' '}
                  {tradingMoney(account.closing_cash, account.currency)}
                </Text>
                <Text>Cash movement {tradingMoney(account.cash_change, account.currency)}</Text>
                {account.movements.length ? (
                  <div className="trading-table-wrap">
                    <table className="trading-table">
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>Movement</th>
                          <th>Amount</th>
                          <th>Evidence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {account.movements.map((movement) => (
                          <tr key={movement.id}>
                            <td>{tradingTime(movement.timestamp)}</td>
                            <td>{readable(movement.kind)}</td>
                            <td className="trading-number">
                              {tradingMoney(movement.amount, account.currency)}
                            </td>
                            <td>
                              {movement.fill_id ? (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => onSelect({ kind: 'fill', id: movement.fill_id! })}
                                >
                                  Inspect fill
                                </Button>
                              ) : movement.order_id ? (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() =>
                                    onSelect({ kind: 'order', id: movement.order_id! })
                                  }
                                >
                                  Inspect order
                                </Button>
                              ) : (
                                'Not recorded'
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState title="No account movements recorded" />
                )}
                <Text size="sm">
                  Retained positions:{' '}
                  {Object.values(account.positions)
                    .filter((position) => Number(position.quantity) !== 0)
                    .map((position) => `${position.quantity} ${position.instrument.symbol}`)
                    .join(' · ') || 'None'}
                </Text>
              </Stack>
            </Panel>
          ))}
          <Text size="sm" tone="muted">
            Agent/model cost {tradingMoney(statement.model_cost_usd, 'USD')} · Reported separately
            from trading results
          </Text>
        </>
      )}
    </Stack>
  )
}
