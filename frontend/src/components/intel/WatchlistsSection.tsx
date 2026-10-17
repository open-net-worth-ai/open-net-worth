import { useState } from 'react'
import { Skeleton } from '../ui'
import { authFetch } from '../../api/client'
import { ChevronRight, ArrowRight } from '../../ui/icons'
import type { TickerMeta } from './StoryArcCard'
import { fPct, fPrice, getCurrencySymbol, getBaseCurrency } from '../../utils/format'
import { AssetLink } from './AssetLink'
import { toneColor } from './pulseHelp'
import { alertChipText } from './priceAlerts/alertModel'

// ── Types ─────────────────────────────────────────────────────────────────────

// A named watchlist (mirrors GET /api/watchlist → watchlists[]).
export interface Watchlist {
  id: string
  name: string
  description: string | null
  is_auto_sync: boolean
  kind: string
  order: number
  muted?: boolean
}

export interface PriceAlert {
  id?: string
  ticker?: string
  op: '>=' | '<=' | '>' | '<'
  /** A price level for kind 'price'; a percent-of-day-move threshold for kind 'move'. */
  value: number
  /** The alert's own short display name (distinct from `label`, the ticker's name). */
  name?: string
  kind?: 'price' | 'move'
  notify?: 'push' | 'in_app' | 'push_email'
  note?: string
  label?: string
  asset_class?: string
  /** What happens after this alert fires once — go quiet for cooldown_minutes and re-arm, or
   * turn off until manually re-enabled. Absent on legacy rows ⇒ treat as 'cooldown'. */
  on_trigger?: 'cooldown' | 'disable'
  /** Minutes to stay quiet after firing (0 = none — may fire repeatedly). Absent ⇒ 30. */
  cooldown_minutes?: number
  /** False once a disable-mode alert has fired — stays off until explicitly re-enabled. */
  enabled?: boolean
  last_fired_at?: string | null
}

export type AlertRow = PriceAlert & { id?: string; ticker?: string }

export interface WatchlistItem {
  id: string
  type: 'ticker' | 'topic' | 'company'
  value: string
  label: string
  asset_class: string
  monitor_news: boolean
  added_by: string
  note: string
  watchlist_ids: string[]
  last_price_checked_at?: string | null
  instrument_id?: string
  // Topic↔instrument linkage (topics only) + user-extensible custom RSS/Atom feeds
  // (ticker or topic) — the Advanced: news sources section on both editor modals.
  linked_tickers?: string[]
  custom_feed_urls?: string[]
  // Tickers: why you watch it — thesis / buy zone / fair value (WatchIntentSection).
  intent?: import('./WatchIntentSection').WatchIntent | null
}

// Pull a server error message (FastAPI {detail}) off a failed response, else fallback.
export async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const d = await res.json()
    if (typeof d?.detail === 'string' && d.detail) return d.detail
  } catch {
    /* body not JSON */
  }
  return fallback
}

// The watchlist-item write choke (db/watchlist_store.py) prefixes its ValidationError
// message with the offending field name ("linked_tickers: ..." / "custom_feed_urls:
// 'bad-url' is not a safe..."). Field-error responses route inline to the matching
// FormField instead of the generic save-failed banner; `quoted` (if present) is the
// single-quoted value substring, used to also highlight a matching feed-row TextField.
export function watchlistFieldError(detail: string): {
  field: 'linked_tickers' | 'custom_feed_urls' | null
  quoted: string | null
} {
  const field = detail.startsWith('linked_tickers')
    ? 'linked_tickers'
    : detail.startsWith('custom_feed_urls')
      ? 'custom_feed_urls'
      : null
  const m = field ? detail.match(/'([^']+)'/) : null
  return { field, quoted: m?.[1] ?? null }
}

function ccySym(ticker: string | undefined, tickerCcy: Record<string, string>): string {
  return getCurrencySymbol((ticker && tickerCcy[ticker]) || getBaseCurrency())
}

function cleanTicker(t: string): string {
  return t.replace(/\.(DE|F|PA|SI|L|T|AX)$/, '')
}

// ── Alert chip ──────────────────────────────────────────────────────────────────

