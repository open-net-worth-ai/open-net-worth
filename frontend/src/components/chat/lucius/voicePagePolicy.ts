// A page keeps the Content-Security-Policy it loaded with (`core/spa_csp.py`) and cannot loosen it,
// and the app changes screens without loading a page. So a page opened before the owner saved a
// self-hosted LiveKit server, or before the flag was turned on, is refused that server until it
// reloads: a new install's first call failed with LiveKit's "could not establish signal
// connection: Failed to fetch", and only a reload cured it (reported by a tester, 2026-11-05).

const RELOAD_KEY = 'onw_voice_policy_reload'

export const page = { reload: () => window.location.reload() }

/** The policy's sources for a server: LiveKit Cloud as a whole (also what a page has with no server
 *  saved), the exact server otherwise. Mirrors `voice_connect_sources` in `core/spa_csp.py`. */
function allowance(url: string): string {
  try {
    const { protocol, host, hostname } = new URL(url.trim())
    const name = hostname.toLowerCase()
    return name === 'livekit.cloud' || name.endsWith('.livekit.cloud')
      ? 'cloud'
      : `${protocol}//${host}`
  } catch {
    return 'cloud'
  }
}

/** Whether saving this address leaves the open page with the wrong policy. */
export function saveNeedsReload(before: string, after: string): boolean {
  return allowance(before) !== allowance(after)
}

/** Notices the browser refusing a connection while a call starts. */
export function watchPolicyBlock(): { blocked: () => boolean; stop: () => void } {
  let seen = false
  const onViolation = (e: SecurityPolicyViolationEvent) => {
    if (e.effectiveDirective === 'connect-src') seen = true
  }
  document.addEventListener('securitypolicyviolation', onViolation)
  return {
    blocked: () => seen,
    stop: () => document.removeEventListener('securitypolicyviolation', onViolation),
  }
}

/** Reloads once per tab until a call connects, so a refusal a reload can't cure (a policy from a
 *  proxy in front of the app) can't loop. True when the page is on its way out. Storage we can't
 *  write can't hold that promise, so it doesn't reload. */
export function reloadForPolicy(): boolean {
  try {
    if (sessionStorage.getItem(RELOAD_KEY)) return false
    sessionStorage.setItem(RELOAD_KEY, 'pending')
  } catch {
    return false
  }
  page.reload()
  return true
}

/** True once, on the page that reload opened: audio needs a fresh tap, so it can't redial itself. */
export function takeReloadNotice(): boolean {
  try {
    if (sessionStorage.getItem(RELOAD_KEY) !== 'pending') return false
    sessionStorage.setItem(RELOAD_KEY, 'shown')
    return true
  } catch {
    return false
  }
}

export function clearPolicyReload(): void {
  try {
    sessionStorage.removeItem(RELOAD_KEY)
  } catch {
    // Nothing was remembered.
  }
}
