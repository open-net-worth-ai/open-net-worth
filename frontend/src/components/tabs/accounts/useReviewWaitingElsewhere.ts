/**
 * useReviewWaitingElsewhere.ts — once a review scoped to one account runs out, how many flagged
 * rows still wait in the OTHER accounts, so its finish screen can offer to review them next.
 */

import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'

const REVIEW_TOTAL = '/api/transactions/recently-added?needs_review=true&limit=1'

async function reviewTotal(accountId?: string): Promise<number | null> {
  const scope = accountId ? `&account_id=${encodeURIComponent(accountId)}` : ''
  const response = await authFetch(`${REVIEW_TOTAL}${scope}`)
  if (!response.ok) return null
  const body = (await response.json()) as { total?: number }
  return body.total ?? 0
}

export function useReviewWaitingElsewhere(accountId: string | null, finished: boolean): number {
  const [waiting, setWaiting] = useState(0)
  useEffect(() => {
    if (!accountId || !finished) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWaiting(0)
      return
    }
    let active = true
    // Book total minus this account's: rows skipped here are still flagged and count in both.
    void Promise.all([reviewTotal(), reviewTotal(accountId)])
      .then(([book, mine]) => {
        if (active && book != null && mine != null) setWaiting(Math.max(0, book - mine))
      })
      .catch(() => {
        /* no offer — Done still closes */
      })
    return () => {
      active = false
    }
  }, [accountId, finished])
  return waiting
}
