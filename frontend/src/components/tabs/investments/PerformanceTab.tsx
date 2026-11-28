import { useState } from 'react'
import { Button, Cluster, EmptyState, ErrorState, TabSkeleton } from '../../ui'
import { MaskedText } from '../../ui/MaskedText'
import { ChartLine } from '../../../ui/icons'
import { LuciusCta } from '../../intro/LuciusCta'
import { usePerformanceView } from './usePerformanceView'
import type { PerfStatCard } from './usePerformanceView'
import { PERFORMANCE_PERIODS } from './performancePeriod'
import { BenchmarkPicker } from './BenchmarkPicker'
import { PerfChart } from './PerfChart'
import './design.css'

// ── StatInfo ──────────────────────────────────────────────────────────────────
// Inline hover-triggered info tooltip ("italic i" button + popover).
// Uses .mk-stat-info/.mk-stat-pop from design.css (CSS shows it on hover/focus).

/** .mk-stat-pop's width, and the gap it keeps from the screen's edges. */
const STAT_POP_W = 232
const STAT_POP_EDGE = 8

interface StatInfoProps {
  text: string
  alignRight?: boolean
}

function StatInfo({ text, alignRight }: StatInfoProps) {
  // Placed as it opens, inside the screen: a card's place in the desktop row says nothing once the
  // cards wrap (on a phone a right-hand card's tooltip ran off the screen, and on a narrow screen
  // neither side fits an icon mid-row). It opens where the CSS puts it, then shifts just enough.
  const [left, setLeft] = useState<number | null>(null)
  const place = (e: React.SyntheticEvent<HTMLElement>) => {
    const icon = e.currentTarget.getBoundingClientRect()
    const natural = alignRight ? icon.right + 5 - STAT_POP_W : icon.left - 5
    const max = window.innerWidth - STAT_POP_EDGE - STAT_POP_W
    const fitted = Math.max(STAT_POP_EDGE, Math.min(natural, max))
    setLeft(Math.round(fitted - icon.left))
  }
  return (
    <span
      className="mk-stat-info"
      tabIndex={0}
      role="button"
      aria-label="What does this mean?"
      onPointerEnter={place}
      onFocus={place}
    >
      i
      <span
        className={'mk-stat-pop' + (alignRight ? ' r' : '')}
        style={left === null ? undefined : { left, right: 'auto' }}
      >
        {text}
      </span>
    </span>
  )
}

// ── StatBar ───────────────────────────────────────────────────────────────────
// Renders the prototype StatBar on top of .sumbar / .sumcard (.sk/.sv).
// The SummaryBar ui/ primitive doesn't carry .sk-row / StatInfo — we extend it here.

interface StatBarProps {
  cards: PerfStatCard[]
}

function StatBar({ cards }: StatBarProps) {
  // We can't pass the help text to the SummaryBar primitive (it doesn't know StatInfo),
  // so we render the cards ourselves using the same .sumbar/.sumcard/.sk/.sv structure.
  return (
    <div className="sumbar">
      {cards.map((c, i) => (
        <div className="sumcard" key={c.key}>
          <div className="sk">
            <span className="sk-row">
              {c.label}
              {c.help && <StatInfo text={c.help} alignRight={i >= cards.length - 2} />}
            </span>
          </div>
          <div className={`sv${c.tone ? ' ' + c.tone : ''}`}>{c.value}</div>
        </div>
      ))}
    </div>
  )
}

// ── PerformanceTab ────────────────────────────────────────────────────────────
// The Performance sub-tab of Investments. Uses usePerformanceView for all data
// and renders the exact prototype layout: StatBar → Returns panel → Risk panel.

const PERIODS = PERFORMANCE_PERIODS

// With nothing held there is no return to show yet, and the useful thing to say is why the
// screen is worth filling: one return across every broker, measured against the market.
const ADD_ALL_MESSAGE =
  'Help me add my investments from every broker and account I use, so I can compare my ' +
  'whole portfolio with the market. Ask which brokers I use; I can send screenshots or ' +
  'statements instead of typing. Ask me one thing at a time.'

