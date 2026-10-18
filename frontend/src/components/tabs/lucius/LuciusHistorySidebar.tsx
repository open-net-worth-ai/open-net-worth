// LuciusHistorySidebar — the Lucius home tab's persistent, collapsible history rail.
//
// Reuses exactly what the floating dock's own ConversationMenu already computes
// (conversationGroups → selectVisibleRows/sortWithinSection → groupByAge) and its row UI
// (ConversationRow) — this list is always visible instead of toggled, but the data and the
// row chrome are identical to the dock's, so the two surfaces never drift apart.

import './lucius-home.css'
import { useState } from 'react'
import {
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  ListChecks,
  MessageSquare,
  SquarePen,
} from '../../../ui/icons'
import {
  BackgroundConversationsToggle,
  ConversationRow,
  ConversationIcon,
  groupByAge,
  selectVisibleRows,
  shortWhen,
  sortWithinSection,
} from '../../chat/ConversationMenu'
import type { ConversationMeta } from '../../../hooks/useConversations'
import type { ConvRowStatus } from '../../../types/chat'

const RECENT_RUNS = 5
const RECENT_CHATS = 25

export function LuciusHistorySidebar({
  groups,
  activeId,
  statusFor,
  onNewChat,
  onSelect,
  onRename,
  onDelete,
  onStop,
  onViewAll,
  onMarkAllRead,
  onCollapse,
}: {
  groups: { key: 'chat' | 'automation'; items: ConversationMeta[]; total: number }[]
  activeId: string
  statusFor: (id: string) => ConvRowStatus
  onNewChat: () => void
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onStop: (id: string) => void
  onViewAll: (kind: 'chat' | 'automation') => void
  onMarkAllRead: (ids: string[]) => void
  onCollapse: () => void
}) {
  const [editingId, setEditingId] = useState<string | null>(null)

  const runs = groups.find((g) => g.key === 'automation')?.items ?? []
  const chats = groups.find((g) => g.key === 'chat')?.items ?? []
  const visibleRuns = sortWithinSection(selectVisibleRows(runs, statusFor, RECENT_RUNS), statusFor)
  const visibleChats = selectVisibleRows(chats, statusFor, RECENT_CHATS)
  // Every unread row in the section — not just the ones the rail shows. A run you never
  // open is otherwise unread forever, and "View all runs" is the only other way to clear it.
  const unreadIds = (items: ConversationMeta[]) =>
    items.filter((c) => ['done', 'failed'].includes(statusFor(c.id))).map((c) => c.id)
  const unreadRuns = unreadIds(runs)
  const unreadChats = unreadIds(chats)
  const markAllRead = (ids: string[]) =>
    ids.length > 0 && (
      <button
        type="button"
        className="conv-menu-markread"
        onClick={() => onMarkAllRead(ids)}
        aria-label="Mark all read"
        title={`Mark ${ids.length} unread as read`}
      >
        <CheckCheck size={14} />
      </button>
    )

  const row = (conv: ConversationMeta) => (
    <ConversationRow
      key={conv.id}
      conv={conv}
      active={conv.id === activeId}
      status={statusFor(conv.id)}
      when={shortWhen(conv)}
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
      icon={<ConversationIcon conv={conv} />}
    />
  )

  return (
    <aside className="lucius-home-side">
      <div className="lucius-home-side-top">
        <button type="button" className="lucius-home-side-new" onClick={onNewChat}>
          <SquarePen size={14} />
          New
        </button>
        <button
          type="button"
          className="lucius-home-side-collapse"
          onClick={onCollapse}
          aria-label="Hide chat history"
          title="Hide chat history"
        >
          <ChevronLeft size={14} />
        </button>
      </div>

      <div className="lucius-home-side-scroll">
        {runs.length > 0 && (
          <div className="conv-menu-sec">
            <div className="conv-menu-sec-h">
              <ListChecks size={14} />
              <span>Automation runs</span>
              {markAllRead(unreadRuns)}
              <button
                type="button"
                className="conv-menu-viewall"
                onClick={() => onViewAll('automation')}
                aria-label="View all runs"
                title="View all runs"
              >
                <ChevronRight size={14} />
              </button>
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

        <div className="conv-menu-sec conv-menu-sec--fill">
          <div className="conv-menu-sec-h">
            <MessageSquare size={14} />
            <span>Chats</span>
            {markAllRead(unreadChats)}
            <BackgroundConversationsToggle />
            <button
              type="button"
              className="conv-menu-viewall"
              onClick={() => onViewAll('chat')}
              aria-label="View all conversations"
              title="View all conversations"
            >
              <ChevronRight size={14} />
            </button>
          </div>
          {chats.length === 0 ? (
            <div className="conv-menu-empty">No chats yet.</div>
          ) : (
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
    </aside>
  )
}
