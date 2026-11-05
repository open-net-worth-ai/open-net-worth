// DriverChips — "today's drivers" mover strip shared by the Investments Brief
// and the Intel brief card. Visual style is the `.mvr-*` family (design.css),
// the Investments tab's original look — Intel's own `.intel-brief-mover*`
// markup was retired in favor of this shared component.

import { useMaskText } from '../../utils/amountMask'
import { fBase, fPct } from '../../utils/format'
import { StatInfo } from '../ui/StatInfo'
import type { DriverChip } from '../../types/portfolioDrivers'

interface Props {
  gainers: DriverChip[]
  losers: DriverChip[]
  onOpenAsset: (driver: DriverChip) => void
}

function sgn(v: number): string {
  return (v >= 0 ? '+' : '−') + fBase(Math.abs(v))
}

function uniqueDrivers(chips: DriverChip[]): DriverChip[] {
  const byInstrument = new Map<string, DriverChip>()
  for (const chip of chips) {
    const current = byInstrument.get(chip.instrument_id)
    byInstrument.set(
      chip.instrument_id,
      current
        ? {
            ...current,
            day_change_base: current.day_change_base + chip.day_change_base,
          }
        : chip,
    )
  }
  return [...byInstrument.values()]
}

export function DriverChips({ gainers, losers, onOpenAsset }: Props) {
  const maskText = useMaskText()
  const uniqueGainers = uniqueDrivers(gainers)
  const uniqueLosers = uniqueDrivers(losers)
  if (uniqueGainers.length + uniqueLosers.length === 0) return null
  return (
    <div className="intel-brief-movers">
      <span className="mvr-label">
        today's drivers{' '}
        <StatInfo
          text="Ranked by dollar impact on your portfolio today, not % move"
          className="stat-info-wrap--sm"
        />
      </span>
      <div className="mvr-list">
        {uniqueGainers.map((chip) => (
          <button
            type="button"
            className="mvr-chip"
            key={'g' + chip.instrument_id}
            title={maskText(
              `Open ${chip.ticker || chip.label} — ${sgn(chip.day_change_base)} today`,
            )}
            onClick={() => onOpenAsset(chip)}
          >
            <span className="mvr-sym">{chip.ticker || chip.label}</span>
            <span className="mvr-pct up">▲ {fPct(chip.day_change_pct)}</span>
          </button>
        ))}
        {uniqueGainers.length > 0 && uniqueLosers.length > 0 && <span className="mvr-div" />}
        {uniqueLosers.map((chip) => (
          <button
            type="button"
            className="mvr-chip"
            key={'l' + chip.instrument_id}
            title={maskText(
              `Open ${chip.ticker || chip.label} — ${sgn(chip.day_change_base)} today`,
            )}
            onClick={() => onOpenAsset(chip)}
          >
            <span className="mvr-sym">{chip.ticker || chip.label}</span>
            <span className="mvr-pct down">▼ {fPct(Math.abs(chip.day_change_pct))}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
