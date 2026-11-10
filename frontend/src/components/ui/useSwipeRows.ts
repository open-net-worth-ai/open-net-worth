// useSwipeRows — whether rows swipe for their actions (SwipeActions): a touch screen with no
// hover. Where it's false, a row shows its own hover controls instead.
import { useSyncExternalStore } from 'react'

export const SWIPE_ROWS_QUERY = '(hover: none) and (pointer: coarse)'

function subscribe(onChange: () => void) {
  const mq = window.matchMedia?.(SWIPE_ROWS_QUERY)
  mq?.addEventListener?.('change', onChange)
  return () => mq?.removeEventListener?.('change', onChange)
}

export function useSwipeRows(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => Boolean(window.matchMedia?.(SWIPE_ROWS_QUERY).matches),
    () => false,
  )
}
