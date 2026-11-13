import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Check, Download, Play } from 'lucide-react'
import { fDate } from '../../../../utils/format'
import { Button } from '../../../ui'
import {
  AccountBoxes,
  ActionError,
  ConnectorSetupButton,
  LinkAccountsButton,
  ScheduleMenu,
} from './BankSyncParts'
import { outOfReads, useAct } from './bankSyncHelpers'
import { Step } from './SyncSetupSteps'
import {
  bankSyncSend,
  type BankSyncAccount,
  type BankSyncStatus,
  type ModeResult,
} from './bankSync'

// A bank's sync before it is on (ADR 2026-11-13), as the budget apps' setup reads: pick the
// accounts, bring in the history, turn it on. Turning it on without step 2 is allowed — its first
// run brings the history in. No AI choice here: someone who wants Lucius to sync a bank uses a
// Lucius Automation.

const REACHED = { accounts: 1, history: 2, sync: 3, done: 4 } as const

export function BankSyncSetup({ status }: { status: BankSyncStatus }) {
  const navigate = useNavigate()
  const { busy, error, act } = useAct()
  const p = status.provider
  // Skip for now: step 2 is optional — the first sync brings the history in anyway. Kept on
  // this screen only; the server's stage still says the history isn't in.
  const [skipped, setSkipped] = useState(false)
  const stage = REACHED[status.stage ?? 'accounts']
  const reached = skipped && stage === 2 ? 3 : stage
  const accounts = status.accounts ?? []
  const imported = accounts.some((a) => a.sync && a.imported)
  // A ticked account with no history yet (one linked after the import) is what Import is for.
  const missing = accounts.filter((a) => a.sync && !a.imported)
  const importing = status.running || busy === 'import'
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
            <ConnectorSetupButton status={status} label={`Connect ${status.label}…`} />
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
          {reached < 2 ? null : importing ? (
            <div className="bs-actions">
              <Button size="sm" variant="primary" loading disabled>
                Importing…
              </Button>
              <span className="bs-note">Reading your history from the bank.</span>
            </div>
          ) : imported && missing.length === 0 ? (
            <p className="bs-done">
              <Check size={14} aria-hidden="true" />
              {`History imported: ${importedLine(accounts)}. Balances unchanged.`}
              {status.last_run && (
                <button
                  type="button"
                  className="a3-linkbtn"
                  onClick={() =>
                    void navigate({
                      to: '/accounts',
                      search: {
                        batch: `bank_sync:${status.last_run!.id}`,
                        batchLabel: `${status.label} import · ${fDate(status.last_run!.started_at)}`,
                      } as never,
                    } as never)
                  }
                >
                  View in register
                </button>
              )}
            </p>
          ) : (
            <>
              <p>
                {skipped
                  ? 'Skipped. The first sync brings it in.'
                  : imported
                    ? `${missing.map((a) => a.name).join(', ')}: no history yet.`
                    : `Up to ${status.history_days ?? 90} days. Today’s balances stay the same.`}
              </p>
              <div className="bs-actions">
                <Button
                  size="sm"
                  variant={reached === 2 ? 'primary' : 'secondary'}
                  iconLeft={<Download size={12} aria-hidden="true" />}
                  disabled={!status.ready || noReads}
                  onClick={() => void act('import', () => bankSyncSend(`/${p}/import`, 'POST'))}
                >
                  Import history
                </Button>
                {reached === 2 && !skipped && (
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

/** "3 transactions from Nov 5 to Nov 12, into 2 accounts at 2 banks" — counted in the book (a
 *  run's own record can be gone): how many, over which dates, and where they went. */
function importedLine(accounts: BankSyncAccount[]): string {
  const into = accounts.filter((a) => a.sync && a.imported)
  const n = into.reduce((sum, a) => sum + (a.booked ?? 0), 0)
  const firsts = into
    .map((a) => a.booked_from)
    .filter((d): d is string => !!d)
    .sort()
  const lasts = into
    .map((a) => a.booked_to)
    .filter((d): d is string => !!d)
    .sort()
  const banks = new Set(into.map((a) => a.bank || '')).size
  const txns = `${n} ${n === 1 ? 'transaction' : 'transactions'}`
  const span =
    firsts.length && lasts.length
      ? firsts[0] === lasts[lasts.length - 1]
        ? ` on ${fDate(firsts[0]!)}`
        : ` from ${fDate(firsts[0]!)} to ${fDate(lasts[lasts.length - 1]!)}`
      : ''
  const where =
    into.length === 1
      ? into[0]!.name
      : `${into.length} accounts${banks > 1 ? ` at ${banks} banks` : ''}`
  return `${txns}${span}, into ${where}`
}
