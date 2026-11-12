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

/** Where GoCardless sends the user after consent: back to its own connector page, which lists
 *  the bank's accounts to link — not the app's home page, with no hint of the next step. */
export function bankReturnUrl(): string {
  return `${window.location.origin}/connectors?connector=gocardless`
}

/** What to ask a provider when its bank sends the user back, before the connector page shows:
 *  without it the page reads the bank as still waiting (an Error badge) until something else
 *  happens to re-read it. A failure is ignored — the page then shows what it knows. */
export const BANK_RETURN_CHECKS: Record<string, () => Promise<unknown>> = {
  gocardless: () => authFetch('/api/gocardless/check-link', { method: 'POST' }),
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
    if (r.ok) return { ok: true, message: 'Bank linked. Link its accounts below.' }
    const d = await r.json().catch(() => ({}))
    return { ok: false, message: d.detail || 'Could not complete the bank link.' }
  } catch {
    return { ok: false, message: 'Could not complete the bank link.' }
  }
}
