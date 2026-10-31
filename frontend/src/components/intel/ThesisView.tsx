import { Plus } from 'lucide-react'
import { useChatComposerOptional } from '../../state/chatComposer'
import { BuildWithLucius } from './BuildWithLucius'
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
/** The brief "Build with Lucius" hands the chat: interview the user one question at a time for
 *  every field the thesis form has, ground suggestions in their real position and the news, then
 *  save it. Held and watched differ the way the form does (role/breaks-if/stop/trim vs buy zone
 *  and fair value). */
export function thesisBuildPrompt(sym: string, held: boolean): string {
  const fields = held
    ? 'why I own it; its role in my portfolio (core, satellite or speculative); how long I plan ' +
      'to hold it; what would prove me wrong (for a company, the business fact that breaks the ' +
      'thesis; for a fund, the market or fund fact); the price below which I would sell (my ' +
      'stop); and the price above which I would take some profit (my trim level)'
    : 'why I would buy it; what would prove the case wrong; the price range I would buy in (my ' +
      'buy zone); and what I think it is worth (fair value)'
  const picture = held
    ? 'this position (cost, weight, gain or loss), how concentrated it makes me and how much it ' +
      'overlaps my other holdings'
    : 'how it would fit next to what I already hold, and the cash I have to invest'
  return (
    `Help me build my investment thesis for ${sym}. First look at my whole picture — ${picture}, ` +
    'my net worth, and my profile and goals — so the role, horizon and prices fit my portfolio, ' +
    `not just this asset. Then interview me one question at a time, covering ${fields}. Before ` +
    'each question, suggest a sensible answer from that picture, the latest news and the analyst ' +
    'view, and keep it short. Push back if my reasoning has gaps. When we have agreed every part, ' +
    `show me the full thesis and save it as my thesis for ${sym} once I confirm.`
  )
}

export function ThesisStart({
  sym,
  held,
  onClick,
  instrumentId,
}: {
  sym: string
  held: boolean
  onClick: () => void
  /** With it (and a chat to send to), a second button builds the thesis with Lucius. */
  instrumentId?: string
}) {
  const composer = useChatComposerOptional()
  const blurb = held
    ? `Why you own ${sym}, what would prove you wrong, and the prices that matter.`
    : `Why you’d buy ${sym}, what would prove the case wrong, and the prices that matter.`
  if (!composer || !instrumentId) {
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
  // The same pair as an empty alerts card: Build with Lucius, then the manual way.
  return (
    <div className="asd-thesis-start">
      <p className="asd-thesis-start-blurb">{blurb}</p>
      <div className="asd-alerts-start">
        <BuildWithLucius
          sym={sym}
          instrumentId={instrumentId}
          prompt={thesisBuildPrompt(sym, held)}
        />
        <Button
          variant="secondary"
          size="sm"
          iconLeft={<Plus size={14} aria-hidden="true" />}
          onClick={onClick}
        >
          Add thesis
        </Button>
      </div>
    </div>
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
