// Screen-aware context store — the CURRENT screen's snapshot (what the user is looking at) plus the
// on/off setting, exposed to ChatPanel (which attaches it to the chat turn) and the "Lucius can see
// this screen" transparency panel.
//
// A screen publishes its snapshot from its OWN view-model via useRegisterScreenSnapshot (structured,
// not DOM-scraped). The snapshot updates as the user navigates/scrolls; it's cleared on unmount.
// Design: docs/superpowers/specs/2026-06-24-screen-aware-context-design.md.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

// The view-model + wire shapes and the serializer now live in the pure lucius leaf
// (lucius/screenSnapshotWire.ts) so the lucius core can build the wire payload without importing
// host state. Re-exported here for backward compatibility — every existing importer is unchanged.
import { serializeSnapshot } from '../lucius/screenSnapshotWire'
import type { ScreenHandle, ScreenSnapshot, ScreenSnapshotWire } from '../lucius/screenSnapshotWire'
import {
  SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT,
  useScreenAwarenessDefault,
} from '../hooks/useChatSuggestionsEnabled'

export { serializeSnapshot }
export type { ScreenHandle, ScreenSnapshot, ScreenSnapshotWire }

const ENABLED_KEY = 'screen-awareness'

function storedChoice(stored: string | null): boolean | null {
  return stored === null ? null : stored !== 'off'
}

interface ScreenSnapshotValue {
  snapshot: ScreenSnapshot | null
  enabled: boolean
  setEnabled: (b: boolean) => void
  /** Handle ids the user tapped "+ attach" on — force-fetched on the next send, then cleared. */
  pinnedHandleIds: string[]
  togglePin: (id: string) => void
  clearPins: () => void
  /** Internal — screens publish via useRegisterScreenSnapshot, not this directly. */
  _setSnapshot: (s: ScreenSnapshot | null) => void
}

const ScreenSnapshotContext = createContext<ScreenSnapshotValue | null>(null)

export function ScreenSnapshotProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<ScreenSnapshot | null>(null)
  const [pinnedHandleIds, setPinned] = useState<string[]>([])
  // This device's own choice, null until it makes one: the account's default applies until then.
  const [deviceChoice, setDeviceChoice] = useState<boolean | null>(() =>
    storedChoice(localStorage.getItem(ENABLED_KEY)),
  )
  const { enabled: accountDefault } = useScreenAwarenessDefault()
  const enabled = deviceChoice ?? accountDefault

  // The toggle lives per browser, so another window changing it applies here too, at once.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === ENABLED_KEY) setDeviceChoice(storedChoice(e.newValue))
    }
    const onDefaultChanged = () => {
      setDeviceChoice(null)
      try {
        localStorage.removeItem(ENABLED_KEY)
      } catch {
        // blocked storage — ignore
      }
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT, onDefaultChanged)
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT, onDefaultChanged)
    }
  }, [])

  const setEnabled = useCallback((b: boolean) => {
    setDeviceChoice(b)
    try {
      localStorage.setItem(ENABLED_KEY, b ? 'on' : 'off')
    } catch {
      // quota — ignore
    }
  }, [])

  const togglePin = useCallback((id: string) => {
    setPinned((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }, [])
  const clearPins = useCallback(() => setPinned((prev) => (prev.length ? [] : prev)), [])

  const value = useMemo<ScreenSnapshotValue>(
    () => ({
      snapshot,
      enabled,
      setEnabled,
      pinnedHandleIds,
      togglePin,
      clearPins,
      _setSnapshot: setSnapshot,
    }),
    [snapshot, enabled, setEnabled, pinnedHandleIds, togglePin, clearPins],
  )
  return <ScreenSnapshotContext.Provider value={value}>{children}</ScreenSnapshotContext.Provider>
}

function useCtx(): ScreenSnapshotValue {
  const ctx = useContext(ScreenSnapshotContext)
  if (!ctx) throw new Error('useScreenSnapshot must be used within <ScreenSnapshotProvider>')
  return ctx
}

/** Read the current snapshot + toggle + pin API (ChatPanel + the transparency panel). */
export function useScreenSnapshot(): Omit<ScreenSnapshotValue, '_setSnapshot'> {
  const { _setSnapshot, ...rest } = useCtx()
  void _setSnapshot
  return rest
}

/**
 * Publish the active screen's snapshot. Call from a screen with its structured view-model data;
 * pass null when there's nothing to share. Re-publishes whenever the snapshot's content changes
 * (deep-compared) and clears on unmount, so "currently on screen" is always live and accurate.
 */
export function useRegisterScreenSnapshot(snapshot: ScreenSnapshot | null): void {
  // Tolerate a missing provider (a screen rendered standalone, e.g. in a unit test): screen
  // awareness is an optional enhancement, so no-op rather than throw — the screen just doesn't
  // publish. In the real app every screen renders inside <ScreenSnapshotProvider> (RouterShell).
  const ctx = useContext(ScreenSnapshotContext)
  const setSnapshot = ctx?._setSnapshot
  const key = snapshot ? JSON.stringify(snapshot) : ''
  useEffect(() => {
    if (!setSnapshot) return
    setSnapshot(snapshot)
    return () => setSnapshot(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setSnapshot])
}
