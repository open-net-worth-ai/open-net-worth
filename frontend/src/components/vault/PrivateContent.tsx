import type { ReactNode } from 'react'

/** What a Private file shows on screen stays out of every screen snapshot, as it stays out of
 *  every other model read: a text or table preview is real text the capture would otherwise
 *  send. `display: contents` so the boundary leaves the viewer's own layout alone. */
export function PrivateContent({ hidden, children }: { hidden: boolean; children: ReactNode }) {
  if (!hidden) return <>{children}</>
  return (
    <div data-ctx-secret="" style={{ display: 'contents' }}>
      {children}
    </div>
  )
}
