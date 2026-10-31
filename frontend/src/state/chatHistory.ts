// Which Lucius conversation a browser history entry stands for, so Back and Forward move between
// chats the way they move between pages. On the Lucius page the open chat is the address
// (`/lucius?conversation=<id>`); in the side panel and the floating dock, which sit over another
// page, it rides in the entry's history state so the page's own address is left alone. A chat
// not yet written to has no id worth sharing, so its entry carries `NEW_CHAT` instead: Back to
// it starts a new chat rather than pointing at one the server has never heard of.

export const NEW_CHAT = 'new'
export const LUCIUS_PATH = '/lucius'

export interface ChatEntry {
  /** `?conversation=` — only ever set on the Lucius page. */
  conversation: string | undefined
  /** The chat stamped in the entry's history state, or undefined for an entry this never wrote. */
  chat: string | undefined
}

export interface ChatHistoryState {
  luciusChat?: string
  /** Marks an entry this module wrote, so the location change it causes is not read back as Back. */
  luciusNav?: string
}

export function readEntry(pathname: string, conversation: unknown, state: unknown): ChatEntry {
  const s = (state ?? {}) as ChatHistoryState
  return {
    conversation:
      pathname === LUCIUS_PATH && typeof conversation === 'string' && conversation
        ? conversation
        : undefined,
    chat: typeof s.luciusChat === 'string' && s.luciusChat ? s.luciusChat : undefined,
  }
}

/** The chat an entry stands for. On the Lucius page the address wins, so a typed or opened
 *  link is honoured over whatever the entry last held. */
export function chatOfEntry(entry: ChatEntry): string | undefined {
  return entry.conversation ?? entry.chat
}

/** The entry the open chat should have on this page. */
export function entryFor(pathname: string, activeId: string, persisted: boolean): ChatEntry {
  return {
    conversation: pathname === LUCIUS_PATH && persisted ? activeId : undefined,
    chat: persisted ? activeId : NEW_CHAT,
  }
}

export function sameEntry(pathname: string, a: ChatEntry, b: ChatEntry): boolean {
  if (a.chat !== b.chat) return false
  return pathname !== LUCIUS_PATH || a.conversation === b.conversation
}

export type HistoryMove = 'push' | 'replace' | null

/** How a change of open chat reaches history. A switch the person made gets its own entry; any
 *  other change (the first message giving a new chat its id, a deleted chat falling back to the
 *  next) rewrites the entry in place. Off the Lucius page an entry this never wrote is left
 *  alone unless the person switched — an Automation or a deletion shouldn't stamp the Dashboard. */
export function moveFor(args: {
  pathname: string
  current: ChatEntry
  desired: ChatEntry
  switched: boolean
}): HistoryMove {
  const { pathname, current, desired, switched } = args
  if (sameEntry(pathname, current, desired)) return null
  if (switched) return 'push'
  if (pathname === LUCIUS_PATH || current.chat !== undefined) return 'replace'
  return null
}

export type OpenAction = { kind: 'switch'; id: string } | { kind: 'fresh' } | null

/** What to open when history lands on an entry. An id from a typed or opened link is opened
 *  even when this browser hasn't listed it yet; an id stamped by an earlier switch is opened
 *  only while it still exists, so Back never resurrects a deleted chat. */
export function openFor(args: {
  entry: ChatEntry
  activeId: string
  activePersisted: boolean
  known: (id: string) => boolean
}): OpenAction {
  const { entry, activeId, activePersisted, known } = args
  const target = chatOfEntry(entry)
  if (!target) return null
  if (target === NEW_CHAT) return activePersisted ? { kind: 'fresh' } : null
  if (target === activeId) return null
  const fromLink = entry.conversation !== undefined && entry.conversation !== entry.chat
  return fromLink || known(target) ? { kind: 'switch', id: target } : null
}

// A switch is a navigation only when the person asked for it. The chat provider marks one at
// its switch/new-chat entry points; the sync reads it once, on the next change of open chat,
// and only if the page hasn't changed in between (a switch that also opens the Lucius page is
// already that page's history entry).
let routerPathname = ''
let pendingSwitch: { pathname: string } | null = null

/** The sync keeps this current, so a mark records the router's page, not the window's. */
export function setChatPathname(pathname: string): void {
  routerPathname = pathname
}

export function markChatSwitch(): void {
  pendingSwitch = { pathname: routerPathname }
}

export function takeChatSwitch(pathname: string): boolean {
  const mark = pendingSwitch
  pendingSwitch = null
  return !!mark && mark.pathname === pathname
}
