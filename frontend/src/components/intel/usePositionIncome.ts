import { useEffect, useState } from 'react'
import { fetchPositionIncome } from '../../api/portfolio'
import { useDataVersion } from '../../hooks/useDataVersion'
import type { IncomeSummary } from '../../types/portfolio'

/** A held position's income summary (the Dividends received figure), or null while loading,
 *  on error, or with no single position to scope to. It refetches after any write: a corrected
 *  or deleted dividend kept the old total on screen until a reload (hand UAT, 2026-11-11). */
export function usePositionIncome(positionId: string | null): IncomeSummary | null {
  const [loaded, setLoaded] = useState<{ id: string; income: IncomeSummary } | null>(null)
  const dataV = useDataVersion('*')
  useEffect(() => {
    if (!positionId) return
    let alive = true
    fetchPositionIncome(positionId)
      .then((income) => {
        if (alive) setLoaded({ id: positionId, income })
      })
      .catch(() => {
        /* leave null → an em-dash */
      })
    return () => {
      alive = false
    }
  }, [positionId, dataV])
  return positionId && loaded?.id === positionId ? loaded.income : null
}
