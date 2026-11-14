// Opening a bank's consent page that the server returns only after an await. A window opened
// after the response falls outside the click's user activation, so browsers block it silently
// (Safari always; Chrome once the server took more than ~5 s — GoCardless's link is three round
// trips). So the tab is opened blank ON the click and pointed at the bank once the URL arrives,
// and the caller still shows the URL as a link: a blocked tab, a mini-window browser that hands
// back no handle, or an iOS home-screen app (which cannot steer a sheet it opened) all leave the
// user one tap from the bank. The desktop app hands the URL to the system browser instead.
import { useSyncExternalStore } from 'react'
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

// Back from a bank, the app finishes the link (a second or two) with the connector's page
// ALREADY open, showing that it is finishing — not on the bare Connectors list with nothing
// happening (owner, 2026-11-14). The connector screen sets it; the provider's page reads it.
let finishing: string | null = null
const finishingListeners = new Set<() => void>()

/** The provider whose bank link is being finished now, or null when it's done. */
export function setFinishingBankLink(provider: string | null): void {
  finishing = provider
  finishingListeners.forEach((l) => l())
}

/** Whether `provider`'s bank link is still being finished. */
export function useFinishingBankLink(provider: string): boolean {
  return useSyncExternalStore(
    (l) => {
      finishingListeners.add(l)
      return () => finishingListeners.delete(l)
    },
    () => finishing === provider,
  )
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

/** Where Enable Banking sends the user after consent. The path marks the return as Enable
 *  Banking's (a bare `state` + `code` is what any OAuth-style redirect carries, so it alone never
 *  finishes a link); the router turns it into `/connectors?connector=enable_banking&…`. A path,
 *  not a query: Enable Banking refuses to register a redirect URL with query parameters, and
 *  compares the one it holds exactly — so this is the URL the setup form shows to register. */
export function enableBankingReturnUrl(): string {
  return `${window.location.origin}/connectors/callback/${ENABLE_BANKING}`
}

const ENABLE_BANKING = 'enable_banking'

export interface EnableBankingReturn {
  state: string
  code: string
  error: string
}

/** An Enable Banking return, as the `/connectors/callback/enable_banking` route hands it on:
 *  `?connector=enable_banking&state=…&code=…` after consent, or `…&state=…&error=…` when the
 *  user cancelled or the bank refused. Null for anything else — including a `state` + `code`
 *  without the marker. A `?…` appended after one already there lands inside `connector`, so
 *  that is read too. */
export function enableBankingReturnOf(search: Record<string, unknown>): EnableBankingReturn | null {
  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  const [id, tail = ''] = text(search.connector).split('?', 2)
  if (id !== ENABLE_BANKING) return null
  const extra = Object.fromEntries(new URLSearchParams(tail))
  const param = (k: string) => text(search[k]) || text(extra[k])
  const state = param('state')
  const code = param('code')
  const error = param('error_description') || param('error')
  if (!state || (!code && !error)) return null
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

/** The failed return's message, if one is waiting. Reading never clears it (a render may run
 *  twice); `clearEnableBankingReturnError` does, once the form has shown it. */
export function peekEnableBankingReturnError(): string | null {
  try {
    return sessionStorage.getItem(EB_RETURN_ERROR)
  } catch {
    return null
  }
}

export function clearEnableBankingReturnError(): void {
  try {
    sessionStorage.removeItem(EB_RETURN_ERROR)
  } catch {
    // storage unavailable — nothing was kept
  }
}
