import { useState } from 'react'
import { ArrowLeftRight, Clock3, Link2, Play, ShieldCheck } from 'lucide-react'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fDate, fScheduleWhen, timeAgo } from '../../../../utils/format'
import { Button, ErrorState, Switch, TabSkeleton } from '../../../ui'
import { ReviewCard } from '../../imports/ReviewItem'
import { RunReportModal } from '../../imports/RunReport'
import { detailOf } from '../../imports/runs'
import { INTERVAL_LABELS, directionLabels, sendSync, type SyncDirection } from '../../imports/sync'
import { AutomationMenu } from '../AutomationMenu'
import { ConnectorPanel } from '../../connectors/ConnectorPanel'
import { useConnectors } from '../../connectors/useConnectors'
import { YNAB_APP, appBlurb, flowLine, trailRule, type BudgetAppInfo } from './budgetApps'
import { SettingMenu, SyncHistory, WorthChecking } from './SyncBlocks'
import { SetupSteps } from './SyncSetupSteps'
import { useBudgetSync } from './ynabSync'

// A budget app's data sync page in Automations. Until it is set up the page is the three setup
// steps (connect, import, turn on); after that it reads like any Automation: on/off and the next
// run on top, what needs you first, then the schedule and the run history.

export function YnabSyncDetail({
  app = YNAB_APP,
  reportId,
  onBack,
  onOpenReport,
}: {
  app?: BudgetAppInfo
  reportId: string | null
  onBack: () => void
  onOpenReport: (runId: string | null) => void
}) {
  const sync = useBudgetSync(app)
  useConnectors() // warm the connector list, so the app's panel opens at once
  const [connectOpen, setConnectOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const status = sync.status

  async function send(label: string, path: string, method: string, body?: unknown) {
    setBusy(label)
    setError(await sendSync(path, method, body, app))
    sync.refresh()
    setBusy(null)
  }

  async function recheck() {
    if (!sync.planId) return
    setBusy('verify')
    setError(null)
    try {
      const r = await authFetch(`${app.apiPrefix}/migration/verify`, {
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

  // The app's connector panel opens here, over the page, rather than on Connectors: setting up
  // or changing the sign-in should not cost the user the step they are on.
  const openConnector = () => setConnectOpen(true)

  const crumb = (
    <nav className="a3-crumb" aria-label="Breadcrumb">
      <button type="button" onClick={onBack}>
        Automations
      </button>
      <span aria-hidden="true">/</span>
      <span className="cur" aria-current="page">
        {app.name}
      </span>
    </nav>
  )

  if (sync.loading) {
    return (
      <div className="a3-wrap">
        <TabSkeleton variant="automations" label={`Loading the ${app.name} sync…`} header={false} />
      </div>
    )
  }
  if (sync.error || !status) {
    return (
      <div className="a3-wrap">
        {crumb}
        <ErrorState
          title={`The ${app.name} sync couldn’t load`}
          message={sync.error instanceof Error ? sync.error.message : 'Try again in a moment.'}
          onRetry={sync.refresh}
        />
      </div>
    )
  }

  const setUp = sync.stage === 'on' || sync.stage === 'off'
  const on = sync.stage === 'on'
  const tracking = status.budget_mode === 'tracking'
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
          connectorId={app.key}
          configureOnOpen
          onClose={() => {
            setConnectOpen(false)
            sync.refresh()
          }}
        />
      )}
      <div className="a3-detail-head">
        <div className="a3-detail-title">
          <h1>{app.name}</h1>
          <div className="a3-detail-sub">{appBlurb(app, tracking)}</div>
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
                aria-label={on ? `Turn the ${app.name} sync off` : `Turn the ${app.name} sync on`}
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
                    label: `Re-check against ${app.name}`,
                    disabled: !!busy || sync.running,
                    icon: (
                      <span className="a3-menu-ic">
                        <ShieldCheck size={14} aria-hidden="true" />
                      </span>
                    ),
                    onSelect: () => void recheck(),
                  },
                  {
                    label: `${app.name} connection…`,
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
                  {`The sync left these alone for you to decide. Each one says what its choice changes, here and in ${app.name}.`}
                </div>
                {status.conflicts.map((item) => (
                  <ReviewCard
                    key={item.id}
                    app={app}
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
            {!!status.warnings?.length && (
              <WorthChecking
                app={app}
                status={status}
                planId={sync.planId}
                runs={sync.runs}
                onConnect={openConnector}
                onOpen={(id) => onOpenReport(id)}
                onImported={sync.refresh}
              />
            )}
            <section className="a3-block" data-od-id="ynab-sync-direction">
              <div className="a3-block-h">Direction</div>
              <SettingMenu
                label="Sync direction"
                icon={<ArrowLeftRight size={14} aria-hidden="true" />}
                value={status.direction}
                options={(Object.keys(directionLabels(app)) as SyncDirection[]).map((d) => ({
                  value: d,
                  label: directionLabels(app)[d],
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
              <div className="a3-block-h">{`${app.name} ${app.planWord}`}</div>
              <div className="a3-model-detail">
                <Link2 size={14} aria-hidden="true" />
                <b>{sync.planName ?? 'Not chosen'}</b>
                {sync.planImport && (
                  <>
                    <span>•</span>
                    <span>Imported {fDate(sync.planImport.started_at, 'short', 'always')}</span>
                  </>
                )}
              </div>
              {tracking && (
                <div className="a3-last-sync">
                  This budget is in tracking mode in {app.name}. Its figures there are spending
                  targets rather than envelopes, so Assigned doesn’t sync. Everything else does.
                </div>
              )}
            </section>
            <section className="a3-block" data-od-id="ynab-sync-rules">
              <div className="a3-block-h">How it decides</div>
              <ul className="a3-rules">
                <li>{flowLine(app, status.direction, tracking)}</li>
                <li>A change on both sides waits for you.</li>
                {status.direction !== 'from_ynab' && trailRule(app, tracking) && (
                  <li>{trailRule(app, tracking)}</li>
                )}
              </ul>
            </section>
          </div>
          <SyncHistory app={app} runs={sync.runs ?? []} onOpen={(id) => onOpenReport(id)} />
        </div>
      ) : (
        <SetupSteps
          app={app}
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
