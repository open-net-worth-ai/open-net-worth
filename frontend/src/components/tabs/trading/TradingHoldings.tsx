import type { TradingAccount } from '../../../types/trading'
import { Panel, EmptyState, Badge, Stack, Text } from '../../ui'
import { tradingMoney } from './presentation'

export function TradingAccounts({ accounts }: { accounts: TradingAccount[] }) {
  return (
    <Panel title="Accounts">
      {!accounts.length ? (
        <EmptyState
          title="No eligible account linked"
          body="Create a run with an allocation. Broker connections are configured in Connectors."
        />
      ) : (
        <div className="trading-table-wrap">
          <table className="trading-table">
            <thead>
              <tr>
                <th>Account / venue</th>
                <th>Allocated</th>
                <th>Cash</th>
                <th>Available</th>
                <th>Reserved</th>
                <th>Reconciliation</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id}>
                  <td>
                    {account.name}
                    <Text as="div" size="xs" tone="muted">
                      {account.venue} · {account.currency}
                    </Text>
                  </td>
                  <td className="trading-number">
                    {tradingMoney(account.capital, account.currency)}
                  </td>
                  <td className="trading-number">{tradingMoney(account.cash, account.currency)}</td>
                  <td className="trading-number">
                    {tradingMoney(account.available_cash, account.currency)}
                  </td>
                  <td className="trading-number">
                    {tradingMoney(account.reserved_cash, account.currency)}
                  </td>
                  <td>
                    <Badge
                      tone={account.reconciliation_status === 'unverified' ? 'warn' : 'neutral'}
                    >
                      {account.reconciliation_status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
export function TradingHoldings({ accounts }: { accounts: TradingAccount[] }) {
  const positions = accounts
    .flatMap((account) =>
      Object.entries(account.positions).map(([key, position]) => ({
        key: `${account.id}:${key}`,
        account,
        position,
      })),
    )
    .filter(({ position }) => Number(position.quantity) !== 0)
  return (
    <Stack gap="--space-6">
      <Panel title="Holdings">
        {!positions.length ? (
          <EmptyState
            title="No positions yet"
            body="Filled trades will appear here, separately from your personal book."
          />
        ) : (
          <div className="trading-table-wrap">
            <table className="trading-table">
              <thead>
                <tr>
                  <th>Instrument / account</th>
                  <th>Quantity</th>
                  <th>Mark</th>
                  <th>Value</th>
                  <th>Cost basis</th>
                  <th>Unrealized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(({ key, account, position }) => (
                  <tr key={key}>
                    <td>
                      {position.instrument.symbol}
                      <Text as="div" size="xs" tone="muted">
                        {position.instrument.venue} · {account.name}
                      </Text>
                    </td>
                    <td className="trading-number">{String(position.quantity)}</td>
                    <td className="trading-number">
                      {tradingMoney(position.market_price, position.instrument.currency)}
                    </td>
                    <td className="trading-number">
                      {tradingMoney(position.market_value, position.instrument.currency)}
                    </td>
                    <td className="trading-number">
                      {tradingMoney(position.cost_basis, position.instrument.currency)}
                    </td>
                    <td className="trading-number">
                      {tradingMoney(position.unrealized_pnl, position.instrument.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <TradingAccounts accounts={accounts} />
    </Stack>
  )
}
