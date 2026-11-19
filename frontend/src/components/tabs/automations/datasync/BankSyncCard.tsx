import { Clock3 } from 'lucide-react'
import { fScheduleWhen } from '../../../../utils/format'
import type { BankSyncStatus } from './bankSync'
import { everyLabel } from './bankSyncHelpers'
import { BankLogo } from './BankSyncParts'

// A bank connector in the Data Sync view: the same row as a budget app's sync, opening the
// bank's own sync page.

export function BankSyncCard({ status, onOpen }: { status: BankSyncStatus; onOpen: () => void }) {
  const on = status.mode === 'data'
  const when = status.running
    ? 'Syncing now…'
    : on
      ? status.next_run_at
        ? `${everyLabel(status.interval_hours)} · next ${fScheduleWhen(status.next_run_at)}`
        : everyLabel(status.interval_hours)
      : 'Off'
  return (
    <article
      className={`a3-row${on ? '' : ' off'}`}
      aria-label={`${status.label} bank sync`}
      data-od-id={`automation-row-bank-${status.provider}`}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <span className="a3-row-body">
        <span className="a3-row-title">
          <BankLogo provider={status.provider} size="xs" />
          <span className="t">{status.label}</span>
          {status.to_review > 0 && (
            <span className="a3-tag warn">{status.to_review} to review</span>
          )}
          {on && !status.ready && <span className="a3-tag warn">Connection paused</span>}
        </span>
        <span className="a3-row-blurb">Syncs your bank transactions automatically.</span>
        <span className={`a3-when${on ? '' : ' off'}`}>
          <Clock3 size={13} strokeWidth={1.7} aria-hidden="true" />
          {when}
        </span>
      </span>
    </article>
  )
}
