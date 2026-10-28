// The manage view's three sub-blocks — the Lucius suggestion set, one alert row, and the
// ticker-level settings (mute + news feeds). Split out of ManageAlerts.tsx for file size only;
// the DOM and class names are the prototype's, unchanged.
import { useRef, useState } from 'react'
import { Button, Money, Switch, TextField } from '../../ui'
import { Check, ChevronDown, ChevronRight, RefreshCw, Sparkles, X } from '../../../ui/icons'
import {
  AFTER_SHORT,
  NEAR,
  OPSYM,
  afterFireFromCondition,
  CUSTOM_FEED_POLL_SECONDS,
  conditionChip,
  fmt,
  rowTitle,
} from './alertModel'
import type { AlertRecord } from './alertModel'
import { routeSummary, useAlertRouting } from './alertRouting'

export interface SuggestionView {
  op: '>=' | '<='
  value: number
  name: string
  why: string
  note: string
}

export function SuggestionSet({
  sym,
  ccy,
  sugg,
  onSuggest,
  onAdd,
  onAddAll,
  onDismiss,
}: {
  sym: string
  ccy: string
  sugg: SuggestionView[] | null
  onSuggest: () => void
  onAdd: (s: SuggestionView) => void
  onAddAll: () => void
  onDismiss: (index: number) => void
}) {
  if (sugg === null) {
    return (
      <div className="pa-sugg">
        <span className="pa-sugg-ic">
          <Sparkles size={15} strokeWidth={1.7} />
        </span>
        <span className="pa-sugg-b">
          <span className="pa-sugg-t">Not sure what to set?</span>
          <span className="pa-sugg-s">
            Lucius can suggest alert levels for {sym} from your position and target mix.
          </span>
        </span>
        <Button variant="secondary" size="sm" className="pa-sugg-btn" onClick={onSuggest}>
          Suggest
        </Button>
      </div>
    )
  }
  if (sugg.length === 0) return null
  return (
    <div className="pa-sg">
      <div className="pa-sg-head">
        <span className="pa-sugg-ic">
          <Sparkles size={15} strokeWidth={1.7} />
        </span>
        <span className="pa-sg-t">
          Lucius suggests {sugg.length} alert{sugg.length === 1 ? '' : 's'}
        </span>
        <Button variant="secondary" size="sm" onClick={onAddAll}>
          Add all
        </Button>
        <button type="button" className="pa-sg-x" aria-label="Suggest again" onClick={onSuggest}>
          <RefreshCw size={13} strokeWidth={2} />
        </button>
      </div>
      {sugg.map((s, k) => (
        <div className="pa-sg-row" key={k}>
          <span className="pa-sg-lvl">
            <i>{OPSYM[s.op]}</i>
            <Money>
              {ccy}
              {fmt(s.value)}
            </Money>
          </span>
          <span>
            <span className="pa-sg-why">{s.why}</span>
            <span className="pa-sg-note">{s.note}</span>
          </span>
          <span className="pa-sg-acts">
            <Button variant="primary" size="sm" onClick={() => onAdd(s)}>
              Add
            </Button>
            <button type="button" className="pa-sg-skip" onClick={() => onDismiss(k)}>
              Skip
            </button>
          </span>
        </div>
      ))}
    </div>
  )
}

type SavedKey = 'mute' | 'feeds' | null

/** Mute + Additional news sources. Each control commits itself — the per-alert toggles already do, so a
 * Save button covering only mute and feeds would be ambiguous. A feed row commits on blur and
 * an empty one is dropped there, matching the upstream filter. */
