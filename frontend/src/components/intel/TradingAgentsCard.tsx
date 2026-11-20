import { useEffect, useRef } from 'react'
import { useFlag } from '../../hooks/useFlag'
import { TradingAgentsPanel } from './TradingAgentsPanel'
import type { TaFocus, TaTakeView } from './useTaPanel'

/** The Analysis-tab card for one equity: ask for a multi-agent research run, watch it, and read
 *  the rating and the reports behind it. Gated by the `trading_agents` flag: off (or not loaded
 *  yet) renders nothing. `focus` opens one run in full and scrolls the card into view; the caller
 *  is told once it has been taken up. */
export function TradingAgentsCard({
  ticker,
  focus = null,
  takeView = null,
  onFocusHandled,
}: {
  instrumentId: string
  ticker: string
  focus?: TaFocus | null
  takeView?: TaTakeView | null
  onFocusHandled?: () => void
}) {
  const enabled = useFlag('trading_agents')
  const root = useRef<HTMLDivElement>(null)
  const seq = enabled === true ? (focus?.seq ?? 0) : 0
  useEffect(() => {
    if (seq === 0) return
    root.current?.scrollIntoView?.({ block: 'start' })
    onFocusHandled?.()
  }, [seq, onFocusHandled])
  if (enabled !== true) return null
  return (
    <div ref={root}>
      <TradingAgentsPanel key={ticker} ticker={ticker} focus={focus} takeView={takeView} />
    </div>
  )
}
