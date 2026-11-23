import { useEffect, useRef, useState } from 'react'
import { useMovementTimeline } from '../../hooks/useMovementTimeline'
import type { Citation, MovementRow, MovementTimeline } from '../../types/assetDetail'
import { fDate, fPct, fUnitPrice, safeHref } from '../../utils/format'
import { Skeleton } from '../ui/Skeleton'
import { Favicon } from './Favicon'
import { clockIn, generatedLabel, notableTitle } from './movementFormat'

// "Notable Price Movement" — Perplexity Finance's per-session timeline, under the chart: the
// venue's state on top (a live row while it trades, a "Market closed" notice while it's shut),
// then each completed session's close, % change, after-hours print and a cited paragraph
// explaining WHY. Every number is the backend's (services.price_movements); the paragraph is
// the one model-authored field and always carries the sources it cited.

// Pages of ten, auto-loaded as the reader nears the bottom of the scroll box (Perplexity's
// pattern: no button), up to the server's ceiling of thirty sessions.
const PAGE_SIZE = 10
const MAX_LIMIT = 30

interface Props {
  instrumentId: string
  /** Hides the section outright for a holding with no market ticker (nothing to time). */
  hasTicker: boolean
  /** Price precision cap — the header's own rule (2 for an equity, undefined for crypto), so
   *  a vendor's raw 3-4 decimal extended-hours print never shows as "$148.915". */
  maxFrac?: number
}

function marketLabel(m: MovementTimeline['market']): string {
  if (!m) return ''
  if (m.always_open) return '24/7'
  switch (m.state) {
    case 'REGULAR':
      return 'Open'
    case 'PRE':
      return 'Pre-Market'
    case 'POST':
      return 'After Hours'
    case 'PREPRE':
    case 'POSTPOST':
    case 'CLOSED':
      return 'Market Closed'
    default:
      return ''
  }
}

function Change({ pct }: { pct: number | null }) {
  if (pct == null) return null
  const up = pct > 0
  const flat = pct === 0
  const cls = flat ? 'asd-pm-flat' : up ? 'asd-pm-up' : 'asd-pm-down'
  return (
    <span className={cls}>
      <span aria-hidden="true">{flat ? '→' : up ? '↗' : '↘'}</span> {fPct(Math.abs(pct))}
    </span>
  )
}

function noticeText(row: MovementRow): string {
  if (row.phase === 'pre') return 'Pre-market. No trades yet — check back at the open.'
  if (row.phase === 'post') return 'Regular session over. After-hours prints arrive here.'
  return 'Market closed. Check back when trading resumes.'
}

function PriceLine({
  row,
  currency,
  maxFrac,
}: {
  row: MovementRow
  currency: string
  maxFrac?: number
}) {
  const notableCls = row.notable ? 'asd-pm-price asd-pm-price--notable' : 'asd-pm-price'
  const title = row.notable ? notableTitle(row.change_pct, row.usual_move_pct) : undefined
  if (row.kind === 'live') {
    return (
      <div className={notableCls} title={title}>
        <span className="asd-pm-px">{fUnitPrice(row.price, currency, maxFrac)}</span>{' '}
        <Change pct={row.change_pct} />
        {row.phase === 'pre' && <span className="asd-pm-tag">Pre-Market</span>}
      </div>
    )
  }
  return (
    <div className={notableCls} title={title}>
      <span className="asd-pm-px">{fUnitPrice(row.price, currency, maxFrac)}</span>{' '}
      <Change pct={row.change_pct} />
      <span className="asd-pm-tag">at Close</span>
      {row.after_hours && (
        <>
          <span className="asd-pm-sep" aria-hidden="true">
            •
          </span>
          {/* One unbreakable group, so a narrow screen wraps the whole after-hours print to
              its own line instead of splitting price from its change mid-phrase. */}
          <span className="asd-pm-ah">
            <span className="asd-pm-px">
              {fUnitPrice(row.after_hours.price, currency, maxFrac)}
            </span>{' '}
            <Change pct={row.after_hours.change_pct} />
            <span className="asd-pm-tag">After Hours</span>
          </span>
        </>
      )}
    </div>
  )
}

