import type { TradingComparison } from '../../../types/trading'
import { Stack, Text } from '../../ui'
import { tradingMoney, tradingTime } from './presentation'

export function ComparisonScenario({ comparison }: { comparison: TradingComparison }) {
  const scenario = comparison.scenario
  if (!scenario) return <Text tone="muted">Scenario details are unavailable.</Text>
  return (
    <Stack gap="--space-4">
      <Text>
        Period: {tradingTime(scenario.period.start_at)} – {tradingTime(scenario.period.end_at)}
      </Text>
      <Text>
        Capital:{' '}
        {Object.entries(scenario.capital_by_currency)
          .map(([currency, value]) => tradingMoney(value, currency))
          .join(' · ')}{' '}
        · Reporting currency: {scenario.base_currency}
      </Text>
      <Text>Dataset: {scenario.data_version}</Text>
      <Text>
        Universe:{' '}
        {scenario.instruments.length
          ? scenario.instruments
              .map(
                (instrument) =>
                  `${instrument.symbol} (${instrument.asset_class}, ${instrument.venue}, ${instrument.currency})`,
              )
              .join(' · ')
          : 'No permitted instruments recorded'}
      </Text>
      <Text>
        Fee: {String(scenario.fee_bps)} bps · Slippage: {String(scenario.slippage_bps)} bps ·
        Maximum volume participation: {String(scenario.max_participation)}
      </Text>
      {comparison.limitations?.map((limitation) => (
        <Text key={limitation} size="sm" tone="muted">
          {limitation}
        </Text>
      ))}
    </Stack>
  )
}
