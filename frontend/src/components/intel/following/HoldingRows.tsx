import { fPct } from '../../../utils/format'
import type { FollowModel, PerfPeriod, PerfWindow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { sharesText, sharesTitle, signedPct, toneOf, tradeLine } from './following.format'

interface Props {
  model: FollowModel
  period: PerfPeriod
  win: PerfWindow | undefined
}

const pct = (n: number) => fPct(n, { fraction: true })

/** Every holding shown, largest first: its weight as a bar, and its own return over the window
 *  the chart above is showing. One quiet list; the moves have their own section. */
export function HoldingRows({ model, period, win }: Props) {
  const top = Math.max(...model.holdings.map((h) => h.weight), 0.0001)
  const returns = win?.status === 'ok' ? win.holdings : {}
  return (
    <section className="fol-sec" aria-label={COPY.holdingsTitle}>
      <div className="fol-sec-head">
        <h3 className="fol-sec-title">{COPY.holdingsTitle}</h3>
        <span className="fol-sec-sub">{model.holdings.length} shown</span>
      </div>
      <div className="fol-hold fol-head" aria-hidden="true">
        <span />
        <span>Stock</span>
        <span>Weight</span>
        <span />
        <span>{COPY.perfColumn[period]}</span>
      </div>
      <ol className="fol-holds">
        {model.holdings.map((h, i) => (
          <li key={h.symbol} className="fol-hold">
            <span className="fol-rank">{i + 1}</span>
            <div className="fol-id">
              <div className="fol-sym">{h.symbol}</div>
              <div className="fol-sub" title={sharesTitle(h, model)}>
                {h.last_trade ? tradeLine(h.last_trade) : `${h.name}${sharesText(h)}`}
              </div>
            </div>
            <span className="fol-num">{pct(h.weight)}</span>
            <span className="fol-track" aria-hidden="true">
              <span
                className="fol-fill"
                style={{ width: `${Math.max(2, (h.weight / top) * 100)}%` }}
              />
            </span>
            <span className={`fol-num ${toneOf(returns[h.symbol])}`}>
              {signedPct(returns[h.symbol])}
            </span>
          </li>
        ))}
      </ol>
      {model.unmatched.length > 0 && (
        <p className="fol-foot">
          {COPY.unmatched(model.unmatched.length)}{' '}
          {model.unmatched.map((x) => `${x.name} (${pct(x.weight)})`).join(', ')}
        </p>
      )}
    </section>
  )
}
