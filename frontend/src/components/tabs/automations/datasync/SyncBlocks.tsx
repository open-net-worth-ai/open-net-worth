import { useState } from 'react'
import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import { fDateTime } from '../../../../utils/format'
import { ActionMenu, SegmentedControl } from '../../../ui'
import { YnabImport } from '../../imports/YnabImport'
import { KIND_LABEL, type RunSummary } from '../../imports/runs'
import type { SyncStatus } from '../../imports/sync'
import type { BudgetAppInfo } from './budgetApps'

// The parts of a data sync's page once it is set up: what it says, its settings, its history.

type HistoryFilter = 'all' | 'attention'

/** What the server says is worth a look while it holds. When it says to import first, the
 *  import is here too: nowhere else on a set-up page offers one. */
export function WorthChecking({
  app,
  status,
  planId,
  runs,
  onConnect,
  onOpen,
  onImported,
}: {
  app: BudgetAppInfo
  status: SyncStatus
  planId: string | null
  runs: RunSummary[] | undefined
  onConnect: () => void
  onOpen: (id: string) => void
  onImported: () => void
}) {
  const warnings = status.warnings ?? []
  return (
    <section className="a3-block a3-attention" data-od-id="ynab-sync-warnings">
      <div className="a3-block-h">Worth checking</div>
      <ul className="a3-rules">
        {warnings.map((w) => (
          <li key={w.kind}>{w.text}</li>
        ))}
      </ul>
      {warnings.some((w) => w.kind === 'import_first') && (
        <YnabImport
          app={app}
          runs={runs}
          startFrom={planId}
          onStarted={onImported}
          onOpen={onOpen}
          onConnect={onConnect}
        />
      )}
    </section>
  )
}

/** One setting read as a Lucius automation's Trigger reads — an icon and its value — that opens
 *  the choices when clicked, the current one ticked. */
export function SettingMenu<T extends string>({
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

export function SyncHistory({
  app,
  runs,
  onOpen,
}: {
  app: BudgetAppInfo
  runs: RunSummary[]
  onOpen: (id: string) => void
}) {
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
        <div className="a3-hist" role="list" aria-label={`${app.name} sync history`}>
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
