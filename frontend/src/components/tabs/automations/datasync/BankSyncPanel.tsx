import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Play } from 'lucide-react'
import { fScheduleWhen, timeAgo } from '../../../../utils/format'
import { Button, Checkbox, ErrorState, Loading, SegmentedControl } from '../../../ui'
import { useFlag } from '../../../../hooks/useFlag'
import { templateKeyForConnector } from '../automationTemplates'
import {
  MODE_LABELS,
  bankSyncSend,
  modeNotice,
  useBankSync,
  type BankSyncAccount,
  type BankSyncMode,
  type BankSyncStatus,
  type ModeResult,
} from './bankSync'
import './bankSync.css'

// A bank connector's sync controls (ADR 2026-11-13): who books its rows (nobody, fixed rules, or
// Lucius), which linked accounts, and when. The same panel sits on the connector's page and on its
// Data sync page. It renders nothing for a connector without a sync, or with the flag off.

/** The panel behind the `bank_data_sync` flag — what a connector page mounts. */
export function BankSyncSection({ provider }: { provider: string }) {
  return useFlag('bank_data_sync') === true ? <BankSyncPanel provider={provider} /> : null
}

export function BankSyncPanel({
  provider,
  heading = true,
}: {
  provider: string
  heading?: boolean
}) {
  const [poll, setPoll] = useState(false)
  const sync = useBankSync(provider, true, poll)
  const running = !!sync.data?.running
  if (running !== poll) setPoll(running)
  if (sync.initialLoading) return <Loading label="Loading the bank sync…" />
  if (sync.error && !sync.data)
    return (
      <ErrorState
        title="The bank sync couldn’t load"
        message={sync.error instanceof Error ? sync.error.message : 'Try again in a moment.'}
        onRetry={() => void sync.refresh()}
      />
    )
  if (!sync.data) return null
  return <PanelBody status={sync.data} heading={heading} />
}

function PanelBody({ status, heading }: { status: BankSyncStatus; heading: boolean }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const p = status.provider

  async function act<T>(label: string, run: () => Promise<T>): Promise<T | null> {
    setBusy(label)
    setError(null)
    try {
      return await run()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn’t work.')
      return null
    } finally {
      setBusy(null)
    }
  }

  async function chooseMode(mode: BankSyncMode) {
    setNotice(null)
    const result = await act('mode', () => bankSyncSend<ModeResult>(`/${p}/mode`, 'PUT', { mode }))
    if (!result) return
    setNotice(modeNotice(result))
    const template = templateKeyForConnector(p)
    if (result.needs_template && template)
      void navigate({ to: '/automations', search: { template } as never } as never)
  }

  return (
    <section className="bs-panel" aria-label={`${status.label} sync`} data-od-id="bank-sync-panel">
      {heading && <div className="cn-sublabel">Sync</div>}
      <SegmentedControl
        ariaLabel={`Who books ${status.label} transactions`}
        options={(['off', 'data', 'lucius'] as const).map((m) => ({
          value: m,
          label: MODE_LABELS[m],
        }))}
        value={status.mode}
        onChange={(m) => void chooseMode(m)}
      />
      <p className="bs-note">{modeLine(status)}</p>
      {notice && (
        <p className="bs-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <div className="a3-action-error" role="alert">
          {error}
        </div>
      )}
      {status.mode === 'data' && (
        <DataControls status={status} busy={busy} act={act} onNotice={setNotice} />
      )}
    </section>
  )
}

function modeLine(status: BankSyncStatus): string {
  if (status.mode === 'data')
    return `New ${status.label} transactions are booked by fixed rules, every ${status.interval_hours} hours. No AI is involved.`
  if (status.mode === 'lucius')
    return `A Lucius Automation fetches and books ${status.label} transactions.`
  return `Nothing books ${status.label} transactions. Choose Sync without AI, or Lucius.`
}

function DataControls({
  status,
  busy,
  act,
  onNotice,
}: {
  status: BankSyncStatus
  busy: string | null
  act: <T>(label: string, run: () => Promise<T>) => Promise<T | null>
  onNotice: (text: string | null) => void
}) {
  const p = status.provider
  const accounts = status.accounts ?? []
  const ticked = accounts.filter((a) => a.sync)
  const outOfReads = ticked.length > 0 && ticked.every((a) => a.reads_left === 0)
  return (
    <>
      {!status.ready && (
        <p className="bs-warn">
          {status.label} isn’t set up or is paused, so nothing can be read until it is.
        </p>
      )}
      {accounts.length === 0 ? (
        <p className="bs-note">Link an account above; each linked account can then sync.</p>
      ) : (
        <div className="bs-accounts" role="group" aria-label="Accounts to sync">
          {accounts.map((a) => (
            <Checkbox
              key={a.account_id}
              id={`bs-${p}-${a.account_id}`}
              label={a.mask ? `${a.name} ··${a.mask}` : a.name}
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
      )}
      <div className="bs-meta">
        <span>{lastRunLine(status)}</span>
        <span>{nextRunLine(status)}</span>
      </div>
      {status.last_error && status.last_status !== 'ok' && (
        <p className="bs-warn">{status.last_error}</p>
      )}
      <div className="bs-actions">
        <Button
          size="sm"
          variant="primary"
          iconLeft={<Play size={12} aria-hidden="true" />}
          loading={status.running || busy === 'run'}
          disabled={!status.ready || ticked.length === 0 || outOfReads || status.running}
          onClick={() =>
            void act('run', async () => {
              const r = await bankSyncSend<{ skipped_for_limit: number }>(`/${p}/run`, 'POST')
              onNotice(
                r.skipped_for_limit
                  ? `Syncing. ${r.skipped_for_limit} account(s) wait for the bank’s daily limit.`
                  : 'Syncing now…',
              )
            })
          }
        >
          Sync now
        </Button>
        {outOfReads && (
          <span className="bs-note">Every ticked account has used today’s bank reads.</span>
        )}
      </div>
      <Checkbox
        id={`bs-${p}-followup`}
        label="Let Lucius categorise what’s left"
        description="After a sync, Lucius sorts the rows no rule or payee could categorise. It uses your AI model; booking never waits for it."
        checked={status.lucius_followup}
        disabled={busy !== null}
        onChange={() =>
          void act('followup', () =>
            bankSyncSend(`/${p}/followup`, 'PUT', { enabled: !status.lucius_followup }),
          )
        }
      />
    </>
  )
}

function readsLine(a: BankSyncAccount, daily: number | null): string {
  if (!a.sync) return 'Not synced: never read or booked.'
  if (daily === null || a.reads_left === null) return 'Synced.'
  if (a.reads_left > 0) return `Synced. ${a.reads_left} of ${daily} bank reads left today.`
  return a.next_read_at
    ? `Synced. Bank reads used for today; next read ${fScheduleWhen(a.next_read_at)}.`
    : 'Synced. Bank reads used for today.'
}

function lastRunLine(status: BankSyncStatus): string {
  const run = status.last_run
  if (status.running) return 'Syncing now…'
  if (!run) return 'Not run yet.'
  const when = timeAgo(run.finished_at || run.started_at)
  return `Last run ${when}: ${run.outcome || run.error || run.status}.`
}

function nextRunLine(status: BankSyncStatus): string {
  if (!status.next_run_at) return 'Next run: within a minute.'
  return `Next run: ${fScheduleWhen(status.next_run_at)}.`
}
