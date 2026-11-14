import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Check, Download, Loader2, Play } from 'lucide-react'
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
import { bankSyncSend, type BankSyncAccount, type BankSyncStatus } from './bankSync'

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
  // The server only STARTS the import and answers at once; until the status shows a newer run,
  // the screen would fall back to the button for a moment. So it stays "importing" from the tap
  // while the status still shows the run from before it — ANY newer run ends the wait (waiting
  // for one particular run left the screen on "Starting…" when another one finished first),
  // and so does a few seconds with nothing.
  const [waitingFrom, setWaitingFrom] = useState<string | null>(null)
  const awaitingRun =
    waitingFrom !== null && !status.running && (status.last_run?.id ?? '') === waitingFrom
  useEffect(() => {
    if (waitingFrom === null) return
    const timer = setTimeout(() => setWaitingFrom(null), 15000)
    return () => clearTimeout(timer)
  }, [waitingFrom])
  const importing = status.running || busy === 'import' || awaitingRun
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
          ) : (
            // One place for the Link button whether accounts are linked or not: it owns the
            // Link dialog, and moving it (no accounts ↔ some) re-made it and shut the dialog.
            <>
              {accounts.length > 0 && <AccountBoxes status={status} busy={busy} act={act} />}
              <LinkAccountsButton status={status} primary={accounts.length === 0} />
            </>
          )}
        </Step>
        <Step n={2} reached={reached} title="Import history">
          {reached < 2 ? null : importing ? (
            <ImportProgress run={status.running ? status.last_run : null} />
          ) : imported && missing.length === 0 ? (
            <ImportedSummary
              accounts={accounts.filter((a) => a.sync && a.imported)}
              historyDays={status.history_days ?? 90}
              onOpen={(id) =>
                void navigate({ to: '/accounts', search: { account: id } as never } as never)
              }
            />
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
                  onClick={() =>
                    void act('import', async () => {
                      const before = status.last_run?.id ?? ''
                      const r = await bankSyncSend<{ run_id?: string }>(`/${p}/import`, 'POST')
                      if (r?.run_id) setWaitingFrom(before)
                      return r
                    })
                  }
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
              <div className="bs-actions">
                <Button
                  size="sm"
                  variant={reached === 3 ? 'primary' : 'secondary'}
                  iconLeft={<Play size={12} aria-hidden="true" />}
                  loading={busy === 'mode'}
                  disabled={busy !== null}
                  onClick={() =>
                    void act('mode', () => bankSyncSend(`/${p}/mode`, 'PUT', { mode: 'data' }))
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

/** The import, account by account: what came in and from when, or that the bank had nothing.
 *  Each account opens its register. One sentence for all of them (176 imported · none at the
 *  bank for •••3555) was hard to read (owner, 2026-11-14). */
function ImportedSummary({
  accounts,
  historyDays,
  onOpen,
}: {
  accounts: BankSyncAccount[]
  historyDays: number
  onOpen: (accountId: string) => void
}) {
  return (
    <div className="bs-imported">
      <p className="bs-done">
        <Check size={14} aria-hidden="true" />
        History imported
      </p>
      <ul className="bs-imported-list">
        {accounts.map((a) => (
          <li key={a.account_id}>
            <button type="button" className="bs-imported-acct" onClick={() => onOpen(a.account_id)}>
              {a.name}
              {a.last_four && <span className="bs-acct-mask">{`•••${a.last_four}`}</span>}
            </button>
            <span className="bs-imported-what">{importedDetail(a, historyDays)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function importedDetail(a: BankSyncAccount, historyDays: number): string {
  const n = a.booked ?? 0
  if (!n) return `No transactions at the bank in the last ${historyDays} days`
  const from = a.booked_from
  const to = a.booked_to
  const span = from && to ? (from === to ? fDate(from) : `${fDate(from)} – ${fDate(to)}`) : ''
  const pending = a.booked_pending ? `${a.booked_pending} pending` : ''
  return [`${n} ${n === 1 ? 'transaction' : 'transactions'}`, span, pending]
    .filter(Boolean)
    .join(' · ')
}

/** What the import is doing, from its run record: the account being read (and how many of
 *  how many), then the booking. A bank can take a minute to send a long history. */
function ImportProgress({ run }: { run: BankSyncStatus['last_run'] }) {
  const progress = run?.progress
  const total = progress?.total ?? 0
  let line = 'Starting…'
  if (run?.phase === 'reading') {
    const of = total > 1 ? ` · ${(progress?.done ?? 0) + 1} of ${total}` : ''
    line = `Reading ${progress?.label || 'your account'} from the bank${of}`
  } else if (run?.phase === 'booking') {
    line = total ? `Booking ${total} ${total === 1 ? 'transaction' : 'transactions'}…` : 'Booking…'
  }
  return (
    <div className="bs-progress" role="status" aria-live="polite">
      <p className="bs-progress-line">
        <Loader2 size={13} className="bs-progress-spin" aria-hidden="true" />
        {line}
      </p>
      <div className="bs-progress-bar" aria-hidden="true">
        <span />
      </div>
      <p className="bs-note">A bank can take a minute to send a long history.</p>
    </div>
  )
}
