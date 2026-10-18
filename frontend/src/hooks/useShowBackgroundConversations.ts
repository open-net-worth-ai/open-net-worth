import { useSyncExternalStore } from 'react'

// Whether the conversation lists include background conversations (a Shortcut / Apple Pay
// capture's booking turn — `origin` on the session). Per device, like the sidebar's collapsed
// state: it is a "let me look something up" switch, not a setting to sync. Held in memory
// too, so the toggle still works where storage throws (private window, blocked site data).
const KEY = 'lucius_show_background_conversations'
const listeners = new Set<() => void>()

function readStored(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

let current = readStored()

export function setShowBackgroundConversations(on: boolean): void {
  current = on
  try {
    if (on) localStorage.setItem(KEY, '1')
    else localStorage.removeItem(KEY)
  } catch {
    /* storage unavailable — the in-memory value still drives this session */
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useShowBackgroundConversations(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(
    subscribe,
    () => current,
    () => false,
  )
  return [on, setShowBackgroundConversations]
}
