// Plaid's bank-link flow (Hosted Link, no frontend Link-SDK). `start()` asks the server for a
// Hosted Link URL and opens it in the browser. Hosted Link ends on Plaid's own "you can close
// this window" page and never shows a public token, so this then polls
// POST /api/plaid/link/complete — the server reads the session's public token back from Plaid
// and exchanges it for an access token — until it reports `linked`.
import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'

// How often, and for how long, to ask the server whether the Hosted Link session finished.
const LINK_POLL_MS = 3000
const LINK_POLL_LIMIT_MS = 15 * 60 * 1000

export interface PlaidLink {
  starting: boolean
  waiting: boolean
  checking: boolean
  /** True once a link this page started has completed. */
  justLinked: boolean
  /** The bank name when the completed link renewed an ALREADY-linked bank (update mode). */
  renewed: string | null
  /** What that renewal was: a renewed sign-in, or the bank's account picker re-opened. */
  renewedKind: 'reconnect' | 'accounts'
  error: string | null
  setError: (e: string | null) => void
  /** Bumped on each completed link, so the Accounts list re-fetches the bank's accounts. */
  linkCount: number
  /** Link a new bank — or, with `bank`, renew that linked bank (Plaid update mode, which keeps
   *  its accounts and their links); `addAccounts` re-opens its account picker. */
  start: (opts?: { bank?: string; addAccounts?: boolean }) => Promise<void>
  checkNow: () => void
}

export function usePlaidLink(onLinked: () => void): PlaidLink {
  const [starting, setStarting] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [checking, setChecking] = useState(false)
  const [justLinked, setJustLinked] = useState(false)
  const [renewed, setRenewed] = useState<string | null>(null)
  const [renewedKind, setRenewedKind] = useState<'reconnect' | 'accounts'>('reconnect')
  const [error, setError] = useState<string | null>(null)
  const [linkCount, setLinkCount] = useState(0)
  const pollStarted = useRef(0)
  const onLinkedRef = useRef(onLinked)
  useEffect(() => {
    onLinkedRef.current = onLinked
  })

  async function start(opts?: { bank?: string; addAccounts?: boolean }) {
    setStarting(true)
    setError(null)
    try {
      const r = await authFetch('/api/plaid/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          opts?.bank ? { bank: opts.bank, add_accounts: !!opts.addAccounts } : {},
        ),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Could not start the bank link.')
        return
      }
      const data = (await r.json()) as { link: string }
      if (data.link) {
        window.open(data.link, '_blank', 'noopener,noreferrer')
        setJustLinked(false)
        setRenewed(null)
        setRenewedKind(opts?.addAccounts ? 'accounts' : 'reconnect')
        pollStarted.current = Date.now()
        setWaiting(true)
      }
    } finally {
      setStarting(false)
    }
  }

  const check = useCallback(async (manual: boolean) => {
    if (manual) setChecking(true)
    try {
      const r = await authFetch('/api/plaid/link/complete', { method: 'POST' })
      if (r.status === 404 || r.status === 405) {
        // The server predates this page (it was updated while the app ran) — this link can't
        // finish, and polling again won't change that.
        setError('Open Net Worth was updated while this page was open. Reload, then link again.')
        setWaiting(false)
        return
      }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Could not complete the bank link.')
        // Only "no link in progress" is final; anything else may clear on the next check.
        if (r.status === 422) setWaiting(false)
        return
      }
      setError(null)
      const d = (await r.json()) as { status: string; mode?: string; institution_name?: string }
      if (d.status === 'linked') {
        setWaiting(false)
        setJustLinked(true)
        setRenewed(d.mode === 'update' ? d.institution_name || 'The bank' : null)
        setLinkCount((k) => k + 1)
        onLinkedRef.current()
        // Every open screen re-reads — the connector's own "Not connected" badge included.
        emitDataChanged(['config'])
      } else if (manual) {
        setError('Plaid has not finished yet — complete the sign-in in the Plaid tab first.')
      }
    } catch {
      if (manual) setError('Could not reach the server.')
    } finally {
      if (manual) setChecking(false)
    }
  }, [])

  useEffect(() => {
    if (!waiting) return
    const id = window.setInterval(() => {
      if (Date.now() - pollStarted.current > LINK_POLL_LIMIT_MS) {
        setWaiting(false)
        return
      }
      void check(false)
    }, LINK_POLL_MS)
    return () => window.clearInterval(id)
  }, [waiting, check])

  return {
    starting,
    waiting,
    checking,
    justLinked,
    renewed,
    renewedKind,
    error,
    setError,
    linkCount,
    start,
    checkNow: () => void check(true),
  }
}