export function PerformanceTab({
  noHoldings = false,
  onAddHolding,
  onAskLucius,
}: {
  /** Nothing is held yet: pitch adding holdings instead of a price-history explanation. */
  noHoldings?: boolean
  onAddHolding?: () => void
  onAskLucius?: (text: string) => void
} = {}) {
  const vm = usePerformanceView()

  const {
    period,
    setPeriod,
    benchKey,
    setBenchKey,
    loading,
    error,
    benchmarks,
    data,
    sufficient,
    returnCards,
    benchCards,
    series,
    hasBenchmark,
    benchLabel,
    fallbackNote,
    risk,
  } = vm

  // Combine return + bench cards for the StatBar
  const allCards = [...returnCards, ...benchCards]

  // Plain-English risk summary: if no risk data yet, skip it.
  const rkNote = risk?.summary ?? ''

  const isEmpty = !loading && !error && data && !sufficient

  return (
    <div className="mk-subt">
      {/* Loading skeleton */}
      {!noHoldings && loading && !data && (
        <TabSkeleton variant="performance" label="Loading returns…" header={false} />
      )}

      {/* Error */}
      {!noHoldings && !loading && error && <ErrorState message="Couldn't load performance data." />}

      {/* Empty: nothing held yet */}
      {noHoldings && (
        <EmptyState
          fill
          icon={<ChartLine size={28} strokeWidth={1.5} aria-hidden="true" />}
          title="Your whole portfolio vs the market"
          body="Add your holdings from every broker and account to see one return for all of them, measured against indices like the S&P 500 and MSCI World."
          action={
            <Cluster gap="--space-3" justify="center">
              {onAddHolding && (
                <Button variant="primary" size="sm" onClick={onAddHolding}>
                  Add holding
                </Button>
              )}
              <LuciusCta
                label="Add with Lucius"
                message={ADD_ALL_MESSAGE}
                onAskLucius={onAskLucius}
              />
            </Cluster>
          }
        />
      )}

      {/* Empty: not enough history */}
      {!noHoldings && isEmpty && (
        <EmptyState
          title="No return to show yet"
          body={
            data?.data_quality.market_since
              ? `Not enough price history before ${data.data_quality.market_since} to compute a return over this window.`
              : 'Figures appear once prices have been collected across the window.'
          }
        />
      )}

      {/* Main content */}
      {!noHoldings && data && !error && sufficient && (
        <>
          {/* Return stat cards */}
          {allCards.length > 0 && <StatBar cards={allCards} />}

          {/* Returns & performance panel */}
          <div className="mk-panel">
            <div className="mk-panel-head">
              <span className="mk-panel-title">Returns &amp; performance</span>
              <span className="mk-panel-spacer" />
              {benchmarks.length > 0 && (
                <BenchmarkPicker value={benchKey} roster={benchmarks} onPick={setBenchKey} />
              )}
              <div className="mk-seg mk-seg--periods" role="group" aria-label="Range">
                {PERIODS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={period === p ? 'on' : ''}
                    aria-pressed={period === p}
                    onClick={() => setPeriod(p)}
                  >
                    {p === 'ALL' ? 'All' : p}
                  </button>
                ))}
              </div>
            </div>
            <div className="mk-panel-body">
              {series.length >= 2 ? (
                <PerfChart series={series} hasBenchmark={hasBenchmark} benchLabel={benchLabel} />
              ) : (
                <EmptyState
                  title="No chart data"
                  body="Not enough daily points to draw a curve for this window."
                />
              )}
              <div className="mk-legend">
                <span>
                  <i style={{ background: 'var(--good)' }} />
                  Your portfolio
                </span>
                {hasBenchmark && (
                  <span>
                    <i style={{ background: 'var(--muted)' }} />
                    {benchLabel}
                  </span>
                )}
              </div>
              {fallbackNote && <p className="inc-chart-cap">{fallbackNote}</p>}
            </div>
          </div>

          {/* Risk-adjusted return panel */}
          <div className="mk-panel">
            <div className="mk-panel-head">
              <span className="mk-panel-title">Risk-adjusted return</span>
              <span className="mk-panel-spacer" />
              <span className="mk-panel-sub">{period} • how much risk earned this return</span>
            </div>
            <div className="mk-panel-body">
              {risk ? (
                <>
                  <div className="rk-grid">
                    <div className="rk-cell">
                      <div className="rk-k">
                        <span className="sk-row">
                          Volatility (ann.)
                          <StatInfo text="How much your daily returns swing, annualized. Higher means a bumpier ride to the same destination." />
                        </span>
                      </div>
                      <div className="rk-v money">
                        {risk.vol != null ? risk.vol.toFixed(1) + '%' : '—'}
                      </div>
                    </div>
                    <div className="rk-cell">
                      <div className="rk-k">
                        <span className="sk-row">
                          {`Beta vs ${risk.betaSymbol}`}
                          <StatInfo
                            text={`How much you move relative to ${risk.betaSymbol}. 1.0 tracks it; above 1 amplifies its moves, below 1 dampens them.`}
                          />
                        </span>
                      </div>
                      <div className="rk-v money">
                        {risk.beta != null ? risk.beta.toFixed(2) : '—'}
                      </div>
                    </div>
                    <div className="rk-cell">
                      <div className="rk-k">
                        <span className="sk-row">
                          Sharpe ratio
                          <StatInfo text="Return earned per unit of risk, above the ~3.5% risk-free rate. Above 1 is good, above 2 is excellent." />
                        </span>
                      </div>
                      <div
                        className="rk-v money"
                        style={{
                          color:
                            risk.sharpe == null
                              ? 'var(--ink)'
                              : risk.sharpe >= 1
                                ? 'var(--good)'
                                : risk.sharpe < 0
                                  ? 'var(--bad)'
                                  : 'var(--ink)',
                        }}
                      >
                        {risk.sharpe != null ? risk.sharpe.toFixed(2) : '—'}
                      </div>
                    </div>
                    <div className="rk-cell">
                      <div className="rk-k">
                        <span className="sk-row">
                          Max drawdown
                          <StatInfo
                            text="The worst peak-to-trough drop over this period — your deepest dip before recovering. Smaller is calmer."
                            alignRight
                          />
                        </span>
                      </div>
                      <div className="rk-v money" style={{ color: 'var(--bad)' }}>
                        {risk.mdd != null ? risk.mdd.toFixed(1) + '%' : '—'}
                      </div>
                    </div>
                  </div>
                  {rkNote && (
                    <p className="inc-chart-cap">
                      <MaskedText>{rkNote}</MaskedText>
                    </p>
                  )}
                </>
              ) : (
                <EmptyState
                  title="Risk metrics unavailable"
                  body="Not enough price history to compute risk-adjusted metrics for this window."
                />
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
