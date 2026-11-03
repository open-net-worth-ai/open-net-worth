import { useState } from 'react'
import {
  ArrowLeftRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Link2,
  Play,
  ShieldCheck,
} from 'lucide-react'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fDate, fDateTime, fScheduleWhen, timeAgo } from '../../../../utils/format'
import { ActionMenu, Button, ErrorState, SegmentedControl, Switch, TabSkeleton } from '../../../ui'
import { ReviewCard } from '../../imports/ReviewItem'
import { RunReportModal } from '../../imports/RunReport'
import { YnabImport } from '../../imports/YnabImport'
import { KIND_LABEL, detailOf, type RunSummary } from '../../imports/runs'
import { DIRECTION_LABELS, INTERVAL_LABELS, sendSync, type SyncDirection } from '../../imports/sync'
import { AutomationMenu } from '../AutomationMenu'
import { ConnectorPanel } from '../../connectors/ConnectorPanel'
import { useConnectors } from '../../connectors/useConnectors'
import { YNAB_SYNC_BLURB, YNAB_SYNC_NAME, useYnabSync, type YnabSyncStage } from './ynabSync'

// The YNAB data sync's page in Automations. Until it is set up the page is the three setup
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
  const sync = useYnabSync()
  useConnectors() // warm the connector list, so the YNAB panel opens at once
  const [connectOpen, setConnectOpen] = useState(false)
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

  // The YNAB connector's own panel opens here, over the page, rather than on Connectors:
  // setting up or changing the token should not cost the user the step they are on.
  const openConnector = () => setConnectOpen(true)

  const crumb = (
    <nav className="a3-crumb" aria-label="Breadcrumb">
      <button type="button" onClick={onBack}>
        Automations
      </button>
      <span aria-hidden="true">/</span>
      <span className="cur" aria-current="page">
        {YNAB_SYNC_NAME}
      </span>
    </nav>
  )

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
    ? 'Off'
    : status.running
      ? 'Syncing now…'
      : status.interval === 'manual'
        ? 'Runs only when you start it.'
        : status.next_run_at
          ? `Next sync: ${fScheduleWhen(status.next_run_at)}`
          : 'Next sync: within a minute' // due: the scheduler checks every 60 seconds

  return (
    <div className="a3-wrap" data-od-id="ynab-sync-detail">
      {crumb}
      {reportId && <RunReportModal runId={reportId} onClose={() => onOpenReport(null)} />}
      {connectOpen && (
        <ConnectorPanel
          connectorId="ynab"
          configureOnOpen
          onClose={() => {
            setConnectOpen(false)
            sync.refresh()
          }}
        />
      )}
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{YNAB_SYNC_NAME}</h1>
          <div className="a3-detail-sub">{YNAB_SYNC_BLURB}</div>
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
                    label: 'YNAB connection…',
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
                    onKeep={(keep, choice) =>
                      void send(item.id, `/conflicts/${item.id}/resolve`, 'POST', {
                        keep,
                        ...(choice && { choice }),
                      })
                    }
                  />
                ))}
              </section>
            )}
            <section className="a3-block" data-od-id="ynab-sync-direction">
              <div className="a3-block-h">Direction</div>
              <SettingMenu
                label="Sync direction"
                icon={<ArrowLeftRight size={14} aria-hidden="true" />}
                value={status.direction}
                options={(Object.keys(DIRECTION_LABELS) as SyncDirection[]).map((d) => ({
                  value: d,
                  label: DIRECTION_LABELS[d],
                }))}
                disabled={!!busy || status.running}
                onPick={(direction) => void send('settings', '/settings', 'PUT', { direction })}
              />
            </section>
            <section className="a3-block" data-od-id="ynab-sync-schedule">
              <div className="a3-block-h">Schedule</div>
              <SettingMenu
                label="How often to sync"
                icon={<Clock3 size={14} aria-hidden="true" />}
                value={status.interval}
                options={status.intervals.map((i) => ({
                  value: i,
                  label: INTERVAL_LABELS[i] ?? i,
                }))}
                disabled={!!busy || status.running}
                onPick={(interval) => void send('settings', '/settings', 'PUT', { interval })}
              />
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
                <li>{FLOW[status.direction]}</li>
                <li>A change on both sides waits for you.</li>
                {status.direction !== 'from_ynab' && (
                  <li>
                    What it adds or changes in YNAB gets YNAB's red flag, so you can spot it there.
                  </li>
                )}
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
          direction={status.direction}
          onDirection={(direction) => void send('settings', '/settings', 'PUT', { direction })}
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

