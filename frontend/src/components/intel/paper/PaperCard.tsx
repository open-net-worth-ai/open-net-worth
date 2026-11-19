import { Tag } from '../../ui'
import { AutopilotTag } from './AutopilotTag'
import { Donut } from '../following/Donut'
import { signedPct, toneOf } from '../following/following.format'
import { fCurrency } from '../../../utils/format'
import type { PaperCard as Card } from '../../../api/paperTrading'

/** One paper portfolio, built as a Copy Trading card: name, source, its return against the
 *  benchmark, its two newest trades, and a ring of what it holds. The whole card opens it. */
export function PaperCard({ p, onOpen }: { p: Card; onOpen: () => void }) {
  const gap =
    p.return_pct != null && p.benchmark_return_pct != null
      ? p.return_pct - p.benchmark_return_pct
      : null
  return (
    <button type="button" className="fol-card" onClick={onOpen} aria-label={`Open ${p.name}`}>
      <div className="fol-card-main">
        <div className="fol-card-name paper-card-name">
          {p.name} <Tag>Paper</Tag>
          {p.autopilot_mode === 'auto' && <AutopilotTag />}
        </div>
        <div className="fol-card-who">
          {/* A cash start names nothing; the line keeps its height so cards stay aligned. */}
          {[p.source_label, p.run_no > 1 ? `Run ${p.run_no}` : ''].filter(Boolean).join(' · ') ||
            ' '}
        </div>
        <div className="fol-card-ret">
          <span className={toneOf(p.return_pct)}>{signedPct(p.return_pct)}</span>
          <span className="fol-card-period">Day {p.day_count}</span>
        </div>
        {gap != null && (
          <div className="fol-card-vs">
            <span>
              {p.benchmark} {signedPct(p.benchmark_return_pct)}
            </span>
            <span className={toneOf(gap)}>{signedPct(gap, 'pts')}</span>
          </div>
        )}
        <div className="fol-card-moves">
          {p.recent_trades.map((t) => (
            <div key={`${t.at}-${t.label}`} className="fol-card-move">
              <b>{t.symbol ?? ''}</b>
              <span className={t.side === 'buy' ? 'up' : 'down'}>
                {t.side === 'buy' ? 'Bought' : 'Sold'}
              </span>
              <span className="fol-card-detail">
                {t.label.split(' at ')[1] ? `at ${t.label.split(' at ')[1]}` : ''}
              </span>
            </div>
          ))}
        </div>
        <div className="fol-card-updated">
          {p.value != null
            ? fCurrency(p.value, p.home_currency, { maximumFractionDigits: 0 })
            : '—'}
          {p.open_orders > 0 && ` · ${p.open_orders} open order${p.open_orders > 1 ? 's' : ''}`}
        </div>
      </div>
      <div className="fol-card-ring">
        <Donut weights={p.weights} count={p.holdings_count} size={160} />
      </div>
    </button>
  )
}
