import { useState } from 'react'
import { fScheduleWhen, timeAgo } from '../../../../utils/format'
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

export function lastRunLine(status: BankSyncStatus): string {
  const run = status.last_run
  if (status.running) return 'Syncing now…'
  if (!run) return 'Not run yet.'
  const when = timeAgo(run.finished_at || run.started_at)
  return `Last run ${when}: ${run.outcome || run.error || run.status}.`
}

export function nextRunLine(status: BankSyncStatus): string {
  if (!status.next_run_at) return 'Next run: within a minute.'
  return `Next run: ${fScheduleWhen(status.next_run_at)}.`
}

/** Every ticked account has used today's bank reads. */
export function outOfReads(status: BankSyncStatus): boolean {
  const ticked = (status.accounts ?? []).filter((a) => a.sync)
  return ticked.length > 0 && ticked.every((a) => a.reads_left === 0)
}
