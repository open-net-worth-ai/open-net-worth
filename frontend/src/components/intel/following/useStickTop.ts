import { useEffect, useLayoutEffect, useState } from 'react'

/** Where a pinned block's top sits: just under the app's top bar, measured from the block's own
 *  scroll area (the page on a desktop, the content pane on a phone), since the bar's height
 *  differs by platform (52px, 48px in the Mac app). Measured after every render, not once: the
 *  block may not exist on the first one (a page opens on its skeleton), and a measure that ran
 *  then left it pinned at 0, under the bar. Setting the same value again does not re-render. */
export function useStickTop(ref: React.RefObject<HTMLElement | null>): number {
  const [top, setTop] = useState(0)
  const measure = () => {
    const el = ref.current
    const bar = document.querySelector<HTMLElement>('.topbar')
    if (!el || !bar) return
    let scroller: HTMLElement | null = el.parentElement
    while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY))
      scroller = scroller.parentElement
    const from = scroller ? scroller.getBoundingClientRect().top : 0
    setTop(Math.max(0, Math.round(bar.getBoundingClientRect().bottom - from)))
  }
  useLayoutEffect(measure)
  useEffect(() => {
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- measure reads only the ref and the DOM
  }, [])
  return top
}
