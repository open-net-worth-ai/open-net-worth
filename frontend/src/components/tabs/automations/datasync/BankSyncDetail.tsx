import { fDateTime } from '../../../../utils/format'
import { ErrorState, Loading } from '../../../ui'
import { BankSyncPanel } from './BankSyncPanel'
import { BankSyncReviews } from './BankSyncReviews'
import { useBankSync, useBankSyncRuns } from './bankSync'
import './bankSync.css'

// A bank connector's Data sync page in Automations (ADR 2026-11-13): its controls, the review
// list, and the run history.

export function BankSyncDetail({ provider, onBack }: { provider: string; onBack: () => void }) {
  const sync = useBankSync(provider)
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
  return (
    <div className="a3-wrap" data-od-id="bank-sync-detail">
      {crumb}
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{label}</h1>
          <div className="a3-detail-sub">
            Bank transactions booked by fixed rules, no AI. Changes the bank makes afterwards wait
            for you below.
          </div>
        </div>
      </div>
      <div className="bs-detail">
        <div className="a3-block">
          <BankSyncPanel provider={provider} heading={false} />
        </div>
        <BankSyncReviews provider={provider} />
        <BankSyncHistory provider={provider} />
      </div>
    </div>
  )
}

function BankSyncHistory({ provider }: { provider: string }) {
  const runs = useBankSyncRuns(provider)
  const list = runs.data ?? []
  return (
    <div className="a3-block" data-od-id="bank-sync-history">
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
        <ul className="bs-history" aria-label="Sync history">
          {list.map((run) => {
            const bad = run.status === 'failed' || run.status === 'interrupted'
            return (
              <li key={run.id} className="bs-history-row">
                <span
                  className={`rd-dot ${bad ? 'bad' : run.status === 'running' ? 'run' : 'good'}`}
                  aria-hidden="true"
                />
                <span className="bs-history-when">
                  {fDateTime(run.started_at, 'short', 'always')}
                </span>
                <span className="bs-history-what">
                  {run.trigger === 'scheduled' ? 'Scheduled' : 'By you'}
                  {' — '}
                  {run.status === 'running' ? 'Syncing…' : run.outcome || run.error || ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
