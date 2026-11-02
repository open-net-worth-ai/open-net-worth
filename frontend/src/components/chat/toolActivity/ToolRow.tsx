// One row on the activity rail: a call, or a run of identical adjacent calls.
//
// A real <button>, not a <span onClick> — the row it replaces was neither keyboard-reachable
// nor announced, and an expandable thing that only a mouse can expand is not expandable.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown } from '../../../ui/icons'
import { useFlag } from '../../../hooks/useFlag'
import { fetchToolDetail } from '../../../utils/chatStore'
import type { Cluster, ToolItem } from './cluster'
import { failureOf } from './cluster'
import { ICON_SIZE, MCP_PLACEHOLDER, connectorIcon, iconForVerb } from './icons'
import type { OwnerLook } from './useToolOwnerLogos'
import { RunCodeDetail } from './RunCodeDetail'
import { ToolDetail } from './ToolDetail'

/** What a row wears, most specific first: the connector's brand mark, else the category
 *  glyph its own Connectors card uses (in its own hue), else the verb's glyph. Same box in
 *  every case, so the rail stays a single column — a rail whose items are different widths
 *  stops reading as a rail. */
function RowIcon({
  verb,
  look,
  ownerId,
  ownerName,
}: {
  verb: string
  look: OwnerLook
  ownerId: string
  ownerName: string
}) {
  if (look.logo) {
    return (
      <span className="msg-toolact__icon">
        <img src={look.logo} alt="" width={ICON_SIZE} height={ICON_SIZE} aria-hidden="true" />
        <span className="sr-only">{ownerName}</span>
      </span>
    )
  }

  // An external server with no mark of its own still says it is EXTERNAL, and that outranks
  // the generic connector glyph below: an MCP server resolves the same `cpu` icon, but only
  // this branch carries the provenance the humanized label erased. A failure outranks both —
  // what went wrong matters more than who was asked.
  if (verb !== 'error' && ownerId.startsWith('mcp:')) {
    return (
      <span
        className="msg-toolact__icon msg-toolact__icon--external"
        title="Third-party MCP server"
      >
        {/* The app's OWN generic MCP-server glyph — the one the "Custom MCP server" card
            uses — rather than a second mark meaning the same thing. */}
        <MCP_PLACEHOLDER />
        <span className="sr-only">Third-party MCP server</span>
      </span>
    )
  }
  // A connector with no brand mark is still a connector. `email` and `webhook` are protocols,
  // not brands, and were falling back to a generic verb glyph while the Connectors screen
  // showed them their own icon in their own colour (owner report, 2026-08-23).
  const CategoryGlyph = verb !== 'error' && look.icon ? connectorIcon(look.icon) : null
  if (CategoryGlyph) {
    return (
      <span
        className="msg-toolact__icon msg-toolact__icon--connector"
        style={look.hue ? { color: look.hue } : undefined}
        title={ownerName || undefined}
      >
        <CategoryGlyph />
        {ownerName && <span className="sr-only">{ownerName}</span>}
      </span>
    )
  }

  const Icon = iconForVerb(verb)
  return (
    <span className={`msg-toolact__icon${verb === 'error' ? ' msg-toolact__icon--error' : ''}`}>
      <Icon size={ICON_SIZE} strokeWidth={1.5} aria-hidden="true" />
    </span>
  )
}

function BusyDots() {
  return (
    <span className="msg-search-loading msg-toolact__busy" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  )
}

/** One call's own expandable detail — used for a single-call row and for each member of an
 *  expanded multi-call cluster. */
