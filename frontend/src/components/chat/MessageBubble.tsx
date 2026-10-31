import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import ReactMarkdown from 'react-markdown'
import { SAFE_MARKDOWN_COMPONENTS } from '../ui/safeMarkdown'
import { ChatLink } from './ChatLink'
import remarkGfm from 'remark-gfm'
import type {
  Attachment,
  CheckpointFileChange,
  CrossSessionChange,
  Message,
  MessageBlock,
  ProposedChange,
  SearchCall,
  TurnUsage,
} from '../../types/chat'
import { useChatCostVisible, useChatThinkingGrouped } from '../../hooks/useChatSuggestionsEnabled'
import { Button, Modal } from '../ui'
import { ArtifactCard } from './ArtifactCard'
import { WriteReceiptChip } from './WriteReceiptChip'
import { useAnchoredPopover } from './anchoredPopover'
import { ScreenCommandChip } from './ScreenCommandChip'
import { CodeBlock } from './CodeBlock'
import { ChangePreviewSwitch } from './ChangePreviewSwitch'
import { DuplicateReview } from './DuplicateReview'
import {
  Search,
  Check,
  Clock,
  Copy,
  FileText,
  RotateCcw,
  ChevronUp,
  ChevronDown,
} from '../../ui/icons'
import { makeMentionTokenRE } from '../../types/mentions'
import type { MentionRef, MentionType } from '../../types/mentions'
import { CitationChips } from './lucius/CitationChips'
import { stripFence, stripScreenContext, stripConfirmHandshake } from '../../utils/untrusted'
import { Chip } from './lucius/Chip'
import { ToolActivity } from './toolActivity/ToolActivity'
import { failureOf } from './toolActivity/cluster'
import type { ActionItem, ThinkingItem } from './toolActivity/cluster'
import { ThinkingBlock } from './toolActivity/ThinkingRow'
import { MentionDetail } from './lucius/MentionDetail'
import type { MentionDetailData } from './lucius/MentionDetail'
import { byId } from '../../lucius/objectFields'
import { resolveChipMeta } from '../../lucius/chipMeta'
import { isSafeCssColor } from '../../utils/mentionable'
import { fTime } from '../../utils/format'
import { repairStrayFences } from '../../utils/strayFences'

/** Hostname for display/favicon. `url` comes from a search provider, so a malformed value
 *  is possible — an unguarded `new URL()` throws during render and blanks the whole
 *  message rather than one result row. */
function safeHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

// Humanize a mention type for the read-only chip label when no carried label is present.
// Used only as a stale-detection sentinel: if the type is unknown and no label was carried,
// the chip shows as stale ("(deleted)").
function mentionTypeLabel(type: string): string {
  const map: Record<string, string> = {
    net_worth: 'Net worth',
    holding: 'Holding',
    instrument: 'Instrument',
    account: 'Account',
    transaction: 'Transaction',
    category: 'Category',
    budget_month: 'Budget',
    target: 'Target',
    allocation_slice: 'Allocation',
    returns: 'Returns',
    payee: 'Payee',
    watchlist: 'Watchlist',
    rule: 'Rule',
    scheduled_task: 'Task',
    automation: 'Automation',
    agent: 'Agent',
    skill: 'Skill',
  }
  return map[type] ?? type
}

// The per-provider web-search label table and READING_TOOL_NAMES lived here to drive an
// animated "reading…" pill for search/fetch tools. Both are GONE (2026-08-22): labels are
// resolved server-side (`ai/tools/display.py`) and every in-flight row now shows the same
// busy indicator, so a search no longer had its own special-case appearance to maintain.

