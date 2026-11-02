import { useState } from 'react'
import { Clock3 } from 'lucide-react'
import { sendSync } from '../../imports/sync'
import { AutomationMenu } from '../AutomationMenu'
import {
  YNAB_SYNC_BLURB,
  YNAB_SYNC_KEY,
  YNAB_SYNC_NAME,
  scheduleLabel,
  type YnabSyncSummary,
} from './ynabSync'

// A data sync in the Data sync view: the same row as a Lucius Automation, with the sync's own
// menu (no edit or duplicate: there is no prompt to change).
export function DataSyncCard({
  sync,
  onOpen,
  onChanged,
}: {
  sync: YnabSyncSummary
  onOpen: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const on = sync.stage === 'on'
  const act = async (path: string, method: string, body?: unknown) => {
    setBusy(true)
    await sendSync(path, method, body)
    setBusy(false)
    onChanged()
  }
  return (
    <article
      className={`a3-row${on ? '' : ' off'}`}
      aria-label={YNAB_SYNC_NAME}
      data-od-id={`automation-row-${YNAB_SYNC_KEY}`}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <span className="a3-row-body">
        <span className="a3-row-title">
          <span className="t">{YNAB_SYNC_NAME}</span>
          {sync.toReview > 0 && <span className="a3-tag warn">{sync.toReview} to review</span>}
          {!on && <span className="a3-tag warn">Off</span>}
        </span>
        <span className="a3-row-blurb">
          {sync.planName ? `${sync.planName}: ` : ''}
          {YNAB_SYNC_BLURB}
        </span>
        <span className={`a3-when${on ? '' : ' off'}`}>
          <Clock3 size={13} strokeWidth={1.7} aria-hidden="true" />
          {sync.running ? 'Syncing now…' : scheduleLabel(sync.status)}
        </span>
      </span>
      <span className="a3-row-right" onClick={(event) => event.stopPropagation()}>
        <AutomationMenu
          items={[
            {
              label: 'Sync now',
              disabled: busy || !on || sync.running,
              onSelect: () => void act('/run', 'POST'),
            },
            {
              label: on ? 'Turn off' : 'Turn on',
              disabled: busy || sync.running || !sync.planId,
              onSelect: () => void act('/settings', 'PUT', { enabled: !on, plan_id: sync.planId }),
            },
            { label: 'Open', onSelect: onOpen },
          ]}
        />
      </span>
    </article>
  )
}
