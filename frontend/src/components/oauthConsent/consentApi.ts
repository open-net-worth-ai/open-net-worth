// What /oauth/consent shows and sends (see ConsentPage). The request id in the URL names the
// staged sign-in; approving needs a password typed in the last few minutes, through the ordinary
// sign-in, so the page signs the owner in first when `signed_in` is false.
import { tooManyAttemptsMessage } from '../../api/rateLimit'

export interface ConsentDetails {
  app_name: string
  app_host: string
  redirect_host: string
  requested_scopes: string[]
  expires_at: string
  signed_in: boolean
}

export type DecideResult =
  | { ok: true; redirect: string }
  | { ok: false; reason: 'signin' | 'expired' | 'failed' }

export type SignInResult = { ok: true } | { ok: false; message: string }

export async function loadConsent(request: string): Promise<ConsentDetails> {
  const r = await fetch(`/api/oauth/consent?request=${encodeURIComponent(request)}`, {
    credentials: 'same-origin',
  })
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as ConsentDetails
}

export async function signIn(password: string): Promise<SignInResult> {
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    if (r.ok) return { ok: true }
    if (r.status === 401) return { ok: false, message: 'That password is not right.' }
    if (r.status === 429) return { ok: false, message: tooManyAttemptsMessage(r) }
    return { ok: false, message: `Sign-in failed (HTTP ${r.status}).` }
  } catch {
    return { ok: false, message: 'Could not reach Open Net Worth. Try again.' }
  }
}

export async function decideConsent(body: {
  request: string
  decision: 'allow' | 'deny'
  allow_write?: boolean
}): Promise<DecideResult> {
  const r = await fetch('/api/oauth/consent', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (r.ok) return { ok: true, redirect: ((await r.json()) as { redirect: string }).redirect }
  if (r.status === 401) return { ok: false, reason: 'signin' }
  if (r.status === 404) return { ok: false, reason: 'expired' }
  return { ok: false, reason: 'failed' }
}
