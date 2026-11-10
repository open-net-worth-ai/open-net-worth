// Manage view — every alert on one ticker (verbatim port of the prototype's ManageAlerts).
// Each control in this view commits itself — the per-alert toggles already do, so a Save
// button covering only mute and feeds would be ambiguous. Mute sits in the header: it silences
// every alert on the ticker and its news scans, so it governs the whole dialog. A feed row commits on blur and an
// empty one is dropped there, matching the upstream filter.
import { Fragment, type ReactNode, useLayoutEffect, useRef, useState } from 'react'
import { Button, EmptyState, Modal, Money, Panel } from '../../ui'
import { Bell, BellOff, ChevronLeft } from '../../../ui/icons'
import { NEAR, fmt, foldOp, layoutTickRows, toFire } from './alertModel'
import type { AlertRecord } from './alertModel'
import { AlertRow, SuggestionSet, TickerSettings } from './ManageAlertsParts'
import type { SuggestionView } from './ManageAlertsParts'
import { BuildWithLucius, alertsBuildPrompt } from '../BuildWithLucius'

export type { SuggestionView } from './ManageAlertsParts'

export interface ManageAlertsProps {
  sym: string
  name: string
  ccy: string
  price: number | null
  /** Today's change in percent — omitted when the caller has no quote for it. */
  chg: number | null
  /** 52-week low/high when the quote source has them. */
  lo: number | null
  hi: number | null
  alerts: AlertRecord[]
  muted: boolean
  feeds: string[]
  /** Mute + news sources belong to the watchlist item — hidden when this ticker has none. */
  hasItem: boolean
  /** Whether the hot RSS lane that fetches custom feeds is running (null = not known yet). */
  feedLaneActive: boolean | null
  /** null = not asked yet; [] = asked and nothing (or everything) taken. */
  sugg: SuggestionView[] | null
  error: string | null
  backLabel: string
  canRemove: boolean
  onSuggest: () => void
  onDismissSuggestion: (index: number) => void
  onMute: (on: boolean) => void
  onFeeds: (next: string[]) => void
  onCommitFeeds: (next: string[]) => void
  onToggle: (id: string, on: boolean) => void
  onNew: () => void
  onAdd: (s: SuggestionView) => void
  onAddAll: () => void
  onEdit: (r: AlertRecord) => void
  onRemove?: () => void
  onClose: () => void
  /** "Why you're watching" (thesis / buy zone / fair value) — shown above the ticker settings. */
  intentSection?: ReactNode
}