// Render a sent user message with entity-ref tokens (`[type: ref]`) shown as read-only
// Chip components (identical to the composer chips, no × button). Clicking a chip opens
// the MentionDetail popover; clicking the same chip again closes it.
function MentionText({ text }: { text: string }) {
  const [detail, setDetail] = useState<MentionDetailData | null>(null)

  // Use a fresh regex instance per render — the /g flag is stateful (lastIndex) and a shared
  // module-level instance is not safe under React 18 concurrent rendering.
  const re = makeMentionTokenRE()
  const nodes: React.ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  let key = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const type = m[1] ?? ''
    const ref = (m[2] ?? '').trim()
    const carriedLabel = (m[3] ?? '').trim()
    const display = carriedLabel || mentionTypeLabel(type)
    // Stale ONLY for a truly unparseable token: unknown type AND no carried label.
    const stale = !carriedLabel && mentionTypeLabel(type) === type

    // Resolve the entity from the live DOM (same path MentionDetail uses). When the entity
    // is on screen its data-ctx-fields carry date/amount/color that we can use to render the
    // output chip identically to the composer input chip. byId returns null when the entity
    // isn't currently in the DOM — that's fine, we fall back to label-only gracefully.
    const record = byId(type, ref)
    let chipDate: string | undefined
    let chipAmt: string | undefined
    let chipAmtNeg: boolean | undefined
    let color: string | undefined
    // The on-screen element carries data-entity-sublabel (the transaction's NATIVE
    // amount, the figure shown on screen) and data-entity-color (the entity's own
    // display color — account tint, envelope/category dot) — neither is in the parsed
    // data-ctx-fields record, so read them off the element.
    const el =
      typeof document !== 'undefined'
        ? document.querySelector(`[data-entity-id="${type}:${ref}"]`)
        : null
    const sublabel = el?.getAttribute('data-entity-sublabel') ?? null
    if (record || sublabel) {
      const meta = resolveChipMeta(type, record, sublabel)
      chipDate = meta.chipDate
      chipAmt = meta.chipAmt
      chipAmtNeg = meta.chipAmtNeg
    }
    const rawColor = el?.getAttribute('data-entity-color') ?? ''
    if (rawColor && isSafeCssColor(rawColor)) color = rawColor

    const mention: MentionRef = {
      type: type as MentionType,
      ref,
      label: stale ? '(deleted)' : display,
      ...(chipDate !== undefined && { chipDate }),
      ...(chipAmt !== undefined && { chipAmt }),
      ...(chipAmtNeg !== undefined && { chipAmtNeg }),
      ...(color !== undefined && { color }),
    }

    function handleChipClick(clicked: MentionRef, anchor: HTMLElement) {
      const rect = anchor.getBoundingClientRect()
      setDetail((prev) => {
        // Toggle: clicking the same chip again closes the popover.
        if (prev && prev.type === clicked.type && prev.id === clicked.ref) return null
        return {
          type: clicked.type,
          label: clicked.label,
          id: clicked.ref,
          rect,
        }
      })
    }

    nodes.push(
      <Chip key={`chip-${key++}`} mention={mention} stale={stale} onClick={handleChipClick} />,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return (
    <>
      {nodes}
      <MentionDetail detail={detail} onClose={() => setDetail(null)} />
    </>
  )
}

type RestoreMode = 'data' | 'conversation' | 'both'

type PreviewResult =
  | { changes: CheckpointFileChange[]; cross_session_changes?: CrossSessionChange[] }
  | { gone: true }

interface Props {
  message: Message
  onSendMessage: (text: string) => void
  // Resolve a paused (awaiting-approval) tool_use — RFC 2026-07-22. Present on every real
  // chat surface; optional only so a read-only render (Inbox / agent findings, which use
  // ProposedChangeDiff instead) never needs to supply one.
  onResolvePendingToolUse?: (
    toolUseId: string,
    decision: 'approve' | 'deny',
    options?: { always?: boolean },
  ) => void
  onRespondToChange?: (
    messageId: string,
    changeId: string | undefined,
    status: 'confirmed' | 'dismissed',
  ) => void
  onRetry?: () => void
  onOpenAiSettings?: () => void
  onPreviewCheckpoint?: (checkpointId: string) => Promise<PreviewResult>
  onRestoreCheckpoint?: (checkpointId: string, mode?: RestoreMode) => Promise<unknown>
  /** This message's turn changed the book's data: its Restore is where the one-time guide to
   *  undoing a change may point. */
  restoreGuide?: boolean
}

function SearchCallItem({ s }: { s: SearchCall }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="msg-search-item">
      <button className="msg-search-header" onClick={() => !s.loading && setOpen((o) => !o)}>
        <span className="msg-search-icon">
          {s.loading ? (
            <span className="msg-search-loading">
              <span />
              <span />
              <span />
            </span>
          ) : (
            <Search size={11} strokeWidth={1.4} aria-hidden="true" />
          )}
        </span>
        <span className="msg-search-query">{s.query}</span>
        {!s.loading && s.results.length > 0 && (
          <span className="msg-search-count">
            {s.results.length} sources{' '}
            {open ? (
              <ChevronUp size={11} aria-hidden="true" />
            ) : (
              <ChevronDown size={11} aria-hidden="true" />
            )}
          </span>
        )}
      </button>
      {open && s.results.length > 0 && (
        <div className="msg-search-results">
          {s.results.map((r, i) => (
            <a
              key={i}
              className="msg-search-result"
              href={r.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span className="msg-search-result-favicon">
                <img
                  src={`https://www.google.com/s2/favicons?domain=${safeHost(r.url)}&sz=16`}
                  alt=""
                  width={12}
                  height={12}
                />
              </span>
              <span className="msg-search-result-title">{stripFence(r.title) || r.url}</span>
              <span className="msg-search-result-host">{safeHost(r.url)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

type GroupedBlock = MessageBlock | { type: 'actions'; items: ActionItem[] }
type GroupedBlockWithChange = GroupedBlock | { type: 'change'; change: ProposedChange }

function actionItemFor(block: MessageBlock): ActionItem | null {
  if (block.type === 'fetch') return { type: 'fetch', url: block.url }
  if (block.type === 'thinking') {
    // A redacted thought carries no readable text; an empty row is not a thought.
    return block.content ? { type: 'thinking', content: block.content } : null
  }
  if (block.type !== 'tool') return null
  return {
    type: 'tool',
    name: block.name,
    id: block.id,
    status: block.status,
    // Server-resolved label/owner/grouping. Dropping it here silently sent every row down
    // the typeset-the-raw-name fallback, which is exactly what a missing backend looks like
    // — an easy defect to mistake for a deployment problem.
    ...(block.display ? { display: block.display } : {}),
    input: block.input,
    result: block.result,
    resultElided: block.resultElided,
    inputElided: block.inputElided,
  }
}

/**
 * Runs of activity become ONE `actions` group — and a thought does not break the run.
 *
 * A turn that thinks, calls a tool, then thinks again is one run of activity interrupted by
 * its own reasoning. Breaking the group at each thought produced a second group with a single
 * row, which by its own rules gets no summary bar — so it had no step count and no way to
 * fold, and collapsing the FIRST group left the thought and a stray row still on screen while
 * the bar above claimed "4 steps" for a turn that made five calls (owner report, 2026-08-25).
 *
 * `text` still breaks it: that is the answer, not the working.
 */
function groupBlocks(
  blocks: MessageBlock[],
  opts: { groupThinking: boolean; streaming: boolean } = { groupThinking: false, streaming: false },
): GroupedBlockWithChange[] {
  const acc: GroupedBlockWithChange[] = []
  const openGroup = () => {
    const last = acc[acc.length - 1]
    return last?.type === 'actions' ? (last as { type: 'actions'; items: ActionItem[] }) : null
  }

  for (const block of blocks) {
    if (block.type === 'thinking') {
      // Suppressed entirely when the reader has asked for one merged block (Settings → AI →
      // Chat) — the bubble draws that above the turn instead.
      if (opts.groupThinking) continue
      const item = actionItemFor(block)
      if (!item) continue
      const group = openGroup()
      if (group) group.items.push(item)
      // No run to join yet. It opens one anyway: a thought is drawn on the rail either way,
      // and a leading thought belongs to the calls it is reasoning towards.
      else acc.push({ type: 'actions', items: [item] })
      continue
    }
    if (block.type === 'tool' || block.type === 'fetch') {
      const item = actionItemFor(block)
      if (!item) continue
      const group = openGroup()
      if (group) group.items.push(item)
      else acc.push({ type: 'actions', items: [item] })
      continue
    }
    acc.push(block)
  }

  // Only the LAST thought of a live turn is still being written; the earlier ones are
  // finished and fold, or every thought in the turn would spring open at once.
  if (opts.streaming) {
    for (let g = acc.length - 1; g >= 0; g -= 1) {
      const entry = acc[g]
      if (entry?.type !== 'actions') continue
      const items = (entry as { type: 'actions'; items: ActionItem[] }).items
      const last = [...items].reverse().find((it): it is ThinkingItem => it.type === 'thinking')
      if (last) {
        last.streaming = true
        break
      }
    }
  }
  return acc
}

function ActionRow({ items, sessionId }: { items: ActionItem[]; sessionId?: string }) {
  return <ToolActivity items={items} sessionId={sessionId} />
}

/**
 * The card the reader should be answering RIGHT NOW — the first one in this message that has
 * no verdict yet.
 *
 * A single provider round can gate several tool calls, and every one of them stages its own
 * card. Rendering all of them at once put a stack of decisions on screen and then moved
 * nothing until the last was tapped, which reads as a hang (the turn genuinely cannot resume
 * until every sibling tool_use has a result — see assistant.resolve_pending_tool_use). One at
 * a time is the same set of decisions, asked in an order the reader can follow.
 *
 * Purely a rendering choice: every card already exists in the message, so hiding one loses
 * nothing and un-hiding is free. That is why this lives here and not in the backend — a
 * server-side queue could drop a card, and a dropped card is a turn stuck forever.
 */
function firstUnansweredChangeId(m: Message): string | undefined {
  const fromBlocks = (m.blocks || []).flatMap((b) => (b.type === 'change' ? [b.change] : []))
  const changes = fromBlocks.length > 0 ? fromBlocks : m.proposedChanges || []
  return changes.find((c) => !c.status)?.id
}

/** Each failed call's error, keyed by its tool_use_id — the id its confirm card shares — so an
 *  approved card whose call then failed says so instead of keeping a green "✓ Approved". */
function callFailures(m: Message): Map<string, string> {
  const failures = new Map<string, string>()
  for (const block of m.blocks || []) {
    const item = actionItemFor(block)
    if (item?.type !== 'tool') continue
    const { failed, message } = failureOf(item)
    if (failed) failures.set(item.id, message)
  }
  return failures
}

/** Is this card the one to show? Answered cards stay (they carry the ✓ / ✗ record). */
function changeIsVisible(change: ProposedChange, firstUnanswered: string | undefined): boolean {
  return Boolean(change.status) || change.id === firstUnanswered
}

function renderGroupedBlock(
  block: GroupedBlockWithChange,
  i: number,
  onSendMessage: (text: string) => void,
  onRespond?: (changeId: string | undefined, status: 'confirmed' | 'dismissed') => void,
  onResolvePendingToolUse?: (
    toolUseId: string,
    decision: 'approve' | 'deny',
    options?: { always?: boolean },
  ) => void,
  sessionId?: string,
  firstUnansweredChange?: string,
) {
  if (block.type === 'actions') {
    return (
      <ActionRow
        key={i}
        items={(block as { type: 'actions'; items: ActionItem[] }).items}
        sessionId={sessionId}
      />
    )
  }
  if (block.type === 'text') {
    // Only whitespace and backticks — a stray fence a CLI round wrapped its tool call in —
    // is not a message; ReactMarkdown would draw it as an empty code box.
    if (/^(?:\s|`{3,}[\w+-]*)*$/.test(block.content)) return null
    // A fence that cannot be real code must not swallow the prose after it (see strayFences).
    const content = repairStrayFences(block.content)
    // A rehydrated message arrives as BLOCKS, and this path rendered them through
    // ReactMarkdown only — so mention tokens lost their chip wiring the moment a
    // conversation was reloaded (the chip still drew, but nothing was clickable).
    // MentionText is the one renderer that attaches the popover handler.
    if (makeMentionTokenRE().test(content)) {
      return (
        <div key={i} className="msg-text">
          <MentionText text={content} />
        </div>
      )
    }
    return (
      <div key={i} className="msg-text">
        <ReactMarkdown
          remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
          components={{
            ...SAFE_MARKDOWN_COMPONENTS,
            a: ChatLink,
            pre: CodeBlock,
            table: ({ children }) => (
              <div className="table-wrap">
                <table>{children}</table>
              </div>
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    )
  }
  // A positioned `thinking` block never reaches here: `groupBlocks` folds it into the
  // `actions` run it happened inside, so it draws as a row on that group's rail.
  if (block.type === 'skill') {
    const prefix =
      block.status === 'loading'
        ? 'Loading'
        : block.status === 'loaded'
          ? 'Loaded'
          : 'Could not load'
    return (
      <div
        key={i}
        className={
          block.status === 'loading'
            ? 'msg-skill-activity msg-skill-activity--loading'
            : block.status === 'failed'
              ? 'msg-skill-activity msg-skill-activity--failed'
              : 'msg-skill-activity'
        }
        data-skill-source={block.source}
        aria-live="polite"
      >
        <div className="msg-skill-activity__line">
          <FileText size={13} strokeWidth={1.5} aria-hidden="true" />
          <span>{`${prefix} ${block.name} skill`}</span>
        </div>
        {block.status === 'failed' && block.error && (
          <span className="msg-skill-activity__error">{block.error}</span>
        )}
      </div>
    )
  }
  if (block.type === 'search') {
    return (
      <div key={i} className="msg-searches">
        <SearchCallItem
          s={{ query: block.query, results: block.results, loading: block.loading }}
        />
      </div>
    )
  }
  if (block.type === 'change') {
    if (!changeIsVisible(block.change, firstUnansweredChange)) return null
    return (
      <div key={i} className="msg-proposed-changes">
        <ChangePreviewSwitch
          change={block.change}
          onResolvePendingToolUse={onResolvePendingToolUse}
          onRespond={onRespond && ((status) => onRespond(block.change.id, status))}
        />
      </div>
    )
  }
  if (block.type === 'duplicate_review') {
    return (
      <div key={i} className="msg-proposed-changes">
        <DuplicateReview
          duplicates={block.duplicates}
          batchId={block.batchId}
          onSendMessage={onSendMessage}
        />
      </div>
    )
  }
  if (block.type === 'artifact') {
    return (
      <div key={i} className="msg-artifact-wrap">
        <ArtifactCard artifact={block.artifact} />
      </div>
    )
  }
  if (block.type === 'receipt') {
    return (
      <div key={i} className="msg-receipt-wrap">
        <WriteReceiptChip receipt={block.receipt} />
      </div>
    )
  }
  if (block.type === 'screen_command') {
    return (
      <div key={i} className="msg-screen-cmd">
        <ScreenCommandChip command={block.command} route={block.open?.route} />
      </div>
    )
  }
  return null
}

// Plain-text projection of a message for the clipboard — joins any text blocks,
// falling back to the flat `text` field.
function messageText(m: Message): string {
  if (m.blocks && m.blocks.length > 0) {
    const text = m.blocks
      .filter((b): b is Extract<MessageBlock, { type: 'text' }> => b.type === 'text')
      .map((b) => b.content)
      .join('\n\n')
      .trim()
    if (text) return text
  }
  // A confirm/dismiss tap has no blocks — its text IS the synthetic handshake, so Copy must
  // hand back what the bubble shows, never the raw tool name + confirm_token.
  return stripConfirmHandshake(m.text || '')
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <button className="msg-copy-btn" onClick={copy} title={copied ? 'Copied' : 'Copy message'}>
      {copied ? (
        <>
          <Check size={11} strokeWidth={1.6} aria-hidden="true" />
          Copied
        </>
      ) : (
        <>
          <Copy size={11} strokeWidth={1.4} aria-hidden="true" />
          Copy
        </>
      )}
    </button>
  )
}

function shortModelName(model: string): string {
  const bare = model.includes(':') ? model.split(':').pop()! : model
  return bare.replace(/^claude-/, '').replace(/-\d{8}$/, '')
}

/** The per-message model label shown in the cost chip. Every assistant turn shows
 *  WHICH model answered (a conversation can switch models mid-thread, and the picker's
 *  label doesn't tell you what a past message used). Returns null when no model was
 *  recorded. Exported for testing. */
export function modelChipLabel(usage: Pick<TurnUsage, 'model'>): string | null {
  if (!usage.model) return null
  return shortModelName(usage.model)
}

/** Exported for testing — the visibility gate lives here, not at the call site. */
export function CostChip({ usage }: { usage: TurnUsage }) {
  // Hidden by preference (Settings -> AI -> Chat). Read here rather than at the call site so
  // every place that renders a chip honours it without each one remembering to check.
  // Nothing until the preference is KNOWN — rendering on a guess and then hiding it is the
  // flash-and-collapse this exists to avoid. Known from the first render when the profile is
  // cached, which is every open after the first load.
  const { visible, loaded } = useChatCostVisible()
  if (!visible || !loaded) return null

  const inp = usage.input_tokens ?? 0
  const cWrite = usage.cache_creation_tokens ?? 0
  const cRead = usage.cache_read_tokens ?? 0
  const out = usage.output_tokens ?? 0
  const cost =
    usage.price_known === false
      ? 'price unknown'
      : usage.cost_usd < 0.001
        ? '<$0.001'
        : `~$${usage.cost_usd.toFixed(3)}`

  const parts: string[] = []
  if (inp) parts.push(`${inp.toLocaleString()} in`)
  if (cWrite) parts.push(`${cWrite.toLocaleString()} cache↑`)
  if (cRead) parts.push(`${cRead.toLocaleString()} cache↓`)
  if (out) parts.push(`${out.toLocaleString()} out`)

  // Show which model answered on EVERY turn (short name, no provider prefix or date
  // suffix — "claude-haiku-4-5-20251001" → "haiku-4-5"); "(auto)" marks a routed turn.
  const modelLabel = modelChipLabel(usage)

  return (
    <div className="msg-cost-chip">
      {parts.join(' • ')} • {cost}
      {modelLabel && <span className="msg-cost-chip__model"> • {modelLabel}</span>}
    </div>
  )
}

/** The turn is in flight but nothing has come back — not thinking, just waiting.
 *
 * These were one chip until 2026-08-01, so a request that had produced no bytes yet was
 * labelled "Thinking" with an empty, un-expandable body. Waiting and thinking are
 * different states and now say so. */
/** A user message sent into an already-busy conversation, queued for delivery into the
 *  running turn rather than opening a fresh one (RFC mid-turn injection; acceptance C1/C2).
 *  Copy leads with the MECHANISM, not a retry-sounding "Waiting to send…" — nothing failed,
 *  nothing is retrying, the message is exactly where it should be.
 *  concurrent-conversations-ui-spec.md §5.3. */
function PendingChip() {
  const [stale, setStale] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setStale(true), 30_000)
    return () => clearTimeout(t)
  }, [])
  return (
    <div className="msg-pending-chip" role="status" aria-live="polite">
      <Clock size={11} strokeWidth={1.6} aria-hidden="true" />
      <span>
        {stale
          ? 'Still queued — Lucius hasn’t reached it yet'
          : 'Queued — Lucius will read this at its next step'}
      </span>
    </div>
  )
}

const FILE_LABELS: Record<string, string> = {
  'transactions.json': 'transactions',
  'accounts.json': 'accounts',
  'profile.json': 'profile',
  'positions.json': 'positions',
  'instruments.json': 'instruments',
  'actions.json': 'actions',
  'watchlist.json': 'watchlist',
  'intel_signals.json': 'intel signals',
  'import_batches.json': 'import batches',
  'statements.json': 'statements',
  'memory.json': 'assistant memory',
}

function summarisePreview(changes: CheckpointFileChange[]): string {
  const parts: string[] = []
  for (const c of changes) {
    const label = FILE_LABELS[c.file] || c.file
    if (c.delta > 0) parts.push(`restore ${c.delta} ${label}`)
    else if (c.delta < 0) parts.push(`remove ${-c.delta} ${label}`)
    else parts.push(`revert ${label}`)
  }
  if (parts.length === 0) return 'No changes to restore.'
  return parts.join(', ') + '.'
}

// Restore is point-in-time: it rewinds to before this message, undoing this turn
// AND every change made after it. Make that explicit so totals aren't surprising.
const RESTORE_LEAD = 'Rewinds to before this message — undoes this and any later changes:'

function RestoreControl({
  message: m,
  onPreview,
  onRestore,
  guide = false,
}: {
  message: Message
  guide?: boolean
  onPreview?: (id: string) => Promise<PreviewResult>
  onRestore?: (id: string, mode?: RestoreMode) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [cross, setCross] = useState<CrossSessionChange[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [gone, setGone] = useState(false)
  const cpId = m.checkpointId!
  // The popover is portaled to <body> and placed against the trigger: inside the thread it
  // always opened DOWNWARD, so on the last message it slid under the composer (which floats
  // over the bottom of the thread) and past the window edge — its buttons unclickable.
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const placement = useAnchoredPopover(
    open,
    triggerRef,
    popoverRef,
    `${preview}|${cross.length}|${err}|${gone}`,
  )

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && preview === null && onPreview) {
      try {
        const res = await onPreview(cpId)
        if ('gone' in res) {
          setGone(true)
          setPreview('This checkpoint is no longer available.')
        } else {
          setPreview(summarisePreview(res.changes || []))
          setCross(res.cross_session_changes || [])
        }
      } catch {
        setPreview('Could not load preview.')
      }
    }
  }

  async function doRestore(mode: RestoreMode) {
    if (!onRestore || busy) return
    setBusy(true)
    setErr(null)
    try {
      await onRestore(cpId, mode)
      setOpen(false)
    } catch (e) {
      if ((e as Error).message === 'CHECKPOINT_GONE') {
        setGone(true)
        setPreview('This checkpoint is no longer available.')
      } else {
        setErr((e as Error).message || 'Restore failed')
      }
    } finally {
      setBusy(false)
    }
  }

  async function undoRestore() {
    if (!onRestore || !m.undoCheckpointId || busy) return
    setBusy(true)
    setErr(null)
    try {
      await onRestore(m.undoCheckpointId, 'data')
    } catch (e) {
      setErr((e as Error).message || 'Undo failed')
    } finally {
      setBusy(false)
    }
  }

  if (m.restored) {
    return (
      <div className="msg-restore-done">
        <span>✓ Restored to before this message</span>
        {m.undoCheckpointId && onRestore && (
          <button className="msg-restore-undo" disabled={busy} onClick={undoRestore}>
            {busy ? 'Undoing…' : 'Undo'}
          </button>
        )}
        {err && <span className="msg-restore-err">{err}</span>}
      </div>
    )
  }

  return (
    <div className="msg-restore">
      <button
        ref={triggerRef}
        className="msg-restore-trigger"
        data-tour={guide ? 'lucius-restore' : undefined}
        aria-expanded={open}
        onClick={toggle}
        title="Restore data to before this message"
      >
        <RotateCcw size={11} strokeWidth={1.4} aria-hidden="true" />
        Restore
      </button>
      {open &&
        createPortal(
          <div
            ref={popoverRef}
            className="msg-restore-popover"
            // Portaled: a tap on it is still inside whatever Modal/panel opened it.
            data-portal-layer
            style={
              placement
                ? { top: placement.top, left: placement.left }
                : { top: 0, left: 0, visibility: 'hidden' }
            }
          >
            {!gone && preview !== null && preview !== 'Could not load preview.' && (
              <div className="msg-restore-lead">{RESTORE_LEAD}</div>
            )}
            <div className="msg-restore-preview">{preview ?? 'Checking impact…'}</div>
            {!gone && cross.length > 0 && (
              <div className="msg-restore-warn">
                ⚠ Also undoes {cross.length} later change{cross.length > 1 ? 's' : ''} from{' '}
                {[...new Set(cross.map((c) => c.session_label))].join(', ')}:
                <ul>
                  {cross.map((c, i) => (
                    <li key={i}>
                      {c.session_label}: “{c.message_preview.slice(0, 40)}”
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {err && <div className="msg-restore-err">{err}</div>}
            {gone ? (
              <div className="msg-restore-actions">
                <button className="msg-restore-cancel" onClick={() => setOpen(false)}>
                  Close
                </button>
              </div>
            ) : (
              <>
                <div className="msg-restore-actions">
                  <button disabled={busy} onClick={() => doRestore('data')}>
                    Restore data
                  </button>
                  <button disabled={busy} onClick={() => doRestore('both')}>
                    Restore data &amp; rewind chat
                  </button>
                  <button
                    disabled={busy}
                    className="msg-restore-cancel"
                    onClick={() => setOpen(false)}
                  >
                    Cancel
                  </button>
                </div>
                <div className="msg-restore-hint">This restore can itself be undone.</div>
              </>
            )}
          </div>,
          document.body,
        )}
    </div>
  )
}

/** A sent image attachment: a real tappable thumbnail, backed by GET /api/uploads/{ref_id}/image.
 *  Falls back to the plain text pill if that upload was since deleted (Settings → Uploaded
 *  files) or the fetch otherwise fails, rather than showing a broken-image icon. */
function AttachmentThumb({ a, onOpen }: { a: Attachment; onOpen: (a: Attachment) => void }) {
  const [broken, setBroken] = useState(false)
  if (broken) {
    return <div className="msg-attachment-pill">🖼 {a.summary}</div>
  }
  return (
    <button
      type="button"
      className="msg-attachment-thumb"
      onClick={() => onOpen(a)}
      title={a.summary}
    >
      <img src={`/api/uploads/${a.ref_id}/image`} alt={a.summary} onError={() => setBroken(true)} />
    </button>
  )
}

export function MessageBubble({
  message: m,
  onSendMessage,
  onResolvePendingToolUse,
  onRespondToChange,
  onRetry,
  onOpenAiSettings,
  onPreviewCheckpoint,
  onRestoreCheckpoint,
  restoreGuide = false,
}: Props) {
  const isUser = m.role === 'user'
  const [lightboxAtt, setLightboxAtt] = useState<Attachment | null>(null)
  // Settings -> AI -> Chat. Default OFF: reasoning is drawn where it happened.
  const { grouped: groupThinking } = useChatThinkingGrouped()

  // Elided tool blocks only ever arrive via the transcript endpoint, whose message ids
  // are positional (`{session}-{counter}`) — so the session id for the fetch-on-tap
  // detail call is recoverable from the id itself. A live-born id yields undefined,
  // and live blocks are never elided.
  const transcriptSessionId = /^(.+)-\d+$/.exec(m.id)?.[1]

  // One decision on screen at a time — see firstUnansweredChangeId.
  const firstUnanswered = firstUnansweredChangeId(m)
  const failures = callFailures(m)

  const respond = onRespondToChange
    ? (changeId: string | undefined, status: 'confirmed' | 'dismissed') =>
        onRespondToChange(m.id, changeId, status)
    : undefined

  return (
    <div className={`msg-bubble ${isUser ? 'msg-user' : 'msg-assistant'}`}>
      {/* The "still working" indicator is NOT here any more — it lives above the composer
          (TurnActivity), where it cannot scroll away mid-turn and sits next to Stop. */}
      {/* One merged block above the turn, in two cases: the reader asked for it (Settings →
          AI → Chat), or the transcript predates positioned thinking and the message-level
          field is all there is. Otherwise the blocks carry the ORDER too, and
          `renderGroupedBlock` draws each thought where it happened. */}
      {m.thinking && (groupThinking || !m.blocks?.some((b) => b.type === 'thinking')) && (
        <ThinkingBlock text={m.thinking} streaming={m.streaming} />
      )}

      {m.attachments && m.attachments.length > 0 && (
        <div className="msg-attachments">
          {m.attachments.map((a) =>
            a.kind === 'image' ? (
              <AttachmentThumb key={a.ref_id} a={a} onOpen={setLightboxAtt} />
            ) : (
              <a
                key={a.ref_id}
                className="msg-attachment-pill msg-attachment-download"
                href={`/api/uploads/${a.ref_id}/file`}
                download
                title={`Download ${a.summary}`}
              >
                📄 {a.summary}
              </a>
            ),
          )}
        </div>
      )}

      {lightboxAtt && (
        <Modal
          open
          onClose={() => setLightboxAtt(null)}
          title={lightboxAtt.summary}
          size="xl"
          className="msg-attachment-lightbox"
        >
          <img
            className="msg-attachment-lightbox-img"
            src={`/api/uploads/${lightboxAtt.ref_id}/image`}
            alt={lightboxAtt.summary}
          />
        </Modal>
      )}

      {m.blocks && m.blocks.length > 0 ? (
        <>
          {/* Artifact blocks render LAST regardless of where the model created them in the
              turn (owner, 2026-08-13: "at the bottom of the message, instead of the top") —
              a model that opens with the artifact and narrates afterward would otherwise show
              the chip above text that's actually introducing it. Partitioned rather than
              sorted-in-place so every OTHER block keeps groupBlocks' own relative order
              (notably its tool/fetch → 'actions' merging, which depends on adjacency). */}
          {(() => {
            const grouped = groupBlocks(m.blocks, {
              groupThinking,
              streaming: Boolean(m.streaming),
            })
            const artifacts = grouped.filter((b) => b.type === 'artifact')
            const rest = grouped.filter((b) => b.type !== 'artifact')
            const renderOne = (block: GroupedBlockWithChange, i: number) => {
              if (block.type === 'change') {
                if (!changeIsVisible(block.change, firstUnanswered)) return null
                return (
                  <div key={i} className="msg-proposed-changes">
                    <ChangePreviewSwitch
                      change={block.change}
                      onResolvePendingToolUse={onResolvePendingToolUse}
                      onRespond={respond && ((status) => respond(block.change.id, status))}
                      failure={failures.get(block.change.id ?? '')}
                    />
                  </div>
                )
              }
              return renderGroupedBlock(
                block,
                i,
                onSendMessage,
                respond,
                onResolvePendingToolUse,
                transcriptSessionId,
                firstUnanswered,
              )
            }
            return (
              <>
                {rest.map(renderOne)}
                {artifacts.map((block, i) => renderOne(block, rest.length + i))}
              </>
            )
          })()}
          {!isUser && m.cites && m.cites.length > 0 && <CitationChips cites={m.cites} />}
        </>
      ) : (
        <>
          {m.text &&
            (isUser ? (
              <>
                <div className={`msg-text${m.pending === 'queued' ? ' msg-user-pending' : ''}`}>
                  <MentionText text={stripConfirmHandshake(stripScreenContext(m.text))} />
                </div>
                {m.pending === 'queued' && <PendingChip />}
              </>
            ) : (
              <div className="msg-text">
                <ReactMarkdown
                  remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
                  components={{
                    ...SAFE_MARKDOWN_COMPONENTS,
                    a: ChatLink,
                    pre: CodeBlock,
                    table: ({ children }) => (
                      <div className="table-wrap">
                        <table>{children}</table>
                      </div>
                    ),
                  }}
                >
                  {repairStrayFences(m.text)}
                </ReactMarkdown>
              </div>
            ))}
          {!isUser && m.cites && m.cites.length > 0 && <CitationChips cites={m.cites} />}

          {m.searches && m.searches.length > 0 && (
            <div className="msg-searches">
              {m.searches.map((s, i) => (
                <SearchCallItem key={i} s={s} />
              ))}
            </div>
          )}

          {m.fetches && m.fetches.length > 0 && (
            <div className="msg-tool-calls">
              {m.fetches.map((url, i) => {
                let host = url
                try {
                  host = new URL(url).hostname
                } catch {
                  /* ignore */
                }
                return (
                  <span key={i} className="msg-tool-pill">
                    📄 {host}
                  </span>
                )
              })}
            </div>
          )}

          {/* The legacy flat tool-name pill row is GONE (2026-08-22). It was the second
              renderer of the same events and the one that printed raw names
              (`mcp__playwright__browser_navigate`); `blocks` carries the real thing, via
              ToolActivity. `m.toolCalls` survives as data other code reads — it just no
              longer draws anything of its own. */}
        </>
      )}

      {(!m.blocks || m.blocks.length === 0) &&
        m.proposedChanges &&
        m.proposedChanges.length > 0 && (
          <div className="msg-proposed-changes">
            {m.proposedChanges
              .filter((change) => changeIsVisible(change, firstUnanswered))
              .map((change, i) => (
                <ChangePreviewSwitch
                  key={i}
                  change={change}
                  onResolvePendingToolUse={onResolvePendingToolUse}
                  onRespond={respond && ((status) => respond(change.id, status))}
                />
              ))}
          </div>
        )}

      {m.error && m.setupHint && (
        <div className="msg-setup-hint" role="alert">
          <span className="msg-setup-hint-icon">⚙</span>
          <span className="msg-setup-hint-text">
            {m.error}
            {onOpenAiSettings && (
              <Button variant="ghost" size="dense" onClick={onOpenAiSettings}>
                Open AI settings
              </Button>
            )}
          </span>
        </div>
      )}
      {m.error && m.notice && !m.setupHint && <div className="msg-notice">{m.error}</div>}
      {m.error && !m.notice && !m.setupHint && (
        <div className="msg-error" role="alert">
          <span className="msg-error-text">{m.error}</span>
          {onOpenAiSettings && (
            <Button variant="ghost" size="dense" onClick={onOpenAiSettings}>
              Open AI settings
            </Button>
          )}
          {onRetry && (
            <button className="msg-retry-btn" onClick={onRetry}>
              <RotateCcw size={12} aria-hidden="true" /> Retry
            </button>
          )}
        </div>
      )}

      {/* `awaitingApproval`: the turn PAUSED on a confirm/permission card and resumes the
          moment it is answered. The chip is a turn's full stop, so showing it here reads as
          "finished" while the model is still mid-task — withhold it until the resumed stream
          completes, when it shows the whole turn's accumulated cost (useChat.mergeUsage). */}
      {!isUser && !m.streaming && !m.awaitingApproval && m.usage && <CostChip usage={m.usage} />}

      {!m.streaming && messageText(m) && (
        <div className="msg-footer">
          {/* Absent for a still-queued message — its timestamp is its delivery time, not
              its send time (chatReducer.deliverQueuedMessage), and it has none yet. */}
          {m.ts !== undefined && <span className="msg-time">{fTime(m.ts)}</span>}
          <CopyButton text={messageText(m)} />
          {isUser && m.checkpointId && !m.checkpointSuperseded && (
            <RestoreControl
              message={m}
              onPreview={onPreviewCheckpoint}
              onRestore={onRestoreCheckpoint}
              guide={restoreGuide}
            />
          )}
        </div>
      )}

      {isUser && m.checkpointId && !m.checkpointSuperseded && !messageText(m) && (
        <RestoreControl
          message={m}
          onPreview={onPreviewCheckpoint}
          onRestore={onRestoreCheckpoint}
          guide={restoreGuide}
        />
      )}
    </div>
  )
}
