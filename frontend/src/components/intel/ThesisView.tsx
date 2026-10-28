import { Plus } from 'lucide-react'
import { Button, InfoPopover } from '../ui'
import type { InvestmentThesis } from '../../types/assetDetail'
import { BREAKS_IF_LABEL, thesisLevels, type ROLES, type ThesisLevel } from './thesisFields'

// ThesisView — the one read view of a stock's investment thesis: its reason, what would break it
// and its price levels against today's price. The asset page's Your Thesis card and the alerts
// screen's Why you're watching both show it, with an Edit button that swaps in ThesisEditor.

function distance(level: number, price: number): string {
  const d = (level / price - 1) * 100
  return `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)}%`
}

function PriceBar({
  thesis,
  levels,
  price,
  fmt,
}: {
  thesis: InvestmentThesis
  levels: ThesisLevel[]
  price: number
  fmt: (v: number) => string
}) {
  const zoneLow = thesis.buy_below != null ? (thesis.buy_above ?? null) : null
  const points = [price, ...levels.map((l) => l.at), ...(zoneLow != null ? [zoneLow] : [])]
  const lo = Math.min(...points) * 0.9
  const hi = Math.max(...points) * 1.05
  const pct = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(2)}%`
  const zoneFrom =
    thesis.buy_below != null ? (zoneLow ?? Math.max(lo, thesis.buy_below * 0.88)) : null
  const label = `Price ${fmt(price)} against your thesis: ${levels.map((l) => `${l.label} ${l.value}`).join(', ')}`
  return (
    <div className="asd-thesis-bar" role="img" aria-label={label}>
      <div className="asd-thesis-track" />
      {thesis.buy_below != null && zoneFrom != null && (
        <div
          className="asd-thesis-zone"
          style={{
            left: pct(zoneFrom),
            width: `${(((thesis.buy_below - zoneFrom) / (hi - lo)) * 100).toFixed(2)}%`,
          }}
        />
      )}
      {levels
        .filter((l) => l.key !== 'zone')
        .map((l) => (
          <div
            key={l.key}
            className={`asd-thesis-tick asd-thesis-tick--${l.key}`}
            style={{ left: pct(l.at) }}
          />
        ))}
      <div className="asd-thesis-now" style={{ left: pct(price) }} />
      <div className="asd-thesis-now-label" style={{ left: pct(price) }}>
        {fmt(price)}
      </div>
    </div>
  )
}

/** No thesis yet: the prompt is the button that starts one. */
export function ThesisStart({
  sym,
  held,
  onClick,
}: {
  sym: string
  held: boolean
  onClick: () => void
}) {
  return (
    <Button
      variant="secondary"
      size="sm"
      iconLeft={<Plus size={13} aria-hidden="true" />}
      onClick={onClick}
    >
      {held ? `Write down why you own ${sym}` : `Write down why you’d buy ${sym}`}
    </Button>
  )
}

/** The role chip with its one-line meaning. */
export function ThesisRoleBadge({ role }: { role: (typeof ROLES)[number] }) {
  return (
    <span className="asd-thesis-role-wrap">
      <span className="asd-thesis-role">{role.label}</span>
      <InfoPopover text={role.help} label={`What ${role.label} means`} />
    </span>
  )
}

export function ThesisView({
  thesis,
  held,
  price,
  fmt,
}: {
  thesis: InvestmentThesis
  held: boolean
  price: number | null
  fmt: (v: number) => string
}) {
  const levels = thesisLevels(thesis, held, fmt)
  return (
    <div className="asd-thesis-body">
      {thesis.reason && <p className="asd-thesis-reason">{thesis.reason}</p>}
      {held && thesis.breaks_if && (
        <div className="asd-thesis-breaks">
          <p className="asd-thesis-breaks-label">{BREAKS_IF_LABEL}</p>
          <p className="asd-thesis-reason">{thesis.breaks_if}</p>
        </div>
      )}
      {levels.length > 0 && (
        <div>
          {price != null && <PriceBar thesis={thesis} levels={levels} price={price} fmt={fmt} />}
          <div className="asd-thesis-levels">
            {levels.map((l) => (
              <div key={l.key} className="asd-pos-row">
                <span className="asd-pos-label">
                  <span className={`asd-thesis-key asd-thesis-key--${l.key}`} aria-hidden="true" />
                  {l.label}
                </span>
                <span className="asd-pos-value">
                  {l.value}
                  {price != null && (
                    <span className="asd-thesis-dist">{distance(l.at, price)}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
