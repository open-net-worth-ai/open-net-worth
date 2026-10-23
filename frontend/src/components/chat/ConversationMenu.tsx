import { useEffect, useState, type ReactNode } from 'react'
import { ListChecks, MessageSquare, Plus, SquarePen, X, Zap } from '../../ui/icons'
import { ConversationRenameInput, ConversationRowMenu } from './ConversationRowActions'
import { ConversationStatusBadge, conversationStatusLabel } from './ConversationStatusBadge'
import type { ConversationMeta } from '../../hooks/useConversations'
import type { ConvRowStatus } from '../../types/chat'
import { fDateTime } from '../../utils/format'

export type ConversationKind = 'chat' | 'automation' | 'capture'

/** The row's type glyph: an Automation run, a capture, or a chat. */
export function ConversationIcon({ conv }: { conv: ConversationMeta }) {
  if (conv.source === 'automation') return <ListChecks size={14} />
  if (conv.origin === 'capture') return <Zap size={14} />
  return <MessageSquare size={14} />
}

/**
 * The Lucius panel's own sidebar: new chat, the runs and chats you were last in, and a way
 * through to all of them.
 *
 * It replaces a dropdown that hung off the header. A menu is fine for a handful of items
 * and wrong for 575: it could not hold a heading, a date grouping, or a "new chat" action
 * without becoming a small page rendered in the worst possible place for one.
 */

// Five a side. Ten pushed Chats below the fold — the sidebar is a shortcut to the last
// few of each, and the moment it needs scrolling it has become the list it links to. A
// needs_you/working row is force-included PAST this cap (selectVisibleRows below) —
// concurrent-conversations-ui-spec.md §5.1.1 rule 2 — so this is "here's what's active,
// plus a few recents," not a hard ceiling.
const RECENT_RUNS = 5
// Chats are the thing you actually navigate by, so the list fills the panel rather than
// stopping at an arbitrary five with empty space below it. Runs stay at five — twenty runs
// of one Automation all carry ITS name and read as duplicates, so more is not more useful.
// Still bounded, and still backed by "View all conversations": the section flexes to the
// available height and scrolls past it, so a tall screen shows more without loading the lot.
const RECENT_CHATS = 25
// Captures are a record of what came in and what got booked — you look one up, you don't
// navigate by them. Three is enough to see the latest landed; the rest are a click away.
export const RECENT_CAPTURES = 3

/** §5.1.1 rule 2 — force-inclusion past the N-item cap. `items` is assumed already
 *  recency-sorted (newest first). A `needs_you`/`working` conversation must never be
 *  silently pushed off the visible sidebar by newer, boring rows — take every one of
 *  those FIRST regardless of recency, then fill the remaining slots with the next most
 *  recent idle/done/failed rows. Preserves the original recency order in its output;
 *  `sortWithinSection` below is the separate pass that reorders for DISPLAY. */
export function selectVisibleRows(
  items: ConversationMeta[],
  statusFor: (id: string) => ConvRowStatus,
  limit: number,
): ConversationMeta[] {
  const urgentIds = new Set(
    items.filter((c) => ['needs_you', 'working'].includes(statusFor(c.id))).map((c) => c.id),
  )
  const fillIds = new Set(
    items
      .filter((c) => !urgentIds.has(c.id))
      .slice(0, Math.max(0, limit - urgentIds.size))
      .map((c) => c.id),
  )
  return items.filter((c) => urgentIds.has(c.id) || fillIds.has(c.id))
}

/** §5.1.1 rule 1 — intra-bucket promotion: needs_you rows first, then working, then the
 *  existing recency order for everything else. Applied WITHIN one section (Automation
 *  runs) or one age bucket (a Chats bucket) — it never pulls a row across a Chats age
 *  bucket boundary. Array.prototype.sort is stable, so ties keep their incoming (recency)
 *  order. */
export function sortWithinSection(
  items: ConversationMeta[],
  statusFor: (id: string) => ConvRowStatus,
): ConversationMeta[] {
  const rank = (c: ConversationMeta) => {
    const s = statusFor(c.id)
    return s === 'needs_you' ? 0 : s === 'working' ? 1 : 2
  }
  return [...items].sort((a, b) => rank(a) - rank(b))
}

