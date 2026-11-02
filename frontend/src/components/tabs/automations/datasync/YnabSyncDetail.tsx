import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Check, ChevronRight, Link2, Play, ShieldCheck } from 'lucide-react'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fDate, fDateTime, fScheduleWhen, timeAgo } from '../../../../utils/format'
import { Button, ErrorState, SegmentedControl, Select, Switch, TabSkeleton } from '../../../ui'
import { ReviewCard } from '../../imports/ReviewItem'
import { RunReport } from '../../imports/RunReport'
import { YnabImport } from '../../imports/YnabImport'
import { KIND_LABEL, detailOf, type RunSummary } from '../../imports/runs'
import { INTERVAL_LABELS, sendSync } from '../../imports/sync'
import { AutomationMenu } from '../AutomationMenu'
import { YNAB_SYNC_BLURB, YNAB_SYNC_NAME, useYnabSync, type YnabSyncStage } from './ynabSync'

// The two-way YNAB sync's page in Automations. Until it is set up the page is the three setup
// steps (connect, import, turn on); after that it reads like any Automation: on/off and the next
// run on top, what needs you first, then the schedule and the run history.

type HistoryFilter = 'all' | 'attention'

export function YnabSyncDetail({
  reportId,
  onBack,
  onOpenReport,
}: {
  reportId: string | null
  onBack: () => void
  onOpenReport: (runId: string | null) => void
}) {
  const navigate = useNavigate()
  const sync = useYnabSync()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const status = sync.status

  async function send(label: string, path: string, method: string, body?: unknown) {
    setBusy(label)
    setError(await sendSync(path, method, body))
    sync.refresh()
    setBusy(null)
  }

  async function recheck() {
    if (!sync.planId) return
    setBusy('verify')
    setError(null)
    try {
      const r = await authFetch('/api/ynab/migration/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: sync.planId, confirm_nonempty: false }),
      })
      if (!r.ok) setError(await detailOf(r, 'Could not start the re-check.'))
      invalidateQueryCache('/api/imports/runs')
      sync.refresh()
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(null)
    }
  }

  const openConnector = () =>
    void navigate({ to: '/connectors', search: { connector: 'ynab' } } as never)

  const crumb = (
    <nav className="a3-crumb" aria-label="Breadcrumb">
      <button type="button" onClick={onBack}>
        Automations
      </button>
      <span aria-hidden="true">/</span>
      {reportId ? (
        <>
          <button type="button" onClick={() => onOpenReport(null)}>
            {YNAB_SYNC_NAME}
          </button>
          <span aria-hidden="true">/</span>
          <span className="cur" aria-current="page">
            Run report
          </span>
        </>
      ) : (
        <span className="cur" aria-current="page">
          {YNAB_SYNC_NAME}
        </span>
      )}
    </nav>
  )

  if (reportId) {
    return (
      <div className="a3-wrap" data-od-id="ynab-sync-report">
        {crumb}
        <RunReport runId={reportId} onBack={() => onOpenReport(null)} />
      </div>
    )
  }
  if (sync.loading) {
    return (
      <div className="a3-wrap">
        <TabSkeleton variant="automations" label="Loading the YNAB sync…" header={false} />
      </div>
    )
  }
  if (sync.error || !status) {
    return (
      <div className="a3-wrap">
        {crumb}
        <ErrorState
          title="The YNAB sync couldn’t load"
          message={sync.error instanceof Error ? sync.error.message : 'Try again in a moment.'}
          onRetry={sync.refresh}
        />
      </div>
    )
  }

  const setUp = sync.stage === 'on' || sync.stage === 'off'
  const on = sync.stage === 'on'
  const next = !on
    ? 'Off. Turn it on to keep the two in step.'
    : status.running
      ? 'Syncing now…'
      : status.interval === 'manual'
        ? 'Runs only when you start it.'
        : status.next_run_at
          ? `Next sync: ${fScheduleWhen(status.next_run_at)}`
          : 'Next sync: any moment now'

  return (
    <div className="a3-wrap" data-od-id="ynab-sync-detail">
      {crumb}
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{YNAB_SYNC_NAME}</h1>
          <div className="a3-detail-sub">{YNAB_SYNC_BLURB}</div>
          <div className="a3-kind-line">
            <span className="a3-tag">Data sync</span>
            <span>No AI involved: it follows fixed rules and costs nothing to run.</span>
          </div>
        </div>
        {setUp && (
          <div className="a3-detail-bar">
            <div className="a3-detail-state">
              <Switch
                id="ynab-sync-enabled"
                checked={on}
                disabled={!!busy || status.running || !sync.planId}
                onChange={() =>
                  void send('settings', '/settings', 'PUT', { enabled: !on, plan_id: sync.planId })
                }
                aria-label={on ? 'Turn the YNAB sync off' : 'Turn the YNAB sync on'}
              />
              <span>{next}</span>
            </div>
            <div className="a3-detail-actions">
              <Button
                size="sm"
                variant="primary"
                disabled={!on || status.running}
                loading={status.running || busy === 'run'}
                iconLeft={<Play size={12} aria-hidden="true" />}
                onClick={() => void send('run', '/run', 'POST')}
              >
                Sync now
              </Button>
              <AutomationMenu
                label="More actions"
                items={[
                  {
                    label: 'Re-check against YNAB',
                    disabled: !!busy || sync.running,
                    icon: (
                      <span className="a3-menu-ic">
                        <ShieldCheck size={14} aria-hidden="true" />
                      </span>
                    ),
                    onSelect: () => void recheck(),
                  },
                  {
                    label: 'Open the YNAB connector',
                    icon: (
                      <span className="a3-menu-ic">
                        <Link2 size={14} aria-hidden="true" />
                      </span>
                    ),
                    onSelect: openConnector,
                  },
                ]}
              />
            </div>
          </div>
        )}
      </div>
      {error && (
        <div className="a3-action-error" role="alert">
          {error}
        </div>
      )}

      {setUp ? (
        <div className="a3-cols">
          <div className="a3-col-setup">
            {status.conflicts.length > 0 && (
              <section className="a3-block a3-attention" data-od-id="ynab-sync-review">
                <div className="a3-block-h">
                  Needs your review
                  <span className="count">{status.conflicts.length}</span>
                </div>
                <div className="a3-block-note">
                  The sync left these alone for you to decide. Each one says what its choice
                  changes, here and in YNAB.
                </div>
                {status.conflicts.map((item) => (
                  <ReviewCard
                    key={item.id}
                    item={item}
                    busy={busy === item.id || status.running}
                    onKeep={(keep) =>
                      void send(item.id, `/conflicts/${item.id}/resolve`, 'POST', { keep })
                    }
                  />
                ))}
              </section>
            )}
            <section className="a3-block" data-od-id="ynab-sync-schedule">
              <div className="a3-block-h">Schedule</div>
              <div className="a3-schedule-row">
                <Select
                  id="ynab-sync-interval"
                  aria-label="How often to sync"
                  value={status.interval}
                  options={status.intervals.map((i) => ({
                    value: i,
                    label: INTERVAL_LABELS[i] ?? i,
                  }))}
                  disabled={!!busy || status.running}
                  onChange={(e) =>
                    void send('settings', '/settings', 'PUT', { interval: e.target.value })
                  }
                />
              </div>
              {status.last_run && (
                <div className={`a3-last-sync${status.last_run.ok ? '' : ' bad'}`}>
                  Last sync {timeAgo(status.last_run.finished_at)}:{' '}
                  {status.last_run.ok
                    ? status.last_run.summary
                    : `failed. ${status.last_run.error ?? ''}`}
                  {status.last_run.run_id && (
                    <>
                      {' '}
                      <button
                        type="button"
                        className="a3-linkbtn"
                        onClick={() => onOpenReport(status.last_run?.run_id ?? null)}
                      >
                        Open report
                      </button>
                    </>
                  )}
                </div>
              )}
              <div className="a3-block-note">
                A sync uses at most 40 of the 200 requests an hour YNAB allows your token.
              </div>
            </section>
            <section className="a3-block" data-od-id="ynab-sync-plan">
              <div className="a3-block-h">YNAB plan</div>
              <div className="a3-model-detail">
                <Link2 size={14} aria-hidden="true" />
                <b>{sync.planName ?? 'Not chosen'}</b>
                {sync.lastImport && (
                  <>
                    <span>•</span>
                    <span>Imported {fDate(sync.lastImport.started_at, 'short', 'always')}</span>
                  </>
                )}
              </div>
            </section>
            <section className="a3-block" data-od-id="ynab-sync-rules">
              <div className="a3-block-h">How it decides</div>
              <ul className="a3-rules">
                <li>A change on one side is copied to the other.</li>
                <li>A change on both sides waits for you. Nothing is overwritten.</li>
                <li>
                  What it sends to YNAB is flagged red there. A changed Assigned adds a dated line
                  to the category’s note.
                </li>
              </ul>
            </section>
          </div>
          <SyncHistory runs={sync.runs ?? []} onOpen={(id) => onOpenReport(id)} />
        </div>
      ) : (
        <SetupSteps
          stage={sync.stage}
          runs={sync.runs}
          busy={busy}
          planId={sync.planId}
          interval={status.interval}
          onConnect={openConnector}
          onOpenReport={(id) => onOpenReport(id)}
          onImported={sync.refresh}
          onTurnOn={() =>
            void send('settings', '/settings', 'PUT', { enabled: true, plan_id: sync.planId })
          }
        />
      )}
    </div>
  )
}

