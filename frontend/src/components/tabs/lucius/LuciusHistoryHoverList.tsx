// The floating conversation list shown on hover over the collapsed history sidebar's reopen
// chevron (LuciusHome.tsx) — lets a user switch conversations without permanently re-expanding
// the sidebar. Same row data/chrome as LuciusHistorySidebar (ConversationRow + the shared
// grouping helpers from ConversationMenu.tsx), just without that component's own header
// (New/Collapse buttons don't belong in a transient hover peek).

import { useState } from 'react'
import { ChevronRight, ListChecks, MessageSquare } from '../../../ui/icons'
import {
  ConversationRow,
  ConversationIcon,
  groupByAge,
  selectVisibleRows,
  sortWithinSection,
} from '../../chat/ConversationMenu'
import type { ConversationMeta } from '../../../hooks/useConversations'
import type { ConvRowStatus } from '../../../types/chat'

const RECENT_RUNS = 5
const RECENT_CHATS = 25

export function LuciusHistoryHoverList({
  groups,
  activeId,
  statusFor,
  onSelect,
  onRename,
  onDelete,
  onStop,
  onViewAll,
  onMenuOpenChange,
}: {
  groups: { key: 'chat' | 'automation'; items: ConversationMeta[]; total: number }[]
  activeId: string
  statusFor: (id: string) => ConvRowStatus
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onStop: (id: string) => void
  onViewAll: (kind: 'chat' | 'automation') => void
  /** Forwarded to every row's kebab menu — LuciusHome.tsx uses it to keep this hover flyout
   *  open while a row's Rename/Delete menu is open. The menu portals to `document.body`
   *  (ActionMenu.tsx), outside this flyout's own DOM subtree, so moving the cursor into it
   *  otherwise reads as "left the flyout" and closes the whole thing out from under the menu
   *  the user is still using (owner report: "tapping Rename or Delete is closing the floating
   *  panel"). */
  onMenuOpenChange?: (open: boolean) => void
}) {
  const [editingId, setEditingId] = useState<string | null>(null)

  const runs = groups.find((g) => g.key === 'automation')?.items ?? []
  const chats = groups.find((g) => g.key === 'chat')?.items ?? []
  const visibleRuns = sortWithinSection(selectVisibleRows(runs, statusFor, RECENT_RUNS), statusFor)
  const visibleChats = selectVisibleRows(chats, statusFor, RECENT_CHATS)

  const row = (conv: ConversationMeta) => (
    <ConversationRow
      key={conv.id}
      conv={conv}
      active={conv.id === activeId}
      status={statusFor(conv.id)}
      when=""
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
      onMenuOpenChange={onMenuOpenChange}
    />
  )

  return (
    <div className="lucius-home-reopen-preview">
      {runs.length > 0 && (
        <div className="conv-menu-sec">
          <div className="conv-menu-sec-h">
            <ListChecks size={14} />
            <span>Automation runs</span>
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
        </div>
      )}
      <div className="conv-menu-sec conv-menu-sec--fill">
        <div className="conv-menu-sec-h">
          <MessageSquare size={14} />
          <span>Chats</span>
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
      </div>
    </div>
  )
}
