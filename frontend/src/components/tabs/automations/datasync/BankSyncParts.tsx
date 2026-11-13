import { fScheduleWhen } from '../../../../utils/format'
import { Checkbox } from '../../../ui'
import { bankSyncSend, type BankSyncAccount, type BankSyncStatus } from './bankSync'
import type { Act } from './bankSyncHelpers'

// Pieces the bank sync's setup steps and its status panel share (ADR 2026-11-13).

export function ActionError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div className="a3-action-error" role="alert">
      {error}
    </div>
  )
}

/** Each linked account with its Sync box. */
export function AccountBoxes({
  status,
  busy,
  act,
}: {
  status: BankSyncStatus
  busy: string | null
  act: Act
}) {
  const p = status.provider
  return (
    <div className="bs-accounts" role="group" aria-label="Accounts to sync">
      {(status.accounts ?? []).map((a) => (
        <Checkbox
          key={a.account_id}
          id={`bs-${p}-${a.account_id}`}
          label={a.name}
          description={readsLine(a, status.daily_reads)}
          checked={a.sync}
          disabled={busy !== null}
          onChange={() =>
            void act('account', () =>
              bankSyncSend(`/${p}/accounts/${a.account_id}`, 'PUT', { sync: !a.sync }),
            )
          }
        />
      ))}
    </div>
  )
}

function readsLine(a: BankSyncAccount, daily: number | null): string {
  const bank = [a.bank_name, a.mask ? `··${a.mask}` : ''].filter(Boolean).join(' ')
  return bank ? `${bank} · ${syncLine(a, daily)}` : syncLine(a, daily)
}

function syncLine(a: BankSyncAccount, daily: number | null): string {
  if (!a.sync) return 'Not synced: never read or booked.'
  if (daily === null || a.reads_left === null) return 'Synced.'
  if (a.reads_left > 0) return `Synced. ${a.reads_left} of ${daily} bank reads left today.`
  return a.next_read_at
    ? `Synced. Bank reads used for today; next read ${fScheduleWhen(a.next_read_at)}.`
    : 'Synced. Bank reads used for today.'
}
