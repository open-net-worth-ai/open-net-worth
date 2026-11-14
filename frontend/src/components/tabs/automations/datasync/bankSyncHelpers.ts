import { useState } from 'react'
import { fScheduleWhen } from '../../../../utils/format'
import type { BankSyncStatus } from './bankSync'

// The bank sync's shared non-component pieces (ADR 2026-11-13): the one-write-at-a-time action
// helper and the lines its screens say about runs.

export type Act = <T>(label: string, run: () => Promise<T>) => Promise<T | null>

/** Run one write at a time; what it said when refused lands in `error`. */
export function useAct() {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const act: Act = async (label, run) => {
    setBusy(label)
    setError(null)
    try {
      return await run()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn’t work.')
      return null
    } finally {
      setBusy(null)
    }
  }
  return { busy, error, act }
}

export function nextRunLine(status: BankSyncStatus): string {
  // Due (or overdue): the scheduler checks every 60 seconds, so a past time would only mislead.
  if (!status.next_run_at || Date.parse(status.next_run_at) <= Date.now())
    return 'Next sync: within a minute'
  return `Next sync: ${fScheduleWhen(status.next_run_at)}`
}

/** "Every hour" / "Every 6 hours" / "Once a day". */
export function everyLabel(hours: number): string {
  if (hours === 1) return 'Every hour'
  if (hours === 24) return 'Once a day'
  return `Every ${hours} hours`
}

/** Every ticked account has used today's bank reads. */
export function outOfReads(status: BankSyncStatus): boolean {
  const ticked = (status.accounts ?? []).filter((a) => a.sync)
  return ticked.length > 0 && ticked.every((a) => a.reads_left === 0)
}
