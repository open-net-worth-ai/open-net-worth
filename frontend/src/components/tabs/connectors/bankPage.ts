// Opening a bank's consent page that the server returns only after an await. A window opened
// after the response falls outside the click's user activation, so browsers block it silently
// (Safari always; Chrome once the server took more than ~5 s — GoCardless's link is three round
// trips). So the tab is opened blank ON the click and pointed at the bank once the URL arrives,
// and the caller still shows the URL as a link: a blocked tab, a mini-window browser that hands
// back no handle, or an iOS home-screen app (which cannot steer a sheet it opened) all leave the
// user one tap from the bank. The desktop app hands the URL to the system browser instead.
import { desktopBridge, openExternal } from '../../../platform/desktop'

export interface BankPageTab {
  /** Send the pre-opened tab to the bank (or open it, on desktop). True if a tab was steered. */
  go(url: string): boolean
  /** The link never came — close the blank tab rather than strand it. */
  cancel(): void
}

function iosHomeScreenApp(): boolean {
  return (navigator as Navigator & { standalone?: boolean }).standalone === true
}

/** Call synchronously inside the click handler, before any `await`. */
export function openBankPageTab(): BankPageTab {
  if (desktopBridge()) {
    return {
      go: (url) => {
        void openExternal(url)
        return true
      },
      cancel: () => {},
    }
  }
  let tab: Window | null = null
  if (!iosHomeScreenApp()) {
    try {
      tab = window.open('', '_blank')
      if (tab) tab.opener = null
    } catch {
      tab = null
    }
  }
  return {
    go: (url) => {
      if (!tab || tab.closed) return false
      try {
        tab.location.href = url
        return true
      } catch {
        return false
      }
    },
    cancel: () => {
      try {
        tab?.close()
      } catch {
        // already gone
      }
    },
  }
}
