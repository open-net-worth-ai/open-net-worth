import { useNavigate } from '@tanstack/react-router'
import { Download, Play } from 'lucide-react'
import { Button } from '../../../ui'
import { templateKeyForConnector } from '../automationTemplates'
import { FollowupBox } from './BankSyncPanel'
import { AccountBoxes, ActionError } from './BankSyncParts'
import { lastRunLine, outOfReads, useAct } from './bankSyncHelpers'
import { Step } from './SyncSetupSteps'
import { bankSyncSend, type BankSyncStatus, type ModeResult } from './bankSync'

// A bank's sync before it is on (ADR 2026-11-13), as the budget apps' setup reads: pick the
// accounts, bring in the history the bank serves, then keep it in sync. Turning the sync on
// without step 2 is allowed — its first run brings the history in — but step 2 is where you see
// it happen.

const REACHED = { accounts: 1, history: 2, sync: 3, done: 4 } as const

export function BankSyncSetup({ status }: { status: BankSyncStatus }) {
  const navigate = useNavigate()
  const { busy, error, act } = useAct()
  const p = status.provider
  const reached = REACHED[status.stage ?? 'accounts']
  const accounts = status.accounts ?? []
  const imported = accounts.filter((a) => a.sync && a.imported)
  const noReads = outOfReads(status)

  async function turnOn(mode: 'data' | 'lucius') {
    const result = await act('mode', () => bankSyncSend<ModeResult>(`/${p}/mode`, 'PUT', { mode }))
    const template = templateKeyForConnector(p)
    if (result?.needs_template && template)
      void navigate({ to: '/automations', search: { template } as never } as never)
  }

  return (
    <>
      <ActionError error={error} />
      <ol className="a3-steps" data-od-id="bank-sync-setup">
        <Step n={1} reached={reached} title="Choose the accounts to sync">
          {accounts.length === 0 ? (
            <>
              <p>{`Link your bank’s accounts on the ${status.label} connector first.`}</p>
              <div>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() =>
                    void navigate({
                      to: '/connectors',
                      search: { connector: p } as never,
                    } as never)
                  }
                >
                  {`Open ${status.label}`}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p>Only ticked accounts are read from the bank and booked.</p>
              <AccountBoxes status={status} busy={busy} act={act} />
            </>
          )}
        </Step>
        <Step n={2} reached={reached} title="Bring in your history">
          {reached < 2 ? (
            <p>After you tick an account.</p>
          ) : (
            <>
              <p>
                {`Your bank serves up to ${status.history_days ?? 90} days. They’re booked into the ticked accounts, and each account’s start moves back so today’s balance stays the same.`}
              </p>
              {imported.length > 0 && !status.running && (
                <p className="bs-notice" role="status">
                  {`Imported into ${imported.map((a) => a.name).join(', ')}. ${lastRunLine(status)}`}
                </p>
              )}
              <div className="bs-actions">
                <Button
                  size="sm"
                  variant={reached === 2 ? 'primary' : 'secondary'}
                  iconLeft={<Download size={12} aria-hidden="true" />}
                  loading={status.running || busy === 'import'}
                  disabled={!status.ready || noReads || status.running}
                  onClick={() => void act('import', () => bankSyncSend(`/${p}/import`, 'POST'))}
                >
                  {imported.length > 0 ? 'Import again' : 'Import history'}
                </Button>
                {noReads && (
                  <span className="bs-note">Today’s bank reads are used; try again tomorrow.</span>
                )}
              </div>
            </>
          )}
        </Step>
        <Step n={3} reached={reached} title="Keep it in sync">
          {reached < 3 ? (
            <p>{`After your history is in — or turn it on now and the first run brings it in.`}</p>
          ) : (
            <p>{`New ${status.label} transactions are booked by fixed rules every ${status.interval_hours} hours. No AI is involved.`}</p>
          )}
          <FollowupBox status={status} busy={busy} act={act} />
          <div className="bs-actions">
            <Button
              size="sm"
              variant={reached === 3 ? 'primary' : 'secondary'}
              iconLeft={<Play size={12} aria-hidden="true" />}
              loading={busy === 'mode'}
              disabled={accounts.length === 0 || busy !== null}
              onClick={() => void turnOn('data')}
            >
              Turn on sync without AI
            </Button>
            <button
              type="button"
              className="a3-linkbtn"
              disabled={busy !== null}
              onClick={() => void turnOn('lucius')}
            >
              Let Lucius sync it instead
            </button>
          </div>
        </Step>
      </ol>
    </>
  )
}
