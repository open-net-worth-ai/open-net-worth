// Price alerts on ONE ticker — two views, one modal host (the "Price Alerts Redesign"):
//   manage — every alert on the ticker, the 52-week track, Lucius's suggested set, and the
//            ticker-level settings (mute, news sources); every control commits itself;
//   form   — one alert, create or edit, saved with a single POST/PATCH.
// The views are verbatim ports (priceAlerts/ManageAlerts, priceAlerts/AlertForm); this file
// owns state and the data seam: /api/alerts is the alert store, the watchlist item carries
// mute + feeds, /api/intel/assets/{id}/stats supplies the 52-week range.
import { useEffect, useRef, useState } from 'react'
import { authFetch } from '../../api/client'
import { getCurrencySymbol } from '../../utils/format'
import { useLuciusContext } from '../../hooks/useLuciusContext'
import { luciusSuggestAlerts } from '../../utils/luciusEngine'
import type { LuciusAsset, LuciusContext } from '../../utils/luciusEngine'
import { errorDetail, watchlistFieldError } from './WatchlistsSection'
import { ManageAlerts } from './priceAlerts/ManageAlerts'
import type { SuggestionView } from './priceAlerts/ManageAlerts'
import { WatchIntentSection, type WatchIntent } from './WatchIntentSection'
import { AlertForm } from './priceAlerts/AlertForm'
import {
  createAlert,
  deleteAlert,
  draftFromRecord,
  draftToBody,
  foldOp,
  listAlerts,
  patchAlert,
} from './priceAlerts/alertModel'
import type { AlertDraft, AlertRecord } from './priceAlerts/alertModel'
import './priceAlerts/priceAlerts.css'

// Only the watchlist-item fields the modal actually reads — keeps the modal usable from any alert
// surface (Intel watchlist, asset-detail rail) without coupling to one caller's fuller item type.
export interface AlertModalItem {
  id: string // watchlist item id (for mute PATCH); may be '' when no item
  value: string // the ticker
  label: string
  asset_class: string
  instrument_id?: string
  price_alerts: AlertRecord[]
  monitor_news: boolean
  custom_feed_urls?: string[]
  type?: string
  intent?: WatchIntent | null
}

interface Props {
  item: AlertModalItem
  livePrice?: number
  /** Today's change in percent when the caller has a quote for it. */
  changePct?: number | null
  ccy: string
  /** Portfolio-aware engine context. Self-built from the shared portfolio context when omitted. */
  luciusCtx?: LuciusContext
  canRemove: boolean
  /** The footer's back action label — where closing this modal returns the user. */
  backLabel?: string
  onClose: () => void
  onSaved: () => void
  /** Required only when canRemove is true. */
  onRemove?: () => void
}

const SAVE_FAILED = 'Could not save — please try again.'

export function PriceAlertsModal(props: Props) {
  return props.luciusCtx ? (
    <AlertsModalBody {...props} ctx={props.luciusCtx} />
  ) : (
    <SelfContextAlertsModal {...props} />
  )
}

function SelfContextAlertsModal(props: Props) {
  const ctx = useLuciusContext()
  return <AlertsModalBody {...props} ctx={ctx} />
}

// The three quote stats the manage view reads (GET /api/intel/assets/{id}/stats — the same
// endpoint QuoteStats.tsx renders in full). Session-cached so a reopen paints instantly.
interface QuoteRange {
  prev_close?: number
  week52_low?: number
  week52_high?: number
}
const rangeCache = new Map<string, QuoteRange>()

