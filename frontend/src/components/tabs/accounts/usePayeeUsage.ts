// Usage numbers (how many live transactions use each payee, and when it was last used) are a
// Manage-Payees concern only. The picker list (`GET /api/payees`) no longer carries them, so
// the editor asks for them itself — on open, and again whenever the list it was handed changes
// (a merge, delete or reassign moves the counts).
//
// Until the first answer lands the counts are UNKNOWN, not zero: `status` says which, so the
// editor never shows "0 txns · Never used" for a busy payee, nor offers the no-reassign delete
// that would strip the payee off every one of its transactions. A refetch keeps the last
// answer on screen.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../../../hooks/serverState/fetchJson'
import type { ApiPayee, Payee } from './types'

type Usage = { txnCount: number; lastUsed: string | null }
export type PayeeUsageStatus = 'loading' | 'ready' | 'error'

export function usePayeeUsage(payees: Payee[]): { payees: Payee[]; status: PayeeUsageStatus } {
  const [usage, setUsage] = useState<ReadonlyMap<string, Usage> | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    fetchJson<ApiPayee[] | { payees?: ApiPayee[] }>('/api/payees?usage=true')
      .then((raw) => {
        if (!live) return
        const list = Array.isArray(raw) ? raw : (raw.payees ?? [])
        setUsage(
          new Map(
            list.map((p) => [p.id, { txnCount: p.txn_count ?? 0, lastUsed: p.last_used ?? null }]),
          ),
        )
        setFailed(false)
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [payees])
  const merged = useMemo(
    () => (usage ? payees.map((p) => ({ ...p, ...usage.get(p.id) })) : payees),
    [payees, usage],
  )
  return { payees: merged, status: usage ? 'ready' : failed ? 'error' : 'loading' }
}