export function TickerSettings({
  sym,
  muted,
  feeds,
  feedLaneActive,
  onMute,
  onFeeds,
  onCommitFeeds,
}: {
  sym: string
  muted: boolean
  feeds: string[]
  /** Whether the hot RSS lane that fetches these feeds is running (null = not known yet). */
  feedLaneActive: boolean | null
  onMute: (on: boolean) => void
  onFeeds: (next: string[]) => void
  onCommitFeeds: (next: string[]) => void
}) {
  const [feedsOpen, setFeedsOpen] = useState(false)
  const [saved, setSaved] = useState<SavedKey>(null)
  const savedT = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flash = (key: SavedKey) => {
    setSaved(key)
    if (savedT.current) clearTimeout(savedT.current)
    savedT.current = setTimeout(() => setSaved(null), 2200)
  }
  const commitMute = (on: boolean) => {
    onMute(on)
    flash('mute')
  }
  return (
    <div className="pa-set">
      <div className="pa-setrow">
        <span className="pa-set-b">
          <span className="pa-set-t">Mute {sym}</span>
          <span className={'pa-saved' + (saved === 'mute' ? ' pa-saved--on' : '')}>
            <Check size={11} strokeWidth={2.4} /> Saved
          </span>
        </span>
        <Switch
          checked={muted}
          onChange={(e) => commitMute(e.target.checked)}
          aria-label={`Mute ${sym}`}
        />
      </div>
      <div className="pa-feeds">
        <button
          type="button"
          className="pa-feeds-head"
          aria-expanded={feedsOpen}
          onClick={() => setFeedsOpen((o) => !o)}
        >
          <span className="pa-feeds-t">Additional news sources</span>
          {feeds.length > 0 && (
            <span className="pa-feeds-n">
              {feeds.length} feed{feeds.length === 1 ? '' : 's'}
            </span>
          )}
          <ChevronDown
            size={13}
            strokeWidth={2.2}
            className={'pa-feeds-cv' + (feedsOpen ? ' pa-feeds-cv--open' : '')}
          />
        </button>
        {feedsOpen && (
          <div className="pa-feeds-body">
            <p className="pa-feeds-help">
              Add extra RSS feeds for {sym} beyond the sources Lucius already pulls from, checked
              every {CUSTOM_FEED_POLL_SECONDS} seconds. Up to 5.
              {feedLaneActive === false &&
                ' Paused right now: RSS freshness is off in Monitoring settings, so these feeds are not checked.'}
            </p>
            {feeds.length > 0 && (
              <div className="pa-feed-rows">
                {feeds.map((u, i) => (
                  <div className="pa-feed-row" key={i}>
                    <TextField
                      value={u}
                      placeholder="https://example.com/feed.xml"
                      onChange={(e) => onFeeds(feeds.map((x, j) => (j === i ? e.target.value : x)))}
                      onBlur={() => {
                        const cleaned = feeds.filter((x, j) => (j === i ? x.trim() : true))
                        if (cleaned.length !== feeds.length) onFeeds(cleaned)
                        if (u.trim()) {
                          onCommitFeeds(cleaned)
                          flash('feeds')
                        }
                      }}
                    />
                    <button
                      type="button"
                      className="pa-feed-x"
                      aria-label="Remove this feed"
                      onClick={() => {
                        const next = feeds.filter((_, j) => j !== i)
                        onFeeds(next)
                        onCommitFeeds(next)
                        flash('feeds')
                      }}
                    >
                      <X size={13} strokeWidth={2} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="pa-feeds-foot">
              <Button
                variant="ghost"
                size="sm"
                className="pa-addfeed"
                disabled={feeds.length >= 5}
                onClick={() => onFeeds([...feeds, ''])}
              >
                Add feed
              </Button>
              <span className="pa-feeds-cap">
                {feeds.length >= 5 ? 'Maximum of 5' : `${5 - feeds.length} of 5 left`}
              </span>
              <span
                className={'pa-saved' + (saved === 'feeds' ? ' pa-saved--on' : '')}
                style={{ marginLeft: 'auto' }}
              >
                <Check size={11} strokeWidth={2.4} /> Saved
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export function AlertRow({
  r,
  gap,
  ccy,
  chg,
  onEdit,
  onToggle,
}: {
  r: AlertRecord
  gap: number
  ccy: string
  chg: number | null
  onEdit: (r: AlertRecord) => void
  onToggle: (id: string, on: boolean) => void
}) {
  const routing = useAlertRouting()
  const enabled = r.enabled !== false
  const near = enabled && gap > 0 && gap <= NEAR
  const chip = conditionChip(r)
  const stat =
    r.kind === 'move'
      ? chg == null
        ? ''
        : `${chg >= 0 ? '+' : ''}${chg.toFixed(1)}% today`
      : !Number.isFinite(gap)
        ? ''
        : gap <= 0
          ? 'Past this level now'
          : `${gap.toFixed(1)}% away`
  const after = AFTER_SHORT[afterFireFromCondition(r)]
  const label = `${chip.sym} ${r.kind === 'move' ? '' : ccy}${chip.text}`
  return (
    <div className={'pa-item' + (enabled ? '' : ' pa-item--off')}>
      <button type="button" className="pa-item-main" onClick={() => onEdit(r)}>
        <span className="pa-chip">
          <i>{chip.sym}</i>
          {r.kind === 'move' ? (
            chip.text
          ) : (
            <Money>
              {ccy}
              {chip.text}
            </Money>
          )}
        </span>
        <span className="pa-body">
          <span className="pa-role">{rowTitle(r, ccy)}</span>
          <span className="pa-note">
            {after} · {routeSummary(r.channels ?? [], routing)}
            {r.note && r.note.trim() ? ' · Has instructions' : ''}
          </span>
        </span>
      </button>
      <span className={'pa-stat' + (near ? ' pa-stat--near' : '')}>{stat}</span>
      <Switch
        checked={enabled}
        disabled={!r.id}
        onChange={(e) => r.id && onToggle(r.id, e.target.checked)}
        aria-label={`${enabled ? 'Turn off' : 'Turn on'} the ${label} alert`}
      />
      <button
        type="button"
        className="pa-item-go"
        onClick={() => onEdit(r)}
        aria-label="Edit alert"
      >
        <ChevronRight size={12} strokeWidth={2.2} className="pa-chev" />
      </button>
    </div>
  )
}
