import './following.css'

import { useLayoutEffect, useRef } from 'react'
import { ErrorState, TabSkeleton } from '../../ui'
import { COPY } from './following.copy'
import { Discover } from './Discover'
import { PortfolioPage } from './PortfolioPage'
import { useFollowList } from './useFollowing'
import { useOpenPortfolio } from './useOpenPortfolio'

interface Props {
  onAskLucius?: (text: string) => void
}

/** The element that scrolls this screen: the app's pane (it scrolls inside .app-layout, not the
 *  window), found as the nearest ancestor that scrolls. */
function scrollerOf(el: HTMLElement | null): HTMLElement | null {
  for (let e = el?.parentElement; e; e = e.parentElement) {
    const y = getComputedStyle(e).overflowY
    if ((y === 'auto' || y === 'scroll') && e.scrollHeight > e.clientHeight) return e
  }
  return document.scrollingElement as HTMLElement | null
}

/** Intelligence › Copy Trading: browse the cards, open one, copy it. */
export function FollowingTab({ onAskLucius }: Props) {
  const list = useFollowList()
  const { open, setOpen } = useOpenPortfolio()
  // A portfolio opens at its top, not at the list's scroll position; going back puts the list
  // where it was, so the reader keeps their place among the cards.
  const rootRef = useRef<HTMLDivElement>(null)
  const listScroll = useRef(0)
  const openKey = open ? `${open.kind}:${open.source_ref}` : null
  const lastKey = useRef<string | null>(null)
  useLayoutEffect(() => {
    const scroller = scrollerOf(rootRef.current)
    if (scroller && openKey !== lastKey.current) {
      if (openKey && !lastKey.current) listScroll.current = scroller.scrollTop
      scroller.scrollTop = openKey ? 0 : listScroll.current
    }
    lastKey.current = openKey
  }, [openKey])

  if (list.initialLoading)
    return <TabSkeleton variant="holdings" label={COPY.loadingList} header={false} />
  if (!list.data)
    return (
      <ErrorState
        title={COPY.listErrorTitle}
        message={COPY.listErrorBody}
        onRetry={() => void list.refresh()}
      />
    )
  if (open)
    return (
      <div ref={rootRef}>
        <PortfolioPage
          portfolio={open}
          onBack={() => setOpen(null)}
          onChanged={list.refresh}
          onAskLucius={onAskLucius}
        />
      </div>
    )
  const watched = list.data.follows.map((f) => ({
    kind: f.kind,
    source_ref: f.source_ref,
    name: f.name,
    subtitle: f.subtitle,
  }))
  return (
    <div ref={rootRef} className="fol-page">
      <Discover watched={watched} onOpen={setOpen} />
    </div>
  )
}
