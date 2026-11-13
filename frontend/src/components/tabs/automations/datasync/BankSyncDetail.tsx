import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { fDateTime } from '../../../../utils/format'
import { ErrorState, Loading } from '../../../ui'
import { BankRunReport } from './BankRunReport'
import { BankSyncHead, BankSyncSettings } from './BankSyncPanel'
import { BankSyncReviews } from './BankSyncReviews'
import { BankSyncSetup } from './BankSyncSetup'
import { useBankSync, useBankSyncRuns } from './bankSync'
import './bankSync.css'

// A bank connector's Data sync page in Automations (ADR 2026-11-13), laid out as the budget
// apps' page: the setup steps until its sync is on; then the switch and Sync now in the head,
// anything to review and the settings on the left, the run history on the right.

export function BankSyncDetail({ provider, onBack }: { provider: string; onBack: () => void }) {
  // Re-read every two seconds only while a run is in progress.
  const [poll, setPoll] = useState(false)
  const sync = useBankSync(provider, true, poll)
  const running = !!sync.data?.running
  if (running !== poll) setPoll(running)
  const label = sync.data?.label ?? 'Bank sync'
  const crumb = (
    <nav className="a3-crumb" aria-label="Breadcrumb">
      <button type="button" onClick={onBack}>
        Automations
      </button>
      <span aria-hidden="true">/</span>
      <span className="cur" aria-current="page">
        {label}
      </span>
    </nav>
  )
  if (sync.initialLoading)
    return (
      <div className="a3-wrap">
        {crumb}
        <Loading label="Loading the bank sync…" />
      </div>
    )
  if (!sync.data)
    return (
      <div className="a3-wrap">
        {crumb}
        <ErrorState
          title="This bank sync isn’t available"
          message={
            sync.error instanceof Error ? sync.error.message : 'It may be off on this server.'
          }
          onRetry={() => void sync.refresh()}
        />
      </div>
    )
  const status = sync.data
  if (status.stage === 'done')
    return (
      <div className="a3-wrap" data-od-id="bank-sync-detail">
        {crumb}
        <BankSyncHead status={status} />
        <div className="a3-cols">
          <div className="a3-col-setup">
            <BankSyncReviews provider={provider} />
            <BankSyncSettings status={status} />
          </div>
          <BankSyncHistory provider={provider} />
        </div>
      </div>
    )
  return (
    <div className="a3-wrap" data-od-id="bank-sync-detail">
      {crumb}
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{label}</h1>
          <div className="a3-detail-sub">Books new bank transactions on a schedule.</div>
        </div>
      </div>
      <BankSyncSetup status={status} />
    </div>
  )
}

function BankSyncHistory({ provider }: { provider: string }) {
  const runs = useBankSyncRuns(provider)
  const [open, setOpen] = useState<string | null>(null)
  const list = runs.data ?? []
  return (
    <div className="a3-block a3-col-history" data-od-id="bank-sync-history">
      {open && <BankRunReport runId={open} onClose={() => setOpen(null)} />}
      <div className="a3-block-h">
        History
        <span className="count">
          {list.length} {list.length === 1 ? 'run' : 'runs'}
        </span>
      </div>
      {runs.initialLoading ? (
        <Loading label="Loading the history…" />
      ) : list.length === 0 ? (
        <div className="a3-history-empty">No runs yet.</div>
      ) : (
        <div className="a3-hist" role="list" aria-label="Sync history">
          {list.map((run) => {
            const bad = run.status === 'failed' || run.status === 'interrupted'
            return (
              <button
                key={run.id}
                type="button"
                role="listitem"
                className="a3-hist-row"
                onClick={() => setOpen(run.id)}
              >
                <span
                  className={`rd-dot ${bad ? 'bad' : run.status === 'running' ? 'run' : 'good'}`}
                  aria-hidden="true"
                />
                <span className="when">{fDateTime(run.started_at, 'short', 'always')}</span>
                <span className="note">
                  {run.trigger === 'scheduled' ? 'Scheduled' : 'By you'}
                  {' — '}
                  {run.status === 'running' ? 'Syncing…' : run.outcome || run.error || ''}
                </span>
                {run.status === 'running' && <span className="a3-live" title="Running now" />}
                <ChevronRight size={13} className="a3-hist-chevron" aria-hidden="true" />
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
