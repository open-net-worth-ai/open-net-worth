import { useState } from 'react'
import { Clock3 } from 'lucide-react'
import { ErrorState, TabSkeleton } from '../../../ui'
import { KeepAwakeBanner } from '../KeepAwakeBanner'
import { BankSyncCard } from './BankSyncCard'
import { BankLogo } from './BankSyncParts'
import { everyLabel } from './bankSyncHelpers'
import { DataSyncCard } from './DataSyncCard'
import type { BankSyncStatus } from './bankSync'
import type { DataSyncEntry } from './useDataSyncs'

// The Data sync view of Automations: syncs that keep this book and another app in step by fixed
// rules, with no AI involved. The ones not set up yet are offered below the ones that are.
export function DataSyncView({
  entries,
  banks = [],
  onOpen,
}: {
  entries: DataSyncEntry[]
  /** Bank connectors that can sync without AI; a card opens `bank:<provider>`. */
  banks?: BankSyncStatus[]
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
  if (banks.length === 0 && entries.length > 0 && failed.length === entries.length) {
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
  // A bank whose sync isn't set up yet (its connector not connected, or connected and never
  // turned on) is offered, as a budget app not adopted is; a card is a sync that's set up.
  const isSetUp = (b: BankSyncStatus) => b.connected !== false && b.stage === 'done'
  const connected = banks.filter(isSetUp)
  const bankOffers = banks.filter((b) => !isSetUp(b))
  return (
    <div className="a3-wrap" data-od-id="data-sync-list">
      <KeepAwakeBanner />
      {refusal && (
        <div className="a3-action-error" role="alert">
          {refusal}
        </div>
      )}
      {connected.length > 0 && (
        <div className="a3-list" aria-label="Bank syncs">
          {connected.map((b) => (
            <BankSyncCard key={b.provider} status={b} onOpen={() => onOpen(`bank:${b.provider}`)} />
          ))}
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
      ) : connected.length > 0 ? null : (
        <div className="a3-empty">
          No data syncs yet. Pick one below to bring your history in and keep it in step.
        </div>
      )}
      {offered.length + bankOffers.length > 0 && (
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
            {bankOffers.map((b) => (
              <button
                key={b.provider}
                type="button"
                className="a3-tpl"
                aria-label={`Set up ${b.label}`}
                onClick={() => onOpen(`bank:${b.provider}`)}
              >
                <BankLogo provider={b.provider} />
                <span className="a3-tpl-body">
                  <span className="a3-tpl-title">{b.label}</span>
                  <span className="a3-tpl-blurb">Syncs your bank transactions automatically.</span>
                  <span className="a3-tpl-when">
                    <Clock3 size={11} aria-hidden="true" /> {everyLabel(b.interval_hours)}
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