function useQuoteRange(instrumentId: string | undefined): QuoteRange | null {
  const [range, setRange] = useState<QuoteRange | null>(() =>
    instrumentId ? (rangeCache.get(instrumentId) ?? null) : null,
  )
  useEffect(() => {
    if (!instrumentId) return
    let alive = true
    authFetch(`/api/intel/assets/${instrumentId}/stats`)
      .then((r) => (r && r.ok ? r.json() : null))
      .then((d: { stats?: QuoteRange } | null) => {
        if (!alive || !d?.stats) return
        rangeCache.set(instrumentId, d.stats)
        setRange(d.stats)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [instrumentId])
  return range
}

// Custom feeds ride the hot RSS lane, which the scheduler removes when monitoring is off or
// RSS freshness is 'off' — read the same config the Monitoring panel edits so the News
// sources copy can say when its feeds are not being fetched.
function useFeedLaneActive(enabled: boolean): boolean | null {
  const [active, setActive] = useState<boolean | null>(null)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    ;(async () => {
      try {
        const r = await authFetch('/api/watchlist')
        const d =
          r && r.ok
            ? ((await r.json()) as { config?: { enabled?: boolean; news_freshness?: string } })
            : null
        if (!alive || !d?.config) return
        setActive(d.config.enabled !== false && (d.config.news_freshness ?? 'fast') !== 'off')
      } catch {
        /* the copy simply stays neutral when the config cannot be read */
      }
    })()
    return () => {
      alive = false
    }
  }, [enabled])
  return active
}

function AlertsModalBody({
  item,
  livePrice,
  changePct,
  ccy,
  ctx,
  canRemove,
  backLabel,
  onClose,
  onSaved,
  onRemove,
}: Props & { ctx: LuciusContext }) {
  const ticker = item.value
  const [alerts, setAlerts] = useState<AlertRecord[]>(() => item.price_alerts || [])
  const [view, setView] = useState<'manage' | 'form'>('manage')
  const [draft, setDraft] = useState<AlertDraft | null>(null)
  const [muted, setMuted] = useState(item.monitor_news === false)
  const [feeds, setFeeds] = useState<string[]>(item.custom_feed_urls || [])
  const [sugg, setSugg] = useState<SuggestionView[] | null>(null)
  const feedLaneActive = useFeedLaneActive(!!item.id)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const instrumentId = item.instrument_id || alerts.find((a) => a.instrument_id)?.instrument_id
  const quote = useQuoteRange(instrumentId)
  const sym = getCurrencySymbol(ccy)
  const price = livePrice ?? null
  const prevClose = quote?.prev_close
  const chg = changePct ?? (price != null && prevClose ? (price / prevClose - 1) * 100 : null)
  const mounted = useRef(true)
  useEffect(() => () => void (mounted.current = false), [])

  async function refresh() {
    try {
      const next = await listAlerts(ticker)
      if (mounted.current) setAlerts(next)
    } catch {
      /* keep the last known list — the write itself already succeeded */
    }
    onSaved()
  }

  async function commit(work: () => Promise<void>): Promise<boolean> {
    setSaving(true)
    setError(null)
    try {
      await work()
      await refresh()
      return true
    } catch {
      if (mounted.current) setError(SAVE_FAILED)
      return false
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const createBody = (d: AlertDraft) => ({
    ...draftToBody(d),
    ticker,
    label: item.label || undefined,
    asset_class: item.asset_class,
    instrument_id: item.instrument_id || undefined,
  })

  const luciusAsset: LuciusAsset = {
    symbol: ticker,
    name: item.label,
    ccy,
    price: price ?? 0,
    cls: item.asset_class,
    alerts: alerts
      .filter((a) => a.kind !== 'move')
      .map((a) => ({
        op: foldOp(a.op) === '<=' ? 'below' : 'above',
        value: a.value,
        note: a.note,
      })),
  }
  const suggestAll = (): SuggestionView[] =>
    luciusSuggestAlerts(luciusAsset, ctx).map((s) => ({
      op: s.op === 'below' ? '<=' : '>=',
      value: s.value,
      name: s.name ?? '',
      why: s.why,
      note: s.note,
    }))
  const suggestionDraft = (s: SuggestionView): AlertDraft => ({
    id: null,
    name: s.name,
    mode: 'price',
    op: s.op,
    value: s.value,
    move: 5,
    after: '1440',
    channels: null,
    note: s.note,
    enabled: true,
  })

  function newDraft(): AlertDraft {
    const s = suggestAll()[0]
    return s
      ? { ...suggestionDraft(s), note: s.note }
      : {
          ...suggestionDraft({
            op: '<=',
            value: price ? Math.round(price * 0.94) : 0,
            name: '',
            why: '',
            note: '',
          }),
          value: price ? Math.round(price * 0.94) : null,
        }
  }

  async function toggle(id: string, on: boolean) {
    setAlerts((as) => as.map((x) => (x.id === id ? { ...x, enabled: on } : x)))
    const ok = await commit(() => patchAlert(id, { enabled: on }))
    if (!ok && mounted.current)
      setAlerts((as) => as.map((x) => (x.id === id ? { ...x, enabled: !on } : x)))
  }

  async function save(d: AlertDraft) {
    const ok = await commit(() =>
      d.id ? patchAlert(d.id, draftToBody(d)) : createAlert(createBody(d)),
    )
    if (ok) setView('manage')
  }

  async function remove(id: string) {
    const ok = await commit(() => deleteAlert(id))
    if (ok) setView('manage')
  }

  async function addSuggestions(list: SuggestionView[]) {
    const ok = await commit(async () => {
      for (const s of list) await createAlert(createBody(suggestionDraft(s)))
    })
    if (ok) setSugg((cur) => (cur ?? []).filter((s) => !list.includes(s)))
  }

  async function patchItem(body: Record<string, unknown>) {
    if (!item.id) return
    const res = await authFetch(`/api/watchlist/items/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const detail = res.status === 422 ? await errorDetail(res, SAVE_FAILED) : SAVE_FAILED
      throw new Error(watchlistFieldError(detail).field ? detail : SAVE_FAILED)
    }
  }

  async function mute(on: boolean) {
    setMuted(on)
    setError(null)
    try {
      await patchItem({ monitor_news: !on })
      onSaved()
    } catch {
      if (!mounted.current) return
      setMuted(!on)
      setError(SAVE_FAILED)
    }
  }

  async function commitFeeds(next: string[]) {
    setError(null)
    try {
      await patchItem({ custom_feed_urls: next.map((u) => u.trim()).filter(Boolean) })
      onSaved()
    } catch (e) {
      if (mounted.current) setError(e instanceof Error && e.message ? e.message : SAVE_FAILED)
    }
  }

  if (view === 'form' && draft) {
    return (
      <AlertForm
        key={draft.id || 'new'}
        draft={draft}
        sym={ticker}
        name={item.label || ticker}
        ccy={sym}
        price={price}
        saving={saving}
        error={error}
        onCancel={() => {
          setError(null)
          setView('manage')
        }}
        onSave={(d) => void save(d)}
        onDelete={draft.id ? () => void remove(draft.id!) : undefined}
      />
    )
  }

  return (
    <ManageAlerts
      sym={ticker}
      name={item.label || ticker}
      ccy={sym}
      price={price}
      chg={chg}
      lo={quote?.week52_low ?? null}
      hi={quote?.week52_high ?? null}
      alerts={alerts}
      muted={muted}
      feeds={feeds}
      hasItem={!!item.id}
      feedLaneActive={feedLaneActive}
      sugg={sugg}
      error={error}
      backLabel={backLabel ?? 'Back'}
      canRemove={canRemove}
      onSuggest={() => setSugg(suggestAll())}
      onDismissSuggestion={(k) => setSugg((cur) => (cur ?? []).filter((_, m) => m !== k))}
      onMute={(on) => void mute(on)}
      onFeeds={setFeeds}
      onCommitFeeds={(next) => void commitFeeds(next)}
      onToggle={(id, on) => void toggle(id, on)}
      onNew={() => {
        setError(null)
        setDraft(newDraft())
        setView('form')
      }}
      onAdd={(s) => void addSuggestions([s])}
      onAddAll={() => void addSuggestions(sugg ?? [])}
      onEdit={(r) => {
        setError(null)
        setDraft(draftFromRecord(r))
        setView('form')
      }}
      onRemove={onRemove}
      onClose={onClose}
      intentSection={
        (item.type ?? 'ticker') === 'ticker' ? (
          <WatchIntentSection
            key={item.id}
            sym={ticker}
            ccy={sym}
            held={!!ctx.holdingsBySymbol[ticker.toUpperCase()]}
            intent={item.intent ?? null}
            onSave={async (next) => {
              await patchItem({ intent: next })
              onSaved()
            }}
          />
        ) : undefined
      }
    />
  )
}
