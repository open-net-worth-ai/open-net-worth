import { Clock3, Landmark } from 'lucide-react'
import { fScheduleWhen } from '../../../../utils/format'
import type { BankSyncStatus } from './bankSync'

// A bank connector in the Data sync view: the same row as a budget app's sync, opening the
// bank's own sync page.

export function BankSyncCard({ status, onOpen }: { status: BankSyncStatus; onOpen: () => void }) {
  const on = status.mode === 'data'
  const when = status.running
    ? 'Syncing now…'
    : on
      ? status.next_run_at
        ? `Every ${status.interval_hours} hours · next ${fScheduleWhen(status.next_run_at)}`
        : `Every ${status.interval_hours} hours`
      : status.mode === 'lucius'
        ? 'A Lucius Automation syncs it'
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
          <span className="t">{status.label}</span>
          {status.to_review > 0 && (
            <span className="a3-tag warn">{status.to_review} to review</span>
          )}
          {on && !status.ready && <span className="a3-tag warn">Connection paused</span>}
        </span>
        <span className="a3-row-blurb">Books new bank transactions on a schedule. No AI.</span>
        <span className={`a3-when${on ? '' : ' off'}`}>
          <Clock3 size={13} strokeWidth={1.7} aria-hidden="true" />
          {when}
        </span>
      </span>
      <span className="a3-row-right" aria-hidden="true">
        <Landmark size={16} strokeWidth={1.7} />
      </span>
    </article>
  )
}