// The name (when set) rides the tooltip beside the instructions.
function AlertChip({ a, sym }: { a: PriceAlert; sym: string }) {
  const title = [a.name, a.note].filter((t) => t && t.trim()).join(' — ')
  return (
    <span className="intel-wlc-alert-chip" title={title}>
      {alertChipText(a, sym)}
    </span>
  )
}

// ── Asset row ────────────────────────────────────────────────────────────────────
//
// The symbol/name area is an AssetLink (opens asset-detail). Alerts editing is
// reached via the explicit ChevronRight edit-button at the row end — both are
// real <button> elements, so this row itself is a <div> to avoid nesting buttons.

function AssetRow({
  item,
  alerts: alertsProp,
  tickerMeta,
  tickerCcy,
  owned,
  dayPct,
  hasStory,
  onEdit,
}: {
  item: WatchlistItem
  alerts?: PriceAlert[]
  dayPct?: number
  hasStory?: boolean
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  owned: boolean
  onEdit: (item: WatchlistItem) => void
}) {
  const meta = tickerMeta[item.value]
  const sym = ccySym(item.value, tickerCcy)
  const alerts = alertsProp || []
  const muted = item.monitor_news === false

  return (
    <div className="intel-wlc-row">
      <div className="intel-wlc-row-top">
        <AssetLink
          instrumentId={item.instrument_id}
          ticker={item.type === 'ticker' ? item.value : undefined}
          label={item.label || item.value}
          className="intel-wlc-row-main"
        >
          <span className="intel-wlc-row-sym">{cleanTicker(item.value)}</span>
          <span className="intel-wlc-row-name" title={meta?.name || item.label || item.value}>
            {meta?.name || item.label || item.value}
          </span>
          {owned && <span className="intel-wlc-row-badge intel-wlc-row-badge--owned">OWNED</span>}
          {muted && <span className="intel-wlc-row-badge intel-wlc-row-badge--muted">MUTED</span>}
        </AssetLink>
        <span className="intel-wlc-row-price money">
          {meta?.price != null ? (
            <>
              {sym}
              {fPrice(meta.price)}
            </>
          ) : (
            '—'
          )}
          {dayPct != null && (
            <span className="intel-wlc-row-day" style={{ color: toneColor(dayPct) }}>
              {fPct(dayPct, { sign: true })}
            </span>
          )}
          {hasStory && (
            <span
              className="intel-wlc-row-storydot"
              role="img"
              aria-label="Has a live story in the feed"
              title="Has a live story in the feed"
            />
          )}
        </span>
        <button
          type="button"
          className="intel-wlc-row-edit"
          onClick={() => onEdit(item)}
          title="Edit alerts"
          aria-label={`Edit alerts for ${item.label || item.value}`}
        >
          <ChevronRight size={11} />
        </button>
      </div>
      <div className="intel-wlc-row-alerts">
        {alerts.length > 0 ? (
          <>
            {alerts.slice(0, 3).map((a, i) => (
              <AlertChip key={i} a={a} sym={sym} />
            ))}
            {alerts.length > 3 && <span className="intel-wlc-row-more">+{alerts.length - 3}</span>}
          </>
        ) : (
          <span className="intel-wlc-row-noalert">No alerts • tap to add</span>
        )}
      </div>
    </div>
  )
}

// ── Inline add-ticker (cockpit Tickers tab) ───────────────────────────────────

function CockpitAddTicker({ listId, onAdded }: { listId: string; onAdded: () => void }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function add() {
    const t = val.trim().toUpperCase()
    if (!t) return
    setBusy(true)
    setErr(null)
    try {
      const res = await authFetch('/api/watchlist/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'ticker',
          value: t,
          asset_class: 'equity',
          watchlist: listId,
        }),
      })
      if (res.status === 409) {
        setErr(`${t} is already in this watchlist.`)
        return
      }
      if (!res.ok) {
        setErr(await errorDetail(res, 'Could not add — try again.'))
        return
      }
      setVal('')
      onAdded()
    } catch {
      setErr('Could not add — try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="intel-wlc-add">
      <div className="intel-wlc-add-row">
        <input
          className="ui-input intel-wlc-add-input"
          value={val}
          onChange={(e) => {
            setVal(e.target.value.toUpperCase())
            if (err) setErr(null)
          }}
          onKeyDown={(e) => e.key === 'Enter' && void add()}
          placeholder="Add ticker — e.g. NVDA"
        />
        <button
          type="button"
          className="intel-wlc-add-btn"
          onClick={() => void add()}
          disabled={!val.trim() || busy}
        >
          {busy ? '…' : 'Add'}
        </button>
      </div>
      {err && <div className="intel-wlc-add-err">{err}</div>}
    </div>
  )
}

