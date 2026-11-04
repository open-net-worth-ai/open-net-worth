import { useState } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import type {
  EvidenceSelection,
  Performance,
  TradingAccount,
  TradingFill,
  TradingOrder,
  TradingRun,
} from '../../../types/trading'
import { ErrorState, Loading, PageTabs, Panel, Stack, SummaryBar, Text } from '../../ui'
import { TradingMarketChart } from './TradingMarketChart'
import { TradingHoldings } from './TradingHoldings'
import { TradingOrders } from './TradingOrders'
import { TradingPerformance } from './TradingPerformance'
import { TradingStatement } from './TradingStatement'
import { errorText, tradingMoney, tradingPercent } from './presentation'

export function TradingMarketPortfolio({
  run,
  performance,
  accounts,
  orders,
  fills,
  highlightedFillIds,
  busy,
  onCancel,
  onSelect,
}: {
  run: TradingRun
  performance?: Performance
  accounts: TradingAccount[]
  orders: TradingOrder[]
  fills: TradingFill[]
  highlightedFillIds?: string[]
  busy: boolean
  onCancel: (order: TradingOrder) => Promise<void>
  onSelect: (selection: EvidenceSelection) => void
}) {
  const [tab, setTab] = useState('market-chart')
  const series = useQueryCache(`${tradingRoot}/runs/${run.id}/market-series?limit=500`, () =>
    tradingApi.marketSeries(run.id),
  )
  return (
    <Panel title="Market & portfolio" className="trading-observation-panel">
      <Stack gap="--space-4">
        <Text size="sm" tone="muted">
          {run.mode} · Environment time {run.current_time} · Dataset {run.data_version}
        </Text>
        {performance && (
          <SummaryBar
            cards={[
              {
                label: 'Equity',
                value: tradingMoney(performance.equity, performance.base_currency),
                caption: performance.valuation_complete ? '' : 'Valuation incomplete',
              },
              { label: 'Return', value: tradingPercent(performance.return_pct) },
              {
                label: 'Available cash',
                value: tradingMoney(performance.available_cash, performance.base_currency),
              },
              { label: 'Drawdown', value: tradingPercent(performance.max_drawdown_pct) },
            ]}
          />
        )}
        {performance && (
          <Text size="sm">
            Realized {tradingMoney(performance.realized_pnl, performance.base_currency)} ·
            Unrealized {tradingMoney(performance.unrealized_pnl, performance.base_currency)} · Fees{' '}
            {tradingMoney(performance.fees, performance.base_currency)}
          </Text>
        )}
        <PageTabs
          ariaLabel="Market and portfolio sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'market-chart', label: 'Chart' },
            { key: 'market-positions', label: 'Positions' },
            { key: 'market-orders', label: 'Orders' },
            { key: 'market-statement', label: 'Statement' },
            { key: 'market-performance', label: 'Equity' },
          ]}
        />
        <div role="tabpanel" aria-labelledby={`ui-page-tab-${tab}`}>
          {tab === 'market-chart' && (
            <Stack gap="--space-4">
              {series.initialLoading && <Loading label="Loading recorded market data…" />}
              {!!series.error && (
                <ErrorState
                  message={errorText(series.error)}
                  onRetry={() => void series.refresh()}
                />
              )}
              {!series.initialLoading && !series.error && (
                <TradingMarketChart
                  observations={series.data?.items ?? []}
                  orders={orders}
                  fills={fills}
                  highlightedFillIds={highlightedFillIds}
                  onSelect={onSelect}
                />
              )}
            </Stack>
          )}
          {tab === 'market-positions' && <TradingHoldings accounts={accounts} />}
          {tab === 'market-orders' && (
            <TradingOrders
              orders={orders}
              fills={fills}
              busy={busy}
              onCancel={onCancel}
              onInspect={(order) => onSelect({ kind: 'order', id: order.id })}
            />
          )}
          {tab === 'market-statement' && <TradingStatement runId={run.id} onSelect={onSelect} />}
          {tab === 'market-performance' &&
            (performance ? (
              <TradingPerformance performance={performance} />
            ) : (
              <Text tone="muted">Performance is unavailable.</Text>
            ))}
        </div>
      </Stack>
    </Panel>
  )
}
