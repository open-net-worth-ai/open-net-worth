import { useState } from 'react'
import { Clock3, Play } from 'lucide-react'
import { Button, Checkbox, Switch } from '../../../ui'
import { ActionError, AccountBoxes } from './BankSyncParts'
import { lastRunLine, nextRunLine, outOfReads, useAct, type Act } from './bankSyncHelpers'
import { bankSyncSend, modeNotice, type BankSyncStatus, type ModeResult } from './bankSync'
import './bankSync.css'

// A bank's Data sync page once it is set up (ADR 2026-11-13), laid out as the budget apps' page
// is: the head holds the switch, the next sync and Sync now; the left column its settings. Data
// sync is the no-AI path: someone who wants Lucius to sync a bank uses a Lucius Automation.

export function BankSyncHead({ status }: { status: BankSyncStatus }) {
  const { busy, error, act } = useAct()
  const [notice, setNotice] = useState<string | null>(null)
  const p = status.provider
  const on = status.mode === 'data'
  const ticked = (status.accounts ?? []).filter((a) => a.sync)

  async function toggle() {
    setNotice(null)
    const mode = on ? 'off' : 'data'
    const result = await act('mode', () => bankSyncSend<ModeResult>(`/${p}/mode`, 'PUT', { mode }))
    if (result) setNotice(modeNotice(result))
  }

  async function runNow() {
    const r = await act('run', () =>
      bankSyncSend<{ skipped_for_limit: number }>(`/${p}/run`, 'POST'),
    )
    setNotice(
      r?.skipped_for_limit ? `${r.skipped_for_limit} account(s) wait for tomorrow’s reads.` : null,
    )
  }

  return (
    <>
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{status.label}</h1>
          <div className="a3-detail-sub">Books new bank transactions on a schedule. No AI.</div>
        </div>
        <div className="a3-detail-bar">
          <div className="a3-detail-state">
            <Switch
              id={`bs-${p}-on`}
              checked={on}
              disabled={busy !== null || status.running}
              onChange={() => void toggle()}
              aria-label={
                on ? `Turn the ${status.label} sync off` : `Turn the ${status.label} sync on`
              }
            />
            <span>{stateLine(status)}</span>
          </div>
          <div className="a3-detail-actions">
            <Button
              size="sm"
              variant="primary"
              iconLeft={<Play size={12} aria-hidden="true" />}
              loading={status.running || busy === 'run'}
              disabled={
                !on || !status.ready || ticked.length === 0 || outOfReads(status) || status.running
              }
              onClick={() => void runNow()}
            >
              Sync now
            </Button>
          </div>
        </div>
      </div>
      {notice && (
        <p className="bs-notice" role="status">
          {notice}
        </p>
      )}
      <ActionError error={error} />
    </>
  )
}

function stateLine(status: BankSyncStatus): string {
  if (status.mode === 'lucius') return 'Off: a Lucius Automation syncs it'
  if (status.mode !== 'data') return 'Off'
  if (status.running) return 'Syncing now…'
  if (!status.ready) return `Waiting: ${status.label} isn’t connected`
  if (outOfReads(status)) return 'Out of bank reads today'
  return nextRunLine(status)
}

/** The left column: which accounts, and when. */
export function BankSyncSettings({ status }: { status: BankSyncStatus }) {
  const { busy, error, act } = useAct()
  const failed = status.last_error && status.last_status !== 'ok'
  return (
    <>
      <section className="a3-block" data-od-id="bank-sync-accounts">
        <div className="a3-block-h">Accounts</div>
        <ActionError error={error} />
        <AccountBoxes status={status} busy={busy} act={act} />
      </section>
      <section className="a3-block" data-od-id="bank-sync-schedule">
        <div className="a3-block-h">Schedule</div>
        <div className="a3-model-detail">
          <Clock3 size={14} aria-hidden="true" />
          <b>{`Every ${status.interval_hours} hours`}</b>
        </div>
        {status.last_run && (
          <div className={`a3-last-sync${failed ? ' bad' : ''}`}>
            {failed ? status.last_error : lastRunLine(status)}
          </div>
        )}
      </section>
      <section className="a3-block" data-od-id="bank-sync-rules">
        <div className="a3-block-h">How it books</div>
        <ul className="a3-rules">
          <li>Each payee’s category, else Uncategorized.</li>
          <li>Pending transactions wait until they post.</li>
          <li>A change the bank makes later waits for you.</li>
        </ul>
      </section>
    </>
  )
}

/** "Let Lucius categorise what's left". Built and wired, but HIDDEN for now: data sync runs
 *  without AI until its own results are understood (owner, 2026-11-13). Off by default, so
 *  nothing runs while it isn't rendered. */
export function FollowupBox({
  status,
  busy,
  act,
}: {
  status: BankSyncStatus
  busy: string | null
  act: Act
}) {
  return (
    <Checkbox
      id={`bs-${status.provider}-followup`}
      label="Let Lucius categorise what’s left"
      description="After a sync, Lucius sorts the rows no rule or payee could categorise. It uses your AI model; booking never waits for it."
      checked={status.lucius_followup}
      disabled={busy !== null}
      onChange={() =>
        void act('followup', () =>
          bankSyncSend(`/${status.provider}/followup`, 'PUT', {
            enabled: !status.lucius_followup,
          }),
        )
      }
    />
  )
}
