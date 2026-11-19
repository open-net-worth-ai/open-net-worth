import { useFlag } from '../../hooks/useFlag'
import { TradingAgentsPanel } from './TradingAgentsPanel'

/** The Analysis-tab card for one equity: ask for a multi-agent research run, watch it, and read
 *  the rating and the reports behind it. Gated by the `trading_agents` flag: off (or not loaded
 *  yet) renders nothing. */
export function TradingAgentsCard({ ticker }: { instrumentId: string; ticker: string }) {
  const enabled = useFlag('trading_agents')
  if (enabled !== true) return null
  return <TradingAgentsPanel ticker={ticker} />
}
