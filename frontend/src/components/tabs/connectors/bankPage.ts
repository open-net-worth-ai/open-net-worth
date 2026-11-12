// Opening a bank's consent page that the server returns only after an await. A window opened
// after the response falls outside the click's user activation, so browsers block it silently
// (Safari always; Chrome once the server took more than ~5 s — GoCardless's link is three round
// trips). So the tab is opened blank ON the click and pointed at the bank once the URL arrives,
// and the caller still shows the URL as a link: a blocked tab, a mini-window browser that hands
// back no handle, or an iOS home-screen app (which cannot steer a sheet it opened) all leave the
// user one tap from the bank. The desktop app hands the URL to the system browser instead.
import { authFetch } from '../../../api/client'
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

/** Where Enable Banking sends the user after consent. It accepts only a redirect registered on
 *  the user's application and compares it EXACTLY, query string included, so this is the bare
 *  page — the return is recognised by the `state` + `code` Enable Banking appends. */
export function enableBankingReturnUrl(): string {
  return `${window.location.origin}/connectors`
}

export interface EnableBankingReturn {
  state: string
  code: string
  error: string
}

/** An Enable Banking return in the page's query: `?state=…&code=…` after consent, or
 *  `?state=…&error=…` when the user cancelled or the bank refused. Null for anything else. */
export function enableBankingReturnOf(search: Record<string, unknown>): EnableBankingReturn | null {
  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  const state = text(search.state)
  const code = text(search.code)
  const error = text(search.error_description) || text(search.error)
  if (!state || (!code && !error) || search.connector !== undefined) return null
  return { state, code, error }
}

/** Finish the link the bank just returned from. The server checks `state` against the link it
 *  started, so a stale or foreign return is refused there. Returns the message to show. */
export async function completeEnableBankingReturn(
  ret: EnableBankingReturn,
): Promise<{ ok: boolean; message: string }> {
  if (!ret.code) return { ok: false, message: `The bank link didn't finish: ${ret.error}` }
  try {
    const r = await authFetch('/api/enable-banking/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: ret.code, state: ret.state }),
    })
    if (r.ok) return { ok: true, message: 'Bank linked. Add its accounts from the Accounts list.' }
    const d = await r.json().catch(() => ({}))
    return { ok: false, message: d.detail || 'Could not complete the bank link.' }
  } catch {
    return { ok: false, message: 'Could not complete the bank link.' }
  }
}

// A failed Enable Banking return opens the connector's form, which shows the reason under
// Link bank — a toast alone vanished before it was read, over a page with nothing linked.
const EB_RETURN_ERROR = 'onw:enable-banking:return-error'

export function keepEnableBankingReturnError(message: string): void {
  try {
    sessionStorage.setItem(EB_RETURN_ERROR, message)
  } catch {
    // storage unavailable — the toast still says it
  }
}

/** The failed return's message, once: reading it clears it. */
export function takeEnableBankingReturnError(): string | null {
  try {
    const message = sessionStorage.getItem(EB_RETURN_ERROR)
    sessionStorage.removeItem(EB_RETURN_ERROR)
    return message
  } catch {
    return null
  }
}