function Sources({
  sources,
  generated,
  rewriting,
}: {
  sources: Citation[]
  generated: string
  rewriting: boolean
}) {
  const [open, setOpen] = useState(false)
  if (!sources.length && !generated && !rewriting) return null
  const label = sources.length === 1 ? '1 source' : `${sources.length} sources`
  return (
    <div className="asd-pm-sources">
      {/* One line: the pill, then the paragraph's own timestamp after a dot (owner ask
          2026-09-21: "next to that, separated by a dot" — not on its own line below). */}
      <div className="asd-pm-sources-row">
        {sources.length > 0 && (
          <button
            type="button"
            className="asd-pm-sources-btn"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <span className="asd-pm-favicons" aria-hidden="true">
              {sources.slice(0, 3).map((s, i) => (
                <Favicon key={i} url={s.url} domain={s.domain} className="asd-pm-favicon" />
              ))}
            </span>
            {label}
          </button>
        )}
        {generated && (
          <span className="asd-pm-generated">
            {sources.length > 0 && (
              <span className="asd-pm-sep" aria-hidden="true">
                •
              </span>
            )}
            {generated}
          </span>
        )}
        {/* The OLD paragraph stays on screen (it is the last known) while a new one is being
            written; this is what says so (owner 2026-09-21: "no way to tell"). */}
        {rewriting && (
          <span className="asd-pm-updating" role="status">
            {(sources.length > 0 || generated) && (
              <span className="asd-pm-sep" aria-hidden="true">
                •
              </span>
            )}
            <span className="ui-btn__spinner" aria-hidden="true" />
            Updating…
          </span>
        )}
      </div>
      {open && (
        <ul className="asd-pm-source-list">
          {sources.map((s, i) => {
            const href = safeHref(s.url)
            const inner = (
              <>
                <Favicon url={s.url} domain={s.domain} className="asd-pm-favicon" />
                <span className="asd-pm-source-copy">
                  <span className="asd-pm-source-title">{s.title || s.domain || s.url}</span>
                  {s.domain && <span className="asd-pm-source-domain">{s.domain}</span>}
                </span>
              </>
            )
            return (
              <li key={i}>
                {href ? (
                  <a
                    className="asd-pm-source"
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {inner}
                  </a>
                ) : (
                  <span className="asd-pm-source">{inner}</span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function Body({ row, generated }: { row: MovementRow; generated: string }) {
  if (row.narration === 'ready' && row.summary) {
    return (
      <>
        <p className="asd-pm-text">{row.summary}</p>
        <Sources sources={row.sources} generated={generated} rewriting={row.rewriting} />
      </>
    )
  }
  if (row.narration === 'pending') {
    return (
      <div className="asd-pm-skeleton" aria-label="Explanation generating">
        <Skeleton height={12} width="92%" />
        <Skeleton height={12} width="78%" />
      </div>
    )
  }
  if (row.narration === 'failed') {
    return <p className="asd-pm-empty">Couldn't generate an explanation yet — will retry.</p>
  }
  // 'none': looked, and the day was quiet in our news cache.
  return <p className="asd-pm-empty">No notable coverage found for this session.</p>
}

function Row({
  row,
  data,
  maxFrac,
}: {
  row: MovementRow
  data: MovementTimeline
  maxFrac?: number
}) {
  const tz = data.market?.timezone ?? null
  const tzShort = data.market?.tz_short ?? null
  const showTime = row.kind !== 'session'
  const dotCls =
    row.kind === 'live'
      ? 'asd-pm-dot asd-pm-dot--live'
      : row.kind === 'market_closed'
        ? 'asd-pm-dot asd-pm-dot--notice'
        : 'asd-pm-dot'
  return (
    <li className="asd-pm-row" data-kind={row.kind}>
      <div className="asd-pm-when">
        <span className="asd-pm-date">{fDate(row.session_date)}</span>
        {showTime && <span className="asd-pm-time">{clockIn(row.as_of, tz, null)}</span>}
        {showTime && tzShort && <span className="asd-pm-tz">{tzShort}</span>}
      </div>
      <div className="asd-pm-rail" aria-hidden="true">
        <span className={dotCls} />
      </div>
      <div className="asd-pm-body">
        {row.kind === 'market_closed' ? (
          <p className="asd-pm-notice">{noticeText(row)}</p>
        ) : (
          <>
            <PriceLine row={row} currency={data.currency} maxFrac={maxFrac} />
            <Body
              row={row}
              generated={generatedLabel(row.narrated_at, row.session_date, tz, tzShort)}
            />
          </>
        )}
      </div>
    </li>
  )
}

export function PriceMovementTimeline({ instrumentId, hasTicker, maxFrac }: Props) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, loading, loadingMore } = useMovementTimeline(instrumentId, limit)
  const cardRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLLIElement>(null)
  // The top fade exists only once the reader has scrolled: at rest it would sit over the first
  // row's date line (owner ask 2026-09-21: a fade at the top too, but the card's top padding
  // stays as it was — no extra air to clear a fade nothing needs yet).
  const [scrolled, setScrolled] = useState(false)
  const sessions = data ? data.rows.filter((r) => r.kind === 'session').length : 0
  // More to load only while the server keeps returning FULL pages (fewer sessions than asked
  // means the history ran out) and the ceiling isn't reached.
  const canLoadMore = !!data && !loadingMore && sessions >= limit && limit < MAX_LIMIT
  useEffect(() => {
    if (!canLoadMore) return
    const root = cardRef.current
    const target = sentinelRef.current
    if (!root || !target || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setLimit((l) => Math.min(l + PAGE_SIZE, MAX_LIMIT))
        }
      },
      { root, rootMargin: '0px 0px 160px 0px' },
    )
    io.observe(target)
    return () => io.disconnect()
  }, [canLoadMore])
  if (!hasTicker) return null
  if (loading && !data) {
    return (
      <div className="asd-pm">
        <div className="asd-section-head">
          <span className="asd-section-title">Notable Price Movement</span>
        </div>
        <div className="asd-pm-card asd-pm-skeleton">
          <Skeleton height={14} width="60%" />
          <Skeleton height={12} width="95%" />
          <Skeleton height={12} width="88%" />
        </div>
      </div>
    )
  }
  if (!data || data.rows.length === 0) return null
  const state = marketLabel(data.market)
  return (
    <div className="asd-pm">
      <div className="asd-section-head">
        <span className="asd-section-title">Notable Price Movement</span>
        {state && <span className="asd-pm-market">{state}</span>}
      </div>
      <div
        ref={cardRef}
        className={scrolled ? 'asd-pm-card asd-pm-card--scrolled' : 'asd-pm-card'}
        role="region"
        aria-label="Notable price movement timeline"
        tabIndex={0}
        onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 0)}
      >
        <ol className="asd-pm-list">
          {data.rows.map((row) => (
            <Row key={`${row.kind}:${row.session_date}`} row={row} data={data} maxFrac={maxFrac} />
          ))}
          {/* The next page appends below the rows already on screen — the card never blanks
              to a skeleton and never changes height while it loads. */}
          {loadingMore && (
            <li className="asd-pm-row asd-pm-row--loading" aria-label="Loading more sessions">
              <div className="asd-pm-when">
                <Skeleton height={12} width={44} />
              </div>
              <div className="asd-pm-rail" aria-hidden="true">
                <span className="asd-pm-dot" />
              </div>
              <div className="asd-pm-body asd-pm-skeleton">
                <Skeleton height={14} width="40%" />
                <Skeleton height={12} width="92%" />
                <Skeleton height={12} width="78%" />
              </div>
            </li>
          )}
          <li ref={sentinelRef} className="asd-pm-sentinel" aria-hidden="true" />
        </ol>
      </div>
    </div>
  )
}
