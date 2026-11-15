// What /oauth/consent shows and sends (see ConsentPage). The page has no session: the request id
// in the URL names the staged sign-in, and the password typed on the page is the credential.

export interface ConsentDetails {
  app_name: string
  app_host: string
  redirect_host: string
  requested_scopes: string[]
  expires_at: string
}

export type DecideResult =
  | { ok: true; redirect: string }
  | { ok: false; reason: 'password' | 'throttled' | 'expired' | 'failed' }

export async function loadConsent(request: string): Promise<ConsentDetails> {
  const r = await fetch(`/api/oauth/consent?request=${encodeURIComponent(request)}`, {
    credentials: 'same-origin',
  })
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as ConsentDetails
}

export async function decideConsent(body: {
  request: string
  decision: 'allow' | 'deny'
  password?: string
  allow_write?: boolean
}): Promise<DecideResult> {
  const r = await fetch('/api/oauth/consent', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (r.ok) return { ok: true, redirect: ((await r.json()) as { redirect: string }).redirect }
  if (r.status === 401) return { ok: false, reason: 'password' }
  if (r.status === 429) return { ok: false, reason: 'throttled' }
  if (r.status === 404) return { ok: false, reason: 'expired' }
  return { ok: false, reason: 'failed' }
}
