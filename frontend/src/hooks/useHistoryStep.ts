// useHistoryStep — an in-page step (an overlay, a page inside a page) that Back undoes.
//
// Opening the step pushes a history entry with the SAME URL and the step in its state, so the
// phone's back gesture, the browser's Back and the desktop app's Back all close it first,
// instead of leaving the page underneath (owner report, 2026-10-28: Memory opened from the
// Lucius tab, then Back, landed on the page before the Lucius tab). History state, not a search
// param: routes validate their search and would strip an unknown one. Closing from the UI goes
// back through history too, so the entry never outlives what it shows. The router's history
// arrives through HistoryStepContext (RouterShell provides it); without one — a component test,
// which mocks the router — it is plain local state.
import { createContext, useCallback, useContext, useState, useSyncExternalStore } from 'react'
import type { RouterHistory } from '@tanstack/history'

export const HistoryStepContext = createContext<RouterHistory | null>(null)

type State = Record<string, unknown>

function stateOf(history: RouterHistory): State {
  return (history.location.state ?? {}) as unknown as State
}

const ROUTER_KEYS = new Set(['key', '__TSR_key', '__TSR_index'])

/** The state an entry carries beyond the router's own bookkeeping. */
function appState(history: RouterHistory): State {
  return Object.fromEntries(Object.entries(stateOf(history)).filter(([k]) => !ROUTER_KEYS.has(k)))
}

export interface HistoryStep<T> {
  value: T | null
  open: (value: T) => void
  close: () => void
}

export function useHistoryStep<T>(name: string): HistoryStep<T> {
  const history = useContext(HistoryStepContext)
  const [local, setLocal] = useState<T | null>(null)
  const subscribe = useCallback(
    (onChange: () => void) => (history ? history.subscribe(onChange) : () => {}),
    [history],
  )
  const fromHistory = useSyncExternalStore(subscribe, () =>
    history ? ((stateOf(history)[name] as T | undefined) ?? null) : null,
  )
  const open = useCallback(
    (value: T) => {
      if (!history) return setLocal(value)
      history.push(history.location.href, { ...appState(history), [name]: value })
    },
    [history, name],
  )
  const close = useCallback(() => {
    if (!history) return setLocal(null)
    if (stateOf(history)[name] != null) history.back()
  }, [history, name])
  return { value: history ? fromHistory : local, open, close }
}

/** Leave every step in ``names`` that is open, in one jump back (a window and the page open in
 *  it close together). Steps are pushed in order, so the open ones are the newest entries.
 *  Returns false when there is no router, so the caller closes its own local step. */
export function useLeaveSteps(names: string[]): () => boolean {
  const history = useContext(HistoryStepContext)
  const key = names.join('|')
  return useCallback(() => {
    if (!history) return false
    const open = key.split('|').filter((n) => stateOf(history)[n] != null).length
    if (open) history.go(-open)
    return true
  }, [history, key])
}
