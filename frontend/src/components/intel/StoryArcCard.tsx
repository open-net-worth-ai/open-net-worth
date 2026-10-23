import { useState } from 'react'
import { authFetch } from '../../api/client'
import {
  Archive,
  ArrowRight,
  Check,
  ChevronRight,
  Link2,
  RotateCcw,
  X,
  ArrowUpRight,
} from '../../ui/icons'
import { fDateTime, safeHref, timeAgo } from '../../utils/format'
import { AssetLink } from './AssetLink'
import { ExplainedBlock, ReadBlock } from './WatchWriteups'
import { SOURCE_KIND, aggregateSources, signalMeta, storyHeadline } from './storyMeta'
import type { StoryArc, SourceKind, TimelineEvent } from './storyMeta'
import { QUEUE_META, classifySignal, isFreshSignal } from './signalQueue'

// Re-export the shared shapes so existing importers keep working.
export type { StoryArc, TimelineEvent, SignalSource, AffectedHolding } from './storyMeta'

export interface TickerMeta {
  price?: number
  name?: string
  exchange?: string
}

// ── Story card ─────────────────────────────────────────────────────────────────

interface Props {
  story: StoryArc
  active?: boolean
  updatedLabel?: string
  defaultExpanded?: boolean
  // Lifecycle — read / dismissed (soft-archive) / resolved (auto, trigger cleared).
  read?: boolean
  dismissed?: boolean
  resolved?: boolean
  resolvedReason?: string
  onRead?: (id: string) => void
  onDismiss?: (id: string) => void
  onRestore?: (id: string) => void
  onDelete?: (id: string) => void
  onAskLucius: (text: string) => void
  registerRef?: (id: string, el: HTMLElement | null) => void
}

