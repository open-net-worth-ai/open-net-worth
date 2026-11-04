import type { TradingComparison } from '../../../types/trading'
import { EmptyState, Panel, Stack, Text } from '../../ui'
import { OvChart } from '../overview/charts'
import { cssToken } from '../../../utils/chartTheme'
import { tradingTime } from './presentation'

export function ComparisonPerformance({
  results,
}: {
  results: NonNullable<TradingComparison['results']>
}) {
  const timestamps = [
    ...new Set(results.flatMap((result) => (result.history ?? []).map((point) => point.timestamp))),
  ].sort((a, b) => new Date(a).getTime() - new Date(b).getTime())
  const colors = ['--good', '--apt', '--warn', '--bad', '--ink']
  return (
    <Panel title="Performance comparison">
      <Stack gap="--space-4">
        <Text tone="muted">
          Account equity in the shared reporting currency. Missing observations remain gaps; agent
          usage costs are shown separately.
        </Text>
        {timestamps.length ? (
          <OvChart
            sig={JSON.stringify(results)}
            config={{
              type: 'line',
              data: {
                labels: timestamps.map(tradingTime),
                datasets: results.map((result, index) => {
                  const points = new Map(
                    (result.history ?? []).map((point) => [point.timestamp, point.equity]),
                  )
                  return {
                    label: `${result.agent_label ?? 'Agent'} · ${result.mode ?? 'run'} · ${result.run_id.slice(-12)}`,
                    data: timestamps.map((timestamp) => {
                      const value = points.get(timestamp)
                      return value == null ? null : Number(value)
                    }),
                    borderColor: cssToken(colors[index % colors.length]!),
                    backgroundColor: cssToken(colors[index % colors.length]!),
                    borderDash: index >= colors.length ? [6, 3] : [],
                    pointRadius: 0,
                    spanGaps: false,
                  }
                }),
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: true } },
              },
            }}
          />
        ) : (
          <EmptyState
            title="No comparison history yet"
            body="Recorded backend valuations will appear as each run advances."
          />
        )}
      </Stack>
    </Panel>
  )
}
