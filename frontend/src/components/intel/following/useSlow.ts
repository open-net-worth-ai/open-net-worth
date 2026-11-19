import { useEffect, useState } from 'react'

/** Most loads here answer in a blink; only the first look at a source is slow. Explain the wait
 *  only once it is real, so a fast load never flashes a message. */
export const SLOW_AFTER_MS = 1500

/** True once `waiting` has stayed true for `ms`; false again the moment it stops. */
export function useSlow(waiting: boolean, ms = SLOW_AFTER_MS): boolean {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (!waiting) return
    const timer = setTimeout(() => setSlow(true), ms)
    return () => {
      clearTimeout(timer)
      setSlow(false)
    }
  }, [waiting, ms])
  return waiting && slow
}

/** Whole seconds since `active` turned true, ticking once a second; 0 while inactive. */
export function useElapsed(active: boolean): number {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!active) return
    const start = Date.now()
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => {
      clearInterval(timer)
      setSeconds(0)
    }
  }, [active])
  return seconds
}