export function StoryArcCard({
  story,
  active = false,
  updatedLabel,
  defaultExpanded,
  read = false,
  dismissed = false,
  resolved = false,
  resolvedReason,
  onRead,
  onDismiss,
  onRestore,
  onDelete,
  onAskLucius,
  registerRef,
}: Props) {
  const [open, setOpen] = useState(defaultExpanded ?? story.status !== 'monitoring')
  const meta = signalMeta(story.type)
  const st = QUEUE_META[classifySignal(story)]
  const historical = Number.isFinite(Date.parse(story.updated_at)) && !isFreshSignal(story)
  const sources = aggregateSources(story)
  const headline = storyHeadline(story)
  const archived = dismissed || resolved

  // F11 fix (owner-reported, 2026-08-29): the feed's own `story.timeline` is bounded to the
  // most recent `showing_timeline` of `total_timeline` (see backend/services/intel_signals.py)
  // — storage itself is unbounded now, but this card only fetches the rest on demand, once
  // expanded, never up front. `fullTimeline` (once populated) fully replaces the bounded array
  // for the remainder of this card's life.
  const [fullTimeline, setFullTimeline] = useState<TimelineEvent[] | null>(null)
  const [loadingFullTimeline, setLoadingFullTimeline] = useState(false)
  const timeline = fullTimeline ?? story.timeline
  const hasMoreTimeline =
    !fullTimeline && story.total_timeline != null && story.total_timeline > story.timeline.length
  const loadFullTimeline = async () => {
    if (loadingFullTimeline) return
    setLoadingFullTimeline(true)
    try {
      const res = await authFetch(`/api/intel/signals/${story.id}`)
      if (res.ok) {
        const full: StoryArc = await res.json()
        setFullTimeline(full.timeline)
      }
    } catch {
      // best-effort — the bounded (recent) timeline stays displayed on failure
    } finally {
      setLoadingFullTimeline(false)
    }
  }

  // Explicit engagement marks the signal read.
  const askLucius = () => {
    onRead?.(story.id)
    onAskLucius(
      `Tell me more about ${story.ticker || story.label}: ${headline}. Verify the latest evidence and prices before recommending an action.`,
    )
  }

  const modifier = active
    ? ' intel-story--active'
    : archived
      ? ' intel-story--archived'
      : read
        ? ' intel-story--read'
        : ''

  return (
    <article ref={(el) => registerRef?.(story.id, el)} className={`intel-story${modifier}`}>
      <div className="intel-story-inner">
        {/* head: unread dot + type pill + ticker + status */}
        <div className="intel-story-head">
          {!read && !archived && (
            <span className="intel-story-unread" title="Unread" aria-label="Unread" />
          )}
          <span
            className="intel-story-pill"
            style={{
              color: meta.color,
              background: `color-mix(in srgb, ${meta.color} 13%, transparent)`,
            }}
          >
            <span className="intel-story-pill-dot" style={{ background: meta.color }} aria-hidden />
            {meta.label}
          </span>
          {story.ticker ? (
            <AssetLink ticker={story.ticker} label={story.label} className="intel-story-ticker">
              {story.ticker}
            </AssetLink>
          ) : (
            <AssetLink label={story.label} className="intel-story-label">
              {story.label}
            </AssetLink>
          )}
          {resolved ? (
            <span className="intel-story-status intel-story-status--resolved">
              <Check size={12} />
              Resolved
            </span>
          ) : (
            <span className="intel-story-status" style={{ color: st.color }}>
              <span
                className="intel-story-status-dot"
                style={{ background: st.color }}
                aria-hidden
              />
              {st.label}
            </span>
          )}
        </div>

        {resolved && resolvedReason && (
          <div className="intel-story-resolved-banner">
            <Check size={13} className="intel-story-resolved-icon" />
            <span className="intel-story-resolved-text">
              <b>Auto-resolved.</b> {resolvedReason}
            </span>
          </div>
        )}

        <div className="intel-story-headline">{headline}</div>
        {updatedLabel && (
          <div className={`intel-story-asof${historical ? ' intel-story-asof--historical' : ''}`}>
            {historical ? 'Historical analysis' : 'Analysis checked'} · {updatedLabel}
            {historical && ' · verify before acting'}
          </div>
        )}
        {story.explained ? (
          <ExplainedBlock ex={story.explained} />
        ) : (
          <>
            <p className="intel-story-summary">{story.summary}</p>
            {story.lucius_read && <ReadBlock read={story.lucius_read} />}
          </>
        )}

        {/* what it means for you — the row above already carries the name's own figures. An
            older analysis's impact is labelled as such, since the position has moved since. */}
        {story.portfolio_impact && (
          <p className="intel-story-impact">
            <span className="intel-story-impact-label">
              {historical ? 'For you · earlier scan' : 'For you'}
            </span>
            {story.portfolio_impact}
          </p>
        )}

        {/* actions */}
        {story.action_items.length > 0 && (
          <ul className="intel-story-actions">
            {story.action_items.map((a, i) => (
              <li key={i} className="intel-story-action">
                <ArrowRight
                  size={12}
                  className="intel-story-action-mark"
                  style={{ color: meta.color }}
                />
                <span>{a}</span>
              </li>
            ))}
          </ul>
        )}

        {/* sources — provenance, always visible */}
        {sources.length > 0 && (
          <div className="intel-story-sources">
            <span className="intel-story-sources-label">Sources</span>
            <div className="intel-story-sources-list">
              {sources.map((src, i) => {
                const href = safeHref(src.url)
                return (
                  <a
                    key={i}
                    className="intel-story-source"
                    href={href || '#'}
                    target={href ? '_blank' : undefined}
                    rel="noreferrer"
                    onClick={(e) => {
                      if (!href) e.preventDefault()
                    }}
                    title={src.detail}
                  >
                    <span
                      className="intel-story-source-dot"
                      style={{
                        background: (SOURCE_KIND[src.kind as SourceKind] ?? SOURCE_KIND.news).color,
                      }}
                      aria-hidden
                    />
                    {src.name}
                    <span className="intel-story-source-arrow">
                      <ArrowUpRight size={12} aria-hidden="true" />
                    </span>
                  </a>
                )
              })}
            </div>
          </div>
        )}

        {/* footer */}
        <div className="intel-story-foot">
          <button type="button" className="intel-story-ask" onClick={askLucius}>
            Ask Lucius <span className="intel-story-ask-spark">✦</span>
          </button>
          <div className="intel-story-foot-actions">
            {archived ? (
              <>
                <button
                  type="button"
                  className="intel-story-life"
                  onClick={() => onRestore?.(story.id)}
                  title={resolved ? 'Reopen this signal' : 'Restore to feed'}
                >
                  <RotateCcw size={12} />
                  {resolved ? 'Reopen' : 'Restore'}
                </button>
                <button
                  type="button"
                  className="intel-story-life intel-story-life--delete"
                  onClick={() => onDelete?.(story.id)}
                  title="Delete permanently"
                >
                  <X size={12} />
                  Delete
                </button>
              </>
            ) : (
              <>
                {!read && (
                  <button
                    type="button"
                    className="intel-story-life"
                    onClick={() => onRead?.(story.id)}
                    title="Mark as read"
                  >
                    <Check size={12} />
                    Read
                  </button>
                )}
                <button
                  type="button"
                  className="intel-story-life"
                  onClick={() => onDismiss?.(story.id)}
                  title="Dismiss this signal"
                >
                  <Archive size={12} />
                  Dismiss
                </button>
              </>
            )}
            {timeline.length > 0 && (
              <button
                type="button"
                className="intel-story-toggle"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
              >
                {open ? 'Hide' : 'Timeline'}
                <ChevronRight
                  size={11}
                  className={`intel-story-toggle-chevron${open ? ' intel-story-toggle-chevron--open' : ''}`}
                />
              </button>
            )}
            {updatedLabel && <span className="intel-story-updated">{updatedLabel}</span>}
          </div>
        </div>

        {/* expanded timeline */}
        {open && timeline.length > 0 && (
          <div className="intel-story-timeline">
            {timeline.map((ev, i) => {
              const flagged = ev.status === 'flagged'
              const superseded = ev.superseded === true
              return (
                <div
                  key={ev.id}
                  className={`intel-story-tl-event${superseded ? ' intel-story-tl-event--superseded' : ''}`}
                >
                  <span
                    className="intel-story-tl-dot"
                    style={{
                      background: superseded ? 'var(--fg-3)' : i === 0 ? st.color : 'var(--fg-3)',
                    }}
                    aria-hidden
                  />
                  <div className="intel-story-tl-body">
                    <div className="intel-story-tl-time" title={fDateTime(ev.date)}>
                      {timeAgo(ev.date) || ev.date}
                    </div>
                    {(flagged || superseded) && (
                      <div className="intel-story-tl-flags">
                        {flagged && (
                          <span
                            className="intel-story-pill intel-story-tl-flag"
                            style={{
                              color: 'var(--warn)',
                              background: 'color-mix(in srgb, var(--warn) 13%, transparent)',
                            }}
                            title="Awaiting a second independent source before this is treated as confirmed"
                          >
                            Unconfirmed
                          </span>
                        )}
                        {superseded && (
                          <span
                            className="intel-story-pill intel-story-tl-flag"
                            style={{ color: 'var(--faint)', background: 'var(--surface2)' }}
                            title="A later, corroborated update replaced this entry"
                          >
                            Superseded
                          </span>
                        )}
                      </div>
                    )}
                    {ev.title && ev.summary && (
                      <div className="intel-story-tl-title">{ev.title}</div>
                    )}
                    <div className="intel-story-tl-text">{ev.summary || ev.title}</div>
                    {ev.sources?.map((s, j) => {
                      const href = safeHref(s.url)
                      return href ? (
                        <a
                          key={j}
                          className="intel-story-tl-src"
                          href={href}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Link2 size={9} />
                          {s.domain || s.title}
                        </a>
                      ) : null
                    })}
                  </div>
                </div>
              )
            })}
            {hasMoreTimeline && (
              <button
                type="button"
                className="intel-story-tl-more"
                onClick={loadFullTimeline}
                disabled={loadingFullTimeline}
              >
                {loadingFullTimeline
                  ? 'Loading…'
                  : `Show ${(story.total_timeline ?? 0) - story.timeline.length} earlier`}
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
