import type { Performance } from '../../../types/trading'
import { Panel, EmptyState, Text, Stack } from '../../ui'
import { OvChart } from '../overview/charts'
import { cssToken } from '../../../utils/chartTheme'
import { tradingMoney, tradingTime } from './presentation'

export function TradingPerformance({ performance }: { performance: Performance }) {
  const history = performance.history ?? []
  return (
    <Panel title="Performance">
      <Stack gap="--space-4">
        <Text tone="muted">
          Started at {tradingMoney(performance.initial_equity, performance.base_currency)} · Current
          equity {tradingMoney(performance.equity, performance.base_currency)}
        </Text>
        {!performance.valuation_complete && (
          <Text tone="warn">
            Awaiting complete valuation. Missing or stale prices and FX are not treated as zero.
          </Text>
        )}
        {history.length ? (
          <OvChart
            sig={JSON.stringify(history)}
            config={{
              type: 'line',
              data: {
                labels: history.map((point) => tradingTime(point.timestamp)),
                datasets: [
                  {
                    label: `Equity (${performance.base_currency})`,
                    data: history.map((point) =>
                      point.equity == null ? null : Number(point.equity),
                    ),
                    borderColor: cssToken('--good'),
                    backgroundColor: cssToken('--good'),
                    pointRadius: 0,
                    spanGaps: false,
                  },
                ],
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
              },
            }}
          />
        ) : (
          <EmptyState
            title="No performance history yet"
            body="History appears as the environment advances and records valuations."
          />
        )}
      </Stack>
    </Panel>
  )
}