function CallDetail({ item, sessionId }: { item: ToolItem; sessionId?: string }) {
  const declinedIsNotFailure = useFlag('permissions_v2') === true
  const elided = item.resultElided !== undefined || item.inputElided !== undefined
  const [detail, setDetail] = useState<{
    input?: Record<string, unknown>
    result?: string
  } | null>(null)
  const [failedToLoad, setFailedToLoad] = useState(false)
  const busy = item.status === 'streaming' || item.status === 'executing'

  // Guards the auto-fetch against re-firing. Without it the fetch was scheduled from RENDER,
  // so every re-render before the response landed queued another request for the same
  // tool_use_id — a side effect in render, and a duplicate-request bug with it.
  const requested = useRef(false)
  const alive = useRef(true)
  useEffect(() => {
    // Set on the way IN as well as out. Cleanup-only left this permanently false under
    // React 18 StrictMode, which mounts, cleans up, then mounts again — so every response
    // was discarded as "component is gone" and the row spun "Loading full result…" forever
    // (owner report, 2026-08-23). The guard is worth keeping: a real unmount mid-fetch would
    // otherwise setState on a dead component.
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Without a session there is nothing to fetch against: `sessionId` is derived from the
  // message id (`MessageBubble.transcriptSessionId`) and is absent for a message whose id
  // does not carry one. That used to leave the row spinning "Loading full result…" forever,
  // because the fetch was skipped and nothing ever cleared the waiting state (owner report,
  // 2026-08-23). A row that cannot load says so instead.
  const canFetch = Boolean(sessionId)

  const load = useCallback(() => {
    if (!sessionId) return
    requested.current = true
    setFailedToLoad(false)
    setDetail(null)
    void fetchToolDetail(sessionId, item.id).then((d) => {
      if (!alive.current) return
      if (d) setDetail({ input: d.input, result: d.result })
      else setFailedToLoad(true)
    })
  }, [sessionId, item.id])

  // Oversized results/inputs are stripped from the transcript LIST payload and fetched the
  // first time this detail is actually rendered.
  useEffect(() => {
    if (elided && !requested.current && canFetch) load()
  }, [elided, canFetch, load])

  const waitingForFetch = elided && detail === null && !failedToLoad && canFetch
  const unavailable = elided && detail === null && !canFetch
  const input = item.input ?? detail?.input
  const result = item.result ?? detail?.result

  // run_code keeps its own view: the generic Request box renders the model-authored script
  // as an escaped JSON one-liner, unreadable exactly when you want to read it.
  if (item.name === 'run_code' && !busy && !waitingForFetch && !failedToLoad && !unavailable) {
    return <RunCodeDetail input={input} result={result} />
  }

  return (
    <ToolDetail
      input={input}
      result={result}
      loading={busy || waitingForFetch}
      loadingLabel={busy ? 'Waiting for result…' : 'Loading full result…'}
      failedToLoad={failedToLoad || unavailable}
      onRetry={canFetch ? load : undefined}
      resultFailed={failureOf({ ...item, result }, declinedIsNotFailure).failed}
    />
  )
}

export function ToolRow({
  cluster,
  sessionId,
  look,
}: {
  cluster: Cluster
  sessionId?: string
  look: OwnerLook
}) {
  const [open, setOpen] = useState(false)
  // One call per row since 2026-08-23 — no nested member list to fall back to.
  const call = cluster.items[0]
  // Nothing to show yet on a call still streaming its arguments — an empty panel that
  // appears and then fills in reads as a glitch, so the row simply isn't expandable yet.
  const expandable =
    !cluster.busy || cluster.items.some((i) => i.input !== undefined || i.result !== undefined)

  return (
    <div className={`msg-toolact__row${open ? ' msg-toolact__row--open' : ''}`}>
      <button
        type="button"
        className="msg-toolact__head"
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        onClick={() => expandable && setOpen((o) => !o)}
      >
        <RowIcon
          verb={cluster.verb}
          look={look}
          ownerId={cluster.ownerId}
          ownerName={cluster.ownerName}
        />
        <span className="msg-toolact__label">
          {cluster.ownerName && <span className="msg-toolact__owner">{cluster.ownerName}</span>}
          <span className="msg-toolact__text">{cluster.label}</span>
          {cluster.detail && <span className="msg-toolact__detail-inline">{cluster.detail}</span>}
        </span>
        {cluster.busy && <BusyDots />}
        {expandable && (
          <ChevronDown size={12} strokeWidth={1.5} className="msg-toolact__caret" aria-hidden />
        )}
      </button>

      {/* A failure is stated on the row itself. Burying "account not found" one click down,
          under a row that looks like every successful one, is how a failed turn reads as a
          successful one. */}
      {cluster.failed && cluster.errorText && (
        <div className="msg-toolact__error">{cluster.errorText}</div>
      )}

      {open && call && <CallDetail item={call} sessionId={sessionId} />}
    </div>
  )
}
