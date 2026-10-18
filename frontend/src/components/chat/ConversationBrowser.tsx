import { useEffect, useMemo, useState } from 'react'
import { Search, X } from '../../ui/icons'
import { ConversationRenameInput, ConversationRowMenu } from './ConversationRowActions'
import { BackgroundConversationsToggle, ConversationIcon } from './ConversationMenu'
import { useShowBackgroundConversations } from '../../hooks/useShowBackgroundConversations'
import { ConversationStatusBadge, conversationStatusLabel } from './ConversationStatusBadge'
import { isBackgroundConversation, type ConversationMeta } from '../../hooks/useConversations'
import type { ConvRowStatus } from '../../types/chat'

/**
 * The full list of conversations, over the whole Lucius panel.
 *
 * The switcher dropdown is for the handful you were just in. Everything else — 383 chats
 * and 192 Automation runs on this instance — needs room, a filter and a search box, and
 * that does not fit in a menu hanging off a header. So "View all" hands the panel over to
 * this until you pick something or close it.
 *
 * No sort/force-inclusion here (unlike ConversationMenu) — the full list already shows
 * everything, so promoting needs_you/working rows is a nice-to-have, not load-bearing
 * (concurrent-conversations-ui-spec.md §5.1.1).
 */
export function ConversationBrowser({
  conversations,
  activeId,
  statusFor,
  onSelect,
  onClose,
  onDelete,
  onRename,
  onStop,
  formatWhen,
  initialKind = 'all',
}: {
  conversations: ConversationMeta[]
  activeId: string
  statusFor: (id: string) => ConvRowStatus
  onSelect: (id: string) => void
  onClose: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  onStop: (id: string) => void
  formatWhen: (conv: ConversationMeta) => string
  /** Which filter it opens on — "View all runs" should land on runs, not ask again. */
  initialKind?: 'all' | 'chat' | 'automation'
}) {
  // Esc goes back to the conversation — same as the sidebar it opened from.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<'all' | 'chat' | 'automation'>(initialKind)
  const [editingId, setEditingId] = useState<string | null>(null)

  const [showBackground] = useShowBackgroundConversations()
  const rows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return conversations.filter((conv) => {
      const isRun = conv.source === 'automation'
      if (!showBackground && isBackgroundConversation(conv)) return false
      // Same rule as `conversationGroups`: "chat" means IN-APP — neither runner-owned
      // (`source`) nor arriving over a transport (`channel`).
      if (kind === 'chat' && (conv.source || conv.channel)) return false
      if (kind === 'automation' && !isRun) return false
      return !needle || conv.title.toLocaleLowerCase().includes(needle)
    })
  }, [conversations, query, kind, showBackground])

  return (
    <div className="conv-browser" role="dialog" aria-label="All conversations">
      <div className="conv-browser-head">
        <span className="conv-browser-t">Chats and runs</span>
        <button type="button" className="conv-browser-x" onClick={onClose} aria-label="Close">
          <X size={18} strokeWidth={1.6} />
        </button>
      </div>

      <div className="conv-browser-tools">
        <div className="conv-browser-searchrow">
          <label className="conv-browser-search">
            <Search size={13} aria-hidden="true" />
            <span className="ui-visually-hidden">Search conversations</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search…"
              autoFocus
            />
          </label>
          <BackgroundConversationsToggle />
        </div>
        <div className="conv-seg conv-seg--browser" role="tablist" aria-label="Filter">
          {(
            [
              ['all', 'All'],
              ['chat', 'Chats'],
              ['automation', 'Runs'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={kind === value}
              className={`conv-seg-btn${kind === value ? ' on' : ''}`}
              onClick={() => setKind(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="conv-browser-list">
        {rows.length === 0 ? (
          <div className="conv-browser-empty">
            {query.trim() ? `Nothing matching “${query.trim()}”.` : 'Nothing here yet.'}
          </div>
        ) : (
          // A DIV wrapping a button, not a button itself — the kebab is a button too, and a
          // button inside a button is invalid markup (browsers hoist it out of the row).
          rows.map((conv) => {
            const status = statusFor(conv.id)
            const statusLabel = conversationStatusLabel(status)
            return (
              <div key={conv.id} className={`conv-browser-row${conv.id === activeId ? ' on' : ''}`}>
                {editingId === conv.id ? (
                  <ConversationRenameInput
                    className="conv-row-rename"
                    initial={conv.title}
                    onCommit={(title) => {
                      setEditingId(null)
                      onRename(conv.id, title)
                    }}
                    onCancel={() => setEditingId(null)}
                  />
                ) : (
                  <button
                    type="button"
                    className="conv-browser-main"
                    onClick={() => onSelect(conv.id)}
                  >
                    <span className="conv-browser-ic" aria-hidden="true">
                      <ConversationIcon conv={conv} />
                    </span>
                    <ConversationStatusBadge status={status} />
                    <span className="conv-browser-title">{conv.title}</span>
                    <span className="conv-browser-when">{statusLabel ?? formatWhen(conv)}</span>
                  </button>
                )}
                <ConversationRowMenu
                  conv={conv}
                  status={status}
                  onStartRename={setEditingId}
                  onDelete={onDelete}
                  onStop={onStop}
                />
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
