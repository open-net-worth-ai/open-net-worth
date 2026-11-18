// The OAuth path's data (flag mcp_oauth): the server status and what the page says about it.
// Components live in McpOAuthPanel.tsx and McpOAuthPane.tsx.
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'

export interface OAuthApp {
  grant_id: string
  name: string
  host: string
  scopes: string[]
  connected_at: string
  last_used_at: string | null
}

export interface OAuthClient {
  client_id: string
  name: string
  redirect_uris: string[]
  created_at: string
}

export interface OAuthStatus {
  available: boolean
  reasons: string[]
  public_url: string
  public_url_source: 'env' | 'stored'
  mcp_url: string
  apps: OAuthApp[]
  clients: OAuthClient[]
}

export const KEY = '/api/mcp/oauth'

export const REASONS: Record<string, string> = {
  flag_off: 'The feature is off.',
  no_public_url: 'Set the public HTTPS address below.',
  invalid_public_url: 'The public address must be https:// and reachable from the internet.',
  no_password: 'Set a password in Settings → Security — the consent page asks for it.',
  auth_disabled: 'Sign-in is turned off on this instance.',
  loopback_trusted:
    'In Settings → Security, turn off "Don\'t ask for the password on this computer": behind a proxy or tunnel every visitor looks like this computer.',
  demo: 'Not available on the public demo.',
  gateway: 'Not available behind the Open Net Worth gateway yet.',
  desktop: 'The desktop app only accepts connections from this computer.',
}

async function loadStatus(): Promise<OAuthStatus | null> {
  const r = await authFetch(KEY)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as OAuthStatus
}

/** The OAuth server's status; ``null`` while the flag is off (the call answers 404). */
export function useOAuthStatus(): { status: OAuthStatus | null; refresh: () => void } {
  const { data, refresh } = useCachedFetchState<OAuthStatus | null>(KEY, loadStatus, {
    staleTime: 0,
  })
  return { status: data ?? null, refresh: () => void refresh() }
}

/** Reasons nothing on this instance's settings can fix: the path just isn't offered here. */
export const BLOCKERS = new Set(['demo', 'gateway', 'desktop'])

/** What the OAuth card says about its path. */
export function oauthState(status: OAuthStatus | null): { label: string; tone: 'good' | 'warn' } {
  if (!status) return { label: 'Not set up', tone: 'warn' }
  if (status.available) return { label: 'Ready', tone: 'good' }
  if (status.reasons.some((r) => BLOCKERS.has(r)))
    return { label: 'Not available here', tone: 'warn' }
  const steps = new Set(
    status.reasons.map((r) => (r === 'invalid_public_url' ? 'no_public_url' : r)),
  )
  const n = steps.size
  return { label: n === 1 ? '1 step left' : `${n} steps left`, tone: 'warn' }
}

const LOOPBACK = /^(127\.0\.0\.1|localhost|\[::1\])$/

/** The page's own address, when it is one the internet could reach: the owner is looking at it
 * in their address bar, so it is a safe suggestion. It is only ever saved by the owner's Save;
 * the server never takes its address from a request. */
export function suggestedPublicUrl(
  loc: Pick<Location, 'protocol' | 'hostname' | 'origin'> = window.location,
): string {
  return loc.protocol === 'https:' && !LOOPBACK.test(loc.hostname) ? loc.origin : ''
}
