import { useEffect, useRef } from 'react'
import { Skeleton } from '../../ui'
import { fDate } from '../../../utils/format'
import type { DirectoryEntry } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { moveDetail, signedPct, toneOf } from './following.format'
import { Donut } from './Donut'
import { LOGOS, PHOTOS } from './portfolioPhotos'
import { usePreviewSummary } from './useFollowing'
import { useSlow } from './useSlow'

const CARD_RETRIES = 3
const CARD_RETRY_MS = 4000

interface Props {
  entry: DirectoryEntry
  onOpen: () => void
  /** Told when this card's wait turns long and when it ends, so the gallery can say why once. */
  onSlow?: (slow: boolean) => void
}

/** One portfolio in Discover: its year against the benchmark, its two biggest moves, and a
 *  ring of what it holds. The whole card opens the portfolio. Nothing else is on it. */
export function PortfolioCard({ entry, onOpen, onSlow }: Props) {
  const card = usePreviewSummary(entry.kind, entry.source_ref)
  const s = card.data
  const cardKey = `${entry.kind}:${entry.source_ref}`
  // Only a card the server must fetch counts as slow: a kept one waiting its turn behind
  // other requests is just loading, and says nothing (owner, 2026-11-13). A card showing its
  // last figures while the server brings it up to date counts too: the banner says the gallery
  // is being refreshed, and the card keeps its figures meanwhile (owner, 2026-11-14).
  const slow = useSlow((!s && !card.error && entry.ready !== true) || s?.stale === true)
  useEffect(() => {
    onSlow?.(slow)
    // A card that leaves (a filter, a new search) stops counting as loading.
    return () => onSlow?.(false)
  }, [slow, onSlow])
  // A card that failed (the server restarting, a source briefly down) asks again a few times
  // rather than staying on "No figures yet" until the page is reloaded (owner, 2026-11-13).
  const tries = useRef(0)
  const failed = !s && card.error != null
  const { refresh } = card
  useEffect(() => {
    if (!failed || tries.current >= CARD_RETRIES) return
    const timer = setTimeout(() => {
      tries.current += 1
      void refresh()
    }, CARD_RETRY_MS)
    return () => clearTimeout(timer)
  }, [failed, refresh, card.error])
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
            {/* "Updated" and the day of the newest move shown when it is a dated trade (its trade
                day, not the day it was filed), else the day the report was disclosed; the year
                only when it is not this one (Moomoo's card, owner 2026-11-12). */}
            {s.filed_at && (
              <div className="fol-card-updated">
                {COPY.cardUpdated(
                  fDate(
                    s.kind !== 'house_member' && typeof s.moves[0]?.detail === 'string'
                      ? s.moves[0].detail
                      : s.filed_at,
                    'short',
                    'auto',
                  ),
                )}
              </div>
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
          <Donut
            weights={s.weights}
            count={s.holdings_count}
            size={160}
            photo={PHOTOS[cardKey]}
            logo={LOGOS[cardKey]}
          />
        ) : PHOTOS[cardKey] || LOGOS[cardKey] ? (
          // The picture is already in the browser: show it now; only the slices wait.
          <Donut
            weights={[]}
            count={0}
            size={160}
            photo={PHOTOS[cardKey]}
            logo={LOGOS[cardKey]}
            loading
          />
        ) : (
          <Skeleton circle width={160} height={160} />
        )}
      </div>
      {/* Which cards the gallery's banner is about: a thin bar along their bottom edge. */}
      {slow && <span className="asd-refreshing-bar" aria-hidden="true" />}
    </button>
  )
}
