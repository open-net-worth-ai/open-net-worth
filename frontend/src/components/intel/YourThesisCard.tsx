import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { InfoPopover, Panel, Text } from '../ui'
import type { InvestmentThesis } from '../../types/assetDetail'
import { fPrice, getCurrencySymbol } from '../../utils/format'
import { ThesisEditor } from './ThesisEditor'
import { BREAKS_IF_LABEL, ROLES } from './thesisFields'

// "Your Thesis" — why you own (or would buy) this instrument and the prices that matter to you.
// One thesis per instrument (services/investment_theses); held vs watched only changes what is shown:
// a holding adds its role, breaks-if, stop and trim. Editing is ThesisEditor, the same form the
// alerts screen shows.

interface Props {
  instrumentId: string
  ticker: string
  thesis: InvestmentThesis | null
  held: boolean
  price: number | null
  /** Max fraction digits for prices (undefined = the formatter's own, for crypto). */
  priceMaxFrac?: number
  /** The instrument's quote currency — what the page price and every level are in. */
  currency?: string
  onSaved: () => void
}

interface Level {
  key: 'stop' | 'zone' | 'fair' | 'trim'
  label: string
  value: string
  at: number
}

function levelsOf(thesis: InvestmentThesis, held: boolean, fmt: (v: number) => string): Level[] {
  const out: Level[] = []
  if (held && thesis.stop_below != null)
    out.push({
      key: 'stop',
      label: 'Stop below',
      value: fmt(thesis.stop_below),
      at: thesis.stop_below,
    })
  if (thesis.buy_below != null)
    out.push({
      key: 'zone',
      label: 'Buy zone',
      value:
        thesis.buy_above != null
          ? `${fmt(thesis.buy_above)}–${fmt(thesis.buy_below)}`
          : `≤ ${fmt(thesis.buy_below)}`,
      at: thesis.buy_below,
    })
  if (thesis.fair_value != null)
    out.push({
      key: 'fair',
      label: 'Fair value',
      value: fmt(thesis.fair_value),
      at: thesis.fair_value,
    })
  if (held && thesis.trim_above != null)
    out.push({
      key: 'trim',
      label: 'Trim above',
      value: fmt(thesis.trim_above),
      at: thesis.trim_above,
    })
  return out
}

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
  levels: Level[]
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

export function YourThesisCard({
  instrumentId,
  ticker,
  thesis,
  held,
  price,
  priceMaxFrac,
  currency,
  onSaved,
}: Props) {
  // The saved thesis shows at once; the refetch's copy replaces it as soon as `thesis` changes.
  const [justSaved, setJustSaved] = useState<{
    over: InvestmentThesis | null
    thesis: InvestmentThesis | null
  } | null>(null)
  const current = justSaved && justSaved.over === thesis ? justSaved.thesis : thesis
  const [editing, setEditing] = useState(false)

  const sym = currency ? getCurrencySymbol(currency) : ''
  const fmt = (v: number) => `${sym}${fPrice(v, priceMaxFrac)}`
  const startEdit = () => setEditing(true)

  const role = held && current?.role ? ROLES.find((r) => r.value === current.role) : undefined
  const levels = current ? levelsOf(current, held, fmt) : []
  const hasContent =
    !!current && (!!current.reason || (held && !!current.breaks_if) || levels.length > 0)

  const actions = editing ? undefined : (
    <button type="button" className="asd-rail-manage" onClick={startEdit}>
      <Pencil size={12} aria-hidden="true" />
      {hasContent ? 'Edit' : 'Add'}
    </button>
  )

  const badge =
    role && !editing ? (
      <span className="asd-thesis-role-wrap">
        <span className="asd-thesis-role">{role.label}</span>
        <InfoPopover text={role.help} label={`What ${role.label} means`} />
      </span>
    ) : undefined

  return (
    <Panel
      title="Your Thesis"
      badge={badge}
      actions={actions}
      className="asd-rail-panel asd-thesis-card"
    >
      {editing ? (
        <ThesisEditor
          instrumentId={instrumentId}
          thesis={current}
          held={held}
          currency={currency}
          onSaved={(saved) => {
            setJustSaved({ over: thesis, thesis: saved })
            setEditing(false)
            onSaved()
          }}
          onCancel={() => setEditing(false)}
        />
      ) : !hasContent ? (
        <Text size="sm" tone="muted">
          {held ? `Write down why you own ${ticker}.` : `Write down why you’d buy ${ticker}.`}
        </Text>
      ) : (
        <div className="asd-thesis-body">
          {current?.reason && <p className="asd-thesis-reason">{current.reason}</p>}
          {held && current?.breaks_if && (
            <div className="asd-thesis-breaks">
              <p className="asd-thesis-breaks-label">{BREAKS_IF_LABEL}</p>
              <p className="asd-thesis-reason">{current.breaks_if}</p>
            </div>
          )}
          {levels.length > 0 && (
            <div>
              {price != null && current && (
                <PriceBar thesis={current} levels={levels} price={price} fmt={fmt} />
              )}
              <div className="asd-thesis-levels">
                {levels.map((l) => (
                  <div key={l.key} className="asd-pos-row">
                    <span className="asd-pos-label">
                      <span
                        className={`asd-thesis-key asd-thesis-key--${l.key}`}
                        aria-hidden="true"
                      />
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
      )}
    </Panel>
  )
}