export function ManageAlerts(p: ManageAlertsProps) {
  const { sym, ccy, price, alerts: mine, feeds, sugg } = p
  const live = mine.filter((x) => x.enabled !== false)
  const gapOf = (r: AlertRecord): number =>
    price && r.kind !== 'move' ? toFire(foldOp(r.op), r.value, price) : Number.POSITIVE_INFINITY
  const sorted = [...mine]
    .map((x) => ({ r: x, gap: gapOf(x) }))
    .sort(
      (x, y) =>
        Number(x.r.enabled === false) - Number(y.r.enabled === false) ||
        Math.abs(x.gap) - Math.abs(y.gap),
    )
  const levels = mine.filter((r) => r.kind !== 'move')
  const vals = levels.map((x) => x.value)
  const lo = Math.min(price ?? Number.POSITIVE_INFINITY, ...vals)
  const hi = Math.max(price ?? Number.NEGATIVE_INFINITY, ...vals)
  const pad = (hi - lo) * 0.07 || 1
  // Measure the track so close-together levels can stagger their labels (prototype levels
  // are spread out; real books cluster stops a few dollars apart).
  const scaleRef = useRef<HTMLDivElement>(null)
  const [scaleW, setScaleW] = useState(0)
  useLayoutEffect(() => {
    const read = () => setScaleW(scaleRef.current?.clientWidth ?? 0)
    read()
    window.addEventListener('resize', read)
    return () => window.removeEventListener('resize', read)
  })
  const pct = (v: number) =>
    Math.max(0, Math.min(100, ((v - (lo - pad)) / (hi + pad - (lo - pad))) * 100))
  const tickRows = layoutTickRows(
    levels.map((r) => ({ pct: pct(r.value), text: `${ccy}${fmt(r.value)}` })),
    scaleW,
  )
  const tickRowCount = 1 + Math.max(0, ...tickRows)

  return (
    <Modal
      open
      onClose={p.onClose}
      size="lg"
      className="pa-modal"
      title={`${sym} · ${p.name}`}
      footer={
        <div className="pa-foot">
          <button type="button" className="pa-back" onClick={p.onClose}>
            <ChevronLeft size={12} strokeWidth={2.2} /> {p.backLabel}
          </button>
          {p.canRemove && p.onRemove && (
            <button type="button" className="pa-del" onClick={p.onRemove}>
              Remove from watchlist
            </button>
          )}
          {p.error && <span className="pa-err">{p.error}</span>}
          <div className="pa-foot-r">
            <Button variant="primary" size="sm" onClick={p.onNew}>
              New alert
            </Button>
          </div>
        </div>
      }
    >
      <div className="pa-head">
        {price != null ? (
          <Money className="pa-head-px">
            {ccy}
            {fmt(price)}
          </Money>
        ) : (
          <span className="pa-head-px">Price unavailable</span>
        )}
        {p.chg != null && (
          <span className={'pa-head-ch' + (p.chg < 0 ? ' pa-head-ch--dn' : '')}>
            {p.chg >= 0 ? '+' : ''}
            {p.chg.toFixed(2)}% today
          </span>
        )}
        {p.hasItem && (
          <button
            type="button"
            className={'pa-mute' + (p.muted ? ' pa-mute--on' : '')}
            aria-pressed={p.muted}
            aria-label={`Mute ${sym}`}
            title={
              p.muted
                ? `Muted: no price alerts or news scans for ${sym}`
                : `Stop price alerts and news scans for ${sym}`
            }
            onClick={() => p.onMute(!p.muted)}
          >
            {p.muted ? (
              <BellOff size={13} strokeWidth={2.2} />
            ) : (
              <Bell size={13} strokeWidth={2.2} />
            )}
            {p.muted ? 'Muted' : 'Mute'}
          </button>
        )}
      </div>

      {levels.length > 0 && price != null && (
        <>
          <div className="pa-scale-title">Alert levels</div>
          <div
            ref={scaleRef}
            className={'pa-scale' + (tickRowCount > 1 ? ` pa-scale--${tickRowCount}rows` : '')}
          >
            <span className="pa-nowlab" style={{ left: pct(price) + '%' }}>
              now{' '}
              <Money>
                {ccy}
                {fmt(price)}
              </Money>
            </span>
            <span className="pa-now" style={{ left: pct(price) + '%' }} />
            {levels.map((r, i) => {
              const g = toFire(foldOp(r.op), r.value, price)
              const cls = r.enabled === false ? '--off' : g > 0 && g <= NEAR ? '--near' : ''
              return (
                <Fragment key={r.id ?? i}>
                  <span
                    className={'pa-tk' + (cls ? ' pa-tk' + cls : '')}
                    style={{ left: pct(r.value) + '%' }}
                  />
                  <span
                    className={
                      'pa-tklab' +
                      (cls ? ' pa-tklab' + cls : '') +
                      (tickRows[i] ? ` pa-tklab--r${tickRows[i]! + 1}` : '')
                    }
                    style={{ left: pct(r.value) + '%' }}
                  >
                    <Money>
                      {ccy}
                      {fmt(r.value)}
                    </Money>
                  </span>
                </Fragment>
              )
            })}
          </div>
          {p.lo != null && p.hi != null && (
            <div className="pa-range">
              52w low{' '}
              <Money>
                {ccy}
                {fmt(p.lo)}
              </Money>{' '}
              · high{' '}
              <Money>
                {ccy}
                {fmt(p.hi)}
              </Money>
            </div>
          )}
        </>
      )}

      {mine.length === 0 ? (
        <EmptyState
          title={`No alerts on ${sym} yet`}
          body="Lucius tracks the price either way — it just won't tell you until there's a level worth hearing about."
          action={
            <div className="asd-alerts-start asd-alerts-start--center">
              <BuildWithLucius sym={sym} prompt={alertsBuildPrompt(sym)} />
              <Button variant="secondary" size="sm" onClick={p.onNew}>
                New alert
              </Button>
            </div>
          }
        />
      ) : (
        <>
          <SuggestionSet
            sym={sym}
            ccy={ccy}
            sugg={sugg}
            onSuggest={p.onSuggest}
            onAdd={p.onAdd}
            onAddAll={p.onAddAll}
            onDismiss={p.onDismissSuggestion}
          />
          <Panel title="Alerts" badge={`${live.length} of ${mine.length} on`}>
            <div className="pa-list">
              {sorted.map(({ r, gap }, i) => (
                <AlertRow
                  key={r.id ?? i}
                  r={r}
                  gap={gap}
                  ccy={ccy}
                  chg={p.chg}
                  onEdit={p.onEdit}
                  onToggle={p.onToggle}
                />
              ))}
            </div>
          </Panel>
        </>
      )}

      {p.hasItem && p.intentSection}

      {p.hasItem && (
        <TickerSettings
          sym={sym}
          feeds={feeds}
          feedLaneActive={p.feedLaneActive}
          onFeeds={p.onFeeds}
          onCommitFeeds={p.onCommitFeeds}
        />
      )}
    </Modal>
  )
}
