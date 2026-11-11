import { Skeleton } from '../../ui'
import type { DirectoryEntry } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { moveDetail, shortDate, signedPct, toneOf } from './following.format'
import { Donut } from './Donut'
import { usePreviewSummary } from './useFollowing'

interface Props {
  entry: DirectoryEntry
  onOpen: () => void
}

/** One portfolio in Discover: its year against the benchmark, its two biggest moves, and a
 *  ring of what it holds. The whole card opens the portfolio. Nothing else is on it. */
export function PortfolioCard({ entry, onOpen }: Props) {
  const card = usePreviewSummary(entry.kind, entry.source_ref)
  const s = card.data
  const gap =
    s && s.return_12m != null && s.benchmark_12m != null ? s.return_12m - s.benchmark_12m : null
  return (
    <button
      type="button"
      className="fol-card"
      onClick={onOpen}
      aria-label={COPY.openLabel(entry.name)}
    >
      <div className="fol-card-main">
        <div className="fol-card-name">{entry.name}</div>
        <div className="fol-card-who">{entry.subtitle}</div>
        {s ? (
          <>
            <div className="fol-card-ret">
              <span className={toneOf(s.return_12m)}>{signedPct(s.return_12m)}</span>
              <span className="fol-card-period">{COPY.oneYear}</span>
            </div>
            {gap != null && (
              <div className="fol-card-vs">
                <span>
                  {s.benchmark} {signedPct(s.benchmark_12m)}
                </span>
                <span className={toneOf(gap)}>{signedPct(gap, 'pts')}</span>
              </div>
            )}
            <div className="fol-card-moves">
              {s.moves.map((m) => (
                <div key={`${m.symbol}-${m.verb}`} className="fol-card-move">
                  <b>{m.symbol}</b>
                  <span className={m.up ? 'up' : 'down'}>{m.verb}</span>
                  <span className="fol-card-detail">{moveDetail(m.detail)}</span>
                </div>
              ))}
            </div>
            {s.filed_at && (
              <div className="fol-card-updated">{COPY.updated(s.kind, shortDate(s.filed_at))}</div>
            )}
          </>
        ) : card.error ? (
          <div className="fol-card-updated">{COPY.cardUnavailable}</div>
        ) : (
          <div className="fol-card-skel">
            <Skeleton height={28} width="55%" />
            <Skeleton height={14} width="70%" />
            <Skeleton height={14} width="60%" />
          </div>
        )}
      </div>
      <div className="fol-card-ring">
        {s ? (
          <Donut weights={s.weights} count={s.holdings_count} />
        ) : (
          <Skeleton circle width={132} height={132} />
        )}
      </div>
    </button>
  )
}