function SetupSteps({
  stage,
  runs,
  busy,
  planId,
  interval,
  onConnect,
  onOpenReport,
  onImported,
  onTurnOn,
}: {
  stage: YnabSyncStage
  runs: RunSummary[] | undefined
  busy: string | null
  planId: string | null
  interval: string
  onConnect: () => void
  onOpenReport: (id: string) => void
  onImported: () => void
  onTurnOn: () => void
}) {
  const reached = { connect: 1, import: 2, enable: 3, off: 3, on: 3 }[stage]
  return (
    <ol className="a3-steps" data-od-id="ynab-sync-setup">
      <Step n={1} reached={reached} title="Connect YNAB">
        {reached === 1 ? (
          <>
            <p>
              Save a YNAB personal access token on the YNAB connector. It stays on this machine.
            </p>
            <div>
              <Button size="sm" variant="primary" onClick={onConnect}>
                Open the YNAB connector
              </Button>
            </div>
          </>
        ) : (
          <p>Token saved.</p>
        )}
      </Step>
      <Step n={2} reached={reached} title="Bring your YNAB history in">
        {reached === 2 ? (
          <>
            <p>
              The sync pairs with what it imported, so this comes first: accounts, categories, every
              transaction and each month’s Assigned, checked against YNAB afterwards. Safe to run
              again; nothing is booked twice.
            </p>
            <YnabImport runs={runs} onStarted={onImported} onOpen={onOpenReport} />
          </>
        ) : reached > 2 ? (
          <p>Imported.</p>
        ) : (
          <p>After you connect YNAB.</p>
        )}
      </Step>
      <Step n={3} reached={reached} title="Turn on the sync">
        {reached === 3 ? (
          <>
            <p>
              From then on it runs {(INTERVAL_LABELS[interval] ?? interval).toLowerCase()}: a change
              on either side is copied to the other. You can change how often later.
            </p>
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
        ) : (
          <p>After the import. Or stop after step 2 if you only want your history.</p>
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

function SyncHistory({ runs, onOpen }: { runs: RunSummary[]; onOpen: (id: string) => void }) {
  const [filter, setFilter] = useState<HistoryFilter>('all')
  const needsAttention = (r: RunSummary) => r.status === 'failed' || r.status === 'interrupted'
  const shown = filter === 'all' ? runs : runs.filter(needsAttention)
  return (
    <div className="a3-block a3-col-history" data-od-id="ynab-sync-history">
      <div className="a3-block-h">
        History
        <span className="count">
          {runs.length} {runs.length === 1 ? 'run' : 'runs'}
        </span>
      </div>
      <SegmentedControl
        ariaLabel="Filter the history"
        options={[
          { value: 'all', label: 'All' },
          { value: 'attention', label: 'Needs attention' },
        ]}
        value={filter}
        onChange={setFilter}
      />
      {shown.length === 0 ? (
        <div className="a3-history-empty">
          {filter === 'all' ? 'No runs yet.' : 'Nothing failed.'}
        </div>
      ) : (
        <div className="a3-hist" role="list" aria-label="YNAB sync history">
          {shown.map((run) => (
            <button
              key={run.id}
              type="button"
              role="listitem"
              className="a3-hist-row"
              onClick={() => onOpen(run.id)}
            >
              <span
                className={`rd-dot ${needsAttention(run) ? 'bad' : run.status === 'running' ? 'run' : 'good'}`}
                aria-hidden="true"
              />
              <span className="when">{fDateTime(run.started_at, 'short', 'always')}</span>
              <span className="note">
                {KIND_LABEL[run.kind]}
                {run.trigger === 'scheduled' ? '' : ' · by you'}
                {' — '}
                {run.status === 'running' ? run.phase : run.outcome || run.error || ''}
              </span>
              {run.status === 'running' && <span className="a3-live" title="Running now" />}
              <ChevronRight size={13} className="a3-hist-chevron" aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
      <div className="a3-history-note">
        A scheduled sync that changes nothing is not added here.
      </div>
    </div>
  )
}
