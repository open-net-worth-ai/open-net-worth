import { Tag } from '../../ui'
import { Donut } from '../following/Donut'
import { signedPct, toneOf } from '../following/following.format'
import { fCurrency } from '../../../utils/format'
import {
  dayOf,
  openOrders,
  portfolioValue,
  rebasedEnd,
  returnSinceStart,
  weights,
  type Portfolio,
} from './paperStore'

/** One paper portfolio, built as a Copy Trading card: name, source, its return against VT,
 *  its two newest trades, and a ring of what it holds. The whole card opens it. */
export function PaperCard({ p, onOpen }: { p: Portfolio; onOpen: () => void }) {
  const ret = returnSinceStart(p)
  const bench = rebasedEnd(p.bench, p)
  const trades = p.activity.filter((a) => /^(Bought|Sold)/.test(a.text)).slice(0, 2)
  const open = openOrders(p).length
  return (
    <button type="button" className="fol-card" onClick={onOpen} aria-label={`Open ${p.name}`}>
      <div className="fol-card-main">
        <div className="fol-card-name paper-card-name">
          {p.name} <Tag tone="paper">Paper</Tag>
        </div>
        <div className="fol-card-who">{p.source}</div>
        <div className="fol-card-ret">
          <span className={toneOf(ret)}>{signedPct(ret)}</span>
          <span className="fol-card-period">Day {dayOf(p)}</span>
        </div>
        <div className="fol-card-vs">
          <span>VT {signedPct(bench)}</span>
          <span className={toneOf(ret - bench)}>{signedPct(ret - bench, 'pts')}</span>
        </div>
        <div className="fol-card-moves">
          {trades.map((t) => {
            const [verb, qty, sym] = t.text.split(' ')
            return (
              <div key={t.id} className="fol-card-move">
                <b>{sym}</b>
                <span className={verb === 'Bought' ? 'up' : 'down'}>{verb}</span>
                <span className="fol-card-detail">{qty}</span>
              </div>
            )
          })}
        </div>
        <div className="fol-card-updated">
          {fCurrency(portfolioValue(p), 'USD', { maximumFractionDigits: 0 })}
          {open > 0 && ` · ${open} open order${open > 1 ? 's' : ''}`}
        </div>
      </div>
      <div className="fol-card-ring">
        <Donut weights={weights(p)} count={p.holdings.length} size={160} />
      </div>
    </button>
  )
}