/** A stamp for automation runs only (owner: "let's not show time and date except it's
 *  automation run in the sidebar") — a chat row shows no stamp at all, since every caller of
 *  this menu already buckets chats by day ("Today" / "Yesterday" / …) via groupByAge below,
 *  and the exact time added nothing a real user needed there. A run isn't bucketed, so its
 *  stamp is the only way to tell two runs apart — kept, but shortened: time-only for today,
 *  "Yesterday HH:MM", and the full month/day/time stamp beyond that.
 *
 *  Scoped to THIS menu's own bucketed rows only — ConversationBrowser's "View all" list has
 *  no day-bucket headers of its own, so it keeps the shared, always-dated conversationWhen
 *  (ChatPanel.tsx) instead; every caller of THIS component (the dock's popup, LuciusMobile,
 *  and — via LuciusHistorySidebar's own reuse of this same function — the desktop home tab's
 *  persistent sidebar) uses this one, so the convention now matches everywhere a conversation
 *  list is actually bucketed by day (owner, 2026-08-13: "not just for mobile but should be
 *  for all"). */
export function shortWhen(conv: ConversationMeta): string {
  if (conv.source !== 'automation') return ''
  const at = new Date(conv.createdAt || conv.updatedAt || '')
  if (Number.isNaN(at.getTime())) return ''
  const time = at.toLocaleString(undefined, { hour: 'numeric', minute: '2-digit' })
  const now = new Date()
  if (at.toDateString() === now.toDateString()) return time
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (at.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`
  return fDateTime(at)
}

export function ConversationMenu({
  groups,
  activeId,
  statusFor,
  onClose,
  onNewChat,
  onSelect,
  onViewAll,
  onRename,
  onDelete,
  onStop,
  formatWhen,
}: {
  /** Already split and sorted by ChatPanel.conversationGroups — newest first, by when the
   *  conversation HAPPENED (an Automation's updated_at is unusable; see byRecency). */
  groups: { key: ConversationKind; items: ConversationMeta[]; total: number }[]
  activeId: string
  statusFor: (id: string) => ConvRowStatus
  onClose: () => void
  onNewChat: () => void
  onSelect: (id: string) => void
  onViewAll: (kind: ConversationKind) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onStop: (id: string) => void
  formatWhen: (conv: ConversationMeta) => string
}) {
  const [editingId, setEditingId] = useState<string | null>(null)
  // Esc goes back to the conversation. A surface that takes over the whole panel has to
  // be dismissable from the keyboard, not only by finding the one button that closes it.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const runs = groups.find((g) => g.key === 'automation')?.items ?? []
  const captures = groups.find((g) => g.key === 'capture')?.items ?? []
  const chats = groups.find((g) => g.key === 'chat')?.items ?? []
  const visibleRuns = sortWithinSection(selectVisibleRows(runs, statusFor, RECENT_RUNS), statusFor)
  const visibleCaptures = selectVisibleRows(captures, statusFor, RECENT_CAPTURES)
  const visibleChats = selectVisibleRows(chats, statusFor, RECENT_CHATS)
  const row = (conv: ConversationMeta) => (
    <ConversationRow
      key={conv.id}
      conv={conv}
      active={conv.id === activeId}
      status={statusFor(conv.id)}
      when={formatWhen(conv)}
      editing={editingId === conv.id}
      onSelect={onSelect}
      onStartRename={setEditingId}
      onCommitRename={(title) => {
        setEditingId(null)
        onRename(conv.id, title)
      }}
      onCancelRename={() => setEditingId(null)}
      onDelete={onDelete}
      onStop={onStop}
    />
  )

  return (
    <div className="conv-menu" role="dialog" aria-label="Conversations">
      <div className="conv-menu-sticky">
        <div className="conv-menu-head">
          <button type="button" className="conv-menu-x" onClick={onClose} aria-label="Close menu">
            <X size={16} />
            <span className="conv-menu-x-t">Back to chat</span>
          </button>
        </div>

        <button type="button" className="conv-menu-action" onClick={onNewChat}>
          <SquarePen size={15} />
          New chat
        </button>
      </div>

      <div className="conv-menu-scroll">
        {runs.length > 0 && (
          <div className="conv-menu-sec">
            <div className="conv-menu-sec-h">
              <ListChecks size={14} />
              <span>Automation runs</span>
            </div>
            {visibleRuns.map(row)}
            <button
              type="button"
              className="conv-menu-more"
              onClick={() => onViewAll('automation')}
            >
              View all runs
            </button>
          </div>
        )}

        {captures.length > 0 && (
          <div className="conv-menu-sec">
            <div className="conv-menu-sec-h">
              <Zap size={14} />
              <span>Captures</span>
            </div>
            {visibleCaptures.map(row)}
            <button type="button" className="conv-menu-more" onClick={() => onViewAll('capture')}>
              View all captures
            </button>
          </div>
        )}

        <div className="conv-menu-sec conv-menu-sec--fill">
          <div className="conv-menu-sec-h">
            <MessageSquare size={14} />
            <span>Chats</span>
            <button
              type="button"
              className="conv-menu-plus"
              onClick={onNewChat}
              aria-label="New chat"
            >
              <Plus size={14} />
            </button>
          </div>
          {chats.length === 0 ? (
            <div className="conv-menu-empty">No chats yet.</div>
          ) : (
            // Grouped by age rather than listed flat: six identically-shaped rows with a
            // timestamp each is a table nobody reads, and "yesterday" is how people actually
            // recall a conversation.
            groupByAge(visibleChats).map((bucket) => (
              <div key={bucket.label} className="conv-menu-bucket">
                <div className="conv-menu-bucket-h">{bucket.label}</div>
                {sortWithinSection(bucket.items, statusFor).map(row)}
              </div>
            ))
          )}
          <button type="button" className="conv-menu-more" onClick={() => onViewAll('chat')}>
            View all conversations
          </button>
        </div>
      </div>
    </div>
  )
}

/** The row is a DIV wrapping a button, not a button itself: the kebab is a button too, and
 *  a button inside a button is invalid — browsers hoist it out and the row stops working. */
export function ConversationRow({
  conv,
  active,
  status,
  when,
  editing,
  onSelect,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onDelete,
  onStop,
  icon,
  onMenuOpenChange,
}: {
  conv: ConversationMeta
  active: boolean
  status: ConvRowStatus
  when: string
  editing: boolean
  onSelect: (id: string) => void
  onStartRename: (id: string) => void
  onCommitRename: (title: string) => void
  onCancelRename: () => void
  onDelete: (id: string) => void
  onStop: (id: string) => void
  /** Per-row type glyph (chat bubble / automation checklist) — matches ConversationBrowser's
   *  own icon (.conv-browser-ic). Optional and undefined by default: the dock's own
   *  ConversationMenu popover never passes it, so its rows render byte-identical to before. */
  icon?: ReactNode
  /** Forwarded to ConversationRowMenu — see its own doc comment. Optional; omitted by every
   *  caller except LuciusHistoryHoverList. */
  onMenuOpenChange?: (open: boolean) => void
}) {
  const statusLabel = conversationStatusLabel(status)
  return (
    <div className={`conv-menu-row${active ? ' on' : ''}`}>
      {editing ? (
        <ConversationRenameInput
          className="conv-row-rename"
          initial={conv.title}
          onCommit={onCommitRename}
          onCancel={onCancelRename}
        />
      ) : (
        <button type="button" className="conv-menu-row-main" onClick={() => onSelect(conv.id)}>
          {icon && (
            <span className="conv-menu-row-ic" aria-hidden="true">
              {icon}
            </span>
          )}
          <ConversationStatusBadge status={status} />
          <span className="conv-menu-row-t">{conv.title}</span>
          {statusLabel ? (
            <span className="conv-menu-row-w conv-menu-row-w--status">{statusLabel}</span>
          ) : (
            when && <span className="conv-menu-row-w">{when}</span>
          )}
        </button>
      )}
      <ConversationRowMenu
        conv={conv}
        status={status}
        onStartRename={onStartRename}
        onDelete={onDelete}
        onStop={onStop}
        onMenuOpenChange={onMenuOpenChange}
      />
    </div>
  )
}

/** Today / Yesterday / This past week / Older — only the buckets that have something. */
export function groupByAge(conversations: ConversationMeta[]) {
  const now = Date.now()
  const day = 86_400_000
  const buckets: { label: string; items: ConversationMeta[] }[] = [
    { label: 'Today', items: [] },
    { label: 'Yesterday', items: [] },
    { label: 'This past week', items: [] },
    { label: 'Older', items: [] },
  ]
  for (const conv of conversations) {
    const at = Date.parse(conv.updatedAt || conv.createdAt || '')
    const age = Number.isNaN(at) ? Infinity : now - at
    const index = age < day ? 0 : age < 2 * day ? 1 : age < 7 * day ? 2 : 3
    buckets[index]!.items.push(conv)
  }
  return buckets.filter((bucket) => bucket.items.length > 0)
}
