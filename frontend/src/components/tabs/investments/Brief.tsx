// Brief — portfolio brief card. Verbatim port of prototype Brief() component.
// Uses real signals from useInvestmentSignals (not mock data) and renders:
//   - Header band with Lucius spark icon + "Portfolio brief" + "N to decide" pill + "Intel"
//   - Body: lead signal tag + narrative + secondary bullets
//   - Movers bar: top up/down movers from the live book
//
// The prototype's briefScenario/preset system is NOT ported (dev-only tweaks panel).
// The "Intel" link calls onNavigate('intel') if provided.
//
// The card is deliberately insights-only. All portfolio metrics live in the masthead.

import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { useIsMobile } from '../../../hooks/useIsMobile'
import type { InvestmentSignal } from '../../../types/investmentSignals'
import type { DerivedBook } from './useInvestmentsBook'
import { Sparkles, ArrowRight, ChevronRight } from '../../../ui/icons'
import { Skeleton } from '../../ui/Skeleton'
import { usePortfolioDrivers } from '../../../hooks/usePortfolioDrivers'
import { DriverChips } from '../../shared/DriverChips'
import { INTEL_SUBTAB_KEY } from '../../../hooks/useIntelStoryIndex'

interface Props {
  d: DerivedBook
  signals: InvestmentSignal[]
  decisionsCount: number
  /** Brief signals are still being fetched — render the skeleton placeholder. */
  loading: boolean
  onNavigate?: (tab: string) => void
  onOpenAssetDetail?: (sym: string) => void
}
function toneVar(t: InvestmentSignal['tone']): string {
  switch (t) {
    case 'warn':
      return 'var(--warn)'
    case 'accent':
      return 'var(--accent)'
    case 'good':
      return 'var(--good)'
    case 'bad':
      return 'var(--bad)'
    default:
      return 'var(--muted)'
  }
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

const COMPACT_PANE_MAX = 760

/** The app viewport stays wide when Lucius opens, so media-query state alone cannot decide
 * whether this card has room for its desktop detail. Measure the same `.intel-tab` query
 * container the CSS responds to and use the phone information hierarchy when it is narrow. */
function useCompactBrief(isMobile: boolean) {
  const rootRef = useRef<HTMLElement>(null)
  const [narrowPane, setNarrowPane] = useState(false)

  useLayoutEffect(() => {
    const pane = rootRef.current?.closest('.intel-tab') as HTMLElement | null
    if (!pane) return
    const measure = () => setNarrowPane(pane.getBoundingClientRect().width <= COMPACT_PANE_MAX)
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(pane)
    return () => observer.disconnect()
  }, [])

  return { rootRef, compact: isMobile || narrowPane }
}

// Footprint-matched loading placeholder. Compact mode folds its bullets behind a toggle,
// whether caused by phone or a narrow Lucius pane.
function BriefSkeleton({
  compact,
  rootRef,
}: {
  compact: boolean
  rootRef: RefObject<HTMLElement | null>
}) {
  return (
    <section ref={rootRef} className="intel-brief-card" aria-busy="true">
      <div className="intel-brief-head">
        <span className="lx-mark">
          <Sparkles size={13} />
        </span>
        <span className="intel-brief-title">Portfolio brief</span>
      </div>
      <div className="intel-brief-body">
        <div className="brief-lead">
          <Skeleton width={104} height={16} radius={6} />
          <Skeleton height={15} radius={6} style={{ marginTop: 10 }} />
          <Skeleton width="78%" height={15} radius={6} style={{ marginTop: 7 }} />
          {compact ? (
            /* the "▸ N more" disclosure row, not the two expanded bullets */
            <Skeleton width={72} height={13} radius={6} style={{ marginTop: 14 }} />
          ) : (
            <>
              <Skeleton width={280} height={13} radius={6} style={{ marginTop: 14 }} />
              <Skeleton width={224} height={13} radius={6} style={{ marginTop: 9 }} />
            </>
          )}
        </div>
      </div>
      <div className="intel-brief-movers">
        <Skeleton width={104} height={11} radius={4} />
        <div className="mvr-list">
          {[68, 76, 82, 70, 78].map((w, i) => (
            <Skeleton key={i} width={w} height={26} radius={999} />
          ))}
        </div>
      </div>
    </section>
  )
}

export function Brief({
  d,
  signals,
  decisionsCount,
  loading,
  onNavigate,
  onOpenAssetDetail,
}: Props) {
  const mountedAt = useRef(new Date())
  const { data: driversData, loading: driversLoading } = usePortfolioDrivers()
  // The secondary bullets fold behind a disclosure on a phone or when Lucius narrows the
  // actual Investments pane.
  const isMobile = useIsMobile()
  const { rootRef, compact } = useCompactBrief(isMobile)
  const [moreOpen, setMoreOpen] = useState(false)

  if (d.empty) return null

  // Hold the full-footprint skeleton until the brief and today's drivers have settled,
  // so the card fades in fully formed instead of popping its movers in piecemeal.
  if (loading || driversLoading) {
    return <BriefSkeleton compact={compact} rootRef={rootRef} />
  }

  if (signals.length === 0) return null

  const leadSig = signals[0]!
  const moreSigs = signals.slice(1)
  const shownMore = moreSigs.slice(0, 2)

  const gainers = driversData?.top_gainers ?? []
  const losers = driversData?.top_losers ?? []
  const showDrivers = gainers.length + losers.length >= 2

  return (
    <section ref={rootRef} className="intel-brief-card">
      <div className="intel-brief-head">
        <span className="lx-mark">
          <Sparkles size={13} />
        </span>
        <span className="intel-brief-title">Portfolio brief</span>
        {decisionsCount > 0 && <span className="intel-brief-pill">{decisionsCount} to decide</span>}
        {onNavigate && (
          <a
            className="intel-brief-ask intel-brief-ask--intel"
            href="#intel"
            style={{ textDecoration: 'none' }}
            onClick={(e) => {
              e.preventDefault()
              // Land on Intel's Holdings tab — the news and risk behind THIS brief.
              try {
                sessionStorage.setItem(INTEL_SUBTAB_KEY, 'holdings')
              } catch {
                /* the Intel tab still opens, on its last-used tab */
              }
              onNavigate('intel')
            }}
          >
            Intel{' '}
            <span className="intel-brief-ask-spark">
              <ArrowRight size={12} aria-hidden="true" />
            </span>
          </a>
        )}
        <span className="intel-brief-updated">{formatTime(mountedAt.current)}</span>
      </div>

      <div className="intel-brief-body">
        <div className="brief-lead">
          <span
            className="brief-tag"
            style={{
              color: toneVar(leadSig.tone),
              background: `color-mix(in srgb, ${toneVar(leadSig.tone)} 14%, transparent)`,
            }}
          >
            {leadSig.tag}
          </span>
          <p className="intel-brief-narrative">{leadSig.narrative}</p>
          {shownMore.length > 0 && compact && (
            <button
              type="button"
              className="brief-more-toggle"
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen((o) => !o)}
            >
              <span className={`brief-more-caret${moreOpen ? ' is-open' : ''}`} aria-hidden>
                <ChevronRight size={12} />
              </span>
              {moreOpen ? 'Show less' : `${shownMore.length} more`}
            </button>
          )}
          {shownMore.length > 0 && (!compact || moreOpen) && (
            <ul className="brief-more">
              {shownMore.map((s) => (
                <li key={s.id}>
                  <span className="brief-dot" style={{ background: toneVar(s.tone) }} />
                  <span>{s.narrative}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {showDrivers && (
        <DriverChips
          gainers={gainers}
          losers={losers}
          onOpenAsset={(driver) => onOpenAssetDetail?.(driver.instrument_id)}
        />
      )}
    </section>
  )
}
