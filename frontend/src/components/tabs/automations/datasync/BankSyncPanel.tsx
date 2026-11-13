import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Play } from 'lucide-react'
import { Button, Checkbox, SegmentedControl } from '../../../ui'
import { templateKeyForConnector } from '../automationTemplates'
import { ActionError, AccountBoxes } from './BankSyncParts'
import { lastRunLine, nextRunLine, outOfReads, useAct, type Act } from './bankSyncHelpers'
import {
  MODE_LABELS,
  bankSyncSend,
  modeNotice,
  type BankSyncMode,
  type BankSyncStatus,
  type ModeResult,
} from './bankSync'
import './bankSync.css'

// A bank's sync once it is set up (ADR 2026-11-13), on its page in Automations → Data sync: who
// books its rows (nobody, fixed rules, or Lucius), which accounts, when, and Sync now.

export function BankSyncPanel({ status }: { status: BankSyncStatus }) {
  const navigate = useNavigate()
  const { busy, error, act } = useAct()
  const [notice, setNotice] = useState<string | null>(null)
  const p = status.provider

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
      <ActionError error={error} />
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
  act: Act
  onNotice: (text: string | null) => void
}) {
  const p = status.provider
  const ticked = (status.accounts ?? []).filter((a) => a.sync)
  const noReads = outOfReads(status)
  return (
    <>
      {!status.ready && (
        <p className="bs-warn">
          {status.label} isn’t set up or is paused, so nothing can be read until it is.
        </p>
      )}
      <AccountBoxes status={status} busy={busy} act={act} />
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
          disabled={!status.ready || ticked.length === 0 || noReads || status.running}
          onClick={() =>
            void act('run', async () => {
              const r = await bankSyncSend<{ skipped_for_limit: number }>(`/${p}/run`, 'POST')
              // The run's own line says "Syncing now…" while it runs; only a skip needs a word.
              onNotice(
                r.skipped_for_limit
                  ? `${r.skipped_for_limit} account(s) wait for the bank’s daily limit.`
                  : null,
              )
            })
          }
        >
          Sync now
        </Button>
        {noReads && (
          <span className="bs-note">Every ticked account has used today’s bank reads.</span>
        )}
      </div>
      <FollowupBox status={status} busy={busy} act={act} />
    </>
  )
}

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
