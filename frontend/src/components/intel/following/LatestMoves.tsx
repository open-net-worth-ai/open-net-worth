import { useMemo } from 'react'
import type { Follow } from '../../../api/followedPortfolios'
import { InfoPopover } from '../../ui'
import { COPY } from './following.copy'
import { shortDate } from './following.format'
import type { Names } from './activityRows'
import { latestGroups } from './latestGroups'

interface Props {
  follow: Follow
  names: Names
  held: Set<string>
  /** Shows the move in the table below: its period, with it picked. */
  onOpen: (periodKey: string, rowKey: string) => void
}

/** What they just did, at a glance: the first thing a reader of a famous portfolio wants. */
export function LatestMoves({ follow, names, held, onOpen }: Props) {
  const groups = useMemo(() => latestGroups(follow, names), [follow, names])
  if (!groups.length) return null
  return (
    <section className="fol-latest" aria-label={COPY.latestTitle}>
      <h2 className="fol-sec-title">
        {COPY.latestTitle}
        {/* What the two kinds of move are, in the reader's terms: when you learn of it, and
            what it tells you. */}
        <InfoPopover
          width={300}
          label={COPY.latestInfoLabel}
          text={
            <>
              <span className="fol-info-line">{COPY.latestInfoTrades}</span>
              <span className="fol-info-line">{COPY.latestInfoReport}</span>
            </>
          }
        />
      </h2>
      {groups.map((g) => (
        <div key={g.key} className="fol-latest-group">
          <h3 className="fol-latest-title">{g.title}</h3>
          <ul className="fol-latest-list">
            {g.rows.map((r) => {
              const own = held.has(r.symbol)
              return (
                <li key={r.key}>
                  <button
                    type="button"
                    className={`fol-move-chip ${r.up ? 'up' : 'down'}${own ? ' is-own' : ''}`}
                    aria-label={`${r.verb} ${r.symbol}, ${r.amountText}${r.dateText ? `, ${r.dateText}` : ''}${own ? `, ${COPY.youOwn.toLowerCase()}` : ''}`}
                    title={own ? `${r.name}. ${COPY.youOwn}` : r.name}
                    onClick={() => onOpen(g.periodKey, r.key)}
                  >
                    <b>{r.symbol}</b>
                    <span>{r.amountText}</span>
                    {g.key === 'since' && r.tradedAt && (
                      <span className="fol-move-when">
                        {shortDate(r.tradedAt).replace(/, \d{4}$/, '')}
                      </span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </section>
  )
}
