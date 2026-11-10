// The popup + poll controller behind every MCP OAuth sign-in (RFC `2026-08-14-mcp-oauth`
// §4.2/§4.3) — ONE implementation shared by the add flow's Sign-in button (`McpAddFlow.tsx`,
// driving `McpConnectionFields.tsx`'s status area) and the Reconnect action for a dead sign-in
// (`ConnectorsTab.tsx`). Both need the exact same dance: open a window, hand it the authorize
// URL once `begin()` answers, then learn how it finished.
//
// TWO independent signals, because a popup can be blocked and a user can finish the vendor's
// consent screen on an entirely different device (RFC §4.2 "Learning it finished"):
//   * a `postMessage` from the callback page (`backend/api/mcp_oauth_api.py`'s `_CALLBACK_HTML`)
//     — the FAST path, ignored if it never arrives;
//   * a poll of `GET /api/mcp-clients/oauth/{flow_id}` every `POLL_MS` — the AUTHORITATIVE path,
//     the one thing this controller actually trusts to finish.
// The callback's message is `{source: "onw-mcp-oauth"}` at `window.location.origin` (this
// app's own origin — never a caller-supplied one, so there is nothing to spoof cross-origin);
// receiving it just triggers an immediate poll tick rather than being treated as the answer
// itself, since it carries no outcome of its own.

import { beginOAuth, pollOAuth, type OAuthBeginBody } from './mcpApi'
import { desktopBridge } from '../../../platform/desktop'

const POLL_MS = 1500
const POPUP_NAME = 'onw-mcp-oauth'
const POPUP_FEATURES = 'width=520,height=680'

export interface OAuthOutcome {
  ok: boolean
  /** `begin()`'s reason code, when the failure came from `begin()` itself — drives the extra
   *  affordances (`no_registration`'s Client ID field, `dcr_rejected`'s redirect-URI list).
   *  `null` for a poll-time failure or a client-side-only reason (`popup_closed`). */
  reason: string | null
  /** Always present on failure — see `mcpApi.ts::OAuthBeginResult`'s doc comment for why this
   *  is rendered verbatim rather than re-mapped from `reason`. */
  error: string | null
  needsClientId: boolean
  redirectUris: string[]
}

/** Open a blank popup SYNCHRONOUSLY, before any `await` — `beginOAuth`'s fetch is async, and a
 *  `window.open` call made only after it resolves falls outside the "direct response to a click"
 *  window most browsers require, so it would be blocked far more often. Navigated to the real
 *  `authorize_url` once `begin()` answers (a well-known technique for keeping an async-gated
 *  popup out of the blocker's crosshairs). */
function openBlankPopup(): Window | null {
  try {
    return window.open('about:blank', POPUP_NAME, POPUP_FEATURES)
  } catch {
    return null
  }
}

/** Open the sign-in window NOW, on the user's click, for a sign-in that starts a moment later on
 *  another screen (Lucius's setup card: "Connect" → Connectors → the add flow). The flow's own
 *  `window.open` then reuses this window by its name instead of opening a new one — which the
 *  popup blocker would refuse, being no longer a direct response to the click. The desktop app
 *  signs in through the system browser, so it opens nothing here. */
export function preopenSignInWindow(): void {
  if (!desktopBridge()) openBlankPopup()
}

export interface OAuthSignInHandle {
  /** Stop polling and close the popup. Sends no request — the staged flow is simply left to
   *  expire on its own 10-minute TTL (`oauth_pending.TTL_SECONDS`); there is no backend
   *  "abandon" endpoint, and minting one to save a slot in an in-memory, already-bounded
   *  (`MAX_OUTSTANDING`) map is not worth a new attacker-reachable route. */
  cancel: () => void
}

/**
 * Start a sign-in: open the popup, call `begin()`, then race a poll against a `postMessage`
 * until the flow resolves. `onOutcome` fires exactly once — on success, on any `begin()`
 * failure, on a poll `status: 'error'` (an expired flow included), or on a popup we hold a
 * handle to being closed before the flow finished. Never fires after `cancel()`.
 * `onAuthorizeUrl` fires once `begin()` answers (browser only), with whether we hold a handle
 * to the window — the caller's fallback link for a window it can't see.
 */