// ── Watchlists cockpit (sidebar card) ─────────────────────────────────────────

interface Props {
  watchlists: Watchlist[]
  items: WatchlistItem[]
  alerts?: PriceAlert[]
  /** The list the Intel tab is on — its tabs ARE the lists, so the card names it and manages its
   *  tickers. A watchlist holds tickers only; topics are followed on the Topics & macro tab
   *  (ADR 2026-09-27-topics-are-followed-not-listed). */
  activeListId: string
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  ownedTickers: Set<string>
  /** Today's move per watched ticker (from the watch pulse) — rendered beside the price. */
  dayPctByTicker?: Record<string, number>
  /** Tickers with a live feed story — rendered as a dot on the row. */
  storyTickers?: Set<string>
  onEditAlerts: (item: WatchlistItem, sourceListId?: string) => void
  onManageAll: () => void
  onRefresh: () => void
  loading?: boolean
  error?: boolean
}

export function WatchlistsSection({
  watchlists,
  items,
  alerts,
  activeListId,
  tickerMeta,
  tickerCcy,
  ownedTickers,
  dayPctByTicker,
  storyTickers,
  onEditAlerts,
  onManageAll,
  onRefresh,
  loading,
  error,
}: Props) {
  if (loading) {
    return (
      <div className="intel-wlc">
        <div className="intel-wlc-skel">
          <Skeleton height={34} />
          <Skeleton height={46} />
          <Skeleton height={46} />
        </div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="intel-wlc">
        <div className="intel-wlc-error">
          Could not load watchlists.{' '}
          <button type="button" className="intel-wlc-retry" onClick={onRefresh}>
            Retry
          </button>
        </div>
      </div>
    )
  }

  const active = watchlists.find((c) => c.id === activeListId) || watchlists[0]
  if (!active) {
    return (
      <div className="intel-wlc">
        <div className="intel-wlc-error">No watchlists yet.</div>
      </div>
    )
  }
  // watchlist_ids can be missing on legacy/malformed items (pre-multi-watchlist data) —
  // guard so a stale item can't crash the whole tab.
  const listItems = items.filter((i) => (i.watchlist_ids ?? []).includes(active.id))
  const assets = listItems.filter((i) => i.type === 'ticker')

  return (
    <div className="intel-wlc">
      {/* header — clickable → workspace */}
      <button
        type="button"
        className="intel-wlc-head"
        onClick={onManageAll}
        title="Manage all watchlists & monitors"
      >
        <span className="intel-wlc-head-title">{active.name}</span>
        {active.muted && <span className="intel-wlc-switch-muted">muted</span>}
        <span className="intel-wlc-manage">
          Manage all <ArrowRight size={12} />
        </span>
      </button>

      {/* rows */}
      <div className="intel-wlc-rows">
        {assets.map((it) => (
          <AssetRow
            key={it.id}
            item={it}
            alerts={alerts?.filter((a) => (a.ticker || '') === it.value)}
            tickerMeta={tickerMeta}
            tickerCcy={tickerCcy}
            owned={!active.is_auto_sync && ownedTickers.has(it.value)}
            dayPct={dayPctByTicker?.[it.value.toUpperCase()]}
            hasStory={storyTickers?.has(it.value.toUpperCase())}
            onEdit={(i) => onEditAlerts(i, active.id)}
          />
        ))}
        {assets.length === 0 && (
          <div className="intel-wlc-empty">
            {active.is_auto_sync
              ? 'No positions yet.'
              : 'No tickers in this list yet — add one below.'}
          </div>
        )}
        {!active.is_auto_sync && (
          <div className="intel-wlc-add-wrap">
            <CockpitAddTicker listId={active.id} onAdded={onRefresh} />
          </div>
        )}
      </div>
    </div>
  )
}
