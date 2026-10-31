// A group of tool calls: what happened overall, then what happened step by step.
//
// This is the ONE renderer for tool activity. Every chat surface reaches it through
// `MessageBubble` — the main tab, the side panel, the floating dock, and an automation run
// (which opens its `session_id` in the same conversation view) — so consistency is a
// property of there being one of these, not of four of them agreeing.
//
// Layout is width-driven, never surface-driven: the dock caps at 560px and the rail is built
// to read there.

import { useMemo, useState } from 'react'
import { ChevronDown } from '../../../ui/icons'
import { useFlag } from '../../../hooks/useFlag'
import {
  clusterItems,
  isGateBounce,
  summaryParts,
  type ActionItem,
  type Cluster,
  type ThinkingItem,
  type ToolItem,
} from './cluster'
import { ThinkingRow } from './ThinkingRow'
import { ToolRow } from './ToolRow'
import { useToolOwnerLogos } from './useToolOwnerLogos'

/** A plain page fetch — no tool call behind it, so it has no request/result to expand. */
function FetchRow({ url }: { url: string }) {
  let host = url
  try {
    host = new URL(url).hostname
  } catch {
    /* a non-absolute URL is shown as-is */
  }
  return (
    <div className="msg-toolact__row">
      <div className="msg-toolact__head msg-toolact__head--static">
        <span className="msg-toolact__icon">↗</span>
        <span className="msg-toolact__label">
          <span className="msg-toolact__text">Opened {host}</span>
        </span>
      </div>
    </div>
  )
}

export function ToolActivity({ items, sessionId }: { items: ActionItem[]; sessionId?: string }) {
  const declinedIsNotFailure = useFlag('permissions_v2') === true
  // Segment first, cluster within a segment. Clustering the tool items as one list and
  // rendering fetches separately would print every fetch above every tool call, silently
  // reordering the timeline — the one thing the grouping rules promise never to do.
  const rows = useMemo(() => {
    const out: Array<
      | { kind: 'cluster'; cluster: Cluster }
      | { kind: 'fetch'; url: string }
      | { kind: 'thinking'; item: ThinkingItem }
    > = []
    let run: ToolItem[] = []
    const flush = () => {
      if (run.length === 0) return
      for (const cluster of clusterItems(run, declinedIsNotFailure))
        out.push({ kind: 'cluster', cluster })
      run = []
    }
    for (const item of items) {
      if (item.type === 'tool') {
        // Dropped, not shown: nothing the user asked for actually failed here, it is the
        // discovery gate's own retry-me-once plumbing. The real call that follows (success,
        // or its own permission card) is the one thing worth putting on screen.
        if (item.status === 'done' && isGateBounce(item.result)) continue
        run.push(item)
      } else if (item.type === 'thinking') {
        flush()
        out.push({ kind: 'thinking', item })
      } else {
        flush()
        out.push({ kind: 'fetch', url: item.url })
      }
    }
    flush()
    return out
  }, [items, declinedIsNotFailure])

  const clusters = useMemo(
    () => rows.flatMap((r) => (r.kind === 'cluster' ? [r.cluster] : [])),
    [rows],
  )
  const { phrase, failed } = useMemo(() => summaryParts(clusters), [clusters])
  // The bar is the fold control as well as the summary, so it appears whenever there is
  // more than one thing to fold.
  // CALLS, not rows: a cluster of two renders as one row, and counting rows made a bar
  // reading "1 step" sit above a row reading "Update 2 run tasks" (owner report,
  // 2026-08-23). Thinking is not a step — it is what happened between them.
  const steps = rows.reduce(
    (n, r) => n + (r.kind === 'cluster' ? r.cluster.items.length : r.kind === 'fetch' ? 1 : 0),
    0,
  )
  // Every call is a step, a failed one included — it ran, it just did not work. A count that
  // silently dropped failures would make the bar disagree with the rows underneath it.
  const stepLabel = steps === 1 ? '1 step' : `${steps} steps`
  // The trailing tallies, in reading order: how much happened, then how much of it went
  // wrong — the failure count qualifies the step count, so it follows it. When there is no
  // phrase the step count IS the sentence, so it does not repeat here.
  const meta = [phrase ? stepLabel : '', failed].filter(Boolean).join(' • ')
  // More than one ROW, not more than one call. A lone row is its own control and says its
  // own sentence, so a bar above it would just stack a second line on one row (owner report,
  // 2026-08-23). A lone row that FAILED still gets one, because "1 failed" is information
  // the row's own label does not carry.
  const showBar = steps > 0 && (rows.length > 1 || Boolean(failed))
  const ownerIds = useMemo(() => clusters.map((c) => c.ownerId), [clusters])
  const lookFor = useToolOwnerLogos(ownerIds)

  // Open while it is HAPPENING, folded once the turn is done — unless someone says otherwise.
  //
  // An 18-step automation pushed its own reply off the screen: the steps are the working, not
  // the answer, so history belongs out of the way. But live activity is the opposite — a turn
  // that runs for a minute behind a single unchanging line reads as a hang, and reasoning that
  // now rides this rail must still open itself while it is being written, which is the whole
  // point of the streaming behaviour in `ThinkingRow`.
  //
  // `null` means "nobody has decided", so a click at any point wins permanently.
  const busy =
    clusters.some((c) => c.busy) ||
    rows.some((r) => r.kind === 'thinking' && Boolean(r.item.streaming))
  const [choice, setChoice] = useState<boolean | null>(null)
  const collapsed = choice ?? !busy

  if (items.length === 0) return null

  return (
    <div className="msg-toolact">
      {/* Derived from what actually ran — verb, noun and real counts — never narrated by the
          model. `TurnActivity` makes the same argument for the working indicator: a
          plausible-sounding step name is a claim the reader cannot check. */}
      {showBar && (
        <button
          type="button"
          className="msg-toolact__summary msg-toolact__summary--toggle"
          aria-expanded={!collapsed}
          aria-live="polite"
          onClick={() => setChoice(!collapsed)}
        >
          {/* The SENTENCE leads; the tallies follow in the quiet trailing style. The failure
              count used to ride inside the sentence ("Get messages · 1 failed"), which drew
              it at full weight beside the label and made a routine count look like an alert
              (owner, 2026-08-25). It is a count, and it is styled as one.
              `phrase` is always non-empty when there are steps; `stepLabel` is the fallback,
              because a bar whose only text is "1 step" explains nothing about what ran. */}
          <span>{phrase || stepLabel}</span>
          {meta && <span className="msg-toolact__count">{meta}</span>}
          <ChevronDown
            size={12}
            strokeWidth={1.5}
            className={`msg-toolact__caret${collapsed ? '' : ' msg-toolact__caret--up'}`}
            aria-hidden
          />
        </button>
      )}
      {/* Only foldable when there is a bar to unfold it with. A lone row has no bar, so
          honouring `collapsed` there would hide it behind a control that does not exist. */}
      <div className="msg-toolact__rail" hidden={showBar && collapsed}>
        {rows.map((row, i) =>
          row.kind === 'fetch' ? (
            <FetchRow key={`f${i}`} url={row.url} />
          ) : row.kind === 'thinking' ? (
            <ThinkingRow
              key={`think-${i}`}
              text={row.item.content}
              streaming={row.item.streaming}
            />
          ) : (
            <ToolRow
              key={row.cluster.key}
              cluster={row.cluster}
              sessionId={sessionId}
              look={lookFor(row.cluster.ownerId)}
            />
          ),
        )}
      </div>
    </div>
  )
}
