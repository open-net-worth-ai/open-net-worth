import { useState } from 'react'
import { Button } from '../../ui'
import type { FollowModel } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { CopyTradeButton } from './CopyTradeButton'
import { moveRows, shortDate } from './following.format'

const FIRST = 8

/** What the fund did between its last two filings, biggest first, said in words: the same row
 *  shape as a member's trades (dot, what happened, how much). */
interface Props {
  model: FollowModel
  who: string
  onAskLucius?: (text: string) => void
}

export function MovesChart({ model, who, onAskLucius }: Props) {
  const [all, setAll] = useState(false)
  const rows = moveRows(model)
  if (rows.length === 0)
    return (
      <section className="fol-sec" aria-label={COPY.movesTitle}>
        <div className="fol-sec-head">
          <h3 className="fol-sec-title">{COPY.movesTitle}</h3>
        </div>
        <p className="fol-foot">{COPY.noMoves}</p>
      </section>
    )
  const shown = all ? rows : rows.slice(0, FIRST)
  const period = model.prev_as_of
    ? `${shortDate(model.prev_as_of)} to ${shortDate(model.as_of)}`
    : ''
  return (
    <section className="fol-sec" aria-label={COPY.movesTitle}>
      <div className="fol-sec-head">
        <h3 className="fol-sec-title">{COPY.movesTitle}</h3>
        <span className="fol-sec-sub">{period}</span>
      </div>
      <ul className="fol-moves">
        {shown.map((r) => {
          return (
            <li key={r.symbol} className="fol-move" title={r.title}>
              <span className={`fol-dot ${r.value > 0 ? 'up' : 'down'}`} />
              <div className="fol-id">
                <div className="fol-sym">
                  {r.verb} {r.symbol}
                </div>
                <div className="fol-sub">{r.name}</div>
              </div>
              <div className="fol-move-what">
                <div className="fol-num">{r.label}</div>
                <div className="fol-sub">{r.detail}</div>
              </div>
              <span className="fol-act">
                <CopyTradeButton
                  who={`${who}'s latest 13F`}
                  what={`${r.value > 0 ? 'bought' : 'sold'} ${r.symbol} (${r.name}), ${r.detail}, between ${period}`}
                  onAskLucius={onAskLucius}
                />
              </span>
            </li>
          )
        })}
      </ul>
      {rows.length > FIRST && !all && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setAll(true)}>
            {COPY.showAllMoves(rows.length)}
          </Button>
        </div>
      )}
      <p className="fol-foot">{COPY.changeBasis}</p>
    </section>
  )
}
