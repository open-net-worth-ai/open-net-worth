// FeedCards — card-density holdings feed. Verbatim port of prototype FeedCards({ groups }).
// Each group renders as an intel-sec with mk-hold cards.

import type { BookGroup, BookRow } from './useInvestmentsBook'
import { IntelStoryDot } from '../../shared/IntelStoryDot'
import type { TickerStories } from '../../../hooks/useIntelStoryIndex'
import { storyKey } from './storyKey'
import { fBase, fUnitPrice, fPct, priceDigits } from '../../../utils/format'
import { ASSET_CLASS_COLOR, paletteColor } from '../../../utils/constants'
import { EntityRow } from '../../../lucius/EntityRow'

// Wrapper badges for certain asset sub-classes (mirrors prototype WRAP map)
const WRAP: Record<string, string> = {
  etf: 'ETF',
  etc: 'ETC',
  etp: 'ETP',
  bondetf: 'ETF',
  govt: 'GOVT',
}

function tone(v: number | null): string {
  if (v == null) return 'var(--muted)'
  return v >= 0 ? 'var(--good)' : 'var(--bad)'
}

/** Signed base amount, or a dash when the figure is unknown (no cost basis recorded). */
function fSignedBase(v: number | null): string {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '−') + fBase(Math.abs(v))
}

interface SortState {
  key: string
  dir: number
}

// Card-mode column header → the sortRows() key it sorts by (mirrors the table-mode thead).
const HEAD_COLS: { k: string; label: string }[] = [
  { k: 'sym', label: 'Holding' },
  { k: 'value', label: 'Value' },
  { k: 'wt', label: 'Weight' },
  { k: 'pl', label: 'P&L' },
  // Price is its own labelled column rather than a second line under TODAY — a
  // per-share quote sitting beneath a "Today" header reads as a day figure.
  { k: 'price', label: 'Price' },
  { k: 'day', label: 'Today' },
]

// Stable module-level component so it isn't remounted on every FeedCards render.
function CardHead({ sort, onSort }: { sort?: SortState; onSort?: (key: string) => void }) {
  return (
    <div className="mk-cardhead">
      {HEAD_COLS.map((c) =>
        onSort ? (
          <span
            key={c.k}
            className="mk-ch-sort"
            role="button"
            tabIndex={0}
            title="Sort by this column"
            aria-label={`Sort by ${c.label}`}
            onClick={() => onSort(c.k)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSort(c.k)
              }
            }}
          >
            {c.label}{' '}
            <span className="ar">{sort?.key === c.k ? (sort.dir > 0 ? '▲' : '▼') : ''}</span>
          </span>
        ) : (
          <span key={c.k}>{c.label}</span>
        ),
      )}
    </div>
  )
}

interface Props {
  groups: BookGroup[]
  onOpenAssetDetail?: (row: BookRow) => void
  // When wired, the card header becomes tap-to-sort (card mode parity with the table).
  sort?: SortState
  onSort?: (key: string) => void
  // Drift (pp) at/above which a class is flagged "off target" — the server's ONE constant
  // (rebalance.DRIFT_FLAG_PP), never a number of our own (MATH-37). Defaults to the server's
  // own default so a caller that hasn't wired it through yet still gets the right threshold.
  thresholdPp?: number
  /** Upper-cased ticker → live Intel stories (useIntelStoryIndex). */
  storyIndex?: Map<string, TickerStories>
  onOpenStory?: (storyId: string) => void
}