const FLOW: Record<SyncDirection, string> = {
  both: 'Transactions, categories and Assigned changed on either side are copied to the other.',
  from_ynab: 'YNAB’s transactions, categories and Assigned come here. Nothing is written to YNAB.',
  to_ynab: 'This book’s transactions, categories and Assigned go to YNAB. Nothing comes back.',
}

/** Which way changes flow. A change on both sides is asked about in every direction. */
/** One setting read as a Lucius automation's Trigger reads — an icon and its value — that opens
 *  the choices when clicked, the current one ticked. */
function SettingMenu<T extends string>({
  label,
  icon,
  value,
  options,
  disabled,
  onPick,
}: {
  label: string
  icon: React.ReactNode
  value: T
  options: { value: T; label: string }[]
  disabled: boolean
  onPick: (value: T) => void
}) {
  const current = options.find((o) => o.value === value)?.label ?? value
  return (
    <ActionMenu
      label={label}
      className="a3-setting"
      trigger={
        <span className="a3-trigger-detail">
          {icon}
          {current}
          <ChevronDown size={13} aria-hidden="true" className="a3-setting-caret" />
        </span>
      }
      items={options.map((o) => ({
        label: o.label,
        disabled,
        icon:
          o.value === value ? (
            <Check size={14} aria-hidden="true" />
          ) : (
            <span className="a3-setting-blank" aria-hidden="true" />
          ),
        onSelect: () => {
          if (o.value !== value) onPick(o.value)
        },
      }))}
    />
  )
}

function DirectionPicker({
  value,
  disabled,
  onChange,
}: {
  value: SyncDirection
  disabled: boolean
  onChange: (direction: SyncDirection) => void
}) {
  return (
    <SegmentedControl
      ariaLabel="Sync direction"
      options={(Object.keys(DIRECTION_LABELS) as SyncDirection[]).map((d) => ({
        value: d,
        label: DIRECTION_LABELS[d],
      }))}
      value={value}
      onChange={(d) => {
        if (!disabled && d !== value) onChange(d)
      }}
    />
  )
}

function SetupSteps({
  stage,
  runs,
  busy,
  planId,
  direction,
  onDirection,
  onConnect,
  onOpenReport,
  onImported,
  onTurnOn,
}: {
  stage: YnabSyncStage
  runs: RunSummary[] | undefined
  busy: string | null
  planId: string | null
  direction: SyncDirection
  onDirection: (direction: SyncDirection) => void
  onConnect: () => void
  onOpenReport: (id: string) => void
  onImported: () => void
  onTurnOn: () => void
}) {
  // Two steps: the import is reviewed, then booked with the sync turned on, in one window. An
  // import that left the sync off (chosen, or it could not turn on) offers it here.
  const reached = { connect: 1, import: 2, enable: 2, off: 3, on: 3 }[stage]
  return (
    <ol className="a3-steps" data-od-id="ynab-sync-setup">
      <Step n={1} reached={reached} title="Connect YNAB">
        {reached === 1 ? (
          <>
            <p>Add your YNAB personal access token.</p>
            <div>
              <Button size="sm" variant="primary" onClick={onConnect}>
                Connect YNAB…
              </Button>
            </div>
          </>
        ) : (
          // A wrong or revoked token shows up as an error in step 2; fix it from here.
          <p>
            Token saved ·{' '}
            <button type="button" className="a3-linkbtn" onClick={onConnect}>
              Change
            </button>
          </p>
        )}
      </Step>
      <Step n={2} reached={reached} title="Bring your plan in and keep it in sync">
        {stage === 'import' ? (
          <>
            <p>Review what your plan holds, then import it. Safe to run again.</p>
            <YnabImport runs={runs} onStarted={onImported} onOpen={onOpenReport} />
          </>
        ) : stage === 'enable' ? (
          <>
            <p>Imported. Turn on the sync to keep the two in step.</p>
            <DirectionPicker value={direction} disabled={!!busy} onChange={onDirection} />
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
        ) : reached > 2 ? (
          <p>Imported and syncing.</p>
        ) : (
          <p>After you connect YNAB.</p>
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