export function startOAuthSignIn(
  body: OAuthBeginBody,
  onOutcome: (outcome: OAuthOutcome) => void,
  onAuthorizeUrl?: (url: string, windowOpened: boolean) => void,
): OAuthSignInHandle {
  const desktop = desktopBridge()
  let popup = desktop ? null : openBlankPopup()
  let cancelled = false
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let messageListener: ((e: MessageEvent) => void) | null = null
  let polling = false

  function stop() {
    if (pollTimer !== null) clearInterval(pollTimer)
    pollTimer = null
    if (messageListener) window.removeEventListener('message', messageListener)
    messageListener = null
  }

  function finish(outcome: OAuthOutcome) {
    if (cancelled) return
    cancelled = true // finish() is terminal — guards a straggling tick from firing twice
    stop()
    popup?.close()
    onOutcome(outcome)
  }

  function fail(reason: string, error: string) {
    finish({ ok: false, reason, error, needsClientId: false, redirectUris: [] })
  }

  async function tick(flowId: string) {
    if (cancelled || polling) return
    if (popup && popup.closed) {
      fail('popup_closed', 'The sign-in window was closed before finishing.')
      return
    }
    polling = true
    const poll = await pollOAuth(flowId)
    polling = false
    if (cancelled) return
    if (poll.status === 'pending') return
    finish({
      ok: poll.status === 'ok',
      reason: null,
      error: poll.status === 'error' ? poll.error : null,
      needsClientId: false,
      redirectUris: [],
    })
  }

  void (async () => {
    const result = await beginOAuth(body)
    if (cancelled) return
    if (!result.ok) {
      finish({
        ok: false,
        reason: result.reason ?? null,
        error: result.error ?? 'Could not start the sign-in.',
        needsClientId: !!result.needs_client_id,
        redirectUris: result.redirect_uris ?? [],
      })
      return
    }
    const flowId = result.flow_id
    const authorizeUrl = result.authorize_url
    if (!flowId || !authorizeUrl) {
      fail('bad_response', 'The sign-in could not be started.')
      return
    }
    if (desktop) {
      try {
        await desktop.openExternal(authorizeUrl)
      } catch {
        fail('browser_launch_failed', 'Could not open your browser for sign-in. Try again.')
        return
      }
    } else if (popup) popup.location.href = authorizeUrl
    else popup = window.open(authorizeUrl, POPUP_NAME, POPUP_FEATURES)
    // A `null` handle is NOT proof the window was blocked: Arc/Dia open the popup in their own
    // mini-window and still return `null`. Failing here abandoned a sign-in the user went on to
    // finish (the callback persisted the server; nothing was listening). So keep polling — the
    // poll is the authoritative signal and ends on its own when the staged flow expires — and
    // hand the URL to the caller so it can offer it as a link.
    if (!desktop) onAuthorizeUrl?.(authorizeUrl, !!popup)

    messageListener = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      const data = e.data as { source?: unknown } | null
      if (!data || data.source !== 'onw-mcp-oauth') return
      void tick(flowId) // the fast path — poll immediately rather than waiting out the interval
    }
    window.addEventListener('message', messageListener)
    pollTimer = setInterval(() => void tick(flowId), POLL_MS)
    void tick(flowId) // also fire once immediately, in case the flow already finished
  })()

  return {
    cancel: () => {
      cancelled = true
      stop()
      popup?.close()
    },
  }
}

/** Promise-wrapped convenience for a fire-and-forget caller with no live UI to drive
 *  (`ConnectorsTab.tsx`'s Reconnect) — same controller, no mid-flight cancel exposed. */
export function signInWithOAuth(body: OAuthBeginBody): Promise<OAuthOutcome> {
  return new Promise((resolve) => {
    startOAuthSignIn(body, resolve)
  })
}
