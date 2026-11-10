// Keeps browser history and the open Lucius conversation in step — see state/chatHistory.ts for
// the rules. Mounted once inside LuciusChatProvider, so it serves the Lucius page, the side panel,
// the floating dock and the phone alike: they all share the one open conversation.
import { useEffect, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate } from '@tanstack/react-router'
import { useLuciusChat } from '../../state/luciusChat'
import {
  LUCIUS_PATH,
  NEW_CHAT,
  entryFor,
  moveFor,
  openFor,
  readEntry,
  sameEntry,
  setChatPathname,
  takeChatSwitch,
  type ChatEntry,
  type ChatHistoryState,
} from '../../state/chatHistory'
import { startFreshConversation } from '../../utils/freshConversation'

let navSeq = 0

export function ChatHistorySync() {
  const chat = useLuciusChat()
  const location = useLocation()
  const navigate = useNavigate()
  const { activeId, conversations } = chat
  const persisted = conversations.some((c) => c.id === activeId)
  const pathname = location.pathname
  const state = location.state as ChatHistoryState | undefined
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const entry = readEntry(pathname, (location.search as any)?.conversation, state)

  const live = useRef({ chat, entry, pathname, persisted })
  useLayoutEffect(() => {
    live.current = { chat, entry, pathname, persisted }
    setChatPathname(pathname)
  })
  const ownNavs = useRef(new Set<string>())
  const opening = useRef<string | null>(null)
  const previous = useRef({ activeId, persisted })
  const mounted = useRef(false)

  const write = (next: ChatEntry, replace: boolean) => {
    const nonce = `n${++navSeq}`
    ownNavs.current.add(nonce)
    const onLucius = live.current.pathname === LUCIUS_PATH
    return navigate({
      search: ((prev: Record<string, unknown>) =>
        onLucius ? { ...prev, conversation: next.conversation } : prev) as never,
      hash: true,
      state: ((prev: ChatHistoryState) => ({
        ...prev,
        luciusChat: next.chat,
        luciusNav: nonce,
      })) as never,
      replace,
    } as never)
  }

  // History landed on an entry (Back, Forward, a typed or opened link, arriving on the Lucius
  // page): open the chat it stands for. Keyed on the entry alone — a change of open chat must
  // never re-run this, or a switch would be undone before its own entry is written.
  const entryKey = `${location.href}|${(state as { __TSR_key?: string } | undefined)?.__TSR_key ?? ''}`
  useEffect(() => {
    const { chat, entry, pathname, persisted } = live.current
    if (state?.luciusNav && ownNavs.current.delete(state.luciusNav)) return
    const action = openFor({
      entry,
      activeId: chat.activeId,
      activePersisted: persisted,
      known: (id) => chat.conversations.some((c) => c.id === id),
    })
    if (action?.kind === 'switch') {
      opening.current = action.id
      chat.switchConversation(action.id)
    } else if (action?.kind === 'fresh') {
      opening.current = NEW_CHAT
      startFreshConversation(chat, { replaceHistory: true })
    } else if (pathname === LUCIUS_PATH) {
      const desired = entryFor(pathname, chat.activeId, persisted)
      if (!sameEntry(pathname, entry, desired)) void write(desired, true)
    }
    takeChatSwitch(pathname)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryKey])

  // The open chat changed: write it to history.
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    const desired = entryFor(pathname, activeId, persisted)
    const before = entryFor(pathname, previous.current.activeId, previous.current.persisted)
    previous.current = { activeId, persisted }
    const fromHistory = opening.current !== null
    opening.current = null
    const switched = takeChatSwitch(pathname) && !fromHistory && before.chat !== desired.chat
    const move = moveFor({ pathname, current: entry, desired, switched })
    if (!move) return
    if (move === 'replace') {
      void write(desired, true)
      return
    }
    // Stamp the entry being left with the chat it showed, so Back finds it, then add the new one.
    const stamp = sameEntry(pathname, entry, before) ? null : write(before, true)
    void Promise.resolve(stamp).then(() => write(desired, false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, persisted])

  return null
}
