import { useLayoutEffect, useRef, useState } from 'react'

/** Whether the element is narrower than ``px``, by the width it actually has (a tablet, a pane
 *  beside Lucius), not the screen's. A toolbar switches to its compact one-row form with it. */
export function useNarrow<T extends HTMLElement>(px: number) {
  const ref = useRef<T>(null)
  const [narrow, setNarrow] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => setNarrow(el.clientWidth < px)
    check()
    const ro = new ResizeObserver(check)
    ro.observe(el)
    return () => ro.disconnect()
  }, [px])
  return [ref, narrow] as const
}
