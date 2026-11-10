import { useState } from 'react'
import { Clock3 } from 'lucide-react'
import { ErrorState, TabSkeleton } from '../../../ui'
import { KeepAwakeBanner } from '../KeepAwakeBanner'
import { DataSyncCard } from './DataSyncCard'
import type { DataSyncEntry } from './useDataSyncs'

// The Data sync view of Automations: syncs that keep this book and another app in step by fixed
// rules, with no AI involved. The ones not set up yet are offered below the ones that are.
export function DataSyncView({
  entries,
  onOpen,
}: {
  entries: DataSyncEntry[]
  onOpen: (key: string) => void
}) {
  const [refusal, setRefusal] = useState<string | null>(null)
  if (entries.some((e) => e.sync.loading)) {
    return (
      <div className="a3-wrap">
        <TabSkeleton variant="automations" label="Loading data syncs…" header={false} />
      </div>
    )
  }
  const failed = entries.filter((e) => e.sync.error)
  if (entries.length > 0 && failed.length === entries.length) {
    const first = failed[0]!.sync
    return (
      <div className="a3-wrap">
        <ErrorState
          title="Data syncs couldn’t load"
          message={first.error instanceof Error ? first.error.message : 'Try again in a moment.'}
          onRetry={() => failed.forEach((e) => e.sync.refresh())}
        />
      </div>
    )
  }
  const healthy = entries.filter((e) => !e.sync.error)
  const adopted = healthy.filter((e) => e.sync.adopted)
  const offered = healthy.filter((e) => !e.sync.adopted)
  return (
    <div className="a3-wrap" data-od-id="data-sync-list">
      <KeepAwakeBanner />
      {refusal && (
        <div className="a3-action-error" role="alert">
          {refusal}
        </div>
      )}
      {adopted.length > 0 ? (
        <div className="a3-list">
          {adopted.map(({ app, sync }) => (
            <DataSyncCard
              key={app.key}
              app={app}
              sync={sync}
              onOpen={() => onOpen(app.key)}
              onChanged={sync.refresh}
              onError={setRefusal}
            />
          ))}
        </div>
      ) : (
        <div className="a3-empty">
          No data syncs yet. Pick one below to bring your history in and keep it in step.
        </div>
      )}
      {offered.length > 0 && (
        <section className="a3-tpl-section" aria-labelledby="data-sync-available-heading">
          <div className="a3-sec-label" id="data-sync-available-heading">
            Add a data sync
            <span className="rule" />
          </div>
          <div className="a3-tpl-grid">
            {offered.map(({ app }) => (
              <button
                key={app.key}
                type="button"
                className="a3-tpl"
                aria-label={`Set up ${app.name}`}
                onClick={() => onOpen(app.key)}
              >
                <app.Mark />
                <span className="a3-tpl-body">
                  <span className="a3-tpl-title">{app.name}</span>
                  <span className="a3-tpl-blurb">{app.blurb}</span>
                  <span className="a3-tpl-when">
                    <Clock3 size={11} aria-hidden="true" /> Every hour
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
      {failed.map(({ app, sync }) => (
        <ErrorState
          key={app.key}
          title={`The ${app.name} sync couldn’t load`}
          message={sync.error instanceof Error ? sync.error.message : 'Try again in a moment.'}
          onRetry={sync.refresh}
        />
      ))}
    </div>
  )
}
