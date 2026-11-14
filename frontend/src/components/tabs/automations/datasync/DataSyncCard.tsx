import { useState } from 'react'
import { Clock3 } from 'lucide-react'
import { directionLabels, sendSync } from '../../imports/sync'
import { AutomationMenu } from '../AutomationMenu'
import { YNAB_APP, appBlurb, type BudgetAppInfo } from './budgetApps'
import { scheduleLabel, type YnabSyncSummary } from './ynabSync'

// A data sync in the Data Sync view: the same row as a Lucius Automation, with the sync's own
// menu (no edit or duplicate: there is no prompt to change).
export function DataSyncCard({
  app = YNAB_APP,
  sync,
  onOpen,
  onChanged,
  onError,
}: {
  app?: BudgetAppInfo
  sync: YnabSyncSummary
  onOpen: () => void
  onChanged: () => void
  /** What a refused action said; null as an action starts. */
  onError?: (message: string | null) => void
}) {
  const [busy, setBusy] = useState(false)
  const on = sync.stage === 'on'
  const paused = !!sync.status?.connector_paused
  const act = async (path: string, method: string, body?: unknown) => {
    setBusy(true)
    onError?.(null)
    const refused = await sendSync(path, method, body, app)
    setBusy(false)
    onError?.(refused)
    onChanged()
  }
  return (
    <article
      className={`a3-row${on ? '' : ' off'}`}
      aria-label={app.name}
      data-od-id={`automation-row-${app.key}`}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <span className="a3-row-body">
        <span className="a3-row-title">
          <span className="t">{app.name}</span>
          {sync.toReview > 0 && <span className="a3-tag warn">{sync.toReview} to review</span>}
          {!on && <span className="a3-tag warn">Off</span>}
          {on && paused && <span className="a3-tag warn">Connection paused</span>}
        </span>
        <span className="a3-row-blurb">
          {sync.planName ? `${sync.planName}: ` : ''}
          {appBlurb(app, sync.status?.budget_mode === 'tracking')}
        </span>
        <span className={`a3-when${on ? '' : ' off'}`}>
          <Clock3 size={13} strokeWidth={1.7} aria-hidden="true" />
          {sync.running
            ? 'Syncing now…'
            : [
                sync.status && directionLabels(app)[sync.status.direction],
                scheduleLabel(sync.status),
              ]
                .filter(Boolean)
                .join(' · ')}
        </span>
      </span>
      <span className="a3-row-right" onClick={(event) => event.stopPropagation()}>
        <AutomationMenu
          items={[
            {
              label: 'Sync now',
              disabled: busy || !on || sync.running || paused,
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
