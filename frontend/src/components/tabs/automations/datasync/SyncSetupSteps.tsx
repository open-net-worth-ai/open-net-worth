import { Check } from 'lucide-react'
import { Button, SegmentedControl } from '../../../ui'
import { YnabImport } from '../../imports/YnabImport'
import type { RunSummary } from '../../imports/runs'
import { directionLabels, type SyncDirection } from '../../imports/sync'
import type { BudgetAppInfo } from './budgetApps'
import type { YnabSyncStage } from './ynabSync'

// A data sync's page before it is set up: connect, then review and import, then turn on.

/** Which way changes flow. A change on both sides is asked about in every direction. */
function DirectionPicker({
  app,
  value,
  disabled,
  onChange,
}: {
  app: BudgetAppInfo
  value: SyncDirection
  disabled: boolean
  onChange: (direction: SyncDirection) => void
}) {
  return (
    <SegmentedControl
      ariaLabel="Sync direction"
      options={(Object.keys(directionLabels(app)) as SyncDirection[]).map((d) => ({
        value: d,
        label: directionLabels(app)[d],
      }))}
      value={value}
      onChange={(d) => {
        if (!disabled && d !== value) onChange(d)
      }}
    />
  )
}

export function SetupSteps({
  app,
  stage,
  runs,
  busy,
  planId,
  direction,
  onDirection,
  onConnect,
  onOpenReport,
  onImported,
  onTurnOn,
}: {
  app: BudgetAppInfo
  stage: YnabSyncStage
  runs: RunSummary[] | undefined
  busy: string | null
  planId: string | null
  direction: SyncDirection
  onDirection: (direction: SyncDirection) => void
  onConnect: () => void
  onOpenReport: (id: string) => void
  onImported: () => void
  onTurnOn: () => void
}) {
  // Two steps: the import is reviewed, then booked with the sync turned on, in one window. An
  // import that left the sync off (chosen, or it could not turn on) offers it here.
  const reached = { connect: 1, import: 2, enable: 2, off: 3, on: 3 }[stage]
  return (
    <ol className="a3-steps" data-od-id="ynab-sync-setup">
      <Step n={1} reached={reached} title={`Connect ${app.name}`}>
        {reached === 1 ? (
          <>
            <p>{app.setupLine}</p>
            <div>
              <Button size="sm" variant="primary" onClick={onConnect}>
                {`Connect ${app.name}…`}
              </Button>
            </div>
          </>
        ) : (
          // A wrong or revoked token shows up as an error in step 2; fix it from here.
          <p>
            {`${app.savedLine} ·`}{' '}
            <button type="button" className="a3-linkbtn" onClick={onConnect}>
              Change
            </button>
          </p>
        )}
      </Step>
      <Step n={2} reached={reached} title={`Bring your ${app.planWord} in and keep it in sync`}>
        {stage === 'import' ? (
          <>
            <p>{`Review what your ${app.planWord} holds, then import it. Safe to run again.`}</p>
            <YnabImport
              app={app}
              runs={runs}
              onStarted={onImported}
              onOpen={onOpenReport}
              onConnect={onConnect}
            />
          </>
        ) : stage === 'enable' ? (
          <>
            <p>Imported. Turn on the sync to keep the two in step.</p>
            <DirectionPicker app={app} value={direction} disabled={!!busy} onChange={onDirection} />
            <div>
              <Button
                size="sm"
                variant="primary"
                loading={busy === 'settings'}
                disabled={!planId}
                onClick={onTurnOn}
              >
                Turn on
              </Button>
            </div>
          </>
        ) : reached > 2 ? (
          <p>Imported and syncing.</p>
        ) : (
          <p>{`After you connect ${app.name}.`}</p>
        )}
      </Step>
    </ol>
  )
}

function Step({
  n,
  reached,
  title,
  children,
}: {
  n: number
  reached: number
  title: string
  children: React.ReactNode
}) {
  const state = n < reached ? 'done' : n === reached ? 'current' : 'later'
  return (
    <li className={`a3-step ${state}`} aria-current={state === 'current' ? 'step' : undefined}>
      <span className="a3-step-n" aria-hidden="true">
        {state === 'done' ? <Check size={13} /> : n}
      </span>
      <div className="a3-step-body">
        <div className="a3-step-title">{title}</div>
        {children}
      </div>
    </li>
  )
}