export function FeedCards({
  groups,
  onOpenAssetDetail,
  sort,
  onSort,
  thresholdPp = 5,
  storyIndex,
  onOpenStory,
}: Props) {
  return (
    <>
      {groups.map((g) => {
        const drift = g.tgt != null ? g.cur - g.tgt : null
        const off = drift != null && Math.abs(drift) >= thresholdPp

        return (
          <section key={g.key} className="intel-sec">
            <div className="intel-sec-head">
              {g.color && <span className="intel-sec-dot" style={{ background: g.color }} />}
              <span className="intel-sec-title">{g.label || 'All holdings'}</span>
              <span className="intel-sec-count">{g.rows.length}</span>
              <span className="mk-sec-sub mask">
                {fBase(g.subtotal)} • {g.cur}% of book
              </span>
              {off && (
                <span className="mk-sec-sub">
                  target {g.tgt}%{' '}
                  <span className="mk-sec-drift money">
                    {(drift! > 0 ? '+' : '−') + Math.abs(drift!)}
                  </span>
                </span>
              )}
            </div>

            <CardHead sort={sort} onSort={onSort} />

            {g.rows.map((r) => {
              const dir = r.day > 0 ? 'up' : r.day < 0 ? 'down' : 'flat'
              const badge = WRAP[r.sub]
              // A merged (multi-account) row has no single POSITION to hang a mention on — but
              // it's still one SECURITY. "Ask Lucius about this" stays meaningful, re-anchored
              // to the instrument (mention type "instrument", ref: instrument_id) below.
              const positionId = r.mergedCount > 1 ? undefined : r._raw.position_id
              const instrumentId = r._raw.instrument_id
              const inner = (
                <>
                  <div className="mk-hold-inner">
                    <div className="mk-id">
                      <span
                        className="mk-hold-marker"
                        style={{
                          background: g.color || ASSET_CLASS_COLOR[r.cls] || paletteColor(r.cls),
                        }}
                        aria-hidden
                      />
                      <div className="mk-id-copy">
                        <div className="mk-sym">
                          {r.sym}
                          {badge && <span className="mk-wrap-badge">{badge}</span>}
                          {onOpenStory && (
                            <IntelStoryDot
                              stories={storyIndex?.get(storyKey(r))}
                              onOpen={onOpenStory}
                            />
                          )}
                        </div>
                        <div className="mk-nm">
                          {r.name}
                          {r.coupon != null && (
                            <span className="mk-bond-meta">
                              {r.coupon}%{' '}
                              {r.maturity ? '• mat ' + String(r.maturity).slice(0, 4) : '• fund'}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="mk-card-metrics">
                      <div className="mk-col">
                        <div className="mk-v mask">{fBase(r.value)}</div>
                      </div>
                      <div className="mk-col">
                        <div className="mk-v mask">{r.wt.toFixed(1)}%</div>
                      </div>
                      <div className="mk-col">
                        <div className="mk-v mask" style={{ color: tone(r.pl) }}>
                          {fSignedBase(r.pl)}
                        </div>
                        <div className="mk-plpct mask" style={{ color: tone(r.pl) }}>
                          {r.plpct == null ? '—' : fPct(r.plpct)}
                        </div>
                      </div>
                    </div>
                    {/* Desktop price column. On a phone this is hidden and the price is
                        shown by .mk-market inside the footer band below instead. */}
                    <div className="mk-col mk-price-col">
                      <div className="mk-v">{fUnitPrice(r.price, r.ccy, priceDigits(r.cls))}</div>
                    </div>
                    <div
                      className="mk-market-move"
                      aria-label={`Latest price ${fUnitPrice(r.price, r.ccy, priceDigits(r.cls))}, today ${fPct(r.day)}`}
                    >
                      <div className="mk-market">
                        <span>{fUnitPrice(r.price, r.ccy, priceDigits(r.cls))}</span>
                      </div>
                      <div className={`mk-day ${dir}`}>{fPct(r.day)}</div>
                    </div>
                  </div>
                </>
              )
              if (!positionId) {
                if (!instrumentId) {
                  return (
                    <article
                      className="mk-hold clickable"
                      key={r.rowKey}
                      onClick={() => onOpenAssetDetail?.(r)}
                    >
                      {inner}
                    </article>
                  )
                }
                return (
                  <EntityRow
                    key={r.rowKey}
                    as="article"
                    type="instrument"
                    id={instrumentId}
                    label={r.sym || r.name}
                    search={[r.sym, r.name].filter(Boolean).join(' ')}
                    item={r}
                    toFields={(it) => ({
                      symbol: it.sym || null,
                      name: it.name || null,
                      asset_class: it.cls || null,
                      // Per-share price is quoted in the instrument's OWN currency (it.ccy),
                      // not the portfolio base — show "$380.73"/"€35.81", never "S$380.73".
                      quote_currency: it.ccy || null,
                      latest_price: fUnitPrice(it.price, it.ccy, priceDigits(it.cls)),
                    })}
                    className="mk-hold clickable"
                    onClick={() => onOpenAssetDetail?.(r)}
                  >
                    {inner}
                  </EntityRow>
                )
              }
              return (
                <EntityRow
                  key={r.rowKey}
                  as="article"
                  type="holding"
                  id={positionId}
                  label={r.sym || r.name}
                  search={[r.sym, r.name, r.account].filter(Boolean).join(' ')}
                  item={r}
                  toFields={(it) => {
                    // Derived from `it`, not the outer row, so the mapping stays correct
                    // if this ever gets called with anything but the row it was built for.
                    const itDayPL = it._raw._daily_pl
                    return {
                      symbol: it.sym || null,
                      name: it.name || null,
                      account: it.account || null,
                      // Per-share price is quoted in the instrument's OWN currency (it.ccy),
                      // not the portfolio base — show "$380.73"/"€35.81", never "S$380.73".
                      price: fUnitPrice(it.price, it.ccy, priceDigits(it.cls)),
                      value_base: fBase(it.value),
                      unrealized_base: it.pl == null ? null : fSignedBase(it.pl),
                      day_change_base:
                        itDayPL == null
                          ? null
                          : (itDayPL >= 0 ? '+' : '−') + fBase(Math.abs(itDayPL)),
                    }
                  }}
                  className="mk-hold clickable"
                  onClick={() => onOpenAssetDetail?.(r)}
                >
                  {inner}
                </EntityRow>
              )
            })}
          </section>
        )
      })}
    </>
  )
}
