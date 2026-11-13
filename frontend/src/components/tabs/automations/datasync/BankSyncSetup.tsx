import { useState } from 'react'
import { Download, Play } from 'lucide-react'
import { Button } from '../../../ui'
import {
  AccountBoxes,
  ActionError,
  ConnectBankButton,
  LinkAccountsButton,
  ScheduleMenu,
} from './BankSyncParts'
import { outOfReads, useAct } from './bankSyncHelpers'
import { Step } from './SyncSetupSteps'
import { bankSyncSend, type BankSyncStatus, type ModeResult } from './bankSync'

// A bank's sync before it is on (ADR 2026-11-13), as the budget apps' setup reads: pick the
// accounts, bring in the history, turn it on. Turning it on without step 2 is allowed — its first
// run brings the history in. No AI choice here: someone who wants Lucius to sync a bank uses a
// Lucius Automation.

const REACHED = { accounts: 1, history: 2, sync: 3, done: 4 } as const

export function BankSyncSetup({ status }: { status: BankSyncStatus }) {
  const { busy, error, act } = useAct()
  const p = status.provider
  // Skip for now: step 2 is optional — the first sync brings the history in anyway. Kept on
  // this screen only; the server's stage still says the history isn't in.
  const [skipped, setSkipped] = useState(false)
  const stage = REACHED[status.stage ?? 'accounts']
  const reached = skipped && stage === 2 ? 3 : stage
  const accounts = status.accounts ?? []
  const imported = accounts.some((a) => a.sync && a.imported)
  const outcome = status.last_run?.outcome
  const noReads = outOfReads(status)

  return (
    <>
      <ActionError error={error} />
      <ol className="a3-steps" data-od-id="bank-sync-setup">
        <Step
          n={1}
          reached={reached}
          title={status.connected === false ? `Connect ${status.label}` : 'Choose accounts'}
        >
          {status.connected === false ? (
            <ConnectBankButton status={status} />
          ) : accounts.length === 0 ? (
            <LinkAccountsButton status={status} primary />
          ) : (
            <>
              <AccountBoxes status={status} busy={busy} act={act} />
              <LinkAccountsButton status={status} />
            </>
          )}
        </Step>
        <Step n={2} reached={reached} title="Import history">
          {reached < 2 ? null : (
            <>
              <p>
                {imported
                  ? `Imported${outcome ? `: ${outcome}` : ''}. Balances unchanged.`
                  : skipped
                    ? 'Skipped. The first sync brings it in.'
                    : `Up to ${status.history_days ?? 90} days. Today’s balances stay the same.`}
              </p>
              <div className="bs-actions">
                <Button
                  size="sm"
                  variant={reached === 2 ? 'primary' : 'secondary'}
                  iconLeft={<Download size={12} aria-hidden="true" />}
                  loading={status.running || busy === 'import'}
                  disabled={!status.ready || noReads || status.running}
                  onClick={() => void act('import', () => bankSyncSend(`/${p}/import`, 'POST'))}
                >
                  {imported ? 'Import again' : 'Import history'}
                </Button>
                {reached === 2 && (
                  <Button size="sm" variant="ghost" onClick={() => setSkipped(true)}>
                    Skip for now
                  </Button>
                )}
                {noReads && <span className="bs-note">Out of bank reads today.</span>}
              </div>
            </>
          )}
        </Step>
        <Step n={3} reached={reached} title="Turn on">
          {reached < 2 ? null : (
            <>
              <ScheduleMenu status={status} busy={busy} act={act} />
              {status.mode === 'lucius' && (
                <p className="bs-note">Pauses the Lucius Automation that syncs it now.</p>
              )}
              <div className="bs-actions">
                <Button
                  size="sm"
                  variant={reached === 3 ? 'primary' : 'secondary'}
                  iconLeft={<Play size={12} aria-hidden="true" />}
                  loading={busy === 'mode'}
                  disabled={busy !== null}
                  onClick={() =>
                    void act('mode', () =>
                      bankSyncSend<ModeResult>(`/${p}/mode`, 'PUT', { mode: 'data' }),
                    )
                  }
                >
                  Turn on
                </Button>
              </div>
            </>
          )}
        </Step>
      </ol>
    </>
  )
}
