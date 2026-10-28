// useLuciusTab — owns the DESKTOP dock's "is the Lucius side panel open", plus its persistence
// (`chat-open` in localStorage, so a reload lands on the same surface).
//
// On a phone this flag is never read. Lucius there is the `/lucius` route — a real tab, the
// same as Overview or Accounts — and "am I looking at Lucius" is answered by the route alone
// (`RouterShell`'s `mobileNav` is `activeTab`, nothing else). Leaving Lucius is leaving a
// route, so every navigation path — the tab bar, the More sheet, the command palette, any
// in-tab "go to …" link, the browser's back — closes it structurally, with no rule anywhere
// to forget. See docs/adr/2026-09-05-phone-lucius-is-the-lucius-route.md for how the phone
// got from a sheet's boolean to a route (and why the desktop dock keeps this flag: it sits
// BESIDE the content and stays open across navigation on purpose).

import { useCallback, useState } from 'react'

const KEY = 'chat-open'

export type LuciusTabState = readonly [
  boolean,
  (next: boolean | ((prev: boolean) => boolean)) => void,
]

export function isLuciusDockVisible(open: boolean, isMobile: boolean, tabId: string): boolean {
  return open && !isMobile && tabId !== 'lucius'
}

/** The desktop's Cmd/Ctrl+N: a new conversation where Lucius already is when the side panel
 *  is showing, otherwise on the Lucius screen. */
export function routeNewConversation(
  panelVisible: boolean,
  surfaces: { startFresh: () => void; focusPanel: () => void; openLuciusTab: () => void },
): void {
  surfaces.startFresh()
  if (panelVisible) surfaces.focusPanel()
  else surfaces.openLuciusTab()
}

export function useLuciusTab(): LuciusTabState {
  const [open, setOpenState] = useState(() => localStorage.getItem(KEY) === 'true')

  const setOpen = useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setOpenState((prev) => {
      const value = typeof next === 'function' ? next(prev) : next
      localStorage.setItem(KEY, String(value))
      return value
    })
  }, [])

  return [open, setOpen] as const
}
