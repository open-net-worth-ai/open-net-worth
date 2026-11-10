import { useState } from 'react'
import { Button } from '../../ui'
import type { FollowActivity } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { CopyTradeButton } from './CopyTradeButton'
import { amountRange, shortDate, signedPct, toneOf } from './following.format'

const FIRST = 8

interface Props {
  activity: FollowActivity[]
  unread: number
  /** "<date>|<symbol>" to the price return since that trade. */
  returns: Record<string, number>
  who: string
  onAskLucius?: (text: string) => void
}

interface RowProps {
  a: FollowActivity
  since: number | undefined
  who: string
  onAskLucius?: (text: string) => void
}

function Row({ a, since, who, onAskLucius }: RowProps) {
  const verb = a.side === 'buy' ? 'Bought' : a.side === 'sell' ? 'Sold' : 'Exchanged'
  return (
    <li className={`fol-trade${a.counted ? '' : ' fol-dim'}`}>
      <span
        className={`fol-dot ${a.side === 'buy' ? 'up' : a.side === 'sell' ? 'down' : 'flat'}`}
      />
      <div className="fol-id">
        <div className="fol-sym">
          {verb} {a.symbol ?? a.name}
        </div>
        <div className="fol-sub">{a.counted ? a.name : COPY.notCounted}</div>
      </div>
      <div className="fol-move-what">
        <div className="fol-num">{amountRange(a)}</div>
        <div className="fol-sub">
          {shortDate(a.date)} · filed {shortDate(a.filed_at)}
        </div>
      </div>
      <span className={`fol-num ${toneOf(since)}`} title={COPY.sinceTradeTitle}>
        {signedPct(since)}
      </span>
      <span className="fol-act">
        {a.counted && a.symbol && (
          <CopyTradeButton
            who={who}
            what={`${verb.toLowerCase()} ${a.symbol} (${a.name}) on ${shortDate(a.date)}, reported as ${amountRange(a)}`}
            onAskLucius={onAskLucius}
          />
        )}
      </span>
    </li>
  )
}

/** A member's disclosed trades, newest first, each with how the stock has done since. */
export function TradeRows({ activity, unread, returns, who, onAskLucius }: Props) {
  const [all, setAll] = useState(false)
  const rows = [...activity].sort((a, b) => b.date.localeCompare(a.date))
  const shown = all ? rows : rows.slice(0, FIRST)
  return (
    <section className="fol-sec" aria-label={COPY.tradesTitle}>
      <div className="fol-sec-head">
        <h3 className="fol-sec-title">{COPY.tradesTitle}</h3>
        <span className="fol-sec-sub">{COPY.sinceTradeHead}</span>
      </div>
      {rows.length === 0 ? (
        <p className="fol-foot">{COPY.noTrades}</p>
      ) : (
        <ul className="fol-trades">
          {shown.map((a, i) => (
            <Row
              key={`${a.date}-${a.symbol ?? a.name}-${i}`}
              a={a}
              since={a.counted && a.symbol ? returns[`${a.date}|${a.symbol}`] : undefined}
              who={who}
              onAskLucius={onAskLucius}
            />
          ))}
        </ul>
      )}
      {rows.length > FIRST && !all && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setAll(true)}>
            {COPY.showAllTrades(rows.length)}
          </Button>
        </div>
      )}
      {unread > 0 && <p className="fol-foot">{COPY.unreadReports(unread)}</p>}
    </section>
  )
}
