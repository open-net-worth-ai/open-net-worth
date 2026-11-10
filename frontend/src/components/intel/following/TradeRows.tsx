import { useState } from 'react'
import { Button } from '../../ui'
import type { FollowActivity } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { amountRange, shortDate } from './following.format'

const SIDE = {
  buy: { cls: 'up', label: 'Buy' },
  sell: { cls: 'down', label: 'Sell' },
  exchange: { cls: 'flat', label: 'Exchange' },
} as const
const FIRST = 10

function Row({ a }: { a: FollowActivity }) {
  return (
    <article className={`fol-card${a.counted ? '' : ' fol-dim'}`}>
      <div className="fol-grid fol-grid--trade">
        <div className="fol-id fol-wide">
          <div className="fol-num fol-num--soft fol-left">{shortDate(a.date)}</div>
          <div className="fol-sub">filed {shortDate(a.filed_at)}</div>
        </div>
        <div className="fol-id">
          <div className="fol-sym">{a.symbol ?? a.name}</div>
          <div className="fol-sub">{a.counted ? a.name : COPY.notCounted}</div>
        </div>
        <span className={`fol-pill ${SIDE[a.side].cls}`}>{SIDE[a.side].label}</span>
        <span className="fol-num fol-wide">{amountRange(a)}</span>
      </div>
    </article>
  )
}

export function TradeRows({ activity, unread }: { activity: FollowActivity[]; unread: number }) {
  const [all, setAll] = useState(false)
  const rows = [...activity].sort((a, b) => b.date.localeCompare(a.date))
  const shown = all ? rows : rows.slice(0, FIRST)
  return (
    <section className="fol-sec" aria-label={COPY.tradesTitle}>
      <div className="fol-sec-head">
        <h3 className="fol-sec-title">{COPY.tradesTitle}</h3>
      </div>
      {rows.length === 0 ? (
        <p className="fol-foot">{COPY.noTrades}</p>
      ) : (
        <>
          <div className="fol-grid fol-grid--trade fol-head" aria-hidden="true">
            <span className="fol-wide">Traded</span>
            <span className="fol-left">Stock</span>
            <span>Side</span>
            <span className="fol-wide">Amount</span>
          </div>
          {shown.map((a, i) => (
            <Row key={`${a.date}-${a.symbol ?? a.name}-${i}`} a={a} />
          ))}
          {rows.length > FIRST && !all && (
            <div>
              <Button variant="ghost" size="sm" onClick={() => setAll(true)}>
                {COPY.showAllTrades(rows.length)}
              </Button>
            </div>
          )}
        </>
      )}
      {unread > 0 && <p className="fol-foot">{COPY.unreadReports(unread)}</p>}
    </section>
  )
}
